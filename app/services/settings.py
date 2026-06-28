# app/services/settings.py
# Read/update user settings (§7.5). Changing cadence-related fields recomputes
# the user's schedule so the next post fires at the right time.

from .scheduler import ensure_schedule
from ..models.database import ScheduledJob

_SCHEDULE_FIELDS = {"post_frequency", "preferred_days", "preferred_time", "timezone"}
_TEXT_FIELDS = ("posting_mode", "post_frequency", "preferred_time", "timezone")


def settings_to_dict(session, user):
    job = session.query(ScheduledJob).filter_by(user_id=user.get_id()).first()
    profile = user.style_profile
    return {
        "posting_mode": user.posting_mode,
        "post_frequency": user.post_frequency,
        "preferred_days": user.preferred_days or [],
        "preferred_time": user.preferred_time,
        "timezone": user.timezone,
        "notification_email": user.notification_email,
        "notification_inapp": user.notification_inapp,
        "schedule_status": job.status if job else None,
        "next_run_at": job.next_run_at.isoformat() if job and job.next_run_at else None,
        "style": {
            "tone": profile.tone if profile else None,
            "preferred_length": profile.preferred_length if profile else None,
            "top_topics": (profile.top_topics if profile else None) or [],
        },
    }


def update_settings(session, user, data):
    """Apply settings changes; recompute the schedule if cadence changed."""
    touched_schedule = False
    for field in _TEXT_FIELDS:
        value = data.get(field)
        if value not in (None, ""):
            setattr(user, field, value)
            if field in _SCHEDULE_FIELDS:
                touched_schedule = True
    if "preferred_days" in data and data["preferred_days"] is not None:
        user.preferred_days = data["preferred_days"]
        touched_schedule = True
    if "notification_email" in data:
        user.notification_email = bool(data["notification_email"])
    if "notification_inapp" in data:
        user.notification_inapp = bool(data["notification_inapp"])

    if touched_schedule:
        # Only re-arm an active schedule; don't resurrect a paused one.
        job = session.query(ScheduledJob).filter_by(user_id=user.get_id()).first()
        if job is None or job.status == "active":
            ensure_schedule(session, user)
    return user
