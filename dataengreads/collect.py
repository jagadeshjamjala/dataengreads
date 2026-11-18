"""Collect post URLs: recent posts from feeds, older ones from sitemaps (backfill)."""
import datetime as dt
import io
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import feedparser

from dataengreads.db import upsert
from dataengreads.discover import medium_feed

NON_POST_SEGMENTS = {"tag", "tags", "tagged", "topic", "topics", "category", "categories", "author", "authors", "page"}
LOCALE = re.compile(r"[a-z]{2}-[a-z]{2}")  # translated copies, e.g. Cloudflare's /zh-cn/<post>
CHILD_HINTS = ("post", "blog", "article")  # which child sitemaps of a big index are worth following
FOLLOW_ALL_CHILDREN = 20
MAX_CHILDREN = 50


def clean_url(url: str) -> str:
    """One spelling per post, so feed and sitemap URLs match: no tracking params, fragment or trailing slash."""
    parts = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k != "source" and not k.startswith("utm_")]
    return urlunparse(parts._replace(netloc=parts.netloc.lower(), path=parts.path.rstrip("/") or "/",
                                     query=urlencode(query), fragment=""))


def _entry_date(entry) -> dt.datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return dt.datetime(*parsed[:6]) if parsed else None


def feed_posts(client, feed_url: str) -> list[dict]:
    text = client.get(feed_url, cache=False)  # feeds change; always fetch fresh
    if not text:
        return []
    feed = feedparser.parse(io.BytesIO(text.encode()))
    return [{"url": clean_url(e.link), "title": e.get("title"), "published_at": _entry_date(e)}
            for e in feed.entries if e.get("link")]


def _entries(root):
    """(loc, lastmod) for each <url> or <sitemap> element, whatever XML namespace the site uses."""
    for item in root:
        fields = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in item}
        if fields.get("loc"):
            yield fields["loc"], fields.get("lastmod")


def _date(value: str | None) -> dt.date | None:
    try:
        return dt.date.fromisoformat(value[:10])
    except (TypeError, ValueError):
        return None


def sitemap_urls(client, sitemap_url: str, since: dt.date, nested: bool = False) -> list[str]:
    """URLs changed on or after `since` (or undated). Follows one level of sitemap index."""
    try:
        root = ET.fromstring((client.get(sitemap_url, cache=False) or "").encode())
    except ET.ParseError:
        return []
    # lastmod >= publish date, so lastmod before `since` means published before it too.
    locs = [loc for loc, lastmod in _entries(root) if (d := _date(lastmod)) is None or d >= since]
    if not root.tag.endswith("sitemapindex"):
        return locs
    if nested:
        return []  # shortcut: one level of index only; deeper nesting hasn't been seen on these blogs
    if len(locs) > FOLLOW_ALL_CHILDREN:  # whole-company indexes (Uber has 1,500+): only blog-looking children
        locs = [loc for loc in locs if any(hint in urlparse(loc).path.lower() for hint in CHILD_HINTS)]
    return [url for child in locs[:MAX_CHILDREN] for url in sitemap_urls(client, child, since, nested=True)]


def post_prefix(blog: dict) -> str:
    """Path posts live under: the `post_prefix` override, else the blog URL's first segment (/blog/engineering -> /blog)."""
    if blog.get("post_prefix"):
        return blog["post_prefix"].rstrip("/")
    first = urlparse(blog["url"]).path.strip("/").split("/")[0]
    return f"/{first}" if first else ""


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def is_post(url: str, hosts: set[str], prefix: str) -> bool:
    parts = urlparse(url)
    path = parts.path.rstrip("/")
    segments = path.lower().split("/")
    return (_host(url) in hosts and (path + "/").startswith(prefix + "/") and path != prefix
            and not NON_POST_SEGMENTS & set(segments) and not any(LOCALE.fullmatch(s) for s in segments))


def _blog_posts(client, blog: dict, feed: str | None, sitemap: str | None, feeds_only: bool) -> list[dict]:
    posts = feed_posts(client, feed) if feed else []
    # Medium's sitemap covers all of Medium, so Medium blogs use their feed only.
    if sitemap and not feeds_only and not medium_feed(blog["url"]):
        hosts, prefix = {_host(blog["url"]), _host(sitemap)}, post_prefix(blog)
        posts += [{"url": url, "title": None, "published_at": None}
                  for url in map(clean_url, sitemap_urls(client, sitemap, blog["backfill_since"]))
                  if is_post(url, hosts, prefix)]
    return posts


def collect(con, client, blogs: list[dict], feeds_only: bool = False) -> dict[str, int | None]:
    """Store unseen post URLs as status 'new', one blog at a time. Returns new URLs per blog (None: not discovered)."""
    stored = {c: (f, s) for c, f, s in con.execute(
        "SELECT company, feed_url, sitemap_url FROM blogs WHERE discovered_at IS NOT NULL").fetchall()}
    known = {url for (url,) in con.execute("SELECT url FROM posts").fetchall()}
    counts = {}
    for blog in blogs:
        if blog["company"] not in stored:
            counts[blog["company"]] = None
            continue
        feed, sitemap = stored[blog["company"]]
        new = {}
        # Feed posts come first, so their titles and dates win over a sitemap's bare URL.
        for post in _blog_posts(client, blog, blog.get("feed") or feed, blog.get("sitemap") or sitemap, feeds_only):
            if post["url"] not in known and post["url"] not in new:
                new[post["url"]] = {**post, "blog": blog["company"], "status": "new"}
        upsert(con, "posts", list(new.values()))
        known |= new.keys()
        counts[blog["company"]] = len(new)
    return counts
