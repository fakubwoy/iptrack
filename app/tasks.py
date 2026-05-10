"""
APScheduler tasks – run every N hours to poll IP India for status updates.
Memory-efficient: process one filing at a time, commit after each.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def schedule_status_checks(app, scheduler):
    hours = app.config.get("CHECK_INTERVAL_HOURS", 12)

    def poll_all_filings():
        with app.app_context():
            _poll_all_filings_inner()

    scheduler.add_job(
        poll_all_filings,
        trigger="interval",
        hours=hours,
        id="poll_all_filings",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    logger.info(f"Status-check job scheduled every {hours}h.")


def _poll_all_filings_inner():
    """
    Iterate filings one at a time to keep memory use flat.
    """
    from app import db
    from app.models import Filing

    logger.info("Starting scheduled polling run.")
    page_size = 20
    offset = 0

    while True:
        batch = (
            Filing.query
            .filter(Filing.alerts_enabled == True)
            .order_by(Filing.id)
            .limit(page_size)
            .offset(offset)
            .all()
        )
        if not batch:
            break
        offset += page_size

        for filing in batch:
            try:
                _check_and_record(filing, db)
            except Exception as exc:
                logger.error(f"Poll error for filing {filing.id}: {exc}")
            finally:
                db.session.expire_all()   # free memory after each filing

    logger.info("Scheduled polling run complete.")


def _check_and_record(filing, db):
    from app.models import StatusHistory, Notification
    from app.scraper import check_status
    import json

    result = check_status(filing.filing_type, filing.application_number)
    now = datetime.utcnow()

    new_status = result.get("status") or result.get("error", "Unknown")
    changed = (new_status != filing.last_status) and (filing.last_status is not None)

    # Persist history
    history = StatusHistory(
        filing_id=filing.id,
        status=new_status,
        raw_data=json.dumps(result)[:4000],
        checked_at=now,
        changed=changed,
    )
    db.session.add(history)

    # Update filing
    filing.last_checked_at = now
    if filing.last_status is None or changed:
        if changed:
            filing.last_changed_at = now
        filing.last_status = new_status

    # Create notification for status change
    if changed:
        msg = (
            f"Status changed for {filing.filing_type.title()} "
            f"{filing.application_number}"
            f"{' – ' + filing.label if filing.label else ''}: "
            f"'{new_status}'"
        )
        notif = Notification(
            user_id=filing.user_id,
            filing_id=filing.id,
            message=msg,
        )
        db.session.add(notif)
        logger.info(f"Status change detected: {msg}")

    db.session.commit()
