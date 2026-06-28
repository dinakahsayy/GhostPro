# app/services/sources.py
# Followed Sources CRUD (§4.4, §7.3). These are the company pages / blogs / news
# feeds the source watcher polls to surface suggested inbox items.

from datetime import datetime

from ..models.database import FollowedSource

VALID_SOURCE_TYPES = {"linkedin_page", "rss_feed", "website"}


def create_source(session, user, source_type, source_url, source_name=None):
    source_type = (source_type or "").strip()
    source_url = (source_url or "").strip()
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError("Unsupported source type")
    if not source_url.startswith("https://"):
        raise ValueError("Source URL must start with https://")

    source = FollowedSource(
        user=user,
        source_type=source_type,
        source_url=source_url,
        source_name=(source_name or None),
        active=True,
        created_at=datetime.utcnow(),
    )
    session.add(source)
    return source


def list_sources(session, user):
    return (
        session.query(FollowedSource)
        .filter(FollowedSource.user_id == user.get_id())
        .order_by(FollowedSource.created_at.desc())
        .all()
    )


def get_source(session, user, source_id):
    source = session.get(FollowedSource, source_id)
    if source is None or source.user_id != user.get_id():
        return None
    return source


def delete_source(session, source):
    session.delete(source)


def toggle_source(source):
    source.active = not source.active
    return source


def source_to_dict(source):
    return {
        "id": source.id,
        "source_type": source.source_type,
        "source_url": source.source_url,
        "source_name": source.source_name,
        "active": source.active,
        "last_checked_at": source.last_checked_at.isoformat() if source.last_checked_at else None,
        "created_at": source.created_at.isoformat() if source.created_at else None,
    }
