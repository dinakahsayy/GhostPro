from app.models.database import Session, User
from app.services.style_profile import (
    _emoji_scale, analyze_posts, generate_style_profile,
)


class FakeOpenAI:
    """Stand-in for OpenAIService.chat — records the last call, returns a canned value."""
    def __init__(self, reply="A concise, conversational style with light emoji use."):
        self.reply = reply
        self.calls = []

    def chat(self, system, user, **kwargs):
        self.calls.append({"system": system, "user": user, **kwargs})
        return self.reply


# --- analyze_posts ---------------------------------------------------------

def test_analyze_posts_extracts_metrics():
    posts = [
        "We hit a major milestone today 🚀🎉 #growth #startup",  # 8 words, 2 emoji, 2 tags
        "Hard work pays off. Proud of the team.",                # 8 words, 0 emoji, 0 tags
    ]
    m = analyze_posts(posts)
    assert m["sample_posts_analyzed"] == 2
    assert m["avg_post_length"] == 8
    assert m["hashtag_count"] == 1.0          # (2 + 0) / 2
    assert m["emoji_usage"] == 3              # avg 1.0 emoji/post -> scale 3


def test_analyze_posts_empty():
    m = analyze_posts([])
    assert m == {
        "sample_posts_analyzed": 0, "avg_post_length": None,
        "emoji_usage": None, "hashtag_count": None,
    }


def test_analyze_posts_ignores_blank_entries():
    assert analyze_posts(["", "   ", None]) == {
        "sample_posts_analyzed": 0, "avg_post_length": None,
        "emoji_usage": None, "hashtag_count": None,
    }


def test_emoji_scale_boundaries():
    assert _emoji_scale(0) == 1
    assert _emoji_scale(0.2) == 2
    assert _emoji_scale(1.0) == 3
    assert _emoji_scale(2.0) == 4
    assert _emoji_scale(5.0) == 5


# --- generate_style_profile ------------------------------------------------

def test_generate_from_posts_sets_metrics_and_summary():
    fake = FakeOpenAI()
    with Session() as s:
        user = User(email="poster@example.com", name="Sam", title="CTO", industry="Fintech")
        s.add(user)
        s.flush()
        posts = ["Shipping fast and learning 🚀 #build", "Reliability matters. Always."]
        profile = generate_style_profile(s, user, fake, posts=posts)
        s.commit()

        assert profile.sample_posts_analyzed == 2
        assert profile.avg_post_length is not None
        assert profile.raw_style_summary == fake.reply
        assert profile.last_updated is not None
        # The model should have been handed the actual post text.
        assert "Shipping fast" in fake.calls[0]["user"]


def test_generate_from_onboarding_when_no_posts():
    fake = FakeOpenAI(reply="Warm, story-driven, minimal hashtags.")
    with Session() as s:
        user = User(email="newbie@example.com", title="Designer", industry="UX")
        s.add(user)
        s.flush()
        profile = generate_style_profile(s, user, fake, posts=None)
        s.commit()

        assert profile.sample_posts_analyzed in (0, None)  # untouched by metrics
        assert profile.raw_style_summary == "Warm, story-driven, minimal hashtags."
        # Prompt should reference the onboarding facts, not post text.
        assert "no LinkedIn post history" in fake.calls[0]["user"]


def test_generate_is_resilient_to_openai_failure():
    class NullOpenAI:
        def chat(self, system, user, **kwargs):
            return None

    with Session() as s:
        user = User(email="nokey@example.com")
        s.add(user)
        s.flush()
        profile = generate_style_profile(s, user, NullOpenAI(), posts=["Hello world"])
        s.commit()

        # No summary, but metrics + timestamp still recorded; no exception raised.
        assert profile.raw_style_summary is None
        assert profile.sample_posts_analyzed == 1
        assert profile.last_updated is not None
