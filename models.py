"""
Database models for ClipFind's paywall.

Uses SQLAlchemy so the same code runs against SQLite locally (zero setup)
and Postgres in production (via the DATABASE_URL env var Render's Postgres
add-on provides automatically). Free-tier users get FREE_DAILY_LIMIT
videos analyzed per day; paid users (is_paid=True, kept in sync via the
Stripe webhook) get unlimited.
"""

import datetime

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# Free tier gets 3 analyses AND 3 cuts/downloads per day — both metered,
# since cutting real video (Webshare bandwidth) is the expensive action,
# not analyzing (just transcript text). Only limiting analyses left cuts
# unbounded, which is a real cost leak on the free tier.
FREE_DAILY_LIMIT = 3
FREE_MAX_CLIP_SECONDS = 90
PAID_MAX_CLIP_SECONDS = 180
FREE_MAX_HEIGHT = 480
PAID_MAX_HEIGHT = 720


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    # Stripe
    stripe_customer_id = db.Column(db.String(255), nullable=True)
    stripe_subscription_id = db.Column(db.String(255), nullable=True)
    is_paid = db.Column(db.Boolean, default=False, nullable=False)

    # Everyone gets the discover digest by default (lazier for the user =
    # more reasons to come back) — this just tracks who's clicked
    # unsubscribe, rather than requiring an explicit opt-in step.
    email_opt_in = db.Column(db.Boolean, default=True, nullable=False)

    def set_password(self, raw_password: str) -> None:
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password: str) -> bool:
        return check_password_hash(self.password_hash, raw_password)

    def usage_today(self, kind: str = "analyze") -> int:
        today = datetime.date.today()
        row = DailyUsage.query.filter_by(user_id=self.id, date=today, kind=kind).first()
        return row.count if row else 0

    def remaining_today(self, kind: str = "analyze"):
        if self.is_paid:
            return None  # unlimited
        return max(0, FREE_DAILY_LIMIT - self.usage_today(kind))

    def can_use(self, kind: str = "analyze") -> bool:
        if self.is_paid:
            return True
        return self.usage_today(kind) < FREE_DAILY_LIMIT

    # kept for compatibility with existing call sites
    def can_analyze(self) -> bool:
        return self.can_use("analyze")

    def record_usage(self, kind: str = "analyze") -> None:
        today = datetime.date.today()
        row = DailyUsage.query.filter_by(user_id=self.id, date=today, kind=kind).first()
        if row is None:
            row = DailyUsage(user_id=self.id, date=today, kind=kind, count=0)
            db.session.add(row)
        row.count += 1
        db.session.commit()

    def max_clip_seconds(self) -> int:
        return PAID_MAX_CLIP_SECONDS if self.is_paid else FREE_MAX_CLIP_SECONDS

    def max_height(self) -> int:
        return PAID_MAX_HEIGHT if self.is_paid else FREE_MAX_HEIGHT


class DailyUsage(db.Model):
    __tablename__ = "daily_usage"
    __table_args__ = (db.UniqueConstraint("user_id", "date", "kind", name="uq_user_date_kind"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    date = db.Column(db.Date, nullable=False, default=datetime.date.today)
    kind = db.Column(db.String(20), nullable=False, default="analyze")
    count = db.Column(db.Integer, default=0, nullable=False)


class DiscoverFeed(db.Model):
    """Caches the Discover tab's results so we hit the YouTube Data API
    and the clip scorer (LLM cost!) once per refresh, not once per
    visitor. A single row holds the whole feed as JSON, replaced wholesale
    on each refresh rather than diffed row-by-row — simplest thing that
    works at this scale."""

    __tablename__ = "discover_feed"

    id = db.Column(db.Integer, primary_key=True)
    computed_at = db.Column(db.DateTime, default=datetime.datetime.utcnow, nullable=False)
    feed_json = db.Column(db.Text, nullable=False)  # JSON-encoded list of picks
