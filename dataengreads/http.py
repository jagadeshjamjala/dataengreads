"""Polite HTTP client: honours robots.txt, 1 request/second per domain, retries, on-disk cache."""
import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

REPO_URL = "https://github.com/jagadeshjamjala/dataengreads"
USER_AGENT = f"DataEngReads/0.1 (+{REPO_URL})"
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
RETRY_STATUS = {429, 500, 502, 503, 504}


class PoliteClient:
    def __init__(self, session=None, cache_dir: Path = CACHE_DIR, delay: float = 1.0, retries: int = 3,
                 clock=time.monotonic, sleep=time.sleep):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.cache_dir = Path(cache_dir)
        self.delay, self.retries = delay, retries
        self.clock, self.sleep = clock, sleep
        self.requests = 0
        self._last: dict[str, float] = {}             # host -> time of its last request
        self._robots: dict[str, RobotFileParser] = {}

    def get(self, url: str, cache: bool = True) -> str | None:
        """Page text, or None if robots.txt disallows it or it can't be fetched (404, server down)."""
        path = self.cache_dir / hashlib.sha256(url.encode()).hexdigest()
        if cache and path.exists():
            return path.read_text()
        if not self.robots(url).can_fetch(USER_AGENT, url):
            return None
        resp = self._request(url)
        if resp is None or resp.status_code != 200:
            return None
        if cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(resp.text)
        return resp.text

    def robots(self, url: str) -> RobotFileParser:
        """The host's parsed robots.txt, fetched once per run."""
        parts = urlparse(url)
        if parts.netloc not in self._robots:
            parser = RobotFileParser()
            resp = self._request(f"{parts.scheme}://{parts.netloc}/robots.txt")
            if resp is None:
                parser.parse(["User-agent: *", "Disallow: /"])  # server unreachable: assume we may not crawl
            else:
                # RFC 9309: a missing robots.txt (4xx) means everything is allowed.
                parser.parse(resp.text.splitlines() if resp.status_code == 200 else [])
            self._robots[parts.netloc] = parser
        return self._robots[parts.netloc]

    def _request(self, url: str) -> requests.Response | None:
        """GET with the per-domain delay and retries. None if the server never gave a usable answer."""
        host = urlparse(url).netloc
        for attempt in range(self.retries):
            wait = self._last.get(host, float("-inf")) + self.delay - self.clock()
            if wait > 0:
                self.sleep(wait)
            self._last[host] = self.clock()
            self.requests += 1
            try:
                resp = self.session.get(url, timeout=30)
                if resp.status_code not in RETRY_STATUS:
                    return resp
            except requests.RequestException:
                pass
            if attempt < self.retries - 1:
                self.sleep(2 ** attempt)
        return None
