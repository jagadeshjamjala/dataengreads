"""Fetch new posts and extract title, date, author and main text with trafilatura."""
import datetime as dt
import hashlib
import json

import trafilatura

from dataengreads.db import upsert

# Newest posts first, taking blogs in turn, so a small --limit samples every blog
# and consecutive requests usually hit different domains (no 1-second wait).
NEW_POSTS = """
SELECT url, blog, title, published_at FROM (
    SELECT *, row_number() OVER (PARTITION BY blog ORDER BY published_at DESC NULLS LAST, url) AS turn
    FROM posts WHERE list_contains(?, status) AND list_contains(?, blog)  -- blogs removed from blogs.yaml stop
)
ORDER BY turn, blog
LIMIT ?
"""


def extract(html: str, url: str) -> dict | None:
    """title, author, published_at, text and word_count; None if trafilatura finds no article."""
    raw = trafilatura.extract(html, url=url, output_format="json", with_metadata=True)
    if not raw:
        return None
    doc = json.loads(raw)
    text = doc.get("text") or ""
    try:
        published = dt.datetime.fromisoformat(doc["date"]) if doc.get("date") else None
    except ValueError:
        published = None
    return {"title": doc.get("title"), "author": doc.get("author"), "published_at": published,
            "text": text, "word_count": len(text.split())}


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def fetch_post(client, url: str, feed_title: str | None, feed_date, min_words: int) -> dict:
    """The posts row for one URL, with status fetched / too_short / failed."""
    html = client.get(url)  # cached on disk, so a re-run never re-downloads
    doc = extract(html, url) if html else None
    if doc is None:
        # Upsert replaces the whole row, so carry the feed's title and date over.
        return {"url": url, "title": feed_title, "published_at": feed_date, "status": "failed", "fetched_at": _now()}
    short = doc["word_count"] < min_words
    return {
        "url": url,
        # The feed's title and date are what the blog published; extraction fills the gaps (sitemap URLs).
        "title": feed_title or doc["title"],
        "published_at": feed_date or doc["published_at"],
        "author": doc["author"],
        "word_count": doc["word_count"],
        "text": None if short else doc["text"],  # only kept for classification
        "html_hash": hashlib.sha256(html.encode()).hexdigest(),
        "fetched_at": _now(),
        "status": "too_short" if short else "fetched",
    }


def fetch(con, client, blogs: list[dict], limit: int, retry_failed: bool = False) -> dict[str, int]:
    """Fetch up to `limit` new posts, saving each as it's done so a crash loses nothing. Returns counts by status."""
    min_words = {b["company"]: b["min_words"] for b in blogs}
    statuses = ["new", "failed"] if retry_failed else ["new"]
    counts = {"fetched": 0, "too_short": 0, "failed": 0}
    for url, blog, title, published in con.execute(NEW_POSTS, [statuses, list(min_words), limit]).fetchall():
        row = fetch_post(client, url, title, published, min_words[blog])
        upsert(con, "posts", [{"blog": blog, **row}])
        counts[row["status"]] += 1
    return counts
