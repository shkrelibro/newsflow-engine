"""HTTP access with identification, rate limits, retries and robots handling.

Policy (documented in README):
  * Feed URLs (RSS/Atom, Google News RSS, Bing News RSS, GDELT API) are fetched
    the way a feed reader does: identified user agent, low frequency, no crawling.
  * HTML page watchers honour robots.txt and are polled at low frequency with
    change detection; nothing is crawled beyond the configured page.
"""
from __future__ import annotations

import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit

import httpx

FetchText = Callable[[str], str]


class FetchError(Exception):
    pass


@dataclass
class RateLimiter:
    """Minimum spacing between requests, per host, thread-safe."""

    default_seconds: float = 1.0
    per_host: dict[str, float] = field(default_factory=dict)
    _last: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait(self, host: str) -> None:
        spacing = self.per_host.get(host, self.default_seconds)
        with self._lock:
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            delay = max(0.0, last + spacing - now)
            self._last[host] = now + delay
        if delay > 0:
            time.sleep(delay)


class Http:
    def __init__(
        self,
        user_agent: str,
        timeout: float = 20.0,
        retries: int = 2,
        limiter: Optional[RateLimiter] = None,
        honour_robots_for_pages: bool = True,
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.limiter = limiter or RateLimiter()
        self.honour_robots_for_pages = honour_robots_for_pages
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "*"},
            timeout=timeout,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------
    def get(self, url: str, *, is_page: bool = False, accept: str = "") -> httpx.Response:
        host = urlsplit(url).netloc
        if is_page and self.honour_robots_for_pages and not self.allowed_by_robots(url):
            raise FetchError(f"robots.txt disallows {url}")
        headers = {"Accept": accept} if accept else {}
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            self.limiter.wait(host)
            try:
                resp = self._client.get(url, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = FetchError(f"HTTP {resp.status_code} for {url}")
                    time.sleep(2.0 * (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} for {url}")
                return resp
            except (httpx.HTTPError, OSError) as exc:
                last_exc = exc
                time.sleep(1.0 * (attempt + 1))
        raise FetchError(str(last_exc) if last_exc else f"failed {url}")

    def get_text(self, url: str, *, is_page: bool = False, accept: str = "") -> str:
        return self.get(url, is_page=is_page, accept=accept).text

    def head_final_url(self, url: str) -> str:
        """Resolve redirects without reading the body; returns the final URL."""
        host = urlsplit(url).netloc
        self.limiter.wait(host)
        try:
            resp = self._client.head(url)
            if resp.status_code < 400 and str(resp.url):
                return str(resp.url)
            resp = self._client.get(url)
            return str(resp.url) if resp.status_code < 400 else url
        except (httpx.HTTPError, OSError):
            return url

    # ------------------------------------------------------------------
    def allowed_by_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                resp = self._client.get(base + "/robots.txt")
                if resp.status_code >= 400:
                    self._robots[base] = None  # no robots file: allowed
                else:
                    rp.parse(resp.text.splitlines())
                    self._robots[base] = rp
            except (httpx.HTTPError, OSError):
                self._robots[base] = None
        rp = self._robots[base]
        if rp is None:
            return True
        return rp.can_fetch(self.user_agent, url) or rp.can_fetch("*", url)

    def close(self) -> None:
        self._client.close()
