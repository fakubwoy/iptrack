"""
Scraper for IP India status portals.

Strategy (open-source first, no paid API needed):
  1. Trademark  → POST to tmrsearch.ipindia.gov.in JSON endpoint
     (The newer public search has a JSON endpoint that some tools use)
  2. Design     → GET/POST to search.ipindia.gov.in/DesignApplicationStatus
  3. If CAPTCHA blocks us, fall back to Playwright (headless Chromium)
     with optional 2captcha/anticaptcha solver key.

Both portals are public government data so scraping is legitimate.
"""

import logging
import json
import time
import re
import os
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# --- Session pool (reuse TCP connections) -----------------------------------
_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-IN,en;q=0.9",
})


# ============================================================================
# Trademark scraper
# ============================================================================

TM_STATUS_URL = "https://tmrsearch.ipindia.gov.in/eregister/eregister.aspx"
TM_JSON_URL   = "https://tmrsearch.ipindia.gov.in/tmrpublicsearch/frmmain.aspx"
TM_API_URL    = "https://tmrsearch.ipindia.gov.in/tmrpublicsearch/PublicationSearch.aspx"


def _scrape_trademark_status_requests(application_number: str) -> dict:
    """
    Attempt to get trademark status via the public-facing eRegister
    without solving CAPTCHA.  The eRegister page returns an HTTP-200
    even without captcha for application-number lookups – we just
    parse the resulting HTML table.
    """
    try:
        # Step 1: get the form state values
        resp = _session.get(TM_STATUS_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        viewstate = soup.find("input", {"id": "__VIEWSTATE"})
        eventval   = soup.find("input", {"id": "__EVENTVALIDATION"})
        viewstate  = viewstate["value"] if viewstate else ""
        eventval   = eventval["value"] if eventval else ""

        payload = {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__VIEWSTATE": viewstate,
            "__EVENTVALIDATION": eventval,
            "ctl00$ContentPlaceHolder1$hdnType": "T",
            "ctl00$ContentPlaceHolder1$txtTMNo": application_number.strip(),
            "ctl00$ContentPlaceHolder1$btnShow": "Show Status",
        }

        resp2 = _session.post(TM_STATUS_URL, data=payload, timeout=20)
        resp2.raise_for_status()
        return _parse_tm_html(resp2.text, application_number)
    except Exception as exc:
        logger.warning(f"TM requests scrape failed for {application_number}: {exc}")
        return {"error": str(exc), "raw": ""}


def _parse_tm_html(html: str, application_number: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    # Look for the status in the known table structure
    result = {
        "application_number": application_number,
        "status": None,
        "applicant": None,
        "mark": None,
        "class": None,
        "filed_date": None,
        "raw": html[:2000],
    }

    # Try to find status cell (IP India uses label/value rows)
    rows = soup.select("table tr")
    for row in rows:
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

    # Fallback: search for status keyword
    if not result["status"]:
        text = soup.get_text(" ", strip=True)
        m = re.search(r"Status[:\s]+([A-Za-z][^\n\r]{2,60})", text, re.IGNORECASE)
        if m:
            result["status"] = m.group(1).strip()

    # CAPTCHA page detection
    if "captcha" in html.lower() and not result["status"]:
        result["error"] = "CAPTCHA_REQUIRED"

    return result


# ============================================================================
# Design scraper
# ============================================================================

DESIGN_STATUS_URL = "https://search.ipindia.gov.in/DesignApplicationStatus/"


def _scrape_design_status_requests(application_number: str) -> dict:
    """
    The design status portal at search.ipindia.gov.in/DesignApplicationStatus
    accepts a simple POST with the application number.
    Format: NNNNNN-001  (the -001 suffix is mandatory per IP India docs)
    """
    app_no = application_number.strip()
    if not re.search(r"-\d{3}$", app_no):
        app_no = app_no + "-001"

    result = {
        "application_number": application_number,
        "normalized_number": app_no,
        "status": None,
        "applicant": None,
        "filed_date": None,
        "raw": "",
    }

    try:
        # GET the page first to grab hidden fields
        resp = _session.get(DESIGN_STATUS_URL, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        viewstate = soup.find("input", {"id": "__VIEWSTATE"})
        eventval   = soup.find("input", {"id": "__EVENTVALIDATION"})
        vs = viewstate["value"] if viewstate else ""
        ev = eventval["value"] if eventval else ""

        payload = {
            "__VIEWSTATE": vs,
            "__EVENTVALIDATION": ev,
            "ctl00$cphBody$txtApplicationNumber": app_no,
            "ctl00$cphBody$btnShowStatus": "Show Status",
        }

        resp2 = _session.post(DESIGN_STATUS_URL, data=payload, timeout=20)
        resp2.raise_for_status()
        result["raw"] = resp2.text[:2000]
        return _parse_design_html(resp2.text, result)
    except Exception as exc:
        logger.warning(f"Design requests scrape failed for {application_number}: {exc}")
        result["error"] = str(exc)
        return result


def _parse_design_html(html: str, result: dict) -> dict:
    soup = BeautifulSoup(html, "lxml")

    rows = soup.select("table tr")
    for row in rows:
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

    if not result["status"]:
        text = soup.get_text(" ", strip=True)
        m = re.search(r"Status[:\s]+([A-Za-z][^\n\r]{2,80})", text, re.IGNORECASE)
        if m:
            result["status"] = m.group(1).strip()

    # "Record Not Found" or error
    if "record not found" in html.lower():
        result["status"] = "Record Not Found"

    if "captcha" in html.lower() and not result["status"]:
        result["error"] = "CAPTCHA_REQUIRED"

    return result


# ============================================================================
# Playwright fallback (headless Chromium, no CAPTCHA solving needed for
# the simpler design portal; TM portal may still need manual CAPTCHA)
# ============================================================================

def _scrape_with_playwright(filing_type: str, application_number: str) -> dict:
    """Use Playwright headless browser. Install once with: playwright install chromium."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "playwright_not_installed", "status": None}

    result = {"application_number": application_number, "status": None, "raw": ""}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page()
        page.set_extra_http_headers({"Accept-Language": "en-IN,en;q=0.9"})

        try:
            if filing_type == "design":
                app_no = application_number.strip()
                if not re.search(r"-\d{3}$", app_no):
                    app_no += "-001"

                page.goto(DESIGN_STATUS_URL, wait_until="networkidle", timeout=30000)
                page.fill("input[id*='txtApplicationNumber']", app_no)
                page.click("input[id*='btnShowStatus']")
                page.wait_for_load_state("networkidle", timeout=20000)

                html = page.content()
                result["raw"] = html[:2000]
                result = _parse_design_html(html, result)

            elif filing_type == "trademark":
                page.goto(TM_STATUS_URL, wait_until="networkidle", timeout=30000)
                page.fill("input[id*='txtTMNo']", application_number.strip())
                # Note: CAPTCHA present – we can only handle if anticaptcha key available
                anticaptcha_key = os.environ.get("ANTICAPTCHA_KEY", "")
                if anticaptcha_key:
                    result = _solve_captcha_and_submit(page, anticaptcha_key, application_number)
                else:
                    result["error"] = "CAPTCHA_REQUIRED"
                    result["status"] = "Manual check required (CAPTCHA)"

        except Exception as exc:
            logger.warning(f"Playwright error for {filing_type}/{application_number}: {exc}")
            result["error"] = str(exc)
        finally:
            browser.close()

    return result


def _solve_captcha_and_submit(page, anticaptcha_key: str, application_number: str) -> dict:
    """
    Attempt anticaptcha.com image recognition to solve the CAPTCHA.
    Requires: pip install anticaptchaofficial
    """
    result = {"application_number": application_number, "status": None, "raw": ""}
    try:
        from anticaptchaofficial.imagecaptcha import imagecaptcha  # type: ignore
        captcha_img = page.locator("img[id*='captcha'], img[class*='captcha']").first
        captcha_bytes = captcha_img.screenshot()
        import base64
        b64 = base64.b64encode(captcha_bytes).decode()

        solver = imagecaptcha()
        solver.set_key(anticaptcha_key)
        solver.set_verbose(0)
        captcha_text = solver.solve_and_return_solution(None, body=b64)

        if captcha_text:
            page.fill("input[id*='captcha'], input[name*='captcha']", captcha_text)
            page.click("input[id*='btnShow'], button[id*='Show']")
            page.wait_for_load_state("networkidle", timeout=20000)
            html = page.content()
            result["raw"] = html[:2000]
            result = _parse_tm_html(html, application_number)
        else:
            result["error"] = "CAPTCHA_SOLVE_FAILED"
    except Exception as exc:
        result["error"] = f"anticaptcha: {exc}"
    return result


# ============================================================================
# Public API
# ============================================================================

def check_status(filing_type: str, application_number: str) -> dict:
    """
    Main entry point.  Returns a dict with at least:
      status (str | None), application_number, error (optional)
    """
    filing_type = filing_type.lower()

    if filing_type == "trademark":
        result = _scrape_trademark_status_requests(application_number)
        if result.get("error") == "CAPTCHA_REQUIRED" or not result.get("status"):
            logger.info(f"Falling back to Playwright for TM {application_number}")
            result = _scrape_with_playwright("trademark", application_number)

    elif filing_type == "design":
        result = _scrape_design_status_requests(application_number)
        if result.get("error") == "CAPTCHA_REQUIRED" or not result.get("status"):
            logger.info(f"Falling back to Playwright for design {application_number}")
            result = _scrape_with_playwright("design", application_number)

    else:
        result = {"error": f"Unknown filing type: {filing_type}", "status": None}

    # Normalise
    if result.get("status"):
        result["status"] = result["status"].strip()

    logger.info(
        f"check_status [{filing_type}] {application_number} → "
        f"{result.get('status') or result.get('error')}"
    )
    return result
