from dataengreads.http import REPO_URL
from tests.fakes import CONNECTION_ERROR, fixture, make_client

ROBOTS = "https://eng.example.com/robots.txt"


def test_user_agent_names_the_repo(tmp_path):
    client = make_client({}, tmp_path)
    assert REPO_URL in client.session.headers["User-Agent"]


def test_robots_disallow_is_honoured(tmp_path):
    client = make_client({ROBOTS: fixture("robots.txt"), "https://eng.example.com/private/x": "secret"}, tmp_path)
    assert client.get("https://eng.example.com/private/x") is None
    assert client.session.calls == [ROBOTS]


def test_unreachable_robots_means_do_not_crawl(tmp_path):
    client = make_client({ROBOTS: CONNECTION_ERROR, "https://eng.example.com/a": "page"}, tmp_path)
    assert client.get("https://eng.example.com/a") is None


def test_one_request_per_second_per_domain(tmp_path):
    client = make_client({"https://eng.example.com/a": "a", "https://eng.example.com/b": "b",
                          "https://other.example.org/c": "c"}, tmp_path)
    client.get("https://eng.example.com/a")
    client.get("https://eng.example.com/b")
    client.get("https://other.example.org/c")
    # robots.txt -> /a -> /b on one host wait 1s each; the other host's first request doesn't wait.
    assert client.sleep.__self__.slept == [1.0, 1.0, 1.0]
    assert client.requests == 5


def test_retries_then_succeeds(tmp_path):
    client = make_client({"https://eng.example.com/a": [503, CONNECTION_ERROR, "ok"]}, tmp_path)
    assert client.get("https://eng.example.com/a") == "ok"


def test_gives_up_after_retries_and_on_404(tmp_path):
    client = make_client({"https://eng.example.com/a": [503]}, tmp_path)
    assert client.get("https://eng.example.com/a") is None
    assert client.session.calls.count("https://eng.example.com/a") == 3
    assert client.get("https://eng.example.com/missing") is None


def test_cache_avoids_refetch_across_runs(tmp_path):
    routes = {"https://eng.example.com/a": "page"}
    assert make_client(routes, tmp_path).get("https://eng.example.com/a") == "page"
    second = make_client(routes, tmp_path)
    assert second.get("https://eng.example.com/a") == "page"
    assert second.session.calls == []
    second.get("https://eng.example.com/a", cache=False)
    assert "https://eng.example.com/a" in second.session.calls
