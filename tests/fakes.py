"""Offline stand-ins for requests.Session and the clock, shared by the HTTP and discover tests."""
from pathlib import Path

import requests

from dataengreads.http import PoliteClient

FIXTURES = Path(__file__).parent / "fixtures" / "discover"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class FakeResponse:
    def __init__(self, status_code: int, body: str | bytes = ""):
        self.status_code = status_code
        self.content = body if isinstance(body, bytes) else body.encode()
        self.text = self.content.decode(errors="replace")


class FakeSession:
    """routes: url -> page text, a status code, an exception, or a list of those served in turn.
    Unknown URLs are 404."""

    def __init__(self, routes: dict):
        self.routes, self.headers, self.calls = routes, {}, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        answer = self.routes.get(url, 404)
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer) if isinstance(answer, int) else FakeResponse(200, answer)


class FakeClock:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def make_client(routes: dict, cache_dir) -> PoliteClient:
    clock = FakeClock()
    return PoliteClient(session=FakeSession(routes), cache_dir=cache_dir, clock=clock, sleep=clock.sleep)


CONNECTION_ERROR = requests.ConnectionError("down")
