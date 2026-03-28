import os
import webbrowser
from scraper import run_scraper

# One-click runner: runs scraper for one hour, then starts the Flask app
DEFAULT_DURATION = 3600

def load_seeds(path='seeds.txt'):
    if not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]


def main():
    duration = int(os.environ.get('SCRAPE_SECONDS', DEFAULT_DURATION))
    query = os.environ.get('SCRAPE_QUERY')
    seeds = load_seeds()

    print(f'Running scraper for {duration} seconds...')
    run_scraper(duration_seconds=duration, query=query, company_seeds=seeds)
    print('Scraper finished. Starting Flask app...')

    # Start the Flask app after scraping
    # Import here to avoid circular imports during scraping
    from app import app

    url = 'http://127.0.0.1:5000/jobs_db'
    print(f'Opening browser to {url}')
    webbrowser.open(url)
    app.run(host='127.0.0.1', port=5000)


if __name__ == '__main__':
    main()
