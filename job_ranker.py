"""
Multi-factor job ranking.

Score breakdown (0-105 total):
  Skills        0-70 pts  — Gemini match_score (0-10) * 7
  Pay           0-30 pts  — higher salary scores higher; unknown = neutral 11
  Political/Gov    +5 pts — flat bonus for political/civic/gov tech roles
"""
from job_matcher import parse_salary


def rank_job(job: dict) -> dict:
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

    # ── Political/Gov Tech bonus (+5) ─────────────────────────────────────────
    political_bonus = 5 if job.get('political_tech') else 0

    total = pay_score + skills_score + political_bonus

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
    job['political_bonus'] = political_bonus
    job['qualification_tier'] = tier

    return job


def rank_jobs(jobs: list) -> list:
    """Attach ranking scores to all jobs and sort descending."""
    return sorted([rank_job(j) for j in jobs], key=lambda j: j['ranking_score'], reverse=True)
