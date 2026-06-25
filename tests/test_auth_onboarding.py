from app.models.database import Session, User
from app.services.users import save_onboarding, upsert_user_from_userinfo
from app.utils.crypto import decrypt

ONBOARDING_PAYLOAD = {
    "name": "Jane Exec", "title": "VP Sales", "company": "Acme", "industry": "SaaS",
    "audience_description": "B2B buyers", "content_goal": "thought leadership",
    "top_topics": ["Industry news", "Career lessons"], "avoid_topics": ["politics"],
    "preferred_length": "medium", "tone": "Conversational",
    "post_frequency": "twice_weekly", "preferred_days": ["Mon", "Wed"],
    "preferred_time": "09:00", "timezone": "America/New_York",
    "posting_mode": "auto_post", "notification_email": True, "notification_inapp": False,
    "bio": "Sales leader.", "emoji_usage": 3, "hashtag_count": 2,
}


# --- HTTP flow -------------------------------------------------------------

def test_landing_shows_connect_for_anonymous(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Connect with LinkedIn" in resp.data


def test_protected_route_redirects_anonymous(client):
    resp = client.get("/dashboard")
    assert resp.status_code == 302
    assert "/" in resp.headers["Location"]


def test_dev_login_creates_user_and_routes_to_onboarding(client):
    resp = client.get("/dev/login")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/onboarding")
    with Session() as s:
        assert s.query(User).filter_by(email="dev@ghostpro.local").count() == 1


def test_dev_login_disabled_outside_development(client, monkeypatch):
    monkeypatch.setenv("FLASK_ENV", "production")
    assert client.get("/dev/login").status_code == 404


def test_dashboard_requires_onboarding_then_succeeds(client):
    client.get("/dev/login")
    # Before onboarding, dashboard bounces to the wizard.
    pre = client.get("/dashboard")
    assert pre.status_code == 302
    assert pre.headers["Location"].endswith("/onboarding")

    save = client.post("/onboarding/save", json=ONBOARDING_PAYLOAD)
    assert save.status_code == 200
    assert save.get_json()["status"] == "success"

    post = client.get("/dashboard")
    assert post.status_code == 200
    assert b"Jane Exec" in post.data


def test_prefill_returns_known_fields(client):
    client.get("/dev/login")
    data = client.get("/onboarding/prefill").get_json()
    assert data["name"] == "Dev User"
    assert "timezone" in data


def test_index_redirects_onboarded_user_to_dashboard(client):
    client.get("/dev/login")
    client.post("/onboarding/save", json=ONBOARDING_PAYLOAD)
    resp = client.get("/")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dashboard")


# --- Service units ---------------------------------------------------------

def test_save_onboarding_persists_user_and_style():
    with Session() as s:
        user = User(email="svc@example.com")
        s.add(user)
        s.flush()
        save_onboarding(s, user, ONBOARDING_PAYLOAD)
        s.commit()
        uid = user.id

    with Session() as s:
        user = s.get(User, uid)
        assert user.onboarding_complete is True
        assert user.title == "VP Sales"
        assert user.preferred_days == ["Mon", "Wed"]
        assert user.notification_inapp is False
        assert user.style_profile.tone == "Conversational"
        assert user.style_profile.emoji_usage == 3
        assert user.style_profile.top_topics == ["Industry news", "Career lessons"]
        assert user.style_profile.avoid_topics == ["politics"]


def test_upsert_user_from_userinfo_creates_then_updates():
    userinfo = {"sub": "li-123", "name": "Pat", "email": "pat@example.com"}
    with Session() as s:
        user = upsert_user_from_userinfo(
            s, userinfo, {"access_token": "tok-1", "refresh_token": "ref-1", "expires_in": 3600}
        )
        s.commit()
        uid = user.id
        # Token is stored encrypted but reads back as plaintext through the ORM.
        assert user.linkedin_access_token == "tok-1"
        assert user.token_expires_at is not None

    # Same email -> same row, token refreshed.
    with Session() as s:
        user = upsert_user_from_userinfo(
            s, userinfo, {"access_token": "tok-2"}
        )
        s.commit()
        assert user.id == uid
        assert user.linkedin_access_token == "tok-2"

    # Ciphertext on disk is not the plaintext token.
    with Session() as s:
        from sqlalchemy import text
        stored = s.execute(
            text("SELECT linkedin_access_token FROM users WHERE id = :i"), {"i": uid}
        ).scalar_one()
    assert stored != "tok-2"
    assert decrypt(stored) == "tok-2"
