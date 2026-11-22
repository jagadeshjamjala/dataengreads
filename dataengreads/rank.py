"""Score data engineering posts so the best case studies come first. Optional Hacker News signal."""
import datetime as dt
import json
import math
from urllib.parse import quote, urlparse

from dataengreads.db import upsert

TYPE_WEIGHT = {"case_study": 1.0, "postmortem": 1.0, "deep_dive": 0.8, "open_source": 0.5,
               "opinion": 0.3, "announcement": 0.1}
DEPTH_MAX = 0.5        # split evenly between word count, scale stated, key decisions
FULL_DEPTH_WORDS = 3000
TIER1_BONUS = 0.2
FRESH_MAX, FRESH_YEARS = 0.1, 5.0
HN_MAX, HN_FULL_POINTS = 0.3, 500

HN_SEARCH = "https://hn.algolia.com/api/v1/search?query={}&restrictSearchableAttributes=url&hitsPerPage=20"


def score(post: dict, now: dt.datetime | None = None) -> float:
    """post: post_type, word_count, scale, key_decisions, tier, confidence, published_at, hn_points."""
    now = now or dt.datetime.now()
    depth = DEPTH_MAX / 3 * (min((post["word_count"] or 0) / FULL_DEPTH_WORDS, 1.0)
                             + bool(post["scale"])
                             + min(len(post["key_decisions"] or []), 3) / 3)
    fresh = 0.0
    if post["published_at"]:
        years = max((now - post["published_at"]).days / 365.25, 0.0)
        fresh = FRESH_MAX * math.exp(-years / FRESH_YEARS)
    hn = HN_MAX * min(math.log1p(post.get("hn_points") or 0) / math.log1p(HN_FULL_POINTS), 1.0)
    base = (TYPE_WEIGHT.get(post["post_type"], 0.3) + depth + (TIER1_BONUS if post["tier"] == 1 else 0.0)
            + fresh + hn)
    return round(base * (post["confidence"] or 0.0), 4)


def _norm(url: str) -> str:
    p = urlparse(url.lower())
    return p.netloc.removeprefix("www.") + p.path.rstrip("/")


def hn_lookup(client, url: str) -> tuple[int, int] | None:
    """(points, comments) of the most-upvoted HN story linking to url; (0, 0) if never posted; None on error."""
    # shortcut: cached forever, so counts never refresh; pass cache=False in the nightly job if that matters.
    body = client.get(HN_SEARCH.format(quote(url, safe="")))
    if body is None:
        return None
    hits = [h for h in json.loads(body).get("hits", []) if h.get("url") and _norm(h["url"]) == _norm(url)]
    if not hits:
        return 0, 0
    best = max(hits, key=lambda h: h.get("points") or 0)
    return best.get("points") or 0, best.get("num_comments") or 0


POSTS = """
SELECT p.url, p.word_count, p.published_at, c.post_type, c.scale, c.key_decisions, c.confidence,
       b.tier, r.hn_points, r.hn_comments
FROM posts p JOIN classifications c USING (url)
LEFT JOIN blogs b ON b.company = p.blog
LEFT JOIN rankings r USING (url)
WHERE c.is_de
"""


def rank(con, client=None, now: dt.datetime | None = None) -> dict:
    """Score every DE post into `rankings`. With an HTTP client, also look up HN for posts without it."""
    cols = [d[0] for d in con.execute(POSTS).description]
    posts = [dict(zip(cols, row)) for row in con.execute(POSTS).fetchall()]
    looked_up = 0
    if client is not None:
        for post in posts:
            if post["hn_points"] is None and (hn := hn_lookup(client, post["url"])) is not None:
                post["hn_points"], post["hn_comments"] = hn
                looked_up += 1
    ranked_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rows = [{"url": p["url"], "score": score(p, now), "hn_points": p["hn_points"],
             "hn_comments": p["hn_comments"], "ranked_at": ranked_at} for p in posts]
    con.execute("DELETE FROM rankings WHERE url NOT IN (SELECT url FROM classifications WHERE is_de)")
    upsert(con, "rankings", rows)
    return {"ranked": len(rows), "hn_looked_up": looked_up}
