from datetime import datetime, timedelta

import pytest

from app.models.database import ContentInbox, Post, Session, User
from app.services.posts import (
    approve_post, discard_post, edit_post, get_versions, publish_post_now,
    regenerate_post, reschedule_post, restore_version,
)
from app.services.scheduler import PREVIEW_WINDOW, publish_due_posts


class FakeLLM:
    def __init__(self, reply="Regenerated content"):
        self.reply = reply

    def chat(self, system, user, **kwargs):
        return self.reply


class FakeLinkedIn:
    def __init__(self, result="urn:li:share:abc"):
        self.result = result

    def create_post(self, token, content):
        return self.result


def _user(session, **kw):
    kw.setdefault("email", "pa@example.com")
    kw.setdefault("onboarding_complete", True)
    user = User(**kw)
    session.add(user)
    session.flush()
    return user


def _draft(session, user, **kw):
    kw.setdefault("status", "scheduled")
    post = Post(user_id=user.id, content="original content", version=1, **kw)
    session.add(post)
    session.flush()
    return post


# --- edit / reschedule / discard ------------------------------------------

def test_edit_caps_length_and_blocks_when_published():
    with Session() as s:
        user = _user(s)
        post = _draft(s, user)
        edit_post(post, "x" * 3500)
        assert len(post.content) == 3000

        post.status = "published"
        with pytest.raises(ValueError):
            edit_post(post, "nope")


def test_reschedule_sets_time():
    with Session() as s:
        user = _user(s)
        post = _draft(s, user, status="draft")
        when = datetime(2026, 7, 1, 9, 0)
        reschedule_post(post, when)
        assert post.scheduled_at == when
        assert post.status == "scheduled"


def test_discard_returns_inbox_item_to_pending():
    with Session() as s:
        user = _user(s)
        item = ContentInbox(user=user, content_type="text_note", raw_content="x",
                            status="in_progress")
        s.add(item)
        s.flush()
        post = _draft(s, user, source_type="content_inbox", inbox_item_id=item.id)
        discard_post(s, post)
        assert post.status == "discarded"
        assert s.get(ContentInbox, item.id).status == "pending"


# --- approve / publish -----------------------------------------------------

def test_approve_marks_approved_and_schedulable():
    with Session() as s:
        user = _user(s, posting_mode="manual_approval")
        post = _draft(s, user)
        now = datetime(2026, 6, 24, 12, 0)
        approve_post(post, now=now)
        assert post.status == "approved"
        assert post.scheduled_at == now


def test_publish_now_publishes_and_marks_inbox_used():
    with Session() as s:
        user = _user(s, linkedin_access_token="tok")
        item = ContentInbox(user=user, content_type="text_note", raw_content="x",
                            status="in_progress")
        s.add(item)
        s.flush()
        post = _draft(s, user, source_type="content_inbox", inbox_item_id=item.id)
        now = datetime(2026, 6, 24, 12, 0)

        ok, error = publish_post_now(s, user, post, FakeLinkedIn("urn:li:share:777"), now=now)
        assert ok and error is None
        assert post.status == "published"
        assert post.linkedin_post_id == "urn:li:share:777"
        assert s.get(ContentInbox, item.id).status == "used"


def test_publish_now_without_token_fails():
    with Session() as s:
        user = _user(s, linkedin_access_token=None)
        post = _draft(s, user)
        ok, error = publish_post_now(s, user, post, FakeLinkedIn())
        assert not ok
        assert "reconnect" in error.lower()


def test_approved_post_publishes_in_manual_mode_via_tick():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user = _user(s, posting_mode="manual_approval", linkedin_access_token="tok")
        post = _draft(s, user, status="approved", scheduled_at=now - timedelta(minutes=1))
        published = publish_due_posts(s, FakeLinkedIn(), now=now)
        assert post in published
        assert post.status == "published"


# --- regenerate / versions / restore --------------------------------------

def test_regenerate_creates_new_version_and_supersedes():
    with Session() as s:
        user = _user(s, posting_mode="auto_post")
        item = ContentInbox(user=user, content_type="text_note", raw_content="seed story",
                            status="in_progress")
        s.add(item)
        s.flush()
        post = _draft(s, user, source_type="content_inbox", inbox_item_id=item.id)
        now = datetime(2026, 6, 24, 12, 0)

        new_post = regenerate_post(s, user, post, FakeLLM("v2 body"), now=now)
        s.commit()

        assert new_post.version == 2
        assert new_post.parent_post_id == post.id
        assert new_post.content == "v2 body"
        assert new_post.scheduled_at == now + PREVIEW_WINDOW  # fresh window
        assert post.status == "superseded"
        assert {p.version for p in get_versions(s, new_post)} == {1, 2}


def test_restore_brings_back_old_content_as_new_version():
    with Session() as s:
        user = _user(s, posting_mode="manual_approval")
        v1 = _draft(s, user, status="superseded")
        v2 = Post(user_id=user.id, content="v2 content", version=2,
                  parent_post_id=v1.id, status="scheduled")
        s.add(v2)
        s.flush()

        restored = restore_version(s, user, v2, v1.id)
        s.commit()

        assert restored.version == 3
        assert restored.content == "original content"  # v1's content
        assert v2.status == "superseded"


# --- routes ----------------------------------------------------------------

def test_post_action_routes_require_login(client):
    assert client.post("/posts/1/approve").status_code in (301, 302)
    assert client.post("/posts/1/publish").status_code in (301, 302)


def test_preview_page_and_actions_via_http(client):
    client.application.extensions["llm_service"] = FakeLLM()
    client.application.extensions["linkedin_api"] = FakeLinkedIn("urn:li:share:xyz")
    client.get("/dev/login")
    with Session() as s:
        user = s.query(User).filter_by(email="dev@ghostpro.local").one()
        user.linkedin_access_token = "tok"
        post = Post(user_id=user.id, content="to publish", version=1, status="scheduled")
        s.add(post)
        s.commit()
        post_id = post.id

    assert client.get(f"/posts/{post_id}").status_code == 200  # preview page
    detail = client.get(f"/posts/{post_id}?format=json").get_json()
    assert detail["versions"][0]["version"] == 1

    resp = client.post(f"/posts/{post_id}/publish")
    assert resp.status_code == 200
    assert resp.get_json()["post"]["status"] == "published"


def test_owned_post_ownership_enforced(client):
    with Session() as s:
        other = User(email="x@x.com")
        s.add(other)
        s.flush()
        post = Post(user_id=other.id, content="theirs", version=1, status="scheduled")
        s.add(post)
        s.commit()
        foreign_id = post.id

    client.get("/dev/login")
    assert client.post(f"/posts/{foreign_id}/approve").status_code == 404
    assert client.get(f"/posts/{foreign_id}?format=json").status_code == 404


def test_legacy_routes_are_gone(client):
    client.get("/dev/login")
    assert client.get("/history").status_code == 404
    assert client.get("/templates").status_code == 404
    assert client.post("/post-to-linkedin/1").status_code == 404
