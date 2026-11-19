import datetime

import pytest
import yaml

from dataengreads.config import ConfigError, load_blogs, load_taxonomy

GOOD_BLOGS = {
    "defaults": {"min_words": 400, "backfill_since": datetime.date(2016, 1, 1)},
    "blogs": [
        {"company": "Netflix", "url": "https://netflixtechblog.com", "industry": "media", "tier": 1},
        {"company": "Stripe", "url": "https://stripe.com/blog", "industry": "fintech", "tier": 2, "min_words": 200},
    ],
}
GOOD_TAXONOMY = {
    "problems": [{"id": "cost", "label": "Cost", "hint": "savings"}],
    "post_types": ["case_study"],
    "levels": ["advanced"],
    "extract": ["stack"],
}


def write(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


def test_real_blogs_load():
    cfg = load_blogs()
    assert len(cfg["blogs"]) >= 20
    assert any(b["industry"] == "fintech" for b in cfg["blogs"])
    assert all(b["min_words"] == 400 for b in cfg["blogs"])


def test_real_taxonomy_loads():
    tax = load_taxonomy()
    assert "cdc_sync" in {p["id"] for p in tax["problems"]}
    assert "case_study" in tax["post_types"]


def test_blog_defaults_merged_and_overridable(tmp_path):
    blogs = load_blogs(write(tmp_path, GOOD_BLOGS))["blogs"]
    assert [b["min_words"] for b in blogs] == [400, 200]
    assert blogs[0]["backfill_since"] == datetime.date(2016, 1, 1)


@pytest.mark.parametrize("change, message", [
    (lambda d: d["blogs"].append(dict(d["blogs"][0], url="https://other.com")), "duplicate"),
    (lambda d: d["blogs"].append(dict(d["blogs"][0], company="Other")), "duplicate"),
    (lambda d: d["blogs"][0].update(tier=3), "tier"),
    (lambda d: d["blogs"][0].update(url="netflixtechblog.com"), "url"),
    (lambda d: d["blogs"][0].update(feed="netflixtechblog.com/feed"), "feed"),
    (lambda d: d["blogs"][0].update(sitemap="ftp://x.com/sitemap.xml"), "sitemap"),
    (lambda d: d["blogs"][0].update(post_prefix="blog"), "post_prefix"),
    (lambda d: d["blogs"][0].update(delay=0.2), "delay"),
    (lambda d: d["blogs"][0].update(delay="5"), "delay"),
    (lambda d: d["blogs"][0].pop("industry"), "industry"),
    (lambda d: d["defaults"].update(min_words=0), "min_words"),
    (lambda d: d["defaults"].update(backfill_since="2016-13-01"), "backfill_since"),
    (lambda d: d.update(blogs=[]), "no blogs"),
])
def test_bad_blogs_rejected(tmp_path, change, message):
    data = yaml.safe_load(yaml.safe_dump(GOOD_BLOGS))
    change(data)
    with pytest.raises(ConfigError, match=message):
        load_blogs(write(tmp_path, data))


@pytest.mark.parametrize("change, message", [
    (lambda d: d["problems"].append(dict(d["problems"][0])), "duplicate"),
    (lambda d: d["problems"][0].pop("hint"), "hint"),
    (lambda d: d.update(problems=[]), "no problems"),
    (lambda d: d.update(post_types=[]), "post_types"),
])
def test_bad_taxonomy_rejected(tmp_path, change, message):
    data = yaml.safe_load(yaml.safe_dump(GOOD_TAXONOMY))
    change(data)
    with pytest.raises(ConfigError, match=message):
        load_taxonomy(write(tmp_path, data))
