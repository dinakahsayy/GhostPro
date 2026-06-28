from datetime import datetime, timedelta

from app.models.database import ContentInbox, Post, ScheduledJob, Session, User
from app.services.scheduler import (
    PREVIEW_WINDOW, compute_next_run, ensure_schedule, generate_scheduled_post,
    pause_schedule, publish_due_posts, resume_schedule, run_due_generations,
)


class FakeLLM:
    def chat(self, system, user, **kwargs):
        return "A generated scheduled post. #update"


class FakeLinkedIn:
    def __init__(self, result="urn:li:share:123"):
        self.result = result
        self.calls = []

    def create_post(self, token, content):
        self.calls.append((token, content))
        return self.result


def _user(session, **kw):
    kw.setdefault("email", "sched@example.com")
    kw.setdefault("onboarding_complete", True)
    user = User(**kw)
    session.add(user)
    session.flush()
    return user


# --- compute_next_run ------------------------------------------------------

def test_weekly_picks_next_allowed_weekday_at_time():
    user = User(email="w@x.com", post_frequency="weekly",
                preferred_days=["Mon"], preferred_time="09:00", timezone="UTC")
    now = datetime(2026, 6, 24, 12, 0)  # a Wednesday
    nxt = compute_next_run(user, now=now)
    assert nxt.weekday() == 0          # Monday
    assert (nxt.hour, nxt.minute) == (9, 0)
    assert nxt > now


def test_daily_is_within_a_day():
    user = User(email="d@x.com", post_frequency="daily",
                preferred_time="09:00", timezone="UTC")
    now = datetime(2026, 6, 24, 12, 0)
    nxt = compute_next_run(user, now=now)
    assert nxt.hour == 9
    assert now < nxt <= now + timedelta(days=1)


def test_timezone_converts_to_utc():
    user = User(email="tz@x.com", post_frequency="daily",
                preferred_time="09:00", timezone="America/New_York")
    now = datetime(2026, 6, 24, 0, 0)  # June -> EDT (UTC-4)
    nxt = compute_next_run(user, now=now)
    assert nxt.hour == 13  # 09:00 EDT == 13:00 UTC


def test_biweekly_respects_minimum_gap():
    user = User(email="bw@x.com", post_frequency="biweekly",
                preferred_days=["Mon"], preferred_time="09:00", timezone="UTC")
    now = datetime(2026, 6, 24, 12, 0)
    last = datetime(2026, 6, 22, 9, 0)  # a Monday
    nxt = compute_next_run(user, now=now, last_run=last)
    assert nxt >= last + timedelta(days=14)
    assert nxt.weekday() == 0


# --- schedule lifecycle ----------------------------------------------------

def test_ensure_pause_resume_schedule():
    with Session() as s:
        user = _user(s, post_frequency="weekly", preferred_days=["Mon"], timezone="UTC")
        job = ensure_schedule(s, user)
        s.commit()
        assert job.status == "active"
        assert job.next_run_at is not None

        pause_schedule(s, user)
        s.commit()
        assert s.query(ScheduledJob).filter_by(user_id=user.id).one().status == "paused"

        resume_schedule(s, user)
        s.commit()
        assert s.query(ScheduledJob).filter_by(user_id=user.id).one().status == "active"


# --- generate_scheduled_post ----------------------------------------------

def test_auto_mode_starts_preview_window():
    with Session() as s:
        user = _user(s, posting_mode="auto_post")
        s.add(ContentInbox(user=user, content_type="text_note", raw_content="news",
                           priority="post_soon", status="pending"))
        s.commit()
        now = datetime(2026, 6, 24, 12, 0)
        post = generate_scheduled_post(s, user, FakeLLM(), now=now)
        assert post.status == "scheduled"
        assert post.scheduled_at == now + PREVIEW_WINDOW
        assert post.notification_sent_at == now


def test_manual_mode_has_no_auto_publish_time():
    with Session() as s:
        user = _user(s, posting_mode="manual_approval")
        s.add(ContentInbox(user=user, content_type="text_note", raw_content="news",
                           priority="post_soon", status="pending"))
        s.commit()
        post = generate_scheduled_post(s, user, FakeLLM(), now=datetime(2026, 6, 24, 12, 0))
        assert post.status == "scheduled"
        assert post.scheduled_at is None


# --- run_due_generations ---------------------------------------------------

def test_run_due_generations_fires_and_advances():
    with Session() as s:
        user = _user(s, posting_mode="auto_post", post_frequency="weekly",
                     preferred_days=["Mon"], timezone="UTC")
        s.add(ContentInbox(user=user, content_type="text_note", raw_content="story",
                           priority="post_soon", status="pending"))
        job = ScheduledJob(user=user, status="active",
                           next_run_at=datetime(2026, 6, 24, 11, 59))
        s.add(job)
        s.commit()

        now = datetime(2026, 6, 24, 12, 0)
        created = run_due_generations(s, FakeLLM(), now=now)
        s.commit()

        assert len(created) == 1
        assert created[0].status == "scheduled"
        job = s.query(ScheduledJob).filter_by(user_id=user.id).one()
        assert job.last_run_at == now
        assert job.next_run_at > now  # advanced into the future


def test_run_due_skips_not_yet_due():
    with Session() as s:
        user = _user(s, posting_mode="auto_post")
        s.add(ScheduledJob(user=user, status="active",
                           next_run_at=datetime(2026, 6, 25, 9, 0)))
        s.commit()
        created = run_due_generations(s, FakeLLM(), now=datetime(2026, 6, 24, 12, 0))
        assert created == []


# --- publish_due_posts -----------------------------------------------------

def _scheduled_auto_post(session, now, **user_kw):
    user = _user(session, posting_mode="auto_post", linkedin_access_token="tok", **user_kw)
    item = ContentInbox(user=user, content_type="text_note", raw_content="x",
                        priority="post_soon", status="in_progress")
    session.add(item)
    session.flush()
    post = Post(user_id=user.id, content="ready to publish", status="scheduled",
                source_type="content_inbox", inbox_item_id=item.id,
                scheduled_at=now - timedelta(minutes=1))
    session.add(post)
    session.add(ScheduledJob(user=user, status="active"))
    session.commit()
    return user, item, post


def test_publish_due_publishes_and_marks_inbox_used():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user, item, post = _scheduled_auto_post(s, now)
        fake = FakeLinkedIn(result="urn:li:share:999")

        published = publish_due_posts(s, fake, now=now)
        s.commit()

        assert len(published) == 1
        assert post.status == "published"
        assert post.published_at == now
        assert post.linkedin_post_id == "urn:li:share:999"
        assert fake.calls and fake.calls[0][0] == "tok"  # decrypted token passed
        assert s.get(ContentInbox, item.id).status == "used"
        assert s.get(ContentInbox, item.id).used_in_post_id == post.id


def test_publish_skips_manual_mode_posts():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user = _user(s, posting_mode="manual_approval", linkedin_access_token="tok")
        post = Post(user_id=user.id, content="manual", status="scheduled",
                    scheduled_at=now - timedelta(minutes=1))
        s.add(post)
        s.commit()

        assert publish_due_posts(s, FakeLinkedIn(), now=now) == []
        assert post.status == "scheduled"  # left for explicit approval


def test_publish_retries_then_errors_and_returns_item_to_pending():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user, item, post = _scheduled_auto_post(s, now)
        fail = FakeLinkedIn(result=None)

        # First two attempts back off and bump retry_count.
        publish_due_posts(s, fail, now=now); s.commit()
        job = s.query(ScheduledJob).filter_by(user_id=user.id).one()
        assert job.retry_count == 1
        assert post.status == "scheduled"

        post.scheduled_at = now - timedelta(minutes=1)  # make due again
        publish_due_posts(s, fail, now=now); s.commit()
        assert job.retry_count == 2

        # Third attempt gives up: post errors, inbox item returns to pending.
        post.scheduled_at = now - timedelta(minutes=1)
        publish_due_posts(s, fail, now=now); s.commit()
        assert post.status == "error"
        assert s.get(ContentInbox, item.id).status == "pending"
        assert job.retry_count == 0
