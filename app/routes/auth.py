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
        "Referer": "https://tmrsearch.ipindia.gov.in/estatus/",
    }
    BASE = "https://tmrsearch.ipindia.gov.in/estatus"
    results = {}

    # Step 1: get the homepage and extract all JS/CSS bundle URLs
    r = _req.get(BASE + "/", headers=HEADERS, verify=False, timeout=20)
    soup = BeautifulSoup(r.text, "lxml")

    script_srcs = [s.get("src","") for s in soup.find_all("script") if s.get("src")]
    results["script_srcs"] = script_srcs

    # Step 2: fetch each JS file and grep for API-looking paths
    api_patterns = re.compile(r'["\`\'](/(?:api|estatus)[^\s"\'`<>]{2,80})["\`\']', re.I)
    fetch_patterns = re.compile(r'fetch\(["\`\'](/[^\s"\'`<>]{5,100})["\`\']', re.I)

    for src in script_srcs[:10]:
        if not src.startswith("http"):
            src = "https://tmrsearch.ipindia.gov.in" + src
        try:
            rj = _req.get(src, headers=HEADERS, verify=False, timeout=15)
            found_apis = list(set(api_patterns.findall(rj.text) + fetch_patterns.findall(rj.text)))
            results[f"js_{src.split('/')[-1][:40]}"] = {
                "status": rj.status_code,
                "size": len(rj.text),
                "api_paths": found_apis[:40],
                "snippet": rj.text[:500],
            }
        except Exception as e:
            results[f"js_{src}"] = {"error": str(e)}

    # Step 3: try common ASP.NET Core API conventions directly
    tm_no = "5870022"
    api_attempts = [
        ("GET",  f"{BASE}/api/trademark/{tm_no}",         {}),
        ("GET",  f"{BASE}/api/TradeMark/{tm_no}",         {}),
        ("GET",  f"{BASE}/api/status/{tm_no}",            {}),
        ("GET",  f"{BASE}/api/eregister/{tm_no}",         {}),
        ("POST", f"{BASE}/api/trademark/status",          {"applicationNumber": tm_no}),
        ("POST", f"{BASE}/api/TradeMark/GetStatus",       {"tmNo": tm_no}),
        ("GET",  f"{BASE}/TradeMarkStatus/GetStatus?tmNo={tm_no}", {}),
        ("GET",  f"{BASE}/Home/GetTMStatus?tmno={tm_no}", {}),
        ("POST", f"{BASE}/Home/GetTMStatus",              {"tmno": tm_no}),
        ("GET",  f"https://tmrsearch.ipindia.gov.in/api/trademark/{tm_no}", {}),
    ]
    for method, url, body in api_attempts:
        try:
            if method == "GET":
                r2 = _req.get(url, headers={**HEADERS, "Accept": "application/json"},
                              verify=False, timeout=10)
            else:
                r2 = _req.post(url, json=body,
                               headers={**HEADERS, "Accept": "application/json",
                                        "Content-Type": "application/json"},
                               verify=False, timeout=10)
            results[f"{method}_{url.split(BASE)[-1][:50]}"] = {
                "status": r2.status_code,
                "content_type": r2.headers.get("Content-Type",""),
                "body": r2.text[:500],
            }
        except Exception as e:
            results[f"{method}_{url}"] = {"error": str(e)}

    return jsonify(results)