from datetime import datetime, timedelta, timezone

from app.models.database import (
    ContentInbox, FollowedSource, Notification, Post, ScheduledJob, Session,
    StyleProfile, User,
)
from app.services.account import (
    GRACE_DAYS, hard_delete_user, purge_expired_accounts, soft_delete_account,
)
from app.services.scheduler import ensure_schedule, run_due_generations


class FakeLLM:
    def chat(self, *a, **k):
        return "post"


def _populated_user(session, email="del@example.com"):
    user = User(email=email, onboarding_complete=True, posting_mode="auto_post")
    user.style_profile = StyleProfile(tone="Direct")
    session.add(user)
    session.flush()
    session.add_all([
        ContentInbox(user=user, content_type="text_note", raw_content="x", status="pending"),
        FollowedSource(user=user, source_type="website", source_url="https://x.com"),
        Post(user_id=user.id, content="a post", status="published"),
        ScheduledJob(user=user, status="active"),
        Notification(user_id=user.id, type="info", title="hi"),
    ])
    session.flush()
    return user


def test_soft_delete_sets_timestamp_and_pauses_schedule():
    with Session() as s:
        user = _populated_user(s)
        ensure_schedule(s, user)
        s.commit()
        soft_delete_account(s, user, now=datetime(2026, 6, 24, 12, 0))
        s.commit()
        assert user.deleted_at is not None
        assert s.query(ScheduledJob).filter_by(user_id=user.id).one().status == "paused"


def test_hard_delete_cascades_all_owned_rows():
    with Session() as s:
        user = _populated_user(s)
        s.commit()
        uid = user.id
        hard_delete_user(s, user)
        s.commit()

        assert s.get(User, uid) is None
        for model in (StyleProfile, ContentInbox, FollowedSource, Post, ScheduledJob, Notification):
            assert s.query(model).filter_by(user_id=uid).count() == 0


def test_purge_only_removes_expired():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        recent = _populated_user(s, "recent@example.com")
        recent.deleted_at = now - timedelta(days=GRACE_DAYS - 1)
        expired = _populated_user(s, "expired@example.com")
        expired.deleted_at = now - timedelta(days=GRACE_DAYS + 1)
        active = _populated_user(s, "active@example.com")  # not deleted
        s.commit()

        removed = purge_expired_accounts(s, now=now)
        s.commit()
        assert removed == 1
        emails = {u.email for u in s.query(User).all()}
        assert "expired@example.com" not in emails
        assert {"recent@example.com", "active@example.com"} <= emails


def test_scheduler_skips_soft_deleted_user():
    with Session() as s:
        user = _populated_user(s)
        job = ensure_schedule(s, user)
        job.next_run_at = datetime(2020, 1, 1)
        soft_delete_account(s, user)
        # Re-activate the job to prove the user-level guard (not just pause) skips it.
        job.status = "active"
        s.commit()
        created = run_due_generations(s, FakeLLM(), now=datetime.now(timezone.utc).replace(tzinfo=None))
        assert created == []


# --- routes ----------------------------------------------------------------

def test_account_delete_requires_login(client):
    assert client.delete("/account").status_code in (301, 302)


def test_account_delete_soft_deletes_and_logs_out(client):
    client.get("/dev/login")
    client.post("/onboarding/save", json={"name": "Dev"})
    assert client.get("/dashboard").status_code == 200

    assert client.delete("/account").status_code == 200

    with Session() as s:
        user = s.query(User).filter_by(email="dev@ghostpro.local").one()
        assert user.deleted_at is not None

    # Session is now treated as logged out -> protected route redirects.
    assert client.get("/dashboard").status_code == 302
