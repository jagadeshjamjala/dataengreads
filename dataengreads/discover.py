"""Find each blog's RSS/Atom feed and sitemap, and store them in the blogs table."""
import io
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import feedparser

from dataengreads.db import upsert

FEED_TYPES = ("application/rss+xml", "application/atom+xml")
FEED_PATHS = ("feed", "rss", "atom.xml", "feed.xml", "rss.xml", "index.xml")


class _FeedLinks(HTMLParser):
    """Collects <link rel="alternate" type="application/rss+xml|atom+xml" href=...>."""

    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        if (tag == "link" and "alternate" in a.get("rel", "").lower().split()
                and a.get("type", "").lower() in FEED_TYPES and a.get("href")):
            self.hrefs.append(a["href"])


def feed_links(html: str, base_url: str) -> list[str]:
    parser = _FeedLinks()
    parser.feed(html)
    return [urljoin(base_url, href) for href in parser.hrefs]


def _root(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}/"


def medium_feed(url: str) -> str | None:
    parts = urlparse(url)
    publication = parts.path.strip("/").split("/")[0]
    if parts.netloc in ("medium.com", "www.medium.com") and publication:
        return f"https://medium.com/feed/{publication}"
    return None


def _feed_candidates(client, url: str):
    """Most likely first, and lazily, so a hit stops further requests."""
    if medium := medium_feed(url):
        yield medium
    if home := client.get(url):
        yield from feed_links(home, url)
    for base in (url.rstrip("/") + "/", _root(url)):
        for path in FEED_PATHS:
            yield base + path


def _is_feed(client, url: str) -> bool:
    text = client.get(url)
    # A stream, because feedparser treats a str argument that looks like a URL or path as one to open.
    return bool(text) and bool(feedparser.parse(io.BytesIO(text.encode())).entries)


def find_feed(client, blog: dict) -> str | None:
    if blog.get("feed"):
        return blog["feed"]
    seen = set()
    for candidate in _feed_candidates(client, blog["url"]):
        if candidate not in seen:
            seen.add(candidate)
            if _is_feed(client, candidate):
                return candidate
    return None


def find_sitemap(client, blog: dict) -> str | None:
    if blog.get("sitemap"):
        return blog["sitemap"]
    # shortcut: keeps only the first sitemap robots.txt lists; add a `sitemap:` override if it's the wrong one.
    if listed := client.robots(blog["url"]).site_maps():
        return listed[0]
    url = _root(blog["url"]) + "sitemap.xml"
    text = client.get(url) or ""
    return url if "<urlset" in text or "<sitemapindex" in text else None


def _needs_discovery(blog: dict, stored: dict) -> bool:
    """New blogs, blogs where nothing was found, and blogs whose config changed."""
    if blog["company"] not in stored:
        return True
    url, feed, sitemap = stored[blog["company"]]
    return (not (feed or sitemap) or url != blog["url"]
            or blog.get("feed", feed) != feed or blog.get("sitemap", sitemap) != sitemap)


def discover(con, client, blogs: list[dict], refresh: bool = False) -> list[dict]:
    """Discover feeds and sitemaps, one blog at a time so a crash loses nothing. Returns blogs with neither."""
    stored = {c: (u, f, s) for c, u, f, s in
              con.execute("SELECT company, url, feed_url, sitemap_url FROM blogs").fetchall()}
    for blog in blogs:
        if refresh or _needs_discovery(blog, stored):
            upsert(con, "blogs", [{
                "company": blog["company"], "url": blog["url"], "industry": blog["industry"], "tier": blog["tier"],
                "feed_url": find_feed(client, blog), "sitemap_url": find_sitemap(client, blog),
                "discovered_at": datetime.now(timezone.utc).replace(tzinfo=None),
            }])
    found = {c for (c,) in con.execute(
        "SELECT company FROM blogs WHERE feed_url IS NOT NULL OR sitemap_url IS NOT NULL").fetchall()}
    return [b for b in blogs if b["company"] not in found]
