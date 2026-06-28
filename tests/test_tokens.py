from datetime import datetime, timedelta

from app.models.database import Post, Session, User
from app.services.posts import publish_post_now
from app.services.tokens import ensure_valid_token


class FakeLinkedIn:
    def __init__(self, refresh_result=None, create_result="urn:li:share:1"):
        self.refresh_result = refresh_result
        self.create_result = create_result
        self.refreshed = False

    def refresh_access_token(self, refresh_token):
        self.refreshed = True
        return self.refresh_result

    def create_post(self, token, content):
        self.last_token = token
        return self.create_result


def _user(session, **kw):
    kw.setdefault("email", "tok@example.com")
    user = User(**kw)
    session.add(user)
    session.flush()
    return user


def test_valid_token_is_returned_without_refresh():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user = _user(s, linkedin_access_token="good",
                     token_expires_at=now + timedelta(hours=2))
        api = FakeLinkedIn()
        assert ensure_valid_token(s, user, api, now=now) == "good"
        assert api.refreshed is False


def test_token_without_expiry_is_assumed_valid():
    with Session() as s:
        user = _user(s, linkedin_access_token="legacy", token_expires_at=None)
        api = FakeLinkedIn()
        assert ensure_valid_token(s, user, api) == "legacy"
        assert api.refreshed is False


def test_expired_token_is_refreshed():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user = _user(s, linkedin_access_token="old",
                     linkedin_refresh_token="refresh-1",
                     token_expires_at=now - timedelta(minutes=1))
        api = FakeLinkedIn(refresh_result={"access_token": "new", "refresh_token": "refresh-2",
                                           "expires_in": 3600})
        token = ensure_valid_token(s, user, api, now=now)
        assert token == "new"
        assert user.linkedin_access_token == "new"
        assert user.linkedin_refresh_token == "refresh-2"
        assert user.token_expires_at == now + timedelta(seconds=3600)


def test_expired_token_refresh_failure_returns_none():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user = _user(s, linkedin_access_token="old",
                     linkedin_refresh_token="refresh-1",
                     token_expires_at=now - timedelta(minutes=1))
        assert ensure_valid_token(s, user, FakeLinkedIn(refresh_result=None), now=now) is None


def test_expired_token_without_refresh_token_returns_none():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user = _user(s, linkedin_access_token="old", linkedin_refresh_token=None,
                     token_expires_at=now - timedelta(minutes=1))
        api = FakeLinkedIn()
        assert ensure_valid_token(s, user, api, now=now) is None
        assert api.refreshed is False


def test_publish_refreshes_expired_token_then_publishes():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user = _user(s, linkedin_access_token="old", linkedin_refresh_token="r",
                     token_expires_at=now - timedelta(minutes=1))
        post = Post(user_id=user.id, content="hi", status="scheduled")
        s.add(post)
        s.flush()
        api = FakeLinkedIn(refresh_result={"access_token": "fresh", "expires_in": 3600},
                           create_result="urn:li:share:9")

        ok, error = publish_post_now(s, user, post, api, now=now)
        assert ok and error is None
        assert api.refreshed is True
        assert api.last_token == "fresh"   # published with the refreshed token
        assert post.status == "published"


def test_publish_errors_when_token_unrefreshable():
    with Session() as s:
        now = datetime(2026, 6, 24, 12, 0)
        user = _user(s, linkedin_access_token="old", linkedin_refresh_token="r",
                     token_expires_at=now - timedelta(minutes=1))
        post = Post(user_id=user.id, content="hi", status="scheduled")
        s.add(post)
        s.flush()
        ok, error = publish_post_now(s, user, post, FakeLinkedIn(refresh_result=None), now=now)
        assert not ok
        assert "reconnect" in error.lower()
        assert post.status == "error"
