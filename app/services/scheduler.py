# app/services/scheduler.py
# Per-user post scheduling (§9). Design is DB-driven: every user has a
# ScheduledJob row whose next_run_at says when their next post should be
# generated. Two lightweight periodic "ticks" scan for due work:
#   - run_due_generations(): generate posts whose schedule is due
#   - publish_due_posts():   auto-publish posts past their 2-hour preview window
# All scheduling logic is pure/testable; APScheduler (start_scheduler) is a thin
# wrapper that calls these on an interval. Celery + Redis replaces it in Phase 4.

import os
from datetime import datetime, time, timedelta, timezone
from ..utils.timeutil import utcnow
from zoneinfo import ZoneInfo

from ..models.database import ContentInbox, Post, ScheduledJob, Session, User
from .generation import generate_post_for_user
from .notifications import notify_failed, notify_preview, notify_published
from .style_profile import maybe_refresh_style_profile
from .tokens import ensure_valid_token

PREVIEW_WINDOW = timedelta(hours=2)      # §9.1 auto-post preview countdown
RETRY_BACKOFF = timedelta(minutes=5)     # §9.1 publish retry backoff
MAX_PUBLISH_RETRIES = 3                   # §2.2

_DAY_TO_INT = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}
_MIN_INTERVAL = {
    "daily": timedelta(days=1),
    "twice_weekly": timedelta(days=3),
    "weekly": timedelta(days=7),
    "biweekly": timedelta(days=14),
    "custom": timedelta(days=7),
}
_DEFAULT_DAYS = {
    "daily": [0, 1, 2, 3, 4, 5, 6],
    "twice_weekly": [0, 3],
    "weekly": [0],
    "biweekly": [0],
    "custom": [0],
}


def _allowed_weekdays(user):
    if user.preferred_days:
        days = {_DAY_TO_INT[d] for d in user.preferred_days if d in _DAY_TO_INT}
        if days:
            return days
    return set(_DEFAULT_DAYS.get(user.post_frequency or "weekly", [0]))


def _parse_time(value):
    if value and ":" in value:
        try:
            hour, minute = value.split(":")[:2]
            return int(hour), int(minute)
        except ValueError:
            pass
    return 9, 0


def compute_next_run(user, now=None, last_run=None):
    """Next post time as a naive-UTC datetime, honoring frequency, preferred
    days/time, and timezone. `last_run` enforces the per-frequency minimum gap."""
    now = now or utcnow()
    try:
        tz = ZoneInfo(user.timezone or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")

    hour, minute = _parse_time(user.preferred_time)
    allowed = _allowed_weekdays(user)
    min_gap = _MIN_INTERVAL.get(user.post_frequency or "weekly", timedelta(days=7))

    earliest = now
    if last_run:
        earliest = max(earliest, last_run + min_gap)

    now_local = now.replace(tzinfo=timezone.utc).astimezone(tz)
    earliest_local = earliest.replace(tzinfo=timezone.utc).astimezone(tz)

    base_date = earliest_local.date()
    for offset in range(0, 22):
        day = base_date + timedelta(days=offset)
        candidate = datetime.combine(day, time(hour, minute), tzinfo=tz)
        if candidate > now_local and candidate >= earliest_local and candidate.weekday() in allowed:
            return candidate.astimezone(timezone.utc).replace(tzinfo=None)

    return now + min_gap  # fallback (shouldn't be reached for normal configs)


def ensure_schedule(session, user, now=None):
    """Create or refresh the user's ScheduledJob (called on onboarding/settings change)."""
    job = session.query(ScheduledJob).filter_by(user_id=user.get_id()).first()
    if job is None:
        job = ScheduledJob(user=user, job_id=f"gen-{user.get_id()}")
        session.add(job)
    job.status = "active"
    job.next_run_at = compute_next_run(user, now=now, last_run=job.last_run_at)
    return job


def pause_schedule(session, user):
    job = session.query(ScheduledJob).filter_by(user_id=user.get_id()).first()
    if job:
        job.status = "paused"
    return job


def resume_schedule(session, user, now=None):
    job = session.query(ScheduledJob).filter_by(user_id=user.get_id()).first()
    if job is None:
        return ensure_schedule(session, user, now=now)
    job.status = "active"
    job.next_run_at = compute_next_run(user, now=now, last_run=job.last_run_at)
    return job


def generate_scheduled_post(session, user, llm_service, now=None):
    """Generate one post for a scheduled slot. Auto-post mode starts the 2-hour
    preview countdown; manual mode leaves it queued for explicit approval."""
    now = now or utcnow()
    post = generate_post_for_user(session, user, llm_service)
    if post is None:
        return None  # inbox empty / Claude failure — slot skipped (§9.4)

    post.status = "scheduled"
    post.notification_sent_at = now
    if (user.posting_mode or "manual_approval") == "auto_post":
        post.scheduled_at = now + PREVIEW_WINDOW
    else:
        post.scheduled_at = None

    session.flush()  # assign post.id before building the notification link
    notify_preview(session, user, post)
    return post


def run_due_generations(session, llm_service, now=None):
    """Generation tick: generate posts for every active schedule that is due."""
    now = now or utcnow()
    jobs = (
        session.query(ScheduledJob)
        .filter(
            ScheduledJob.status == "active",
            ScheduledJob.next_run_at.isnot(None),
            ScheduledJob.next_run_at <= now,
        )
        .all()
    )
    created = []
    for job in jobs:
        user = session.get(User, job.user_id)
        if user is None or user.deleted_at is not None or not user.onboarding_complete:
            continue
        post = generate_scheduled_post(session, user, llm_service, now=now)
        if post is not None:
            created.append(post)
        job.last_run_at = now
        job.next_run_at = compute_next_run(user, now=now, last_run=now)
    return created


def publish_due_posts(session, linkedin_api, llm_service=None, now=None):
    """Publish tick: push due posts to LinkedIn. Auto-post 'scheduled' posts go
    once their 2-hour window elapses; user-'approved' posts go regardless of mode."""
    now = now or utcnow()
    due = (
        session.query(Post)
        .filter(
            Post.status.in_(("scheduled", "approved")),
            Post.scheduled_at.isnot(None),
            Post.scheduled_at <= now,
        )
        .all()
    )
    published = []
    for post in due:
        user = session.get(User, post.user_id)
        if user is None or user.deleted_at is not None:
            continue
        # 'scheduled' posts only auto-publish in auto-post mode; 'approved' posts
        # were explicitly OK'd by the user and publish in any mode.
        if post.status == "scheduled" and (user.posting_mode or "manual_approval") != "auto_post":
            continue

        job = session.query(ScheduledJob).filter_by(user_id=user.get_id()).first()
        token = ensure_valid_token(session, user, linkedin_api, now=now)
        if not token:
            post.status = "error"  # §9.4 — refresh failed / needs reconnect
            notify_failed(session, user, post)
            continue

        result = linkedin_api.create_post(token, post.content)
        if result:
            post.status = "published"
            post.published_at = now
            post.posted_at = now
            post.linkedin_post_id = result if isinstance(result, str) else None
            if post.inbox_item_id:
                item = session.get(ContentInbox, post.inbox_item_id)
                if item:
                    item.status = "used"
                    item.used_at = now
                    item.used_in_post_id = post.id
            if job:
                job.retry_count = 0
            notify_published(session, user, post)
            if llm_service is not None:
                maybe_refresh_style_profile(session, user, llm_service)
            published.append(post)
        else:
            # Retry up to MAX_PUBLISH_RETRIES with backoff, then give up (§9.1).
            retries = (job.retry_count or 0) + 1 if job else MAX_PUBLISH_RETRIES
            if retries >= MAX_PUBLISH_RETRIES:
                post.status = "error"
                if post.inbox_item_id:
                    item = session.get(ContentInbox, post.inbox_item_id)
                    if item:
                        item.status = "pending"  # return to queue
                if job:
                    job.retry_count = 0
                notify_failed(session, user, post)
            else:
                if job:
                    job.retry_count = retries
                post.scheduled_at = now + RETRY_BACKOFF
    return published


def start_scheduler(app):
    """Wire APScheduler interval jobs that drive the two ticks. Called from the
    entrypoint (main.py), not the app factory, so tests don't spawn threads."""
    from apscheduler.schedulers.background import BackgroundScheduler

    interval = int(os.getenv("SCHEDULER_INTERVAL_SECONDS", "60"))
    scheduler = BackgroundScheduler(timezone="UTC")

    def _tick(fn, service_key):
        def run():
            with app.app_context():
                with Session() as session:
                    try:
                        fn(session, app.extensions[service_key])
                        session.commit()
                    except Exception:
                        session.rollback()
                        app.logger.exception("scheduler tick failed")
        return run

    scheduler.add_job(_tick(run_due_generations, "llm_service"),
                      "interval", seconds=interval, id="generation_tick")

    def _publish_tick():
        with app.app_context():
            with Session() as session:
                try:
                    publish_due_posts(
                        session, app.extensions["linkedin_api"], app.extensions["llm_service"]
                    )
                    session.commit()
                except Exception:
                    session.rollback()
                    app.logger.exception("publish tick failed")

    scheduler.add_job(_publish_tick, "interval", seconds=interval, id="publish_tick")

    # Source watcher runs on its own (daily by default) cadence.
    from .source_watcher import run_source_watch

    watch_interval = int(os.getenv("SOURCE_WATCH_INTERVAL_SECONDS", "86400"))

    def _watch_tick():
        with app.app_context():
            with Session() as session:
                try:
                    run_source_watch(session)
                    session.commit()
                except Exception:
                    session.rollback()
                    app.logger.exception("source watch tick failed")

    scheduler.add_job(_watch_tick, "interval", seconds=watch_interval, id="source_watch_tick")

    # Daily purge of accounts whose deletion grace period has elapsed.
    from .account import purge_expired_accounts

    def _purge_tick():
        with app.app_context():
            with Session() as session:
                try:
                    purge_expired_accounts(session)
                    session.commit()
                except Exception:
                    session.rollback()
                    app.logger.exception("account purge tick failed")

    scheduler.add_job(_purge_tick, "interval", seconds=watch_interval, id="account_purge_tick")

    scheduler.start()
    app.extensions["scheduler"] = scheduler
    return scheduler
