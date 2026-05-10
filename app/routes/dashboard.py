from flask import Blueprint, render_template, redirect, url_for, flash, request, abort, current_app
from flask_login import login_required, current_user
from app import db
from app.models import Filing, Notification, StatusHistory

dashboard_bp = Blueprint("dashboard", __name__)


def _unread_count():
    """Helper: unread notification count for the current user (used by base.html)."""
    return Notification.query.filter_by(user_id=current_user.id, is_read=False).count()


@dashboard_bp.route("/dashboard")
@login_required
def home():
    filings = (
        current_user.filings
        .order_by(Filing.created_at.desc())
        .all()
    )
    unread = _unread_count()
    return render_template(
        "dashboard.html",
        filings=filings,
        unread_count=unread,
        check_interval_hours=current_app.config.get("CHECK_INTERVAL_HOURS", 12),
    )


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
            flash("Filing added! Status will be checked shortly.", "success")
            # Fire-and-forget in a background thread so the response returns immediately.
            # The scraper can take up to 30–60 s (Playwright); we must not block here.
            _trigger_immediate_check_async(filing.id)
            return redirect(url_for("dashboard.home"))

    return render_template("add_filing.html", unread_count=_unread_count())


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
    return render_template("history.html", filing=filing, history=history, unread_count=_unread_count())


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
    return render_template("notifications.html", notifications=notifs, unread_count=0)


def _trigger_immediate_check_async(filing_id: int):
    """
    Run a status check in a daemon thread so the web response isn't blocked.
    Uses the current app's context via current_app._get_current_object().
    """
    import threading
    import logging
    from flask import current_app

    app = current_app._get_current_object()

    def _run():
        with app.app_context():
            from app import db as _db
            from app.models import Filing as _Filing
            from app.tasks import _check_and_record
            filing = _Filing.query.get(filing_id)
            if filing is None:
                return
            try:
                _check_and_record(filing, _db)
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    f"Immediate check failed for filing {filing_id}: {exc}"
                )

    t = threading.Thread(target=_run, daemon=True)
    t.start()