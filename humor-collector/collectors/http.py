"""Polite HTTP client shared by all collectors.

Responsibilities:
- per-host minimum interval between requests (max of configured delay and robots.txt Crawl-delay)
- robots.txt compliance (protego: supports ``*``/``$`` wildcards and Crawl-delay)
- timeouts and bounded retries with exponential backoff for transient failures
- detection of bot challenges / CAPTCHA pages -> ``AccessBlocked`` (never bypassed)

The retry loop is written out here rather than delegated to urllib3's ``Retry``
because two things must happen *between* attempts: the per-host rate limiter has
to gate every attempt, and a challenge page must stop retries immediately instead
of being retried like an ordinary 503.
"""
from __future__ import annotations

import logging
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import requests
from protego import Protego

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}
# Markers of anti-bot interstitials. Checked on non-2xx responses, and on 2xx only via <title>.
CHALLENGE_BODY_MARKERS = (
    "challenges.cloudflare.com",
    "cf-chl",
    "cf_chl_opt",
    "Just a moment...",
    "turnstile",
    "captcha",
    "보안 검사",
    "보안 시스템",
)
CHALLENGE_TITLES = ("just a moment", "보안 검사", "보안 시스템", "attention required")
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_-]+)""", re.I)


class FetchError(Exception):
    def __init__(self, message: str, url: str = "", status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.url = url
        self.status = status
        self.body = body            # error response body (e.g. an API's JSON error), truncated


class AccessBlocked(FetchError):
    """The site answered with a challenge/CAPTCHA or an explicit bot block.

    Collectors must not try to get around this; the source is paused instead.
    """


class RobotsDisallowed(FetchError):
    """robots.txt does not allow this URL for our user agent."""


@dataclass
class Response:
    url: str
    status: int
    text: str
    headers: dict[str, str]


def decode_body(resp: requests.Response) -> str:
    """Decode with the header charset, else the page's <meta charset>, else detection.

    ``requests`` falls back to ISO-8859-1 for ``text/html`` without a charset, which
    garbles Korean pages that only declare UTF-8/EUC-KR in a meta tag (e.g. 보배드림).
    """
    content_type = resp.headers.get("Content-Type", "").lower()
    if "charset=" in content_type:
        return resp.text
    if "json" in content_type:                   # RFC 8259: JSON is UTF-8
        return resp.content.decode("utf-8", errors="replace")
    match = _META_CHARSET.search(resp.content[:4096])
    encoding = match.group(1).decode("ascii") if match else (resp.apparent_encoding or "utf-8")
    try:
        return resp.content.decode(encoding, errors="replace")
    except LookupError:
        return resp.content.decode("utf-8", errors="replace")


def detect_block(status: int, headers: dict[str, str], text: str) -> str | None:
    """Return a human-readable reason if the response is a bot challenge, else None."""
    lowered_headers = {k.lower(): v for k, v in headers.items()}
    if lowered_headers.get("cf-mitigated", "").lower() == "challenge":
        return f"Cloudflare challenge (HTTP {status}, cf-mitigated: challenge)"
    head = text[:20000]
    head_lower = head.lower()
    title = ""
    start = head_lower.find("<title>")
    if start != -1:
        end = head_lower.find("</title>", start)
        title = head_lower[start + 7 : end if end != -1 else start + 200].strip()
    if any(t in title for t in CHALLENGE_TITLES):
        return f"challenge page (HTTP {status}, title={title[:60]!r})"
    if status == 430:
        return "HTTP 430 (site security system)"
    if status in (401, 403, 429, 503) and any(m.lower() in head_lower for m in CHALLENGE_BODY_MARKERS):
        return f"bot protection page (HTTP {status})"
    return None


class PoliteHttpClient:
    def __init__(
        self,
        user_agent: str,
        *,
        timeout: float = 20.0,
        max_retries: int = 3,
        backoff_base: float = 2.0,
        backoff_max: float = 60.0,
        retry_after_max: float = 120.0,
        default_delay: float = 3.0,
        host_delays: dict[str, float] | None = None,
        respect_robots: bool = True,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.retry_after_max = retry_after_max
        self.default_delay = default_delay
        self.host_delays = dict(host_delays or {})
        self.respect_robots = respect_robots
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            }
        )
        self._sleep = sleep
        self._clock = clock
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, Protego | None] = {}   # None = allow all (robots.txt absent)
        self._lock = threading.Lock()
        self.request_count = 0
        self.secrets: set[str] = set()   # values (API keys) scrubbed from every log line and error

    # ------------------------------------------------------------------ public
    def get(self, url: str, *, params: dict | None = None, accept: str | None = None) -> Response:
        if self.respect_robots:
            self._check_robots(url)
        return self._get_with_retries(url, params=params, accept=accept)

    def scrub(self, text: str) -> str:
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "***")
        return text

    def delay_for(self, url: str) -> float:
        host = urlsplit(url).netloc
        delay = self.host_delays.get(host, self.default_delay)
        robots = self._robots.get(host)
        if robots is not None:
            crawl_delay = robots.crawl_delay(self.user_agent)
            if crawl_delay:
                delay = max(delay, float(crawl_delay))
        return delay

    # ---------------------------------------------------------------- internals
    def _wait_turn(self, url: str) -> None:
        host = urlsplit(url).netloc
        with self._lock:
            delay = self.delay_for(url)
            last = self._last_request.get(host)
            now = self._clock()
            if last is not None and now - last < delay:
                wait = delay - (now - last)
                log.debug("rate limit: sleeping %.1fs before %s", wait, host)
                self._sleep(wait)
            self._last_request[host] = self._clock()

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.retry_after_max)
            except ValueError:
                pass
        base = min(self.backoff_base ** attempt, self.backoff_max)
        return base + random.uniform(0, base / 4)

    def _get_with_retries(self, url: str, *, params: dict | None, accept: str | None) -> Response:
        headers = {"Accept": accept} if accept else None
        last_error: str = ""
        for attempt in range(1, self.max_retries + 2):   # 1 try + max_retries retries
            self._wait_turn(url)
            self.request_count += 1
            try:
                resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_error = self.scrub(f"{type(exc).__name__}: {exc}")
                log.warning("attempt %d/%d failed for %s: %s", attempt, self.max_retries + 1, url, last_error)
                if attempt <= self.max_retries:
                    self._sleep(self._backoff(attempt, None))
                continue
            text = decode_body(resp)
            hdrs = dict(resp.headers)
            reason = detect_block(resp.status_code, hdrs, text)
            if reason:
                raise AccessBlocked(reason, url=url, status=resp.status_code)
            if resp.status_code in RETRY_STATUSES:
                last_error = f"HTTP {resp.status_code}"
                log.warning("attempt %d/%d for %s returned %s", attempt, self.max_retries + 1, url, last_error)
                if attempt <= self.max_retries:
                    self._sleep(self._backoff(attempt, resp.headers.get("Retry-After")))
                continue
            if resp.status_code >= 400:
                raise FetchError(f"HTTP {resp.status_code}", url=url, status=resp.status_code,
                                 body=self.scrub(text[:4000]))
            return Response(url=resp.url, status=resp.status_code, text=text, headers=hdrs)
        raise FetchError(f"giving up after {self.max_retries + 1} attempts ({last_error})", url=url)

    def _check_robots(self, url: str) -> None:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            self._robots[host] = self._load_robots(f"{parts.scheme}://{host}/robots.txt")
        robots = self._robots[host]
        if robots is not None and not robots.can_fetch(url, self.user_agent):
            raise RobotsDisallowed(f"robots.txt disallows {url}", url=url)

    def _load_robots(self, robots_url: str) -> Protego | None:
        """RFC 9309: 4xx -> no restrictions; 5xx/unreachable -> assume full disallow."""
        try:
            resp = self._get_with_retries(robots_url, params=None, accept="text/plain")
        except AccessBlocked:
            raise
        except FetchError as exc:
            if exc.status is not None and 400 <= exc.status < 500:
                log.info("no robots.txt at %s (HTTP %s): no restrictions", robots_url, exc.status)
                return None
            log.warning("robots.txt unreachable at %s (%s): treating site as disallowed", robots_url, exc)
            return Protego.parse("User-agent: *\nDisallow: /")
        return Protego.parse(resp.text)
