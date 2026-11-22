import datetime as dt
from pathlib import Path
from urllib.parse import quote

import pytest

from dataengreads.db import connect, upsert
from dataengreads.rank import HN_SEARCH, hn_lookup, rank, score
from tests.fakes import make_client

NOW = dt.datetime(2026, 11, 1)
HN_JSON = (Path(__file__).parent / "fixtures" / "hn" / "search.json").read_text()
URL = "https://example.com/blog/lakehouse"


def post(**kw):
    base = {"post_type": "case_study", "word_count": 3000, "scale": "4 PB", "key_decisions": ["a", "b", "c"],
            "tier": 2, "confidence": 1.0, "published_at": NOW, "hn_points": None}
    return base | kw


def test_case_study_beats_announcement():
    assert score(post(), NOW) > score(post(post_type="announcement"), NOW)


@pytest.mark.parametrize("worse", [
    {"word_count": 500}, {"scale": ""}, {"key_decisions": ["a"]},  # shallower
    {"published_at": dt.datetime(2016, 1, 1)}, {"published_at": None},  # older / undated
    {"confidence": 0.5},
])
def test_each_signal_moves_the_score(worse):
    assert score(post(**worse), NOW) < score(post(), NOW)


def test_tier_and_hn_raise_the_score():
    assert score(post(tier=1), NOW) > score(post(), NOW)
    assert score(post(hn_points=500), NOW) > score(post(hn_points=10), NOW) > score(post(), NOW)


def test_freshness_is_light():
    old = score(post(published_at=dt.datetime(2016, 1, 1)), NOW)
    assert score(post(), NOW) - old < 0.1  # a 10-year-old case study still beats a new announcement
    assert old > score(post(post_type="announcement"), NOW)


def test_hn_lookup_matches_the_exact_url_and_takes_top_story(tmp_path):
    client = make_client({HN_SEARCH.format(quote(URL, safe="")): HN_JSON}, tmp_path)
    assert hn_lookup(client, URL) == (412, 150)  # not the 900-point story on another site
    assert hn_lookup(make_client({HN_SEARCH.format(quote(URL + "x", safe="")): HN_JSON}, tmp_path),
                     URL + "x") == (0, 0)
    assert hn_lookup(make_client({}, tmp_path / "empty"), URL) is None  # 404 -> retry next run


@pytest.fixture
def con(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    upsert(con, "blogs", [{"company": "Ex", "tier": 1}])
    upsert(con, "posts", [{"url": u, "blog": "Ex", "title": u, "word_count": 2000, "published_at": NOW,
                           "status": "fetched"} for u in (URL, "https://example.com/web")])
    upsert(con, "classifications", [
        {"url": URL, "is_de": True, "confidence": 0.9, "post_type": "case_study", "scale": "",
         "key_decisions": ["a"]},
        {"url": "https://example.com/web", "is_de": False, "confidence": 0.9, "post_type": "case_study",
         "scale": "", "key_decisions": []},
    ])
    return con


def test_rank_scores_only_de_posts_and_keeps_hn(con, tmp_path):
    client = make_client({HN_SEARCH.format(quote(URL, safe="")): HN_JSON}, tmp_path / "cache")
    assert rank(con, client, NOW) == {"ranked": 1, "hn_looked_up": 1}
    assert con.execute("SELECT url, hn_points, hn_comments FROM rankings").fetchall() == [(URL, 412, 150)]
    first = con.execute("SELECT score FROM rankings").fetchone()[0]
    assert rank(con, None, NOW) == {"ranked": 1, "hn_looked_up": 0}  # no client: HN kept from last run
    assert con.execute("SELECT hn_points, score FROM rankings").fetchone() == (412, first)
