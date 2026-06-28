from datetime import datetime, timedelta

from app.models.database import Post, Session, User
from app.services.dashboard import analytics_summary, calendar_events, sync_engagement


class FakeLinkedIn:
    def __init__(self, metrics=None):
        self.metrics = metrics

    def get_engagement(self, token, urn):
        return self.metrics


def _user(session, **kw):
    kw.setdefault("email", "dash@example.com")
    kw.setdefault("onboarding_complete", True)
    user = User(**kw)
    session.add(user)
    session.flush()
    return user


def test_calendar_splits_upcoming_and_past():
    with Session() as s:
        user = _user(s)
        s.add_all([
            Post(user_id=user.id, content="scheduled one", status="scheduled",
                 scheduled_at=datetime(2026, 7, 1, 9, 0)),
            Post(user_id=user.id, content="published one", status="published",
                 published_at=datetime(2026, 6, 1, 9, 0)),
            Post(user_id=user.id, content="a draft", status="draft"),       # excluded
            Post(user_id=user.id, content="superseded", status="superseded"),  # excluded
        ])
        s.commit()

        events = calendar_events(s, user)
        assert len(events) == 2
        upcoming = [e for e in events if e["is_upcoming"]]
        past = [e for e in events if not e["is_upcoming"]]
        assert len(upcoming) == 1 and upcoming[0]["status"] == "scheduled"
        assert len(past) == 1 and past[0]["status"] == "published"


def test_analytics_summary_aggregates():
    with Session() as s:
        user = _user(s)
        s.add_all([
            Post(user_id=user.id, content="a", status="published",
                 likes_count=10, comments_count=2, shares_count=1),
            Post(user_id=user.id, content="b", status="published",
                 likes_count=4, comments_count=0, shares_count=1),
            Post(user_id=user.id, content="draft", status="draft", likes_count=99),  # ignored
        ])
        s.commit()

        stats = analytics_summary(s, user)
        assert stats["published_count"] == 2
        assert stats["likes"] == 14
        assert stats["comments"] == 2
        assert stats["shares"] == 2
        assert stats["total_engagement"] == 18
        assert stats["avg_engagement"] == 9.0


def test_sync_engagement_updates_counts():
    with Session() as s:
        user = _user(s, linkedin_access_token="tok")
        post = Post(user_id=user.id, content="x", status="published",
                    linkedin_post_id="urn:li:share:1")
        s.add(post)
        s.commit()

        updated = sync_engagement(s, user, FakeLinkedIn({"likes": 7, "comments": 3, "shares": 0}))
        s.commit()
        assert updated == 1
        assert post.likes_count == 7
        assert post.comments_count == 3


def test_sync_engagement_without_token_is_noop():
    with Session() as s:
        user = _user(s, linkedin_access_token=None)
        post = Post(user_id=user.id, content="x", status="published",
                    linkedin_post_id="urn:li:share:1")
        s.add(post)
        s.commit()
        assert sync_engagement(s, user, FakeLinkedIn({"likes": 5})) == 0


# --- routes ----------------------------------------------------------------

def test_dashboard_routes_require_login(client):
    assert client.get("/dashboard/calendar").status_code in (301, 302)
    assert client.post("/dashboard/sync").status_code in (301, 302)


def test_dashboard_endpoints_via_http(client):
    client.application.extensions["linkedin_api"] = FakeLinkedIn({"likes": 5, "comments": 1, "shares": 0})
    client.get("/dev/login")
    with Session() as s:
        user = s.query(User).filter_by(email="dev@ghostpro.local").one()
        user.linkedin_access_token = "tok"
        s.add(Post(user_id=user.id, content="published post", status="published",
                   linkedin_post_id="urn:li:share:9"))
        s.commit()

    assert client.get("/dashboard/calendar").status_code == 200
    assert client.get("/dashboard/analytics").get_json()["published_count"] == 1

    synced = client.post("/dashboard/sync").get_json()
    assert synced["updated"] == 1
    assert client.get("/dashboard/analytics").get_json()["likes"] == 5
