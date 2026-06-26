import pytest

from app.models.database import ContentInbox, Post, Session, StyleProfile, User
from app.services.generation import (
    LINKEDIN_MAX_CHARS, build_system_prompt, build_user_prompt,
    generate_post_for_user, post_process,
)
from app.services.source_selection import Source, select_source


class FakeOpenAI:
    def __init__(self, reply="Generated post body. #growth"):
        self.reply = reply
        self.calls = []

    def chat(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        return self.reply


def _user(session, **kw):
    user = User(email=kw.pop("email", "gen@example.com"), **kw)
    session.add(user)
    session.flush()
    return user


# --- source selection ------------------------------------------------------

def test_post_soon_beats_use_whenever_and_is_fifo():
    with Session() as s:
        user = _user(s)
        # Two use_whenever (older) and one post_soon (newest) — post_soon wins.
        s.add(ContentInbox(user=user, content_type="text_note", raw_content="old whenever",
                           priority="use_whenever", status="pending"))
        s.flush()
        s.add(ContentInbox(user=user, content_type="text_note", raw_content="the soon one",
                           priority="post_soon", status="pending"))
        s.commit()

        src = select_source(s, user)
        assert src.source_type == "content_inbox"
        assert src.text == "the soon one"


def test_use_whenever_when_no_post_soon_oldest_first():
    with Session() as s:
        user = _user(s)
        first = ContentInbox(user=user, content_type="text_note", raw_content="first",
                             priority="use_whenever", status="pending")
        s.add(first)
        s.flush()
        s.add(ContentInbox(user=user, content_type="text_note", raw_content="second",
                           priority="use_whenever", status="pending"))
        s.commit()

        assert select_source(s, user).text == "first"


def test_url_item_uses_parsed_content():
    with Session() as s:
        user = _user(s)
        s.add(ContentInbox(user=user, content_type="url", raw_content="https://x.example.com",
                           parsed_content="Parsed body", priority="post_soon", status="pending"))
        s.commit()
        assert select_source(s, user).text == "Parsed body"


def test_falls_back_to_user_topic_then_seasonal():
    with Session() as s:
        user = _user(s)
        user.style_profile = StyleProfile(top_topics=["AI in healthcare"])
        s.commit()
        topic_src = select_source(s, user)
        assert topic_src.source_type == "user_topic"
        assert topic_src.text == "AI in healthcare"

    with Session() as s:
        bare = _user(s, email="bare@example.com")
        s.commit()
        assert select_source(s, bare).source_type == "seasonal"


def test_select_source_is_user_scoped():
    with Session() as s:
        a = _user(s, email="a@example.com")
        b = _user(s, email="b@example.com")
        s.add(ContentInbox(user=a, content_type="text_note", raw_content="A's item",
                           priority="post_soon", status="pending"))
        s.commit()
        # b has no inbox items -> should not see A's; falls through to seasonal.
        assert select_source(s, b).source_type == "seasonal"


# --- prompt construction ---------------------------------------------------

def test_system_prompt_includes_identity_and_style():
    with Session() as s:
        user = _user(s, name="Jane", title="VP Sales", industry="SaaS")
        user.style_profile = StyleProfile(tone="Conversational", preferred_length="short",
                                          emoji_usage=2, hashtag_count=3, avoid_topics=["politics"])
        s.flush()
        prompt = build_system_prompt(user, user.style_profile)
        for fragment in ["Jane", "VP Sales", "SaaS", "Conversational", "short", "2/5", "politics"]:
            assert fragment in prompt


def test_user_prompt_inbox_vs_topic():
    inbox = build_user_prompt(Source("content_inbox", "we won a big client",
                                     context_note="for founders"))
    assert "we won a big client" in inbox
    assert "for founders" in inbox

    topic = build_user_prompt(Source("user_topic", "remote work"))
    assert "remote work" in topic


def test_post_process_trims_and_caps():
    assert post_process("  hi  ") == "hi"
    long_text = "x" * (LINKEDIN_MAX_CHARS + 500)
    assert len(post_process(long_text)) == LINKEDIN_MAX_CHARS


# --- orchestration ---------------------------------------------------------

def test_generate_creates_draft_and_marks_inbox_in_progress():
    fake = FakeOpenAI()
    with Session() as s:
        user = _user(s, name="Sam", title="CTO", industry="Fintech")
        item = ContentInbox(user=user, content_type="text_note", raw_content="shipped v2",
                            priority="post_soon", status="pending")
        s.add(item)
        s.commit()

        post = generate_post_for_user(s, user, fake)
        s.commit()

        assert post.status == "draft"
        assert post.source_type == "content_inbox"
        assert post.inbox_item_id == item.id
        assert post.generation_prompt and "SYSTEM:" in post.generation_prompt
        assert "shipped v2" in fake.calls[0]["user"]
        # Inbox item consumed.
        assert s.get(ContentInbox, item.id).status == "in_progress"


def test_generate_returns_none_and_consumes_nothing_on_failure():
    class NullOpenAI:
        def chat(self, *a, **k):
            return None

    with Session() as s:
        user = _user(s)
        item = ContentInbox(user=user, content_type="text_note", raw_content="note",
                            priority="post_soon", status="pending")
        s.add(item)
        s.commit()

        assert generate_post_for_user(s, user, NullOpenAI()) is None
        s.commit()
        # Nothing created, inbox item untouched.
        assert s.query(Post).count() == 0
        assert s.get(ContentInbox, item.id).status == "pending"


# --- routes ----------------------------------------------------------------

def test_posts_routes_require_login(client):
    assert client.post("/posts/generate").status_code in (301, 302)
    assert client.get("/posts").status_code in (301, 302)


def test_generate_endpoint_creates_post(client):
    client.application.extensions["openai_service"] = FakeOpenAI("Fresh draft body")
    client.get("/dev/login")
    client.post("/inbox", json={"content_type": "text_note", "raw_content": "milestone hit"})

    resp = client.post("/posts/generate")
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["post"]["status"] == "draft"
    assert body["post"]["content"] == "Fresh draft body"

    listed = client.get("/posts").get_json()
    assert len(listed) == 1


def test_generate_endpoint_reports_failure(client):
    class NullOpenAI:
        def chat(self, *a, **k):
            return None

    client.application.extensions["openai_service"] = NullOpenAI()
    client.get("/dev/login")
    resp = client.post("/posts/generate")
    assert resp.status_code == 502
    assert resp.get_json()["status"] == "error"
