from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import os


def _get_playwright_proxy():
    # Read PLAYWRIGHT_PROXY environment variable, expected format: http://host:port or socks5://...
    return os.environ.get('PLAYWRIGHT_PROXY')


def extract_with_playwright(url, timeout=30000):
    results = []
    proxy = _get_playwright_proxy()
    with sync_playwright() as p:
        launch_args = {'headless': True}
        if proxy:
            launch_args['proxy'] = {'server': proxy}
        browser = p.chromium.launch(**launch_args)
        page = browser.new_page()
        try:
            page.goto(url, timeout=timeout)
            content = page.content()
            soup = BeautifulSoup(content, 'html.parser')
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string)
                except Exception:
                    continue
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if item.get('@type') == 'JobPosting' or item.get('@type','').lower() == 'jobposting':
                        results.append(item)
        finally:
            browser.close()
    return results
