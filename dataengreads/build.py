"""Write the static site: site/index.html (copied) + site/data.js (generated). Never publishes article text."""
import datetime as dt
import json
import shutil
import tempfile
from pathlib import Path

import yaml

from dataengreads.db import connect, upsert
from dataengreads.rank import rank

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"
WEB_DIR = Path(__file__).resolve().parent / "web"
DEMO_FIXTURE = ROOT / "tests" / "fixtures" / "demo.yaml"

# Only these fields reach the site. posts.text is deliberately absent (copyright).
POSTS = """
SELECT p.url, p.blog AS company, b.industry, p.title, strftime(p.published_at, '%Y-%m-%d') AS date,
       c.primary_problem, c.problems, c.post_type, c.level, c.stack, c.scale, c.key_decisions,
       c.summary, c.interview_angle, r.score, coalesce(r.hn_points, 0) AS hn_points,
       coalesce(r.hn_comments, 0) AS hn_comments
FROM rankings r JOIN posts p USING (url) JOIN classifications c USING (url)
LEFT JOIN blogs b ON b.company = p.blog
WHERE c.is_de
ORDER BY r.score DESC
"""


def site_data(con, taxonomy: dict) -> dict:
    cur = con.execute(POSTS)
    cols = [d[0] for d in cur.description]
    return {
        "built_at": dt.date.today().isoformat(),
        "problems": {p["id"]: p["label"] for p in taxonomy["problems"]},
        "post_types": taxonomy["post_types"],
        "questions": taxonomy.get("interview_questions", []),
        "posts": [dict(zip(cols, row)) for row in cur.fetchall()],
    }


def build(con, taxonomy: dict, out_dir: Path = SITE_DIR) -> int:
    """Write the site to out_dir. Returns the number of posts published."""
    data = site_data(con, taxonomy)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # A <script src> file, not JSON: file:// pages can't fetch(). Escaping "<" keeps it safe even if inlined.
    (out_dir / "data.js").write_text("window.DATA = " + json.dumps(data, ensure_ascii=True).replace("<", "\\u003c") + ";\n")
    for name in ("index.html", "og.png"):
        shutil.copyfile(WEB_DIR / name, out_dir / name)
    return len(data["posts"])


def demo(taxonomy: dict, out_dir: Path = SITE_DIR, fixture: Path = DEMO_FIXTURE) -> int:
    """Offline: load made-up fixture posts into a throwaway DB, rank, build. No network, no keys."""
    data = yaml.safe_load(Path(fixture).read_text())
    with tempfile.TemporaryDirectory() as tmp:
        con = connect(Path(tmp) / "demo.duckdb")
        for table in ("blogs", "posts", "classifications"):
            upsert(con, table, data[table])
        rank(con)
        n = build(con, taxonomy, out_dir)
        con.close()
    return n
