"""HTTP access with identification, rate limits, retries and robots handling.

Policy (documented in README):
  * Feed URLs (RSS/Atom, Google News RSS, Bing News RSS, GDELT API) are fetched
    the way a feed reader does: identified user agent, low frequency, no crawling.
  * HTML page watchers honour robots.txt and are polled at low frequency with
    change detection; nothing is crawled beyond the configured page.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import time
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import urlsplit

import httpx

FetchText = Callable[[str], str]


class FetchError(Exception):
    pass


class BudgetExceeded(FetchError):
    """The run's time budget passed while this request was still queueing for its host.

    Raised instead of sleeping. Without it the budget only stopped NEW jobs from starting: a job
    already in flight against a host on a 60 second penalty would still sit through its three
    attempts, and four workers doing that pushed whole runs past GitHub's 20 minute job limit,
    which cancels the job silently (no exports, no alert, and the database of that run is lost).
    """


@dataclass
class RateLimiter:
    """Minimum spacing between requests, per host, thread-safe, with adaptive backoff.

    The fixed spacing alone is not enough. A run issues around a hundred GDELT jobs, and when
    GDELT starts refusing, each job used to rediscover that independently: two retries, a couple
    of seconds each, then a failure, a hundred times over. Sixteen rate-limited responses in a
    single window. So a host that refuses us widens its own spacing for the rest of the run, and
    a host that answers cleanly earns that back gradually.
    """

    default_seconds: float = 1.0
    per_host: dict[str, float] = field(default_factory=dict)
    max_penalty_seconds: float = 60.0
    _last: dict[str, float] = field(default_factory=dict)
    _penalty: dict[str, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def base_spacing(self, host: str) -> float:
        return self.per_host.get(host, self.default_seconds)

    def penalty(self, host: str) -> float:
        with self._lock:
            return self._penalty.get(host, 0.0)

    def wait(self, host: str, deadline: Optional[float] = None) -> None:
        """Sleep until this host's next slot. With a deadline (time.monotonic()), refuse instead
        of sleeping when the slot would fall after it, and leave the slot unclaimed."""
        with self._lock:
            spacing = self.per_host.get(host, self.default_seconds) + self._penalty.get(host, 0.0)
            now = time.monotonic()
            last = self._last.get(host, 0.0)
            delay = max(0.0, last + spacing - now)
            if deadline is not None and now + delay > deadline:
                raise BudgetExceeded(f"run time budget reached while waiting for {host}")
            self._last[host] = now + delay
        if delay > 0:
            time.sleep(delay)

    def penalise(self, host: str, seconds: Optional[float] = None) -> float:
        """Widen this host's spacing after a refusal. Returns the new penalty.

        With a Retry-After header, honour it. Without one, double, starting from the host's own
        base spacing, and cap it so one bad upstream cannot stall the whole run.
        """
        with self._lock:
            current = self._penalty.get(host, 0.0)
            base = self.per_host.get(host, self.default_seconds)
            if seconds is not None:
                proposed = max(seconds, current)
            else:
                proposed = current * 2 if current else base
            self._penalty[host] = min(self.max_penalty_seconds, proposed)
            return self._penalty[host]

    def relax(self, host: str) -> None:
        """Give back half the penalty after a clean response; a recovered host must not stay slow."""
        with self._lock:
            current = self._penalty.get(host, 0.0)
            if not current:
                return
            nxt = current / 2.0
            if nxt < 0.25:
                self._penalty.pop(host, None)
            else:
                self._penalty[host] = nxt


def retry_after_seconds(resp: "httpx.Response") -> Optional[float]:
    """Parse Retry-After, which is either a count of seconds or an HTTP date."""
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())


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
        # time.monotonic() value set by the run; once passed, no request waits or retries again
        self.deadline: Optional[float] = None
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Language": "*"},
            timeout=timeout,
            follow_redirects=True,
        )

    def _nap(self, seconds: float) -> None:
        """Sleep between attempts, unless that would carry the run past its deadline."""
        if self.deadline is not None and time.monotonic() + seconds > self.deadline:
            raise BudgetExceeded("run time budget reached before the next attempt")
        time.sleep(seconds)

    # ------------------------------------------------------------------
    def get(self, url: str, *, is_page: bool = False, accept: str = "") -> httpx.Response:
        host = urlsplit(url).netloc
        if is_page and self.honour_robots_for_pages and not self.allowed_by_robots(url):
            raise FetchError(f"robots.txt disallows {url}")
        headers = {"Accept": accept} if accept else {}
        last_exc: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            self.limiter.wait(host, self.deadline)
            try:
                resp = self._client.get(url, headers=headers)
                if resp.status_code == 429 or resp.status_code >= 500:
                    # Remember the refusal on the host, not just in this call, so the next job
                    # against the same host starts out slower instead of walking into it again.
                    hinted = retry_after_seconds(resp)
                    penalty = self.limiter.penalise(host, hinted)
                    last_exc = FetchError(f"HTTP {resp.status_code} for {url}")
                    # A Retry-After is honoured up to the same cap as the penalty. An upstream that
                    # asks for an hour gets the next cycle, not a worker asleep for an hour.
                    if hinted is not None:
                        nap = min(hinted, self.limiter.max_penalty_seconds)
                    else:
                        nap = min(penalty, 2.0 * (attempt + 1))
                    self._nap(nap)
                    continue
                if resp.status_code >= 400:
                    raise FetchError(f"HTTP {resp.status_code} for {url}")
                self.limiter.relax(host)
                return resp
            except (httpx.HTTPError, OSError) as exc:
                last_exc = exc
                self._nap(1.0 * (attempt + 1))
        raise FetchError(str(last_exc) if last_exc else f"failed {url}")

    def get_text(self, url: str, *, is_page: bool = False, accept: str = "") -> str:
        return self.get(url, is_page=is_page, accept=accept).text

    def head_final_url(self, url: str) -> str:
        """Resolve redirects without reading the body; returns the final URL."""
        host = urlsplit(url).netloc
        try:
            self.limiter.wait(host, self.deadline)
        except BudgetExceeded:
            return url          # unresolved is a valid answer; a crashed run is not
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
