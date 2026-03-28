# One-click helper for Windows PowerShell
param(
    [int]$seconds = 3600
)

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:SCRAPE_SECONDS = $seconds
python run_all.py
