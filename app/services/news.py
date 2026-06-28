# app/services/news.py
# Industry news lookup for the §8.1 step-4 content source. Backed by NewsAPI.org;
# returns None gracefully without NEWSAPI_API_KEY or on any failure, so source
# selection simply falls through to the seasonal fallback.

import os

import requests

_NEWS_URL = "https://newsapi.org/v2/everything"


def fetch_news_topic(industry, api_key=None):
    """Return {title, text, url} for a recent article about the industry, or None."""
    api_key = api_key or os.getenv("NEWSAPI_API_KEY")
    if not api_key or not industry:
        return None
    try:
        resp = requests.get(
            _NEWS_URL,
            params={"q": industry, "language": "en", "sortBy": "publishedAt", "pageSize": 5},
            headers={"X-Api-Key": api_key},
            timeout=8,
        )
        if resp.status_code != 200:
            return None
        articles = resp.json().get("articles") or []
    except Exception:
        return None

    for article in articles:
        title = (article.get("title") or "").strip()
        description = (article.get("description") or "").strip()
        text = ". ".join(p for p in (title, description) if p).strip()
        if text:
            return {"title": title or text, "text": text, "url": article.get("url") or ""}
    return None
