import pytest

from app.models.database import ContentInbox, Session, User
from app.services.inbox import (
    create_inbox_item, get_inbox_item, list_inbox_items, skip_inbox_item,
    soft_delete_inbox_item, toggle_priority, update_inbox_item,
)

OK_FETCHER = lambda url: ("Parsed article title\n\nBody text here.", None)
FAIL_FETCHER = lambda url: (None, "blocked")


def _make_user(session, email="inbox@example.com"):
    user = User(email=email)
    session.add(user)
    session.flush()
    return user


# --- service: create -------------------------------------------------------

def test_create_text_note():
    with Session() as s:
        user = _make_user(s)
        item = create_inbox_item(s, user, "text_note", "We landed a huge client today")
        s.commit()
        assert item.status == "pending"
        assert item.priority == "use_whenever"
        assert item.source_label.startswith("We landed")


def test_create_url_parses_via_fetcher():
    with Session() as s:
        user = _make_user(s)
        item = create_inbox_item(s, user, "url", "https://blog.example.com/post",
                                 fetcher=OK_FETCHER)
        s.commit()
        assert item.parsed_content.startswith("Parsed article title")
        assert item.source_label == "Parsed article title"


def test_create_url_parse_failure_raises():
    with Session() as s:
        user = _make_user(s)
        with pytest.raises(ValueError, match="Could not read that URL"):
            create_inbox_item(s, user, "url", "https://10.0.0.1/x", fetcher=FAIL_FETCHER)


def test_create_rejects_bad_type_and_empty():
    with Session() as s:
        user = _make_user(s)
        with pytest.raises(ValueError):
            create_inbox_item(s, user, "suggested", "x")  # not user-submittable
        with pytest.raises(ValueError):
            create_inbox_item(s, user, "text_note", "   ")


def test_post_soon_priority():
    with Session() as s:
        user = _make_user(s)
        item = create_inbox_item(s, user, "quote_stat", "94% retention", priority="post_soon")
        s.commit()
        assert item.priority == "post_soon"


# --- service: list / transitions ------------------------------------------

def test_list_excludes_deleted_and_filters():
    with Session() as s:
        user = _make_user(s)
        a = create_inbox_item(s, user, "text_note", "first")
        b = create_inbox_item(s, user, "text_note", "second", priority="post_soon")
        s.commit()
        soft_delete_inbox_item(a)
        s.commit()

        visible = list_inbox_items(s, user)
        assert [i.id for i in visible] == [b.id]
        assert list_inbox_items(s, user, priority="post_soon") == [b]


def test_toggle_priority_and_skip():
    with Session() as s:
        user = _make_user(s)
        item = create_inbox_item(s, user, "text_note", "note")
        s.commit()
        toggle_priority(item)
        assert item.priority == "post_soon"
        toggle_priority(item)
        assert item.priority == "use_whenever"
        skip_inbox_item(item)
        assert item.status == "skipped"


def test_update_only_when_pending():
    with Session() as s:
        user = _make_user(s)
        item = create_inbox_item(s, user, "text_note", "note")
        s.commit()
        update_inbox_item(s, item, {"priority": "post_soon", "context_note": "for execs"})
        assert item.priority == "post_soon"
        assert item.context_note == "for execs"

        item.status = "used"
        with pytest.raises(ValueError, match="pending"):
            update_inbox_item(s, item, {"context_note": "nope"})


def test_get_enforces_ownership():
    with Session() as s:
        owner = _make_user(s, "owner@example.com")
        other = _make_user(s, "other@example.com")
        item = create_inbox_item(s, owner, "text_note", "secret")
        s.commit()
        assert get_inbox_item(s, owner, item.id) is not None
        assert get_inbox_item(s, other, item.id) is None


# --- routes ----------------------------------------------------------------

def test_inbox_requires_login(client):
    assert client.get("/inbox?format=json").status_code in (301, 302)
    assert client.post("/inbox", json={}).status_code in (301, 302)


def test_inbox_create_and_list_via_http(client):
    client.get("/dev/login")
    resp = client.post("/inbox", json={"content_type": "text_note", "raw_content": "Big win today"})
    assert resp.status_code == 201
    assert resp.get_json()["item"]["source_label"].startswith("Big win")

    listed = client.get("/inbox?format=json").get_json()
    assert len(listed) == 1
    assert listed[0]["raw_content"] == "Big win today"


def test_inbox_create_validation_error(client):
    client.get("/dev/login")
    resp = client.post("/inbox", json={"content_type": "text_note", "raw_content": ""})
    assert resp.status_code == 400


def test_inbox_prioritize_skip_delete_via_http(client):
    client.get("/dev/login")
    item_id = client.post(
        "/inbox", json={"content_type": "text_note", "raw_content": "story"}
    ).get_json()["item"]["id"]

    assert client.post(f"/inbox/{item_id}/prioritize").get_json()["item"]["priority"] == "post_soon"
    assert client.post(f"/inbox/{item_id}/skip").get_json()["item"]["status"] == "skipped"
    assert client.delete(f"/inbox/{item_id}").status_code == 200
    # Soft-deleted -> no longer listed.
    assert client.get("/inbox?format=json").get_json() == []


def test_inbox_ownership_blocks_cross_user_access(client):
    # Create an item owned by a different user directly.
    with Session() as s:
        other = User(email="someoneelse@example.com")
        s.add(other)
        s.flush()
        item = create_inbox_item(s, other, "text_note", "not yours")
        s.commit()
        foreign_id = item.id

    client.get("/dev/login")  # logs in as dev@ghostpro.local
    assert client.get(f"/inbox/{foreign_id}").status_code == 404
    assert client.post(f"/inbox/{foreign_id}/skip").status_code == 404
    assert client.delete(f"/inbox/{foreign_id}").status_code == 404
