from datetime import datetime

from app.models.database import Post, Session, User
from app.services.posts import publish_post_now
from app.services.style_profile import REFRESH_EVERY, maybe_refresh_style_profile


class FakeLLM:
    def chat(self, system, user, **kwargs):
        return "Refreshed voice summary."


class FakeLinkedIn:
    def create_post(self, token, content):
        return "urn:li:share:1"


def _user(session, **kw):
    kw.setdefault("email", "refresh@example.com")
    kw.setdefault("onboarding_complete", True)
    user = User(**kw)
    session.add(user)
    session.flush()
    return user


def _publish(session, user, n):
    for i in range(n):
        session.add(Post(user_id=user.id, content=f"published post {i} body text",
                         status="published", published_at=datetime(2026, 6, 24, 12, i % 60)))
    session.flush()


def test_no_refresh_before_threshold():
    with Session() as s:
        user = _user(s)
        _publish(s, user, REFRESH_EVERY - 1)
        s.commit()
        assert maybe_refresh_style_profile(s, user, FakeLLM()) is None


def test_refresh_at_threshold_analyzes_published_posts():
    with Session() as s:
        user = _user(s)
        _publish(s, user, REFRESH_EVERY)
        s.commit()
        profile = maybe_refresh_style_profile(s, user, FakeLLM())
        s.commit()
        assert profile is not None
        assert profile.sample_posts_analyzed == REFRESH_EVERY
        assert profile.raw_style_summary == "Refreshed voice summary."
        assert profile.avg_post_length is not None


def test_publish_now_triggers_refresh_on_tenth_post():
    with Session() as s:
        user = _user(s, linkedin_access_token="tok")
        _publish(s, user, REFRESH_EVERY - 1)  # 9 already published
        pending = Post(user_id=user.id, content="the tenth post body", status="scheduled")
        s.add(pending)
        s.commit()

        ok, _ = publish_post_now(s, user, pending, FakeLinkedIn(), FakeLLM(),
                                 now=datetime(2026, 6, 24, 13, 0))
        s.commit()
        assert ok
        # Reaching 10 published triggers a refresh from the user's own content.
        assert user.style_profile is not None
        assert user.style_profile.sample_posts_analyzed == REFRESH_EVERY
