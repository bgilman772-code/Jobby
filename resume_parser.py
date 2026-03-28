import os
import json
from google import genai


def _client():
    return genai.Client(api_key=os.environ['GEMINI_API_KEY'])


def extract_text(filepath: str) -> str:
    """Extract plain text from PDF, DOCX, or TXT file."""
    ext = os.path.splitext(filepath)[1].lower()
    if ext == '.pdf':
        import pypdf
        reader = pypdf.PdfReader(filepath)
        return '\n'.join(page.extract_text() or '' for page in reader.pages)
    elif ext == '.docx':
        import docx
        doc = docx.Document(filepath)
        return '\n'.join(p.text for p in doc.paragraphs)
    else:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()


def analyze_resume(resume_text: str, cover_text: str = '') -> dict:
    """
    Use Gemini to extract a full profile from the resume.

    Returns two categories of data:
      Contact / identity (pre-fill the profile form):
        name, email, phone, city, linkedin, website, intro, skills_summary
      Job-search metadata (used for matching):
        skills, job_titles, target_roles, seniority, experience_years, search_queries
    """
    client = _client()

    combined = f"RESUME:\n{resume_text}"
    if cover_text:
        combined += f"\n\nCOVER LETTER:\n{cover_text}"

    prompt = (
        f"{combined}\n\n"
        "Extract a structured profile from the above resume. "
        "Return a single JSON object with ALL of these keys:\n\n"
        "Contact / identity (extract directly from the resume header):\n"
        "- name: full name (string, empty string if not found)\n"
        "- email: email address (string, empty string if not found)\n"
        "- phone: phone number as written (string, empty string if not found)\n"
        "- city: city and state or 'Remote' (string, empty string if not found)\n"
        "- linkedin: LinkedIn URL if present (string, empty string if not found)\n"
        "- website: personal website or portfolio URL if present (string, empty string if not found)\n"
        "- intro: a one-sentence professional summary written in first person "
        "(e.g. 'Experienced data engineer with 6 years building pipelines at scale.')\n"
        "- skills_summary: a brief comma-separated list of 6-10 top skills for display "
        "(e.g. 'Python, SQL, dbt, Tableau, AWS, Spark')\n\n"
        "Job-search metadata:\n"
        "- skills: list of 10-20 key technical and domain skills\n"
        "- job_titles: list of past job titles held\n"
        "- target_roles: list of 3-5 specific job titles this person should target\n"
        "- seniority: one of junior/mid/senior/lead/principal/director/executive\n"
        "- experience_years: integer estimate of total years of professional experience\n"
        "- search_queries: list of 6-10 short job search queries tailored to this person's "
        "background (e.g. 'senior data engineer', 'analytics engineer dbt', "
        "'political data analyst', 'business intelligence tableau')\n"
        "- skill_categories: object with 4 keys: languages (list of 3-5 programming languages like Python/SQL/R), tools (list of 4-8 data tools like dbt/Tableau/Airflow/Pandas), platforms (list of 2-5 cloud/data platforms like AWS/Snowflake/Databricks), domains (list of 2-5 subject matter domains like 'political data'/'electoral analytics'/'public policy')\n"
        "- ideal_position_summary: a 1-2 sentence description of the ideal next role based on the candidate's trajectory and skills (e.g. 'Senior data engineer role at a mission-driven organization building data infrastructure for political or civic tech applications.')\n\n"
        "Return ONLY valid JSON with no markdown fences, no commentary."
    )

    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
    )
    text = response.text.strip()
    if text.startswith('```'):
        text = text.split('```', 2)[1]
        if text.startswith('json'):
            text = text[4:]
        text = text.rsplit('```', 1)[0].strip()
    try:
        return json.loads(text)
    except Exception:
        return {}
