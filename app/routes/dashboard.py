from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user
from app import db
from app.models import Filing, Notification, StatusHistory

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/dashboard")
@login_required
def home():
    filings = (
        current_user.filings
        .order_by(Filing.created_at.desc())
        .all()
    )
    unread_count = (
        Notification.query
        .filter_by(user_id=current_user.id, is_read=False)
        .count()
    )
    return render_template("dashboard.html", filings=filings, unread_count=unread_count)


@dashboard_bp.route("/filings/add", methods=["GET", "POST"])
@login_required
def add_filing():
    if request.method == "POST":
        filing_type = request.form.get("filing_type", "").strip().lower()
        app_no      = request.form.get("application_number", "").strip()
        label       = request.form.get("label", "").strip()

        if filing_type not in ("trademark", "design"):
            flash("Select a valid filing type.", "danger")
        elif not app_no:
            flash("Application number is required.", "danger")
        else:
            existing = Filing.query.filter_by(
                user_id=current_user.id,
                filing_type=filing_type,
                application_number=app_no,
            ).first()
            if existing:
                flash("You are already tracking this filing.", "warning")
                return redirect(url_for("dashboard.home"))

            filing = Filing(
                user_id=current_user.id,
                filing_type=filing_type,
                application_number=app_no,
                label=label or None,
                alerts_enabled=True,
            )
            db.session.add(filing)
            db.session.commit()
            flash("Filing added! Checking status now…", "success")
            # Immediate async-style check via inline call
            _trigger_immediate_check(filing)
            return redirect(url_for("dashboard.home"))

    return render_template("add_filing.html")


@dashboard_bp.route("/filings/<int:filing_id>/delete", methods=["POST"])
@login_required
def delete_filing(filing_id):
    filing = Filing.query.get_or_404(filing_id)
    if filing.user_id != current_user.id:
        abort(403)
    db.session.delete(filing)
    db.session.commit()
    flash("Filing removed.", "info")
    return redirect(url_for("dashboard.home"))


@dashboard_bp.route("/filings/<int:filing_id>/history")
@login_required
def filing_history(filing_id):
    filing = Filing.query.get_or_404(filing_id)
    if filing.user_id != current_user.id:
        abort(403)
    history = (
        filing.status_history
        .order_by(StatusHistory.checked_at.desc())
        .limit(50)
        .all()
    )
    return render_template("history.html", filing=filing, history=history)


@dashboard_bp.route("/notifications")
@login_required
def notifications():
    notifs = (
        Notification.query
        .filter_by(user_id=current_user.id)
        .order_by(Notification.created_at.desc())
        .limit(100)
        .all()
    )
    # Mark all as read
    Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    return render_template("notifications.html", notifications=notifs)


def _trigger_immediate_check(filing):
    """Run a status check synchronously right after adding a filing."""
    from app.tasks import _check_and_record
    from app import db as _db
    try:
        _check_and_record(filing, _db)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(f"Immediate check failed: {exc}")
