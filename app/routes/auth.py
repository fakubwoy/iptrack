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

    # GET options.aspx
    r = _req.get(BASE + "options.aspx", headers=HEADERS, verify=False, timeout=20)
    soup = BeautifulSoup(r.text, "lxml")
    vs  = (soup.find("input", {"id": "__VIEWSTATE"}) or {}).get("value", "")
    vsg = (soup.find("input", {"id": "__VIEWSTATEGENERATOR"}) or {}).get("value", "")
    vse = (soup.find("input", {"id": "__VIEWSTATEENCRYPTED"}) or {}).get("value", "")

    # Extract all .aspx references and __doPostBack targets from source
    aspx_refs = re.findall(r'["\']([^"\']*\.aspx[^"\']*)["\']', r.text, re.I)
    dopostback = re.findall(r"__doPostBack\('([^']+)'", r.text)
    all_hrefs = [a.get("href", "") for a in soup.find_all("a")]
    results["options_analysis"] = {
        "status": r.status_code,
        "aspx_refs": aspx_refs,
        "dopostback_targets": dopostback,
        "hrefs": all_hrefs,
        "full_raw": r.text,
    }

    # Try __doPostBack for each target found
    for target in dopostback:
        payload = {
            "__VIEWSTATE": vs, "__VIEWSTATEGENERATOR": vsg,
            "__VIEWSTATEENCRYPTED": vse,
            "__EVENTTARGET": target, "__EVENTARGUMENT": "",
        }
        try:
            r2 = _req.post(BASE + "options.aspx", data=payload, headers=HEADERS, verify=False, timeout=20)
            soup2 = BeautifulSoup(r2.text, "lxml")
            t = soup2.find("title")
            results[f"postback_{target}"] = {
                "status": r2.status_code, "final_url": r2.url,
                "title": t.get_text(strip=True) if t else "",
                "inputs": [{k:v for k,v in i.attrs.items() if k in ("id","name","type")} for i in soup2.find_all("input")],
                "raw": r2.text[:2000],
            }
        except Exception as e:
            results[f"postback_{target}"] = {"error": str(e)}

    # Probe candidate URLs directly
    for path in [
        "Application_View.aspx", "TM_View.aspx", "ereg_view.aspx",
        "showstatus.aspx", "TM_Status.aspx", "TMStatus.aspx",
        "TradeMarkStatus.aspx", "AppStatus.aspx", "ViewStatus.aspx",
        "TM_AppStatus.aspx", "eregister_status.aspx", "status.aspx",
        "TradeMarkApp_View.aspx", "eregister_search.aspx", "StatusView.aspx",
    ]:
        try:
            r3 = _req.get(BASE + path, headers=HEADERS, verify=False, timeout=10)
            soup3 = BeautifulSoup(r3.text, "lxml")
            t = soup3.find("title")
            results[f"probe_{path}"] = {
                "status": r3.status_code,
                "title": t.get_text(strip=True) if t else "",
                "inputs": [{k:v for k,v in i.attrs.items() if k in ("id","name","type")} for i in soup3.find_all("input")],
                "raw": r3.text[:800],
            }
        except Exception as e:
            results[f"probe_{path}"] = {"error": str(e)}

    return jsonify(results)