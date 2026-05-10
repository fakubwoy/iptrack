"""
Scraper for IP India status portals.

Strategy (open-source first):
  1. requests + BeautifulSoup  (no external dependency, free)
  2. Playwright headless Chromium fallback (installed at build time on Railway)
  3. Optional anticaptcha.com solver for the TM CAPTCHA

DEBUG: set env var SCRAPER_DEBUG=1 to dump raw HTML to logs.
"""

import logging
import re
import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)
DEBUG = os.environ.get("SCRAPER_DEBUG", "0") == "1"

# ── Shared session ────────────────────────────────────────────────────────────
_session = requests.Session()
_session.verify = False  # IP India portals have frequent SSL chain issues
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://ipindia.gov.in/",
})
_retry = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
_session.mount("https://", HTTPAdapter(max_retries=_retry))
_session.mount("http://",  HTTPAdapter(max_retries=_retry))

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

TM_STATUS_URL     = "https://tmrsearch.ipindia.gov.in/eregister/eregister.aspx"
DESIGN_STATUS_URL = "https://search.ipindia.gov.in/DesignApplicationStatus/"


# ── Diagnostics ───────────────────────────────────────────────────────────────

def _diagnose_html(label, html, status_code, final_url):
    soup = BeautifulSoup(html, "lxml")
    inputs = [
        {k: v for k, v in inp.attrs.items() if k in ("id", "name", "type")}
        for inp in soup.find_all("input")
    ]
    iframes = [fr.get("src", "") for fr in soup.find_all(["iframe", "frame"])]
    title = soup.find("title")
    title_text = title.get_text(strip=True) if title else ""
    logger.info(
        f"[DIAG {label}] HTTP {status_code} | URL: {final_url} | "
        f"title: {title_text!r} | inputs: {inputs} | iframes: {iframes}"
    )
    if DEBUG:
        logger.debug(f"[DIAG {label}] RAW HTML:\n{html[:5000]}")


def _detect_block(html, status_code):
    low = html.lower()
    if status_code == 403:
        return "Portal blocked this server (HTTP 403) – check manually at ipindia.gov.in"
    if status_code == 503:
        return "Portal unavailable (HTTP 503) – try again later"
    if "not in allowlist" in low or "access denied" in low:
        return "Portal blocked this server – check manually at ipindia.gov.in"
    if "maintenance" in low and ("under" in low or "scheduled" in low):
        return "Portal under maintenance – try again later"
    if "cloudflare" in low and "ray id" in low:
        return "Portal protected by Cloudflare – check manually at ipindia.gov.in"
    return None


# ── Design (no CAPTCHA) ───────────────────────────────────────────────────────

def _scrape_design_requests(app_no):
    result = {"application_number": app_no, "status": None, "raw": ""}
    if not re.search(r"-\d{3}$", app_no):
        app_no = app_no + "-001"
    result["normalized_number"] = app_no

    try:
        resp = _session.get(DESIGN_STATUS_URL, timeout=20)
        resp.raise_for_status()

        block = _detect_block(resp.text, resp.status_code)
        if block:
            result["status"] = block
            return result

        _diagnose_html("DESIGN-GET", resp.text, resp.status_code, resp.url)
        soup = BeautifulSoup(resp.text, "lxml")

        vs  = (soup.find("input", {"id": "__VIEWSTATE"}) or {}).get("value", "")
        ev  = (soup.find("input", {"id": "__EVENTVALIDATION"}) or {}).get("value", "")
        vsg = (soup.find("input", {"id": "__VIEWSTATEGENERATOR"}) or {}).get("value", "")

        # Discover field names dynamically instead of hardcoding
        submit_btn = soup.find("input", {"type": "submit"}) or {}
        submit_name  = submit_btn.get("name", "ctl00$cphBody$btnShowStatus")
        submit_value = submit_btn.get("value", "Show Status")

        text_inputs = [
            inp for inp in soup.find_all("input", {"type": "text"})
            if inp.get("name") and
            any(kw in (inp.get("id","") + inp.get("name","")).lower()
                for kw in ["application", "appno", "regno"])
        ]
        if not text_inputs:
            text_inputs = soup.find_all("input", {"type": "text"})
        app_field_name = (
            text_inputs[0].get("name", "ctl00$cphBody$txtApplicationNumber")
            if text_inputs else "ctl00$cphBody$txtApplicationNumber"
        )

        payload = {
            "__VIEWSTATE": vs,
            "__VIEWSTATEGENERATOR": vsg,
            "__EVENTVALIDATION": ev,
            app_field_name: app_no,
            submit_name: submit_value,
        }
        logger.debug(f"Design POST payload keys: {list(payload.keys())}")

        resp2 = _session.post(DESIGN_STATUS_URL, data=payload, timeout=25)
        resp2.raise_for_status()
        _diagnose_html("DESIGN-POST", resp2.text, resp2.status_code, resp2.url)
        result["raw"] = resp2.text[:3000]
        return _parse_design_html(resp2.text, result)

    except Exception as exc:
        logger.warning(f"Design requests failed for {app_no}: {exc}")
        result["error"] = str(exc)
        return result


def _parse_design_html(html, result):
    soup = BeautifulSoup(html, "lxml")
    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)
            if "status" in label:
                result["status"] = value
            elif "applicant" in label:
                result["applicant"] = value
            elif "date" in label and "filing" in label:
                result["filed_date"] = value

    if not result.get("status"):
        text = soup.get_text(" ", strip=True)
        m = re.search(r"Status[:\s]+([A-Za-z][^\n\r]{2,80})", text, re.IGNORECASE)
        if m:
            result["status"] = m.group(1).strip()

    if "record not found" in html.lower():
        result["status"] = "Record Not Found"
    if "captcha" in html.lower() and not result.get("status"):
        result["error"] = "CAPTCHA_REQUIRED"

    return result


# ── Trademark ─────────────────────────────────────────────────────────────────

def _scrape_tm_requests(app_no):
    result = {"application_number": app_no, "status": None, "raw": ""}
    try:
        resp = _session.get(TM_STATUS_URL, timeout=20)
        resp.raise_for_status()

        block = _detect_block(resp.text, resp.status_code)
        if block:
            result["status"] = block
            return result

        _diagnose_html("TM-GET", resp.text, resp.status_code, resp.url)
        soup = BeautifulSoup(resp.text, "lxml")

        vs  = (soup.find("input", {"id": "__VIEWSTATE"}) or {}).get("value", "")
        ev  = (soup.find("input", {"id": "__EVENTVALIDATION"}) or {}).get("value", "")
        vsg = (soup.find("input", {"id": "__VIEWSTATEGENERATOR"}) or {}).get("value", "")

        tm_input = (
            soup.find("input", {"id": re.compile(r"txtTMNo", re.I)}) or
            soup.find("input", {"name": re.compile(r"txtTMNo", re.I)}) or
            soup.find("input", {"type": "text"})
        )
        tm_field_name = (
            tm_input.get("name", "ctl00$ContentPlaceHolder1$txtTMNo")
            if tm_input else "ctl00$ContentPlaceHolder1$txtTMNo"
        )

        submit_btn = (
            soup.find("input", {"id": re.compile(r"btnShow", re.I)}) or
            soup.find("input", {"type": "submit"}) or
            {}
        )
        submit_name  = submit_btn.get("name", "ctl00$ContentPlaceHolder1$btnShow")
        submit_value = submit_btn.get("value", "Show Status")

        payload = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": vs,
            "__VIEWSTATEGENERATOR": vsg,
            "__EVENTVALIDATION": ev,
            "ctl00$ContentPlaceHolder1$hdnType": "T",
            tm_field_name: app_no.strip(),
            submit_name: submit_value,
        }
        logger.debug(f"TM POST payload keys: {list(payload.keys())}")

        resp2 = _session.post(TM_STATUS_URL, data=payload, timeout=25)
        resp2.raise_for_status()
        _diagnose_html("TM-POST", resp2.text, resp2.status_code, resp2.url)
        result["raw"] = resp2.text[:3000]
        return _parse_tm_html(resp2.text, result)

    except Exception as exc:
        logger.warning(f"TM requests failed for {app_no}: {exc}")
        result["error"] = str(exc)
        return result


def _parse_tm_html(html, result):
    soup = BeautifulSoup(html, "lxml")
    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) >= 2:
            label = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)
            if "status" in label and "trade" not in label:
                result["status"] = value
            elif "applicant" in label:
                result["applicant"] = value
            elif "trade mark" in label or "mark" in label:
                result["mark"] = value
            elif "class" in label:
                result["class"] = value
            elif "date of filing" in label or "filed" in label:
                result["filed_date"] = value

    if not result.get("status"):
        text = soup.get_text(" ", strip=True)
        m = re.search(r"Status[:\s]+([A-Za-z][^\n\r]{2,60})", text, re.IGNORECASE)
        if m:
            result["status"] = m.group(1).strip()

    if "captcha" in html.lower() and not result.get("status"):
        result["error"] = "CAPTCHA_REQUIRED"

    return result


# ── Playwright fallback ───────────────────────────────────────────────────────

def _log_pw_inputs(page, label):
    try:
        inputs = page.evaluate("""() =>
            Array.from(document.querySelectorAll('input,select,textarea')).map(el => ({
                tag: el.tagName, id: el.id, name: el.name, type: el.type || ''
            }))
        """)
        logger.info(f"[PW {label}] URL={page.url}  inputs={inputs}")
    except Exception as e:
        logger.info(f"[PW {label}] Could not enumerate inputs: {e}")


def _pw_fill_dynamic(page, filing_type, value):
    """Fill the application number input, discovering the selector via JS."""
    if filing_type == "trademark":
        keywords = ["tmno", "tm_no", "trademarknumber", "applicationno", "appno"]
    else:
        keywords = ["applicationnumber", "appno", "app_no", "registrationno"]

    try:
        selector = page.evaluate(f"""() => {{
            const kws = {keywords};
            for (const el of document.querySelectorAll('input[type="text"], input:not([type])')) {{
                const combined = (el.id + el.name).toLowerCase();
                if (kws.some(k => combined.includes(k))) {{
                    return el.id ? '#' + el.id : '[name="' + el.name + '"]';
                }}
            }}
            for (const el of document.querySelectorAll('input[type="text"]')) {{
                if (el.offsetParent !== null) {{
                    return el.id ? '#' + el.id : '[name="' + el.name + '"]';
                }}
            }}
            return null;
        }}""")
        if not selector:
            return False
        logger.info(f"[PW {filing_type}] fill selector: {selector!r}")
        page.fill(selector, value, timeout=10000)
        return True
    except Exception as e:
        logger.warning(f"[PW {filing_type}] fill failed: {e}")
        return False


def _pw_click_submit_dynamic(page, label):
    try:
        selector = page.evaluate("""() => {
            for (const el of document.querySelectorAll('input[type="submit"],button[type="submit"],button')) {
                const sig = ((el.id || '') + (el.value || '') + (el.textContent || '')).toLowerCase();
                if (sig.includes('show') || sig.includes('submit') || sig.includes('check')) {
                    if (el.id) return '#' + el.id;
                    if (el.name) return '[name="' + el.name + '"]';
                }
            }
            const s = document.querySelector('input[type="submit"],button[type="submit"]');
            return s ? (s.id ? '#' + s.id : (s.name ? '[name="' + s.name + '"]' : null)) : null;
        }""")
        if selector:
            logger.info(f"[PW {label}] click selector: {selector!r}")
            page.click(selector, timeout=10000)
        else:
            logger.warning(f"[PW {label}] no submit button found")
    except Exception as e:
        logger.warning(f"[PW {label}] click failed: {e}")


def _pw_blocked_status(page, label):
    html = page.content()
    low = html.lower()
    if "403" in html[:300] or "access denied" in low or "not in allowlist" in low:
        return "Portal blocked this server – check manually at ipindia.gov.in"
    if "maintenance" in low:
        return "Portal under maintenance – try again later"
    if "cloudflare" in low and "ray id" in low:
        return "Portal protected by Cloudflare – check manually at ipindia.gov.in"
    logger.warning(f"[PW {label}] form input not found. Page snippet: {html[:800]}")
    return "Could not reach portal form – check manually at ipindia.gov.in"


def _scrape_with_playwright(filing_type, app_no):
    result = {"application_number": app_no, "status": None, "raw": ""}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["status"] = "Could not retrieve status – Playwright not installed"
        return result

    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu",
                      "--ignore-certificate-errors"],
            )
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-IN,en;q=0.9"})

            if filing_type == "design":
                norm = app_no if re.search(r"-\d{3}$", app_no) else app_no + "-001"
                page.goto(DESIGN_STATUS_URL, wait_until="domcontentloaded", timeout=30000)
                _log_pw_inputs(page, "DESIGN")
                block = _detect_block(page.content(), 200)
                if block:
                    result["status"] = block
                    return result
                if not _pw_fill_dynamic(page, "design", norm):
                    result["status"] = _pw_blocked_status(page, "DESIGN")
                    return result
                _pw_click_submit_dynamic(page, "design")
                page.wait_for_load_state("domcontentloaded", timeout=20000)
                html = page.content()
                result["raw"] = html[:3000]
                result = _parse_design_html(html, result)

            elif filing_type == "trademark":
                page.goto(TM_STATUS_URL, wait_until="domcontentloaded", timeout=35000)
                _log_pw_inputs(page, "TM")
                block = _detect_block(page.content(), 200)
                if block:
                    result["status"] = block
                    return result
                if not _pw_fill_dynamic(page, "trademark", app_no.strip()):
                    result["status"] = _pw_blocked_status(page, "TM")
                    return result
                anticaptcha_key = os.environ.get("ANTICAPTCHA_KEY", "")
                if anticaptcha_key:
                    result = _solve_and_submit_tm(page, anticaptcha_key, app_no, result)
                else:
                    _pw_click_submit_dynamic(page, "trademark")
                    page.wait_for_load_state("domcontentloaded", timeout=20000)
                    html = page.content()
                    result["raw"] = html[:3000]
                    result = _parse_tm_html(html, result)
                    if not result.get("status") or result.get("error") == "CAPTCHA_REQUIRED":
                        result["status"] = (
                            "CAPTCHA blocked – set ANTICAPTCHA_KEY env var "
                            "or check manually at tmrsearch.ipindia.gov.in"
                        )
                        result.pop("error", None)

        except Exception as exc:
            logger.warning(f"Playwright error for {filing_type}/{app_no}: {exc}")
            if not result.get("status"):
                result["status"] = f"Scrape failed – {exc}"
        finally:
            if browser:
                browser.close()

    return result


def _solve_and_submit_tm(page, anticaptcha_key, app_no, result):
    try:
        from anticaptchaofficial.imagecaptcha import imagecaptcha  # type: ignore
        import base64
        captcha_img = page.locator("img[id*='captcha'], img[class*='captcha']").first
        b64 = base64.b64encode(captcha_img.screenshot()).decode()
        solver = imagecaptcha()
        solver.set_key(anticaptcha_key)
        solver.set_verbose(0)
        captcha_text = solver.solve_and_return_solution(None, body=b64)
        if captcha_text:
            page.fill("input[id*='captcha'], input[name*='captcha']", captcha_text)
            _pw_click_submit_dynamic(page, "tm-captcha")
            page.wait_for_load_state("domcontentloaded", timeout=20000)
            html = page.content()
            result["raw"] = html[:3000]
            result = _parse_tm_html(html, result)
        else:
            result["status"] = "CAPTCHA solve failed – try again"
    except Exception as exc:
        result["status"] = "CAPTCHA solve error – try again later"
        result["error"] = f"anticaptcha: {exc}"
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def check_status(filing_type, application_number):
    filing_type = filing_type.lower()

    if filing_type == "trademark":
        result = _scrape_tm_requests(application_number)
        # Only Playwright-fallback if requests genuinely errored — not if we got a block message
        if not result.get("status") and result.get("error"):
            logger.info(f"Falling back to Playwright for TM {application_number}")
            result = _scrape_with_playwright("trademark", application_number)

    elif filing_type == "design":
        result = _scrape_design_requests(application_number)
        if not result.get("status") and result.get("error"):
            logger.info(f"Falling back to Playwright for design {application_number}")
            result = _scrape_with_playwright("design", application_number)

    else:
        result = {"error": f"Unknown filing type: {filing_type}", "status": None}

    if result.get("status"):
        result["status"] = result["status"].strip()

    logger.info(
        f"check_status [{filing_type}] {application_number} → "
        f"{result.get('status') or result.get('error')}"
    )
    return result