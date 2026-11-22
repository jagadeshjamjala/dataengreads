"""DuckDB storage: one file, four tables. Raw facts and LLM output stay separate."""
from pathlib import Path

import duckdb

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "dataengreads.duckdb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS blogs (
    company       VARCHAR PRIMARY KEY,
    url           VARCHAR,
    industry      VARCHAR,
    tier          INTEGER,
    feed_url      VARCHAR,
    sitemap_url   VARCHAR,
    discovered_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS posts (
    url          VARCHAR PRIMARY KEY,
    blog         VARCHAR,          -- blogs.company
    title        VARCHAR,
    published_at TIMESTAMP,
    author       VARCHAR,
    word_count   INTEGER,
    text         VARCHAR,          -- for classification only; never published
    html_hash    VARCHAR,
    fetched_at   TIMESTAMP,
    status       VARCHAR           -- new | fetched | too_short | failed
);
CREATE TABLE IF NOT EXISTS classifications (
    url             VARCHAR PRIMARY KEY,
    is_de           BOOLEAN,
    confidence      DOUBLE,
    primary_problem VARCHAR,
    problems        VARCHAR[],
    post_type       VARCHAR,
    level           VARCHAR,
    stack           VARCHAR[],
    scale           VARCHAR,
    key_decisions   VARCHAR[],
    summary         VARCHAR,
    interview_angle VARCHAR,
    model           VARCHAR,
    classified_at   TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rankings (
    url         VARCHAR PRIMARY KEY,
    score       DOUBLE,
    hn_points   INTEGER,          -- NULL = not looked up yet, 0 = never on Hacker News
    hn_comments INTEGER,
    ranked_at   TIMESTAMP
);
"""


def connect(path: Path | str = DB_PATH) -> duckdb.DuckDBPyConnection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute(SCHEMA)
    return con


def _columns(con, table: str) -> list[str]:
    rows = con.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?", [table]
    ).fetchall()
    if not rows:
        raise ValueError(f"unknown table '{table}'")
    return [r[0] for r in rows]


def upsert(con, table: str, rows: list[dict]) -> int:
    """Insert rows, replacing any with the same primary key. Returns rows written."""
    if not rows:
        return 0
    cols = list(rows[0])
    # Identifiers can't be bound as parameters, so check them against the schema.
    unknown = set(cols) - set(_columns(con, table))
    if unknown:
        raise ValueError(f"{table}: unknown column(s) {sorted(unknown)}")
    sql = f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})"
    con.executemany(sql, [[row[c] for c in cols] for row in rows])
    return len(rows)
