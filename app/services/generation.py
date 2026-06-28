# app/services/generation.py
# Post generation pipeline (§8.2): builds the system/user prompts from the user's
# style profile + selected source, calls GPT-4o, post-processes, and stores a draft
# Post. Consumed inbox items are flipped to 'in_progress'.

from datetime import datetime

from ..models.database import Post
from .source_selection import select_source

LINKEDIN_MAX_CHARS = 3000
# Rough max_tokens per configured post length.
_LENGTH_TOKENS = {"short": 250, "medium": 450, "long": 900}


def build_system_prompt(user, profile):
    tone = (profile.tone if profile else None) or "professional"
    length = (profile.preferred_length if profile else None) or "medium"
    emoji = (profile.emoji_usage if profile and profile.emoji_usage else 1)
    hashtags = (profile.hashtag_count if profile and profile.hashtag_count is not None else 2)
    style = (profile.raw_style_summary if profile else None) or "clear, authentic, and professional"
    avoid = ", ".join(profile.avoid_topics) if (profile and profile.avoid_topics) else "none"
    return (
        f"You are writing a LinkedIn post on behalf of {user.name or 'this professional'}, "
        f"a {user.title or 'professional'} in the {user.industry or 'their'} industry. "
        f"Write in their voice: {style}. Post length: {length}. Tone: {tone}. "
        f"Emoji usage: {emoji}/5. Hashtags: approximately {hashtags}. "
        f"Avoid these topics: {avoid}."
    )


def build_user_prompt(source):
    if source.source_type == "content_inbox":
        context = f" {source.context_note}." if source.context_note else ""
        return (
            "Write a LinkedIn post based on the following. This is something the user "
            f"experienced or wants to share: '{source.text}'.{context} Make it feel "
            "personal and authentic — as if they are sharing this from their own "
            "experience. Do not start with 'I' or a generic opener."
        )
    return (
        f"Write a LinkedIn post about: {source.text}. Frame it from the user's "
        "perspective. Make it feel authentic and not AI-generated. Do not start with "
        "'I' or a generic opener."
    )


def post_process(text):
    """Trim whitespace and enforce LinkedIn's character limit (§8.2)."""
    if not text:
        return text
    text = text.strip()
    if len(text) > LINKEDIN_MAX_CHARS:
        text = text[:LINKEDIN_MAX_CHARS].rstrip()
    return text


def compose_post_content(user, source, openai_service):
    """Build the prompts for a source and return (content, generation_prompt), or
    (None, prompt) if the model produced nothing. No DB side effects — reused by
    both first-time generation and regeneration."""
    profile = user.style_profile
    system_prompt = build_system_prompt(user, profile)
    user_prompt = build_user_prompt(source)
    length = (profile.preferred_length if profile else None) or "medium"
    max_tokens = _LENGTH_TOKENS.get(length, 450)

    content = openai_service.chat(
        system_prompt, user_prompt, model="gpt-4o", temperature=0.75, max_tokens=max_tokens
    )
    generation_prompt = f"SYSTEM:\n{system_prompt}\n\nUSER:\n{user_prompt}"
    if not content:
        return None, generation_prompt
    return post_process(content), generation_prompt


def generate_post_for_user(session, user, openai_service, source=None):
    """Generate a draft Post for the user from the highest-priority source.

    Returns the Post, or None if generation failed (e.g. no OpenAI key) — in which
    case no inbox item is consumed and no row is created.
    """
    if source is None:
        source = select_source(session, user)

    content, generation_prompt = compose_post_content(user, source, openai_service)
    if content is None:
        return None

    post = Post(
        user_id=user.get_id(),
        content=content,
        version=1,
        status="draft",
        source_type=source.source_type,
        inbox_item_id=source.inbox_item.id if source.inbox_item else None,
        source_topic=source.text if source.source_type != "content_inbox" else None,
        generation_prompt=generation_prompt,
        created_at=datetime.utcnow(),
    )
    session.add(post)

    # Mark the consumed inbox item as in-progress (§4.3).
    if source.inbox_item is not None:
        source.inbox_item.status = "in_progress"

    return post


def post_to_dict(post):
    return {
        "id": post.id,
        "content": post.content,
        "status": post.status,
        "version": post.version,
        "source_type": post.source_type,
        "inbox_item_id": post.inbox_item_id,
        "source_topic": post.source_topic,
        "char_count": len(post.content or ""),
        "linkedin_post_id": post.linkedin_post_id,
        "scheduled_at": post.scheduled_at.isoformat() if post.scheduled_at else None,
        "published_at": post.published_at.isoformat() if post.published_at else None,
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }
