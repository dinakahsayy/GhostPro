# app/services/notifications.py
# Preview / publish / failure notifications (§2.1, §9.1). Two channels, each
# gated by the user's preferences:
#   - in-app: a Notification row shown in the bell / notification center
#   - email:  via SendGrid (no-ops gracefully without SENDGRID_API_KEY)
# Every notification carries a source label ("Based on your story about X").

import os
from datetime import datetime

from ..models.database import ContentInbox, Notification

_PREVIEW_CHARS = 220


def _app_base_url():
    return os.getenv("APP_BASE_URL", "http://localhost:8080").rstrip("/")


def build_source_label(session, post):
    """Human-readable description of where a post came from."""
    if post.source_type == "content_inbox" and post.inbox_item_id:
        item = session.get(ContentInbox, post.inbox_item_id)
        if item and item.source_label:
            return f"Based on your story: {item.source_label}"
        return "Based on your Content Inbox"
    if post.source_type == "user_topic" and post.source_topic:
        return f"About: {post.source_topic}"
    return "A timely post for you"


def create_inapp_notification(session, user, type, title, body, source_label=None, post=None):
    note = Notification(
        user_id=user.get_id(),
        post_id=post.id if post else None,
        type=type,
        title=title,
        body=body,
        source_label=source_label,
        read=False,
        created_at=datetime.utcnow(),
    )
    session.add(note)
    return note


def send_email(to_email, subject, body):
    """Send an email via SendGrid. Returns True on send, False if not configured
    or on failure (never raises, so notification flow never breaks)."""
    api_key = os.getenv("SENDGRID_API_KEY")
    from_email = os.getenv("NOTIFICATION_FROM_EMAIL")
    if not api_key or not from_email or not to_email:
        return False
    try:
        from sendgrid import SendGridAPIClient
        from sendgrid.helpers.mail import Mail

        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            plain_text_content=body,
        )
        SendGridAPIClient(api_key).send(message)
        return True
    except Exception:
        return False


def _preview_snippet(post):
    text = (post.content or "").strip()
    return text[:_PREVIEW_CHARS] + ("..." if len(text) > _PREVIEW_CHARS else "")


def _notify(session, user, post, type, title, body, source_label):
    if user.notification_inapp:
        create_inapp_notification(
            session, user, type=type, title=title, body=body,
            source_label=source_label, post=post,
        )
    if user.notification_email:
        send_email(user.email, title, body)


def notify_preview(session, user, post):
    """Sent ~2 hours before an auto-post goes live, or when a manual post is queued."""
    label = build_source_label(session, post)
    link = f"{_app_base_url()}/posts/{post.id}"
    if (user.posting_mode or "manual_approval") == "auto_post":
        title = "Your next LinkedIn post is ready to preview"
        action = "It will publish automatically in 2 hours unless you change it."
    else:
        title = "A new LinkedIn post is waiting for your approval"
        action = "It won't go live until you approve it."
    body = f"{label}\n\n{_preview_snippet(post)}\n\n{action}\nPreview it: {link}"
    _notify(session, user, post, "preview", title, body, label)


def notify_published(session, user, post):
    label = build_source_label(session, post)
    title = "Your LinkedIn post is live"
    body = f"{label}\n\n{_preview_snippet(post)}"
    _notify(session, user, post, "published", title, body, label)


def notify_failed(session, user, post):
    label = build_source_label(session, post)
    title = "We couldn't publish your post"
    body = (
        f"{label}\n\nPublishing failed after several attempts. Your content is "
        f"safe and back in your queue. You may need to reconnect LinkedIn."
    )
    _notify(session, user, post, "error", title, body, label)


def notify_suggestions(session, user, count):
    """In-app only (§9.2): new suggested items are waiting for review."""
    if not user.notification_inapp:
        return
    plural = "s" if count != 1 else ""
    create_inapp_notification(
        session, user, type="suggestion",
        title=f"{count} new suggested item{plural} to review",
        body="New content from your followed sources is waiting in your Suggestions tab.",
    )


def notification_to_dict(note):
    return {
        "id": note.id,
        "post_id": note.post_id,
        "type": note.type,
        "title": note.title,
        "body": note.body,
        "source_label": note.source_label,
        "read": note.read,
        "created_at": note.created_at.isoformat() if note.created_at else None,
    }
