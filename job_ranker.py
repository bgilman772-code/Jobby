"""
Multi-factor job ranking.

Score breakdown (0-120 total):
  Skills        0-70 pts  — Gemini match_score (0-10) * 7
  Pay           0-30 pts  — higher salary scores higher; unknown = neutral 11
  Political/Gov    +5 pts — flat bonus for political/civic/gov tech roles
  Priority co.    +15 pts — job is from a company the user explicitly flagged
"""
import re as _re
from job_matcher import parse_salary

# Bonus awarded to jobs from user-specified priority companies
_COMPANY_PRIORITY_BONUS = 15


def _normalise(name: str) -> str:
    """Lowercase, strip punctuation/suffixes for fuzzy company matching."""
    n = name.lower()
    for suffix in (' inc', ' llc', ' ltd', ' corp', ' corporation', ' group',
                   ' technologies', ' solutions', ' consulting', ', inc', '.com'):
        n = n.replace(suffix, '')
    return _re.sub(r'[^a-z0-9]', '', n)


def _company_is_priority(job: dict, priority_names: set) -> bool:
    """Return True if this job's company matches any priority company name."""
    if not priority_names:
        return False
    company = job.get('company_name') or job.get('company') or ''
    norm = _normalise(company)
    return norm in priority_names


def rank_job(job: dict, priority_names: set = None) -> dict:
    """Compute composite ranking score and attach breakdown to job dict."""
    # ── Pay score (0-30) ──────────────────────────────────────────────────────
    _, max_s = parse_salary(job.get('salary') or '')
    if max_s is None:
        pay_score = 11
    elif max_s >= 250_000:
        pay_score = 30
    elif max_s >= 200_000:
        pay_score = 26
    elif max_s >= 175_000:
        pay_score = 22
    elif max_s >= 150_000:
        pay_score = 19
    elif max_s >= 130_000:
        pay_score = 16
    elif max_s >= 100_000:
        pay_score = 13
    else:
        pay_score = 0

    # ── Skills match score (0-70) ─────────────────────────────────────────────
    skills_score = int(job.get('match_score', 5)) * 7

    # ── Priority company bonus (+15) ─────────────────────────────────────────
    company_bonus = _COMPANY_PRIORITY_BONUS if _company_is_priority(job, priority_names or set()) else 0

    total = pay_score + skills_score + company_bonus

    # ── Qualification tier ────────────────────────────────────────────────────
    match_score = int(job.get('match_score', 5))
    if match_score >= 8:
        tier = 'Strongly Qualified'
    elif match_score >= 5:
        tier = 'Qualified'
    else:
        tier = 'Reach'

    job['ranking_score'] = total
    job['pay_score'] = pay_score
    job['skills_score'] = skills_score
    job['company_bonus'] = company_bonus
    job['qualification_tier'] = tier

    return job


def rank_jobs(jobs: list, priority_companies: list = None) -> list:
    """Attach ranking scores to all jobs and sort descending.

    priority_companies — list of {name, url} dicts from the user's watchlist.
    Jobs from these companies receive a +15 bonus on top of their normal score.
    """
    priority_names = set()
    for entry in (priority_companies or []):
        name = entry.get('name', '')
        if name:
            priority_names.add(_normalise(name))

    return sorted(
        [rank_job(j, priority_names) for j in jobs],
        key=lambda j: j['ranking_score'],
        reverse=True,
    )
