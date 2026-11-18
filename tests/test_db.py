import pytest

from dataengreads.db import connect, upsert

POSTS = [
    {"url": "https://netflixtechblog.com/a", "blog": "Netflix", "title": "A", "status": "new"},
    {"url": "https://netflixtechblog.com/b", "blog": "Netflix", "title": "B", "status": "new"},
]


@pytest.fixture
def con(tmp_path):
    con = connect(tmp_path / "sub" / "test.duckdb")
    yield con
    con.close()


def count(con, table):
    return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_connect_creates_tables_and_is_rerunnable(tmp_path):
    path = tmp_path / "test.duckdb"
    connect(path).close()
    con = connect(path)
    tables = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    assert {"blogs", "posts", "classifications"} <= tables


def test_upsert_twice_does_not_duplicate(con):
    assert upsert(con, "posts", POSTS) == 2
    upsert(con, "posts", [{**POSTS[0], "title": "A v2", "status": "fetched"}, POSTS[1]])
    assert count(con, "posts") == 2
    assert con.execute("SELECT title, status FROM posts WHERE url = ?", [POSTS[0]["url"]]).fetchone() == ("A v2", "fetched")


def test_list_columns_round_trip(con):
    row = {"url": POSTS[0]["url"], "is_de": True, "stack": ["Kafka", "Flink"], "key_decisions": ["x", "y", "z"]}
    upsert(con, "classifications", [row])
    upsert(con, "classifications", [row])
    assert count(con, "classifications") == 1
    assert con.execute("SELECT stack, key_decisions FROM classifications").fetchone() == (["Kafka", "Flink"], ["x", "y", "z"])


def test_upsert_rejects_unknown_column_and_table(con):
    with pytest.raises(ValueError, match="unknown column"):
        upsert(con, "posts", [{"url": "u", "evil; DROP TABLE posts": 1}])
    with pytest.raises(ValueError, match="unknown table"):
        upsert(con, "nope", [{"url": "u"}])


def test_upsert_empty_is_noop(con):
    assert upsert(con, "blogs", []) == 0
