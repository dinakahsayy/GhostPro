from datetime import datetime, timedelta

from app.models.database import ContentInbox, Notification, Post, ScheduledJob, Session, User
from app.services import notifications as notif
from app.services.notifications import (
    build_source_label, notify_preview, send_email,
)
from app.services.scheduler import generate_scheduled_post


class FakeLLM:
    def chat(self, system, user, **kwargs):
        return "A scheduled post body."


def _user(session, **kw):
    kw.setdefault("email", "notify@example.com")
    kw.setdefault("onboarding_complete", True)
    user = User(**kw)
    session.add(user)
    session.flush()
    return user


# --- source label ----------------------------------------------------------

def test_source_label_for_inbox_and_topic():
    with Session() as s:
        user = _user(s)
        item = ContentInbox(user=user, content_type="text_note", raw_content="x",
                            source_label="client win story", status="in_progress")
        s.add(item)
        s.flush()
        inbox_post = Post(user_id=user.id, content="c", source_type="content_inbox",
                          inbox_item_id=item.id)
        topic_post = Post(user_id=user.id, content="c", source_type="user_topic",
                          source_topic="remote work")
        s.add_all([inbox_post, topic_post])
        s.flush()
        assert "client win story" in build_source_label(s, inbox_post)
        assert "remote work" in build_source_label(s, topic_post)


# --- email graceful no-op --------------------------------------------------

def test_send_email_without_config_returns_false(monkeypatch):
    monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
    assert send_email("a@b.com", "subject", "body") is False


# --- preference gating -----------------------------------------------------

def test_notify_preview_respects_inapp_pref(monkeypatch):
    sent = []
    monkeypatch.setattr(notif, "send_email", lambda *a, **k: sent.append(a) or True)

    with Session() as s:
        user = _user(s, notification_inapp=True, notification_email=False,
                     posting_mode="auto_post")
        post = Post(user_id=user.id, content="hello world", source_type="seasonal", status="scheduled")
        s.add(post)
        s.flush()
        notify_preview(s, user, post)
        s.commit()

        notes = s.query(Notification).filter_by(user_id=user.id).all()
        assert len(notes) == 1
        assert notes[0].type == "preview"
        assert sent == []  # email disabled


def test_notify_preview_respects_email_pref(monkeypatch):
    sent = []
    monkeypatch.setattr(notif, "send_email", lambda to, subj, body: sent.append(to) or True)

    with Session() as s:
        user = _user(s, email="mailme@example.com", notification_inapp=False,
                     notification_email=True, posting_mode="manual_approval")
        post = Post(user_id=user.id, content="hello", source_type="seasonal", status="scheduled")
        s.add(post)
        s.flush()
        notify_preview(s, user, post)
        s.commit()

        assert s.query(Notification).filter_by(user_id=user.id).count() == 0  # in-app off
        assert sent == ["mailme@example.com"]


# --- scheduler integration -------------------------------------------------

def test_scheduled_generation_creates_preview_notification():
    with Session() as s:
        user = _user(s, posting_mode="auto_post", notification_inapp=True)
        s.add(ContentInbox(user=user, content_type="text_note", raw_content="big news",
                           source_label="big news", priority="post_soon", status="pending"))
        s.commit()

        post = generate_scheduled_post(s, user, FakeLLM(), now=datetime(2026, 6, 24, 12, 0))
        s.commit()

        note = s.query(Notification).filter_by(user_id=user.id, type="preview").one()
        assert note.post_id == post.id
        assert "big news" in note.source_label


# --- routes ----------------------------------------------------------------

def test_notification_routes_require_login(client):
    assert client.get("/notifications?format=json").status_code in (301, 302)
    assert client.get("/notifications/unread_count").status_code in (301, 302)


def test_notifications_list_read_and_unread_count(client):
    client.get("/dev/login")
    # Seed two notifications for the logged-in dev user.
    with Session() as s:
        user = s.query(User).filter_by(email="dev@ghostpro.local").one()
        s.add_all([
            Notification(user_id=user.id, type="preview", title="One", read=False),
            Notification(user_id=user.id, type="published", title="Two", read=False),
        ])
        s.commit()

    assert client.get("/notifications/unread_count").get_json()["unread"] == 2

    items = client.get("/notifications?format=json").get_json()
    assert len(items) == 2

    first_id = items[0]["id"]
    assert client.post(f"/notifications/{first_id}/read").status_code == 200
    assert client.get("/notifications/unread_count").get_json()["unread"] == 1

    assert client.post("/notifications/read_all").status_code == 200
    assert client.get("/notifications/unread_count").get_json()["unread"] == 0


def test_notification_ownership(client):
    with Session() as s:
        other = User(email="stranger@example.com")
        s.add(other)
        s.flush()
        note = Notification(user_id=other.id, type="info", title="secret", read=False)
        s.add(note)
        s.commit()
        foreign_id = note.id

    client.get("/dev/login")
    assert client.post(f"/notifications/{foreign_id}/read").status_code == 404
