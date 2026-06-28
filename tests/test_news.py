from app.models.database import Session, StyleProfile, User
from app.services.news import fetch_news_topic
from app.services.source_selection import select_source


def _user(session, **kw):
    kw.setdefault("email", "news@example.com")
    user = User(**kw)
    session.add(user)
    session.flush()
    return user


def test_fetch_news_topic_without_key_returns_none(monkeypatch):
    monkeypatch.delenv("NEWSAPI_API_KEY", raising=False)
    assert fetch_news_topic("fintech") is None
    assert fetch_news_topic("", api_key="k") is None


def test_news_used_when_inbox_and_topics_empty():
    fake_news = lambda industry: {"title": "Big fintech news", "text": "Fintech is booming.", "url": "https://x"}
    with Session() as s:
        user = _user(s, industry="Fintech")
        src = select_source(s, user, news_fetcher=fake_news)
        assert src.source_type == "news_api"
        assert "Fintech is booming." in src.text


def test_falls_back_to_seasonal_when_news_none():
    with Session() as s:
        user = _user(s, industry="Fintech")
        src = select_source(s, user, news_fetcher=lambda industry: None)
        assert src.source_type == "seasonal"


def test_no_news_without_industry():
    with Session() as s:
        user = _user(s, industry=None)
        called = []
        src = select_source(s, user, news_fetcher=lambda i: called.append(i) or {"title": "x", "text": "x", "url": ""})
        assert src.source_type == "seasonal"
        assert called == []  # news not attempted without an industry


def test_topics_take_precedence_over_news():
    with Session() as s:
        user = _user(s, industry="Fintech")
        user.style_profile = StyleProfile(top_topics=["leadership"])
        s.flush()
        src = select_source(s, user, news_fetcher=lambda i: {"title": "x", "text": "x", "url": ""})
        assert src.source_type == "user_topic"
        assert src.text == "leadership"
