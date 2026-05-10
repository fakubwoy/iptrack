"""
JSON API endpoints consumed by the frontend JS.
"""
from flask import Blueprint, jsonify, abort
from flask_login import login_required, current_user
from app import db
from app.models import Filing, Notification

api_bp = Blueprint("api", __name__)


@api_bp.route("/filings/<int:filing_id>/check", methods=["POST"])
@login_required
def manual_check(filing_id):
    filing = Filing.query.get_or_404(filing_id)
    if filing.user_id != current_user.id:
        abort(403)

    from app.tasks import _check_and_record
    try:
        _check_and_record(filing, db)
        return jsonify({
            "ok": True,
            "status": filing.last_status,
            "last_checked": filing.last_checked_at.isoformat() if filing.last_checked_at else None,
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@api_bp.route("/notifications/unread-count")
@login_required
def unread_count():
    count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
    return jsonify({"count": count})


@api_bp.route("/notifications/recent")
@login_required
def recent_notifications():
    notifs = (
        Notification.query
        .filter_by(user_id=current_user.id, is_read=False)
        .order_by(Notification.created_at.desc())
        .limit(10)
        .all()
    )
    data = [
        {
            "id": n.id,
            "message": n.message,
            "created_at": n.created_at.isoformat(),
            "filing_id": n.filing_id,
        }
        for n in notifs
    ]
    return jsonify({"notifications": data})


@api_bp.route("/filings/<int:filing_id>/toggle-alerts", methods=["POST"])
@login_required
def toggle_alerts(filing_id):
    filing = Filing.query.get_or_404(filing_id)
    if filing.user_id != current_user.id:
        abort(403)
    filing.alerts_enabled = not filing.alerts_enabled
    db.session.commit()
    return jsonify({"ok": True, "alerts_enabled": filing.alerts_enabled})
