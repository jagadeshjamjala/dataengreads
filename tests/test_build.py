import json

from dataengreads.build import build, demo
from dataengreads.config import load_taxonomy
from dataengreads.db import connect, upsert
from dataengreads.rank import rank

TAX = load_taxonomy()
SECRET = "ARTICLE-BODY-MUST-NEVER-BE-PUBLISHED"


def load(path):
    js = path.read_text()
    assert js.startswith("window.DATA = ") and js.rstrip().endswith(";")
    return json.loads(js[len("window.DATA = "):].rstrip().rstrip(";"))


def test_demo_builds_a_complete_site(tmp_path):
    assert demo(TAX, tmp_path) == 11  # the iOS post is not data engineering
    assert {p.name for p in tmp_path.iterdir()} == {"index.html", "data.js", "og.png"}
    data = load(tmp_path / "data.js")
    scores = [p["score"] for p in data["posts"]]
    assert scores == sorted(scores, reverse=True)
    assert data["problems"]["realtime"] and data["questions"]
    html = (tmp_path / "index.html").read_text()
    assert 'src="data.js"' in html and "og:image" in html and ".innerHTML" not in html


def test_article_text_is_never_published_and_markup_stays_data(tmp_path):
    con = connect(tmp_path / "t.duckdb")
    url = "https://x.example/p"
    upsert(con, "posts", [{"url": url, "blog": "X", "title": "</script><script>alert(1)</script>",
                           "text": SECRET, "word_count": 2000, "status": "fetched"}])
    upsert(con, "classifications", [{"url": url, "is_de": True, "confidence": 0.9, "post_type": "case_study",
                                     "scale": "", "key_decisions": [], "summary": "S"}])
    rank(con)
    build(con, TAX, tmp_path / "site")
    js = (tmp_path / "site" / "data.js").read_text()
    assert SECRET not in js
    assert "</script>" not in js  # "<" is escaped as \\u003c
    post = load(tmp_path / "site" / "data.js")["posts"][0]
    assert post["title"] == "</script><script>alert(1)</script>" and "text" not in post
