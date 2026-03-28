from apscheduler.schedulers.background import BackgroundScheduler
from scraper import run_scraper
import os


def start_scheduler():
    # Run scraper once at startup and then daily
    scheduler = BackgroundScheduler()
    duration = int(os.environ.get('SCRAPE_SECONDS', '3600'))
    query = os.environ.get('SCRAPE_QUERY')
    seeds = []
    if os.path.exists('seeds.txt'):
        with open('seeds.txt', 'r', encoding='utf-8') as f:
            seeds = [l.strip() for l in f if l.strip() and not l.strip().startswith('#')]

    def job():
        run_scraper(duration_seconds=duration, query=query, company_seeds=seeds)

    # Run once now
    scheduler.add_job(job, 'date', id='initial_run')
    # Then schedule daily runs at 2:00 AM
    scheduler.add_job(job, 'cron', hour=2, minute=0, id='daily_run')
    scheduler.start()
    return scheduler


if __name__ == '__main__':
    start_scheduler()
