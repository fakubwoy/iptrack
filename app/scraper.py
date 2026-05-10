"""
Scraper for IP India status portals.

Strategy (open-source first):
  1. requests + BeautifulSoup  (no external dependency, free)
  2. Playwright headless Chromium fallback (installed at build time on Railway)
  3. Optional anticaptcha.com solver for the TM CAPTCHA
"""

import logging
import json
import re
import os
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
})

TM_STATUS_URL     = "https://tmrsearch.ipindia.gov.in/eregister/eregister.aspx"
DESIGN_STATUS_URL = "https://search.ipindia.gov.in/DesignApplicationStatus/"


# ── Design (no CAPTCHA) ──────────────────────────────────────────────────────

def _scrape_design_requests(app_no: str) -> dict:
    result = {"application_number": app_no, "status": None, "raw": ""}
    # Normalise: format must be NNNNNN-001
    if not re.search(r"-\d{3}$", app_no):
        app_no = app_no + "-001"
    result["normalized_number"] = app_no

    try:
        resp = _session.get(DESIGN_STATUS_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        vs = (soup.find("input", {"id": "__VIEWSTATE"}) or {}).get("value", "")
        ev = (soup.find("input", {"id": "__EVENTVALIDATION"}) or {}).get("value", "")

        payload = {
            "__VIEWSTATE": vs,
            "__EVENTVALIDATION": ev,
            "ctl00$cphBody$txtApplicationNumber": app_no,
            "ctl00$cphBody$btnShowStatus": "Show Status",
        }
        resp2 = _session.post(DESIGN_STATUS_URL, data=payload, timeout=20)
        resp2.raise_for_status()
        result["raw"] = resp2.text[:3000]
        return _parse_design_html(resp2.text, result)
    except Exception as exc:
        logger.warning(f"Design requests failed for {app_no}: {exc}")
        result["error"] = str(exc)
        return result


def _parse_design_html(html: str, result: dict) -> dict:
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


# ── Trademark ────────────────────────────────────────────────────────────────

def _scrape_tm_requests(app_no: str) -> dict:
    result = {"application_number": app_no, "status": None, "raw": ""}
    try:
        resp = _session.get(TM_STATUS_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        vs = (soup.find("input", {"id": "__VIEWSTATE"}) or {}).get("value", "")
        ev = (soup.find("input", {"id": "__EVENTVALIDATION"}) or {}).get("value", "")

        payload = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": vs,
            "__EVENTVALIDATION": ev,
            "ctl00$ContentPlaceHolder1$hdnType": "T",
            "ctl00$ContentPlaceHolder1$txtTMNo": app_no.strip(),
            "ctl00$ContentPlaceHolder1$btnShow": "Show Status",
        }
        resp2 = _session.post(TM_STATUS_URL, data=payload, timeout=20)
        resp2.raise_for_status()
        result["raw"] = resp2.text[:3000]
        return _parse_tm_html(resp2.text, result)
    except Exception as exc:
        logger.warning(f"TM requests failed for {app_no}: {exc}")
        result["error"] = str(exc)
        return result


def _parse_tm_html(html: str, result: dict) -> dict:
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


# ── Playwright fallback ──────────────────────────────────────────────────────

def _scrape_with_playwright(filing_type: str, app_no: str) -> dict:
    result = {"application_number": app_no, "status": None, "raw": ""}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["error"] = "Playwright not installed"
        result["status"] = "Could not retrieve status – scraper dependency missing"
        return result

    with sync_playwright() as p:
        browser = None
        try:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"]
            )
            page = browser.new_page()
            page.set_extra_http_headers({"Accept-Language": "en-IN,en;q=0.9"})

            if filing_type == "design":
                norm = app_no if re.search(r"-\d{3}$", app_no) else app_no + "-001"
                page.goto(DESIGN_STATUS_URL, wait_until="networkidle", timeout=30000)
                field = page.locator("input[id*='txtApplicationNumber']")
                field.fill(norm)
                page.locator("input[id*='btnShowStatus']").click()
                page.wait_for_load_state("networkidle", timeout=20000)
                html = page.content()
                result["raw"] = html[:3000]
                result = _parse_design_html(html, result)

            elif filing_type == "trademark":
                anticaptcha_key = os.environ.get("ANTICAPTCHA_KEY", "")
                page.goto(TM_STATUS_URL, wait_until="networkidle", timeout=30000)
                page.locator("input[id*='txtTMNo']").fill(app_no.strip())

                if anticaptcha_key:
                    result = _solve_and_submit_tm(page, anticaptcha_key, app_no, result)
                else:
                    # Try submitting without solving – sometimes works on first load
                    page.locator("input[id*='btnShow']").click()
                    page.wait_for_load_state("networkidle", timeout=15000)
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
            result["error"] = str(exc)
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
            page.locator("input[id*='btnShow']").click()
            page.wait_for_load_state("networkidle", timeout=20000)
            html = page.content()
            result["raw"] = html[:3000]
            result = _parse_tm_html(html, result)
        else:
            result["status"] = "CAPTCHA solve failed – try again"
    except Exception as exc:
        result["error"] = f"anticaptcha: {exc}"
    return result


# ── Public API ───────────────────────────────────────────────────────────────

def check_status(filing_type: str, application_number: str) -> dict:
    filing_type = filing_type.lower()

    if filing_type == "trademark":
        result = _scrape_tm_requests(application_number)
        if not result.get("status") or result.get("error"):
            logger.info(f"Falling back to Playwright for TM {application_number}")
            result = _scrape_with_playwright("trademark", application_number)

    elif filing_type == "design":
        result = _scrape_design_requests(application_number)
        if not result.get("status") or result.get("error") == "CAPTCHA_REQUIRED":
            logger.info(f"Falling back to Playwright for design {application_number}")
            result = _scrape_with_playwright("design", application_number)

    else:
        result = {"error": f"Unknown filing type: {filing_type}", "status": None}

    if result.get("status"):
        result["status"] = result["status"].strip()

    logger.info(f"check_status [{filing_type}] {application_number} → {result.get('status') or result.get('error')}")
    return result