import datetime as dt
import gzip
from pathlib import Path

import pytest

from dataengreads.collect import clean_url, collect, feed_posts, is_post, post_prefix, sitemap_urls
from dataengreads.db import connect, upsert
from tests.fakes import make_client

FIX = Path(__file__).parent / "fixtures" / "collect"
SINCE = dt.date(2016, 1, 1)
FEED = "https://eng.example.com/feed"
SITEMAP = "https://eng.example.com/sitemap_index.xml"
BLOG = {"company": "Example", "url": "https://eng.example.com/blog/engineering", "industry": "saas", "tier": 1,
        "backfill_since": SINCE}
ROUTES = {
    FEED: (FIX / "feed_medium.xml").read_text(),
    SITEMAP: (FIX / "sitemap_index.xml").read_text(),
    "https://eng.example.com/sitemap-posts.xml": (FIX / "sitemap_posts.xml").read_text(),
    "https://eng.example.com/sitemap-2015.xml": "<urlset><url><loc>https://eng.example.com/blog/ancient</loc></url></urlset>",
    "https://eng.example.com/sitemap-more.xml.gz": gzip.compress((FIX / "sitemap_more.xml").read_bytes()),
}
EXPECTED_NEW = {
    "https://eng.example.com/blog/lakehouse-27447416e68f",
    "https://eng.example.com/blog/cdc-at-scale",
    "https://eng.example.com/blog/streaming-joins",
    "https://eng.example.com/blog/no-date-post",
    "https://www.eng.example.com/blog/from-gzip",
}


@pytest.fixture
def client(tmp_path):
    return make_client(dict(ROUTES), tmp_path / "cache")


@pytest.fixture
def con(tmp_path):
    con = connect(tmp_path / "test.duckdb")
    upsert(con, "blogs", [{"company": "Example", "url": BLOG["url"], "feed_url": FEED, "sitemap_url": SITEMAP,
                           "discovered_at": dt.datetime(2024, 9, 1)}])
    return con


@pytest.mark.parametrize("raw, clean", [
    ("https://Eng.Example.com/blog/a/?utm_source=x&id=3#top", "https://eng.example.com/blog/a?id=3"),
    ("https://medium.com/p/abc?source=rss----1---4", "https://medium.com/p/abc"),
    ("https://eng.example.com/", "https://eng.example.com/"),
])
def test_clean_url(raw, clean):
    assert clean_url(raw) == clean


def test_feed_posts_have_clean_urls_titles_and_dates(client):
    posts = feed_posts(client, FEED)
    assert [p["url"] for p in posts] == ["https://eng.example.com/blog/lakehouse-27447416e68f",
                                         "https://eng.example.com/blog/cdc-at-scale"]
    assert posts[0]["title"] == "Building a lakehouse"
    assert posts[0]["published_at"] == dt.datetime(2024, 9, 10, 9, 0)


def test_sitemap_index_skips_old_children_and_reads_gzip(client):
    urls = sitemap_urls(client, SITEMAP, SINCE)
    assert "https://eng.example.com/blog/ancient" not in urls          # child sitemap last changed in 2015
    assert "https://eng.example.com/blog/old-post" not in urls         # post lastmod 2014
    assert "https://www.eng.example.com/blog/from-gzip" in urls
    assert "https://eng.example.com/blog/no-date-post" in urls         # undated entries are kept


def test_big_index_follows_only_blog_looking_children(tmp_path):
    children = [f"https://big.example.com/routes-{i}.xml" for i in range(30)] + ["https://big.example.com/blog-posts.xml"]
    index = "<sitemapindex>" + "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children) + "</sitemapindex>"
    client = make_client({"https://big.example.com/sitemap.xml": index,
                          "https://big.example.com/blog-posts.xml": "<urlset><url><loc>https://big.example.com/blog/x</loc></url></urlset>"},
                         tmp_path)
    assert sitemap_urls(client, "https://big.example.com/sitemap.xml", SINCE) == ["https://big.example.com/blog/x"]
    assert not any("routes-" in c for c in client.session.calls)


def test_post_filter():
    hosts, prefix = {"eng.example.com"}, post_prefix(BLOG)
    assert prefix == "/blog"
    assert is_post("https://eng.example.com/blog/streaming-joins", hosts, prefix)
    for url in ["https://eng.example.com/blog", "https://eng.example.com/blog/tagged/kafka",
                "https://eng.example.com/careers/x", "https://eng.example.com/blogroll/x",
                "https://eng.example.com/blog/zh-cn/streaming-joins", "https://eng.example.com/blog/topic/research",
                "https://other.example.org/blog/x"]:
        assert not is_post(url, hosts, prefix), url
    assert post_prefix({**BLOG, "post_prefix": "/posts/"}) == "/posts"
    assert post_prefix({"url": "https://netflixtechblog.com"}) == ""


def test_collect_stores_new_posts_once_and_rerun_adds_zero(con, client):
    assert collect(con, client, [BLOG]) == {"Example": len(EXPECTED_NEW)}
    rows = con.execute("SELECT url, blog, title, status FROM posts").fetchall()
    assert {r[0] for r in rows} == EXPECTED_NEW
    assert all(r[1] == "Example" and r[3] == "new" for r in rows)
    # In both the feed and the sitemap: stored once, with the feed's title.
    assert ("https://eng.example.com/blog/lakehouse-27447416e68f", "Example", "Building a lakehouse", "new") in rows
    assert collect(con, client, [BLOG]) == {"Example": 0}


def test_feeds_only_and_undiscovered_blogs(con, client):
    other = {**BLOG, "company": "Nobody"}
    assert collect(con, client, [BLOG, other], feeds_only=True) == {"Example": 2, "Nobody": None}
    assert SITEMAP not in client.session.calls


def test_medium_blog_skips_sitemap(con, client):
    medium = {**BLOG, "url": "https://medium.com/example-engineering"}
    assert collect(con, client, [medium]) == {"Example": 2}
    assert SITEMAP not in client.session.calls
