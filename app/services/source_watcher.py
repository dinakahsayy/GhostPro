# app/services/source_watcher.py
# Daily job (§9.2): poll each user's active Followed Sources and surface new
# articles/posts as 'suggested' inbox items awaiting one-tap confirmation.
# RSS feeds are parsed with feedparser; websites/pages are fetched through the
# SSRF-guarded fetcher. Items are de-duplicated by link so nothing repeats.

from datetime import datetime

from ..models.database import ContentInbox, FollowedSource, User
from ..utils.url_fetch import fetch_url_text
from .notifications import notify_suggestions

_MAX_ENTRIES_PER_FEED = 5


def _default_feed_parser(url):
    import feedparser
    return feedparser.parse(url)


def _suggestion_exists(session, user_id, link):
    return (
        session.query(ContentInbox)
        .filter(ContentInbox.user_id == user_id, ContentInbox.suggested_by == link)
        .first()
        is not None
    )


def _poll_source(session, source, feed_parser, fetcher):
    """Return a list of new {title, link, summary} dicts for a source."""
    results = []
    if source.source_type == "rss_feed":
        parsed = feed_parser(source.source_url)
        entries = getattr(parsed, "entries", None) or []
        for entry in entries[:_MAX_ENTRIES_PER_FEED]:
            link = entry.get("link")
            if not link or _suggestion_exists(session, source.user_id, link):
                continue
            results.append({
                "title": entry.get("title") or link,
                "link": link,
                "summary": entry.get("summary") or entry.get("title") or "",
            })
    else:
        # website / linkedin_page — fetch once; de-dup the URL across all runs.
        link = source.source_url
        if _suggestion_exists(session, source.user_id, link):
            return results
        text, error = fetcher(link)
        if error or not text:
            return results
        title = source.source_name or text.splitlines()[0][:120]
        results.append({"title": title, "link": link, "summary": text})
    return results


def _create_suggestion(session, source, data, now):
    title = " ".join((data["title"] or "").split())
    label = (title[:77] + "...") if len(title) > 80 else title
    session.add(ContentInbox(
        user_id=source.user_id,
        content_type="suggested",
        raw_content=title or data["link"],
        parsed_content=data["summary"] or None,
        priority="use_whenever",
        status="pending_confirmation",
        source_label=label or source.source_name,
        suggested_by=data["link"],
        created_at=now,
    ))


def run_source_watch(session, now=None, feed_parser=None, fetcher=None):
    """Poll every active followed source, create suggestions, notify users.
    Returns {user_id: new_count}."""
    now = now or datetime.utcnow()
    feed_parser = feed_parser or _default_feed_parser
    fetcher = fetcher or fetch_url_text

    sources = session.query(FollowedSource).filter(FollowedSource.active.is_(True)).all()
    new_by_user = {}
    for source in sources:
        try:
            items = _poll_source(session, source, feed_parser, fetcher)
        except Exception:
            items = []  # one bad source never breaks the whole run
        for data in items:
            _create_suggestion(session, source, data, now)
            new_by_user[source.user_id] = new_by_user.get(source.user_id, 0) + 1
        source.last_checked_at = now

    for user_id, count in new_by_user.items():
        user = session.get(User, user_id)
        if user:
            notify_suggestions(session, user, count)
    return new_by_user
