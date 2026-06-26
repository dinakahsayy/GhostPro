# app/services/inbox.py
# Content Inbox operations (§4, §7.3), kept out of the route layer for testability.

from datetime import datetime

from ..models.database import ContentInbox
from ..utils.url_fetch import fetch_url_text

# 'suggested' is reserved for the Phase 3 source watcher, not manual submission.
CONTENT_TYPES = {"text_note", "url", "quote_stat", "company_update"}
PRIORITIES = {"post_soon", "use_whenever"}
# Statuses shown in the inbox history; 'deleted' is hidden.
VISIBLE_STATUSES = {"pending", "in_progress", "used", "skipped"}


def _make_source_label(content_type, raw_content, parsed_content):
    """Short human-readable label shown in notifications and the inbox list."""
    if content_type == "url":
        # First line of parsed text is usually the page title.
        first_line = (parsed_content or "").splitlines()[0].strip() if parsed_content else ""
        label = first_line or raw_content
    else:
        label = raw_content
    label = " ".join(label.split())  # collapse whitespace
    return (label[:77] + "...") if len(label) > 80 else label


def create_inbox_item(session, user, content_type, raw_content,
                      priority="use_whenever", context_note=None,
                      fetcher=fetch_url_text):
    content_type = (content_type or "").strip()
    raw_content = (raw_content or "").strip()
    priority = priority or "use_whenever"

    if content_type not in CONTENT_TYPES:
        raise ValueError("Unsupported content type")
    if not raw_content:
        raise ValueError("Content is required")
    if priority not in PRIORITIES:
        raise ValueError("Invalid priority")

    parsed_content = None
    if content_type == "url":
        parsed_content, error = fetcher(raw_content)
        if error:
            raise ValueError(f"Could not read that URL ({error}). Try pasting the text instead.")

    item = ContentInbox(
        user=user,
        content_type=content_type,
        raw_content=raw_content,
        parsed_content=parsed_content,
        context_note=(context_note or None),
        priority=priority,
        status="pending",
        source_label=_make_source_label(content_type, raw_content, parsed_content),
        created_at=datetime.utcnow(),
    )
    session.add(item)
    return item


def list_inbox_items(session, user, status=None, priority=None):
    query = session.query(ContentInbox).filter(ContentInbox.user_id == user.get_id())
    if status:
        query = query.filter(ContentInbox.status == status)
    else:
        query = query.filter(ContentInbox.status.in_(VISIBLE_STATUSES))
    if priority:
        query = query.filter(ContentInbox.priority == priority)
    return query.order_by(ContentInbox.created_at.desc()).all()


def get_inbox_item(session, user, item_id):
    """Fetch an item owned by the user, or None."""
    item = session.get(ContentInbox, item_id)
    if item is None or item.user_id != user.get_id():
        return None
    return item


def update_inbox_item(session, item, data, fetcher=fetch_url_text):
    """Edit content/context/priority — only allowed while still pending (§7.3)."""
    if item.status != "pending":
        raise ValueError("Only pending items can be edited")

    if "priority" in data and data["priority"]:
        if data["priority"] not in PRIORITIES:
            raise ValueError("Invalid priority")
        item.priority = data["priority"]
    if "context_note" in data:
        item.context_note = data["context_note"] or None
    if data.get("raw_content"):
        new_raw = data["raw_content"].strip()
        if item.content_type == "url" and new_raw != item.raw_content:
            parsed, error = fetcher(new_raw)
            if error:
                raise ValueError(f"Could not read that URL ({error}).")
            item.parsed_content = parsed
        item.raw_content = new_raw
        item.source_label = _make_source_label(
            item.content_type, item.raw_content, item.parsed_content
        )
    return item


def toggle_priority(item):
    item.priority = "use_whenever" if item.priority == "post_soon" else "post_soon"
    return item


def skip_inbox_item(item):
    """Take an item out of the active rotation."""
    item.status = "skipped"
    return item


def soft_delete_inbox_item(item):
    item.status = "deleted"
    return item


def inbox_item_to_dict(item):
    return {
        "id": item.id,
        "content_type": item.content_type,
        "raw_content": item.raw_content,
        "parsed_content": item.parsed_content,
        "context_note": item.context_note,
        "priority": item.priority,
        "status": item.status,
        "source_label": item.source_label,
        "used_in_post_id": item.used_in_post_id,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
