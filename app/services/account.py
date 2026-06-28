# app/services/account.py
# GDPR/CCPA account deletion (§11.2): soft-delete first (deleted_at), then hard
# delete after a 30-day grace period. A hard delete cascades to every owned row
# (style profile, inbox items, followed sources, posts, scheduled jobs,
# notifications, and the encrypted tokens) via the User relationships.

from datetime import datetime, timedelta
from ..utils.timeutil import utcnow

from ..models.database import ScheduledJob, User

GRACE_DAYS = 30


def soft_delete_account(session, user, now=None):
    """Mark the account for deletion and stop its schedule immediately."""
    now = now or utcnow()
    user.deleted_at = now
    job = session.query(ScheduledJob).filter_by(user_id=user.get_id()).first()
    if job:
        job.status = "paused"
    return user


def hard_delete_user(session, user):
    """Permanently erase a user and all owned data (relationship cascade)."""
    session.delete(user)


def purge_expired_accounts(session, now=None, grace_days=GRACE_DAYS):
    """Hard-delete accounts whose grace period has elapsed. Returns the count."""
    now = now or utcnow()
    cutoff = now - timedelta(days=grace_days)
    expired = (
        session.query(User)
        .filter(User.deleted_at.isnot(None), User.deleted_at < cutoff)
        .all()
    )
    for user in expired:
        session.delete(user)
    return len(expired)
