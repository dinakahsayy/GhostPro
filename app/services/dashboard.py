# app/services/dashboard.py
# Dashboard data (§7.6): calendar of upcoming/past posts, engagement analytics,
# and a manual LinkedIn engagement metric sync.

from ..models.database import Post

# Statuses that represent real, user-facing posts (exclude drafts/superseded).
_CALENDAR_STATUSES = ("scheduled", "approved", "published", "error")


def calendar_events(session, user):
    """Scheduled + past posts as calendar entries, soonest/most-recent first."""
    posts = (
        session.query(Post)
        .filter(Post.user_id == user.get_id(), Post.status.in_(_CALENDAR_STATUSES))
        .all()
    )
    events = []
    for p in posts:
        when = p.published_at or p.scheduled_at or p.created_at
        snippet = (p.content or "")[:80]
        events.append({
            "id": p.id,
            "title": snippet + ("…" if len(p.content or "") > 80 else ""),
            "status": p.status,
            "date": when.isoformat() if when else None,
            "is_upcoming": p.status in ("scheduled", "approved"),
        })
    events.sort(key=lambda e: e["date"] or "", reverse=True)
    return events


def analytics_summary(session, user):
    """Aggregate engagement across the user's published posts."""
    published = (
        session.query(Post)
        .filter(Post.user_id == user.get_id(), Post.status == "published")
        .all()
    )
    count = len(published)
    likes = sum(p.likes_count or 0 for p in published)
    comments = sum(p.comments_count or 0 for p in published)
    shares = sum(p.shares_count or 0 for p in published)
    total = likes + comments + shares
    return {
        "published_count": count,
        "likes": likes,
        "comments": comments,
        "shares": shares,
        "total_engagement": total,
        "avg_engagement": round(total / count, 1) if count else 0,
    }


def sync_engagement(session, user, linkedin_api):
    """Refresh like/comment/share counts for the user's published posts.
    Returns the number of posts updated."""
    token = user.linkedin_access_token
    if not token:
        return 0
    posts = (
        session.query(Post)
        .filter(
            Post.user_id == user.get_id(),
            Post.status == "published",
            Post.linkedin_post_id.isnot(None),
        )
        .all()
    )
    updated = 0
    for post in posts:
        metrics = linkedin_api.get_engagement(token, post.linkedin_post_id)
        if not metrics:
            continue
        post.likes_count = metrics.get("likes", post.likes_count)
        post.comments_count = metrics.get("comments", post.comments_count)
        post.shares_count = metrics.get("shares", post.shares_count)
        updated += 1
    return updated
