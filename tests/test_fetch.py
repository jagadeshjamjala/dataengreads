import datetime as dt
from pathlib import Path

import pytest

from dataengreads.db import connect, upsert
from dataengreads.fetch import extract, fetch
from tests.fakes import make_client

FIX = Path(__file__).parent / "fixtures" / "fetch"
BLOGS = [{"company": "A", "min_words": 400}, {"company": "B", "min_words": 400}]


def page(name):
    return (FIX / name).read_text()


@pytest.mark.parametrize("name, title, author, date", [
    ("medium.html", "Building a Lakehouse for Streaming Data", "Ana Lima", dt.datetime(2024, 9, 10)),
    ("wordpress.html", "How We Run CDC at Scale", "Raj Patel", dt.datetime(2023, 3, 1)),
    ("custom.html", "Scaling Our Feature Store", "Mei Chen", dt.datetime(2022, 6, 15)),
])
def test_extracts_metadata_and_main_text_only(name, title, author, date):
    doc = extract(page(name), "https://eng.example.com/post")
    assert (doc["title"], doc["author"], doc["published_at"]) == (title, author, date)
    assert doc["word_count"] > 450
    assert "Section 1 looks at ingestion" in doc["text"]
    for junk in ("Sign up", "Sign in", "Recent posts", "Great post", "All rights reserved"):
        assert junk not in doc["text"]


def test_no_article_returns_none():
    assert extract("<html><body></body></html>", "https://eng.example.com/x") is None


@pytest.fixture
def setup(tmp_path):
    con = connect(tmp_path / "test.duckdb")
    posts = {
        "https://a.example.com/medium": ("A", "Feed title", dt.datetime(2024, 9, 11), page("medium.html")),
        "https://a.example.com/short": ("A", None, None, page("short.html")),
        "https://a.example.com/gone": ("A", "Gone", dt.datetime(2020, 1, 1), 404),
        "https://b.example.com/wp": ("B", None, None, page("wordpress.html")),
    }
    upsert(con, "posts", [{"url": u, "blog": b, "title": t, "published_at": d, "status": "new"}
                          for u, (b, t, d, _) in posts.items()])
    client = make_client({u: body for u, (*_, body) in posts.items()}, tmp_path / "cache")
    return con, client


def rows(con):
    return {r[0]: r[1:] for r in con.execute(
        "SELECT url, status, title, published_at, author, word_count, text IS NOT NULL, html_hash IS NOT NULL FROM posts"
    ).fetchall()}


def test_fetch_marks_status_and_keeps_feed_metadata(setup):
    con, client = setup
    assert fetch(con, client, BLOGS, limit=10) == {"fetched": 2, "too_short": 1, "failed": 1}
    r = rows(con)
    # The feed's title and date win; extraction fills in the author.
    assert r["https://a.example.com/medium"][:4] == ("fetched", "Feed title", dt.datetime(2024, 9, 11), "Ana Lima")
    # A sitemap URL had no title or date: extraction provides them.
    assert r["https://b.example.com/wp"][:3] == ("fetched", "How We Run CDC at Scale", dt.datetime(2023, 3, 1))
    assert r["https://a.example.com/short"][0] == "too_short"
    assert r["https://a.example.com/short"][5] is False  # short posts' text isn't kept
    assert r["https://a.example.com/gone"][:3] == ("failed", "Gone", dt.datetime(2020, 1, 1))


def test_retry_failed_refetches_only_failed_posts(setup):
    con, client = setup
    fetch(con, client, BLOGS, limit=10)
    client.session.routes["https://a.example.com/gone"] = page("custom.html")  # the outage is over
    assert fetch(con, client, BLOGS, limit=10) == {"fetched": 0, "too_short": 0, "failed": 0}
    assert fetch(con, client, BLOGS, limit=10, retry_failed=True) == {"fetched": 1, "too_short": 0, "failed": 0}
    assert rows(con)["https://a.example.com/gone"][0] == "fetched"


def test_blogs_removed_from_config_are_not_fetched(setup):
    con, client = setup
    assert fetch(con, client, BLOGS[:1], limit=10) == {"fetched": 1, "too_short": 1, "failed": 1}
    assert rows(con)["https://b.example.com/wp"][0] == "new"


def test_limit_takes_blogs_in_turn_and_rerun_fetches_nothing(setup):
    con, client = setup
    fetch(con, client, BLOGS, limit=2)
    fetched = {u for u, (status, *_) in rows(con).items() if status != "new"}
    assert {u.split("/")[2] for u in fetched} == {"a.example.com", "b.example.com"}
    fetch(con, client, BLOGS, limit=10)
    calls = len(client.session.calls)
    assert fetch(con, client, BLOGS, limit=10) == {"fetched": 0, "too_short": 0, "failed": 0}
    assert len(client.session.calls) == calls
