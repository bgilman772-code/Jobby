"""
Multi-source job fetcher.

Sources
-------
Board APIs (keyword search)
  - The Muse
  - Jobicy          (US filter, includes salary)
  - RemoteOK        (includes salary)
  - Arbeitnow
  - Himalayas       (remote-first, free API)
  - Remotive        (remote-first, tech/data roles)

RSS feeds
  - WeWorkRemotely  (5 category feeds)
  - Indeed          (free RSS feeds, multi-query + multi-location, no key needed)

Broad job board APIs
  - Dice.com        (free POST API, no key needed)
  - JSearch         (via RapidAPI, requires JSEARCH_API_KEY)
  - Jooble          (requires JOOBLE_API_KEY)

Web-indexed ATS discovery
  - Bing ATS search (discovers new Greenhouse/Lever/Ashby boards via Bing Web
                     Search, requires BING_SEARCH_KEY; caches discovered slugs
                     48h in .job_source_cache/discovered_slugs.json)

Company ATS boards (direct — no middleman)
  - Greenhouse      (~145 companies, keyword-searched)
  - Lever           (~85 companies, keyword-searched)
  - Ashby           (~20 companies, includes salary)
  - Workday         (~70 S&P 500 + DC defense + regional, 6 keyword searches each)
  - iCIMS           (~13 DC mid-cap companies)
  - BambooHR        (~20 political/boutique companies)

Location-anchored
  - Adzuna          (DC metro, requires ADZUNA_APP_ID/KEY)
  - USAJobs         (federal DC roles, requires USAJOBS_API_KEY/EMAIL)

Big Tech direct career APIs
  - Google, Microsoft, Meta (RSS), Amazon, Apple, Netflix, IBM

Direct career pages (JSON-LD + HTML extraction)
  - Political/civic tech, think tanks, gov-adjacent orgs

All results normalised to:
  url, title, company, company_name, description,
  location, remote, salary, date_posted, source
"""

import os as _os
import json as _json
import time as _time
import requests
from bs4 import BeautifulSoup
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

_HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; JobBot/1.0)'}
_TIMEOUT = 12          # reduced from 20 — dead endpoints fail faster
_SHORT_TIMEOUT = 7     # reduced from 10

# ── Login-walled domains ───────────────────────────────────────────────────────
# Jobs whose apply URL points to one of these require the user to have an
# account before they can apply.  We filter them out entirely so only
# direct-to-ATS links (Greenhouse, Lever, Workday, etc.) come through.
_LOGIN_WALLED_DOMAINS = {
    'linkedin.com',
    'indeed.com',
    'ziprecruiter.com',
    'glassdoor.com',
    'monster.com',
    'careerbuilder.com',
    'simplyhired.com',
    'snagajob.com',
    'lensa.com',
}

# Domains that are fine even though they look like aggregators
_ALLOWED_DOMAINS = {
    'greenhouse.io', 'boards.greenhouse.io',
    'lever.co',          # direct apply (not account-gated)
    'ashbyhq.com',
    'myworkdayjobs.com',
    'icims.com',
    'bamboohr.com',
    'smartrecruiters.com',
    'careers.google.com',
    'jobs.careers.microsoft.com',
    'amazon.jobs',
    'jobs.apple.com',
    'jobs.netflix.com',
    'careers.ibm.com',
}


def _is_login_walled(url: str) -> bool:
    """Return True if this URL requires a platform account to apply."""
    if not url:
        return True
    try:
        from urllib.parse import urlparse
        host = urlparse(url).netloc.lower().lstrip('www.')
        # Check exact match and subdomain match
        for domain in _LOGIN_WALLED_DOMAINS:
            if host == domain or host.endswith('.' + domain):
                return True
    except Exception:
        pass
    return False

# ── Disk result cache ─────────────────────────────────────────────────────────
# ATS boards update at most a few times per day; cache for 6h so repeat
# searches are near-instant.  Each source writes one JSON file.

_CACHE_DIR = _os.path.join(_os.path.dirname(__file__), '.job_source_cache')
_CACHE_TTL = 6 * 3600   # seconds


def _cache_read(key: str):
    path = _os.path.join(_CACHE_DIR, f'{key}.json')
    try:
        if _os.path.exists(path):
            if _time.time() - _os.path.getmtime(path) < _CACHE_TTL:
                with open(path, 'r', encoding='utf-8') as f:
                    return _json.load(f)
    except Exception:
        pass
    return None


def _cache_write(key: str, data: list):
    _os.makedirs(_CACHE_DIR, exist_ok=True)
    path = _os.path.join(_CACHE_DIR, f'{key}.json')
    try:
        with open(path, 'w', encoding='utf-8') as f:
            _json.dump(data, f)
    except Exception:
        pass


def cache_age_hours() -> float | None:
    """Return age (hours) of the oldest cache file, or None if no cache."""
    try:
        files = [_os.path.join(_CACHE_DIR, f)
                 for f in _os.listdir(_CACHE_DIR) if f.endswith('.json')]
        if not files:
            return None
        oldest_mtime = min(_os.path.getmtime(p) for p in files)
        return round((_time.time() - oldest_mtime) / 3600, 1)
    except Exception:
        return None


def clear_source_cache():
    """Delete all cached source files so the next search fetches fresh data."""
    try:
        for fname in _os.listdir(_CACHE_DIR):
            if fname.endswith('.json'):
                _os.remove(_os.path.join(_CACHE_DIR, fname))
    except Exception:
        pass


def _get(url, **kwargs):
    kwargs.setdefault('headers', _HEADERS)
    kwargs.setdefault('timeout', _TIMEOUT)
    return requests.get(url, **kwargs)


def _job(url='', title='', company='', description='', location='',
         remote=False, salary=None, date_posted=None, source=''):
    return {
        'url': url,
        'title': title,
        'company': company,
        'company_name': company,
        'description': description,
        'location': location,
        'remote': remote,
        'salary': salary,
        'date_posted': date_posted,
        'source': source,
    }


# ── The Muse ──────────────────────────────────────────────────────────────────

def fetch_themuse(query: str) -> list:
    try:
        resp = _get('https://www.themuse.com/api/public/jobs',
                    params={'name': query, 'page': 0})
        resp.raise_for_status()
        out = []
        for j in resp.json().get('results', []):
            url = (j.get('refs') or {}).get('landing_page', '')
            if not url:
                continue
            locs = [l.get('name', '') for l in (j.get('locations') or [])]
            out.append(_job(
                url=url,
                title=j.get('name', ''),
                company=(j.get('company') or {}).get('name', ''),
                description=j.get('contents', ''),
                location=', '.join(locs),
                remote=not locs or any(
                    kw in l.lower() for l in locs
                    for kw in ('remote', 'flexible', 'anywhere')),
                date_posted=j.get('publication_date'),
                source='themuse',
            ))
        return out
    except Exception:
        return []


# ── Jobicy ────────────────────────────────────────────────────────────────────

def fetch_jobicy(query: str = '') -> list:
    try:
        params = {'count': 50, 'geo': 'usa'}
        if query:
            params['tag'] = query
        resp = _get('https://jobicy.com/api/v2/remote-jobs', params=params)
        resp.raise_for_status()
        out = []
        for j in resp.json().get('jobs', []):
            url = j.get('url', '')
            if not url:
                continue
            sal = None
            if j.get('annualSalaryMin') and j.get('annualSalaryMax'):
                sal = f"${int(j['annualSalaryMin']):,}–${int(j['annualSalaryMax']):,}"
            elif j.get('annualSalaryMin'):
                sal = f"${int(j['annualSalaryMin']):,}+"
            out.append(_job(
                url=url,
                title=j.get('jobTitle', ''),
                company=j.get('companyName', ''),
                description=j.get('jobDescription', ''),
                location=j.get('jobGeo', 'USA'),
                remote=True,
                salary=sal,
                date_posted=j.get('pubDate'),
                source='jobicy',
            ))
        return out
    except Exception:
        return []


# ── RemoteOK ──────────────────────────────────────────────────────────────────

_REMOTEOK_TAGS = ['sql', 'data', 'analytics', 'python', 'engineer', 'bi', 'tableau']

def fetch_remoteok() -> list:
    out = {}
    for tag in _REMOTEOK_TAGS:
        try:
            resp = _get('https://remoteok.com/api', params={'tag': tag})
            resp.raise_for_status()
            data = resp.json()
            for j in (data[1:] if isinstance(data, list) and len(data) > 1 else []):
                url = j.get('url', '')
                if not url or url in out:
                    continue
                sal = None
                if j.get('salary_min') and j.get('salary_max'):
                    sal = f"${int(j['salary_min']):,}–${int(j['salary_max']):,}"
                out[url] = _job(
                    url=url,
                    title=j.get('position', ''),
                    company=j.get('company', ''),
                    description=j.get('description', ''),
                    location=j.get('location', 'Worldwide'),
                    remote=True,
                    salary=sal,
                    date_posted=j.get('date'),
                    source='remoteok',
                )
        except Exception:
            continue
    return list(out.values())


# ── Arbeitnow ─────────────────────────────────────────────────────────────────

def fetch_arbeitnow() -> list:
    try:
        resp = _get('https://www.arbeitnow.com/api/job-board-api')
        resp.raise_for_status()
        out = []
        for j in resp.json().get('data', []):
            url = j.get('url', '')
            if not url:
                continue
            out.append(_job(
                url=url,
                title=j.get('title', ''),
                company=j.get('company_name', ''),
                description=j.get('description', ''),
                location=j.get('location', ''),
                remote=j.get('remote', True),
                source='arbeitnow',
            ))
        return out
    except Exception:
        return []


# ── Himalayas ─────────────────────────────────────────────────────────────────

def fetch_himalayas() -> list:
    try:
        resp = _get('https://himalayas.app/jobs/api', params={'limit': 100})
        resp.raise_for_status()
        out = []
        for j in resp.json().get('jobs', []):
            url = j.get('url', '') or j.get('applicationUrl', '')
            if not url:
                continue
            sal = j.get('salary', '') or None
            out.append(_job(
                url=url,
                title=j.get('title', ''),
                company=(j.get('company') or {}).get('name', '') if isinstance(j.get('company'), dict) else j.get('companyName', ''),
                description=j.get('description', ''),
                location=j.get('location', 'Remote'),
                remote=True,
                salary=sal,
                date_posted=j.get('datePosted'),
                source='himalayas',
            ))
        return out
    except Exception:
        return []


# ── Remotive ─────────────────────────────────────────────────────────────────

_REMOTIVE_CATEGORIES = ['software-dev', 'data', 'product', 'all-others']

def fetch_remotive() -> list:
    out = {}
    for cat in _REMOTIVE_CATEGORIES:
        try:
            resp = _get('https://remotive.com/api/remote-jobs',
                        params={'category': cat, 'limit': 100})
            resp.raise_for_status()
            for j in resp.json().get('jobs', []):
                url = j.get('url', '')
                if not url or url in out:
                    continue
                sal = j.get('salary', '') or None
                out[url] = _job(
                    url=url,
                    title=j.get('title', ''),
                    company=j.get('company_name', ''),
                    description=j.get('description', ''),
                    location=j.get('candidate_required_location', 'Worldwide'),
                    remote=True,
                    salary=sal,
                    date_posted=j.get('publication_date'),
                    source='remotive',
                )
        except Exception:
            continue
    return list(out.values())


# ── WeWorkRemotely RSS ────────────────────────────────────────────────────────

_WWR_FEEDS = [
    'https://weworkremotely.com/categories/remote-programming-jobs.rss',
    'https://weworkremotely.com/categories/remote-data-science-jobs.rss',
    'https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss',
    'https://weworkremotely.com/categories/remote-management-finance-jobs.rss',
    'https://weworkremotely.com/categories/remote-product-jobs.rss',
]


def fetch_weworkremotely() -> list:
    out = []
    for feed_url in _WWR_FEEDS:
        try:
            resp = _get(feed_url)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
            for item in root.findall('.//item'):
                link_el = item.find('link')
                url = ''
                if link_el is not None:
                    url = (link_el.text or '').strip()
                    if not url and link_el.tail:
                        url = link_el.tail.strip()
                if not url:
                    guid = item.find('guid')
                    url = (guid.text or '').strip() if guid is not None else ''
                if not url:
                    continue
                raw_title = (item.findtext('title') or '').strip()
                company, title = '', raw_title
                if ': ' in raw_title:
                    company, title = raw_title.split(': ', 1)
                out.append(_job(
                    url=url, title=title, company=company,
                    description=item.findtext('description') or '',
                    location='Remote', remote=True,
                    date_posted=item.findtext('pubDate'),
                    source='weworkremotely',
                ))
        except Exception:
            continue
    return out


# ── Greenhouse ATS ────────────────────────────────────────────────────────────
# Public API: GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

GREENHOUSE_COMPANIES = [
    # ── Data / Analytics / BI ─────────────────────────────────────────────────
    ('databricks',       'Databricks'),
    ('datadog',          'Datadog'),
    ('amplitude',        'Amplitude'),
    ('mixpanel',         'Mixpanel'),
    ('fivetran',         'Fivetran'),
    ('dbtlabs',          'dbt Labs'),
    ('hightouch',        'Hightouch'),
    ('metabase',         'Metabase'),
    ('preset',           'Preset'),
    ('hex',              'Hex'),
    ('mode',             'Mode Analytics'),
    ('sigma',            'Sigma Computing'),
    ('thoughtspot',      'ThoughtSpot'),
    ('atscale',          'AtScale'),
    ('acceldata',        'Acceldata'),
    # ── Cloud / Infra / DevTools ──────────────────────────────────────────────
    ('cloudflare',       'Cloudflare'),
    ('hashicorp',        'HashiCorp'),
    ('mongodb',          'MongoDB'),
    ('elastic',          'Elastic'),
    ('cockroachlabs',    'CockroachDB'),
    ('planetscale',      'PlanetScale'),
    ('neon',             'Neon'),
    ('timescale',        'Timescale'),
    ('singlestore',      'SingleStore'),
    ('crunchy-data',     'Crunchy Data'),
    ('gitlab',           'GitLab'),
    ('github',           'GitHub'),
    ('circleci',         'CircleCI'),
    ('pagerduty',        'PagerDuty'),
    ('postman',          'Postman'),
    ('contentful',       'Contentful'),
    ('algolia',          'Algolia'),
    # ── FinTech / Payments ────────────────────────────────────────────────────
    ('stripe',           'Stripe'),
    ('coinbase',         'Coinbase'),
    ('brex',             'Brex'),
    ('plaid',            'Plaid'),
    ('robinhood',        'Robinhood'),
    ('ramp',             'Ramp'),
    ('chime',            'Chime'),
    ('nerdwallet',       'NerdWallet'),
    ('block',            'Block (Square)'),
    ('marqeta',          'Marqeta'),
    ('capitalonetech',   'Capital One (Tech)'),
    # ── Consumer Tech / Marketplace ───────────────────────────────────────────
    ('airbnb',           'Airbnb'),
    # doordash, uber, snap — migrated away from Greenhouse
    ('instacart',        'Instacart'),
    ('lyft',             'Lyft'),
    ('pinterest',        'Pinterest'),
    ('discord',          'Discord'),
    ('reddit',           'Reddit'),
    ('dropbox',          'Dropbox'),
    # ── Enterprise SaaS ──────────────────────────────────────────────────────
    # hubspot, zendesk — migrated away from Greenhouse
    ('twilio',           'Twilio'),
    ('intercom',         'Intercom'),
    ('squarespace',      'Squarespace'),
    ('figma',            'Figma'),
    ('notion',           'Notion'),
    ('airtable',         'Airtable'),
    ('miro',             'Miro'),
    ('calendly',         'Calendly'),
    ('zapier',           'Zapier'),
    ('okta',             'Okta'),
    ('rippling',         'Rippling'),
    ('gusto',            'Gusto'),
    ('deel',             'Deel'),
    ('remote',           'Remote.com'),
    ('lattice',          'Lattice'),
    ('workato',          'Workato'),
    ('procore',          'Procore'),
    ('veeva',            'Veeva Systems'),
    ('servicetitan',     'ServiceTitan'),
    ('toast',            'Toast'),
    ('medallia',         'Medallia'),
    ('sprinklr',         'Sprinklr'),
    ('zuora',            'Zuora'),
    # ── Security ─────────────────────────────────────────────────────────────
    # palantir — migrated away from Greenhouse
    ('verkada',          'Verkada'),
    ('lacework',         'Lacework'),
    ('snyk',             'Snyk'),
    # ── Other Notable ─────────────────────────────────────────────────────────
    ('samsara',          'Samsara'),
    ('benchling',        'Benchling'),
    ('clio',             'Clio'),
    ('smartsheet',       'Smartsheet'),
    ('expensify',        'Expensify'),
    ('thumbtack',        'Thumbtack'),
    ('spotify',          'Spotify'),
    ('stitchfix',        'Stitch Fix'),
    # ── Data / Analytics Growth Stage ────────────────────────────────────────
    # looker — acquired by Google, migrated away from Greenhouse
    ('lightdash',        'Lightdash'),
    ('metriql',          'Metriql'),
    ('datacoves',        'Datacoves'),
    ('selectstar',       'Select Star'),
    ('castor',           'Castor'),
    ('y42',              'Y42'),
    ('synq',             'SYNQ'),
    ('recce',            'Recce'),
    ('tinybird',         'Tinybird'),
    # ── DC / GovTech / Defense Tech ───────────────────────────────────────────
    ('anduril',          'Anduril Industries'),
    ('shield-ai',        'Shield AI'),
    ('sievert',          'Sievert Analytics'),
    ('govini',           'Govini'),
    ('qomplx',           'QOMPLX'),
    ('rebellion-defense','Rebellion Defense'),
    ('primer',           'Primer AI'),
    ('palantirfoundation','Palantir (Foundation)'),
    # ── Marketing / AdTech / Media Analytics ─────────────────────────────────
    ('thetradedesk',     'The Trade Desk'),
    ('liveramp',         'LiveRamp'),
    ('bazaarvoice',      'Bazaarvoice'),
    ('integral-ad-science','Integral Ad Science'),
    ('comscore',         'Comscore'),
    # ── Financial Data / Quant ────────────────────────────────────────────────
    ('twosgma',          'Two Sigma'),
    ('dv-trading',       'DV Trading'),
    ('iex',              'IEX'),
    ('alphasense',       'AlphaSense'),
    ('morningstar',      'Morningstar'),
    # ── Health Data / Policy ─────────────────────────────────────────────────
    ('komodohealth',     'Komodo Health'),
    ('health-catalyst',  'Health Catalyst'),
    ('arcadia',          'Arcadia'),
    ('lightbeam',        'Lightbeam Health'),
    # ── Political / Civic / Gov Tech ─────────────────────────────────────────
    ('catalist',         'Catalist'),
    ('civisanalytics',   'Civis Analytics'),
    ('bluelabs',         'BlueLabs Analytics'),
    ('targetsmart',      'TargetSmart'),
    ('dccc',             'DCCC'),
    ('democracyworks',   'Democracy Works'),
    ('arena',            'Arena'),
    ('everytownresearch','Everytown Research'),
    # ── Data Journalism / Media Analytics ────────────────────────────────────
    ('axios',            'Axios'),
    ('politico',         'Politico'),
    ('vox',              'Vox Media'),
    ('nytimes',          'New York Times'),
    # ── Additional Data / Analytics ───────────────────────────────────────────
    ('dremio',           'Dremio'),
    ('starburst',        'Starburst Data'),
    ('imply',            'Imply'),
    ('pinecone',         'Pinecone'),
    ('weaviate',         'Weaviate'),
    ('qdrant',           'Qdrant'),
    ('chroma',           'Chroma'),
    ('lancedb',          'LanceDB'),
    ('motherduck',       'MotherDuck'),
    ('clickhouse',       'ClickHouse'),
    ('timescaledb',      'Timescale'),
    ('questdb',          'QuestDB'),
    ('risingwave',       'RisingWave Labs'),
    ('nessie',           'Project Nessie / Dremio'),
    ('coalesce',         'Coalesce'),
    ('paradime',         'Paradime'),
    ('elementary-data',  'Elementary Data'),
    ('soda-data',        'Soda'),
    ('datafold',         'Datafold'),
    # ── DC/Gov Adjacent ───────────────────────────────────────────────────────
    ('nrdc',             'NRDC'),
    ('wri',              'World Resources Institute'),
    ('crs',              'Congressional Research Service'),
    ('navigator-research','Navigator Research'),
    ('aclu',             'ACLU'),
    # ── Additional Enterprise SaaS ────────────────────────────────────────────
    ('braze',            'Braze'),
    ('klaviyo',          'Klaviyo'),
    ('iterable',         'Iterable'),
    ('sendgrid',         'SendGrid / Twilio'),
    ('segment',          'Segment'),
    ('heap',             'Heap'),
    ('fullstory',        'FullStory'),
    ('contentsquare',    'Contentsquare'),
    ('quantum-metric',   'Quantum Metric'),
    ('glean',            'Glean'),
    ('moveworks',        'Moveworks'),
    ('rapid7',           'Rapid7'),
    ('tenable',          'Tenable'),
    ('qualys',           'Qualys'),
    ('carbonblack',      'Carbon Black'),
    ('cybereason',       'Cybereason'),
    ('darktrace',        'Darktrace'),
    ('illumio',          'Illumio'),
    # ── DC Policy / Non-profit ────────────────────────────────────────────────
    ('urban',            'Urban Institute'),
    ('brookings',        'Brookings Institution'),
    ('cato',             'Cato Institute'),
    ('aei',              'American Enterprise Institute'),
    ('cap',              'Center for American Progress'),
    ('hrw',              'Human Rights Watch'),
    ('amnesty',          'Amnesty International USA'),
    # ── DC Think Tanks (additional) ───────────────────────────────────────────
    ('cbpp',             'Center on Budget and Policy Priorities'),
    ('epi',              'Economic Policy Institute'),
    ('itif',             'Information Technology and Innovation Foundation'),
    ('rstreet',          'R Street Institute'),
    ('niskanen',         'Niskanen Center'),
    ('piie',             'Peterson Institute for International Economics'),
    ('stimson',          'Stimson Center'),
    ('gmf',              'German Marshall Fund of the United States'),
    ('cnas',             'Center for a New American Security'),
    ('crsb',             'Center for Strategic and Budgetary Assessments'),
    ('newamerica',       'New America Foundation'),
    ('rooseveltinstitute', 'Roosevelt Institute'),
    # ── Northern VA / DC Tech ─────────────────────────────────────────────────
    ('appian',           'Appian Corporation'),
    ('alarmdotcom',      'Alarm.com'),
    ('novetta',          'Novetta'),
    ('evolent',          'Evolent Health'),
    ('octo',             'Octo Consulting'),
    ('cvent',            'Cvent'),
    ('microsegment',     'MicroStrategy'),
    ('sogeti',           'Sogeti'),
    # ── DC Civic / Political Tech ─────────────────────────────────────────────
    ('actblue',          'ActBlue'),
    ('codeforamerica',   'Code for America'),
    ('ctcl',             'Center for Tech and Civic Life'),
    ('techandciviclife', 'CTCL'),
    ('countable',        'Countable'),
    ('doubleline',       'DoubleLine'),
    ('voter-participation', 'Voter Participation Center'),
    # ── International Development ─────────────────────────────────────────────
    ('fhi360',           'FHI 360'),
    ('chemonics',        'Chemonics International'),
    ('dai',              'DAI Global'),
    ('winrock',          'Winrock International'),
    ('icmpd',            'ICMPD'),
    ('mercycorps',       'Mercy Corps'),
    ('counterpart',      'Counterpart International'),
    # ── DC Associations / Advocacy / Nonprofits ───────────────────────────────
    ('aarp',             'AARP'),
    ('redcross',         'American Red Cross'),
    ('wwf',              'World Wildlife Fund'),
    ('edf',              'Environmental Defense Fund'),
    ('nwf',              'National Wildlife Federation'),
    ('sierraclub',       'Sierra Club'),
    ('naacp',            'NAACP'),
    ('aclu',             'ACLU'),
    ('hrc',              'Human Rights Campaign'),
    ('emilyslist',       "EMILY's List"),
    ('nrdc',             'NRDC'),
    # ── Federal Contractors / Defense Tech ────────────────────────────────────
    ('boozallen',            'Booz Allen Hamilton'),
    ('leidos',               'Leidos'),
    ('saic',                 'SAIC'),
    ('mantech',              'ManTech International'),
    ('peraton',              'Peraton'),
    ('mitre',                'MITRE Corporation'),
    ('miterrasolutions',     'Mitreva Solutions'),
    ('caci',                 'CACI International'),
    # ── Consulting ────────────────────────────────────────────────────────────
    ('mckinsey',             'McKinsey & Company'),
    ('bain',                 'Bain & Company'),
    ('bcg',                  'Boston Consulting Group'),
    ('deloitte',             'Deloitte'),
    ('kpmg',                 'KPMG'),
    ('pwc',                  'PricewaterhouseCoopers'),
    ('accenture',            'Accenture'),
    ('capgemini',            'Capgemini'),
    # ── Additional Tech ───────────────────────────────────────────────────────
    ('duolingo',             'Duolingo'),
    # shopify, zendesk — migrated away from Greenhouse (duplicate entries removed too)
    ('stripe-inc',           'Stripe'),
    ('twilio',               'Twilio'),
    ('zoom',                 'Zoom'),
    ('box',                  'Box'),
    ('docusign',             'DocuSign'),
    # ── More DC / Civic ───────────────────────────────────────────────────────
    ('georgetownuniversity', 'Georgetown University'),
    ('americanuniversity',   'American University'),
    ('gwu',                  'George Washington University'),
    ('cato',                 'Cato Institute'),
    ('aei-org',              'American Enterprise Institute'),
    ('aspenmedia',           'Aspen Institute'),
    # ── Policy / Advocacy / Nonprofits (new) ─────────────────────────────────
    ('brennancenter',        'Brennan Center for Justice'),
    ('demos',                'Demos'),
    ('thenation',            'The Nation'),
    ('sunlightfoundation',   'Sunlight Foundation'),
    ('maplight',             'MapLight'),
    ('indivisible',          'Indivisible'),
    ('moveon',               'MoveOn'),
    # ── Think tanks / Research (new) ─────────────────────────────────────────
    ('urban-institute',      'Urban Institute'),
    ('wilsoncenterjobs',     'Wilson Center'),
    ('bpc-action',           'Bipartisan Policy Center'),
    # ── GovTech (new) ─────────────────────────────────────────────────────────
    ('nava',                 'Nava PBC'),
    ('adhocteam',            'Ad Hoc LLC'),
    ('pluralpolicy',         'Plural Policy'),
    ('quorum',               'Quorum Analytics'),
    ('votingworks',          'VotingWorks'),
    ('democrats',            'Democratic Party'),
    # ── Tech companies DC area / defense (new) ────────────────────────────────
    ('booz-allen-hamilton',  'Booz Allen Hamilton'),
    ('maximus-inc',          'Maximus'),
    ('deloitte-us',          'Deloitte'),
    # ── Data / Analytics companies (new) ─────────────────────────────────────
    ('dbt-labs',             'dbt Labs'),
    ('monte-carlo-data',     'Monte Carlo'),
    # ── Miami area (new) ──────────────────────────────────────────────────────
    ('carnival-cruise',      'Carnival Cruise Line'),
    ('worldfuel',            'World Kinect'),
    ('lennar',               'Lennar'),
    ('chewy',                'Chewy'),
    ('ryder',                'Ryder'),
    # ── Austin area (new) ─────────────────────────────────────────────────────
    ('homeaway',             'Vrbo / HomeAway'),
    ('whole-foods',          'Whole Foods Market'),
    ('indeed',               'Indeed'),
    ('bumble',               'Bumble'),
    ('opcity',               'Opcity'),
    # ── Verified by research agent ────────────────────────────────────────────
    ('affirm',               'Affirm'),
    ('apolloio',             'Apollo.io'),
    ('betterment',           'Betterment'),
    ('caylent',              'Caylent'),
    ('celonis',              'Celonis'),
    ('clickup',              'ClickUp'),
    ('coursera',             'Coursera'),
    ('demandbase',           'Demandbase'),
    ('faire',                'Faire'),
    ('gongio',               'Gong'),
    ('grammarly',            'Grammarly'),
    ('growtherapy',          'Grow Therapy'),
    ('invitae',              'Invitae'),
    ('mavenclinic',          'Maven Clinic'),
    ('natera',               'Natera'),
    ('nextdoor',             'Nextdoor'),
    ('opendoor',             'Opendoor'),
    ('pendo',                'Pendo'),
    ('propublica',           'ProPublica'),
    ('realtimeboardglobal',  'Miro (Board)'),
    ('rubrik',               'Rubrik'),
    ('salesloft',            'Salesloft'),
    ('smartlyio',            'Smartly.io'),
    ('stackadapt',           'StackAdapt'),
    ('tanium',               'Tanium'),
    ('tempus',               'Tempus'),
    ('thoughtworks',         'Thoughtworks'),
    ('tpgcareers',           'TPG Capital'),
    ('udemy',                'Udemy'),
    ('webflow',              'Webflow'),
    ('wizinc',               'Wiz'),
    ('zensourcer',           'Gem'),
    # ── High-confidence additions ─────────────────────────────────────────────
    ('10xgenomics',          '10x Genomics'),
    ('abnormalsecurity',     'Abnormal Security'),
    ('appfolio',             'AppFolio'),
    ('cardlytics',           'Cardlytics'),
    ('carrot',               'Carrot Fertility'),
    ('cb-insights',          'CB Insights'),
    ('cerebral',             'Cerebral'),
    ('cityblock',            'Cityblock Health'),
    ('coda',                 'Coda'),
    ('color',                'Color Health'),
    ('costar',               'CoStar Group'),
    ('domo',                 'Domo'),
    ('dotdash',              'Dotdash Meredith'),
    ('envoy',                'Envoy'),
    ('epicgames',            'Epic Games'),
    ('everlane',             'Everlane'),
    ('fastly',               'Fastly'),
    ('flatiron',             'Flatiron Health'),
    ('flywire',              'Flywire'),
    ('grail',                'GRAIL'),
    ('harness',              'Harness'),
    ('headway',              'Headway'),
    ('hims',                 'Hims & Hers'),
    ('jobyaviation',         'Joby Aviation'),
    ('justworks',            'Justworks'),
    ('knowbe4',              'KnowBe4'),
    ('lucidworks',           'Lucidworks'),
    ('lyra',                 'Lyra Health'),
    ('modernhealth',         'Modern Health'),
    ('motive',               'Motive'),
    ('netlify',              'Netlify'),
    ('newrelic',             'New Relic'),
    ('olo',                  'Olo'),
    ('omada',                'Omada Health'),
    ('orchard',              'Orchard'),
    ('qualtrics',            'Qualtrics'),
    ('recursion',            'Recursion Pharmaceuticals'),
    ('relativity',           'Relativity'),
    ('renttherunway',        'Rent the Runway'),
    ('ro',                   'Ro'),
    ('roblox',               'Roblox'),
    ('seismic',              'Seismic'),
    ('sentry',               'Sentry'),
    ('sonder',               'Sonder'),
    ('sourcegraph',          'Sourcegraph'),
    ('spring-health',        'Spring Health'),
    ('sumo-logic',           'Sumo Logic'),
    ('sysdig',               'Sysdig'),
    ('vacasa',               'Vacasa'),
    ('whoop',                'WHOOP'),
    ('workiva',              'Workiva'),
    ('zocdoc',               'Zocdoc'),
    # ── Migrated from Lever (confirmed working 2026-03) ───────────────────────
    ('anthropic',            'Anthropic'),
    ('asana',                'Asana'),
    ('carta',                'Carta'),
    ('vercel',               'Vercel'),
    ('attentive',            'Attentive'),
    ('launchdarkly',         'LaunchDarkly'),
    ('jfrog',                'JFrog'),
]


def _fetch_greenhouse(slug: str, company_name: str) -> list:
    try:
        resp = requests.get(
            f'https://boards-api.greenhouse.io/v1/boards/{slug}/jobs',
            params={'content': 'true'},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        out = []
        for j in resp.json().get('jobs', []):
            url = j.get('absolute_url', '')
            if not url:
                continue
            loc = (j.get('location') or {}).get('name', '')
            out.append(_job(
                url=url,
                title=j.get('title', ''),
                company=company_name,
                description=j.get('content', ''),
                location=loc,
                remote='remote' in loc.lower() or not loc,
                date_posted=j.get('updated_at'),
                source=f'greenhouse/{slug}',
            ))
        return out
    except Exception:
        return []


def fetch_greenhouse_all() -> list:
    cached = _cache_read('greenhouse')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fetch_greenhouse, slug, name)
                   for slug, name in GREENHOUSE_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('greenhouse', out)
    return out


# ── Lever ATS ─────────────────────────────────────────────────────────────────
# Public API: GET https://api.lever.co/v0/postings/{slug}?mode=json

LEVER_COMPANIES = [
    # ── Live as of 2026-03 (verified returning jobs) ──────────────────────────
    # AI / Tech
    ('mistral',            'Mistral AI'),
    ('gohighlevel',        'GoHighLevel'),
    ('pattern',            'Pattern'),
    # FinTech / Payments
    ('plaid',              'Plaid'),
    ('nium',               'Nium'),
    ('floqast',            'FloQast'),
    ('emburse',            'Emburse'),
    ('pipedrive',          'Pipedrive'),
    ('wealthfront',        'Wealthfront'),
    # Enterprise SaaS
    ('outreach',           'Outreach'),
    ('captivateiq',        'CaptivateIQ'),
    ('highspot',           'Highspot'),
    ('clari',              'Clari'),
    ('jumpcloud',          'JumpCloud'),
    # E-commerce / Consumer
    ('loopreturns',        'Loop Returns'),
    ('minted',             'Minted'),
    # Political / Civic
    ('commoncause',        'Common Cause'),
    ('emilyslist',         "EMILY's List"),
    ('nationaljournal',    'National Journal'),
    ('15five',             '15Five'),
    # Media / Other
    ('medium',             'Medium'),
    ('xero',               'Xero'),
    ('hyperscience',       'HyperScience'),
    ('narmi',              'Narmi'),
]


def _fetch_lever(slug: str, company_name: str) -> list:
    try:
        resp = requests.get(
            f'https://api.lever.co/v0/postings/{slug}',
            params={'mode': 'json'},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        out = []
        for j in resp.json():
            url = j.get('hostedUrl', '')
            # Skip postings that redirect off jobs.lever.co (e.g. Opendoor splash pages)
            if not url or 'jobs.lever.co' not in url:
                continue
            cats = j.get('categories') or {}
            loc = cats.get('location', '')
            workplace = (j.get('workplaceType') or '').lower()
            desc = ' '.join(filter(None, [
                j.get('descriptionBody', ''),
                j.get('description', ''),
                ' '.join(l.get('content', '') for l in (j.get('lists') or [])),
            ]))
            ts = j.get('createdAt')
            out.append(_job(
                url=url,
                title=j.get('text', ''),
                company=company_name,
                description=desc,
                location=loc,
                remote='remote' in loc.lower() or 'remote' in workplace,
                date_posted=(datetime.fromtimestamp(ts / 1000).isoformat() if ts else None),
                source=f'lever/{slug}',
            ))
        return out
    except Exception:
        return []


def fetch_lever_all() -> list:
    cached = _cache_read('lever')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fetch_lever, slug, name)
                   for slug, name in LEVER_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('lever', out)
    return out


# ── Ashby ATS ─────────────────────────────────────────────────────────────────
# Public API: GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
# Includes salary data natively.

ASHBY_COMPANIES = [
    ('linear',              'Linear'),
    ('mercury',             'Mercury'),
    ('modern-treasury',     'Modern Treasury'),
    ('ramp',                'Ramp'),
    ('arc',                 'Arc'),
    ('runway',              'Runway'),
    ('causal',              'Causal'),
    ('orb',                 'Orb'),
    ('brex',                'Brex'),
    ('ashby',               'Ashby'),
    ('watershed',           'Watershed'),
    ('vannevar-labs',       'Vannevar Labs'),
    ('kepler',              'Kepler'),
    ('vanta',               'Vanta'),
    ('drata',               'Drata'),
    ('hex',                 'Hex'),
    ('retool',              'Retool'),
    ('anyscale',            'Anyscale'),
    ('modal',               'Modal'),
    ('prefect',             'Prefect'),
    ('dagster-labs',        'Dagster'),
    ('sourcegraph',          'Sourcegraph'),
    ('dune-analytics',       'Dune Analytics'),
    ('commonroom',           'Common Room'),
    ('census',               'Census'),
    ('airbyte',              'Airbyte'),
    ('elementary',           'Elementary Data'),
    ('coalesce',             'Coalesce'),
    ('paradime',             'Paradime'),
    ('soda',                 'Soda Data'),
    ('datafold',             'Datafold'),
    ('turntable',            'Turntable'),
    ('eppo',                 'Eppo'),
    ('growthbook',           'GrowthBook'),
    ('metriql',              'Metriql'),
    ('lightdash-hq',         'Lightdash'),
    ('streamlit',            'Streamlit'),
    ('evidence-dev',         'Evidence'),
    ('rill',                 'Rill Data'),
    ('cube',                 'Cube'),
    ('starburstdata',        'Starburst Data'),
    ('imply',                'Imply'),
    ('clickhouse',           'ClickHouse'),
    ('motherduck',           'MotherDuck'),
    ('duckdb-foundation',    'DuckDB Foundation'),
    ('turbopuffer',          'turbopuffer'),
    # ── Migrated from Lever (confirmed working 2026-03) ───────────────────────
    ('openai',               'OpenAI'),
    ('cohere',               'Cohere'),
    ('elevenlabs',           'ElevenLabs'),
    ('replit',               'Replit'),
    ('supabase',             'Supabase'),
    ('cursor',               'Cursor'),
    ('benchling',            'Benchling'),
    ('sentry',               'Sentry'),
]


def _fetch_ashby(slug: str, company_name: str) -> list:
    try:
        resp = requests.get(
            f'https://api.ashbyhq.com/posting-api/job-board/{slug}',
            params={'includeCompensation': 'true'},
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        out = []
        for j in resp.json().get('jobs', []):
            url = j.get('jobUrl', '') or j.get('applyUrl', '')
            if not url:
                continue
            # Salary from Ashby's compensation field
            sal = None
            comp = j.get('compensation') or {}
            if comp.get('compensationTierSummary'):
                sal = comp['compensationTierSummary']
            # Location
            primary = j.get('primaryLocation') or {}
            loc_parts = [primary.get('city'), primary.get('state'), primary.get('country')]
            loc = ', '.join(p for p in loc_parts if p)
            workplace = (j.get('workplaceType') or '').lower()
            out.append(_job(
                url=url,
                title=j.get('title', ''),
                company=company_name,
                description=j.get('descriptionPlain') or j.get('descriptionHtml', ''),
                location=loc,
                remote='remote' in workplace,
                salary=sal,
                date_posted=j.get('publishedAt'),
                source=f'ashby/{slug}',
            ))
        return out
    except Exception:
        return []


def fetch_ashby_all() -> list:
    cached = _cache_read('ashby')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_ashby, slug, name)
                   for slug, name in ASHBY_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('ashby', out)
    return out


# ── Workday ATS ───────────────────────────────────────────────────────────────
# Many S&P 500 / Fortune 500 companies run on Workday.
# POST https://{tenant}.wd{n}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs
# Body: {"appliedFacets":{},"limit":20,"offset":0,"searchText":""}

WORKDAY_COMPANIES = [
    # (tenant, board, wd_suffix, display_name)
    # Tenant IDs verified against live myworkdayjobs.com URLs — many differ from company name!
    # ── Big Tech / Cloud / Software ──────────────────────────────────────────
    ('nvidia',                'NVIDIAExternalCareerSite',      '5',  'NVIDIA'),
    ('salesforce',            'External_Career_Site',          '12', 'Salesforce'),
    ('servicenow',            'External',                      '5',  'ServiceNow'),
    ('adobe',                 'external',                      '5',  'Adobe'),
    ('cisco',                 'Cisco_Careers',                 '5',  'Cisco'),
    ('paloaltonetworks',      'External',                      '5',  'Palo Alto Networks'),
    ('intel',                 'External',                      '1',  'Intel'),
    ('amd',                   'External',                      '5',  'AMD'),
    ('qualcomm',              'External',                      '5',  'Qualcomm'),
    ('broadcom',              'External_Career',               '1',  'Broadcom'),
    ('amat',                  'External',                      '1',  'Applied Materials'),
    ('workday',               'Workday',                       '5',  'Workday'),
    ('crowdstrike',           'crowdstrikecareers',            '5',  'CrowdStrike'),
    ('zscaler',               'External',                      '3',  'Zscaler'),
    ('intuit',                'External',                      '5',  'Intuit'),
    ('paypal',                'jobs',                          '1',  'PayPal'),
    ('atlassian',             'External',                      '5',  'Atlassian'),
    ('shopify',               'External',                      '5',  'Shopify'),
    ('docusign',              'External',                      '5',  'DocuSign'),
    ('ringcentral',           'External',                      '5',  'RingCentral'),
    ('oracle',                'OracleCareer',                  '1',  'Oracle'),
    ('dell',                  'External',                      '1',  'Dell Technologies'),
    ('hpe',                   'Jobsathpe',                     '5',  'HPE'),
    ('hp',                    'External',                      '5',  'HP Inc.'),
    ('teradata',              'External',                      '5',  'Teradata'),
    ('informatica',           'External',                      '5',  'Informatica'),
    ('verint',                'External',                      '5',  'Verint Systems'),
    ('gartner',               'External',                      '5',  'Gartner'),
    ('forrester',             'External',                      '5',  'Forrester Research'),
    ('nxp',                   'External',                      '5',  'NXP Semiconductors'),
    ('cirrus',                'External',                      '5',  'Cirrus Logic'),
    # ── Professional Services / Consulting ───────────────────────────────────
    ('deloitte',              'DTCareer',                      '1',  'Deloitte'),
    ('accenture',             'accenture',                     '3',  'Accenture'),
    ('pwc',                   'Global_Experienced_Careers',    '3',  'PwC'),
    ('ey',                    'ey',                            '1',  'EY'),
    ('kpmg',                  'External',                      '5',  'KPMG'),
    # ── DC Defense / Government IT ────────────────────────────────────────────
    ('lmco',                  'LMCareers',                     '1',  'Lockheed Martin'),
    ('ngc',                   'External',                      '1',  'Northrop Grumman'),       # tenant=ngc NOT northropgrumman
    ('globalhr',              'REC_RTX_Ext_Gateway',           '5',  'RTX (Raytheon)'),         # tenant=globalhr NOT rtx
    ('bah',                   'BAH_Jobs',                      '1',  'Booz Allen Hamilton'),    # tenant=bah NOT boozallen
    ('boeing',                'EXTERNAL_CAREERS',              '1',  'Boeing'),
    ('leidos',                'External',                      '5',  'Leidos'),
    ('gdit',                  'External_Career_Site',          '5',  'General Dynamics IT'),
    ('saic',                  'SAIC',                         '5',  'SAIC'),
    ('l3harris',              'EN',                            '5',  'L3Harris'),
    ('mantech',               'External',                      '1',  'ManTech'),
    ('parsons',               'External',                      '5',  'Parsons Corporation'),
    ('peraton',               'External',                      '5',  'Peraton'),
    ('amentum',               'External',                      '5',  'Amentum'),
    ('mci',                   'External',                      '5',  'MCI (consulting)'),
    ('mitre',                 'External',                      '5',  'MITRE Corporation'),
    ('noblis',                'External',                      '5',  'Noblis'),
    ('maximus',               'External',                      '5',  'Maximus'),
    # ── Financial Services ────────────────────────────────────────────────────
    ('ms',                    'External',                      '5',  'Morgan Stanley'),         # tenant=ms NOT morganstanley
    ('wf',                    'WellsFargoJobs',                '1',  'Wells Fargo'),            # tenant=wf NOT wellsfargo
    ('ghr',                   'Lateral-US',                    '1',  'Bank of America'),        # tenant=ghr NOT bankofamerica
    ('citi',                  '2',                             '5',  'Citigroup'),
    ('visa',                  'Visa_Early_Careers',            '5',  'Visa'),
    ('mastercard',            'CorporateCareers',              '1',  'Mastercard'),
    ('blackrock',             'BlackRock_Professional',        '1',  'BlackRock'),
    ('fmr',                   'FidelityCareers',               '1',  'Fidelity Investments'),   # tenant=fmr NOT fidelity
    ('invesco',               'IVZ',                           '1',  'Invesco'),
    ('franklintempleton',     'Primary-External-1',            '5',  'Franklin Templeton'),
    ('troweprice',            'TRowePrice',                    '5',  'T. Rowe Price'),
    ('statestreet',           'Global',                        '1',  'State Street'),
    ('synchronyfinancial',    'careers',                       '5',  'Synchrony Financial'),    # NOT synchrony
    ('discover',              'External',                      '5',  'Discover Financial'),
    ('aig',                   'aig',                           '1',  'AIG'),
    ('pru',                   'Careers',                       '5',  'Prudential Financial'),   # tenant=pru NOT prudential
    ('travelers',             'External',                      '5',  'Travelers'),
    ('thehartford',           'Careers_External',              '5',  'The Hartford'),
    ('allstate',              'allstate_careers',              '5',  'Allstate'),
    ('aflac',                 'External',                      '5',  'Aflac'),
    ('unum',                  'External',                      '5',  'Unum Group'),
    ('lincoln',               'External',                      '5',  'Lincoln Financial'),
    ('schwab',                'External',                      '5',  'Charles Schwab'),
    # ── Healthcare ────────────────────────────────────────────────────────────
    ('unitedhealthgroup',     'External',                      '5',  'UnitedHealth Group'),
    ('cvshealth',             'cvs_health_careers',            '1',  'CVS Health'),
    ('elevancehealth',        'ANT',                           '1',  'Elevance Health'),        # NOT elevance
    ('cigna',                 'cignacareers',                  '5',  'Cigna'),
    ('humana',                'Humana_External_Career_Site',   '5',  'Humana'),
    ('centene',               'Centene_External',              '5',  'Centene'),
    # ── Pharma / Life Sciences ────────────────────────────────────────────────
    ('jj',                    'JJ',                            '5',  'Johnson & Johnson'),      # tenant=jj NOT jnj
    ('pfizer',                'PfizerCareers',                 '1',  'Pfizer'),
    ('msd',                   'SearchJobs',                    '5',  'Merck'),                  # tenant=msd NOT merck
    ('lilly',                 'LLY',                           '5',  'Eli Lilly'),
    ('abbott',                'abbottcareers',                 '5',  'Abbott'),
    ('bms',                   'External',                      '5',  'Bristol Myers Squibb'),
    ('amgen',                 'External',                      '5',  'Amgen'),
    ('biogen',                'External',                      '5',  'Biogen'),
    ('gilead',                'External',                      '5',  'Gilead Sciences'),
    ('regeneron',             'External',                      '5',  'Regeneron'),
    ('vertex',                'External',                      '5',  'Vertex Pharmaceuticals'),
    ('moderna',               'External',                      '5',  'Moderna'),
    ('astrazeneca',           'AstraZenecaExternal',           '5',  'AstraZeneca'),
    ('novartis',              'External',                      '5',  'Novartis'),
    ('roche',                 'External',                      '5',  'Roche'),
    ('bayer',                 'External',                      '5',  'Bayer'),
    # ── Telecom / Media ───────────────────────────────────────────────────────
    ('verizon',               'verizon-careers',               '12', 'Verizon'),               # wd12! NOT wd1
    ('att',                   'ATTCollege',                    '1',  'AT&T'),
    ('comcast',               'Comcast_Careers',               '5',  'Comcast'),
    ('tmobile',               'External',                      '1',  'T-Mobile'),
    ('disney',                'External',                      '5',  'The Walt Disney Company'),
    ('fox',                   'External',                      '5',  'Fox Corporation'),
    ('paramount',             'External',                      '5',  'Paramount'),
    # ── Energy ────────────────────────────────────────────────────────────────
    ('chevron',               'jobs',                          '5',  'Chevron'),
    ('conocophillips',        'eQuest',                        '1',  'ConocoPhillips'),
    ('bakerhughes',           'BakerHughes',                   '5',  'Baker Hughes'),
    ('bp',                    'External',                      '5',  'BP'),
    ('shell',                 'External',                      '5',  'Shell'),
    ('dukeenergy',            'search',                        '1',  'Duke Energy'),
    ('nextera',               'External',                      '5',  'NextEra Energy'),
    ('southern',              'External',                      '5',  'Southern Company'),
    ('dominionenergy',        'External',                      '5',  'Dominion Energy'),
    ('sempra',                'External',                      '5',  'Sempra Energy'),
    # ── Industrial / Aerospace / Auto ────────────────────────────────────────
    ('ge',                    'ExternalCareer',                '5',  'GE Aerospace'),
    ('siemens',               'External',                      '5',  'Siemens'),
    ('cat',                   'CaterpillarCareers',            '5',  'Caterpillar'),
    ('3m',                    'Search',                        '1',  '3M'),
    ('generalmotors',         'Careers_GM',                    '5',  'General Motors'),
    ('stellantis',            'External_Career_Site_ID01',     '3',  'Stellantis'),
    ('emerson',               'External',                      '5',  'Emerson Electric'),
    ('parker',                'External',                      '5',  'Parker Hannifin'),
    ('illinois-tool',         'External',                      '5',  'Illinois Tool Works'),
    ('danaher',               'External',                      '5',  'Danaher'),
    ('eaton',                 'External',                      '5',  'Eaton'),
    ('textron',               'External',                      '5',  'Textron'),
    ('lear',                  'External',                      '5',  'Lear Corporation'),
    ('borgwarner',            'External',                      '5',  'BorgWarner'),
    # ── Retail / Consumer ─────────────────────────────────────────────────────
    ('walmart',               'WalmartExternal',               '5',  'Walmart'),
    ('target',                'targetcareers',                 '5',  'Target'),
    ('homedepot',             'CareerDepot',                   '5',  'Home Depot'),
    ('lowes',                 'LWS_External_CS',               '5',  "Lowe's"),
    ('pg',                    '1000',                          '5',  'Procter & Gamble'),       # tenant=pg NOT proctergamble
    ('bestbuy',               'External',                      '5',  'Best Buy'),
    ('costco',                'External',                      '5',  'Costco'),
    ('tjx',                   'External',                      '5',  'TJX Companies'),
    ('ross',                  'External',                      '5',  'Ross Stores'),
    ('dollargeneral',         'External',                      '5',  'Dollar General'),
    ('dollartree',            'External',                      '5',  'Dollar Tree'),
    ('kroger',                'External',                      '5',  'Kroger'),
    ('walgreens',             'External',                      '5',  'Walgreens'),
    ('mckesson',              'External',                      '5',  'McKesson'),
    ('cardinal',              'External',                      '5',  'Cardinal Health'),
    # ── Food & Beverage ───────────────────────────────────────────────────────
    ('pepsico',               'External',                      '5',  'PepsiCo'),
    ('cocacola',              'External',                      '5',  'Coca-Cola'),
    ('ab-inbev',              'External',                      '5',  'AB InBev'),
    ('mdlz',                  'External',                      '3',  'Mondelez International'), # tenant=mdlz NOT mondelez
    ('generalmills',          'External',                      '5',  'General Mills'),
    ('kelloggs',              'External',                      '5',  'Kellanova'),
    ('campbellsoup',          'ExternalCareers_GlobalSite',    '5',  'Campbell Soup'),
    ('heinz',                 'KraftHeinz_Careers',            '1',  'Kraft Heinz'),            # tenant=heinz NOT kraftheinz
    ('conagra',               'External',                      '5',  'Conagra Brands'),
    # ── Transportation / Logistics ────────────────────────────────────────────
    ('hcmportal',             'Search',                        '5',  'UPS'),                    # tenant=hcmportal NOT ups
    ('fedex',                 'FXE-US_External',               '1',  'FedEx'),
    ('unitedairlines',        'External',                      '5',  'United Airlines'),
    ('delta',                 'External',                      '5',  'Delta Air Lines'),
    ('aa',                    'External',                      '5',  'American Airlines'),
    ('southwest',             'External',                      '5',  'Southwest Airlines'),
    ('ryder',                 'External',                      '5',  'Ryder'),
    ('werner',                'External',                      '5',  'Werner Enterprises'),
    ('jbhunt',                'External',                      '5',  'J.B. Hunt'),
    # ── Real Estate / REIT ────────────────────────────────────────────────────
    ('cbre',                  'External',                      '5',  'CBRE'),
    ('jll',                   'External',                      '5',  'JLL'),
    ('prologis',              'External',                      '5',  'Prologis'),
    ('simon',                 'External',                      '5',  'Simon Property Group'),
    # ── Northern VA / DC Metro Large Employers ────────────────────────────────
    ('fanniemae',             'External',                      '5',  'Fannie Mae'),
    ('freddiemac',            'External',                      '5',  'Freddie Mac'),
    ('navyfederal',           'External',                      '5',  'Navy Federal Credit Union'),
    ('inova',                 'External',                      '5',  'Inova Health System'),
    ('medstar',               'External',                      '5',  'MedStar Health'),
    ('aes',                   'External',                      '5',  'AES Corporation'),
    ('carlyle',               'External',                      '5',  'The Carlyle Group'),
    ('markel',                'External',                      '5',  'Markel Corporation'),
    ('geico',                 'External',                      '5',  'GEICO'),
    ('gmu',                   'External',                      '5',  'George Mason University'),
    ('gwu',                   'External',                      '5',  'George Washington University'),
    ('americanuniversity',    'External',                      '5',  'American University'),
    ('georgetown',            'External',                      '5',  'Georgetown University'),
    ('umd',                   'External',                      '5',  'University of Maryland'),
    ('vt',                    'External',                      '5',  'Virginia Tech'),
    ('pentagonfederal',       'External',                      '5',  'Pentagon Federal Credit Union'),
    ('usaa',                  'External',                      '5',  'USAA'),
    ('transamerica',          'External',                      '5',  'Transamerica'),
    ('stifel',                'External',                      '5',  'Stifel Financial'),
    # ── International / DC Associations ──────────────────────────────────────
    ('worldbank',             'External',                      '5',  'World Bank Group'),
    ('imf',                   'External',                      '5',  'IMF'),
    # ── Miami area ────────────────────────────────────────────────────────────
    ('carnival',              'External',                      '5',  'Carnival Corporation'),
    ('royalcaribbean',        'External',                      '5',  'Royal Caribbean Group'),
    ('lennar',                'External',                      '5',  'Lennar Corporation'),
]


_WORKDAY_SEARCH_TERMS = [
    'analyst', 'engineer', 'data',
    'product manager', 'business intelligence',
    'analytics', 'software',
]

def _fetch_workday(tenant: str, board: str, suffix: str, company_name: str) -> list:
    api_url = (f'https://{tenant}.wd{suffix}.myworkdayjobs.com'
               f'/wday/cxs/{tenant}/{board}/jobs')
    base_url = f'https://{tenant}.wd{suffix}.myworkdayjobs.com/en-US/{board}'
    seen_paths: set = set()
    all_out = []
    board_ok = True  # set to False if we get a non-200 to skip remaining terms

    def _fetch_term(term: str):
        """Fetch one search term. Returns (ok: bool, jobs: list)."""
        try:
            resp = requests.post(
                api_url,
                json={'appliedFacets': {}, 'limit': 20, 'offset': 0, 'searchText': term},
                headers={**_HEADERS, 'Content-Type': 'application/json'},
                timeout=_SHORT_TIMEOUT,
            )
            if resp.status_code != 200:
                return False, []  # signal board is invalid
            data = resp.json()
            postings = data.get('jobPostings') or []
            jobs = []
            for j in postings:
                path = j.get('externalPath', '')
                if not path:
                    continue
                job_url = f'{base_url}{path}'
                loc = j.get('locationsText', '')
                jobs.append((path, _job(
                    url=job_url,
                    title=j.get('title', ''),
                    company=company_name,
                    location=loc,
                    remote='remote' in loc.lower(),
                    date_posted=j.get('postedOn'),
                    source=f'workday/{tenant}',
                )))
            return True, jobs
        except Exception:
            return False, []

    # Run all term searches in parallel (capped to avoid thread explosion when nested)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures = {ex.submit(_fetch_term, term): term for term in _WORKDAY_SEARCH_TERMS}
        for f in as_completed(futures):
            ok, jobs = f.result()
            if not ok:
                board_ok = False
                # Cancel is best-effort; just stop collecting if board is invalid
                break
            for path, job in jobs:
                if path not in seen_paths:
                    seen_paths.add(path)
                    all_out.append(job)

    return all_out if board_ok or all_out else []


def _fetch_workday_legacy(tenant: str, board: str, suffix: str, company_name: str) -> list:
    """Original single-search fallback (kept for reference)."""
    url = (f'https://{tenant}.wd{suffix}.myworkdayjobs.com'
           f'/wday/cxs/{tenant}/{board}/jobs')
    try:
        resp = requests.post(
            url,
            json={'appliedFacets': {}, 'limit': 20, 'offset': 0, 'searchText': ''},
            headers={**_HEADERS, 'Content-Type': 'application/json'},
            timeout=_SHORT_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        postings = data.get('jobPostings')
        if not postings:
            return []
        out = []
        base_url = f'https://{tenant}.wd{suffix}.myworkdayjobs.com/en-US/{board}'
        for j in postings:
            path = j.get('externalPath', '')
            job_url = f'{base_url}{path}' if path else ''
            if not job_url:
                continue
            loc = j.get('locationsText', '')
            out.append(_job(
                url=job_url,
                title=j.get('title', ''),
                company=company_name,
                location=loc,
                remote='remote' in loc.lower(),
                date_posted=j.get('postedOn'),
                source=f'workday/{tenant}',
            ))
        return out
    except Exception:
        return []


def fetch_workday_all() -> list:
    cached = _cache_read('workday')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = [ex.submit(_fetch_workday, tenant, board, suffix, name)
                   for tenant, board, suffix, name in WORKDAY_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('workday', out)
    return out


# ── iCIMS ATS ─────────────────────────────────────────────────────────────────
# Many mid-cap and DC-area companies use iCIMS (Russell 2000 coverage).
# RSS feed: https://{tenant}.icims.com/jobs/search?pr=2&rss=1&ss=1
# Fails silently if the tenant slug is wrong.

ICIMS_COMPANIES = [
    # ── DC-area government services / consulting ──────────────────────────────
    ('icf',          'ICF International'),
    ('noblis',       'Noblis'),
    ('maximus',      'Maximus'),
    ('peraton',      'Peraton'),
    ('amentum',      'Amentum'),
    ('parsons',      'Parsons Corporation'),
    ('lmi',          'LMI (Logistics Management Institute)'),
    ('mitre',        'MITRE Corporation'),
    # ── Mid-cap tech / enterprise ─────────────────────────────────────────────
    ('xerox',        'Xerox'),
    ('conduent',     'Conduent'),
    ('unisys',       'Unisys'),
    ('saic-fed',     'SAIC Federal'),
    ('engility',     'Engility'),
    # ── Additional Russell 2000 mid-cap ───────────────────────────────────────
    ('saic',             'SAIC'),
    ('caci',             'CACI International'),
    ('gdit',             'General Dynamics IT'),
    ('keyw',             'KEYW Holding'),
    ('keylogic',         'KeyLogic Systems'),
    ('perspecta',        'Perspecta'),
    ('dlt',              'DLT Solutions'),
    ('technuf',          'Technuf'),
    ('sievert',          'Sievert Larsen'),
    ('gtl',              'GTL'),
    ('titan',            'Titan Corporation'),
    ('akima',            'Akima'),
    ('daybreak',         'Daybreak'),
    ('sapient',          'Sapient Government Services'),
    # ── Mid-cap Financial / Insurance ─────────────────────────────────────────
    ('synchrony',        'Synchrony Financial'),
    ('navient',          'Navient'),
    ('salliemae',        'Sallie Mae'),
    ('firstdata',        'First Data (Fiserv)'),
    ('fiserv',           'Fiserv'),
    ('jack-henry',       'Jack Henry & Associates'),
    ('ss-c',             'SS&C Technologies'),
    # ── Mid-cap Healthcare ────────────────────────────────────────────────────
    ('covanta',          'Covanta'),
    ('molina',           'Molina Healthcare'),
    ('centene',          'Centene Corporation'),
    ('wellcare',         'WellCare Health Plans'),
    ('healthnet',        'Health Net'),
    # ── Mid-cap Tech / Services ───────────────────────────────────────────────
    ('ciber',            'CIBER'),
    ('tata-consultancy', 'Tata Consultancy Services'),
    ('wipro',            'Wipro'),
    ('infosys-bpm',      'Infosys BPM'),
    ('hcl',              'HCL Technologies'),
]


def _fetch_icims(tenant: str, company_name: str) -> list:
    url = f'https://{tenant}.icims.com/jobs/search'
    try:
        resp = requests.get(url, params={'pr': '2', 'rss': '1', 'ss': '1'},
                            headers=_HEADERS, timeout=_SHORT_TIMEOUT)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        out = []
        for item in root.findall('.//item'):
            job_url = (item.findtext('link') or '').strip()
            if not job_url:
                guid = item.find('guid')
                job_url = (guid.text or '').strip() if guid is not None else ''
            if not job_url:
                continue
            raw_title = (item.findtext('title') or '').strip()
            # iCIMS titles often include location: "Data Engineer - Washington, DC"
            title = raw_title.split(' - ')[0] if ' - ' in raw_title else raw_title
            loc_hint = raw_title.split(' - ', 1)[1] if ' - ' in raw_title else ''
            out.append(_job(
                url=job_url,
                title=title,
                company=company_name,
                description=item.findtext('description') or '',
                location=loc_hint,
                remote='remote' in raw_title.lower(),
                date_posted=item.findtext('pubDate'),
                source=f'icims/{tenant}',
            ))
        return out
    except Exception:
        return []


def fetch_icims_all() -> list:
    cached = _cache_read('icims')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_icims, tenant, name)
                   for tenant, name in ICIMS_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('icims', out)
    return out


# ── Taleo / Oracle Recruiting Cloud ───────────────────────────────────────────
# Many Fortune 500 use Taleo (AT&T, Walmart, Target etc.)
# REST endpoint: POST https://{tenant}.taleo.net/careersection/rest/jobboard/v1/list/posting
# RSS fallback:  GET  https://{tenant}.taleo.net/careersection/rss.xml

TALEO_COMPANIES = [
    # ── Retail / Consumer ─────────────────────────────────────────────────────
    ('walmart',         'Walmart'),
    ('starbucks',       'Starbucks'),
    ('mcdonalds',       "McDonald's"),
    ('yum',             'Yum! Brands'),
    ('marriott',        'Marriott International'),
    ('hilton',          'Hilton'),
    # ── Telecom / Utilities ───────────────────────────────────────────────────
    ('centurylink',     'Lumen Technologies'),
    ('amdocs',          'Amdocs'),
    # ── Financial Services ────────────────────────────────────────────────────
    ('nationwide',      'Nationwide Insurance'),
    ('nationsbank',     'Bank of America (Alt)'),
    ('citi',            'Citibank'),
    # ── Healthcare ────────────────────────────────────────────────────────────
    ('medtronic',       'Medtronic'),
    ('stryker',         'Stryker'),
    ('zimmer',          'Zimmer Biomet'),
    ('hologic',         'Hologic'),
    # ── Defense / Government Services ─────────────────────────────────────────
    ('dxc',             'DXC Technology'),
    ('unisys',          'Unisys'),
    ('csra',            'CSRA'),
    ('atos',            'Atos'),
    # ── Manufacturing / Industrial ────────────────────────────────────────────
    ('ford',            'Ford Motor Company'),
    ('gm',              'General Motors'),
    ('chrysler',        'Stellantis'),
    ('toyota',          'Toyota'),
    # ── Energy ────────────────────────────────────────────────────────────────
    ('halliburton',     'Halliburton'),
    ('slb',             'SLB (Schlumberger)'),
    ('baker-hughes',    'Baker Hughes'),
    # ── Professional Services ─────────────────────────────────────────────────
    ('pwc',             'PricewaterhouseCoopers'),
    ('mckinsey',        'McKinsey & Company'),
    ('bain',            'Bain & Company'),
    ('bcg',             'Boston Consulting Group'),
    # ── DC / International Organizations ─────────────────────────────────────
    ('worldbank',       'World Bank Group'),
    ('imf',             'International Monetary Fund'),
    ('iadb',            'Inter-American Development Bank'),
    ('oas',             'Organization of American States'),
    ('usaid',           'USAID'),
    # ── DC Government Services / Consulting ───────────────────────────────────
    ('deloittefederal', 'Deloitte Federal'),
    ('grantthornton',   'Grant Thornton'),
    ('kpmgfederal',     'KPMG Federal'),
    ('icf',             'ICF International'),
    ('navigant',        'Navigant Consulting'),
    ('ftigovernment',   'FTI Consulting Government'),
]


def _fetch_taleo(tenant: str, company_name: str) -> list:
    """Try Taleo REST API then fall back to RSS."""
    # ── REST API attempt ──────────────────────────────────────────────────────
    rest_url = f'https://{tenant}.taleo.net/careersection/rest/jobboard/v1/list/posting'
    try:
        resp = requests.post(
            rest_url,
            json={
                'preferredLanguage': ['en'],
                'filters': {},
                'offset': 0,
                'limit': 100,
                'sort': {'fieldId': 'DATE', 'direction': 'DESCENDING'},
            },
            headers={**_HEADERS, 'Content-Type': 'application/json'},
            timeout=_SHORT_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            postings = data.get('postings') or data.get('jobs') or []
            if postings:
                out = []
                for j in postings:
                    job_id = j.get('contestNumber') or j.get('id', '')
                    job_url = (
                        j.get('referenceUrl')
                        or (f'https://{tenant}.taleo.net/careersection/2/jobdetail.ftl?job={job_id}' if job_id else '')
                    )
                    if not job_url:
                        continue
                    loc = (j.get('location') or {}).get('city', '') or j.get('locationDisplay', '')
                    out.append(_job(
                        url=job_url,
                        title=j.get('title', '') or j.get('jobTitle', ''),
                        company=company_name,
                        location=loc,
                        remote='remote' in loc.lower(),
                        date_posted=j.get('openDate') or j.get('lastModification'),
                        source=f'taleo/{tenant}',
                    ))
                return out
    except Exception:
        pass

    # ── RSS fallback ──────────────────────────────────────────────────────────
    rss_url = f'https://{tenant}.taleo.net/careersection/rss.xml'
    try:
        resp = requests.get(rss_url, headers=_HEADERS, timeout=_SHORT_TIMEOUT)
        if resp.status_code != 200:
            return []
        root = ET.fromstring(resp.content)
        out = []
        for item in root.findall('.//item'):
            job_url = (item.findtext('link') or '').strip()
            if not job_url:
                guid = item.find('guid')
                job_url = (guid.text or '').strip() if guid is not None else ''
            if not job_url:
                continue
            raw_title = (item.findtext('title') or '').strip()
            out.append(_job(
                url=job_url,
                title=raw_title,
                company=company_name,
                description=item.findtext('description') or '',
                date_posted=item.findtext('pubDate'),
                source=f'taleo/{tenant}',
            ))
        return out
    except Exception:
        return []


def fetch_taleo_all() -> list:
    cached = _cache_read('taleo')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_taleo, tenant, name)
                   for tenant, name in TALEO_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('taleo', out)
    return out


# ── BambooHR ATS ──────────────────────────────────────────────────────────────
# Many political tech, boutique consulting, and smaller companies use BambooHR.
# Public API: GET https://{subdomain}.bamboohr.com/careers/list  (no auth needed)

BAMBOOHR_COMPANIES = [
    # ── Political / Civic Tech ────────────────────────────────────────────────
    ('catalist',          'Catalist'),
    ('bluelabs',          'BlueLabs Analytics'),
    ('targetsmart',       'TargetSmart'),
    ('arena',             'Arena'),
    ('hustle',            'Hustle'),
    ('ngpvan',            'NGP VAN'),
    ('nationbuilder',     'NationBuilder'),
    ('everyaction',       'EveryAction'),
    ('democracyworks',    'Democracy Works'),
    ('techforcampaigns',  'Tech for Campaigns'),
    # ── DC Consulting / Think Tank ────────────────────────────────────────────
    ('thirdway',          'Third Way'),
    ('newamerica',        'New America'),
    ('resultsforamerica', 'Results for America'),
    ('bipartisanpolicy',  'Bipartisan Policy Center'),
    ('governmentperformance', 'Partnership for Public Service'),
    ('datacollaborative', 'The Data Collaborative'),
    # ── Data / Analytics Boutiques ────────────────────────────────────────────
    ('civis',             'Civis Analytics'),
    ('engagious',         'Engagious'),
    ('clearypolitical',   'Cleary Political'),
    # ── DC Think Tanks / Policy Research ─────────────────────────────────────
    ('cbpp',              'Center on Budget and Policy Priorities'),
    ('epi',               'Economic Policy Institute'),
    ('taxfoundation',     'Tax Foundation'),
    ('taxpolicycenter',   'Tax Policy Center'),
    ('childtrends',       'Child Trends'),
    ('mdrc',              'MDRC'),
    ('vera',              'Vera Institute of Justice'),
    ('westat',            'Westat'),
    ('impaq',             'IMPAQ International'),
    ('icf',               'ICF International (BambooHR)'),
    # ── DC Political / Civic Tech ─────────────────────────────────────────────
    ('actblue',           'ActBlue'),
    ('acronym',           'ACRONYM'),
    ('nextgenamerica',    'NextGen America'),
    ('vpcaction',         'Voter Participation Center'),
    ('techforcampaigns',  'Tech for Campaigns'),
    ('campaignlegal',     'Campaign Legal Center'),
    ('commoncause',       'Common Cause'),
    # ── DC Advocacy / Nonprofits ──────────────────────────────────────────────
    ('hrc',               'Human Rights Campaign'),
    ('naacp',             'NAACP'),
    ('ppfa',              'Planned Parenthood'),
    ('naral',             'NARAL Pro-Choice America'),
    ('aarp',              'AARP (BambooHR)'),
    ('wwfus',             'World Wildlife Fund US'),
    ('edfaction',         'Environmental Defense Fund'),
    ('sierraclub',        'Sierra Club'),
    # ── International Development ─────────────────────────────────────────────
    ('fhi360',            'FHI 360'),
    ('chemonics',         'Chemonics International'),
    ('dai',               'DAI Global'),
    ('winrock',           'Winrock International'),
    ('mercycorps',        'Mercy Corps'),
    ('counterpart',       'Counterpart International'),
    ('irc',               'International Rescue Committee'),
]


def _fetch_bamboohr(subdomain: str, company_name: str) -> list:
    try:
        resp = requests.get(
            f'https://{subdomain}.bamboohr.com/careers/list',
            headers=_HEADERS, timeout=_SHORT_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        out = []
        for j in resp.json().get('result', []):
            job_id = j.get('id') or j.get('jobOpeningId', '')
            url = f'https://{subdomain}.bamboohr.com/careers/{job_id}' if job_id else ''
            if not url:
                continue
            loc = j.get('location', {})
            if isinstance(loc, dict):
                loc_str = ', '.join(filter(None, [loc.get('city'), loc.get('state')]))
            else:
                loc_str = str(loc)
            out.append(_job(
                url=url,
                title=j.get('title', '') or j.get('jobTitle', ''),
                company=company_name,
                location=loc_str,
                remote='remote' in (j.get('employmentStatusLabel', '') or '').lower()
                       or 'remote' in loc_str.lower(),
                date_posted=j.get('datePosted'),
                source=f'bamboohr/{subdomain}',
            ))
        return out
    except Exception:
        return []


def fetch_bamboohr_all() -> list:
    cached = _cache_read('bamboohr')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_bamboohr, sub, name)
                   for sub, name in BAMBOOHR_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('bamboohr', out)
    return out


# ── Workable ATS ─────────────────────────────────────────────────────────────
# Public API: POST https://apply.workable.com/api/v3/accounts/{slug}/jobs

WORKABLE_COMPANIES = [
    # ── Tech / SaaS ───────────────────────────────────────────────────────────
    ('typeform',             'Typeform'),
    ('skyscanner',           'Skyscanner'),
    ('brainly',              'Brainly'),
    ('personio',             'Personio'),
    ('taxfix',               'Taxfix'),
    ('storyblok',            'Storyblok'),
    ('factorial',            'Factorial HR'),
    ('paddle',               'Paddle'),
    ('loom',                 'Loom'),
    ('hotjar',               'Hotjar'),
    # ── DC / Policy / Gov-Adjacent ────────────────────────────────────────────
    ('quorum',               'Quorum'),
    ('popvox',               'POPVOX'),
    ('fiscalnote',           'FiscalNote'),
    ('democracyforward',     'Democracy Forward'),
    ('aaas',                 'AAAS'),
    ('healthydemocracy',     'Healthy Democracy'),
    # ── Data / Analytics ──────────────────────────────────────────────────────
    ('supermetrics',         'Supermetrics'),
    ('klipfolio',            'Klipfolio'),
    ('chartmogul',           'ChartMogul'),
    ('baremetrics',          'Baremetrics'),
    # ── International Development ─────────────────────────────────────────────
    ('pact',                 'Pact'),
    ('internews',            'Internews'),
    ('globalintegrity',      'Global Integrity'),
    ('developmentgateway',   'Development Gateway'),
    # ── Media / Research ──────────────────────────────────────────────────────
    ('themarkup',            'The Markup'),
    ('citylab',              'Bloomberg CityLab'),
    # ── AI / ML / Data ────────────────────────────────────────────────────────
    ('dataiku',              'Dataiku'),
    ('hex',                  'Hex Technologies'),
    ('deepnote',             'Deepnote'),
    ('tecton',               'Tecton'),
    ('gretel',               'Gretel'),
    ('ataccama',             'Ataccama'),
    ('precisely',            'Precisely'),
    # ── Product Analytics / CX ────────────────────────────────────────────────
    ('maze',                 'Maze'),
    ('appcues',              'Appcues'),
    ('pendo-io',             'Pendo'),
    ('gainsight',            'Gainsight'),
    ('totango',              'Totango'),
    ('churnzero',            'ChurnZero'),
    ('vitally',              'Vitally'),
    ('gong-io',              'Gong'),
    ('salesloft',            'Salesloft'),
    ('apollo-io',            'Apollo.io'),
    ('zoominfo',             'ZoomInfo'),
    ('clearbit',             'Clearbit'),
    ('bombora',              'Bombora'),
    ('g2',                   'G2'),
    ('trustpilot',           'Trustpilot'),
    # ── Fintech / Payments ────────────────────────────────────────────────────
    ('patreon',              'Patreon'),
    ('substack',             'Substack'),
    ('chargebee',            'Chargebee'),
    ('recurly',              'Recurly'),
    ('adyen',                'Adyen'),
    ('checkout-com',         'Checkout.com'),
    ('rapyd',                'Rapyd'),
    ('payoneer',             'Payoneer'),
    ('tipalti',              'Tipalti'),
    ('expensify',            'Expensify'),
    ('navan',                'Navan'),
    ('airbase',              'Airbase'),
    # ── HR / Work Tech ────────────────────────────────────────────────────────
    ('hibob',                'HiBob'),
    ('oysterhr',             'Oyster HR'),
    ('velocity-global',      'Velocity Global'),
    # ── E-commerce / Logistics ────────────────────────────────────────────────
    ('recharge',             'Recharge'),
    ('loop-returns',         'Loop Returns'),
    ('narvar',               'Narvar'),
    ('shipbob',              'ShipBob'),
    ('flexport',             'Flexport'),
    ('stord',                'Stord'),
    # ── Security ──────────────────────────────────────────────────────────────
    ('1password',            '1Password'),
    ('dashlane',             'Dashlane'),
    ('bitwarden',            'Bitwarden'),
    ('snyk',                 'Snyk'),
    ('lacework',             'Lacework'),
    ('orca-security',        'Orca Security'),
    ('wiz-io',               'Wiz'),
    ('axonius',              'Axonius'),
    ('recorded-future',      'Recorded Future'),
    # ── Healthcare Tech ───────────────────────────────────────────────────────
    ('spring-health',        'Spring Health'),
    ('virta-health',         'Virta Health'),
    ('noom',                 'Noom'),
    ('omada-health',         'Omada Health'),
    ('betterhelp',           'BetterHelp'),
    ('talkspace',            'Talkspace'),
    # ── Verified by research agent ────────────────────────────────────────────
    ('groundtruth',          'GroundTruth'),
    ('rokt',                 'Rokt'),
    ('zerofox',              'ZeroFox'),
    ('aimpoint-digital',     'Aimpoint Digital'),
    ('superdispatch',        'Super Dispatch'),
    ('nucleusteq',           'NucleusTeq'),
    ('bear-robotics',        'Bear Robotics'),
    ('compass-datacenters',  'Compass Datacenters'),
    ('nationsbenefits',      'NationsBenefits'),
    ('netrix-global',        'Netrix Global'),
]


def _fetch_workable(slug: str, company_name: str) -> list:
    try:
        resp = requests.post(
            f'https://apply.workable.com/api/v3/accounts/{slug}/jobs',
            json={'limit': 100, 'offset': 0, 'query': ''},
            headers={**_HEADERS, 'Content-Type': 'application/json'},
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        out = []
        for j in resp.json().get('results', []):
            code = j.get('shortcode', '')
            if not code:
                continue
            url = f'https://apply.workable.com/{slug}/j/{code}'
            loc = j.get('location') or {}
            loc_str = ', '.join(filter(None, [loc.get('city', ''), loc.get('country', '')]))
            remote = j.get('remote', False) or 'remote' in loc_str.lower()
            out.append(_job(
                url=url,
                title=j.get('title', ''),
                company=company_name,
                description=j.get('description', ''),
                location=loc_str,
                remote=remote,
                date_posted=j.get('published_on'),
                source=f'workable/{slug}',
            ))
        return out
    except Exception:
        return []


def fetch_workable_all() -> list:
    cached = _cache_read('workable')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_workable, slug, name)
                   for slug, name in WORKABLE_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('workable', out)
    return out


# ── Direct career pages ───────────────────────────────────────────────────────
# Companies that post jobs only (or primarily) on their own website.
# Uses JSON-LD extraction first, then BeautifulSoup link heuristics.
# Fails silently for JS-rendered pages — those are handled by ATS integrations.

COMPANY_CAREER_PAGES = [
    # ── Political / Civic Tech ────────────────────────────────────────────────
    ('https://catalist.us/about/careers/',                   'Catalist'),
    ('https://civisanalytics.com/join-our-team/',            'Civis Analytics'),
    ('https://bluelabs.com/who-we-are/careers/',             'BlueLabs Analytics'),
    ('https://claritycampaignlabs.com/careers/',             'Clarity Campaign Labs'),
    ('https://www.l2political.com/about/careers/',           'L2 Political'),
    # ── Think Tanks / Policy Research (DC) ───────────────────────────────────
    ('https://www.pewresearch.org/about/careers/',           'Pew Research Center'),
    ('https://www.brookings.edu/about-brookings/careers-at-brookings/', 'Brookings Institution'),
    ('https://www.americanprogress.org/about/jobs/',         'Center for American Progress'),
    ('https://www.urban.org/about/careers',                  'Urban Institute'),
    ('https://www.rand.org/jobs.html',                       'RAND Corporation'),
    ('https://www.csis.org/programs/careers',                'CSIS'),
    ('https://www.wilsoncenter.org/careers',                 'Wilson Center'),
    ('https://carnegieendowment.org/about/careers',          'Carnegie Endowment'),
    ('https://www.atlanticcouncil.org/careers/',             'Atlantic Council'),
    ('https://bipartisanpolicy.org/about/careers/',          'Bipartisan Policy Center'),
    ('https://www.thirdway.org/careers',                     'Third Way'),
    ('https://www.newamerica.org/jobs/',                     'New America'),
    ('https://www.heritage.org/about-heritage/careers',      'Heritage Foundation'),
    # ── Government-Adjacent Tech ──────────────────────────────────────────────
    ('https://www.usds.gov/apply',                           'US Digital Service'),
    ('https://18f.gsa.gov/join/',                            '18F / GSA'),
    ('https://www.gao.gov/careers',                          'GAO'),
    # ── DC Data / Analytics Boutiques ────────────────────────────────────────
    ('https://www.analysisgroup.com/careers/',               'Analysis Group'),
    ('https://www.nera.com/careers.html',                    'NERA Economic Consulting'),
    ('https://www.lmi.org/lmi-careers',                      'LMI'),
    ('https://www.mathematica.org/career-opportunities',     'Mathematica'),
    ('https://www.rti.org/careers',                          'RTI International'),
    ('https://www.icf.com/careers',                          'ICF'),
    # ── Media / Data Journalism ───────────────────────────────────────────────
    ('https://fivethirtyeight.com/jobs/',                    'FiveThirtyEight'),
    ('https://www.politico.com/careers',                     'Politico'),
    ('https://careers.theatlantic.com/',                     'The Atlantic'),
    ('https://jobs.washingtonpost.com/',                     'Washington Post'),
    ('https://www.axios.com/jobs',                           'Axios'),
    ('https://www.rollcall.com/jobs/',                       'Roll Call'),
    ('https://thehill.com/about/advertise/careers/',         'The Hill'),
    ('https://www.npr.org/about-npr/181953728/work-at-npr',  'NPR'),
    ('https://careers.nationalgeographic.com/',              'National Geographic Society'),
    # ── International Organizations ───────────────────────────────────────────
    ('https://jobs.worldbank.org/en/jobs',                   'World Bank Group'),
    ('https://www.imf.org/en/About/Recruitment',             'International Monetary Fund'),
    ('https://www.iadb.org/en/careers',                      'Inter-American Development Bank'),
    ('https://www.mcc.gov/careers',                          'Millennium Challenge Corporation'),
    ('https://www.ifc.org/wps/wcm/connect/corp_ext_content/ifc_external_corporate_site/about+ifc_new/ifc+fast+facts/careers',
                                                             'IFC'),
    # ── DC Policy / Research Organizations ───────────────────────────────────
    ('https://www.westat.com/careers',                       'Westat'),
    ('https://www.childtrends.org/what-we-do/our-people/career-opportunities', 'Child Trends'),
    ('https://www.mdrc.org/about/careers-mdrc',              'MDRC'),
    ('https://www.piie.com/about/careers',                   'Peterson Institute (PIIE)'),
    ('https://www.cna.org/careers',                          'CNA Corporation'),
    ('https://www.ida.org/careers',                          'Institute for Defense Analyses'),
    ('https://stimson.org/careers/',                         'Stimson Center'),
    ('https://www.gmfus.org/about/careers',                  'German Marshall Fund'),
    ('https://www.cbpp.org/careers',                         'Center on Budget and Policy Priorities'),
    ('https://www.epi.org/jobs/',                            'Economic Policy Institute'),
    ('https://itif.org/careers/',                            'ITIF'),
    ('https://www.rstreet.org/work-with-us/',                'R Street Institute'),
    # ── DC Consulting / Government Adjacent ───────────────────────────────────
    ('https://guidehouse.com/careers',                       'Guidehouse'),
    ('https://www.chemonics.com/life-at-chemonics/careers/', 'Chemonics International'),
    ('https://www.dai.com/careers',                          'DAI Global'),
    ('https://www.fhi360.org/careers',                       'FHI 360'),
    ('https://www.winrock.org/careers/',                     'Winrock International'),
    ('https://jobs.mercycorps.org/',                         'Mercy Corps'),
    # ── DC Civic / Political Tech ─────────────────────────────────────────────
    ('https://secure.actblue.com/careers',                   'ActBlue'),
    ('https://www.opensecrets.org/about/jobs',               'OpenSecrets'),
    ('https://www.brennancenter.org/join-us/jobs',           'Brennan Center for Justice'),
    ('https://protectdemocracy.org/about/jobs/',             'Protect Democracy'),
    ('https://www.commoncause.org/careers/',                 'Common Cause'),
    # ── DC Associations / Nonprofits ──────────────────────────────────────────
    ('https://careers.aarp.org/',                            'AARP'),
    ('https://www.redcross.org/about-us/careers.html',       'American Red Cross'),
    ('https://careers.worldwildlife.org/',                   'World Wildlife Fund'),
    ('https://www.edf.org/careers',                          'Environmental Defense Fund'),
    ('https://www.hrc.org/careers',                          'Human Rights Campaign'),
    ('https://www.naacp.org/job-openings/',                  'NAACP'),
    ('https://www.sierraclub.org/jobs',                      'Sierra Club'),
    # ── Federal Contractors / Defense ────────────────────────────────────────
    ('https://careers.boozallen.com/jobs/SearchJobs',            'Booz Allen Hamilton'),
    ('https://www.leidos.com/careers/open-positions',            'Leidos'),
    ('https://jobs.saic.com/jobs',                               'SAIC'),
    ('https://careers.mantech.com/jobs/SearchJobs',              'ManTech International'),
    ('https://careers.peraton.com/jobs',                         'Peraton'),
    ('https://www.mitre.org/careers/job-openings',               'MITRE Corporation'),
    ('https://careers.caci.com/global/en/search-results',        'CACI International'),
    # ── Major DC-Area Tech Employers ─────────────────────────────────────────
    ('https://careers.capitalone.com/job-search-results',        'Capital One'),
    ('https://jobs.bah.com/',                                    'Booz Allen (jobs)'),
    ('https://www.nga.mil/careers/',                             'NGA (National Geospatial)'),
    ('https://careers.microsoft.com/us/en',                      'Microsoft'),
    ('https://www.amazon.jobs/en/search',                        'Amazon'),
    # ── Additional DC Media / Journalism ─────────────────────────────────────
    ('https://www.washingtonpost.com/newsroom/job-openings/',     'Washington Post'),
    ('https://www.c-span.org/about/careers/',                    'C-SPAN'),
    ('https://www.nationalsecurity.news/careers/',               'National Security News'),
    # ── Universities / Education ─────────────────────────────────────────────
    ('https://hr.nih.gov/jobs',                                  'NIH'),
    ('https://www.georgetown.edu/work-at-georgetown/',           'Georgetown University'),
    ('https://www.american.edu/hr/jobs.cfm',                     'American University'),
    ('https://www.gwu.edu/employment',                           'George Washington University'),
    # ── More Research / International ────────────────────────────────────────
    ('https://www.worldbank.org/en/about/careers/programs-and-internships', 'World Bank'),
    ('https://www.usaid.gov/careers',                            'USAID'),
    ('https://www.state.gov/careers/',                           'State Department'),
    ('https://www.commerce.gov/hr/careers',                      'Dept of Commerce'),
]


def _fetch_career_page(url: str, company_name: str) -> list:
    """
    Fetch a company career page and extract job postings.
    Tries JSON-LD first, then looks for job links via BeautifulSoup heuristics.
    """
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=8)
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, 'html.parser')
        out = []

        # ── JSON-LD extraction ────────────────────────────────────────────────
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = _json.loads(script.string or '')
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if (item.get('@type') or '').lower() == 'jobposting':
                        job_url = item.get('url') or item.get('sameAs') or url
                        loc_raw = item.get('jobLocation') or {}
                        if isinstance(loc_raw, dict):
                            loc_str = (loc_raw.get('address') or {}).get('addressLocality', '')
                        elif isinstance(loc_raw, list) and loc_raw:
                            loc_str = (loc_raw[0].get('address') or {}).get('addressLocality', '')
                        else:
                            loc_str = ''
                        out.append(_job(
                            url=job_url,
                            title=item.get('title', ''),
                            company=company_name,
                            description=item.get('description', '')[:3000],
                            location=loc_str,
                            remote='remote' in (item.get('employmentType', '') or '').lower(),
                            salary=str((item.get('baseSalary') or {}).get('value', '') or '') or None,
                            date_posted=item.get('datePosted'),
                            source=f'careers/{company_name.lower().replace(" ", "_")}',
                        ))
            except Exception:
                continue

        if out:
            return out

        # ── HTML heuristic: find links that look like job postings ────────────
        from urllib.parse import urljoin, urlparse
        base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        seen = set()
        job_path_hints = ('/job/', '/jobs/', '/careers/', '/opening', '/position', '/role/')
        for a in soup.find_all('a', href=True):
            href = a['href']
            full = urljoin(base, href) if not href.startswith('http') else href
            # Must be same domain, contain a job-path hint, and have a meaningful title
            if urlparse(full).netloc != urlparse(url).netloc:
                continue
            if not any(hint in full.lower() for hint in job_path_hints):
                continue
            text = a.get_text(strip=True)
            if len(text) < 5 or len(text) > 120:
                continue
            if full in seen:
                continue
            seen.add(full)
            out.append(_job(
                url=full,
                title=text,
                company=company_name,
                source=f'careers/{company_name.lower().replace(" ", "_")}',
            ))

        return out
    except Exception:
        return []


def fetch_career_pages_all() -> list:
    cached = _cache_read('career_pages')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fetch_career_page, url, name)
                   for url, name in COMPANY_CAREER_PAGES]
        for f in as_completed(futures):
            out.extend(f.result())
    # Deduplicate by URL
    seen, deduped = set(), []
    for j in out:
        u = j.get('url', '')
        if u and u not in seen:
            seen.add(u)
            deduped.append(j)
    _cache_write('career_pages', deduped)
    return deduped


# ── Adzuna API ────────────────────────────────────────────────────────────────
# Aggregates from company websites + job boards. Location-filtered so it
# catches smaller/mid-size companies (Russell 2000) that don't have an ATS API.
# Requires env vars: ADZUNA_APP_ID, ADZUNA_APP_KEY
# Free tier: 25k calls/month.  https://developer.adzuna.com/

_ADZUNA_QUERIES = [
    # Tech / data
    'data analyst', 'data engineer', 'software engineer',
    'business intelligence', 'product manager', 'analytics engineer',
    'data scientist', 'database engineer',
    'tableau developer', 'databricks engineer',
    # Political / civic tech
    'political data analyst', 'government technology', 'civic tech',
    'campaign analytics', 'policy data analyst',
    # Non-software roles
    'policy analyst', 'policy director', 'policy manager',
    'communications director', 'communications manager',
    'operations manager', 'operations director',
    'research analyst', 'research director',
    'strategy consultant', 'strategic planning',
    'marketing analyst', 'marketing manager',
    'public affairs', 'government relations',
    'advocacy director', 'advocacy manager',
    'program director', 'nonprofit management',
    'outreach manager', 'partnerships manager',
]
_ADZUNA_LOCATIONS = [
    ('washington dc', 50),
    ('miami fl', 40),
    ('austin tx', 40),
]
_ADZUNA_RADIUS_MILES = 50


def fetch_adzuna(queries: list = None, min_salary: int = 100_000) -> list:
    """Fetch from Adzuna with server-side salary gating — only high-paying jobs
    are returned, saving API quota on irrelevant listings."""
    import os
    app_id  = os.environ.get('ADZUNA_APP_ID')  or os.environ.get('ADZUNA_ID')
    app_key = os.environ.get('ADZUNA_APP_KEY') or os.environ.get('ADZUNA_KEY')
    if not app_id or not app_key:
        return []

    terms = queries or _ADZUNA_QUERIES
    seen = set()
    out = []

    def _fetch_one(what: str, where: str, radius: int) -> list:
        results = []
        try:
            resp = requests.get(
                'https://api.adzuna.com/v1/api/jobs/us/search/1',
                params={
                    'app_id': app_id,
                    'app_key': app_key,
                    'what': what,
                    'where': where,
                    'distance': radius,
                    'results_per_page': 50,
                    'salary_min': min_salary,      # server-side salary gate
                    'salary_include_unknown': 0,   # exclude no-salary listings
                    'content-type': 'application/json',
                },
                headers=_HEADERS, timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            for j in resp.json().get('results', []):
                url = j.get('redirect_url', '')
                if not url:
                    continue
                sal = None
                lo, hi = j.get('salary_min'), j.get('salary_max')
                if lo and hi:
                    sal = f'${int(lo):,}–${int(hi):,}'
                elif lo:
                    sal = f'${int(lo):,}+'
                loc_parts = (j.get('location') or {}).get('area', [])
                loc = ', '.join(loc_parts[-2:]) if loc_parts else ''
                results.append(_job(
                    url=url,
                    title=j.get('title', ''),
                    company=(j.get('company') or {}).get('display_name', ''),
                    description=j.get('description', ''),
                    location=loc,
                    salary=sal,
                    date_posted=j.get('created'),
                    source='adzuna',
                ))
        except Exception:
            pass
        return results

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [
            ex.submit(_fetch_one, q, where, radius)
            for q in terms
            for (where, radius) in _ADZUNA_LOCATIONS
        ]
        for f in as_completed(futures):
            for job in f.result():
                url = job.get('url', '')
                if url and url not in seen:
                    seen.add(url)
                    out.append(job)
    return out


# ── USAJobs API ───────────────────────────────────────────────────────────────
# Official US federal government jobs API. Free, DC-focused, no rate limit issues.
# Requires env vars: USAJOBS_API_KEY, USAJOBS_EMAIL (used as User-Agent)
# Register at: https://developer.usajobs.gov/apirequest/

_USAJOBS_QUERIES = [
    'data analyst', 'data engineer', 'software engineer',
    'business intelligence', 'IT specialist', 'database administrator',
    'information technology', 'cybersecurity analyst',
    'tableau', 'databricks', 'data analytics',
    'policy analyst', 'political data',
    'policy director', 'communications specialist',
    'operations analyst', 'research director',
    'public affairs specialist', 'program analyst',
    'outreach coordinator', 'government relations',
]


def fetch_usajobs(queries: list = None) -> list:
    import os
    api_key = os.environ.get('USAJOBS_API_KEY')
    email = os.environ.get('USAJOBS_EMAIL')
    if not api_key or not email:
        return []

    terms = queries or _USAJOBS_QUERIES
    seen = set()
    out = []

    def _fetch_one(keyword: str) -> list:
        try:
            resp = requests.get(
                'https://data.usajobs.gov/api/search',
                params={
                    'Keyword': keyword,
                    'LocationName': 'Washington, DC',
                    'Radius': 50,
                    'ResultsPerPage': 50,
                    'Fields': 'Min',
                },
                headers={
                    'Host': 'data.usajobs.gov',
                    'User-Agent': email,
                    'Authorization-Key': api_key,
                },
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            results = []
            items = (resp.json().get('SearchResult') or {}).get('SearchResultItems', [])
            for item in items:
                mv = item.get('MatchedObjectDescriptor') or {}
                url = mv.get('ApplyURI', [None])[0] if mv.get('ApplyURI') else ''
                if not url:
                    url = mv.get('PositionURI', '')
                if not url:
                    continue
                locs = [l.get('LocationName', '') for l in (mv.get('PositionLocation') or [])]
                sal_range = (mv.get('PositionRemuneration') or [{}])[0]
                sal = None
                lo = sal_range.get('MinimumRange')
                hi = sal_range.get('MaximumRange')
                if lo and hi:
                    sal = f'${int(float(lo)):,}–${int(float(hi)):,}'
                results.append(_job(
                    url=url,
                    title=mv.get('PositionTitle', ''),
                    company=mv.get('OrganizationName', '') or mv.get('DepartmentName', ''),
                    description=mv.get('QualificationSummary', ''),
                    location=', '.join(locs),
                    salary=sal,
                    date_posted=mv.get('PublicationStartDate'),
                    source='usajobs',
                ))
            return results
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_one, q) for q in terms]
        for f in as_completed(futures):
            for job in f.result():
                url = job.get('url', '')
                if url and url not in seen:
                    seen.add(url)
                    out.append(job)
    return out


# ── SmartRecruiters ───────────────────────────────────────────────────────────
# Massive ATS used by thousands of companies (LinkedIn, Bosch, etc.).
# Public search API — no auth required.
# GET https://www.smartrecruiters.com/JobSearchApi/v1/listing?q=...&country=us&limit=100

def fetch_smartrecruiters(query: str) -> list:
    try:
        resp = _get(
            'https://www.smartrecruiters.com/JobSearchApi/v1/listing',
            params={'q': query, 'country': 'us', 'limit': 100},
        )
        resp.raise_for_status()
        out = []
        for j in resp.json().get('content', []):
            url = j.get('ref', '')
            if not url:
                continue
            loc = j.get('location') or {}
            city = loc.get('city', '')
            region = (loc.get('region') or {}).get('regionCode', '')
            loc_str = ', '.join(filter(None, [city, region]))
            is_remote = bool(loc.get('remote'))
            out.append(_job(
                url=url,
                title=j.get('name', ''),
                company=(j.get('company') or {}).get('name', ''),
                location=loc_str,
                remote=is_remote or 'remote' in loc_str.lower(),
                date_posted=j.get('releasedDate'),
                source='smartrecruiters',
            ))
        return out
    except Exception:
        return []


# ── Breezy HR ATS ─────────────────────────────────────────────────────────────
# Used by many startups and mid-size companies.
# GET https://{slug}.breezy.hr/json  — no auth required.

def _fetch_breezyhr(slug: str, company_name: str) -> list:
    try:
        resp = _get(f'https://{slug}.breezy.hr/json', timeout=_SHORT_TIMEOUT)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list):
            return []
        out = []
        for j in data:
            job_id = j.get('_id', '')
            url = f'https://{slug}.breezy.hr/p/{job_id}' if job_id else ''
            if not url:
                continue
            loc = j.get('location') or {}
            loc_str = loc.get('name', '') if isinstance(loc, dict) else str(loc)
            out.append(_job(
                url=url,
                title=j.get('name', ''),
                company=company_name,
                location=loc_str,
                remote=j.get('type', '') == 'remote' or 'remote' in loc_str.lower(),
                date_posted=j.get('creation_date'),
                source=f'breezyhr/{slug}',
            ))
        return out
    except Exception:
        return []


BREEZY_COMPANIES = [
    # ── Data / Analytics ─────────────────────────────────────────────────────
    ('chartmogul',       'ChartMogul'),
    ('growthbook',       'GrowthBook'),
    ('metronome',        'Metronome'),
    ('propeldata',       'Propel Data'),
    # ── Political / Civic Tech ────────────────────────────────────────────────
    ('mobilizeamerica',  'Mobilize America'),
    ('indigov',          'Indigov'),
    ('quorum',           'Quorum Analytics'),
    ('plural-policy',    'Plural Policy'),
    # ── DC Policy / Consulting ────────────────────────────────────────────────
    ('nscresearch',      'NSC Research'),
    ('policylink',       'PolicyLink'),
    # ── Tech / SaaS ───────────────────────────────────────────────────────────
    ('invision',         'InVision'),
    ('miro',             'Miro'),
    ('lucid',            'Lucid Software'),
    ('podium',           'Podium'),
    ('entrata',          'Entrata'),
    ('weave',            'Weave'),
    ('ivanti',           'Ivanti'),
    ('connectwise',      'ConnectWise'),
    ('kaseya',           'Kaseya'),
    ('solarwinds',       'SolarWinds'),
    ('goto',             'GoTo'),
    ('nutanix',          'Nutanix'),
    ('commvault',        'Commvault'),
    ('veeam',            'Veeam'),
    ('backblaze',        'Backblaze'),
    # ── Marketing / AdTech ────────────────────────────────────────────────────
    ('meltwater',        'Meltwater'),
    ('semrush',          'Semrush'),
    ('similarweb',       'SimilarWeb'),
    # ── IT Services ───────────────────────────────────────────────────────────
    ('dxc-technology',   'DXC Technology'),
    ('conduent',         'Conduent'),
    ('unisys',           'Unisys'),
    ('cognizant',        'Cognizant'),
    ('hcl-technologies', 'HCL Technologies'),
    ('ltimindtree',      'LTIMindtree'),
    # ── Healthcare ────────────────────────────────────────────────────────────
    ('guardant-health',  'Guardant Health'),
    ('flatiron-health',  'Flatiron Health'),
    ('tempus',           'Tempus'),
    ('illumina',         'Illumina'),
    # ── Verified by research agent ────────────────────────────────────────────
    ('aimpoint-digital',     'Aimpoint Digital'),
    ('bear-robotics',        'Bear Robotics'),
    ('bexorg',               'Bexorg'),
    ('compass-datacenters',  'Compass Datacenters'),
    ('datadrive',            'DataDrive'),
    ('founders-workshop',    'Founders Workshop'),
    ('halliday',             'Halliday'),
    ('insiten',              'Insiten'),
    ('ltg',                  'LTG'),
    ('nationsbenefits',      'NationsBenefits'),
    ('navaide',              'Navaide'),
    ('netrix-global',        'Netrix Global'),
    ('nucleusteq',           'NucleusTeq'),
    ('sa-global',            'sa.global'),
    ('seeknow',              'Seek Now'),
    ('seidor',               'SEIDOR'),
    ('superdispatch',        'Super Dispatch'),
    ('techsmart',            'TechSmart'),
    ('turaco',               'Turaco'),
    ('zerofox',              'ZeroFox'),
]


def fetch_breezyhr_all() -> list:
    cached = _cache_read('breezyhr')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [ex.submit(_fetch_breezyhr, slug, name)
                   for slug, name in BREEZY_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('breezyhr', out)
    return out


# ── Custom company watchlist ───────────────────────────────────────────────────
# User-defined companies. We try each ATS in order before falling back to
# direct URL scraping (for JS-light pages) or a SmartRecruiters name search.

import re as _re


def _name_to_slug(name: str) -> str:
    """Convert a company name to a likely ATS slug (lowercase, hyphenated)."""
    slug = name.lower()
    for suffix in (' inc', ' llc', ' ltd', ' corp', ' corporation', ' group',
                   ' analytics', ' technologies', ' solutions', ' consulting',
                   ' research', ' labs', ', inc', ', llc', '.com'):
        slug = slug.replace(suffix, '')
    slug = _re.sub(r'[^a-z0-9]+', '-', slug).strip('-')
    return slug


def _try_company_ats(name: str, career_url: str = '') -> list:
    """
    Try multiple ATS platforms for a single company.
    Returns the first non-empty result set found.
    """
    if not name:
        return []
    slug = _name_to_slug(name)

    # Direct URL first (if provided)
    if career_url:
        results = _fetch_career_page(career_url, name)
        if results:
            return results

    # Try each ATS API in parallel
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {
            ex.submit(_fetch_greenhouse, slug, name): 'greenhouse',
            ex.submit(_fetch_lever,      slug, name): 'lever',
            ex.submit(_fetch_ashby,      slug, name): 'ashby',
            ex.submit(_fetch_breezyhr,   slug, name): 'breezyhr',
        }
        for f in as_completed(futs):
            try:
                results = f.result()
                if results:
                    # Cancel remaining (best-effort — futures already submitted)
                    return results
            except Exception:
                pass

    # Last resort: SmartRecruiters company-name search
    try:
        resp = _get(
            'https://www.smartrecruiters.com/JobSearchApi/v1/listing',
            params={'q': name, 'country': 'us', 'limit': 50},
        )
        if resp.ok:
            matches = []
            for j in resp.json().get('content', []):
                co = (j.get('company') or {}).get('name', '')
                if co.lower() == name.lower():
                    url = j.get('ref', '')
                    if url:
                        loc = j.get('location') or {}
                        city = loc.get('city', '')
                        loc_str = city
                        matches.append(_job(
                            url=url, title=j.get('name', ''), company=co,
                            location=loc_str, remote=bool(loc.get('remote')),
                            date_posted=j.get('releasedDate'),
                            source=f'smartrecruiters/watchlist',
                        ))
            if matches:
                return matches
    except Exception:
        pass

    return []


def fetch_custom_companies(watchlist: list) -> list:
    """
    watchlist: list of dicts with 'name' (required) and optionally 'url'.
    Tries to find jobs for each company via ATS detection or URL scraping.
    """
    if not watchlist:
        return []
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [
            ex.submit(_try_company_ats, w.get('name', ''), w.get('url', ''))
            for w in watchlist if w.get('name', '').strip()
        ]
        for f in as_completed(futures):
            try:
                out.extend(f.result())
            except Exception:
                pass
    return out


def discover_companies_in_city(city: str, skills: list, target_roles: list) -> list:
    """
    Use Gemini to generate a list of companies in a given city/metro area
    that actively hire for data, analytics, and tech roles.

    Returns list of dicts: {name, ats, slug, career_url, industry}
    """
    try:
        import os as _os_inner
        from google import genai as _genai
        _gclient = _genai.Client(api_key=_os_inner.environ['GEMINI_API_KEY'])
    except Exception:
        return []

    skills_str = ', '.join(skills[:15]) if skills else 'data analysis, SQL, Python'
    roles_str = ', '.join(target_roles[:5]) if target_roles else 'data analyst, data engineer'

    prompt = (
        f"List 25 real companies with significant presence in {city} that hire {roles_str}.\n"
        f"Candidate skills: {skills_str}\n\n"
        f"For each company return a JSON object with:\n"
        f"  name: company name (string)\n"
        f"  ats: job board they use — one of: greenhouse, lever, workday, ashby, workable, icims, bamboohr, taleo, other\n"
        f"  slug: their board slug (e.g. for greenhouse boards-api use 'companyname', for lever use 'company-name'). Empty string if unknown.\n"
        f"  career_url: direct URL to their careers/jobs page\n"
        f"  industry: one of: tech, consulting, government, nonprofit, media, finance, healthcare, education, defense, other\n\n"
        f"Include a mix of: tech companies, consulting firms, government contractors, think tanks, nonprofits.\n"
        f"Only include companies you're confident actually have a presence in {city}.\n"
        f"Return ONLY a valid JSON array. No markdown fences, no commentary."
    )

    try:
        response = _gclient.models.generate_content(
            model='gemini-2.5-flash', contents=prompt)
        text = response.text.strip()
        if text.startswith('```'):
            text = text.split('```', 2)[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.rsplit('```', 1)[0].strip()
        import json as _json_inner
        result = _json_inner.loads(text)
        if isinstance(result, list):
            # Validate and clean each entry
            out = []
            for c in result:
                if isinstance(c, dict) and c.get('name'):
                    out.append({
                        'name':       str(c.get('name', '')),
                        'ats':        str(c.get('ats', 'other')),
                        'slug':       str(c.get('slug', '')),
                        'career_url': str(c.get('career_url', '')),
                        'industry':   str(c.get('industry', 'other')),
                    })
            return out
    except Exception:
        pass
    return []


# ── Indeed RSS feeds ──────────────────────────────────────────────────────────
# Free, no API key required.
# URL pattern: https://www.indeed.com/rss?q={query}&l={location}&sort=date&limit=50&fromage=30

_INDEED_LOCATIONS = ['Washington DC', 'Miami FL', 'Austin TX', '']


def fetch_indeed(queries: list) -> list:
    """Fetch jobs from Indeed's public RSS feeds for multiple queries and locations."""
    seen: set = set()
    out: list = []

    def _fetch_one(query: str, location: str) -> list:
        results = []
        try:
            from urllib.parse import quote_plus
            url = (
                f'https://www.indeed.com/rss'
                f'?q={quote_plus(query)}'
                f'&l={quote_plus(location)}'
                f'&sort=date&limit=50&fromage=30'
            )
            resp = _get(url, timeout=_TIMEOUT)
            if resp.status_code != 200:
                return []
            root = ET.fromstring(resp.content)
            for item in root.findall('.//item'):
                job_url = (item.findtext('link') or '').strip()
                if not job_url:
                    guid = item.find('guid')
                    job_url = (guid.text or '').strip() if guid is not None else ''
                if not job_url:
                    continue
                title = (item.findtext('title') or '').strip()
                description = (item.findtext('description') or '').strip()
                pub_date = item.findtext('pubDate')
                # Company from <source> tag
                source_el = item.find('source')
                company = (source_el.text or '').strip() if source_el is not None else ''
                # If no source tag, try to extract from description
                if not company and description:
                    import re as _re_inner
                    m = _re_inner.search(r'<b>([^<]+)</b>', description)
                    if m:
                        company = m.group(1).strip()
                is_remote = not location or 'remote' in location.lower()
                results.append(_job(
                    url=job_url,
                    title=title,
                    company=company,
                    description=description,
                    location=location or 'Remote / Nationwide',
                    remote=is_remote,
                    date_posted=pub_date,
                    source='indeed',
                ))
        except Exception:
            pass
        return results

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [
            ex.submit(_fetch_one, q, loc)
            for q in queries
            for loc in _INDEED_LOCATIONS
        ]
        for f in as_completed(futures):
            for job in f.result():
                url = job.get('url', '')
                if url and url not in seen:
                    seen.add(url)
                    out.append(job)
    return out


# ── JSearch API (RapidAPI) ────────────────────────────────────────────────────
# Requires env var: JSEARCH_API_KEY
# Endpoint: https://jsearch.p.rapidapi.com/search

_JSEARCH_LOCATIONS = ['Washington DC', 'Miami FL', 'Austin TX', 'remote']


def fetch_jsearch(queries: list) -> list:
    """Fetch jobs via JSearch API on RapidAPI. Returns [] if no JSEARCH_API_KEY."""
    import os
    api_key = os.environ.get('JSEARCH_API_KEY')
    if not api_key:
        return []

    seen: set = set()
    out: list = []

    def _fetch_one(query: str, location: str) -> list:
        results = []
        try:
            combined = f'{query} in {location}' if location else query
            resp = requests.get(
                'https://jsearch.p.rapidapi.com/search',
                params={
                    'query': combined,
                    'page': '1',
                    'num_pages': '2',
                    'date_posted': 'month',
                    'employment_types': 'FULLTIME',
                },
                headers={
                    **_HEADERS,
                    'X-RapidAPI-Key': api_key,
                    'X-RapidAPI-Host': 'jsearch.p.rapidapi.com',
                },
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            for j in resp.json().get('data', []):
                url = j.get('job_apply_link', '')
                if not url:
                    continue
                city = j.get('job_city', '') or ''
                state = j.get('job_state', '') or ''
                loc_str = ', '.join(filter(None, [city, state]))
                sal = None
                lo = j.get('job_min_salary')
                hi = j.get('job_max_salary')
                if lo and hi:
                    sal = f'${int(lo):,}–${int(hi):,}'
                elif lo:
                    sal = f'${int(lo):,}+'
                results.append(_job(
                    url=url,
                    title=j.get('job_title', ''),
                    company=j.get('employer_name', ''),
                    description=j.get('job_description', ''),
                    location=loc_str,
                    remote=bool(j.get('job_is_remote')),
                    salary=sal,
                    date_posted=j.get('job_posted_at_datetime_utc'),
                    source='jsearch',
                ))
        except Exception:
            pass
        return results

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [
            ex.submit(_fetch_one, q, loc)
            for q in queries
            for loc in _JSEARCH_LOCATIONS
        ]
        for f in as_completed(futures):
            for job in f.result():
                url = job.get('url', '')
                if url and url not in seen:
                    seen.add(url)
                    out.append(job)
    return out




# ── Recruitee ATS ─────────────────────────────────────────────────────────────
# Public API: GET https://careers.recruitee.com/api/c/{slug}/offers/
# No auth required.

RECRUITEE_COMPANIES = [
    # ── Tech / SaaS ───────────────────────────────────────────────────────────
    ('n26',                'N26'),
    ('sumup',              'SumUp'),
    ('klarna',             'Klarna'),
    ('paysafe',            'Paysafe'),
    ('worldline',          'Worldline'),
    ('nexi',               'Nexi'),
    ('nets',               'Nets'),
    ('bambora',            'Bambora'),
    ('zettle',             'Zettle'),
    ('izettle',            'iZettle'),
    ('mollie',             'Mollie'),
    ('buckaroo',           'Buckaroo'),
    ('multisafepay',       'MultiSafePay'),
    ('pay-nl',             'Pay.nl'),
    ('payplug',            'PayPlug'),
    ('lemonway',           'Lemonway'),
    ('lime-technologies',  'Lime Technologies'),
    ('limeade',            'Limeade'),
    ('gympass',            'Gympass'),
    ('wellhub',            'Wellhub'),
    ('forma',              'Forma'),
    ('bswift',             'bswift'),
    ('businessolver',      'Businessolver'),
    ('benefitfocus',       'Benefitfocus'),
    ('benify',             'Benify'),
    ('perkbox',            'Perkbox'),
    ('reward-gateway',     'Reward Gateway'),
    ('achievers',          'Achievers'),
    ('recognition-io',     'Kazoo'),
    ('bonusly',            'Bonusly'),
    ('assembly',           'Assembly'),
    ('nectar',             'Nectar'),
    ('motivosity',         'Motivosity'),
    ('workhuman',          'Workhuman'),
    ('kudos',              'Kudos'),
    ('fond',               'Fond'),
    ('awardco',            'Awardco'),
    # ── Media / Content ───────────────────────────────────────────────────────
    ('papier',             'Papier'),
    ('photobox',           'Photobox'),
    ('snapfish',           'Snapfish'),
    ('shutterfly',         'Shutterfly'),
    ('artifact-uprising',  'Artifact Uprising'),
    ('minted',             'Minted'),
    ('redbubble',          'Redbubble'),
    ('society6',           'Society6'),
    ('threadless',         'Threadless'),
    ('teepublic',          'TeePublic'),
    ('merch-by-amazon',    'Merch by Amazon'),
    ('printful',           'Printful'),
    ('printify',           'Printify'),
    ('gooten',             'Gooten'),
    ('gelato',             'Gelato'),
    ('prodigi',            'Prodigi'),
    ('contrado',           'Contrado'),
    # ── Enterprise / Services ─────────────────────────────────────────────────
    ('visma',              'Visma'),
    ('unit4',              'Unit4'),
    ('infor',              'Infor'),
    ('epicor',             'Epicor'),
    ('ifs',                'IFS'),
    ('aptean',             'Aptean'),
    ('syspro',             'SYSPRO'),
    ('sage',               'Sage'),
    ('pegasystems',        'Pegasystems'),
    ('appian',             'Appian'),
    ('outsystems',         'OutSystems'),
    ('mendix',             'Mendix'),
    ('kofax',              'Kofax'),
    ('opentext',           'OpenText'),
    ('hyland',             'Hyland'),
    ('laserfiche',         'Laserfiche'),
    ('m-files',            'M-Files'),
    ('alfresco',           'Alfresco'),
    ('nuxeo',              'Nuxeo'),
    ('box',                'Box'),
]


def _fetch_recruitee(slug: str, company_name: str) -> list:
    try:
        resp = requests.get(
            f'https://careers.recruitee.com/api/c/{slug}/offers/',
            headers=_HEADERS, timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        out = []
        for j in resp.json().get('offers', []):
            job_url = j.get('careers_url', '') or j.get('url', '')
            if not job_url:
                continue
            loc = j.get('location', '') or j.get('city', '') or 'United States'
            remote = j.get('remote', False) or 'remote' in loc.lower()
            out.append(_job(
                url=job_url,
                title=j.get('title', ''),
                company=company_name,
                description=j.get('description', ''),
                location=loc,
                remote=remote,
                salary=None,
                date_posted=j.get('published_at'),
                source=f'recruitee/{slug}',
            ))
        return out
    except Exception:
        return []


def fetch_recruitee_all() -> list:
    cached = _cache_read('recruitee')
    if cached is not None:
        return cached
    out = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fetch_recruitee, slug, name)
                   for slug, name in RECRUITEE_COMPANIES]
        for f in as_completed(futures):
            out.extend(f.result())
    _cache_write('recruitee', out)
    return out


# ── Bing ATS discovery ────────────────────────────────────────────────────────
# Uses Bing Web Search to discover company ATS boards not already listed.
# Requires env var: BING_SEARCH_KEY
# Discovered slugs are cached 48h in .job_source_cache/discovered_slugs.json

_ATS_DISCOVERY_TTL = 48 * 3600  # 48 hours
_DISCOVERED_SLUGS_CACHE = _os.path.join(_CACHE_DIR, 'discovered_slugs.json')

_ATS_SITE_PATTERNS = [
    ('greenhouse', 'boards.greenhouse.io'),
    ('lever',      'jobs.lever.co'),
    ('ashby',      'jobs.ashbyhq.com'),
]


def _load_discovered_slugs() -> dict:
    """Load cached discovered slugs. Returns dict keyed by ats name."""
    try:
        if _os.path.exists(_DISCOVERED_SLUGS_CACHE):
            if _time.time() - _os.path.getmtime(_DISCOVERED_SLUGS_CACHE) < _ATS_DISCOVERY_TTL:
                with open(_DISCOVERED_SLUGS_CACHE, 'r', encoding='utf-8') as f:
                    return _json.load(f)
    except Exception:
        pass
    return {'greenhouse': [], 'lever': [], 'ashby': []}


def _save_discovered_slugs(data: dict):
    """Persist discovered slugs to cache."""
    _os.makedirs(_CACHE_DIR, exist_ok=True)
    try:
        with open(_DISCOVERED_SLUGS_CACHE, 'w', encoding='utf-8') as f:
            _json.dump(data, f)
    except Exception:
        pass


def discover_ats_via_search(queries: list) -> list:
    """
    Use Bing Web Search to discover company ATS boards (Greenhouse, Lever, Ashby)
    not already in the known company lists. Fetches those boards and returns jobs.
    Returns [] if no BING_SEARCH_KEY.
    """
    import os
    import re as _re_bing
    bing_key = os.environ.get('BING_SEARCH_KEY')
    if not bing_key:
        return []

    # Load previously discovered slugs (cache hit = skip re-discovery)
    cached_slugs = _load_discovered_slugs()
    known = {
        'greenhouse': set(s for s, _ in GREENHOUSE_COMPANIES),
        'lever':      set(s for s, _ in LEVER_COMPANIES),
        'ashby':      set(s for s, _ in ASHBY_COMPANIES),
    }

    # Build set of already-cached discovered slugs so we don't re-add duplicates
    already_discovered = {
        ats: set(cached_slugs.get(ats, []))
        for ats in ('greenhouse', 'lever', 'ashby')
    }
    new_slugs = {ats: set() for ats in ('greenhouse', 'lever', 'ashby')}

    def _bing_search(search_query: str) -> list:
        """Run a single Bing Web Search and return result URLs."""
        try:
            resp = requests.get(
                'https://api.bing.microsoft.com/v7.0/search',
                params={'q': search_query, 'count': 20, 'mkt': 'en-US'},
                headers={**_HEADERS, 'Ocp-Apim-Subscription-Key': bing_key},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            return [v.get('url', '') for v in
                    (resp.json().get('webPages') or {}).get('value', [])]
        except Exception:
            return []

    def _extract_slug(url: str, ats: str) -> str:
        """Extract the company slug from an ATS URL."""
        patterns = {
            'greenhouse': r'boards\.greenhouse\.io/([^/?\s]+)',
            'lever':      r'jobs\.lever\.co/([^/?\s]+)',
            'ashby':      r'jobs\.ashbyhq\.com/([^/?\s]+)',
        }
        m = _re_bing.search(patterns[ats], url)
        return m.group(1).lower() if m else ''

    # Run Bing searches for each ATS site + each query (up to 3 queries)
    search_tasks = [
        (f'site:{domain} "{q}"', ats)
        for q in queries[:3]
        for ats, domain in _ATS_SITE_PATTERNS
    ]

    with ThreadPoolExecutor(max_workers=9) as ex:
        bing_futures = {ex.submit(_bing_search, sq): ats for sq, ats in search_tasks}
        for f in as_completed(bing_futures):
            ats = bing_futures[f]
            for url in f.result():
                slug = _extract_slug(url, ats)
                if (slug
                        and slug not in known[ats]
                        and slug not in already_discovered[ats]):
                    new_slugs[ats].add(slug)

    if not any(new_slugs.values()):
        return []

    # Fetch jobs for newly discovered slugs
    out: list = []
    fetch_map = {
        'greenhouse': _fetch_greenhouse,
        'lever':      _fetch_lever,
        'ashby':      _fetch_ashby,
    }
    fetch_tasks = []
    for ats, slugs in new_slugs.items():
        for slug in slugs:
            # Use slug as a stand-in for the company name (title-cased)
            company_name = slug.replace('-', ' ').title()
            fetch_tasks.append((ats, slug, company_name))

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {
            ex.submit(fetch_map[ats], slug, name): (ats, slug)
            for ats, slug, name in fetch_tasks
        }
        for f in as_completed(futures):
            jobs = f.result()
            out.extend(jobs)

    # Persist newly discovered slugs to cache
    for ats in ('greenhouse', 'lever', 'ashby'):
        all_discovered = list(already_discovered[ats] | new_slugs[ats])
        cached_slugs[ats] = all_discovered
    _save_discovered_slugs(cached_slugs)

    return out


# ── Dice.com ──────────────────────────────────────────────────────────────────
# Free, no API key required.
# POST https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search

def fetch_dice(queries: list) -> list:
    """Fetch jobs from Dice.com's job search API. No API key required."""
    seen: set = set()
    out: list = []

    def _fetch_one(query: str) -> list:
        results = []
        try:
            resp = requests.post(
                'https://job-search-api.svc.dhigroupinc.com/v1/dice/jobs/search',
                json={
                    'q': query,
                    'countryCode2': 'US',
                    'radius': 50,
                    'radiusUnit': 'mi',
                    'pageSize': 50,
                    'currencyCode': 'USD',
                    'fields': 'id,title,companyName,location,remote,postedDate,salaryMin,salaryMax,jobDescription,applyUrl',
                },
                headers={**_HEADERS, 'Content-Type': 'application/json'},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            for j in resp.json().get('data', []):
                url = j.get('applyUrl', '')
                if not url:
                    continue
                sal = None
                lo = j.get('salaryMin')
                hi = j.get('salaryMax')
                if lo and hi:
                    sal = f'${int(lo):,}–${int(hi):,}'
                elif lo:
                    sal = f'${int(lo):,}+'
                results.append(_job(
                    url=url,
                    title=j.get('title', ''),
                    company=j.get('companyName', ''),
                    description=j.get('jobDescription', ''),
                    location=j.get('location', ''),
                    remote=bool(j.get('remote')),
                    salary=sal,
                    date_posted=j.get('postedDate'),
                    source='dice',
                ))
        except Exception:
            pass
        return results

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fetch_one, q) for q in queries[:8]]
        for f in as_completed(futures):
            for job in f.result():
                url = job.get('url', '')
                if url and url not in seen:
                    seen.add(url)
                    out.append(job)
    return out


# ── Jooble ────────────────────────────────────────────────────────────────────
# Requires env var: JOOBLE_API_KEY
# Endpoint: https://jooble.org/api/{JOOBLE_API_KEY}

_JOOBLE_LOCATIONS = ['Washington, DC', 'Miami, FL', 'Austin, TX', '']


def fetch_jooble(queries: list) -> list:
    """Fetch jobs via the Jooble API. Returns [] if no JOOBLE_API_KEY."""
    import os
    api_key = os.environ.get('JOOBLE_API_KEY')
    if not api_key:
        return []

    seen: set = set()
    out: list = []

    def _fetch_one(query: str, location: str) -> list:
        results = []
        try:
            resp = requests.post(
                f'https://jooble.org/api/{api_key}',
                json={'keywords': query, 'location': location, 'page': 1},
                headers={**_HEADERS, 'Content-Type': 'application/json'},
                timeout=_TIMEOUT,
            )
            if resp.status_code != 200:
                return []
            for j in resp.json().get('jobs', []):
                url = j.get('link', '')
                if not url:
                    continue
                sal = j.get('salary') or None
                is_remote = not location or 'remote' in (j.get('location', '') or '').lower()
                results.append(_job(
                    url=url,
                    title=j.get('title', ''),
                    company=j.get('company', ''),
                    description=j.get('snippet', ''),
                    location=j.get('location', location),
                    remote=is_remote,
                    salary=sal,
                    date_posted=j.get('updated'),
                    source='jooble',
                ))
        except Exception:
            pass
        return results

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = [
            ex.submit(_fetch_one, q, loc)
            for q in queries
            for loc in _JOOBLE_LOCATIONS
        ]
        for f in as_completed(futures):
            for job in f.result():
                url = job.get('url', '')
                if url and url not in seen:
                    seen.add(url)
                    out.append(job)
    return out


# ── Big Tech direct career APIs ───────────────────────────────────────────────
# Google, Microsoft, Meta, Netflix, Apple — all have public JSON endpoints.
# Amazon.jobs uses a GraphQL API.

_BIGTECH_SEARCH_TERMS = [
    'analyst', 'engineer', 'data', 'software',
    'product manager', 'program manager', 'analytics',
    'developer', 'architect', 'consultant',
    'machine learning', 'business intelligence',
]


def _fetch_amazon_jobs() -> list:
    """Amazon Jobs — public JSON endpoint (confirmed working)."""
    cached = _cache_read('amazon_careers')
    if cached is not None:
        return cached
    out = []
    try:
        for term in _BIGTECH_SEARCH_TERMS[:8]:
            url = (
                'https://www.amazon.jobs/en/search.json'
                f'?base_query={requests.utils.quote(term)}'
                '&result_limit=50&sort=relevant'
            )
            r = _get(url, timeout=_TIMEOUT,
                     headers={**_HEADERS, 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
            if r.status_code != 200:
                continue
            data = r.json()
            for job in data.get('jobs', []):
                job_path = job.get('job_path', '')
                job_url = f'https://www.amazon.jobs{job_path}' if job_path else ''
                if not job_url:
                    continue
                loc = job.get('location', '') or job.get('city', '') or 'United States'
                out.append(_job(
                    url=job_url,
                    title=job.get('title', ''),
                    company=job.get('company_name', 'Amazon'),
                    description=job.get('description_short', '') or job.get('description', '') or job.get('basic_qualifications', ''),
                    location=loc,
                    remote='remote' in loc.lower(),
                    salary=None,
                    source='amazon_careers',
                ))
    except Exception:
        pass
    out = list({j['url']: j for j in out if j['url']}.values())
    _cache_write('amazon_careers', out)
    return out


def fetch_bigtech_all() -> list:
    """Fetch jobs from Amazon (other big tech APIs are not publicly accessible)."""
    cached = _cache_read('bigtech')
    if cached is not None:
        return cached
    out = _fetch_amazon_jobs()
    _cache_write('bigtech', out)
    return out


# ── Master fetch ──────────────────────────────────────────────────────────────

def fetch_all_jobs(queries: list, watchlist: list = None) -> dict:
    """
    Fetch from all sources concurrently.
    Returns dict of url -> normalised job (deduped by URL).
    """
    all_jobs: dict = {}

    def _merge(jobs):
        for j in jobs:
            url = j.get('url', '')
            if url and url not in all_jobs and not _is_login_walled(url):
                all_jobs[url] = j

    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = []

        # Keyword-search APIs (per query)
        for q in queries[:5]:
            futures.append(ex.submit(fetch_themuse, q))
            futures.append(ex.submit(fetch_jobicy, q))
            # fetch_smartrecruiters disabled — API path returns 404

        # Full-board fetches
        # fetch_arbeitnow disabled — European jobs only
        # fetch_remoteok disabled — requires paid subscription
        futures.append(ex.submit(fetch_weworkremotely))
        futures.append(ex.submit(fetch_himalayas))
        futures.append(ex.submit(fetch_remotive))

        # Big Tech direct career APIs (Google, Microsoft, Meta, Amazon, Apple, Netflix)
        futures.append(ex.submit(fetch_bigtech_all))

        # ATS company boards (each internally parallelised)
        futures.append(ex.submit(fetch_greenhouse_all))
        futures.append(ex.submit(fetch_lever_all))
        futures.append(ex.submit(fetch_ashby_all))
        futures.append(ex.submit(fetch_workday_all))
        # fetch_breezyhr_all disabled — all slugs redirect to homepage (302)

        # fetch_icims_all disabled — RSS endpoint now login-gated (302 to login)

        # fetch_bamboohr_all disabled — API returns 403 for all companies
        # fetch_workable_all disabled — /api/v3/accounts/{slug}/jobs returns 404

        # fetch_taleo_all disabled — domains dead (ECONNREFUSED/404)

        # fetch_recruitee_all disabled — careers.recruitee.com domain retired

        # Direct company career pages (political tech, think tanks, gov-adjacent)
        futures.append(ex.submit(fetch_career_pages_all))

        # User-defined company watchlist
        if watchlist:
            futures.append(ex.submit(fetch_custom_companies, watchlist))

        # fetch_indeed disabled — RSS feeds shut down (404)
        # fetch_dice disabled — 403 bot-blocked

        # New broad sources (require API keys — no-ops if key absent)
        if _os.environ.get('JSEARCH_API_KEY'):
            futures.append(ex.submit(fetch_jsearch, queries))
        if _os.environ.get('JOOBLE_API_KEY'):
            futures.append(ex.submit(fetch_jooble, queries))
        if _os.environ.get('BING_SEARCH_KEY'):
            futures.append(ex.submit(discover_ats_via_search, queries))

        # Location-anchored searches (DC metro, catches smaller companies)
        # These are no-ops if the required env vars are not set.
        futures.append(ex.submit(fetch_adzuna, queries))
        futures.append(ex.submit(fetch_usajobs, queries))

        for f in as_completed(futures):
            try:
                _merge(f.result())
            except Exception:
                pass

    return all_jobs
