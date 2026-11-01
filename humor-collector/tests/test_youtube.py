"""YouTube lookup: request parameters, order, raw values, errors, key secrecy, retention, quota,
terms acceptance, separation from community data, CLI.

No API key was available while writing this, so the fixtures are synthetic responses built from
the official API reference (tests/fixtures/youtube_*.json), not captured live answers."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
PROJECT = Path(__file__).resolve().parent.parent


def make_api(routes):
    session = FakeSession(routes)
    http = PoliteHttpClient("t", session=session, sleep=lambda s: None, default_delay=0, max_retries=1,
                            backoff_base=0)
    return YouTubeAPI(http, KEY), session


def ok(name):
    return FakeResponse("u", 200, fixture_text(name), JSON)


# ------------------------------------------------------------------ API client
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


def test_comments_keep_api_order_and_attribution():
    api, _ = make_api({COMMENTS: ok("youtube_comment_threads.json")})
    comments = api.top_comments("Qx7fK2mN9aB", max_results=10)
    raw = json.loads(fixture_text("youtube_comment_threads.json"))["items"]
    assert [c.comment_id for c in comments] == [i["snippet"]["topLevelComment"]["id"] for i in raw]
    assert comments[0].author == raw[0]["snippet"]["topLevelComment"]["snippet"]["authorDisplayName"]
    assert comments[0].url("Qx7fK2mN9aB").endswith(f"&lc={comments[0].comment_id}")
    api, _ = make_api({COMMENTS: FakeResponse("u", 403, fixture_text("youtube_error_comments_disabled.json"), JSON)})
    with pytest.raises(YouTubeError) as info:
        api.top_comments("x")
    assert info.value.reason == "commentsDisabled"


# ------------------------------------------------------------------ storage
def test_storage_is_separate_replaced_capped_and_purged(tmp_path):
    db = Database(tmp_path / "y.db")
    api, _ = make_api({SEARCH: ok("youtube_search_list.json"), VIDEOS: ok("youtube_videos_list.json")})
    videos = api.videos(api.search_video_ids("밈", now=NOW))
    db.save_youtube_results("밈", videos, NOW - timedelta(days=2))
    db.save_youtube_results("짤", videos[:1], NOW - timedelta(hours=1))
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0   # never mixed into posts
    rows = db.youtube_results("밈")
    assert [r["position"] for r in rows] == [1, 2, 3] and rows[0]["view_count"] == 1843920
    db.save_youtube_results("밈", videos[:2], NOW)                # a new answer replaces the old one
    assert len(db.youtube_results("밈")) == 2
    page = tmp_path / "old.html"
    page.write_text("x")
    db.record_youtube_file(page, NOW - timedelta(days=2))
    db.save_youtube_results("old", videos, NOW - timedelta(days=2))
    assert db.purge_youtube(NOW - timedelta(hours=24)) == 3 and not page.exists()
    assert db.purge_youtube(NOW - timedelta(days=30), max_rows=2) == 1         # oldest query ("짤") over the cap goes
    assert {r["query"] for r in db.youtube_results()} == {"밈"}
    assert db.purge_youtube(None) == 2 and db.youtube_results() == []
    db.close()


def test_trends_and_scoring_code_never_touch_youtube_data():
    for module in ("processing/trends.py", "processing/scoring.py", "processing/dedup.py", "pipeline.py"):
        source = (PROJECT / module).read_text(encoding="utf-8")
        for ref in ("youtube_results", "youtube_files", "collectors.youtube", "YouTubeAPI", "YouTubeVideo"):
            assert ref not in source, (module, ref)


# ------------------------------------------------------------------ CLI
def _config(tmp_path, extra=""):
    cfg = tmp_path / "settings.toml"
    cfg.write_text(f'[general]\ndb_path = "{tmp_path / "c.db"}"\nlog_dir = "{tmp_path / "logs"}"\n'
                   f'export_dir = "{tmp_path / "ex"}"\nlock_file = "{tmp_path / "l.lock"}"\n[youtube]\n{extra}',
                   encoding="utf-8")
    return str(cfg)


@pytest.fixture
def cli(tmp_path, monkeypatch):
    import main
    session = FakeSession({SEARCH: ok("youtube_search_list.json"), VIDEOS: ok("youtube_videos_list.json"),
                           COMMENTS: ok("youtube_comment_threads.json")})
    monkeypatch.setenv("YOUTUBE_API_KEY", KEY)
    monkeypatch.setattr(main, "PoliteHttpClient",
                        lambda *a, **k: PoliteHttpClient("t", session=session, sleep=lambda s: None, default_delay=0))
    return main, session


def test_terms_must_be_accepted_first_then_key_is_required(tmp_path, monkeypatch, capsys, cli):
    main, session = cli
    cfg = _config(tmp_path)
    assert main.main(["--config", cfg, "youtube", "search", "밈"]) == 1
    assert "accept-terms" in capsys.readouterr().out and session.calls == []
    assert main.main(["--config", cfg, "youtube", "accept-terms", "--yes"]) == 0
    assert "YouTube 서비스 약관" in capsys.readouterr().out
    monkeypatch.delenv("YOUTUBE_API_KEY")
    assert main.main(["--config", cfg, "youtube", "search", "밈"]) == 1
    assert "YOUTUBE_API_KEY" in capsys.readouterr().out


def test_search_output_page_cache_and_budget(tmp_path, capsys, cli):
    main, session = cli
    cfg = _config(tmp_path, "daily_search_cap = 2\n")
    main.main(["--config", cfg, "youtube", "accept-terms", "--yes"])
    html = tmp_path / "yt.html"
    assert main.main(["--config", cfg, "youtube", "search", "담배피는 눈사람", "--html", str(html)]) == 0
    out = capsys.readouterr().out
    assert "[검색어: 담배피는 눈사람 (7d,short,25 results,KR,ko) - 이 도구가 정한 검색어이며 YouTube 데이터가 아님]" in out
    assert "결과 출처: YouTube" in out and "YouTube에서 보기: https://www.youtube.com/watch?v=" in out
    full_title = json.loads(fixture_text("youtube_videos_list.json"))["items"][0]["snippet"]["title"]
    assert f"   1. {full_title}" in out                               # titles are never shortened
    page = html.read_text(encoding="utf-8")
    assert page.count('class="tag yt">YouTube<') == 3 and "https://www.youtube.com/t/terms" in page
    assert "<title>영상 검색 결과</title>" in page and 'class="whole"' in page   # uncropped thumbnails
    assert KEY not in out and KEY not in page

    assert main.main(["--config", cfg, "youtube", "search", "담배피는 눈사람"]) == 0   # cached: no new search
    assert session.count(SEARCH) == 1
    assert main.main(["--config", cfg, "youtube", "search", "음료수 주르륵"]) == 0
    assert main.main(["--config", cfg, "youtube", "search", "세번째"]) == 1           # local cap of 2 reached
    assert "local daily search cap reached" in capsys.readouterr().out
    assert session.count(SEARCH) == 2

    db = Database(tmp_path / "c.db")                             # every command purges expired data + pages
    db.conn.execute("UPDATE youtube_results SET fetched_at = '2000-01-01T00:00:00+00:00'")
    db.conn.execute("UPDATE youtube_files SET written_at = '2000-01-01T00:00:00+00:00'")
    db.conn.commit()
    db.close()
    assert main.main(["--config", cfg, "stats"]) == 0
    db = Database(tmp_path / "c.db")
    assert db.youtube_results() == [] and not html.exists()
    db.close()


def test_comments_are_shown_with_video_and_not_stored(tmp_path, capsys, cli):
    main, _ = cli
    cfg = _config(tmp_path)
    main.main(["--config", cfg, "youtube", "accept-terms", "--yes"])
    capsys.readouterr()
    assert main.main(["--config", cfg, "youtube", "comments", "Qx7fK2mN9aB"]) == 0
    out = capsys.readouterr().out
    title = json.loads(fixture_text("youtube_videos_list.json"))["items"][0]["snippet"]["title"]
    first = json.loads(fixture_text("youtube_comment_threads.json"))["items"][0]["snippet"]["topLevelComment"]
    assert out.startswith(title) and first["snippet"]["authorDisplayName"] in out and f"lc={first['id']}" in out
    db = Database(tmp_path / "c.db")
    dump = "\n".join(db.conn.iterdump())
    assert first["snippet"]["authorDisplayName"] not in dump and first["snippet"]["textDisplay"][:20] not in dump
    db.close()


def test_trends_youtube_results_go_to_their_own_page(tmp_path, capsys, cli, make_pipeline):
    main, session = cli
    from tests.test_trends import post
    db = Database(tmp_path / "c.db")
    now = datetime.now(timezone.utc)
    for i, (src, title) in enumerate([("dogdrip", "담배피는 눈사람 짤"), ("ruliweb", "담배 피는 눈사람 원본")]):
        db.conn.execute(
            "INSERT INTO posts (source, source_post_id, url, url_normalized, title, collected_at, detail_status, "
            "first_seen_at, last_seen_at, updated_at, published_at, popularity_score) "
            "VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?, ?, 50)",
            (src, str(i), f"https://x.test/{i}", f"x.test/{i}", title, now.isoformat(), now.isoformat(),
             now.isoformat(), now.isoformat(), (now - timedelta(days=1)).isoformat()))
    db.conn.commit()
    db.close()
    cfg = _config(tmp_path)
    main.main(["--config", cfg, "youtube", "accept-terms", "--yes"])
    html = tmp_path / "trends.html"
    assert main.main(["--config", cfg, "trends", "--youtube", "9", "--html", str(html)]) == 0
    assert session.count(SEARCH) == 1                            # one trend phrase -> one search
    trends_page = html.read_text(encoding="utf-8")
    videos_page = (tmp_path / "trends-videos.html").read_text(encoding="utf-8")
    assert "i.ytimg.com" not in trends_page and 'href="trends-videos.html"' in trends_page
    assert "i.ytimg.com" in videos_page and "x.test" not in videos_page
    assert post  # imported helper exists



# ---- regressions found in review
def test_page_from_cached_rows_expires_with_the_data(tmp_path, cli):
    main, _ = cli
    cfg = _config(tmp_path)
    main.main(["--config", cfg, "youtube", "accept-terms", "--yes"])
    main.main(["--config", cfg, "youtube", "search", "밈"])
    db = Database(tmp_path / "c.db")
    db.conn.execute("UPDATE youtube_results SET fetched_at = ?",
                    ((datetime.now(timezone.utc) - timedelta(hours=23)).isoformat(timespec="seconds"),))
    db.conn.commit()
    db.close()
    page = tmp_path / "list.html"
    main.main(["--config", cfg, "youtube", "list", "--html", str(page)])     # written now from 23 h old data
    db = Database(tmp_path / "c.db")
    [(written,)] = db.conn.execute("SELECT written_at FROM youtube_files").fetchall()
    assert written < (datetime.now(timezone.utc) - timedelta(hours=22)).isoformat()
    db.close()


def test_negative_trend_lookup_count_makes_no_searches(tmp_path, cli):
    main, session = cli
    cfg = _config(tmp_path)
    main.main(["--config", cfg, "youtube", "accept-terms", "--yes"])
    db = Database(tmp_path / "c.db")
    now = datetime.now(timezone.utc).isoformat()
    for i, (src, title) in enumerate([("dogdrip", "담배피는 눈사람 짤"), ("ruliweb", "담배 피는 눈사람 원본")]):
        db.conn.execute(
            "INSERT INTO posts (source, source_post_id, url, url_normalized, title, collected_at, detail_status, "
            "first_seen_at, last_seen_at, updated_at, published_at) VALUES (?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?, ?)",
            (src, str(i), f"https://x.test/{i}", f"x.test/{i}", title, now, now, now, now, now))
    db.conn.commit()
    db.close()
    assert main.main(["--config", cfg, "trends", "--youtube", "-1"]) == 0
    assert session.count(SEARCH) == 0


def test_cache_key_covers_result_count(tmp_path, cli):
    main, session = cli
    cfg = _config(tmp_path)
    main.main(["--config", cfg, "youtube", "accept-terms", "--yes"])
    main.main(["--config", cfg, "youtube", "search", "밈", "--limit", "1"])
    main.main(["--config", cfg, "youtube", "search", "밈", "--limit", "50"])
    assert session.count(SEARCH) == 2
    assert [p["maxResults"] for p in session.params_log if "q" in p] == [1, 50]


def test_empty_answers_are_cached_too(tmp_path, cli):
    main, session = cli
    session.routes[SEARCH] = FakeResponse("u", 200, '{"items": []}', JSON)
    cfg = _config(tmp_path)
    main.main(["--config", cfg, "youtube", "accept-terms", "--yes"])
    for _ in range(3):
        main.main(["--config", cfg, "youtube", "search", "아무도 안 올린 밈"])
    assert session.count(SEARCH) == 1


def test_odd_error_bodies_and_duplicate_ids_do_not_crash():
    api, _ = make_api({SEARCH: FakeResponse("u", 403, '{"error": "Forbidden"}', JSON)})
    with pytest.raises(YouTubeError, match="Forbidden"):
        api.search_video_ids("x", now=NOW)
    api, _ = make_api({SEARCH: FakeResponse("u", 403, '{"error": {"errors": ["quotaExceeded"]}}', JSON)})
    with pytest.raises(YouTubeError):
        api.search_video_ids("x", now=NOW)
    data = json.loads(fixture_text("youtube_search_list.json"))
    data["items"].append(data["items"][0])
    api, _ = make_api({SEARCH: FakeResponse("u", 200, json.dumps(data), JSON)})
    ids = api.search_video_ids("x", now=NOW)
    assert len(ids) == len(set(ids)) == 3


def test_purge_reports_what_it_deleted(tmp_path, cli, capsys):
    main, _ = cli
    cfg = _config(tmp_path)
    main.main(["--config", cfg, "youtube", "accept-terms", "--yes"])
    main.main(["--config", cfg, "youtube", "search", "밈"])
    db = Database(tmp_path / "c.db")
    db.conn.execute("UPDATE youtube_results SET fetched_at = '2000-01-01T00:00:00+00:00'")
    db.conn.commit()
    db.close()
    capsys.readouterr()
    assert main.main(["--config", cfg, "youtube", "purge"]) == 0
    assert "deleted 3 stored YouTube rows" in capsys.readouterr().out
