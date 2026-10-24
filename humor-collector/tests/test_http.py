"""PoliteHttpClient: retries, timeouts, robots.txt, rate limiting, challenge detection."""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from collectors.http import AccessBlocked, FetchError, PoliteHttpClient, RobotsDisallowed, detect_block
from tests.conftest import FakeResponse, FakeSession, fixture_text


class _Handler(BaseHTTPRequestHandler):
    script: dict[str, list] = {}       # path -> list of (status, body, delay) consumed in order
    hits: dict[str, int] = {}

    def do_GET(self):  # noqa: N802
        self.hits[self.path] = self.hits.get(self.path, 0) + 1
        steps = self.script.get(self.path) or [(404, "missing", 0)]
        status, body, delay = steps.pop(0) if len(steps) > 1 else steps[0]
        if delay:
            time.sleep(delay)
        data = body.encode()
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    _Handler.script, _Handler.hits = {}, {}
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def client(**kw) -> PoliteHttpClient:
    defaults = dict(default_delay=0, backoff_base=0.01, max_retries=2, timeout=2.0, sleep=lambda s: None)
    defaults.update(kw)
    return PoliteHttpClient("test-agent/1.0", **defaults)


def test_transient_5xx_is_retried_then_succeeds(server):
    _, base = server
    _Handler.script = {"/robots.txt": [(404, "", 0)],
                       "/page": [(503, "busy", 0), (502, "bad gateway", 0), (200, "<p>ok</p>", 0)]}
    sleeps: list[float] = []
    resp = client(sleep=sleeps.append).get(base + "/page")
    assert resp.status == 200 and resp.text == "<p>ok</p>"
    assert _Handler.hits["/page"] == 3
    assert len(sleeps) == 2                                   # backoff between the attempts


def test_timeout_is_retried(server):
    _, base = server
    _Handler.script = {"/robots.txt": [(404, "", 0)],
                       "/slow": [(200, "late", 1.5), (200, "fast", 0)]}
    resp = client(timeout=0.5).get(base + "/slow")
    assert resp.text == "fast"
    assert _Handler.hits["/slow"] == 2


def test_gives_up_after_max_retries(server):
    _, base = server
    _Handler.script = {"/robots.txt": [(404, "", 0)], "/down": [(503, "down", 0)]}
    with pytest.raises(FetchError, match="giving up after 3 attempts"):
        client().get(base + "/down")
    assert _Handler.hits["/down"] == 3


def test_404_is_not_retried(server):
    _, base = server
    _Handler.script = {"/robots.txt": [(404, "", 0)], "/gone": [(404, "gone", 0)]}
    with pytest.raises(FetchError) as info:
        client().get(base + "/gone")
    assert info.value.status == 404 and _Handler.hits["/gone"] == 1


def test_connection_refused_is_retried_and_reported():
    sleeps: list[float] = []
    c = client(sleep=sleeps.append, respect_robots=False, timeout=0.5)
    with pytest.raises(FetchError, match="ConnectionError"):
        c.get("http://127.0.0.1:9/nothing-listens-here")
    assert len(sleeps) == 2


def test_challenge_page_raises_access_blocked_without_retry():
    session = FakeSession({"https://example.com/list": (403, fixture_text("challenge_cloudflare.html"))})
    c = PoliteHttpClient("t", session=session, sleep=lambda s: None, default_delay=0)
    with pytest.raises(AccessBlocked, match="challenge"):
        c.get("https://example.com/list")
    assert session.count("https://example.com/list") == 1


def test_detect_block_variants():
    assert detect_block(403, {}, fixture_text("challenge_cloudflare.html"))
    assert detect_block(430, {}, fixture_text("challenge_fmkorea_430.html"))
    assert detect_block(403, {"cf-mitigated": "challenge"}, "")
    assert detect_block(200, {}, fixture_text("ruliweb_post.html")) is None
    # a normal page that merely mentions a captcha image is not a block
    assert detect_block(200, {}, "<html><title>게시판</title><img src='/kcaptcha/image'></html>") is None


def test_robots_disallow_and_crawl_delay():
    robots = "User-agent: *\nCrawl-delay: 10\nDisallow: /private\nDisallow: /*?sort=\n"
    session = FakeSession({"https://site.test/robots.txt": robots, "https://site.test/*": "<p>ok</p>"})
    c = PoliteHttpClient("t", session=session, sleep=lambda s: None, default_delay=1)
    assert c.get("https://site.test/list?page=2").status == 200
    with pytest.raises(RobotsDisallowed):
        c.get("https://site.test/private/1")
    with pytest.raises(RobotsDisallowed):
        c.get("https://site.test/list?sort=top")             # wildcard rule (protego)
    assert c.delay_for("https://site.test/list") == 10       # Crawl-delay beats default_delay


def test_robots_missing_allows_and_robots_5xx_disallows():
    session = FakeSession({"https://a.test/robots.txt": (404, ""), "https://b.test/robots.txt": (500, "err"),
                           "https://a.test/*": "ok", "https://b.test/*": "ok"})
    c = PoliteHttpClient("t", session=session, sleep=lambda s: None, default_delay=0, max_retries=0)
    assert c.get("https://a.test/x").text == "ok"
    with pytest.raises(RobotsDisallowed):
        c.get("https://b.test/x")


def test_per_host_rate_limit_uses_clock():
    now = [100.0]
    sleeps: list[float] = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    session = FakeSession({"https://h.test/*": "ok", "https://other.test/*": "ok"})
    c = PoliteHttpClient("t", session=session, sleep=fake_sleep, clock=lambda: now[0], default_delay=3,
                         respect_robots=False)
    c.get("https://h.test/1")
    now[0] += 1.0
    c.get("https://h.test/2")                                  # 1s after the first -> waits 2s
    c.get("https://other.test/1")                              # other host: no wait
    assert sleeps == [pytest.approx(2.0)]


def test_retry_after_header_is_capped():
    responses = iter([FakeResponse("u", 429, "slow down", {"Retry-After": "3600"}), FakeResponse("u", 200, "ok")])
    session = FakeSession({"https://r.test/x": lambda url: next(responses)})
    sleeps: list[float] = []
    c = PoliteHttpClient("t", session=session, sleep=sleeps.append, default_delay=0, respect_robots=False,
                         retry_after_max=120)
    assert c.get("https://r.test/x").text == "ok"
    assert sleeps == [120]


@pytest.mark.parametrize("encoding,meta", [("utf-8", "utf-8"), ("euc-kr", "euc-kr")])
def test_html_without_header_charset_uses_meta_charset(encoding, meta):
    html = f'<html><head><meta http-equiv="Content-Type" content="text/html; charset={meta}"></head>' \
           '<body><th>제목</th></body></html>'
    raw = html.encode(encoding)
    resp = FakeResponse("https://k.test/", 200, raw.decode("latin-1"), {"Content-Type": "text/html"}, content=raw)
    session = FakeSession({"https://k.test/": resp})
    c = PoliteHttpClient("t", session=session, sleep=lambda s: None, default_delay=0, respect_robots=False)
    assert "<th>제목</th>" in c.get("https://k.test/").text


def test_json_without_charset_is_utf8():
    raw = '{"name": "Café ☕ 농담"}'.encode("utf-8")
    resp = FakeResponse("https://j.test/", 200, raw.decode("latin-1"), {"Content-Type": "application/json"},
                        content=raw)
    resp.apparent_encoding = "ascii"                 # a wrong guess must not matter
    c = PoliteHttpClient("t", session=FakeSession({"https://j.test/": resp}), sleep=lambda s: None,
                         default_delay=0, respect_robots=False)
    assert c.get("https://j.test/").text == '{"name": "Café ☕ 농담"}'
