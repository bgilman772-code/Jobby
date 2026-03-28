# Resume Sender (Minimal MVP)

This is a small local Flask app to help search remote jobs (using Remotive API), review listings, auto-fill a cover letter template with your profile, and package your resume + cover letter for submission.

Quick start (Windows PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:FLASK_APP = 'app.py'
flask run
```

Open http://127.0.0.1:5000 in your browser (or VS Code's integrated browser) and:
- Fill your profile and upload your resume (.pdf/.docx) and optionally a cover template (.txt)
- Search jobs, click "Review & Customize" on a job, edit the generated cover letter, and click "Package" to download a ZIP containing your resume and cover letter ready for submission.

Importing files from the local filesystem (optional)

If you already have your resume and cover letter on disk and prefer to copy them into the app without using the web UI, use the included script:

```powershell
python import_files.py --resume C:\path\to\YourResume.pdf --cover C:\path\to\CoverTemplate.txt
```

Or (POSIX):

```bash
python import_files.py --resume /home/me/Resume.pdf --cover /home/me/cover.txt
```

Notes:
- This is an MVP. For production you may want to add authentication, persistent DB storage, better error handling, and integration with job provider APIs or email submission workflows.

Scraping and discovered jobs

- A small scraper module (`scraper.py`) is included. It currently pulls jobs from Remotive and can extract `JobPosting` JSON-LD from company career pages.
- The scraper stores results in `jobs.db` (SQLite). Run the scraper for one hour with:

```powershell
python scraper.py
```

Or run programmatically from Python with custom seeds:

```python
from scraper import run_scraper
run_scraper(duration_seconds=3600, query='python', company_seeds=['https://example.com/careers'])
```

- After a run, open `http://127.0.0.1:5000/jobs_db` to view and filter discovered jobs (company, title, location, remote, salary, and since-days filter).

One-click run (scrape then launch)

Place any company career page URLs (one per line) in `seeds.txt` (optional). Then run the one-click script which will:
 - Run the scraper for 1 hour (default), saving results to `jobs.db`.
 - Launch the Flask app and open the discovered jobs view.

Windows PowerShell:
```powershell
.\run_all.ps1
```

Or run directly with Python (cross-platform):
```powershell
python run_all.py
```

To change duration or pass a search query, set env vars before running:
```powershell
$env:SCRAPE_SECONDS = 1800
$env:SCRAPE_QUERY = 'backend python'
python run_all.py
```

