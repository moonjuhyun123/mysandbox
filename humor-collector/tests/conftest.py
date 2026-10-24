from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pytest
import requests

PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from collectors.http import PoliteHttpClient  # noqa: E402
from pipeline import CollectionPipeline  # noqa: E402
from processing.classifier import KeywordClassifier  # noqa: E402
from settings import DEFAULTS, deep_merge  # noqa: E402
from storage.db import Database  # noqa: E402

FIXTURES = PROJECT / "tests" / "fixtures"
NOW = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)      # 11:00 KST, when fixtures were captured
ALLOW_ALL_ROBOTS = "User-agent: *\nAllow: /\n"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakeResponse:
    def __init__(self, url: str, status: int = 200, text: str = "", headers: dict | None = None,
                 content: bytes | None = None):
        self.url = url
        self.status_code = status
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")
        self.headers = headers or {"Content-Type": "text/html; charset=UTF-8"}
        self.apparent_encoding = "utf-8"


Route = Callable[[str], "FakeResponse | Exception | tuple[int, str]"]


class FakeSession:
    """Stands in for requests.Session: maps URLs to canned responses, records every call.

    ``routes`` maps an exact URL (query string included) or a URL prefix ending in ``*`` to
    a string (200 body), a ``(status, body)`` tuple, an exception instance to raise, or a
    callable taking the URL. robots.txt is allowed for every host unless routed explicitly.
    """

    def __init__(self, routes: dict[str, object] | None = None):
        self.routes = dict(routes or {})
        self.headers: dict[str, str] = {}
        self.calls: list[str] = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(url)
        target = self._match(url)
        if target is None:
            if url.endswith("/robots.txt"):
                return FakeResponse(url, 200, ALLOW_ALL_ROBOTS, {"Content-Type": "text/plain"})
            return FakeResponse(url, 404, "not found")
        if callable(target) and not isinstance(target, (str, tuple)):
            target = target(url)
        if isinstance(target, BaseException):
            raise target
        if isinstance(target, FakeResponse):
            return target
        if isinstance(target, tuple):
            status, body = target
            return FakeResponse(url, status, body)
        return FakeResponse(url, 200, target)

    def _match(self, url: str):
        if url in self.routes:
            return self.routes[url]
        for key, value in self.routes.items():
            if key.endswith("*") and url.startswith(key[:-1]):
                return value
        return None

    def count(self, prefix: str) -> int:
        return sum(1 for c in self.calls if c.startswith(prefix))


def make_settings(tmp_path: Path, sources: dict | None = None, **general) -> dict:
    settings = deep_merge(DEFAULTS, {"sources": sources or {}})
    settings["general"].update(
        db_path=str(tmp_path / "test.db"), log_dir=str(tmp_path / "logs"),
        export_dir=str(tmp_path / "exports"), lock_file=str(tmp_path / "test.lock"),
    )
    settings["general"].update(general)
    return settings


class Clock:
    def __init__(self, start: datetime = NOW):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make_pipeline(tmp_path):
    """Build a pipeline wired to a FakeSession; returns (pipeline, session, db)."""
    created: list[Database] = []

    def factory(routes: dict, sources: dict, *, clock: Callable[[], datetime] | None = None,
                db: Database | None = None, **general):
        settings = make_settings(tmp_path, sources, **general)
        session = FakeSession(routes)
        http = PoliteHttpClient("test-agent/1.0", session=session, sleep=lambda s: None,
                                default_delay=0, max_retries=2, backoff_base=0)
        database = db or Database(settings["general"]["db_path"])
        created.append(database)
        pipeline = CollectionPipeline(database, http, KeywordClassifier(), settings,
                                      clock=clock or Clock())
        return pipeline, session, database

    yield factory
    for database in created:
        try:
            database.close()
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Any test that forgets to inject a session must not reach the internet."""
    def refuse(self, method, url, *args, **kwargs):
        if not url.startswith("http://127.0.0.1"):
            raise AssertionError(f"unexpected real network call to {url}")
        return original(self, method, url, *args, **kwargs)
    original = requests.Session.request
    monkeypatch.setattr(requests.Session, "request", refuse)
