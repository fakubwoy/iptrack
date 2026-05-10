from datetime import datetime
from flask_login import UserMixin
from app import db, login_manager
import pytz


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_active = db.Column(db.Boolean, default=True)

    filings = db.relationship("Filing", backref="owner", lazy="dynamic",
                               cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.email}>"


class Filing(db.Model):
    """Represents a single trademark or design registration the user tracks."""
    __tablename__ = "filings"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    # "trademark" | "design"
    filing_type = db.Column(db.String(20), nullable=False)
    application_number = db.Column(db.String(50), nullable=False)
    label = db.Column(db.String(200))           # e.g. "Alfaleus Class 9"

    # latest known status
    last_status = db.Column(db.Text)
    last_checked_at = db.Column(db.DateTime)
    last_changed_at = db.Column(db.DateTime)    # when status last changed

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    alerts_enabled = db.Column(db.Boolean, default=True)

    status_history = db.relationship("StatusHistory", backref="filing", lazy="dynamic",
                                      cascade="all, delete-orphan",
                                      order_by="StatusHistory.checked_at.desc()")

    def last_checked_ist(self):
        if not self.last_checked_at:
            return None
        ist = pytz.timezone("Asia/Kolkata")
        return self.last_checked_at.replace(tzinfo=pytz.utc).astimezone(ist)

    def __repr__(self):
        return f"<Filing {self.filing_type}:{self.application_number}>"


class StatusHistory(db.Model):
    """Immutable log of every status snapshot we've ever captured."""
    __tablename__ = "status_history"

    id = db.Column(db.Integer, primary_key=True)
    filing_id = db.Column(db.Integer, db.ForeignKey("filings.id"), nullable=False, index=True)
    status = db.Column(db.Text, nullable=False)
    raw_data = db.Column(db.Text)               # full JSON/text from scraper
    checked_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    changed = db.Column(db.Boolean, default=False)  # True if different from previous


class Notification(db.Model):
    """In-app notifications for status changes."""
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    filing_id = db.Column(db.Integer, db.ForeignKey("filings.id"), nullable=False)
    message = db.Column(db.Text, nullable=False)
    is_read = db.Column(db.Boolean, default=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    filing = db.relationship("Filing")
