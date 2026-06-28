# app/services/posts.py
# Preview/approval actions on Posts (§2.1, §7.4, §9.3): approve, publish,
# edit, discard, reschedule, regenerate (with version history), and restore.

from datetime import datetime
from ..utils.timeutil import utcnow

from ..models.database import ContentInbox, Post
from .generation import compose_post_content, post_process, post_to_dict
from .notifications import notify_failed, notify_preview, notify_published
from .scheduler import PREVIEW_WINDOW
from .source_selection import Source
from .style_profile import maybe_refresh_style_profile
from .tokens import ensure_valid_token

# Statuses past which a post can no longer be edited/approved/etc.
_TERMINAL = {"published", "discarded"}


def _root_id(post):
    return post.parent_post_id or post.id


def get_versions(session, post):
    """All posts in this post's version group, oldest version first."""
    root = _root_id(post)
    return (
        session.query(Post)
        .filter((Post.id == root) | (Post.parent_post_id == root))
        .order_by(Post.version.asc())
        .all()
    )


def _return_inbox_item(session, post, status):
    if post.inbox_item_id:
        item = session.get(ContentInbox, post.inbox_item_id)
        if item:
            item.status = status
        return item
    return None


def edit_post(post, content):
    if post.status in _TERMINAL:
        raise ValueError("This post can no longer be edited")
    post.content = post_process(content or "")
    return post


def reschedule_post(post, when):
    if post.status in _TERMINAL:
        raise ValueError("This post can no longer be rescheduled")
    post.scheduled_at = when
    if post.status not in ("scheduled", "approved"):
        post.status = "scheduled"
    return post


def discard_post(session, post):
    """Discard a queued post; return its inbox item to the queue (§9.3)."""
    if post.status == "published":
        raise ValueError("Published posts cannot be discarded")
    post.status = "discarded"
    if post.inbox_item_id:
        item = session.get(ContentInbox, post.inbox_item_id)
        if item and item.status == "in_progress":
            item.status = "pending"
    return post


def approve_post(post, now=None):
    """Approve a queued post to go live — the publish tick picks it up promptly."""
    if post.status in _TERMINAL:
        raise ValueError("This post cannot be approved")
    now = now or utcnow()
    post.status = "approved"
    post.scheduled_at = now
    return post


def publish_post_now(session, user, post, linkedin_api, openai_service=None, now=None):
    """Publish immediately to LinkedIn. Returns (ok, error)."""
    if post.status == "published":
        return False, "Already published"
    now = now or utcnow()
    token = ensure_valid_token(session, user, linkedin_api, now=now)
    if not token:
        post.status = "error"
        notify_failed(session, user, post)
        return False, "LinkedIn connection expired — please reconnect."

    result = linkedin_api.create_post(token, post.content)
    if not result:
        return False, "LinkedIn rejected the post — try again."

    post.status = "published"
    post.published_at = now
    post.posted_at = now
    post.linkedin_post_id = result if isinstance(result, str) else None
    item = _return_inbox_item(session, post, "used")
    if item is not None:
        item.used_at = now
        item.used_in_post_id = post.id
    notify_published(session, user, post)
    if openai_service is not None:
        maybe_refresh_style_profile(session, user, openai_service)
    return True, None


def regenerate_post(session, user, post, openai_service, now=None):
    """Create a fresh version from the same source, supersede the old one, and
    restart the preview window (§9.3). Returns the new Post or None on failure."""
    if post.status in _TERMINAL:
        raise ValueError("This post can no longer be regenerated")
    now = now or utcnow()
    source = _source_from_post(session, post)
    content, generation_prompt = compose_post_content(user, source, openai_service)
    if content is None:
        return None

    versions = get_versions(session, post)
    next_version = max(p.version for p in versions) + 1

    new_post = Post(
        user_id=user.get_id(),
        content=content,
        version=next_version,
        parent_post_id=_root_id(post),
        status="scheduled",
        source_type=post.source_type,
        inbox_item_id=post.inbox_item_id,
        source_topic=post.source_topic,
        generation_prompt=generation_prompt,
        notification_sent_at=now,
        created_at=now,
    )
    if (user.posting_mode or "manual_approval") == "auto_post":
        new_post.scheduled_at = now + PREVIEW_WINDOW
    session.add(new_post)
    post.status = "superseded"

    session.flush()
    notify_preview(session, user, new_post)
    return new_post


def restore_version(session, user, post, version_id, now=None):
    """Restore a previous version's content as a new active version."""
    now = now or utcnow()
    target = session.get(Post, version_id)
    if target is None or _root_id(target) != _root_id(post) or target.user_id != user.get_id():
        raise ValueError("That version does not belong to this post")

    versions = get_versions(session, post)
    next_version = max(p.version for p in versions) + 1
    current = next((p for p in versions if p.status not in ("superseded", "discarded")), post)

    new_post = Post(
        user_id=user.get_id(),
        content=target.content,
        version=next_version,
        parent_post_id=_root_id(post),
        status="scheduled",
        source_type=target.source_type,
        inbox_item_id=target.inbox_item_id,
        source_topic=target.source_topic,
        created_at=now,
    )
    if (user.posting_mode or "manual_approval") == "auto_post":
        new_post.scheduled_at = now + PREVIEW_WINDOW
    session.add(new_post)
    current.status = "superseded"
    return new_post


def _source_from_post(session, post):
    """Reconstruct the original Source so regeneration uses the same material."""
    if post.source_type == "content_inbox" and post.inbox_item_id:
        item = session.get(ContentInbox, post.inbox_item_id)
        if item:
            text = item.raw_content
            if item.content_type == "url" and item.parsed_content:
                text = item.parsed_content
            return Source("content_inbox", text, inbox_item=item,
                          context_note=item.context_note, label=item.source_label)
    return Source(post.source_type, post.source_topic or "", label=None)


def post_detail_to_dict(session, post):
    """Single post + its version history for the preview page."""
    from .notifications import build_source_label

    data = post_to_dict(post)
    data["source_label"] = build_source_label(session, post)
    data["versions"] = [
        {
            "id": v.id,
            "version": v.version,
            "status": v.status,
            "char_count": len(v.content or ""),
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in get_versions(session, post)
    ]
    return data
