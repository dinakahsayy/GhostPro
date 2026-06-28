# app/celery_app.py
# Optional Celery + Redis execution path for Phase 4 scale, replacing APScheduler.
# Each periodic job is a task on its own queue (generation / publishing /
# source_watch / maintenance) so workers can be scaled independently.
#
# Run (with GHOSTPRO_SCHEDULER=0 so APScheduler doesn't double-fire):
#   celery -A app.celery_app.celery worker -Q generation,publishing,source_watch,maintenance
#   celery -A app.celery_app.celery beat
#
# The task bodies reuse the same service functions the APScheduler ticks call, so
# behavior is identical regardless of execution path.

import os

from celery import Celery
from celery.schedules import crontab

_broker = os.getenv("CELERY_BROKER_URL") or os.getenv("REDIS_URL", "redis://localhost:6379/0")
_backend = os.getenv("CELERY_RESULT_BACKEND") or _broker

celery = Celery("ghostpro", broker=_broker, backend=_backend)
celery.conf.task_routes = {
    "ghostpro.generate_due": {"queue": "generation"},
    "ghostpro.publish_due": {"queue": "publishing"},
    "ghostpro.watch_sources": {"queue": "source_watch"},
    "ghostpro.purge_accounts": {"queue": "maintenance"},
}
celery.conf.beat_schedule = {
    "generate-due": {"task": "ghostpro.generate_due", "schedule": 60.0},
    "publish-due": {"task": "ghostpro.publish_due", "schedule": 60.0},
    "watch-sources": {"task": "ghostpro.watch_sources", "schedule": crontab(hour=6, minute=0)},
    "purge-accounts": {"task": "ghostpro.purge_accounts", "schedule": crontab(hour=3, minute=0)},
}

_flask_app = None


def _get_app():
    global _flask_app
    if _flask_app is None:
        from app import create_app
        _flask_app = create_app()
    return _flask_app


def _run(fn):
    """Run a (session, *services) callable inside an app context + DB session."""
    from app.models.database import Session
    app = _get_app()
    with app.app_context():
        with Session() as session:
            try:
                fn(session, app)
                session.commit()
            except Exception:
                session.rollback()
                raise


@celery.task(name="ghostpro.generate_due")
def generate_due():
    from app.services.scheduler import run_due_generations
    _run(lambda s, app: run_due_generations(s, app.extensions["llm_service"]))


@celery.task(name="ghostpro.publish_due")
def publish_due():
    from app.services.scheduler import publish_due_posts
    _run(lambda s, app: publish_due_posts(
        s, app.extensions["linkedin_api"], app.extensions["llm_service"]))


@celery.task(name="ghostpro.watch_sources")
def watch_sources():
    from app.services.source_watcher import run_source_watch
    _run(lambda s, app: run_source_watch(s))


@celery.task(name="ghostpro.purge_accounts")
def purge_accounts():
    from app.services.account import purge_expired_accounts
    _run(lambda s, app: purge_expired_accounts(s))
