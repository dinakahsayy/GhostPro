# app/services/source_selection.py
# Picks the source material for a post following the §8.1 priority order:
#   1. Content Inbox - Post Soon items   (oldest first / FIFO)
#   2. Content Inbox - Use Whenever items (oldest first / FIFO)
#   3. User-defined topics from the style profile
#   4. Industry news from a news API      (Phase 3 - not yet wired)
#   5. Seasonal / general fallback

import random
from dataclasses import dataclass

from ..models.database import ContentInbox


@dataclass
class Source:
    source_type: str          # content_inbox | user_topic | news_api | seasonal
    text: str                 # the material to write about
    inbox_item: object = None  # ContentInbox row when source_type == content_inbox
    context_note: str = None   # optional angle/audience from the inbox item
    label: str = None          # human-readable source label for notifications


def _oldest_pending(session, user, priority):
    return (
        session.query(ContentInbox)
        .filter(
            ContentInbox.user_id == user.get_id(),
            ContentInbox.status == "pending",
            ContentInbox.priority == priority,
        )
        .order_by(ContentInbox.created_at.asc())
        .first()
    )


def select_source(session, user):
    """Return the highest-priority available Source for this user."""
    # 1 & 2 — Content Inbox, Post Soon then Use Whenever, oldest first.
    for priority in ("post_soon", "use_whenever"):
        item = _oldest_pending(session, user, priority)
        if item is not None:
            material = item.raw_content
            if item.content_type == "url" and item.parsed_content:
                material = item.parsed_content
            return Source(
                source_type="content_inbox",
                text=material,
                inbox_item=item,
                context_note=item.context_note,
                label=item.source_label or "your Content Inbox",
            )

    # 3 — User-defined topics from the style profile.
    profile = user.style_profile
    if profile and profile.top_topics:
        topic = random.choice(profile.top_topics)
        return Source(source_type="user_topic", text=topic, label=f"Topic: {topic}")

    # 4 — Industry news (Phase 3). Skipped for now.

    # 5 — Seasonal / general fallback.
    return Source(
        source_type="seasonal",
        text="a timely professional insight or lesson relevant to your field",
        label="Seasonal / general",
    )
