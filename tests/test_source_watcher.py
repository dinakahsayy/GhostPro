import types
from datetime import datetime

import pytest

from app.models.database import ContentInbox, FollowedSource, Notification, Session, User
from app.services.inbox import confirm_suggestion, dismiss_suggestion, list_suggestions
from app.services.source_selection import select_source
from app.services.source_watcher import run_source_watch
from app.services.sources import (
    create_source, delete_source, get_source, list_sources, toggle_source,
)


def _user(session, **kw):
    kw.setdefault("email", "watch@example.com")
    kw.setdefault("onboarding_complete", True)
    kw.setdefault("notification_inapp", True)
    user = User(**kw)
    session.add(user)
    session.flush()
    return user


def _feed(*entries):
    """Build a fake feedparser result."""
    return lambda url: types.SimpleNamespace(entries=list(entries))


# --- sources service -------------------------------------------------------

def test_create_source_validates():
    with Session() as s:
        user = _user(s)
        src = create_source(s, user, "rss_feed", "https://blog.example.com/feed", "Acme Blog")
        s.commit()
        assert src.active is True
        with pytest.raises(ValueError):
            create_source(s, user, "carrier_pigeon", "https://x.com")
        with pytest.raises(ValueError):
            create_source(s, user, "website", "http://insecure.example.com")


def test_source_crud_and_ownership():
    with Session() as s:
        owner = _user(s, email="o@example.com")
        other = _user(s, email="p@example.com")
        src = create_source(s, owner, "website", "https://example.com")
        s.commit()
        assert len(list_sources(s, owner)) == 1
        assert get_source(s, other, src.id) is None  # ownership
        toggle_source(src)
        assert src.active is False
        delete_source(s, src)
        s.commit()
        assert list_sources(s, owner) == []


# --- watcher: RSS ----------------------------------------------------------

def test_watch_creates_suggestions_from_rss_and_dedups():
    feed = _feed(
        {"title": "Post A", "link": "https://blog.example.com/a", "summary": "Body A"},
        {"title": "Post B", "link": "https://blog.example.com/b", "summary": "Body B"},
    )
    with Session() as s:
        user = _user(s)
        create_source(s, user, "rss_feed", "https://blog.example.com/feed", "Blog")
        s.commit()

        new = run_source_watch(s, feed_parser=feed, now=datetime(2026, 6, 24, 12, 0))
        s.commit()
        assert new[user.id] == 2

        suggestions = list_suggestions(s, user)
        assert len(suggestions) == 2
        assert all(x.status == "pending_confirmation" and x.content_type == "suggested" for x in suggestions)
        # An in-app notification was raised.
        assert s.query(Notification).filter_by(user_id=user.id, type="suggestion").count() == 1
        # last_checked_at recorded.
        assert s.query(FollowedSource).one().last_checked_at is not None

        # Second run with the same feed creates nothing new (dedup by link).
        again = run_source_watch(s, feed_parser=feed, now=datetime(2026, 6, 25, 12, 0))
        s.commit()
        assert again == {}
        assert len(list_suggestions(s, user)) == 2


def test_watch_skips_inactive_sources():
    feed = _feed({"title": "X", "link": "https://b.example.com/x", "summary": "x"})
    with Session() as s:
        user = _user(s)
        src = create_source(s, user, "rss_feed", "https://b.example.com/feed")
        src.active = False
        s.commit()
        assert run_source_watch(s, feed_parser=feed) == {}
        assert list_suggestions(s, user) == []


# --- watcher: website ------------------------------------------------------

def test_watch_website_uses_fetcher_once():
    fetcher = lambda url: ("Acme news headline\n\nFull article text.", None)
    with Session() as s:
        user = _user(s)
        create_source(s, user, "website", "https://news.example.com", "Acme News")
        s.commit()

        run_source_watch(s, feed_parser=_feed(), fetcher=fetcher, now=datetime(2026, 6, 24, 12, 0))
        s.commit()
        items = list_suggestions(s, user)
        assert len(items) == 1
        assert items[0].parsed_content.startswith("Acme news headline")

        # De-duped on the next run.
        run_source_watch(s, feed_parser=_feed(), fetcher=fetcher)
        s.commit()
        assert len(list_suggestions(s, user)) == 1


# --- confirm / dismiss + selection ----------------------------------------

def test_confirm_makes_suggestion_selectable():
    feed = _feed({"title": "Big news", "link": "https://b.example.com/n", "summary": "Article body"})
    with Session() as s:
        user = _user(s)
        create_source(s, user, "rss_feed", "https://b.example.com/feed")
        s.commit()
        run_source_watch(s, feed_parser=feed)
        s.commit()

        item = list_suggestions(s, user)[0]
        # Not selectable while awaiting confirmation.
        assert select_source(s, user).source_type != "content_inbox"

        confirm_suggestion(item)
        s.commit()
        src = select_source(s, user)
        assert src.source_type == "content_inbox"
        assert src.text == "Article body"  # parsed_content preferred


def test_dismiss_hides_suggestion():
    feed = _feed({"title": "N", "link": "https://b.example.com/d", "summary": "b"})
    with Session() as s:
        user = _user(s)
        create_source(s, user, "rss_feed", "https://b.example.com/feed")
        s.commit()
        run_source_watch(s, feed_parser=feed)
        s.commit()
        item = list_suggestions(s, user)[0]
        dismiss_suggestion(item)
        s.commit()
        assert list_suggestions(s, user) == []


# --- routes ----------------------------------------------------------------

def test_sources_routes_require_login(client):
    assert client.get("/inbox/sources").status_code in (301, 302)
    assert client.get("/inbox/suggestions").status_code in (301, 302)


def test_sources_and_suggestions_via_http(client):
    client.get("/dev/login")
    # Add a source.
    resp = client.post("/inbox/sources", json={
        "source_type": "rss_feed", "source_url": "https://blog.example.com/feed", "source_name": "Blog",
    })
    assert resp.status_code == 201
    src_id = resp.get_json()["source"]["id"]
    assert len(client.get("/inbox/sources").get_json()) == 1

    # Toggle + invalid add.
    assert client.post(f"/inbox/sources/{src_id}/toggle").get_json()["source"]["active"] is False
    assert client.post("/inbox/sources", json={"source_type": "website", "source_url": "ftp://x"}).status_code == 400

    # Seed a suggestion directly, then confirm via HTTP.
    with Session() as s:
        user = s.query(User).filter_by(email="dev@ghostpro.local").one()
        s.add(ContentInbox(user_id=user.id, content_type="suggested", raw_content="t",
                           status="pending_confirmation", suggested_by="https://x/y"))
        s.commit()
    sug = client.get("/inbox/suggestions").get_json()
    assert len(sug) == 1
    assert client.post(f"/inbox/suggestions/{sug[0]['id']}/confirm").status_code == 200
    # Confirmed -> no longer in suggestions, now a pending inbox item.
    assert client.get("/inbox/suggestions").get_json() == []
    assert client.get("/inbox?format=json").get_json()[0]["status"] == "pending"


def test_source_ownership_via_http(client):
    with Session() as s:
        other = User(email="stranger@example.com")
        s.add(other)
        s.flush()
        src = FollowedSource(user=other, source_type="website", source_url="https://x.com", active=True)
        s.add(src)
        s.commit()
        foreign_id = src.id

    client.get("/dev/login")
    assert client.post(f"/inbox/sources/{foreign_id}/toggle").status_code == 404
    assert client.delete(f"/inbox/sources/{foreign_id}").status_code == 404
