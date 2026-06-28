# app/utils/timeutil.py
# Single source of "now". The codebase stores naive-UTC datetimes (the DB columns
# are naive), so this returns a timezone-naive UTC value — but via the
# non-deprecated datetime.now(UTC) rather than the deprecated datetime.utcnow().

from datetime import datetime, timezone


def utcnow():
    """Current UTC time as a naive datetime (tzinfo stripped)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
