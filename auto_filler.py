"""
Playwright-based auto form filler with anti-bot measures.

Single-job flow:
  start_fill() -> background thread -> opens browser -> fills fields
  -> pauses at 'awaiting_confirmation' -> user confirms -> submits

Batch flow:
  start_fill() per job, jobs run sequentially via _run_batch().
  completion_event is set when a job reaches a terminal state.

Extra profile fields handled beyond standard contact info:
  work_auth, sponsorship, salary_expectation, start_date,
  referral, cover_letter

ATS-specific support:
  Greenhouse      - #application_form, form[action*="apply"]
  Lever           - .application-form, [data-qa="application-form"]
  Workday         - [data-automation-id] patterns
  Ashby           - [data-testid] patterns
  SmartRecruiters - #first_name, #last_name, #email patterns
  iCIMS           - input[data-field] patterns
"""

import os
import re
import json
import time
import random
import threading
import logging
import subprocess
import urllib.request
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
import contextlib

logging.basicConfig(level=logging.INFO, format='[autofill] %(asctime)s %(message)s')
_log = logging.getLogger('autofill')

# Global registry of pending auto-fill sessions
PENDING_APPLICATIONS: dict = {}

# Persistent browser profile — keeps cookies/sessions between runs so users
# only have to log in to each site once.
BROWSER_PROFILE_DIR = os.path.join(os.path.dirname(__file__), '.browser_profile')

# Only one thread may hold the persistent profile at a time (Chrome's SingletonLock)
_PROFILE_LOCK = threading.Lock()

# Chrome lock files that must be removed before a new persistent context can launch
_CHROME_LOCK_FILES = [
    'SingletonLock', 'SingletonCookie', 'SingletonSocket',
    'lockfile', '.lock',
]

def _clear_profile_locks():
    """Remove stale Chrome singleton lock files so a new instance can start."""
    if not os.path.isdir(BROWSER_PROFILE_DIR):
        return
    for fname in _CHROME_LOCK_FILES:
        path = os.path.join(BROWSER_PROFILE_DIR, fname)
        try:
            if os.path.islink(path) or os.path.isfile(path):
                os.remove(path)
                _log.info('Removed stale Chrome lock: %s', path)
        except Exception as e:
            _log.debug('Could not remove lock file %s: %s', path, e)

_CHROME_DEBUG_PORT = 9222
_CHROME_PROC = None
_CHROME_LAUNCH_LOCK = threading.Lock()


def _get_chrome_exe() -> str:
    """Find the Chrome executable on Windows."""
    candidates = [
        os.path.expandvars(r'%PROGRAMFILES%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe'),
        os.path.expandvars(r'%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe'),
        r'C:\Program Files\Google\Chrome\Application\chrome.exe',
        r'C:\Program Files (x86)\Google\Chrome\Application\chrome.exe',
    ]
    return next((p for p in candidates if os.path.exists(p)), None)


def _chrome_debug_alive() -> bool:
    """Return True if Chrome is already listening on the debug port."""
    try:
        urllib.request.urlopen(
            f'http://localhost:{_CHROME_DEBUG_PORT}/json/version', timeout=1
        )
        return True
    except Exception:
        return False


def _ensure_chrome_with_debug() -> str:
    """Start Chrome with remote-debugging if not already running.
    Returns the CDP endpoint URL (e.g. 'http://localhost:9222'), or '' on failure.
    """
    global _CHROME_PROC
    with _CHROME_LAUNCH_LOCK:
        if _chrome_debug_alive():
            return f'http://localhost:{_CHROME_DEBUG_PORT}'
        chrome_exe = _get_chrome_exe()
        if not chrome_exe:
            _log.warning('_ensure_chrome_with_debug: Chrome executable not found')
            return ''
        os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
        _clear_profile_locks()
        cmd = [
            chrome_exe,
            f'--remote-debugging-port={_CHROME_DEBUG_PORT}',
            f'--user-data-dir={BROWSER_PROFILE_DIR}',
            '--disable-blink-features=AutomationControlled',
            '--no-first-run',
            '--disable-default-apps',
            '--start-maximized',
        ]
        _CHROME_PROC = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        _log.info('_ensure_chrome_with_debug: launched Chrome pid=%s', _CHROME_PROC.pid)
        for _ in range(20):
            time.sleep(0.5)
            if _chrome_debug_alive():
                return f'http://localhost:{_CHROME_DEBUG_PORT}'
        _log.warning('_ensure_chrome_with_debug: Chrome did not become ready')
        return ''


# Stored credentials for job boards that require login (used in headless/Quick Apply mode)
SITE_CREDENTIALS_PATH = os.path.join(os.path.dirname(__file__), 'site_credentials.json')


def _load_credentials() -> dict:
    """Load stored site credentials from disk."""
    try:
        with open(SITE_CREDENTIALS_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _get_root_domain(url: str) -> str:
    """Extract root domain (e.g. 'lever.co') from a URL."""
    try:
        netloc = urlparse(url).netloc.lower()
        parts = netloc.split('.')
        if len(parts) >= 2:
            return '.'.join(parts[-2:])
        return netloc
    except Exception:
        return ''


def _auto_login(page, email: str, password: str, entry: dict) -> bool:
    """
    Attempt to fill a login form with the given credentials and submit.
    Returns True if the page no longer looks like a login wall after submission.
    """
    try:
        _set_message(entry, f'Trying stored credentials…')

        # Fill email / username
        filled_email = False
        for sel in [
            'input[type="email"]',
            'input[name="email"]', 'input[name="username"]',
            'input[name="user"]', 'input[name="login"]',
            'input[id*="email" i]', 'input[id*="username" i]',
            'input[placeholder*="email" i]', 'input[placeholder*="username" i]',
        ]:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible(timeout=600):
                    el.clear()
                    el.fill(email)
                    filled_email = True
                    break
            except Exception:
                pass

        if not filled_email:
            _log.warning('_auto_login: could not find email/username field')
            return False

        # Fill password
        try:
            pw = page.locator('input[type="password"]').first
            if pw.count() and pw.is_visible(timeout=600):
                pw.clear()
                pw.fill(password)
            else:
                _log.warning('_auto_login: no visible password field')
                return False
        except Exception:
            return False

        _random_delay(0.3, 0.6)

        # Click login/submit button
        for sel in [
            'button[type="submit"]', 'input[type="submit"]',
            'button:has-text("Sign in")', 'button:has-text("Log in")',
            'button:has-text("Login")', 'button:has-text("Continue")',
            'button:has-text("Next")',
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=500):
                    btn.click()
                    break
            except Exception:
                pass

        # Wait for navigation
        try:
            page.wait_for_load_state('load', timeout=8000)
        except Exception:
            pass
        _random_delay(1.0, 2.0)

        # Check whether we're still on a login wall
        if _detect_login_required(page):
            _log.warning('_auto_login: still on login wall after submission')
            return False

        _log.info('_auto_login: login appears successful')
        return True

    except Exception as e:
        _log.warning('_auto_login failed: %s', e)
        return False


# ── Internal helpers ──────────────────────────────────────────────────────────

def _random_delay(lo=0.3, hi=0.8):
    time.sleep(random.uniform(lo, hi))


def _set_message(entry: dict, msg: str):
    """Update the live status message visible to the user."""
    entry['message'] = msg
    _log.info(msg)


def _stealth_context(playwright, headless=False):
    launch_args = [
        '--disable-blink-features=AutomationControlled',
        '--disable-infobars',
        '--no-first-run',
        '--disable-default-apps',
    ]
    if not headless:
        launch_args.append('--start-maximized')
    browser = playwright.chromium.launch(
        headless=headless,
        args=launch_args,
    )
    ctx_kwargs = dict(
        user_agent=(
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/122.0.0.0 Safari/537.36'
        ),
        locale='en-US',
        timezone_id='America/New_York',
    )
    if headless:
        ctx_kwargs['viewport'] = {'width': 1280, 'height': 900}
    else:
        ctx_kwargs['no_viewport'] = True   # let --start-maximized control size
    context = browser.new_context(**ctx_kwargs)
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
        "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
        "window.chrome = {runtime: {}};"
    )
    return browser, context


def _stealth_persistent_context(playwright):
    """Launch a persistent Chromium context that saves cookies/sessions between runs.
    Users only need to log in to each site once — credentials are stored in BROWSER_PROFILE_DIR.
    Returns (None, context) — persistent contexts have no separate browser object to close.

    Acquires _PROFILE_LOCK for the lifetime of the context (caller must close context to
    release it).  Clears stale Chrome singleton lock files before launching.
    """
    os.makedirs(BROWSER_PROFILE_DIR, exist_ok=True)
    _clear_profile_locks()
    _PROFILE_LOCK.acquire()
    try:
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--disable-infobars',
            '--no-first-run',
            '--disable-default-apps',
            '--start-maximized',
        ]
        context = playwright.chromium.launch_persistent_context(
            BROWSER_PROFILE_DIR,
            headless=False,
            args=launch_args,
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/122.0.0.0 Safari/537.36'
            ),
            locale='en-US',
            timezone_id='America/New_York',
            no_viewport=True,
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});"
            "Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});"
            "window.chrome = {runtime: {}};"
        )
        return None, context  # no separate browser object
    except Exception:
        _PROFILE_LOCK.release()
        raise


def _detect_login_required(page) -> bool:
    """Return True if the current page looks like a login wall rather than an application form."""
    try:
        url = page.url.lower()
        login_url_signals = ['login', 'signin', 'sign-in', '/auth/', 'sso', 'account/login']
        if any(sig in url for sig in login_url_signals):
            return True

        page_text = (page.evaluate('() => document.body.innerText') or '').lower()
        login_text_signals = [
            'please sign in', 'please log in', 'create an account to apply',
            'you must be logged in', 'sign in to apply', 'log in to apply',
            'create account to continue', 'sign in or create an account',
            'to apply for this position, please sign in',
        ]
        if any(sig in page_text for sig in login_text_signals):
            return True

        # Password field without an application form = login gate
        has_password = page.locator('input[type="password"]:visible').count() > 0
        has_apply_form = page.locator(
            '#application_form, .application-form, '
            '[data-qa="application-form"], form[action*="apply"]'
        ).count() > 0
        if has_password and not has_apply_form:
            return True

    except Exception:
        pass
    return False


def _fast_fill(page, element, text: str):
    """Click an element and fill instantly using Playwright's fill() API.
    fill() dispatches input/change events, so no character-by-character typing needed."""
    element.click()
    time.sleep(random.uniform(0.03, 0.08))
    element.fill(text)


def _try_fill(page, labels: list, value: str, filled: list):
    """Try to find a text input by placeholder or label text and fill it."""
    if not value:
        return
    for label in labels:
        for method in ('placeholder', 'label'):
            try:
                if method == 'placeholder':
                    el = page.get_by_placeholder(re.compile(label, re.I)).first
                else:
                    el = page.get_by_label(re.compile(label, re.I)).first
                if el.count() and el.is_visible(timeout=1500):
                    el.clear()
                    _fast_fill(page, el, value)
                    filled.append(f"{labels[0]}: {value[:60]}")
                    return
            except Exception:
                pass


def _try_fill_textarea(page, labels: list, value: str, filled: list):
    """Try to find a textarea by label and fill it."""
    if not value:
        return
    for label in labels:
        try:
            el = page.get_by_label(re.compile(label, re.I)).first
            if el.count() and el.is_visible(timeout=1500):
                el.click()
                time.sleep(random.uniform(0.03, 0.08))
                el.fill(value)
                filled.append(f"{labels[0]}: (text filled)")
                return
        except Exception:
            pass
    # Fallback: first visible textarea on the page
    try:
        ta = page.locator('textarea:visible').first
        if ta.count() and ta.is_visible(timeout=400):
            ta.click()
            time.sleep(0.05)
            ta.fill(value)
            filled.append('cover_letter: (text filled)')
    except Exception:
        pass


def _try_fill_radio(page, labels: list, value: str, filled: list):
    """Try to select a Yes/No radio button near a question matching one of the labels."""
    if not value:
        return
    answer = 'yes' if value.lower() in ('yes', 'y', 'true', '1') else 'no'
    for label in labels:
        try:
            group = page.locator(
                '[class*="field"], [class*="group"], fieldset, [role="radiogroup"]'
            ).filter(has_text=re.compile(label, re.I)).first
            if group.count() and group.is_visible(timeout=500):
                radio = group.get_by_label(re.compile(f'^{answer}$', re.I)).first
                if not radio.count():
                    radio = group.locator(
                        f'[value="{answer}"], [value="{answer.upper()}"],'
                        f'[value="{answer.capitalize()}"]'
                    ).first
                if radio.count() and radio.is_visible(timeout=400):
                    radio.scroll_into_view_if_needed()
                    radio.click()
                    filled.append(f"{labels[0]}: {answer}")
                    return
        except Exception:
            pass


# ── Select / dropdown helpers ─────────────────────────────────────────────────

def _fill_select(page, element, value: str, filled: list, label: str):
    """Select the best matching option in a <select> element.
    Tries exact match first, then partial match, then word match."""
    if not value:
        return False
    try:
        options = element.locator('option').all()
        option_texts = []
        for opt in options:
            try:
                txt = opt.inner_text(timeout=300).strip()
                option_texts.append(txt)
            except Exception:
                option_texts.append('')

        value_lower = value.lower()

        # 1. Exact match (case-insensitive)
        for txt in option_texts:
            if txt.lower() == value_lower:
                element.select_option(label=txt)
                filled.append(f'{label}: {txt}')
                return True

        # 2. Partial match (option contains value or value contains option)
        for txt in option_texts:
            tl = txt.lower()
            if value_lower in tl or tl in value_lower:
                if len(tl) > 1:  # avoid empty/whitespace options
                    element.select_option(label=txt)
                    filled.append(f'{label}: {txt}')
                    return True

        # 3. Word match (any word from value appears in option text)
        value_words = set(value_lower.split())
        for txt in option_texts:
            tl = txt.lower()
            tl_words = set(tl.split())
            if value_words & tl_words:
                if len(tl) > 1:
                    element.select_option(label=txt)
                    filled.append(f'{label}: {txt}')
                    return True

    except Exception:
        pass
    return False


def _try_fill_select(page, labels: list, value: str, filled: list):
    """Find a <select> element near a matching label and select the best option."""
    if not value:
        return
    for label in labels:
        try:
            el = page.get_by_label(re.compile(label, re.I)).first
            # Check it's actually a select
            tag = el.evaluate('e => e.tagName', ) if el.count() else ''
            if tag == 'SELECT' and el.is_visible(timeout=800):
                if _fill_select(page, el, value, filled, labels[0]):
                    return
        except Exception:
            pass
        # Also try finding a select whose nearby label matches
        try:
            selects = page.locator('select:visible').all()
            for sel_el in selects:
                try:
                    nearby_text = sel_el.evaluate("""el => {
                        let p = el.parentElement;
                        for (let i = 0; i < 3; i++) {
                            if (!p) break;
                            const lbl = p.querySelector('label');
                            if (lbl) return lbl.innerText;
                            p = p.parentElement;
                        }
                        return '';
                    }""")
                    if nearby_text and re.search(label, nearby_text, re.I):
                        if _fill_select(page, sel_el, value, filled, labels[0]):
                            return
                except Exception:
                    pass
        except Exception:
            pass


def _fill_common_selects(page, profile: dict, filled: list):
    """Handle standard dropdown (select) fields that appear in many applications."""
    # Country
    for country_val in ['United States', 'US', 'USA', 'United States of America']:
        _try_fill_select(page, ['country', 'nation'], country_val, filled)
        if any('country' in f.lower() for f in filled):
            break

    # Work authorization (select version, not radio)
    work_auth = profile.get('work_auth', '')
    if work_auth:
        _try_fill_select(
            page,
            ['work authorization', 'authorized to work', 'eligible to work', 'work eligibility'],
            'Yes' if work_auth.lower() in ('yes', 'y', 'true', '1') else work_auth,
            filled,
        )

    # EEO dropdowns — use profile values if set, else decline
    eeo_decline_values = ['Decline', 'I prefer not to answer', 'Prefer not to say',
                          'Prefer not to disclose', 'I do not wish', 'Choose not to identify']

    def _fill_eeo(labels, profile_key, fallback_values):
        """Fill an EEO dropdown using profile value or decline option."""
        pval = profile.get(profile_key, '').strip()
        if pval and pval.lower() not in ('', 'decline', 'prefer not to say'):
            _try_fill_select(page, labels, pval, filled)
            # If select fill worked, done; else fall through to decline
            if any(labels[0].lower() in f.lower() for f in filled):
                return
        for dval in fallback_values:
            _try_fill_select(page, labels, dval, filled)
            if any(labels[0].lower() in f.lower() for f in filled):
                return

    _fill_eeo(['gender', 'sex'], 'eeo_gender', eeo_decline_values)
    _fill_eeo(['race', 'ethnicity', 'racial'], 'eeo_race', eeo_decline_values)
    _fill_eeo(['veteran', 'military', 'protected veteran'], 'eeo_veteran', eeo_decline_values)
    _fill_eeo(['disability'], 'eeo_disability', eeo_decline_values)

    # Degree level
    degree = profile.get('degree', '') or profile.get('education_level', '')
    if degree:
        _try_fill_select(page, ['degree', 'education level', 'highest education',
                                 'highest degree', 'level of education'], degree, filled)


# ── Checkbox handling ─────────────────────────────────────────────────────────

def _fill_checkboxes(page, profile: dict, filled: list):
    """Handle checkboxes: check required agreement boxes, decline EEO multi-select checkboxes."""
    try:
        checkboxes = page.locator('input[type="checkbox"]:visible').all()
        for cb in checkboxes:
            try:
                # Skip already-checked boxes
                if cb.is_checked(timeout=300):
                    continue

                label_text = _get_field_label(page, cb).lower()

                # Auto-check "I agree to terms", "I acknowledge", etc.
                agree_signals = ['agree', 'accept', 'acknowledge', 'certify', 'confirm',
                                 'terms', 'privacy policy', 'consent', 'authorize']
                is_required = False
                try:
                    is_required = bool(cb.get_attribute('required')) or \
                                  cb.get_attribute('aria-required') == 'true'
                except Exception:
                    pass

                if is_required and any(sig in label_text for sig in agree_signals):
                    cb.click()
                    filled.append(f'checkbox (agree/terms): checked')
                    continue

                # For EEO "Decline" checkboxes
                if any(sig in label_text for sig in
                       ['decline', 'prefer not', 'do not wish', 'choose not']):
                    cb.click()
                    filled.append(f'checkbox (EEO decline): checked')

            except Exception:
                continue
    except Exception:
        pass


# ── Unfilled field detection ──────────────────────────────────────────────────

def _detect_unfilled_fields(page) -> list:
    """
    After filling is complete, scan all visible inputs/selects/textareas
    that are empty or have no selection.
    Returns list of dicts: {'label': str, 'type': str, 'required': bool}
    Skips: file inputs, hidden inputs, submit buttons, search inputs.
    """
    unfilled = []
    seen_labels = set()

    def _check_element(el, el_type: str):
        try:
            if not el.is_visible(timeout=300):
                return
            # Skip certain types
            input_type = ''
            try:
                input_type = (el.get_attribute('type') or '').lower()
            except Exception:
                pass
            if input_type in ('file', 'hidden', 'submit', 'button', 'reset',
                              'image', 'checkbox', 'radio'):
                return
            if input_type == 'search':
                return

            # Check if empty
            is_empty = False
            if el_type == 'select':
                try:
                    val = el.evaluate('e => e.value')
                    is_empty = not val or val == '' or val == '0'
                except Exception:
                    is_empty = True
            else:
                try:
                    val = el.input_value(timeout=300)
                    is_empty = not val or len(val.strip()) == 0
                except Exception:
                    is_empty = True

            if not is_empty:
                return

            # Get label
            label = _get_field_label(page, el)
            if not label:
                try:
                    ph = el.get_attribute('placeholder') or ''
                    label = ph.strip()
                except Exception:
                    pass
            if not label:
                label = f'(unlabeled {el_type})'

            label_key = label.lower().strip()
            if label_key in seen_labels:
                return
            seen_labels.add(label_key)

            # Check required
            is_required = False
            try:
                is_required = bool(el.get_attribute('required')) or \
                              el.get_attribute('aria-required') == 'true'
            except Exception:
                pass

            unfilled.append({
                'label': label,
                'type': el_type,
                'required': is_required,
            })
        except Exception:
            pass

    # Scan inputs
    try:
        for el in page.locator('input:visible').all():
            _check_element(el, 'input')
    except Exception:
        pass

    # Scan selects
    try:
        for el in page.locator('select:visible').all():
            _check_element(el, 'select')
    except Exception:
        pass

    # Scan textareas
    try:
        for el in page.locator('textarea:visible').all():
            _check_element(el, 'textarea')
    except Exception:
        pass

    return unfilled


# ── Multi-page form navigation ────────────────────────────────────────────────

def _try_advance_page(page) -> bool:
    """Look for Next/Continue buttons and click one if found.
    Returns True if a button was clicked and navigation occurred."""
    next_patterns = [
        'button:has-text("Next")',
        'button:has-text("Continue")',
        'button:has-text("Next Step")',
        'button:has-text("Proceed")',
        'button:has-text("Next Page")',
        'a:has-text("Next")',
        'a:has-text("Continue")',
        '[data-automation-id="bottomNavigationNext"]',
        '[data-automation-id="nextButton"]',
        'input[type="button"][value*="Next"]',
        'input[type="button"][value*="Continue"]',
    ]
    for pat in next_patterns:
        try:
            btn = page.locator(pat).first
            if btn.is_visible(timeout=500):
                btn.scroll_into_view_if_needed()
                btn.click()
                try:
                    page.wait_for_load_state('load', timeout=5000)
                except Exception:
                    pass
                _random_delay(0.5, 1.0)
                _log.info('Advanced to next page via: %s', pat)
                return True
        except Exception:
            pass
    return False


# ── ATS detection ─────────────────────────────────────────────────────────────

def _detect_ats(url: str) -> str:
    """Detect which ATS the URL belongs to."""
    url_lower = url.lower()
    if 'greenhouse.io' in url_lower or 'boards.greenhouse' in url_lower:
        return 'greenhouse'
    if 'lever.co' in url_lower or 'jobs.lever' in url_lower:
        return 'lever'
    if 'myworkdayjobs.com' in url_lower or 'workday.com' in url_lower:
        return 'workday'
    if 'ashbyhq.com' in url_lower or 'jobs.ashbyhq' in url_lower:
        return 'ashby'
    if 'smartrecruiters.com' in url_lower:
        return 'smartrecruiters'
    if 'jobvite.com' in url_lower:
        return 'jobvite'
    if 'icims.com' in url_lower:
        return 'icims'
    return 'generic'


def _wait_for_form(page, ats: str, timeout_ms: int = 12000) -> bool:
    """
    Wait for a form to appear after clicking Apply.
    Returns True if a form was detected, False if timed out.
    """
    selectors_by_ats = {
        'greenhouse': [
            '#application_form',
            '#application-form',
            'form[action*="apply"]',
            '[data-testid="application-form"]',
            'form.application',
            'form[action*="applications"]',
            '#app_fields',
            'input[name="job_application[first_name]"]',
        ],
        'lever': [
            '.application-form',
            '[data-qa="application-form"]',
            'form.application',
            'input[name="name"]',
        ],
        'workday': [
            '[data-automation-id="applyStep"]',
            '[data-automation-id="submitButton"]',
            'form[data-automation-id]',
            '[data-automation-id="firstName"]',
        ],
        'ashby': [
            '[data-testid="application-form"]',
            'form[data-testid]',
            '[data-testid="firstName"]',
            '.ashby-application-form',
        ],
        'smartrecruiters': [
            '#application-form',
            '.smart-apply',
            'form.application-form',
            '#first_name',
        ],
        'icims': [
            'input[data-field="firstName"]',
            'form.iCIMS_InfoMsg_Form',
            '.iCIMS_MainColumn',
        ],
        'generic': [
            'form input[type="text"]',
            'form input[type="email"]',
            '#first_name', '#firstname', '#firstName',
            'input[name="first_name"]', 'input[name="firstName"]',
            'input[placeholder*="First"]',
            'input[placeholder*="Name"]',
        ],
    }

    selectors = selectors_by_ats.get(ats, selectors_by_ats['generic'])
    if ats != 'generic':
        selectors = selectors + selectors_by_ats['generic']

    deadline = time.time() + timeout_ms / 1000
    while time.time() < deadline:
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if loc.count() and loc.is_visible(timeout=400):
                    _log.info('Form detected via selector: %s', sel)
                    return True
            except Exception:
                pass
        time.sleep(0.5)
    return False


def _click_apply_button(page, entry: dict) -> bool:
    """Try to find and click an Apply button, then wait for the form to load."""
    _set_message(entry, 'Looking for Apply button on job page...')
    patterns = [
        '[data-automation="job-detail-apply"]',
        'button:has-text("Apply Now")', 'a:has-text("Apply Now")',
        'button:has-text("Apply for this job")', 'a:has-text("Apply for this job")',
        'button:has-text("Apply for Job")', 'a:has-text("Apply for Job")',
        'button:has-text("Apply")', 'a:has-text("Apply")',
        'text=Apply Now', 'text=Apply',
        '[data-automation-id="applyNowButton"]',
        '.postings-btn', '.btn-apply',
    ]
    for pat in patterns:
        try:
            btn = page.locator(pat).first
            if btn.is_visible(timeout=1500):
                _set_message(entry, f'Clicking Apply button (matched: {pat})...')
                btn.scroll_into_view_if_needed()
                _random_delay(0.3, 0.8)
                btn.click()
                try:
                    page.wait_for_load_state('load', timeout=8000)
                except Exception:
                    pass
                _random_delay(0.5, 1.0)
                return True
        except Exception:
            pass

    _set_message(entry, 'No Apply button found — the page may already show the form.')
    return False


def _upload_resume(page, resume_path: str, filled: list):
    """Find a file input and upload the resume."""
    try:
        for selector in ['input[type="file"]:visible', 'input[type="file"]']:
            file_input = page.locator(selector).first
            if file_input.count() > 0:
                file_input.set_input_files(resume_path)
                _random_delay(0.5, 1.0)
                filled.append('resume: uploaded')
                return
    except Exception:
        pass


def _find_submit_button(page):
    """Find the form submit button without clicking it."""
    patterns = [
        'button[type="submit"]', 'input[type="submit"]',
        'button:has-text("Submit")', 'button:has-text("Submit Application")',
        'button:has-text("Send Application")', 'button:has-text("Review Application")',
        '[data-automation-id="submitButton"]',
        '#submit_app',
    ]
    for pat in patterns:
        try:
            btn = page.locator(pat).last
            if btn.is_visible(timeout=500):
                return btn
        except Exception:
            pass
    return None


def _fill_by_name(page, name_attr: str, value: str, filled: list, label: str = None):
    """Fill an input by its name attribute."""
    if not value:
        return False
    try:
        el = page.locator(f'input[name="{name_attr}"], textarea[name="{name_attr}"]').first
        if el.count() and el.is_visible(timeout=1500):
            el.clear()
            _fast_fill(page, el, value)
            filled.append(f'{label or name_attr}: {value[:60]}')
            return True
    except Exception:
        pass
    return False


def _fill_by_id(page, id_attr: str, value: str, filled: list, label: str = None):
    """Fill an input by its id attribute."""
    if not value:
        return False
    try:
        el = page.locator(f'#{id_attr}').first
        if el.count() and el.is_visible(timeout=1500):
            el.clear()
            _fast_fill(page, el, value)
            filled.append(f'{label or id_attr}: {value[:60]}')
            return True
    except Exception:
        pass
    return False


def _fill_greenhouse(page, profile: dict, first_name: str, last_name: str, filled: list,
                     entry: dict = None):
    """Fill Greenhouse-specific form fields using known name= and id= patterns."""
    def _msg(m):
        if entry:
            _set_message(entry, m)
    _msg('Filling name and contact fields...')
    field_defs = [
        ('first_name', first_name,
            ['first_name', 'firstName'],
            ['job_application[first_name]', 'first_name']),
        ('last_name', last_name,
            ['last_name', 'lastName'],
            ['job_application[last_name]', 'last_name']),
        ('email', profile.get('email', ''),
            ['email', 'email_address'],
            ['job_application[email]', 'email']),
        ('phone', profile.get('phone', ''),
            ['phone', 'phone_number'],
            ['job_application[phone]', 'phone']),
        ('city', profile.get('city', ''),
            ['location', 'city'],
            ['job_application[location]', 'location']),
        ('linkedin', profile.get('linkedin', ''),
            ['linkedin_profile', 'linkedin'],
            ['job_application[linkedin_profile]', 'linkedin_profile']),
        ('website', profile.get('website', ''),
            ['website', 'portfolio'],
            ['job_application[website]', 'website']),
    ]

    filled_labels = set()
    for label, value, id_variants, name_variants in field_defs:
        if not value or label in filled_labels:
            continue
        for id_ in id_variants:
            if _fill_by_id(page, id_, value, filled, label):
                filled_labels.add(label)
                _random_delay(0.03, 0.08)
                break
        else:
            for name_ in name_variants:
                if _fill_by_name(page, name_, value, filled, label):
                    filled_labels.add(label)
                    _random_delay(0.03, 0.08)
                    break

    # Cover letter textarea
    cl = profile.get('cover_letter', '')
    if cl and 'cover_letter' not in filled_labels:
        _msg('Filling cover letter...')
        for sel in ['#cover_letter_text', '#cover_letter',
                    'textarea[name*="cover_letter"]', 'textarea[id*="cover"]', 'textarea']:
            try:
                ta = page.locator(sel).first
                if ta.count() and ta.is_visible(timeout=800):
                    ta.click()
                    time.sleep(0.05)
                    ta.fill(cl)
                    filled.append('cover_letter: (text filled)')
                    break
            except Exception:
                pass

    # Work auth / sponsorship radios
    _msg('Filling authorization questions...')
    for labels, value in [
        (['authorized', 'eligible to work', 'work authorization'], profile.get('work_auth', '')),
        (['sponsorship', 'visa'], profile.get('sponsorship', '')),
    ]:
        _try_fill_radio(page, labels, value, filled)


def _fill_lever(page, profile: dict, first_name: str, last_name: str, filled: list):
    """Fill Lever-specific form fields."""
    lever_fields = [
        ('name',     profile.get('name', ''),       'full_name'),
        ('email',    profile.get('email', ''),      'email'),
        ('phone',    profile.get('phone', ''),      'phone'),
        ('org',      profile.get('company', ''),    'current_company'),
        ('urls[LinkedIn]', profile.get('linkedin', ''), 'linkedin'),
        ('urls[Portfolio]', profile.get('website', ''), 'website'),
        ('comments', profile.get('cover_letter', ''), 'cover_letter'),
    ]
    for name_attr, value, label in lever_fields:
        if _fill_by_name(page, name_attr, value, filled, label):
            _random_delay(0.03, 0.08)


def _fill_workday(page, profile: dict, first_name: str, last_name: str, filled: list):
    """Fill Workday-specific form fields using data-automation-id patterns."""
    wd_fields = [
        ('[data-automation-id="legalNameSection_firstName"]', first_name, 'first_name'),
        ('[data-automation-id="legalNameSection_lastName"]',  last_name,  'last_name'),
        ('[data-automation-id="email"]',                      profile.get('email', ''), 'email'),
        ('[data-automation-id="phone-number"]',               profile.get('phone', ''), 'phone'),
        ('[data-automation-id="addressSection_city"]',        profile.get('city', ''),  'city'),
    ]
    for sel, value, label in wd_fields:
        if not value:
            continue
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible(timeout=1500):
                el.clear()
                _fast_fill(page, el, value)
                filled.append(f'{label}: {value[:60]}')
                _random_delay(0.03, 0.08)
        except Exception:
            pass


def _fill_ashby(page, profile: dict, first_name: str, last_name: str, filled: list):
    """Fill Ashby-specific form fields using data-testid patterns."""
    ashby_fields = [
        ('[data-testid="firstName"]',    first_name,               'first_name'),
        ('[data-testid="lastName"]',     last_name,                'last_name'),
        ('[data-testid="email"]',        profile.get('email', ''), 'email'),
        ('[data-testid="phone"]',        profile.get('phone', ''), 'phone'),
        ('[data-testid="linkedInUrl"]',  profile.get('linkedin', ''), 'linkedin'),
        ('[data-testid="websiteUrl"]',   profile.get('website', ''), 'website'),
        ('[data-testid="coverLetter"]',  profile.get('cover_letter', ''), 'cover_letter'),
    ]
    for sel, value, label in ashby_fields:
        if not value:
            continue
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible(timeout=1500):
                el.clear()
                el.fill(value)
                filled.append(f'{label}: {value[:60]}')
                _random_delay(0.03, 0.08)
        except Exception:
            pass


def _fill_smartrecruiters(page, profile: dict, first_name: str, last_name: str, filled: list):
    """Fill SmartRecruiters-specific form fields."""
    sr_fields = [
        ('#first_name',  first_name,               'first_name'),
        ('#last_name',   last_name,                'last_name'),
        ('#email',       profile.get('email', ''), 'email'),
        ('#phone',       profile.get('phone', ''), 'phone'),
    ]
    for sel, value, label in sr_fields:
        if not value:
            continue
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible(timeout=1500):
                el.clear()
                _fast_fill(page, el, value)
                filled.append(f'{label}: {value[:60]}')
                _random_delay(0.03, 0.08)
        except Exception:
            pass

    # Cover letter via rich text editor
    cl = profile.get('cover_letter', '')
    if cl:
        for sel in ['.sl-rich-text-editor', 'div[contenteditable="true"]', 'textarea']:
            try:
                el = page.locator(sel).first
                if el.count() and el.is_visible(timeout=800):
                    el.click()
                    time.sleep(0.05)
                    try:
                        el.fill(cl)
                    except Exception:
                        page.keyboard.type(cl)
                    filled.append('cover_letter: (text filled)')
                    break
            except Exception:
                pass


def _fill_icims(page, profile: dict, first_name: str, last_name: str, filled: list):
    """Fill iCIMS-specific form fields using data-field attributes."""
    icims_fields = [
        ('input[data-field="firstName"]',  first_name,               'first_name'),
        ('input[data-field="lastName"]',   last_name,                'last_name'),
        ('input[data-field="email"]',      profile.get('email', ''), 'email'),
        ('input[data-field="phone"]',      profile.get('phone', ''), 'phone'),
        ('input[data-field="address"]',    profile.get('city', ''),  'city'),
    ]
    for sel, value, label in icims_fields:
        if not value:
            continue
        try:
            el = page.locator(sel).first
            if el.count() and el.is_visible(timeout=1500):
                el.clear()
                _fast_fill(page, el, value)
                filled.append(f'{label}: {value[:60]}')
                _random_delay(0.03, 0.08)
        except Exception:
            pass


# ── Q&A bank helpers ─────────────────────────────────────────────────────────

def _load_qa_bank(path: str) -> list:
    """Load [{question, answer}] from qa_bank.json. Returns [] on any error."""
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_qa_bank(path: str, bank: list):
    """Atomically write qa_bank.json."""
    if not path:
        return
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(bank, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log.warning('Could not save Q&A bank: %s', e)


_QA_BANK_LOCK = threading.Lock()


def _upsert_qa_bank(path: str, question: str, answer: str):
    """Thread-safe upsert of a Q&A pair into the bank file."""
    with _QA_BANK_LOCK:
        bank = _load_qa_bank(path)
        q_lower = question.lower().strip()
        match = next((i for i, e in enumerate(bank)
                      if e.get('question', '').lower().strip() == q_lower), None)
        if match is not None:
            bank[match]['answer'] = answer
        else:
            bank.append({'question': question, 'answer': answer})
        _save_qa_bank(path, bank)


def _lookup_qa_bank(bank: list, question: str) -> str:
    """
    Return a saved answer if the bank contains a sufficiently similar question.
    Uses simple word-overlap similarity (no external deps).
    Returns '' if no good match found.
    """
    if not bank or not question:
        return ''
    import re as _re2
    def _tokens(s):
        return set(_re2.findall(r'[a-z]+', s.lower()))

    q_tokens = _tokens(question)
    if not q_tokens:
        return ''

    best_score = 0.0
    best_answer = ''
    for entry in bank:
        saved_q = entry.get('question', '')
        saved_a = entry.get('answer', '')
        if not saved_q or not saved_a:
            continue
        s_tokens = _tokens(saved_q)
        if not s_tokens:
            continue
        intersection = q_tokens & s_tokens
        union = q_tokens | s_tokens
        jaccard = len(intersection) / len(union)
        if jaccard > best_score:
            best_score = jaccard
            best_answer = saved_a

    # Require ≥60% token overlap to consider a match
    return best_answer if best_score >= 0.60 else ''


# ── AI-powered custom question answering ─────────────────────────────────────

_STANDARD_FIELD_NAMES = {
    'name', 'first name', 'last name', 'full name', 'firstname', 'lastname',
    'email', 'e-mail', 'email address', 'phone', 'mobile', 'telephone',
    'city', 'location', 'address', 'zip', 'postal', 'state', 'country',
    'linkedin', 'linkedin url', 'linkedin profile',
    'website', 'portfolio', 'github', 'personal site',
    'salary', 'desired salary', 'expected salary', 'compensation',
    'start date', 'available', 'availability',
    'referral', 'how did you hear', 'source',
    'cover letter', 'resume', 'upload',
}

_QUESTION_SIGNALS = [
    'why', 'how', 'what', 'where', 'when', 'describe', 'tell us', 'tell me',
    'explain', 'share', 'provide', 'please', 'additional', 'anything else',
    'background', 'experience', 'passion', 'interested', 'motivation',
    'strengths', 'strength', 'weakness', 'challenge', 'accomplishment',
    'achievement', 'qualify', 'qualified', 'contribute', 'value', 'goal',
    'impact', 'learn', 'grow', 'team', 'leadership', 'project', 'situation',
    'example', 'time when', 'briefly', 'summary', 'about yourself',
]


def _clean_label(text: str) -> str:
    """Strip required-field markers and whitespace from a label string."""
    import re as _re
    t = text.strip()
    # Remove trailing asterisk(s), "(required)", "required", "(optional)"
    t = _re.sub(r'\s*\*+\s*$', '', t)
    t = _re.sub(r'\s*\(required\)\s*$', '', t, flags=_re.I)
    t = _re.sub(r'\s+required\s*$', '', t, flags=_re.I)
    t = _re.sub(r'\s*\(optional\)\s*$', '', t, flags=_re.I)
    return t.strip()


def _get_field_label(page, element) -> str:
    """Extract the label/question text associated with a form element."""
    try:
        v = element.get_attribute('aria-label')
        if v and len(v.strip()) > 3:
            return _clean_label(v)
    except Exception:
        pass

    try:
        lby = element.get_attribute('aria-labelledby')
        if lby:
            for lid in lby.split():
                try:
                    text = page.locator(f'#{lid}').inner_text(timeout=500)
                    if text and len(text.strip()) > 3:
                        return _clean_label(text)
                except Exception:
                    pass
    except Exception:
        pass

    try:
        fid = element.get_attribute('id')
        if fid:
            lbl = page.locator(f'label[for="{fid}"]').first
            if lbl.count():
                text = lbl.inner_text(timeout=500)
                if text and len(text.strip()) > 3:
                    return _clean_label(text)
    except Exception:
        pass

    try:
        parent_text = element.evaluate("""el => {
            let p = el.parentElement;
            for (let i = 0; i < 5; i++) {
                if (!p) break;
                const candidates = p.querySelectorAll(
                    'label, [class*="label"], [class*="question"], [class*="prompt"], legend, h3, h4, p'
                );
                for (const c of candidates) {
                    // Skip if this element contains the input itself
                    if (c.contains(el)) continue;
                    const t = c.innerText.trim();
                    if (t.length > 5 && t.length < 500) return t;
                }
                p = p.parentElement;
            }
            return '';
        }""")
        if parent_text and len(parent_text.strip()) > 5:
            return _clean_label(parent_text)
    except Exception:
        pass

    try:
        ph = element.get_attribute('placeholder')
        if ph and len(ph.strip()) > 5:
            return _clean_label(ph)
    except Exception:
        pass

    return ''


def _is_custom_question(label: str) -> bool:
    """Return True if the label looks like an open-ended question, not a standard field."""
    if not label or len(label.strip()) < 8:
        return False
    lower = label.lower().strip()

    for std in _STANDARD_FIELD_NAMES:
        if lower == std or lower.startswith(std + ' ') or lower.endswith(' ' + std):
            return False

    if lower.endswith('?'):
        return True
    if any(sig in lower for sig in _QUESTION_SIGNALS):
        return True

    return False


def _fill_custom_questions(page, profile: dict, filled: list, entry: dict = None):
    """
    Scan for unfilled textareas (and long-text inputs) whose labels look like
    open-ended questions, then use Gemini AI to answer them from the resume.
    Includes Greenhouse-specific scanning by [id^="question_"] pattern.
    """
    import os
    if not os.environ.get('GEMINI_API_KEY'):
        return

    resume_text     = profile.get('resume_text', '')
    job_title       = profile.get('_job_title', '')
    company         = profile.get('_company', '')
    job_description = profile.get('_job_description', '')

    # Build a profile summary to use as context even when resume_text is thin
    profile_context = resume_text
    if not profile_context:
        parts = []
        if profile.get('name'):
            parts.append(f"Name: {profile['name']}")
        if profile.get('skills'):
            skills = profile['skills'] if isinstance(profile['skills'], list) else [profile['skills']]
            parts.append(f"Skills: {', '.join(str(s) for s in skills[:20])}")
        if profile.get('bio') or profile.get('summary'):
            parts.append(f"Bio: {profile.get('bio') or profile.get('summary', '')}")
        if profile.get('target_roles'):
            roles = profile['target_roles'] if isinstance(profile['target_roles'], list) else [profile['target_roles']]
            parts.append(f"Target roles: {', '.join(str(r) for r in roles[:5])}")
        if profile.get('cover_letter'):
            parts.append(f"Cover letter:\n{profile['cover_letter'][:800]}")
        profile_context = '\n'.join(parts)

    if not profile_context.strip():
        _log.warning('_fill_custom_questions: no resume_text or profile context — skipping AI answers')
        return

    already_filled_labels = {f.split(':')[0].lower().strip() for f in filled}

    try:
        from cover_letter_generator import answer_application_question
    except ImportError:
        return

    # Load Q&A bank once for this fill session
    qa_bank_path = profile.get('_qa_bank_path', '')
    qa_bank = _load_qa_bank(qa_bank_path)
    _log.info('Q&A bank loaded: %d entries from %s', len(qa_bank), qa_bank_path or '(none)')

    def _answer_element(el, is_greenhouse_question=False):
        """Fill a single textarea/input, using bank first then AI. Returns True on success."""
        try:
            current_val = el.input_value(timeout=500)
            if current_val and len(current_val.strip()) > 10:
                return False

            label = _get_field_label(page, el)
            if not label:
                return False

            label_key = label.lower().strip()
            if any(label_key.startswith(k) for k in ('cover letter', 'cover_letter')):
                return False
            if label_key in already_filled_labels:
                return False
            # For Greenhouse question_ IDs, skip the _is_custom_question heuristic —
            # the ID pattern already confirms it's a custom question.
            if not is_greenhouse_question and not _is_custom_question(label):
                return False

            # ── Check bank first ──────────────────────────────────────────
            answer = _lookup_qa_bank(qa_bank, label)
            source = 'bank'
            if answer:
                _log.info('Q&A bank hit for: %s', label[:80])
                if entry:
                    _set_message(entry, f'Using saved answer for: {label[:60]}')
            else:
                # ── Fallback to Gemini ────────────────────────────────────
                _log.info('AI answering question: %s', label[:80])
                if entry:
                    _set_message(entry, f'Generating answer for: {label[:60]}...')
                answer = answer_application_question(
                    label, profile_context, job_title, company, job_description, profile
                )
                source = 'AI'

            if not answer:
                return False

            el.scroll_into_view_if_needed(timeout=2000)
            el.click()
            time.sleep(random.uniform(0.05, 0.15))
            el.fill(answer)
            filled.append(f'Q: {label[:60]} ({source} answered)')
            if entry:
                _set_message(entry, f'Filled: {label[:60]}')
            already_filled_labels.add(label_key)
            _random_delay(0.1, 0.3)

            # ── Persist AI answers to bank for future reuse ───────────────
            if source == 'AI' and qa_bank_path:
                _upsert_qa_bank(qa_bank_path, label, answer)
                # Keep in-memory bank in sync so this session also benefits
                q_lower = label.lower().strip()
                match = next((i for i, e in enumerate(qa_bank)
                              if e.get('question', '').lower().strip() == q_lower), None)
                if match is not None:
                    qa_bank[match]['answer'] = answer
                else:
                    qa_bank.append({'question': label, 'answer': answer})

            return True
        except Exception as _e:
            _log.debug('_answer_element skip: %s', _e)
            return False

    # --- Greenhouse-specific: scan [id^="question_"] elements first ---
    # In old Greenhouse boards, each custom question textarea/input has id="question_XXXXXX".
    # In new React boards, the container div has that id and the input is a child.
    # We trust they're real questions without needing _is_custom_question heuristics.
    try:
        gh_els = page.locator('[id^="question_"]').all()
        if gh_els and entry:
            _set_message(entry, f'Found {len(gh_els)} Greenhouse question field(s) — answering with AI...')
        for el in gh_els:
            try:
                tag = el.evaluate('el => el.tagName.toLowerCase()')
                if tag == 'textarea':
                    _answer_element(el, is_greenhouse_question=True)
                elif tag == 'input':
                    t = el.get_attribute('type') or 'text'
                    if t.lower() == 'text':
                        _answer_element(el, is_greenhouse_question=True)
                elif tag in ('div', 'section', 'fieldset'):
                    # Container — find textarea or text input inside it
                    for child_sel in ['textarea', 'input[type="text"]']:
                        try:
                            child = el.locator(child_sel).first
                            if child.count() and child.is_visible(timeout=400):
                                _answer_element(child, is_greenhouse_question=True)
                                break
                        except Exception:
                            pass
            except Exception as _e:
                _log.debug('GH question_ scan skip: %s', _e)
    except Exception:
        pass

    # --- Generic: scan visible textareas ---
    try:
        textareas = page.locator('textarea:visible').all()
    except Exception:
        textareas = []

    for ta in textareas:
        try:
            _answer_element(ta, is_greenhouse_question=False)
        except Exception as _e:
            _log.debug('_fill_custom_questions textarea skip: %s', _e)
            continue

    # --- Scan unfilled selects with non-trivial options (education, experience, etc.) ---
    try:
        selects = page.locator('select:visible').all()
    except Exception:
        selects = []

    _SELECT_SKIP = {'country', 'state', 'province', 'gender', 'ethnicity', 'race',
                    'veteran', 'disability', 'pronouns', 'currency', 'timezone'}

    for sel_el in selects:
        try:
            current = sel_el.input_value(timeout=500)
            if current and current not in ('', '0', 'none', 'select', 'please select'):
                continue  # already has a value
            label = _get_field_label(page, sel_el)
            if not label:
                continue
            label_key = label.lower().strip()
            if any(s in label_key for s in _SELECT_SKIP):
                continue
            if label_key in already_filled_labels:
                continue

            # Get available options
            options = sel_el.locator('option').all_inner_texts()
            options = [o.strip() for o in options if o.strip()
                       and o.strip().lower() not in ('', 'select', 'please select', '--')]
            if not options:
                continue

            _log.info('AI picking select option for: %s', label[:60])
            # Ask AI which option best fits the candidate
            prompt_q = (f'For the dropdown question "{label}", the available options are: '
                        f'{", ".join(options[:20])}. Which option best fits this candidate? '
                        f'Reply with ONLY the exact option text, nothing else.')
            answer = answer_application_question(
                prompt_q, resume_text, job_title, company, job_description, profile
            )
            if not answer:
                continue
            # Find closest matching option
            answer_lower = answer.strip().lower()
            best = next((o for o in options if o.lower() == answer_lower), None)
            if not best:
                best = next((o for o in options if answer_lower in o.lower()), None)
            if best:
                sel_el.select_option(label=best)
                filled.append(f'Q: {label[:60]} → {best} (AI selected)')
                already_filled_labels.add(label_key)
                _random_delay(0.1, 0.2)
        except Exception as _e:
            _log.debug('_fill_custom_questions select skip: %s', _e)
            continue

    # --- Scan visible text inputs whose labels look like open questions ---
    try:
        inputs = page.locator('input[type="text"]:visible').all()
    except Exception:
        inputs = []

    for inp in inputs:
        try:
            current_val = inp.input_value(timeout=500)
            if current_val and len(current_val.strip()) > 3:
                continue

            label = _get_field_label(page, inp)
            if not label:
                continue

            label_key = label.lower().strip()
            if label_key in already_filled_labels:
                continue
            if not _is_custom_question(label):
                continue

            _log.info('AI answering text input question: %s', label[:80])
            answer = answer_application_question(
                label, resume_text, job_title, company, job_description, profile
            )
            if not answer:
                continue

            first_sentence = answer.split('.')[0].strip()
            if first_sentence:
                answer = first_sentence + '.'

            inp.clear()
            _fast_fill(page, inp, answer)
            filled.append(f'Q: {label[:60]} (AI answered)')
            already_filled_labels.add(label_key)
            _random_delay(0.1, 0.3)

        except Exception as _e:
            _log.debug('_fill_custom_questions input skip: %s', _e)
            continue


# ── Public API ────────────────────────────────────────────────────────────────

def _run_fill_pass(page, ats: str, profile: dict, first_name: str, last_name: str,
                   field_map: list, radio_map: list, textarea_map: list,
                   resume_path: str, filled: list, entry: dict):
    """Execute one pass of form filling (ATS-specific + generic fallback)."""

    # ── ATS-specific filling (direct selectors) ────────────────────────────
    if ats == 'greenhouse':
        _fill_greenhouse(page, profile, first_name, last_name, filled, entry)
    elif ats == 'lever':
        _fill_lever(page, profile, first_name, last_name, filled)
    elif ats == 'workday':
        _fill_workday(page, profile, first_name, last_name, filled)
    elif ats == 'ashby':
        _fill_ashby(page, profile, first_name, last_name, filled)
    elif ats == 'smartrecruiters':
        _fill_smartrecruiters(page, profile, first_name, last_name, filled)
    elif ats == 'icims':
        _fill_icims(page, profile, first_name, last_name, filled)

    # ── Generic fallback for any fields not yet filled ─────────────────────
    already_filled_labels = {f.split(':')[0].lower().replace(' ', '_') for f in filled}
    for labels, value in field_map:
        if labels[0].lower().replace(' ', '_') not in already_filled_labels:
            _try_fill(page, labels, value, filled)
            _random_delay(0.03, 0.08)

    for labels, value in radio_map:
        _try_fill_radio(page, labels, value, filled)
        _random_delay(0.03, 0.07)

    for labels, value in textarea_map:
        if 'cover_letter' not in already_filled_labels and '(text_filled)' not in str(filled):
            _try_fill_textarea(page, labels, value, filled)
            _random_delay(0.05, 0.1)

    # ── Common select/dropdown handling ───────────────────────────────────
    _fill_common_selects(page, profile, filled)

    # ── Checkbox handling ──────────────────────────────────────────────────
    _fill_checkboxes(page, profile, filled)

    # ── Resume upload ──────────────────────────────────────────────────────
    if resume_path and os.path.exists(resume_path):
        if not any('resume' in f.lower() for f in filled):
            _set_message(entry, 'Uploading resume...')
            _upload_resume(page, resume_path, filled)


def fill_application_async(app_id: str, job_url: str, profile: dict, resume_path: str):
    """
    Runs in a background thread.
    Opens browser, fills form, waits for user confirmation via
    PENDING_APPLICATIONS[app_id]['event'].
    Sets completion_event when the job reaches a terminal state.
    """
    entry = PENDING_APPLICATIONS[app_id]
    filled = []

    name_parts = (profile.get('name') or '').split(' ', 1)
    first_name = name_parts[0] if name_parts else ''
    last_name  = name_parts[1] if len(name_parts) > 1 else ''

    # ── Text field map ────────────────────────────────────────────────────────
    field_map = [
        (['first name', 'firstname', 'given name'],        first_name),
        (['last name',  'lastname',  'surname', 'family name'], last_name),
        (['full name',  'your name', '^name$', 'name'],    profile.get('name', '')),
        (['email', 'e-mail', 'email address'],              profile.get('email', '')),
        (['phone', 'mobile', 'telephone', 'cell'],          profile.get('phone', '')),
        (['linkedin', 'linkedin url', 'linkedin profile'],  profile.get('linkedin', '')),
        (['website', 'portfolio', 'personal site'],           profile.get('website', '')),
        (['github', 'github url', 'github profile', 'github link'], profile.get('github', '')),
        (['city', 'location', 'current city'],              profile.get('city', '')),
        (['salary', 'desired salary', 'expected salary', 'expected compensation',
          'desired compensation', 'salary expectation', 'pay expectation'],
         profile.get('salary_expectation', '')),
        (['start date', 'available start', 'earliest start',
          'when can you start', 'date available', 'availability'],
         profile.get('start_date', '')),
        (['how did you hear', 'referral source', 'where did you learn',
          'how did you find', 'referred by', 'source'],
         profile.get('referral', '')),
    ]

    # ── Radio / Yes-No field map ──────────────────────────────────────────────
    radio_map = [
        (['authorized to work', 'eligible to work', 'work authorization',
          'legally authorized', 'permitted to work', 'right to work'],
         profile.get('work_auth', '')),
        (['sponsorship', 'visa sponsorship', 'require sponsorship',
          'will you require', 'immigration sponsorship', 'work visa'],
         profile.get('sponsorship', '')),
    ]

    # ── Textarea map ─────────────────────────────────────────────────────────
    textarea_map = [
        (['cover letter', 'why do you want', 'why are you interested',
          'tell us about yourself', 'additional information', 'message',
          'anything else'],
         profile.get('cover_letter', '')),
    ]

    ats = _detect_ats(job_url)
    _log.info('fill_application_async starting app_id=%s url=%s ats=%s', app_id, job_url, ats)
    entry['ats'] = ats
    browser = None
    context = None
    page = None
    _headless = entry.get('headless', False)

    @contextlib.contextmanager
    def _mk_browser():
        if _headless:
            _pw = sync_playwright().start()
            _b, _c = _stealth_context(_pw, headless=True)
            try:
                yield _b, _c
            finally:
                try: _c.close()
                except Exception: pass
                try: _b.close()
                except Exception: pass
                try: _pw.stop()
                except Exception: pass
        else:
            # Connect to the user's existing Chrome via CDP so fills open as
            # new tabs in the same window rather than launching a new browser.
            _cdp_url = _ensure_chrome_with_debug()
            _pw = sync_playwright().start()
            if _cdp_url:
                _b = _pw.chromium.connect_over_cdp(_cdp_url)
                # Use the default (first) context so cookies/sessions are preserved
                _c = _b.contexts[0] if _b.contexts else _b.new_context()
            else:
                # Fallback: launch a fresh persistent context
                _log.warning('CDP unavailable — falling back to launch_persistent_context')
                _b = None
                _clear_profile_locks()
                launch_args = [
                    '--disable-blink-features=AutomationControlled',
                    '--disable-infobars',
                    '--no-first-run',
                    '--disable-default-apps',
                    '--start-maximized',
                ]
                _c = _pw.chromium.launch_persistent_context(
                    BROWSER_PROFILE_DIR,
                    headless=False,
                    args=launch_args,
                    user_agent=(
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/122.0.0.0 Safari/537.36'
                    ),
                    locale='en-US',
                    timezone_id='America/New_York',
                    no_viewport=True,
                )
            try:
                yield _b, _c
            finally:
                # Disconnect from Chrome but leave it open for the next fill
                try:
                    if _cdp_url and _b:
                        _b.disconnect()
                except Exception:
                    pass
                try:
                    _pw.stop()
                except Exception:
                    pass

    try:
        with _mk_browser() as (browser, context):
            _set_message(entry, 'Launching Chrome browser...')
            _set_message(entry, 'Browser launched. Opening job page...')
            page = context.new_page()

            entry['status'] = 'navigating'
            _set_message(entry, f'Navigating to job page ({ats.upper()} detected)...')

            nav_url = job_url
            if ats == 'greenhouse':
                base_url = job_url.split('?')[0].rstrip('/')
                if 'job-boards.greenhouse.io' in job_url:
                    # New Greenhouse React boards — form is already on the page, no /apply needed
                    nav_url = base_url
                elif 'boards.greenhouse.io' in job_url or 'greenhouse.io' in job_url:
                    if not base_url.endswith('/apply'):
                        nav_url = base_url + '/apply'
                _log.info('Greenhouse nav URL: %s', nav_url)
            elif ats == 'lever' and 'jobs.lever.co' in job_url:
                base_url = job_url.split('?')[0].rstrip('/')
                if not base_url.endswith('/apply'):
                    nav_url = base_url + '/apply'
                    _log.info('Lever direct apply URL: %s', nav_url)

            page.goto(nav_url, wait_until='domcontentloaded', timeout=30000)
            if not entry.get('headless'):
                page.bring_to_front()
            # Give React/SPA forms extra time to hydrate
            if ats == 'greenhouse':
                _set_message(entry, 'Waiting for Greenhouse form to load...')
                _random_delay(2.0, 3.0)
                # Wait for the basic form input to appear
                try:
                    page.wait_for_selector(
                        '#first_name, input[name="job_application[first_name]"], '
                        'input[id*="first"], input[type="text"]:visible',
                        timeout=10000,
                    )
                except Exception:
                    pass
                # Extra wait for custom question fields (async hydration)
                try:
                    page.wait_for_selector('[id^="question_"], textarea:visible', timeout=6000)
                except Exception:
                    pass
                _random_delay(0.5, 1.0)
            elif ats in ('lever', 'ashby', 'workday'):
                _random_delay(1.5, 2.5)
                try:
                    page.wait_for_selector(
                        'input[type="text"]:visible, textarea:visible',
                        timeout=8000,
                    )
                except Exception:
                    pass
            else:
                _random_delay(0.5, 1.0)

            # Detect common error pages
            try:
                page_text = page.evaluate('() => document.body.innerText') or ''
                error_phrases = [
                    "sorry, but we can't find that page",
                    "this job is no longer available",
                    "job has been closed",
                    "posting is no longer active",
                    "position has been filled",
                    "404",
                ]
                if any(p in page_text.lower() for p in error_phrases):
                    raise RuntimeError(
                        f'Job posting appears to be closed or expired. '
                        f'URL: {nav_url}'
                    )
            except RuntimeError:
                raise
            except Exception:
                pass

            form_found = _wait_for_form(page, ats, timeout_ms=5000)

            if not form_found:
                clicked = _click_apply_button(page, entry)
                if not entry.get('headless'):
                    page.bring_to_front()
                if clicked:
                    _set_message(entry, 'Waiting for application form to load...')
                    form_found = _wait_for_form(page, ats, timeout_ms=12000)
                    if form_found:
                        _set_message(entry, 'Application form detected. Starting to fill fields...')
                    else:
                        _set_message(entry, 'Form not auto-detected — attempting to fill visible fields anyway...')
                else:
                    _set_message(entry, 'Attempting to fill any visible form fields...')
            else:
                _set_message(entry, 'Application form found. Filling fields...')

            # ── Login-wall detection ───────────────────────────────────────────
            if entry.get('headless') and _detect_login_required(page):
                # Headless / Quick Apply mode: try stored credentials
                creds = _load_credentials()
                domain = _get_root_domain(job_url)
                cred = creds.get(domain)
                if cred:
                    success = _auto_login(page, cred['email'], cred['password'], entry)
                    if success:
                        _set_message(entry, f'Logged in to {domain} — resuming…')
                        form_found = _wait_for_form(page, ats, timeout_ms=5000)
                        if not form_found:
                            _click_apply_button(page, entry)
                            _wait_for_form(page, ats, timeout_ms=10000)
                    else:
                        entry['status'] = 'error'
                        entry['error'] = (
                            f'Login failed for {domain}. '
                            f'Check your credentials in Settings → Job Board Accounts.'
                        )
                        _set_message(entry, entry['error'])
                        return
                else:
                    entry['status'] = 'error'
                    entry['error'] = (
                        f'Login required for {domain} but no credentials saved. '
                        f'Add your login in Settings → Job Board Accounts, then retry.'
                    )
                    _set_message(entry, entry['error'])
                    return

            if not entry.get('headless') and _detect_login_required(page):
                _set_message(entry,
                    'This site requires you to log in before applying. '
                    'Please log in in the browser window, then click '
                    '"I\'ve logged in \u2014 Continue" below.')
                entry['status'] = 'awaiting_login'
                signalled = entry['login_event'].wait(timeout=600)
                if not signalled:
                    entry['status'] = 'cancelled'
                    entry['error'] = 'Login timed out after 10 minutes — no action taken.'
                    _set_message(entry, entry['error'])
                    return
                if entry.get('login_cancelled'):
                    entry['status'] = 'cancelled'
                    _set_message(entry, 'Cancelled by user during login.')
                    return
                entry['status'] = 'navigating'
                _set_message(entry, 'Login confirmed \u2014 resuming application...')
                _random_delay(1.0, 2.0)
                # Re-check for form after login (page may have redirected)
                form_found = _wait_for_form(page, ats, timeout_ms=5000)
                if not form_found:
                    _click_apply_button(page, entry)
                    _wait_for_form(page, ats, timeout_ms=10000)

            entry['status'] = 'filling'

            # ── First fill pass ───────────────────────────────────────────
            if ats == 'greenhouse':
                _set_message(entry, 'Filling Greenhouse application form...')
            elif ats == 'lever':
                _set_message(entry, 'Filling Lever application form...')
            elif ats == 'workday':
                _set_message(entry, 'Filling Workday application form...')
            elif ats == 'ashby':
                _set_message(entry, 'Filling Ashby application form...')
            elif ats == 'smartrecruiters':
                _set_message(entry, 'Filling SmartRecruiters application form...')
            elif ats == 'icims':
                _set_message(entry, 'Filling iCIMS application form...')
            else:
                _set_message(entry, 'Filling application form fields...')

            _run_fill_pass(page, ats, profile, first_name, last_name,
                           field_map, radio_map, textarea_map, resume_path, filled, entry)

            # ── AI-powered custom question answering ──────────────────────
            if os.environ.get('GEMINI_API_KEY'):
                _set_message(entry, 'Scanning for custom questions to answer with AI...')
                _fill_custom_questions(page, profile, filled, entry)

            # ── Multi-page form: advance up to 3 more pages ───────────────
            for _page_num in range(3):
                advanced = _try_advance_page(page)
                if not advanced:
                    break
                _set_message(entry, f'Advancing to next form page (pass {_page_num + 2})...')
                _run_fill_pass(page, ats, profile, first_name, last_name,
                               field_map, radio_map, textarea_map, resume_path, filled, entry)
                if os.environ.get('GEMINI_API_KEY'):
                    _fill_custom_questions(page, profile, filled, entry)

            entry['filled_fields'] = filled
            count = len(filled)

            # ── Detect unfilled fields ────────────────────────────────────
            unfilled_fields = _detect_unfilled_fields(page)
            entry['unfilled_fields'] = unfilled_fields

            if entry.get('headless'):
                # ── Headless / Quick Apply mode: auto-submit without user review ──
                if count:
                    _set_message(entry,
                        f'Filled {count} field(s). Auto-submitting...')
                else:
                    _set_message(entry, 'Could not fill fields. Attempting submit anyway...')

                submit_btn = _find_submit_button(page)
                if submit_btn:
                    _random_delay(0.3, 0.8)
                    submit_btn.click()
                    _random_delay(1, 2)
                    entry['status'] = 'submitted'
                    _set_message(entry, 'Application submitted automatically.')
                else:
                    entry['status'] = 'error'
                    entry['error'] = 'Submit button not found — could not auto-submit.'
                    _set_message(entry, entry['error'])
            else:
                # ── Visible browser mode: wait for user confirmation ──
                filled_summary = ', '.join(f.split(':')[0] for f in filled[:5])

                if unfilled_fields:
                    required_unfilled = [f for f in unfilled_fields if f.get('required')]
                    optional_unfilled = [f for f in unfilled_fields if not f.get('required')]
                    attention_parts = []
                    if required_unfilled:
                        req_names = ', '.join(f['label'][:30] for f in required_unfilled[:3])
                        attention_parts.append(f'{len(required_unfilled)} required: {req_names}')
                    if optional_unfilled:
                        attention_parts.append(f'{len(optional_unfilled)} optional')
                    attention_str = '; '.join(attention_parts)
                    unfilled_msg = f' | ⚠️ {len(unfilled_fields)} fields need attention: {attention_str}'
                else:
                    unfilled_msg = ''

                if count:
                    _set_message(entry,
                        f'Filled {count} field(s): {filled_summary}.{unfilled_msg} '
                        f'Review the browser window, then confirm below.')
                else:
                    _set_message(entry,
                        f'Could not auto-fill any fields (form may need manual interaction).{unfilled_msg} '
                        f'Please fill the form in the browser window, then confirm below.')

                entry['status'] = 'awaiting_confirmation'

                # Wait for user to confirm or cancel (15 minute timeout)
                signalled = entry['event'].wait(timeout=900)

                if not signalled:
                    entry['status'] = 'cancelled'
                    entry['error'] = 'Application timed out — no confirmation received after 15 minutes.'
                    _set_message(entry, entry['error'])
                    # Fall through to cleanup
                elif entry.get('confirmed'):
                    _set_message(entry, 'Submitting application...')
                    submit_btn = _find_submit_button(page)
                    if submit_btn:
                        _random_delay(0.3, 0.8)
                        submit_btn.click()
                        _random_delay(1, 2)
                        entry['status'] = 'submitted'
                        _set_message(entry, 'Application submitted successfully!')
                    else:
                        entry['status'] = 'error'
                        entry['error'] = 'Could not find submit button — please submit manually in the browser.'
                        _set_message(entry, entry['error'])
                else:
                    entry['status'] = 'cancelled'
                    _set_message(entry, 'Application cancelled by user.')

            _random_delay(0.5, 1.0)

    except Exception as e:
        _log.error('fill_application_async FAILED app_id=%s: %s', app_id, e, exc_info=True)
        entry['status'] = 'error'
        entry['error'] = str(e)
        _set_message(entry, f'Error: {e}')
    finally:
        _log.info('fill_application_async done app_id=%s status=%s', app_id, entry.get('status'))
        entry['completion_event'].set()
        on_complete = entry.get('on_complete')
        if on_complete:
            try:
                on_complete(entry)
            except Exception as _cb_err:
                _log.error('on_complete callback failed app_id=%s: %s', app_id, _cb_err)


def start_fill(app_id: str, job_url: str, profile: dict, resume_path: str,
               headless: bool = False, on_complete=None) -> threading.Thread:
    """Register a new pending application and start the background thread.

    headless=True  — invisible browser, auto-submits after filling (Quick Apply mode).
    on_complete    — optional callable(entry) invoked after the thread finishes.
    """
    event            = threading.Event()
    completion_event = threading.Event()
    login_event      = threading.Event()
    PENDING_APPLICATIONS[app_id] = {
        'status':           'starting',
        'message':          'Initializing...',
        'event':            event,
        'completion_event': completion_event,
        'login_event':      login_event,
        'confirmed':        False,
        'login_cancelled':  False,
        'filled_fields':    [],
        'unfilled_fields':  [],
        'error':            '',
        'ats':              '',
        'job_url':          job_url,
        'headless':         headless,
        'on_complete':      on_complete,
    }
    t = threading.Thread(
        target=fill_application_async,
        args=(app_id, job_url, profile, resume_path),
        daemon=True,
    )
    t.start()
    return t
