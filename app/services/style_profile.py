# app/services/style_profile.py
# Builds a user's StyleProfile (§2.1, §8.3): deterministic metrics extracted from
# past posts in pure Python, plus a GPT-4o prose summary used as the system-prompt
# voice description. Falls back to onboarding answers when there is no post history.

import re
from datetime import datetime

from ..models.database import Post, StyleProfile

# Re-analyze the user's voice after every N published posts (§8.3).
REFRESH_EVERY = 10
_MAX_ANALYZED = 50

# Common emoji blocks (variation selectors deliberately excluded so a base glyph
# followed by U+FE0F is counted once, not twice).
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"   # symbols, pictographs, emoji
    "\U00002600-\U000027BF"   # misc symbols + dingbats
    "\U0001F1E6-\U0001F1FF"   # regional indicators
    "\U00002B00-\U00002BFF"   # misc symbols & arrows
    "]"
)
_HASHTAG_RE = re.compile(r"#\w+")


def _emoji_scale(avg_emojis_per_post):
    """Map average emojis/post onto the 1-5 scale used by StyleProfile.emoji_usage."""
    if avg_emojis_per_post <= 0:
        return 1
    if avg_emojis_per_post < 0.5:
        return 2
    if avg_emojis_per_post < 1.5:
        return 3
    if avg_emojis_per_post < 3:
        return 4
    return 5


def analyze_posts(posts):
    """Extract deterministic style metrics from a list of post text strings.

    Returns a dict with sample_posts_analyzed, avg_post_length (words),
    emoji_usage (1-5), and hashtag_count (avg per post). All-None metrics when
    there are no usable posts.
    """
    texts = [p for p in (posts or []) if p and p.strip()]
    n = len(texts)
    if n == 0:
        return {
            "sample_posts_analyzed": 0,
            "avg_post_length": None,
            "emoji_usage": None,
            "hashtag_count": None,
        }

    total_words = sum(len(t.split()) for t in texts)
    total_emojis = sum(len(_EMOJI_RE.findall(t)) for t in texts)
    total_hashtags = sum(len(_HASHTAG_RE.findall(t)) for t in texts)

    return {
        "sample_posts_analyzed": n,
        "avg_post_length": round(total_words / n),
        "emoji_usage": _emoji_scale(total_emojis / n),
        "hashtag_count": round(total_hashtags / n, 1),
    }


def _summary_from_posts(openai_service, user, posts):
    joined = "\n\n---\n\n".join(posts[:50])
    system = "You are an expert writing-style analyst for professional social content."
    prompt = (
        f"Below are past LinkedIn posts written by {user.name or 'a professional'}"
        f"{f', a {user.title}' if user.title else ''}"
        f"{f' in {user.industry}' if user.industry else ''}.\n\n"
        "In 3-5 sentences, describe their writing style as guidance for an AI "
        "ghostwriter imitating them: tone, typical length, emoji and hashtag use, "
        "recurring themes, and sentence structure. Write only the description.\n\n"
        f"{joined}"
    )
    return openai_service.chat(system, prompt, temperature=0.4, max_tokens=300)


def _summary_from_onboarding(openai_service, user, profile):
    facts = [
        f"Role: {user.title}" if user.title else None,
        f"Industry: {user.industry}" if user.industry else None,
        f"Audience: {user.audience_description}" if user.audience_description else None,
        f"Self-description: {user.bio}" if user.bio else None,
        f"Preferred tone: {profile.tone}" if profile.tone else None,
        f"Preferred length: {profile.preferred_length}" if profile.preferred_length else None,
        f"Goal: {profile.content_goal}" if profile.content_goal else None,
        f"Emoji usage (1-5): {profile.emoji_usage}" if profile.emoji_usage else None,
    ]
    facts = [f for f in facts if f]
    system = "You are an expert writing-style analyst for professional social content."
    prompt = (
        "This professional has no LinkedIn post history. From their onboarding "
        "answers below, write 3-5 sentences describing the writing style an AI "
        "ghostwriter should use for them: tone, length, emoji/hashtag use, and "
        "likely themes. Write only the description.\n\n"
        + "\n".join(facts)
    )
    return openai_service.chat(system, prompt, temperature=0.5, max_tokens=300)


def generate_style_profile(session, user, openai_service, posts=None):
    """Create or update the user's StyleProfile from past posts when available,
    otherwise from their onboarding answers. The GPT summary is best-effort: if
    it returns None (e.g. no API key), the row is still updated with whatever
    metrics/timestamp we have. Returns the StyleProfile."""
    profile = user.style_profile
    if profile is None:
        profile = StyleProfile(user=user)
        session.add(profile)

    metrics = analyze_posts(posts)
    if metrics["sample_posts_analyzed"] > 0:
        profile.avg_post_length = metrics["avg_post_length"]
        profile.emoji_usage = metrics["emoji_usage"]
        profile.hashtag_count = metrics["hashtag_count"]
        profile.sample_posts_analyzed = metrics["sample_posts_analyzed"]
        summary = _summary_from_posts(openai_service, user, posts)
    else:
        summary = _summary_from_onboarding(openai_service, user, profile)

    if summary:
        profile.raw_style_summary = summary
    profile.last_updated = datetime.utcnow()
    return profile


def maybe_refresh_style_profile(session, user, openai_service):
    """After every REFRESH_EVERY published posts, re-derive the style profile
    from the user's own published content (§8.3). Returns the profile or None."""
    count = (
        session.query(Post)
        .filter(Post.user_id == user.get_id(), Post.status == "published")
        .count()
    )
    if count == 0 or count % REFRESH_EVERY != 0:
        return None

    posts = (
        session.query(Post)
        .filter(Post.user_id == user.get_id(), Post.status == "published")
        .order_by(Post.published_at.desc())
        .limit(_MAX_ANALYZED)
        .all()
    )
    contents = [p.content for p in posts if p.content]
    return generate_style_profile(session, user, openai_service, posts=contents)
