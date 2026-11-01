"""Bluesky adapter: feed parsing, opt-out / adult filtering, refresh with deletions.
Fixtures are real getFeed / getPosts answers (2026-11-01) with text, DIDs, names and URLs replaced."""
from __future__ import annotations

import json
from datetime import timedelta

from collectors.bluesky import parse_visibility
from collectors.registry import build_collector
from tests.conftest import NOW, FakeResponse, fixture_text

FEED_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.getFeed*"
GET_POSTS = "https://public.api.bsky.app/xrpc/app.bsky.feed.getPosts*"
GET_RECORD = "https://public.api.bsky.app/xrpc/com.atproto.repo.getRecord*"
SOURCES = {"bluesky": {"feeds": ["jaem_30_reposts"]}}
NOT_FOUND = (400, '{"error":"RecordNotFound","message":"Could not locate record"}')


def visibility(url):
    """Author fixture0002 asked to be hidden from algorithmic recommendations; nobody else did."""
    if "fixture0002" in url:
        return FakeResponse(url, 200, '{"uri":"x","value":{"$type":"app.bsky.actor.contentVisibilityDeclaration",'
                                      '"hideFromAlgorithmicRecommendations":true}}', {"Content-Type": "application/json"})
    return NOT_FOUND


def parsed():
    c = build_collector("bluesky", {"feeds": ["jaem_30_reposts"]})
    return c, c.parse_listing(fixture_text("bluesky_feed.json"), c.listings()[0], NOW)


def test_feed_listing_url_and_single_page():
    c = build_collector("bluesky", {"listings": {"jaem_30_reposts": {"max_pages": 5}}})
    lst = {x.name: x for x in c.listings()}
    assert set(lst) == {"jaem_30_reposts", "ko_most_reposted"}
    assert lst["jaem_30_reposts"].max_pages == 1                 # cursor-paged feed: one page per run
    assert lst["jaem_30_reposts"].url.startswith(
        "https://public.api.bsky.app/xrpc/app.bsky.feed.getFeed?feed=at%3A%2F%2Fdid%3Aplc%3Aplqo3qfexrv2vwlbnxobff7c")


def test_entry_fields_and_media_variants():
    c, result = parsed()
    assert result.errors == [] and len(result.entries) == 10
    by_key = {e.source_post_id.split("/")[0][-4:]: e for e in result.entries}
    img = by_key["0001"]
    assert img.source_post_id == "did:plc:fixture0001/3fixture0001"
    assert img.url == "https://bsky.app/profile/did:plc:fixture0001/post/3fixture0001"
    assert img.title == "동네마트 갔다가 본 담배피는 눈사람"           # first line of the text
    assert img.body.startswith("동네마트") and "사장님 센스" in img.body
    assert (img.metrics.likes, img.metrics.comments_count, img.metrics.views) == (47, 1, None)
    assert img.extra["reposts"] is not None and img.ranking == 1
    assert img.media_urls == ["https://cdn.bsky.app/img/feed_fullsize/plain/did:plc:fixture0001/img0@jpeg"]
    assert img.category == "한국어/한글 30 리포스트 이상 잼얘 모음"
    assert img.published_at is not None and img.published_at.tzinfo is not None
    assert len(by_key["0002"].media_urls) >= 2                   # gallery
    assert by_key["0003"].media_urls[0].endswith("thumbnail.jpg")  # video thumbnail
    assert by_key["0004"].extra["link"]["url"] == "https://news.example/article/1"
    assert by_key["0005"].extra["quoted_text"] == "엄마들 카톡 특징 모음"
    assert by_key["0006"].media_urls                             # quote with media
    assert by_key["0010"].media_urls == []
    raw = fixture_text("bluesky_feed.json")
    stored = json.dumps([e.__dict__ for e in result.entries], default=str, ensure_ascii=False)
    assert "user1.example" in raw and "user1.example" not in stored   # handles / names never kept
    assert "사용자1" not in stored


def test_opted_out_adult_and_foreign_posts_are_skipped():
    c, result = parsed()
    accepted = {e.source_post_id[-4:] for e in result.entries if c.accept(e)}
    assert accepted == {"0001", "0002", "0003", "0004", "0005", "0006", "0010"}
    reasons = {e.source_post_id[-4:]: e.extra["skip_reason"] for e in result.entries}
    assert "no-unauthenticated" in reasons["0007"]
    assert reasons["0008"].startswith("adult label")
    assert reasons["0009"] is None                               # English: dropped by the language filter only


def test_collect_and_refresh_deletes_gone_and_opted_out(make_pipeline, clock):
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), GET_POSTS: fixture_text("bluesky_get_posts.json"),
              GET_RECORD: visibility}
    pipeline, session, db = make_pipeline(routes, SOURCES, clock=clock)
    [summary] = pipeline.collect(["bluesky"])
    assert summary.status == "ok"
    # 10 rows: opted-out, adult and English are dropped; author 0002 declared "hide from recommendations"
    assert summary.counters["posts_new"] == 6 and summary.counters["posts_skipped"] == 4
    assert "did:plc:fixture0002/3fixture0002" not in {r[0] for r in db.conn.execute("SELECT source_post_id FROM posts")}
    rows = db.conn.execute("SELECT * FROM posts ORDER BY source_post_id").fetchall()
    assert all(r["detail_status"] == "not_needed" and r["views"] is None for r in rows)
    assert all("handle" not in r["extra"] for r in rows)

    clock.now = NOW + timedelta(hours=8)
    summary = pipeline.refresh("bluesky", max_age_days=3650)
    assert summary.status == "ok"
    # getPosts fixture: post 1 still there (+100 likes), post 4's author opted out, the rest are gone
    remaining = {r["source_post_id"][-4:]: r for r in db.conn.execute("SELECT * FROM posts")}
    assert set(remaining) == {"0001"}
    assert remaining["0001"]["likes"] == 147
    assert summary.counters["posts_deleted"] == 5
    assert session.count(GET_POSTS.rstrip("*")) == 1             # 6 posts fit in one getPosts batch


def test_refresh_keeps_posts_when_the_check_fails(make_pipeline, clock):
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), GET_POSTS: (500, "upstream error"), GET_RECORD: NOT_FOUND}
    pipeline, _, db = make_pipeline(routes, SOURCES, clock=clock)
    pipeline.collect(["bluesky"])
    clock.now = NOW + timedelta(hours=8)
    summary = pipeline.refresh("bluesky", max_age_days=3650)
    assert summary.status == "partial"
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 7


def test_unknown_visibility_means_not_stored_now(make_pipeline):
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), GET_RECORD: (503, "unavailable")}
    pipeline, _, db = make_pipeline(routes, SOURCES)
    [summary] = pipeline.collect(["bluesky"])
    assert summary.counters["posts_new"] == 0 and summary.counters["posts_skipped"] == 10
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_visibility_check_runs_once_per_author(make_pipeline):
    pipeline, session, _ = make_pipeline({FEED_URL: fixture_text("bluesky_feed.json"), GET_RECORD: visibility,
                                          GET_POSTS: fixture_text("bluesky_get_posts.json")}, SOURCES)
    pipeline.collect(["bluesky"])
    assert session.count(GET_RECORD.rstrip("*")) == 7             # 7 distinct authors reached the check
    pipeline.collect(["bluesky"])                                 # known posts: no new checks
    pipeline.refresh("bluesky", max_age_days=3650, min_interval_hours=0)
    assert session.count(GET_RECORD.rstrip("*")) == 7             # answers cached (7 days)


def test_parse_visibility():
    assert parse_visibility(200, '{"value":{"hideFromAlgorithmicRecommendations":true}}') is True
    assert parse_visibility(200, '{"value":{"hideFromAlgorithmicRecommendations":false}}') is False
    assert parse_visibility(400, '{"error":"RecordNotFound"}') is False
    assert parse_visibility(400, '{"error":"InvalidRequest"}') is None
    assert parse_visibility(None, "") is None and parse_visibility(200, "not json") is None


def test_refresh_url_batches():
    c = build_collector("bluesky")
    ids = [f"did:plc:x{i}/3abc{i}" for i in range(30)]
    batches = c.refresh_urls(ids)
    assert [len(b) for _, b in batches] == [25, 5]
    assert batches[0][0].count("uris=") == 25
    assert "uris=at%3A%2F%2Fdid%3Aplc%3Ax0%2Fapp.bsky.feed.post%2F3abc0" in batches[0][0]
