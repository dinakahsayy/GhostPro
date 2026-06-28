from app.models.database import ScheduledJob, Session, StyleProfile, User
from app.services.scheduler import ensure_schedule
from app.services.settings import settings_to_dict, update_settings


def _user(session, **kw):
    kw.setdefault("email", "set@example.com")
    kw.setdefault("onboarding_complete", True)
    kw.setdefault("post_frequency", "weekly")
    kw.setdefault("timezone", "UTC")
    user = User(**kw)
    session.add(user)
    session.flush()
    return user


def test_settings_to_dict_includes_schedule_and_style():
    with Session() as s:
        user = _user(s)
        user.style_profile = StyleProfile(tone="Direct", preferred_length="short", top_topics=["ai"])
        ensure_schedule(s, user)
        s.commit()
        data = settings_to_dict(s, user)
        assert data["post_frequency"] == "weekly"
        assert data["schedule_status"] == "active"
        assert data["style"]["tone"] == "Direct"


def test_update_settings_recomputes_schedule_on_cadence_change():
    with Session() as s:
        user = _user(s, post_frequency="weekly", preferred_days=["Mon"])
        ensure_schedule(s, user)
        s.commit()
        before = s.query(ScheduledJob).filter_by(user_id=user.id).one().next_run_at

        update_settings(s, user, {
            "post_frequency": "daily", "preferred_time": "07:00",
            "posting_mode": "auto_post", "notification_email": False,
        })
        s.commit()

        assert user.post_frequency == "daily"
        assert user.posting_mode == "auto_post"
        assert user.notification_email is False
        after = s.query(ScheduledJob).filter_by(user_id=user.id).one().next_run_at
        assert after != before  # schedule re-armed


def test_update_settings_does_not_resurrect_paused_schedule():
    with Session() as s:
        user = _user(s)
        job = ensure_schedule(s, user)
        job.status = "paused"
        s.commit()

        update_settings(s, user, {"post_frequency": "daily"})
        s.commit()
        assert s.query(ScheduledJob).filter_by(user_id=user.id).one().status == "paused"


# --- routes ----------------------------------------------------------------

def test_settings_routes_require_login(client):
    assert client.get("/settings?format=json").status_code in (301, 302)
    assert client.post("/settings/pause").status_code in (301, 302)


def test_settings_flow_via_http(client):
    client.get("/dev/login")
    client.post("/onboarding/save", json={"name": "Dev", "post_frequency": "weekly",
                                          "preferred_days": ["Mon"], "timezone": "UTC"})

    data = client.get("/settings?format=json").get_json()
    assert data["schedule_status"] == "active"

    # Update via PUT.
    resp = client.put("/settings", json={"post_frequency": "daily", "posting_mode": "auto_post"})
    assert resp.status_code == 200
    assert resp.get_json()["settings"]["post_frequency"] == "daily"

    # Pause / resume.
    assert client.post("/settings/pause").get_json()["settings"]["schedule_status"] == "paused"
    assert client.post("/settings/resume").get_json()["settings"]["schedule_status"] == "active"
