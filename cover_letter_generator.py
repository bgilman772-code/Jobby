import os
import re
import json
from google import genai


def _clean_letter(text: str) -> str:
    """Strip markdown formatting so the cover letter reads as clean plain text."""
    # Remove code fences
    text = re.sub(r'```[^\n]*\n?', '', text)
    # Bold/italic: **text** or __text__ or *text* or _text_
    text = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', text)
    text = re.sub(r'_{1,2}([^_]+)_{1,2}', r'\1', text)
    # Headings: ## Heading
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Bullet points: keep the text, remove the marker
    text = re.sub(r'^\s*[-*•]\s+', '', text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
    # Collapse more than 2 consecutive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _client():
    return genai.Client(api_key=os.environ['GEMINI_API_KEY'])


def tailor_cover_letter(job: dict, resume_text: str, base_letter: str, profile: dict) -> str:
    """
    Use Gemini to rewrite the cover letter specifically for this job.
    Highlights skills and experience that match the job description.
    """
    client = _client()

    company = job.get('company_name') or job.get('company') or 'the company'
    title = job.get('title') or 'the role'
    description = (job.get('description') or '')[:2000]
    candidate_name = profile.get('name') or 'the candidate'

    prompt = (
        f"You are writing a tailored job application cover letter.\n\n"
        f"CANDIDATE NAME: {candidate_name}\n\n"
        f"CANDIDATE RESUME (excerpt):\n{resume_text[:2000]}\n\n"
        f"TARGET JOB:\n"
        f"Company: {company}\n"
        f"Title: {title}\n"
        f"Description:\n{description}\n\n"
        f"BASE COVER LETTER (use as style/voice reference):\n{base_letter}\n\n"
        f"Instructions:\n"
        f"- Rewrite the cover letter to be highly specific to this job and company\n"
        f"- Mention {company} and the {title} role by name\n"
        f"- Highlight 2-3 specific skills from the resume that directly match the job requirements\n"
        f"- Reference specific technologies or requirements from the job description\n"
        f"- Keep the same professional tone and length as the base letter (3-4 paragraphs)\n"
        f"- Do NOT add placeholders like [Your Name] — use the actual candidate name\n"
        f"- Return ONLY the cover letter text, no commentary or labels\n"
        f"- Do NOT use markdown formatting — no asterisks, no hashtags, no bullet points, plain text only"
    )

    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    return _clean_letter(response.text)


def generate_resume_feedback(job: dict, resume_text: str) -> dict:
    """
    Use Gemini to analyze how well the candidate's resume matches the job,
    returning actionable feedback for resume and cover letter improvements.

    Returns a dict with keys:
      strengths      - list of matching strengths
      gaps           - list of missing qualifications
      resume_tips    - list of specific resume edits to make
      cover_angle    - string: the best cover letter angle for this job
      keywords       - list of keywords to add to resume/cover letter
      tier_reasoning - string: brief explanation of match quality
    """
    client = _client()

    company = job.get('company_name') or job.get('company') or 'the company'
    title = job.get('title') or 'the role'
    description = (job.get('description') or '')[:3000]

    prompt = (
        f"You are a professional career coach reviewing a candidate's resume against a job posting.\n\n"
        f"TARGET JOB:\n"
        f"Company: {company}\n"
        f"Title: {title}\n"
        f"Description:\n{description}\n\n"
        f"CANDIDATE RESUME:\n{resume_text[:3000]}\n\n"
        f"Provide a JSON response with exactly these keys:\n"
        f"  strengths: array of 2-4 strings — specific resume strengths that match this job\n"
        f"  gaps: array of 2-4 strings — specific qualifications the job wants that are missing or weak in the resume\n"
        f"  resume_tips: array of 2-4 strings — concrete edits to make to the resume to better match this job\n"
        f"  cover_angle: string — the single best angle/narrative for the cover letter (1-2 sentences)\n"
        f"  keywords: array of 5-8 strings — important keywords/phrases from the job to incorporate\n"
        f"  tier_reasoning: string — brief (1 sentence) explanation of the overall match quality\n\n"
        f"Return ONLY valid JSON, no markdown fences, no commentary."
    )

    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    raw = response.text.strip()
    # Strip markdown code fences if present
    if raw.startswith('```'):
        raw = raw.split('```', 2)[1]
        if raw.startswith('json'):
            raw = raw[4:]
        raw = raw.rsplit('```', 1)[0].strip()
    try:
        return json.loads(raw)
    except Exception:
        # Fallback: return raw text in a structured way
        return {
            'strengths': [],
            'gaps': [],
            'resume_tips': [raw],
            'cover_angle': '',
            'keywords': [],
            'tier_reasoning': '',
        }


def answer_application_question(question: str, resume_text: str,
                                job_title: str, company: str,
                                job_description: str, profile: dict) -> str:
    """
    Use Gemini to write a concise, resume-grounded answer to a custom application question.
    Returns a plain-text answer (1-3 short paragraphs, no fluff).
    """
    if not question.strip():
        return ''
    client = _client()
    candidate_name = profile.get('name') or 'the candidate'

    # Build structured candidate context from profile fields + resume text
    candidate_sections = []
    if resume_text and resume_text.strip():
        candidate_sections.append(f"RESUME:\n{resume_text[:2500]}")

    # Structured profile fields supplement or replace thin resume text
    profile_lines = []
    if profile.get('city'):
        profile_lines.append(f"Location: {profile['city']}")
    if profile.get('linkedin'):
        profile_lines.append(f"LinkedIn: {profile['linkedin']}")
    if profile.get('github'):
        profile_lines.append(f"GitHub: {profile['github']}")
    if profile.get('skills'):
        skills = profile['skills'] if isinstance(profile['skills'], list) else [profile['skills']]
        profile_lines.append(f"Skills: {', '.join(str(s) for s in skills[:25])}")
    if profile.get('target_roles'):
        roles = profile['target_roles'] if isinstance(profile['target_roles'], list) else [profile['target_roles']]
        profile_lines.append(f"Target roles: {', '.join(str(r) for r in roles[:5])}")
    if profile.get('bio') or profile.get('summary'):
        profile_lines.append(f"Summary: {profile.get('bio') or profile.get('summary', '')}")
    if profile.get('cover_letter'):
        profile_lines.append(f"Cover letter excerpt:\n{profile['cover_letter'][:600]}")
    if profile_lines:
        candidate_sections.append("CANDIDATE PROFILE:\n" + '\n'.join(profile_lines))

    candidate_context = '\n\n'.join(candidate_sections) or f"Candidate name: {candidate_name}"

    prompt = (
        f"You are filling out a job application on behalf of {candidate_name}.\n"
        f"Write the answer AS {candidate_name}, in first person, as if they typed it themselves.\n\n"
        f"TARGET JOB:\n"
        f"Company: {company or 'the company'}\n"
        f"Title: {job_title or 'the role'}\n"
        f"Job description excerpt:\n{(job_description or '')[:1500]}\n\n"
        f"CANDIDATE BACKGROUND:\n{candidate_context}\n\n"
        f"APPLICATION QUESTION:\n{question}\n\n"
        f"Instructions:\n"
        f"- Write a focused, specific answer using real details from the candidate's background\n"
        f"- Keep it concise: 2-4 sentences for simple questions, up to 2 short paragraphs for complex ones\n"
        f"- Write in first person (\"I\", \"my\", \"I have\") as {candidate_name}\n"
        f"- Reference specific skills, tools, or experiences mentioned in their background\n"
        f"- Do NOT invent qualifications not present in the candidate's background\n"
        f"- Do NOT use buzzword-heavy filler — be direct and specific\n"
        f"- Return ONLY the answer text, no labels, no markdown formatting"
    )

    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        return _clean_letter(response.text)
    except Exception:
        return ''


def generate_search_queries(ideal_position: str, profile: dict) -> list:
    """
    Generate targeted job search queries from user's ideal position description + extracted profile.
    Returns list of 6-10 query strings to augment existing search queries.
    """
    if not ideal_position or not ideal_position.strip():
        return []

    client = _client()
    skills = profile.get('skills', [])
    target_roles = profile.get('target_roles', [])
    existing = profile.get('search_queries', [])

    prompt = (
        f"Generate 8 specific, distinct job search queries for someone with this ideal position:\n"
        f"\"{ideal_position.strip()}\"\n\n"
        f"Their skills: {', '.join(skills[:15]) or 'not specified'}\n"
        f"Their target roles: {', '.join(target_roles[:5]) or 'not specified'}\n\n"
        f"Rules:\n"
        f"- Each query is 2-5 words\n"
        f"- Use different angles: title-only, title+tool, title+domain, seniority+title\n"
        f"- Don't duplicate: {', '.join(existing[:8])}\n"
        f"- Reflect the specific focus in the ideal position description\n\n"
        f"Return ONLY a JSON array of strings like: [\"senior data analyst python\", \"analytics engineer dbt\"]\n"
        f"No markdown, no commentary."
    )

    try:
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        text = response.text.strip()
        if text.startswith('```'):
            text = text.split('```', 2)[1]
            if text.startswith('json'):
                text = text[4:]
            text = text.rsplit('```', 1)[0].strip()
        result = json.loads(text)
        return [q.strip() for q in result if isinstance(q, str) and q.strip()][:10]
    except Exception:
        return []
