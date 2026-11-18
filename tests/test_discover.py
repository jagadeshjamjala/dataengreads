import pytest

from dataengreads.db import connect
from dataengreads.discover import discover, find_feed, find_sitemap
from tests.fakes import fixture, make_client

EXAMPLE = {"company": "Example", "url": "https://eng.example.com/blog", "industry": "saas", "tier": 1}
PLAIN = {"company": "Plain", "url": "https://plain.example.org", "industry": "media", "tier": 2}
EMPTY = {"company": "Empty", "url": "https://empty.example.net", "industry": "saas", "tier": 2}

ROUTES = {
    "https://eng.example.com/robots.txt": fixture("robots.txt"),
    "https://eng.example.com/blog": fixture("homepage_with_link.html"),
    "https://eng.example.com/blog/rss.xml": fixture("rss.xml"),
    "https://plain.example.org": fixture("homepage_plain.html"),
    "https://plain.example.org/feed": fixture("homepage_plain.html"),  # HTML, not a feed
    "https://plain.example.org/atom.xml": fixture("atom.xml"),
    "https://plain.example.org/sitemap.xml": fixture("sitemap.xml"),
    "https://empty.example.net": fixture("homepage_plain.html"),
}


@pytest.fixture
def client(tmp_path):
    return make_client(dict(ROUTES), tmp_path / "cache")


def test_feed_from_homepage_link(client):
    assert find_feed(client, EXAMPLE) == "https://eng.example.com/blog/rss.xml"


def test_feed_from_common_path_skips_html(client):
    assert find_feed(client, PLAIN) == "https://plain.example.org/atom.xml"


def test_medium_feed_without_fetching_homepage(tmp_path):
    blog = {"company": "Airbnb", "url": "https://medium.com/airbnb-engineering"}
    client = make_client({"https://medium.com/feed/airbnb-engineering": fixture("rss.xml")}, tmp_path)
    assert find_feed(client, blog) == "https://medium.com/feed/airbnb-engineering"
    assert "https://medium.com/airbnb-engineering" not in client.session.calls


def test_sitemap_from_robots_then_fallback(client):
    assert find_sitemap(client, EXAMPLE) == "https://eng.example.com/sitemap_index.xml"
    assert find_sitemap(client, PLAIN) == "https://plain.example.org/sitemap.xml"
    assert find_sitemap(client, EMPTY) is None


def test_config_overrides_win_without_requests(client):
    blog = {**EMPTY, "feed": "https://x.com/feed", "sitemap": "https://x.com/sitemap.xml"}
    assert (find_feed(client, blog), find_sitemap(client, blog)) == ("https://x.com/feed", "https://x.com/sitemap.xml")
    assert client.session.calls == []


def test_discover_stores_reports_and_resumes(tmp_path, client):
    con = connect(tmp_path / "test.duckdb")
    missing = discover(con, client, [EXAMPLE, PLAIN, EMPTY])
    assert [b["company"] for b in missing] == ["Empty"]
    assert con.execute("SELECT feed_url, sitemap_url FROM blogs WHERE company = 'Example'").fetchone() == (
        "https://eng.example.com/blog/rss.xml", "https://eng.example.com/sitemap_index.xml")

    # Re-run: found blogs are skipped; the empty one is retried, and an override fixes it.
    client.session.calls.clear()
    fixed = {**EMPTY, "feed": "https://empty.example.net/posts.rss"}
    assert discover(con, client, [EXAMPLE, PLAIN, fixed]) == []
    assert not any("eng.example.com" in c or "plain.example.org" in c for c in client.session.calls)
    assert con.execute("SELECT count(*) FROM blogs").fetchone()[0] == 3
