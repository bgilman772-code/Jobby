import re
import os
import json
from datetime import datetime, timezone, timedelta
from google import genai

# Cities and keywords within ~1 hour drive of Washington DC
DC_METRO = {
    'washington', 'dc', 'd.c.', 'district of columbia',
    'arlington', 'alexandria', 'fairfax', 'reston', 'herndon',
    'mclean', 'vienna', 'falls church', 'tysons', 'chantilly',
    'sterling', 'ashburn', 'manassas', 'woodbridge', 'stafford',
    'northern virginia', 'nova',
    'bethesda', 'rockville', 'silver spring', 'gaithersburg',
    'germantown', 'college park', 'laurel', 'bowie', 'greenbelt',
    'hyattsville', 'waldorf', 'upper marlboro', 'dmv',
}

MIAMI_METRO = {
    'miami', 'miami-dade', 'miami beach', 'coral gables', 'brickell',
    'coconut grove', 'doral', 'hialeah', 'aventura', 'sunny isles',
    'fort lauderdale', 'hollywood', 'pompano beach', 'boca raton',
    'delray beach', 'west palm beach', 'boynton beach',
    'south florida', 'broward', 'palm beach',
}

AUSTIN_METRO = {
    'austin', 'round rock', 'cedar park', 'pflugerville', 'leander',
    'georgetown', 'kyle', 'buda', 'san marcos', 'bastrop',
    'lakeway', 'bee cave', 'dripping springs', 'central texas',
    'travis county',
}


def _client():
    return genai.Client(api_key=os.environ['GEMINI_API_KEY'])


def _strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', ' ', text)


# Words that must appear CLOSE to a dollar amount for it to be treated as salary.
# Require very explicit compensation language — not generic financial words.
_SALARY_CONTEXT = re.compile(
    r'\bsalary\b|\bsalaries\b|base pay|base salary|base compensation'
    r'|total comp|total compensation|\bcompensation\b|\bcomp\b'
    r'|annual pay|annual salary|per year|per annum|/year|/yr'
    r'|\bwage\b|\bstipend\b|ote\b|on-target|target compensation'
    r'|pay range|salary range|compensation range|pay band',
    re.I
)

# Patterns that look like dollar amounts but are NOT salaries — scrub before matching
_NON_SALARY_PATTERNS = re.compile(
    r'401\s*\(?\s*k\s*\)?'                                         # 401(k)
    r'|section\s*\d+'                                              # Section 401
    r'|\d+\s*(?:employee|people|person|candidate|team|member)'     # headcount
    r'|\d+\s*(?:day|week|month|hour|hr\b)'                         # durations/hourly
    r'|\d+\s*(?:sq\.?\s*ft|square)'                                # office size
    r'|(?:budget|grant|raise[sd]?|fund(?:ing|ed)?|invest(?:ment|ed)?'
    r'|revenue|donation|endowment|award|contract|loan|valuat)'     # financial sums
    r'\s+(?:of\s+)?\$?\d'                                          #   followed by number
    r'|\$\d[\d,]*\s*(?:million|billion|M\b|B\b)',                  # large round sums
    re.I
)


def parse_salary(salary_str: str):
    """
    Parse an explicit salary field (e.g. '$120K', '$120,000–$150,000', '120k').
    Returns (min_usd, max_usd) or (None, None).

    Rules:
    - Numbers with K/k suffix are treated as thousands: 120k → 120,000
    - Full numbers ≥ 1,000 are used as-is: 120000 → 120,000
    - Small bare numbers (< 1,000, no K suffix) are ignored — too ambiguous
    - Known non-salary strings (401k, etc.) are stripped before parsing
    """
    if not salary_str:
        return None, None

    # Strip known non-salary patterns first
    cleaned = _NON_SALARY_PATTERNS.sub(' ', salary_str)
    s = cleaned.lower().replace(',', '').replace('$', '').replace('usd', '')

    values = []
    for m in re.finditer(r'(\d+(?:\.\d+)?)\s*(k\b)?', s):
        v = float(m.group(1))
        if m.group(2):          # explicit K suffix → multiply
            v *= 1000
        elif v >= 1_000:        # already a full dollar amount
            pass
        else:
            continue            # bare small number — ignore, too ambiguous
        if 20_000 <= v <= 2_000_000:
            values.append(v)
    if not values:
        return None, None
    return min(values), max(values)


# Regex for description scanning — four patterns, all unambiguous:
#   1. $120K or $120k      — dollar + short number + K suffix
#   2. $120,000            — dollar + comma-formatted full amount
#   3. $120000             — dollar + 5-7 digit number (no comma, unambiguous size)
#   4. 120,000 USD/yr      — comma-formatted + explicit unit
# Deliberately excludes bare "$400" (no K, no comma, <5 digits) — too ambiguous
_SALARY_RE = re.compile(
    r'(?:'
    r'\$\s*(\d{1,3})\s*(?=[kK]\b)'             # group 1: $120K  (multiply by 1000)
    r'|\$\s*(\d{1,3}(?:,\d{3}){1,2})'          # group 2: $120,000
    r'|\$\s*(\d{5,7})'                          # group 3: $120000 (5-7 raw digits)
    r'|(\d{1,3}(?:,\d{3}){1,2})\s*(?:usd|/yr|/year|per\s+year|annually)'  # group 4
    r'|(\d{5,7})\s*(?:usd|/yr|/year|per\s+year|annually)'                  # group 5
    r'|(\d{5,7})'                                                           # group 6: bare large number (context required)
    r')',
    re.I
)


def _extract_salary_from_description(desc: str):
    """
    Scan job description text for salary figures.

    Conservative rules to avoid false positives:
    - Requires the match to look like a full annual amount ($120,000) OR
      an explicit K-suffix amount ($120K) — bare $400 is ignored
    - Requires salary-context words (salary, compensation, base, OTE, etc.)
      within 200 characters of the dollar amount
    - Filters out known non-salary patterns (401k, hourly rates, etc.)
    - Returns (min_usd, max_usd) or (None, None)
    """
    if not desc:
        return None, None
    text = _strip_html(desc)
    # Strip known non-salary patterns so they don't accidentally match
    text_clean = _NON_SALARY_PATTERNS.sub(' ', text)

    found = []
    for m in _SALARY_RE.finditer(text_clean):
        raw = next((g for g in m.groups() if g is not None), '').replace(',', '')
        if not raw:
            continue
        try:
            v = float(raw)
        except ValueError:
            continue

        # Group 1 matched $NNNk — multiply by 1000
        if m.group(1):
            v *= 1000

        # Must be a plausible annual salary
        if not (30_000 <= v <= 2_000_000):
            continue

        # Groups 4/5 (explicit unit suffix) don't need context — the unit is the context.
        # Group 6 (bare large number) always needs context.
        needs_context = m.group(4) is None and m.group(5) is None
        if needs_context:
            # Tight 80-char window: salary context must be right next to the number
            start = max(0, m.start() - 80)
            end   = min(len(text_clean), m.end() + 80)
            window = text_clean[start:end]
            if not _SALARY_CONTEXT.search(window):
                continue

        found.append(v)

    if not found:
        return None, None
    return min(found), max(found)


def _passes_salary(job: dict) -> bool:
    # Try description extraction first — it's more reliable than aggregator salary fields
    desc_min, desc_max = _extract_salary_from_description(
        job.get('description') or job.get('contents') or ''
    )
    if desc_max is not None:
        # Use description salary — overwrite any inaccurate aggregator field
        sal_str = (f'${int(desc_min):,}–${int(desc_max):,}/yr'
                   if desc_min and desc_min != desc_max
                   else f'${int(desc_max):,}/yr')
        job['salary'] = sal_str
        job['salary_max'] = int(desc_max)
        return desc_max >= 100_000

    # Fall back to explicit salary field from the job source
    _, max_s = parse_salary(job.get('salary') or '')
    if max_s is not None:
        job['salary_max'] = int(max_s)
        return max_s >= 100_000

    return False  # No salary found → exclude


# Regions that explicitly exclude US candidates
_NON_US_ONLY = (
    'europe', 'eu only', 'emea', 'uk only', 'united kingdom', 'germany',
    'france', 'spain', 'italy', 'netherlands', 'poland', 'portugal',
    'asia', 'apac', 'australia', 'new zealand', 'canada only', 'latam only',
    'latin america only', 'africa', 'india only',
)

# Countries that indicate a non-NA/non-EU company headquarters
_NON_NA_EU_HQ_COUNTRIES = (
    'india', 'china', 'singapore', 'japan', 'south korea',
    'australia', 'new zealand', 'brazil', 'mexico', 'argentina',
    'indonesia', 'vietnam', 'thailand', 'philippines', 'malaysia',
    'pakistan', 'bangladesh', 'nigeria', 'kenya', 'south africa',
    'egypt', 'uae', 'united arab emirates', 'dubai', 'saudi arabia',
    'taiwan', 'hong kong',
)

# US states / cities used to detect US-located roles
_US_STATES = (
    'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado',
    'connecticut', 'delaware', 'florida', 'georgia', 'hawaii', 'idaho',
    'illinois', 'indiana', 'iowa', 'kansas', 'kentucky', 'louisiana',
    'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota',
    'mississippi', 'missouri', 'montana', 'nebraska', 'nevada',
    'new hampshire', 'new jersey', 'new mexico', 'new york', 'north carolina',
    'north dakota', 'ohio', 'oklahoma', 'oregon', 'pennsylvania',
    'rhode island', 'south carolina', 'south dakota', 'tennessee', 'texas',
    'utah', 'vermont', 'virginia', 'washington', 'west virginia',
    'wisconsin', 'wyoming', ', al', ', ak', ', az', ', ar', ', ca', ', co',
    ', ct', ', de', ', fl', ', ga', ', hi', ', id', ', il', ', in', ', ia',
    ', ks', ', ky', ', la', ', me', ', md', ', ma', ', mi', ', mn', ', ms',
    ', mo', ', mt', ', ne', ', nv', ', nh', ', nj', ', nm', ', ny', ', nc',
    ', nd', ', oh', ', ok', ', or', ', pa', ', ri', ', sc', ', sd', ', tn',
    ', tx', ', ut', ', vt', ', va', ', wa', ', wv', ', wi', ', wy',
    'san francisco', 'los angeles', 'new york city', 'nyc', 'seattle',
    'chicago', 'boston', 'austin', 'denver', 'atlanta', 'miami',
    'portland', 'philadelphia', 'san diego', 'dallas', 'houston',
)


def _job_full_text(job: dict) -> str:
    """Return stripped plaintext of all searchable job fields."""
    desc = job.get('description') or job.get('contents') or ''
    desc = _strip_html(desc)
    return ' '.join([
        job.get('title') or '',
        job.get('company') or job.get('company_name') or '',
        job.get('location') or job.get('candidate_required_location') or '',
        desc,
    ]).lower()


def is_us_eligible(job: dict) -> bool:
    """Return True if the job is open to US-based candidates."""
    loc = (job.get('candidate_required_location') or job.get('location') or '').lower()

    # Explicitly non-US-only in location field → reject unless US also mentioned
    if any(kw in loc for kw in _NON_US_ONLY):
        if not any(kw in loc for kw in ('usa', 'us', 'united states', 'america')):
            return False

    # Location field is clear enough — check it first
    if loc:
        if any(kw in loc for kw in ('worldwide', 'anywhere', 'global', 'remote',
                                     'usa', 'us only', 'united states',
                                     'north america', 'americas', 'flexible')):
            return True
        if any(kw in loc for kw in _US_STATES):
            return True
        if any(kw in loc for kw in DC_METRO):
            return True

    # No useful location field — scan the full description
    full = _job_full_text(job)

    # Reject if description explicitly restricts to non-US region only
    if any(kw in full for kw in _NON_US_ONLY):
        if not any(kw in full for kw in ('united states', 'usa', 'us-based',
                                          'north america', 'americas')):
            return False

    # Accept if description mentions US eligibility or a US location
    if any(kw in full for kw in ('united states', 'usa', 'u.s.', 'us-based',
                                  'north america', 'must be authorized to work',
                                  'authorized to work in the us',
                                  'work authorization')):
        return True
    if any(kw in full for kw in _US_STATES):
        return True
    if any(kw in full for kw in DC_METRO):
        return True

    # No location signal at all — assume worldwide/open
    return True


def is_remote(job: dict) -> bool:
    if job.get('remote'):
        return True
    if 'remote' in (job.get('job_type') or '').lower():
        return True
    loc = (job.get('candidate_required_location') or job.get('location') or '').lower()
    if not loc:
        return True
    return any(kw in loc for kw in ('remote', 'worldwide', 'anywhere', 'global',
                                     'usa', 'us only', 'united states',
                                     'north america', 'americas', 'flexible'))


def is_dc_metro(job: dict) -> bool:
    loc = (job.get('candidate_required_location') or job.get('location') or '').lower()
    return any(kw in loc for kw in DC_METRO)


def is_miami_metro(job: dict) -> bool:
    loc = (job.get('candidate_required_location') or job.get('location') or '').lower()
    return any(kw in loc for kw in MIAMI_METRO)


def is_austin_metro(job: dict) -> bool:
    loc = (job.get('candidate_required_location') or job.get('location') or '').lower()
    return any(kw in loc for kw in AUSTIN_METRO)


def _is_company_na_eu(job: dict) -> bool:
    """Return True if the company appears to be NA/EU-headquartered (or origin is unknown).

    Scans description for explicit 'headquartered in / based in / hq in' statements.
    If no signal is found, allows through (benefit of the doubt).
    """
    full = _job_full_text(job)
    hq_match = re.search(
        r'(?:headquartered|hq|based|incorporated)\s+(?:in|at)\s+([\w][\w\s,]{0,40})',
        full
    )
    if hq_match:
        hq_text = hq_match.group(1).lower()
        if any(country in hq_text for country in _NON_NA_EU_HQ_COUNTRIES):
            return False
    return True


def _get_max_salary(job: dict):
    """Return max salary for the job, checking field then description. May be None."""
    _, max_s = parse_salary(job.get('salary') or '')
    if max_s is None:
        _, max_s = _extract_salary_from_description(
            job.get('description') or job.get('contents') or ''
        )
    return max_s


def _passes_location(job: dict) -> bool:
    """Location rules:
    - All jobs: company must be NA/EU-headquartered (no Asian/Latam/etc. companies).
    - Jobs < $200K: must be remote OR in DC metro, Miami metro, or Austin metro.
    - Jobs >= $200K: any US-eligible in-person location is fine.
    """
    if not _is_company_na_eu(job):
        return False

    max_s = _get_max_salary(job)

    if max_s is not None and max_s < 200_000:
        # Under $200K — must work remotely or from DC metro, Miami, or Austin
        return (is_remote(job) or is_dc_metro(job)
                or is_miami_metro(job) or is_austin_metro(job))

    # $200K+ — in-person anywhere is acceptable; still require US eligibility
    return is_us_eligible(job)


# Title keywords that identify a tech/data role
_TECH_TITLE_INCLUDE = (
    'engineer', 'developer', 'data', 'analyst', 'analytics', 'database',
    'sql', 'tableau', 'databricks', 'bi ', 'business intelligence',
    'product manager', 'product management', 'program manager', 'project manager',
    'architect', 'devops', 'platform', 'infrastructure', 'cloud',
    'machine learning', 'ml ',
    'science', 'scientist', 'technical', 'technology', 'software',
    'systems', 'security', 'backend', 'frontend', 'full stack', 'fullstack',
    'api', 'pipeline', 'etl', 'reporting', 'dashboard', 'insights',
    'quantitative', 'quant', 'research', 'director of', 'head of',
    'vp of', 'chief', 'cto', 'cdo', 'lead',
    # Political / civic tech roles
    'civic tech', 'political data', 'campaign technology', 'election data',
    'voter data', 'policy technology', 'govtech', 'gov tech',
    # Non-software / policy / comms / ops roles
    'policy analyst', 'policy director', 'policy manager',
    'communications director', 'communications manager',
    'operations director', 'operations manager',
    'research analyst', 'research director',
    'public affairs', 'government relations',
    'advocacy director', 'advocacy manager',
    'program director', 'outreach manager', 'outreach director',
    'partnerships manager', 'partnerships director',
    'strategic planning', 'strategy consultant',
    'marketing analyst', 'marketing manager',
    'nonprofit', 'policy',
)

# Title keywords that are clearly not tech roles → hard reject
_NON_TECH_TITLE_EXCLUDE = (
    'driver', 'delivery', 'warehouse', 'cashier', 'nurse', 'nursing',
    'physician', 'doctor', 'medical', 'dental', 'therapist', 'counselor',
    'teacher', 'tutor', 'instructor', 'chef', 'cook', 'barista',
    'electrician', 'plumber', 'hvac', 'janitor', 'custodian', 'cleaner',
    'mover', 'laborer', 'construction', 'welder', 'mechanic',
    'sales representative', 'account executive', 'account manager',
    'insurance agent', 'loan officer', 'mortgage', 'real estate agent',
    'receptionist', 'administrative assistant', 'office assistant',
    'customer service representative', 'call center', 'retail',
    'store manager', 'shift supervisor', 'grocery',
    # AI/ML engineering roles
    'ai engineer', 'machine learning engineer', 'ml engineer',
    'deep learning engineer', 'llm engineer', 'artificial intelligence engineer',
    'generative ai', 'gen ai engineer', 'applied ai', 'ai researcher',
    'research scientist', 'research engineer',
)


def _passes_tech_role(job: dict) -> bool:
    title = (job.get('title') or '').lower()
    # Hard reject non-tech titles regardless of description
    if any(kw in title for kw in _NON_TECH_TITLE_EXCLUDE):
        return False
    # Title clearly matches tech → pass
    if any(kw in title for kw in _TECH_TITLE_INCLUDE):
        return True
    # Ambiguous title — check the full description for tech signals
    full = _job_full_text(job)
    tech_signals = (
        'sql', 'database', 'tableau', 'data warehouse', 'business intelligence',
        'python', 'etl', 'analytics', 'cloud', 'aws', 'azure', 'gcp',
        'software engineer', 'data engineer', 'machine learning', 'api',
        'product manager', 'technical', 'infrastructure', 'devops',
    )
    return any(kw in full for kw in tech_signals)


def estimate_salaries_from_comparables(jobs: list) -> None:
    """
    For jobs that have no salary, estimate from comparable listings in the same batch.

    Strategy (in priority order):
      1. Same company + similar title keyword → use median of their salaries
      2. Same title keyword bucket (e.g. "data engineer") → use median of their salaries

    Mutates job dicts in place, setting job['salary'] to an estimation string.
    Jobs that already have a salary are never overwritten.
    """
    import statistics

    def _explicit_salary(job: dict):
        _, max_s = parse_salary(job.get('salary') or '')
        if max_s is None:
            _, max_s = _extract_salary_from_description(
                job.get('description') or job.get('contents') or ''
            )
        return max_s

    # Title keyword buckets — ordered from most specific to broadest
    _TITLE_BUCKETS = [
        'principal engineer', 'staff engineer', 'senior staff',
        'data engineer', 'analytics engineer', 'data scientist', 'data analyst',
        'machine learning', 'ml engineer', 'ai engineer',
        'software engineer', 'backend engineer', 'frontend engineer',
        'full stack', 'fullstack', 'devops', 'platform engineer', 'sre',
        'product manager', 'program manager', 'project manager',
        'engineering manager', 'director of engineering', 'vp of engineering',
        'business intelligence', 'bi developer', 'bi engineer',
        'security engineer', 'cloud engineer', 'infrastructure engineer',
        'database', 'sql developer', 'data warehouse',
    ]

    def _title_bucket(title: str) -> str | None:
        t = title.lower()
        for bucket in _TITLE_BUCKETS:
            if bucket in t:
                return bucket
        return None

    # Build lookup: company → {bucket → [salaries]}
    company_bucket_salaries: dict = {}
    bucket_salaries: dict = {}

    for job in jobs:
        sal = _explicit_salary(job)
        if sal is None:
            continue
        company = (job.get('company') or job.get('company_name') or '').lower().strip()
        bucket = _title_bucket(job.get('title') or '')
        if company:
            company_bucket_salaries.setdefault(company, {}).setdefault(bucket, []).append(sal)
        if bucket:
            bucket_salaries.setdefault(bucket, []).append(sal)

    # Now fill in missing salaries
    for job in jobs:
        if job.get('salary'):  # already has a salary string — skip
            continue
        if _explicit_salary(job) is not None:  # parseable from existing salary field
            continue

        company = (job.get('company') or job.get('company_name') or '').lower().strip()
        bucket = _title_bucket(job.get('title') or '')

        # Priority 1: same company + same bucket
        if company and bucket:
            comp_sals = company_bucket_salaries.get(company, {}).get(bucket, [])
            if comp_sals:
                med = int(statistics.median(comp_sals))
                job['salary'] = f'~${med:,}/yr (est. from comparable {bucket} roles at {job.get("company") or job.get("company_name", "")})'
                continue

        # Priority 2: same company, any bucket
        if company:
            all_comp_sals = []
            for sals in company_bucket_salaries.get(company, {}).values():
                all_comp_sals.extend(sals)
            if all_comp_sals:
                med = int(statistics.median(all_comp_sals))
                job['salary'] = f'~${med:,}/yr (est. from comparable roles at {job.get("company") or job.get("company_name", "")})'
                continue

        # Priority 3: same title bucket, any company in batch
        if bucket:
            bkt_sals = bucket_salaries.get(bucket, [])
            if bkt_sals:
                med = int(statistics.median(bkt_sals))
                job['salary'] = f'~${med:,}/yr (est. from comparable {bucket} roles)'
                continue

        # Priority 4: market-rate median by title bucket (2024 US remote tech benchmarks)
        _MARKET_MEDIANS = {
            'principal engineer': 220_000, 'staff engineer': 210_000,
            'senior staff': 215_000,
            'engineering manager': 200_000, 'director of engineering': 230_000,
            'vp of engineering': 270_000,
            'machine learning': 180_000, 'ml engineer': 175_000, 'ai engineer': 175_000,
            'data scientist': 155_000, 'data engineer': 155_000,
            'analytics engineer': 145_000, 'data analyst': 125_000,
            'software engineer': 160_000, 'backend engineer': 160_000,
            'frontend engineer': 145_000, 'full stack': 155_000, 'fullstack': 155_000,
            'devops': 160_000, 'sre': 165_000, 'platform engineer': 165_000,
            'cloud engineer': 160_000, 'infrastructure engineer': 155_000,
            'security engineer': 170_000,
            'product manager': 170_000, 'program manager': 150_000,
            'project manager': 130_000,
            'business intelligence': 130_000, 'bi developer': 130_000,
            'bi engineer': 135_000,
            'database': 130_000, 'sql developer': 125_000, 'data warehouse': 140_000,
        }
        if bucket and bucket in _MARKET_MEDIANS:
            med = _MARKET_MEDIANS[bucket]
            job['salary'] = f'~${med:,}/yr (est. market rate for {bucket} roles)'
            continue


def _parse_date(date_str: str):
    """Parse a date string into a timezone-aware datetime, or None on failure."""
    if not date_str:
        return None
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%d', '%m/%d/%Y', '%d %b %Y', '%B %d, %Y'):
        try:
            dt = datetime.strptime(date_str[:26].strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def apply_filters(jobs: list, min_salary: int = 100_000, max_age_days: int = 30) -> list:
    """Filter to tech roles only, with salary >= min_salary and US location.
    Jobs posted more than max_age_days ago are excluded; jobs with no date pass through.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days) if max_age_days > 0 else None

    def _salary_ok(job):
        if min_salary <= 0:
            return True
        _, max_s = parse_salary(job.get('salary') or '')
        if max_s is None:
            _, max_s = _extract_salary_from_description(
                job.get('description') or job.get('contents') or ''
            )
            if max_s is not None:
                job['salary'] = f'~${int(max_s):,}/yr (est. from description)'
        if max_s is None:
            return True  # No salary listed — pass through, don't penalise
        return max_s >= min_salary

    def _date_ok(job):
        if cutoff is None:
            return True
        dt = _parse_date(job.get('date_posted') or '')
        if dt is None:
            return True   # No date — always pass through (not a dealbreaker)
        return dt >= cutoff

    return [j for j in jobs
            if _passes_tech_role(j) and _salary_ok(j) and _passes_location(j) and _date_ok(j)]


# Job categories used throughout the app
CATEGORIES = [
    'Backend Engineering', 'Frontend Engineering', 'Full-Stack Engineering',
    'DevOps/Infrastructure', 'Data Engineering', 'Data Science/ML',
    'Security Engineering', 'Mobile Engineering', 'Product Management',
    'Engineering Management', 'Political/Gov Tech', 'Design', 'Other',
]

# Signals used to detect political/civic tech focus in a job
_POLITICAL_TECH_SIGNALS = (
    'political', 'campaign', 'election', 'electoral', 'voter data',
    'voting', 'democratic party', 'republican party', 'partisan',
    'advocacy', 'lobbying', 'legislative', 'congressional', 'senate',
    'house of representatives', 'civic tech', 'govtech', 'gov tech',
    'policy analytics', 'policy data', 'political data',
    'ngp van', 'catalist', 'civis analytics', 'bluelabs', 'clarity campaign',
    'pew research', 'brookings', 'think tank', 'dnc', 'rnc',
    'public affairs', 'government affairs', 'political consulting',
    'opposition research', 'poll', 'polling', 'ballot',
)


_SCORE_BATCH_SIZE = 50   # jobs per Gemini call
_MAX_JOBS_TO_SCORE = 150  # total jobs returned to UI


def _score_batch(client, batch: list, batch_offset: int,
                 profile_summary: str, cats: str):
    """Score one batch of jobs via a single Gemini call."""
    jobs_text = ''
    for i, job in enumerate(batch):
        title = job.get('title') or ''
        company = job.get('company_name') or job.get('company') or ''
        salary = job.get('salary') or 'Not listed'
        raw_desc = job.get('description') or job.get('contents') or ''
        desc = _strip_html(raw_desc)[:800]
        jobs_text += f"\n[{i}] {title} @ {company} | {salary}\n{desc}\n"

    prompt = (
        f"You are matching a DATA & TECHNOLOGY professional to job listings.\n\n"
        f"Candidate profile:\n{profile_summary}\n\n"
        f"The candidate's core strengths are: SQL, relational databases, data analytics, "
        f"Tableau/BI dashboards, Databricks, and technology product work. They are seeking "
        f"roles at technology companies, or technology/data roles inside any company.\n\n"
        f"Scoring rules:\n"
        f"- Score 9-10: STRONG PREFERENCE — role involves political data analytics, "
        f"electoral/campaign technology, civic tech, government data, policy analytics, "
        f"or legislative technology (e.g. voter data, campaign software, think tank analytics, "
        f"government BI). These are high-priority even if the skill overlap is partial.\n"
        f"- Score 8-10: role directly uses SQL, databases, data analytics, Tableau, "
        f"Databricks, BI, or data engineering — OR is a tech product/program/project manager "
        f"role at a tech company or in a tech function\n"
        f"- Score 5-7: adjacent tech role where the candidate's data skills would transfer\n"
        f"- Score 1-4: tech role but weak skill overlap (e.g. pure frontend, mobile dev, "
        f"deep ML research)\n"
        f"- Score 0: not a technology role (delivery, trades, healthcare, retail, etc.) — "
        f"these should have been filtered already but reject them hard if seen\n\n"
        f"Also set political_tech=true if the role or company involves political campaigns, "
        f"elections, voter data, civic/government technology, policy analytics, think tanks, "
        f"political consulting, advocacy, or legislative technology.\n\n"
        f"Assign one category from: {cats}\n"
        f"Use 'Political/Gov Tech' for any political, electoral, civic, or government-focused role.\n\n"
        f"Jobs:\n{jobs_text}\n\n"
        f"Return JSON array, one object per job (index 0–{len(batch)-1}):\n"
        f'[{{"index": 0, "score": 7, "reason": "one sentence", "category": "Data Engineering", "political_tech": false}}, ...]\n\n'
        f"Return only valid JSON with no markdown."
    )
    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        text = response.text.strip()
        if text.startswith('```'):
            text = '\n'.join(text.split('\n')[1:-1])
        results = json.loads(text)
        result_map = {r['index']: r for r in results}
        for i, job in enumerate(batch):
            r = result_map.get(i, {})
            job['match_score'] = int(r.get('score', 5))
            job['match_reason'] = r.get('reason', '')
            job['category'] = r.get('category', 'Other')
            job['political_tech'] = bool(r.get('political_tech', False))
    except Exception as _e:
        _gemini_error = str(_e)
        for job in batch:
            job.setdefault('match_score', 5)
            job.setdefault('match_reason', '')
            job.setdefault('category', 'Other')
            job.setdefault('political_tech', False)
            job['_gemini_error'] = _gemini_error


_MAX_JOBS_PER_COMPANY = 8   # cap per company to prevent one source dominating


def _diversify(jobs: list, max_per_company: int, total: int) -> list:
    """Return up to `total` jobs with at most `max_per_company` per company.

    Uses round-robin across companies so no single source dominates.
    """
    from collections import defaultdict
    buckets: dict = defaultdict(list)
    for j in jobs:
        company = (j.get('company_name') or j.get('company') or '').strip().lower()
        if len(buckets[company]) < max_per_company:
            buckets[company].append(j)

    out = []
    # Round-robin: one job per company each pass until total reached
    companies = list(buckets.keys())
    indices = {c: 0 for c in companies}
    while len(out) < total:
        added_this_pass = False
        for company in companies:
            if len(out) >= total:
                break
            idx = indices[company]
            bucket = buckets[company]
            if idx < len(bucket):
                out.append(bucket[idx])
                indices[company] += 1
                added_this_pass = True
        if not added_this_pass:
            break
    return out


def score_and_categorize_jobs(jobs: list, resume_profile: dict) -> list:
    """Score and categorize jobs in batches of 50, returning up to 150 total."""
    if not jobs:
        return []

    client = _client()

    profile_summary = (
        f"Skills: {', '.join(resume_profile.get('skills', [])[:15])}\n"
        f"Seniority: {resume_profile.get('seniority', 'unknown')}\n"
        f"Past titles: {', '.join(resume_profile.get('job_titles', [])[:5])}\n"
        f"Experience: ~{resume_profile.get('experience_years', '?')} years\n"
        f"Target roles: {', '.join(resume_profile.get('target_roles', []))}"
    )
    cats = ', '.join(CATEGORIES)

    jobs_to_score = _diversify(jobs, _MAX_JOBS_PER_COMPANY, _MAX_JOBS_TO_SCORE)

    # Score in batches to stay within Gemini token limits
    for offset in range(0, len(jobs_to_score), _SCORE_BATCH_SIZE):
        batch = jobs_to_score[offset: offset + _SCORE_BATCH_SIZE]
        _score_batch(client, batch, offset, profile_summary, cats)

    # Enrich with salary / location / political_tech keyword fallback
    for job in jobs_to_score:
        _, max_s = parse_salary(job.get('salary') or '')
        job['salary_unknown'] = max_s is None
        job['salary_max'] = int(max_s) if max_s else 0
        job['is_remote'] = is_remote(job)
        job['is_dc_metro'] = is_dc_metro(job)
        if not job.get('political_tech'):
            full = _job_full_text(job)
            job['political_tech'] = any(kw in full for kw in _POLITICAL_TECH_SIGNALS)

    return jobs_to_score
