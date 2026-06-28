from app import _init_sentry, create_app


def test_celery_tasks_registered():
    from app.celery_app import celery

    expected = {
        "ghostpro.generate_due",
        "ghostpro.publish_due",
        "ghostpro.watch_sources",
        "ghostpro.purge_accounts",
    }
    assert expected <= set(celery.tasks.keys())


def test_celery_queues_routed_separately():
    from app.celery_app import celery

    routes = celery.conf.task_routes
    assert routes["ghostpro.generate_due"]["queue"] == "generation"
    assert routes["ghostpro.publish_due"]["queue"] == "publishing"
    assert routes["ghostpro.watch_sources"]["queue"] == "source_watch"
    assert routes["ghostpro.purge_accounts"]["queue"] == "maintenance"


def test_sentry_init_is_noop_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    _init_sentry()  # must not raise


def test_app_boots_without_sentry(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert create_app() is not None
