import time
import requests
import sqlite3
import json
import hashlib
from datetime import datetime, timedelta
import dateparser
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

DB_PATH = 'jobs.db'


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
    CREATE TABLE IF NOT EXISTS jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source TEXT,
        url TEXT,
        title TEXT,
        company TEXT,
        description TEXT,
        location TEXT,
        remote INTEGER,
        salary TEXT,
        date_posted TEXT,
        discovered_at TEXT,
        raw TEXT,
        dedup_hash TEXT
    )
    ''')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS jobs_url_idx ON jobs(url)')
    c.execute('CREATE UNIQUE INDEX IF NOT EXISTS jobs_hash_idx ON jobs(dedup_hash)')
    conn.commit()
    conn.close()


def normalize_date(text):
    if not text:
        return None
    # Try ISO first
    try:
        return datetime.fromisoformat(text).isoformat()
    except Exception:
        pass
    # Fallback to dateparser to handle "3 days ago", mixed formats, timezones
    dt = dateparser.parse(text)
    if dt:
        return dt.isoformat()
    return None


def insert_job(job: dict):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    try:
        # compute dedup hash (prefer canonical url, fallback to title+company+location)
        # normalize values for stable deduping
        def _norm(s):
            if not s:
                return ''
            import re
            s2 = re.sub(r'\s+', ' ', s).strip().lower()
            s2 = re.sub(r"[^a-z0-9 ]", '', s2)
            return s2

        key = job.get('url') or f"{_norm(job.get('title',''))}|{_norm(job.get('company',''))}|{_norm(job.get('location',''))}"
        h = hashlib.sha256(key.encode('utf-8')).hexdigest()
        c.execute('''INSERT OR IGNORE INTO jobs (source,url,title,company,description,location,remote,salary,date_posted,discovered_at,raw,dedup_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''', (
            job.get('source'),
            job.get('url'),
            job.get('title'),
            job.get('company'),
            job.get('description'),
            job.get('location'),
            1 if job.get('remote') else 0,
            job.get('salary'),
            job.get('date_posted'),
            datetime.utcnow().isoformat(),
            json.dumps(job),
            h
        ))
        conn.commit()
    finally:
        conn.close()


def extract_from_remotive(query=None):
    params = {'search': query} if query else {}
    # Use provider wrapper if SCRAPERAPI_KEY is set, otherwise direct request
    url = 'https://remotive.io/api/remote-jobs'
    resp = fetch_url(url, params=params)
    data = resp.json()
    out = []
    for j in data.get('jobs', []):
        job = {
            'source': 'remotive',
            'url': j.get('url'),
            'title': j.get('title'),
            'company': j.get('company_name'),
            'description': j.get('description'),
            'location': j.get('candidate_required_location'),
            'remote': 'remote' in (j.get('job_type','') or '').lower() or j.get('category') == 'Remote',
            'salary': j.get('salary'),
            'date_posted': normalize_date(j.get('publication_date') or j.get('date'))
        }
        out.append(job)
    return out


def extract_jsonld_from_url(url):
    try:
        resp = fetch_url(url, timeout=20)
    except Exception:
        return []
    if resp.status_code != 200:
        return []
    soup = BeautifulSoup(resp.text, 'html.parser')
    results = []
    for script in soup.find_all('script', type='application/ld+json'):
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        # data can be list or dict
        items = data if isinstance(data, list) else [data]
        for item in items:
            if item.get('@type') == 'JobPosting' or item.get('@type', '').lower() == 'jobposting':
                job = {
                    'source': urlparse(url).netloc,
                    'url': url,
                    'title': item.get('title'),
                    'company': (item.get('hiringOrganization') or {}).get('name') if isinstance(item.get('hiringOrganization'), dict) else item.get('hiringOrganization'),
                    'description': item.get('description'),
                    'location': item.get('jobLocation') and (item.get('jobLocation').get('address') or {}).get('addressLocality') if isinstance(item.get('jobLocation'), dict) else None,
                    'remote': 'remote' in (item.get('employmentType','') or '').lower(),
                    'salary': (item.get('baseSalary') or {}).get('value') if isinstance(item.get('baseSalary'), dict) else item.get('baseSalary'),
                    'date_posted': normalize_date(item.get('datePosted'))
                }
                results.append(job)
    return results


def fetch_url(url, params=None, timeout=20):
    # If SCRAPERAPI_KEY is provided, use ScraperAPI to fetch pages to avoid blocks.
    key = os.environ.get('SCRAPERAPI_KEY')
    if key:
        provider = os.environ.get('SCRAPERAPI_URL', 'http://api.scraperapi.com')
        # provider expected to accept ?api_key=KEY&url=TARGET
        target = url
        provider_url = f"{provider}?api_key={key}&url={target}"
        if params:
            # append additional params to provider URL
            from urllib.parse import urlencode
            provider_url += '&' + urlencode(params)
        return requests.get(provider_url, timeout=timeout)

    # Otherwise rely on requests (respects HTTP_PROXY/HTTPS_PROXY env vars)
    return requests.get(url, params=params, timeout=timeout)


def extract_via_playwright_fallback(url):
    # Use Playwright when static fetch fails to find JSON-LD
    try:
        from playwright_scraper import extract_with_playwright
    except Exception:
        return []
    items = extract_with_playwright(url)
    out = []
    for item in items:
        job = {
            'source': 'playwright',
            'url': url,
            'title': item.get('title'),
            'company': (item.get('hiringOrganization') or {}).get('name') if isinstance(item.get('hiringOrganization'), dict) else item.get('hiringOrganization'),
            'description': item.get('description'),
            'location': item.get('jobLocation') and (item.get('jobLocation').get('address') or {}).get('addressLocality') if isinstance(item.get('jobLocation'), dict) else None,
            'remote': 'remote' in (item.get('employmentType','') or '').lower(),
            'salary': (item.get('baseSalary') or {}).get('value') if isinstance(item.get('baseSalary'), dict) else item.get('baseSalary'),
            'date_posted': normalize_date(item.get('datePosted'))
        }
        out.append(job)
    return out


def crawl_company_list(seed_urls):
    jobs = []
    for url in seed_urls:
        jobs.extend(extract_jsonld_from_url(url))
    return jobs


def run_scraper(duration_seconds=3600, query=None, company_seeds=None):
    init_db()
    end_time = time.time() + duration_seconds
    # simple loop: fetch remotive, then company pages, wait a bit, repeat
    while time.time() < end_time:
        try:
            rem_jobs = extract_from_remotive(query=query)
            for j in rem_jobs:
                insert_job(j)

            if company_seeds:
                comp_jobs = crawl_company_list(company_seeds)
                for j in comp_jobs:
                    insert_job(j)
        except Exception as e:
            print('Error in scraper cycle:', e)

        # Sleep small amount to avoid hammering
        time.sleep(10)

    print('Scrape run complete')


if __name__ == '__main__':
    # Example: run one hour with no seeds
    run_scraper(duration_seconds=3600)
