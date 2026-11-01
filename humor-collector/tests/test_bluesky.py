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
PLC = "https://plc.directory/*"
GET_RECORD = "https://pds.example.com/xrpc/com.atproto.repo.getRecord*"


def did_doc(url):
    did = url.rsplit("/", 1)[1]
    return FakeResponse(url, 200, json.dumps({
        "id": did, "service": [{"id": "#atproto_pds", "type": "AtprotoPersonalDataServer",
                                "serviceEndpoint": "https://pds.example.com"}]}), {"Content-Type": "application/json"})
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
    assert by_key["0005"].extra["quotes_a_post"] is True
    assert "엄마들 카톡" not in json.dumps(by_key["0005"].extra, ensure_ascii=False)   # quoted author's text not kept
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
              PLC: did_doc, GET_RECORD: visibility}
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
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), GET_POSTS: (500, "upstream error"), PLC: did_doc,
              GET_RECORD: NOT_FOUND}
    pipeline, _, db = make_pipeline(routes, SOURCES, clock=clock)
    pipeline.collect(["bluesky"])
    clock.now = NOW + timedelta(hours=8)
    summary = pipeline.refresh("bluesky", max_age_days=3650)
    assert summary.status == "partial"
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 7


def test_unknown_visibility_means_not_stored_now(make_pipeline):
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), PLC: did_doc, GET_RECORD: (503, "unavailable")}
    pipeline, _, db = make_pipeline(routes, SOURCES)
    [summary] = pipeline.collect(["bluesky"])
    assert summary.counters["posts_new"] == 0 and summary.counters["posts_skipped"] == 10
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0


def test_visibility_check_runs_once_per_author(make_pipeline, clock):
    pipeline, session, _ = make_pipeline({FEED_URL: fixture_text("bluesky_feed.json"), GET_RECORD: visibility,
                                          PLC: did_doc, GET_POSTS: fixture_text("bluesky_get_posts.json")}, SOURCES,
                                         clock=clock)
    pipeline.collect(["bluesky"])
    assert session.count(GET_RECORD.rstrip("*")) == 7             # 7 distinct authors reached the check
    assert session.count("https://plc.directory/did:") == 7       # one DID document per author (+ robots.txt)
    pipeline.collect(["bluesky"])                                 # known posts: no new checks
    pipeline.clock.now = NOW + timedelta(hours=8)
    summary = pipeline.refresh("bluesky", max_age_days=3650, min_interval_hours=6)
    assert session.count(GET_POSTS.rstrip("*")) == 1               # the refresh really ran
    assert summary.counters["posts_updated"] + summary.counters.get("posts_deleted", 0) == 6
    assert session.count(GET_RECORD.rstrip("*")) == 7             # but reused the cached answers


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


def test_did_documents_cannot_point_to_internal_hosts():
    from collectors.bluesky import did_document_url, pds_endpoint
    doc = lambda ep: json.dumps({"service": [{"id": "#atproto_pds", "type": "AtprotoPersonalDataServer",  # noqa: E731
                                              "serviceEndpoint": ep}]})
    assert pds_endpoint(doc("https://morel.us-east.host.bsky.network")) == "https://morel.us-east.host.bsky.network"
    for bad in ("https://127.0.0.1", "https://[::1]:443", "https://169.254.169.254/x", "http://pds.example.com",
                "https://localhost", "https://pds.internal", "https://user@evil.example.com"):
        assert pds_endpoint(doc(bad)) is None, bad
    assert did_document_url("did:plc:abc123") == "https://plc.directory/did:plc:abc123"
    assert did_document_url("did:plc:../../x") is None
    assert did_document_url("did:web:example.com") == "https://example.com/.well-known/did.json"
    assert did_document_url("did:web:10.0.0.1") is None and did_document_url("did:web:host:8080") is None


def test_unsafe_pds_means_not_stored(make_pipeline):
    evil = lambda url: FakeResponse(url, 200, json.dumps({"service": [{  # noqa: E731
        "id": "#atproto_pds", "type": "AtprotoPersonalDataServer", "serviceEndpoint": "https://127.0.0.1"}]}),
        {"Content-Type": "application/json"})
    pipeline, session, db = make_pipeline({FEED_URL: fixture_text("bluesky_feed.json"), PLC: evil}, SOURCES)
    [summary] = pipeline.collect(["bluesky"])
    assert summary.counters["posts_new"] == 0
    assert not any("127.0.0.1" in c for c in session.calls)


def test_forget_author_and_post_then_never_recollected(make_pipeline, tmp_path, capsys):
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), PLC: did_doc, GET_RECORD: NOT_FOUND}
    pipeline, _, db = make_pipeline(routes, SOURCES)
    pipeline.collect(["bluesky"])
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 7
    now = NOW
    db.block("bluesky", "author", "did:plc:fixture0001", now)
    assert db.forget("bluesky", author="did:plc:fixture0001") == 1
    db.block("bluesky", "post", "did:plc:fixture0002/3fixture0002", now)
    assert db.forget("bluesky", post_id="did:plc:fixture0002/3fixture0002") == 1
    [summary] = pipeline.collect(["bluesky"])
    assert summary.counters["posts_new"] == 0                     # blocked ones are not collected again
    ids = {r[0] for r in db.conn.execute("SELECT source_post_id FROM posts")}
    assert not any(i.startswith(("did:plc:fixture0001/", "did:plc:fixture0002/")) for i in ids)
    assert len(ids) == 5


def test_forget_cli_accepts_bsky_url_and_at_uri(make_pipeline, tmp_path, capsys):
    import main
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), PLC: did_doc, GET_RECORD: NOT_FOUND}
    pipeline, _, db = make_pipeline(routes, SOURCES)
    pipeline.collect(["bluesky"])
    db.close()
    cfg = tmp_path / "s.toml"
    cfg.write_text(f'[general]\ndb_path = "{tmp_path / "test.db"}"\nlog_dir = "{tmp_path / "logs"}"\n'
                   f'lock_file = "{tmp_path / "l.lock"}"\n', encoding="utf-8")
    assert main.main(["--config", str(cfg), "forget", "--source", "bluesky",
                      "--post", "https://bsky.app/profile/did:plc:fixture0004/post/3fixture0004"]) == 0
    assert main.main(["--config", str(cfg), "forget", "--source", "bluesky",
                      "--post", "at://did:plc:fixture0005/app.bsky.feed.post/3fixture0005"]) == 0
    out = capsys.readouterr().out
    assert out.count("deleted 1 stored posts") == 2
    from storage.db import Database
    db = Database(tmp_path / "test.db")
    assert db.is_blocked("bluesky", "did:plc:fixture0004/3fixture0004", None)
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 5
    db.close()


def test_retention_deletes_old_bluesky_posts(make_pipeline, clock):
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), PLC: did_doc, GET_RECORD: NOT_FOUND,
              GET_POSTS: fixture_text("bluesky_get_posts.json")}
    pipeline, _, db = make_pipeline(routes, {"bluesky": {"feeds": ["jaem_30_reposts"], "retention_days": 90}},
                                    clock=clock)
    pipeline.collect(["bluesky"])
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 7
    clock.now = NOW + timedelta(days=91)
    pipeline.refresh("bluesky", max_age_days=3650, min_interval_hours=0)
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 0



# ---- regressions found in review
def test_refresh_rotates_through_all_posts_with_a_limit(make_pipeline, clock):
    feed = json.loads(fixture_text("bluesky_feed.json"))["feed"]
    still_there = [feed[i]["post"] for i in (0, 2, 3, 4, 5, 9)]          # 0002 is gone at the source
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), PLC: did_doc, GET_RECORD: NOT_FOUND,
              GET_POSTS: lambda url: FakeResponse(url, 200, json.dumps({"posts": [
                  p for p in still_there if p["uri"].replace("at://", "").replace("/app.bsky.feed.post/", "/")
                  in url.replace("%3A", ":").replace("%2F", "/").replace("at://", "").replace(
                      "/app.bsky.feed.post/", "/")]}), {"Content-Type": "application/json"})}
    pipeline, session, db = make_pipeline(routes, SOURCES, clock=clock)
    pipeline.collect(["bluesky"])
    stored = {r[0] for r in db.conn.execute("SELECT source_post_id FROM posts")}
    assert len(stored) == 7
    for hour in (8, 9, 10, 11):                                    # 4 runs x 2 posts >= 7 posts
        clock.now = NOW + timedelta(hours=hour)
        pipeline.refresh("bluesky", limit=2, min_interval_hours=0.5)
    rows = {r[0]: r[1] for r in db.conn.execute("SELECT source_post_id, last_seen_at FROM posts")}
    assert "did:plc:fixture0002/3fixture0002" not in rows          # the gone one was found and deleted
    assert set(rows) == stored - {"did:plc:fixture0002/3fixture0002"}
    assert all(seen > "2026-10-24T02:00:00+00:00" for seen in rows.values())   # every other one re-checked


def test_answer_without_posts_list_deletes_nothing(make_pipeline, clock):
    for body in ("{}", '{"error":"InternalServerError"}', '{"posts": []}'):
        routes = {FEED_URL: fixture_text("bluesky_feed.json"), PLC: did_doc, GET_RECORD: NOT_FOUND,
                  GET_POSTS: (200, body)}
        pipeline, _, db = make_pipeline(routes, SOURCES, clock=clock)
        clock.now = NOW
        pipeline.collect(["bluesky"])
        count = db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
        clock.now = NOW + timedelta(hours=8)
        summary = pipeline.refresh("bluesky", min_interval_hours=0)
        assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == count, body
        assert summary.counters.get("posts_deleted", 0) == 0 and summary.counters["errors"] >= 1
        db.conn.execute("DELETE FROM posts")
        db.conn.commit()


def test_inconclusive_answers_are_not_cached(make_pipeline, clock):
    calls = {"n": 0}

    def flaky(url):
        calls["n"] += 1
        return (408, "timeout") if calls["n"] <= 7 else NOT_FOUND

    routes = {FEED_URL: fixture_text("bluesky_feed.json"), PLC: did_doc, GET_RECORD: flaky}
    pipeline, _, db = make_pipeline(routes, SOURCES, clock=clock)
    assert pipeline.collect(["bluesky"])[0].counters["posts_new"] == 0      # all inconclusive: none stored
    clock.now = NOW + timedelta(hours=1)
    assert pipeline.collect(["bluesky"])[0].counters["posts_new"] == 7      # 408 was not reused


def test_unexpected_request_errors_are_inconclusive_not_crashes(make_pipeline):
    import requests
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), PLC: requests.exceptions.ChunkedEncodingError("cut")}
    pipeline, _, db = make_pipeline(routes, SOURCES)
    [summary] = pipeline.collect(["bluesky"])
    assert summary.status in ("ok", "partial") and summary.counters["posts_skipped"] == 10


def test_refresh_keeps_posts_when_visibility_cannot_be_checked(make_pipeline, clock):
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), PLC: did_doc, GET_RECORD: NOT_FOUND,
              GET_POSTS: lambda url: FakeResponse(url, 200, json.dumps({"posts": [
                  i["post"] for i in json.loads(fixture_text("bluesky_feed.json"))["feed"]]}),
                  {"Content-Type": "application/json"})}
    pipeline, session, db = make_pipeline(routes, SOURCES, clock=clock)
    pipeline.collect(["bluesky"])
    session.routes[GET_RECORD] = (503, "unavailable")
    clock.now = NOW + timedelta(days=8)                           # cached answers expired
    summary = pipeline.refresh("bluesky", min_interval_hours=0)
    assert summary.counters.get("posts_deleted", 0) == 0
    assert db.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0] == 7


def test_deletions_are_recorded_in_runs(make_pipeline, clock):
    routes = {FEED_URL: fixture_text("bluesky_feed.json"), GET_POSTS: fixture_text("bluesky_get_posts.json"),
              PLC: did_doc, GET_RECORD: NOT_FOUND}
    pipeline, _, db = make_pipeline(routes, SOURCES, clock=clock)
    pipeline.collect(["bluesky"])
    clock.now = NOW + timedelta(hours=8)
    pipeline.refresh("bluesky")
    run = db.recent_runs(1)[0]
    assert run["mode"] == "refresh" and run["posts_deleted"] == 6
