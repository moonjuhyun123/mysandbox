"""YouTube lookup: request parameters, order, raw values, errors, key secrecy, retention, CLI.

No API key was available while writing this, so the fixtures are synthetic responses built from
the official API reference (tests/fixtures/youtube_*.json), not captured live answers."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
import requests

from collectors.http import PoliteHttpClient
from collectors.youtube import QuotaExceeded, YouTubeAPI, YouTubeError
from storage.db import Database
from tests.conftest import NOW, FakeResponse, FakeSession, fixture_text

KEY = "AIzaTEST-not-a-real-key-123"
SEARCH = "https://www.googleapis.com/youtube/v3/search"
VIDEOS = "https://www.googleapis.com/youtube/v3/videos"
COMMENTS = "https://www.googleapis.com/youtube/v3/commentThreads"
JSON = {"Content-Type": "application/json; charset=UTF-8"}


def make_api(routes):
    session = FakeSession(routes)
    http = PoliteHttpClient("t", session=session, sleep=lambda s: None, default_delay=0, max_retries=1,
                            backoff_base=0)
    return YouTubeAPI(http, KEY), session


def ok(name):
    return FakeResponse("u", 200, fixture_text(name), JSON)


def test_search_parameters_order_and_raw_values():
    videos = json.loads(fixture_text("youtube_videos_list.json"))
    videos["items"].reverse()                                    # the API may answer in any order
    api, session = make_api({SEARCH: ok("youtube_search_list.json"),
                             VIDEOS: FakeResponse("u", 200, json.dumps(videos), JSON)})
    ids = api.search_video_ids("담배피는 눈사람", days=7, now=NOW)
    p = session.params_log[-1]
    assert (p["type"], p["order"], p["regionCode"], p["relevanceLanguage"], p["videoDuration"]) == \
        ("video", "viewCount", "KR", "ko", "short")
    assert p["publishedAfter"] == "2026-10-17T02:00:00Z" and p["q"] == "담배피는 눈사람" and p["key"] == KEY
    found = api.videos(ids)
    assert [v.video_id for v in found] == ids                    # search order kept
    first = found[0]
    assert first.view_count == 1843920 and first.like_count == 52311 and first.comment_count == 1204
    assert first.duration == "PT42S" and first.thumbnail_url.startswith("https://i.ytimg.com/vi/")
    assert first.url == f"https://www.youtube.com/watch?v={first.video_id}"
    assert session.params_log[-1]["part"] == "snippet,statistics,contentDetails"


def test_quota_exceeded_is_reported_and_not_retried():
    api, session = make_api({SEARCH: FakeResponse("u", 403, fixture_text("youtube_error_quota.json"), JSON)})
    with pytest.raises(QuotaExceeded) as info:
        api.search_video_ids("밈", now=NOW)
    assert info.value.reason == "quotaExceeded"
    assert session.count(SEARCH) == 1


def test_errors_never_leak_the_api_key():
    api, _ = make_api({SEARCH: FakeResponse("u", 400, fixture_text("youtube_error_key_invalid.json"), JSON),
                       VIDEOS: requests.ConnectionError(f"Max retries exceeded with url: /videos?id=x&key={KEY}")})
    with pytest.raises(YouTubeError) as bad_key:
        api.search_video_ids("밈", now=NOW)
    assert bad_key.value.reason == "keyInvalid" and KEY not in str(bad_key.value)
    with pytest.raises(YouTubeError) as conn:
        api.videos(["abc"])
    assert KEY not in str(conn.value) and "***" in str(conn.value)


def test_comments_are_display_only_without_authors():
    api, _ = make_api({COMMENTS: ok("youtube_comment_threads.json")})
    comments = api.top_comments("Qx7fK2mN9aB", max_results=10)
    assert comments and comments[0].text and comments[0].like_count is not None
    assert not hasattr(comments[0], "author") and "authorDisplayName" not in json.dumps(comments[0].__dict__)
    api, _ = make_api({COMMENTS: FakeResponse("u", 403, fixture_text("youtube_error_comments_disabled.json"), JSON)})
    with pytest.raises(YouTubeError) as info:
        api.top_comments("x")
    assert info.value.reason == "commentsDisabled"


def test_storage_is_separate_replaced_per_query_and_purged(tmp_path):
    db = Database(tmp_path / "y.db")
    api, _ = make_api({SEARCH: ok("youtube_search_list.json"), VIDEOS: ok("youtube_videos_list.json")})
    videos = api.videos(api.search_video_ids("밈", now=NOW))
    db.save_youtube_results("밈", videos, NOW - timedelta(days=30))
    db.save_youtube_results("짤", videos[:1], NOW)
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0   # never mixed into posts
    rows = db.youtube_results("밈")
    assert [r["position"] for r in rows] == [1, 2, 3] and rows[0]["view_count"] == 1843920
    db.save_youtube_results("밈", videos[:2], NOW)                # a new answer replaces the old one
    assert len(db.youtube_results("밈")) == 2
    db.save_youtube_results("old", videos, NOW - timedelta(days=30))
    assert db.purge_youtube(NOW - timedelta(days=29)) == 3
    assert {r["query"] for r in db.youtube_results()} == {"밈", "짤"}
    assert db.purge_youtube(None) == 3 and db.youtube_results() == []
    db.close()


def _config(tmp_path):
    cfg = tmp_path / "settings.toml"
    cfg.write_text(f'[general]\ndb_path = "{tmp_path / "c.db"}"\nlog_dir = "{tmp_path / "logs"}"\n'
                   f'export_dir = "{tmp_path / "ex"}"\nlock_file = "{tmp_path / "l.lock"}"\n'
                   '[youtube]\nkeywords = ["밈"]\n', encoding="utf-8")
    return cfg


def test_cli_without_key_explains_how_to_get_one(tmp_path, monkeypatch, capsys):
    import main
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    assert main.main(["--config", str(_config(tmp_path)), "youtube", "popular"]) == 1
    assert "YOUTUBE_API_KEY" in capsys.readouterr().out


def test_cli_search_prints_attributed_results_and_writes_html(tmp_path, monkeypatch, capsys):
    import main
    session = FakeSession({SEARCH: ok("youtube_search_list.json"), VIDEOS: ok("youtube_videos_list.json")})
    monkeypatch.setenv("YOUTUBE_API_KEY", KEY)
    monkeypatch.setattr(main, "PoliteHttpClient",
                        lambda *a, **k: PoliteHttpClient("t", session=session, sleep=lambda s: None, default_delay=0))
    html = tmp_path / "yt.html"
    assert main.main(["--config", str(_config(tmp_path)), "youtube", "search", "담배피는 눈사람",
                      "--html", str(html)]) == 0
    out = capsys.readouterr().out
    assert "[YouTube 검색: 담배피는 눈사람] 출처: YouTube" in out and "https://www.youtube.com/watch?v=" in out
    page = html.read_text(encoding="utf-8")
    assert page.count('class="tag yt">YouTube<') == 3 and "i.ytimg.com" in page
    assert KEY not in out and KEY not in page
    db = Database(tmp_path / "c.db")
    assert len(db.youtube_results("담배피는 눈사람")) == 3
    db.close()


def test_collect_purges_expired_youtube_rows(tmp_path, monkeypatch):
    import main
    cfg = _config(tmp_path)
    db = Database(tmp_path / "c.db")
    api, _ = make_api({SEARCH: ok("youtube_search_list.json"), VIDEOS: ok("youtube_videos_list.json")})
    videos = api.videos(api.search_video_ids("밈", now=NOW))
    db.save_youtube_results("밈", videos, datetime.now(timezone.utc) - timedelta(days=40))
    db.close()
    assert main.main(["--config", str(cfg), "collect", "--source", "ruliweb", "--limit", "0"]) in (0, 1)
    db = Database(tmp_path / "c.db")
    assert db.youtube_results() == []
    db.close()
