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
