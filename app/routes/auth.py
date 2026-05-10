from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from app import db, bcrypt
from app.models import User

auth_bp = Blueprint("auth", __name__)


@auth_bp.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@auth_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))
    return redirect(url_for("auth.login"))


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    if request.method == "POST":
        name  = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        pw2   = request.form.get("password2", "")

        if not name or not email or not pw:
            flash("All fields are required.", "danger")
        elif pw != pw2:
            flash("Passwords do not match.", "danger")
        elif len(pw) < 8:
            flash("Password must be at least 8 characters.", "danger")
        elif User.query.filter_by(email=email).first():
            flash("An account with that email already exists.", "danger")
        else:
            hashed = bcrypt.generate_password_hash(pw).decode("utf-8")
            user = User(name=name, email=email, password_hash=hashed)
            db.session.add(user)
            db.session.commit()
            login_user(user)
            flash(f"Welcome, {name}! Start tracking your IP filings.", "success")
            return redirect(url_for("dashboard.home"))

    return render_template("signup.html")


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        pw    = request.form.get("password", "")
        user  = User.query.filter_by(email=email).first()

        if user and bcrypt.check_password_hash(user.password_hash, pw):
            login_user(user, remember=bool(request.form.get("remember")))
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard.home"))
        else:
            flash("Invalid email or password.", "danger")

    return render_template("login.html")


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@auth_bp.route("/debug-tm-portal")
def debug_tm_portal():
    """TEMPORARY – remove after debugging."""
    import requests as _req
    from bs4 import BeautifulSoup
    import urllib3, re
    urllib3.disable_warnings()

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9",
        "Referer": "https://tmrsearch.ipindia.gov.in/eregister/eregister.aspx",
    }
    BASE = "https://tmrsearch.ipindia.gov.in/eregister/"
    results = {}

    # 1. Fetch the CSS file — it may reference the target page URLs
    for asset in ["Css/ereg.css", "Css/style.css", "Css/main.css"]:
        try:
            r = _req.get(BASE + asset, headers=HEADERS, verify=False, timeout=10)
            results[f"css_{asset}"] = {"status": r.status_code, "body": r.text[:3000]}
        except Exception as e:
            results[f"css_{asset}"] = {"error": str(e)}

    # 2. Use Playwright to actually load the page, click the first visible
    #    button-like element, and capture what URL loads in showframe
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox","--disable-setuid-sandbox",
                      "--disable-dev-shm-usage","--ignore-certificate-errors"]
            )
            context = browser.new_context(ignore_https_errors=True)

            # Intercept all requests to find what URLs get fetched
            all_requests = []
            context.on("request", lambda req: all_requests.append({
                "url": req.url, "method": req.method,
                "post_data": req.post_data if req.method == "POST" else None,
            }))

            page = context.new_page()
            page.goto(BASE + "eregister.aspx", wait_until="domcontentloaded", timeout=30000)

            # Give frames time to load
            page.wait_for_timeout(3000)

            # Get all frame URLs
            frame_urls = [f.url for f in page.frames]
            results["playwright_frames"] = frame_urls

            # Try to access the options frame and click anything in it
            options_frame = None
            for frame in page.frames:
                if "options" in frame.url:
                    options_frame = frame
                    break

            if options_frame:
                frame_html = options_frame.content()
                results["options_frame_html"] = frame_html
                # Find all clickable elements
                clickables = options_frame.evaluate("""() =>
                    Array.from(document.querySelectorAll('a, input[type=submit], button')).map(el => ({
                        tag: el.tagName, href: el.href || '', id: el.id,
                        text: el.textContent.trim().slice(0,50),
                        onclick: el.getAttribute('onclick') || ''
                    }))
                """)
                results["options_frame_clickables"] = clickables

                # Click the first link and see what happens
                links = options_frame.query_selector_all("a")
                if links:
                    links[0].click()
                    page.wait_for_timeout(2000)
                    # Check showframe content
                    for frame in page.frames:
                        if "options" not in frame.url and "ereg_top" not in frame.url and frame.url != BASE + "eregister.aspx":
                            results["showframe_after_click"] = {
                                "url": frame.url,
                                "html": frame.content()[:3000],
                            }
            else:
                results["options_frame"] = "not found"
                results["all_frame_urls"] = frame_urls

            results["all_network_requests"] = all_requests[:50]
            browser.close()

    except Exception as e:
        results["playwright_error"] = str(e)

    return jsonify(results)