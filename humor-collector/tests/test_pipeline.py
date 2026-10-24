"""End-to-end collection against fixtures: dedup on re-runs, metric updates, failure isolation,
blocking, archive cursors, interruption/resume, cross-site re-uploads, export."""
from __future__ import annotations

import json
import re
from datetime import timedelta

import pytest

from tests.conftest import fixture_text

RULI_LIST = "https://bbs.ruliweb.com/best/humor_only/now?page=1"
RULI_POST = "https://bbs.ruliweb.com/community/board/300143/read/"
BOBAE_HOF = "https://www.bobaedream.co.kr/list?code=best&vdate=h&page="
BOBAE_WEEKLY = "https://www.bobaedream.co.kr/list?code=best&vdate=w&page=1"
BOBAE_POST = "https://www.bobaedream.co.kr/view?code=best&No="
DOGDRIP_LIST = "https://www.dogdrip.net/dogdrip?page=1"
LEMMY = "https://lemmy.world/api/v3/post/list*"

RULI_SOURCES = {"ruliweb": {"allowed_boards": ["300143"], "min_likes": 10,
                            "listings": {"hit_gallery": {"enabled": False}, "humor_best": {"max_pages": 1}}}}
RULI_ACCEPTED = ["300143/77134407", "300143/77133864", "300143/77135357", "300143/77087613"]


def ruli_post(post_id: str, title: str | None = None, likes: int = 243) -> str:
    """Post page fixture specialised per post (unique title/body), as on the real site."""
    html = fixture_text("ruliweb_post.html")
    html = html.replace("편의점 알바 첫날 사장님이 남긴 메모", title or f"테스트 게시글 {post_id}")
    html = re.sub(r'(<div class="view_content[^>]*>).*?(</div>)',
                  rf"\1<p>본문 {post_id}: 이 글은 {post_id}번 글의 고유한 내용입니다.</p>\2", html, count=1, flags=re.S)
    return html.replace('<span class="like_value">243</span>', f'<span class="like_value">{likes}</span>')


def ruli_routes(**overrides) -> dict:
    routes = {RULI_LIST: fixture_text("ruliweb_list.html"),
              RULI_POST + "*": lambda url: ruli_post(url.rsplit("/", 1)[1])}
    routes.update(overrides)
    return routes


def bobae_post(post_no: str, category: str = "신유머/이슈/움짤") -> str:
    html = fixture_text("bobaedream_post.html").replace("할머니가 손주 용돈 주는 방법", f"보배 테스트 {post_no}")
    return html.replace("신유머/이슈/움짤", category).replace("명절에 할머니가", f"{post_no}번 글: 명절에 할머니가")


def count(db, sql="SELECT COUNT(*) FROM posts", *args):
    return db.conn.execute(sql, args).fetchone()[0]


# --------------------------------------------------------------------------------- basics
def test_collect_stores_posts_with_body_and_metrics(make_pipeline):
    pipeline, session, db = make_pipeline(ruli_routes(), RULI_SOURCES)
    [summary] = pipeline.collect(["ruliweb"], limit=100)
    assert summary.status == "ok"
    assert summary.counters["posts_new"] == 4 and summary.counters["posts_skipped"] == 5
    rows = {r["source_post_id"]: r for r in db.conn.execute("SELECT * FROM posts")}
    assert sorted(rows) == sorted(RULI_ACCEPTED)
    first = rows["300143/77134407"]
    assert first["url"] == RULI_POST + "77134407"
    assert first["title"] == "테스트 게시글 77134407"
    assert first["body"].startswith("본문 77134407")
    assert (first["likes"], first["dislikes"], first["views"], first["comments_count"]) == (243, 11, 14354, 71)
    assert first["published_at"] == "2026-10-24T00:14:22+00:00"       # 09:14:22 KST stored as UTC
    assert first["detail_status"] == "ok" and first["ranking"] == 1
    assert first["popularity_score"] is not None
    state = db.get_source_state("ruliweb")
    assert state.status == "ok" and state.last_success_at is not None


def test_rerun_creates_no_duplicates_and_updates_metrics(make_pipeline, clock):
    pipeline, session, db = make_pipeline(ruli_routes(), RULI_SOURCES, clock=clock)
    pipeline.collect(["ruliweb"])
    detail_calls = session.count(RULI_POST)
    assert count(db) == 4

    clock.now += timedelta(hours=1)
    bumped = fixture_text("ruliweb_list.html").replace(
        '<td class="recomd">\n\t\t\t\t\t\t\t\t\t235', '<td class="recomd">\n\t\t\t\t\t\t\t\t\t400')
    assert bumped != fixture_text("ruliweb_list.html")
    session.routes[RULI_LIST] = bumped
    [summary] = pipeline.collect(["ruliweb"])
    assert summary.counters["posts_new"] == 0 and summary.counters["posts_updated"] == 4
    assert count(db) == 4                                              # no new rows
    assert session.count(RULI_POST) == detail_calls                    # known posts: no post page re-read
    row = db.conn.execute("SELECT * FROM posts WHERE source_post_id = '300143/77134407'").fetchone()
    assert row["likes"] == 400
    assert row["last_seen_at"] > row["first_seen_at"]
    history = db.history(row["id"])
    assert [h["likes"] for h in history] == [243, 400]


def test_counters_never_go_down_when_list_is_stale(make_pipeline):
    pipeline, session, db = make_pipeline(ruli_routes(), RULI_SOURCES)
    pipeline.collect(["ruliweb"])
    pipeline.collect(["ruliweb"])                                      # list says 235, post page said 243
    row = db.conn.execute("SELECT likes FROM posts WHERE source_post_id = '300143/77134407'").fetchone()
    assert row["likes"] == 243


# ----------------------------------------------------------------------- failure isolation
def test_one_failing_post_page_keeps_list_data_and_is_retried_later(make_pipeline):
    broken = RULI_POST + "77133864"
    pipeline, session, db = make_pipeline(ruli_routes(**{broken: (500, "server error")}), RULI_SOURCES)
    [summary] = pipeline.collect(["ruliweb"])
    assert summary.status == "partial" and summary.counters["posts_new"] == 4
    failed = db.conn.execute("SELECT * FROM posts WHERE source_post_id = '300143/77133864'").fetchone()
    assert failed["detail_status"] == "failed" and failed["body"] is None
    assert (failed["likes"], failed["views"], failed["comments_count"]) == (89, 16143, 59)   # from the list row
    assert count(db, "SELECT COUNT(*) FROM posts WHERE detail_status = 'ok'") == 3
    errors = db.recent_errors(10)
    assert any(e["stage"] == "detail" and e["url"] == broken for e in errors)

    session.routes[broken] = ruli_post("77133864")                   # site recovered
    pipeline.collect(["ruliweb"])
    fixed = db.conn.execute("SELECT * FROM posts WHERE source_post_id = '300143/77133864'").fetchone()
    assert fixed["detail_status"] == "ok" and fixed["body"].startswith("본문 77133864")
    assert fixed["detail_attempts"] == 2
    assert count(db) == 4


def test_unparseable_post_page_only_affects_that_post(make_pipeline):
    odd = RULI_POST + "77135357"
    pipeline, _, db = make_pipeline(ruli_routes(**{odd: "<html><body>점검 중</body></html>"}), RULI_SOURCES)
    pipeline.collect(["ruliweb"])
    statuses = dict(db.conn.execute("SELECT source_post_id, detail_status FROM posts").fetchall())
    assert statuses.pop("300143/77135357") == "failed"
    assert set(statuses.values()) == {"ok"}
    assert any(e["error_type"] == "ParseError" for e in db.recent_errors(5))


def test_transient_network_error_on_list_is_retried(make_pipeline):
    import requests
    attempts = iter([requests.ConnectionError("reset"), requests.Timeout("slow")])

    def flaky(url):
        try:
            return next(attempts)
        except StopIteration:
            return fixture_text("ruliweb_list.html")

    pipeline, session, db = make_pipeline(ruli_routes(**{RULI_LIST: flaky}), RULI_SOURCES)
    [summary] = pipeline.collect(["ruliweb"])
    assert summary.status == "ok" and count(db) == 4
    assert session.count(RULI_LIST) == 3


def test_blocked_source_is_paused_and_others_continue(make_pipeline, clock):
    routes = ruli_routes(**{"https://www.bobaedream.co.kr/list*": (403, fixture_text("challenge_cloudflare.html"))})
    sources = {**RULI_SOURCES, "bobaedream": {}}
    pipeline, session, db = make_pipeline(routes, sources, clock=clock)
    summaries = {s.source: s for s in pipeline.collect(["bobaedream", "ruliweb"])}
    assert summaries["bobaedream"].status == "blocked"
    assert summaries["ruliweb"].status == "ok" and count(db) == 4
    state = db.get_source_state("bobaedream")
    assert state.status == "blocked" and "challenge" in state.blocked_reason
    assert state.blocked_until == "2026-10-24T14:00:00+00:00"           # now + 12h cooldown
    calls = session.count("https://www.bobaedream.co.kr/list")
    assert calls == 1                                                  # challenge is not retried

    clock.now += timedelta(hours=1)
    summaries = {s.source: s for s in pipeline.collect(["bobaedream"])}
    assert summaries["bobaedream"].status == "skipped"
    assert session.count("https://www.bobaedream.co.kr/list") == calls  # no request while paused

    summaries = {s.source: s for s in pipeline.collect(["bobaedream"], force=True)}
    assert summaries["bobaedream"].status == "blocked"
    assert session.count("https://www.bobaedream.co.kr/list") == calls + 1


def test_crashing_adapter_does_not_stop_other_sources(make_pipeline, monkeypatch):
    routes = ruli_routes(**{DOGDRIP_LIST: fixture_text("dogdrip_list.html")})
    pipeline, _, db = make_pipeline(routes, {**RULI_SOURCES, "dogdrip": {"listings": {"board": {"max_pages": 1}}}})
    from collectors.dogdrip import DogdripCollector

    def boom(self, *a, **k):
        raise RuntimeError("adapter bug")
    monkeypatch.setattr(DogdripCollector, "parse_listing", boom)
    summaries = {s.source: s for s in pipeline.collect(["dogdrip", "ruliweb"])}
    assert summaries["dogdrip"].status == "error" and "adapter bug" in summaries["dogdrip"].message
    assert summaries["ruliweb"].status == "ok"
    state = db.get_source_state("dogdrip")
    assert state.status == "error" and state.last_success_at is None
    assert any(e["stage"] == "internal" for e in db.recent_errors(5))


# --------------------------------------------------------------------- archive / resume
def test_archive_mode_resumes_from_saved_cursor(make_pipeline):
    page1 = fixture_text("bobaedream_list_hof.html")
    page2 = page1.replace("No=388403", "No=111111").replace("No=986353", "No=222222")
    routes = {BOBAE_HOF + "1": page1, BOBAE_HOF + "2": page2, BOBAE_HOF + "3": "<table id='boardlist'></table>",
              BOBAE_POST + "*": lambda url: bobae_post(url.rsplit("=", 1)[1])}
    sources = {"bobaedream": {"allowed_categories": ["유머게시판", "신유머/이슈/움짤"],
                              "listings": {"hall_of_fame": {"max_pages": 1}}}}
    pipeline, session, db = make_pipeline(routes, sources)
    pipeline.collect(["bobaedream"], mode="archive")
    assert db.get_source_state("bobaedream").cursors == {"hall_of_fame": 2}
    pipeline.collect(["bobaedream"], mode="archive")
    assert db.get_source_state("bobaedream").cursors == {"hall_of_fame": 3}
    ids = sorted(r[0] for r in db.conn.execute("SELECT source_post_id FROM posts"))
    assert ids == ["111111", "222222", "388403", "986353"]
    row = db.conn.execute("SELECT * FROM posts WHERE source_post_id = '388403'").fetchone()
    assert row["ranking"] == 1471 and row["ranking_listing"] == "hall_of_fame"
    assert row["published_at"] == "2026-10-24T01:36:00+00:00"          # full date from the post page


def test_interrupted_run_can_be_restarted_without_duplicates(make_pipeline):
    calls = {"n": 0}

    def crash_on_third(url):
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyboardInterrupt                                    # e.g. Ctrl-C / kill during a run
        return ruli_post(url.rsplit("/", 1)[1])

    pipeline, session, db = make_pipeline(ruli_routes(**{RULI_POST + "*": crash_on_third}), RULI_SOURCES)
    with pytest.raises(KeyboardInterrupt):
        pipeline.collect(["ruliweb"])
    assert count(db) == 2                                              # committed before the crash
    pipeline.collect(["ruliweb"])
    assert count(db) == 4
    assert count(db, "SELECT COUNT(DISTINCT source_post_id) FROM posts") == 4
    assert count(db, "SELECT COUNT(*) FROM posts WHERE detail_status = 'ok'") == 4


# ----------------------------------------------------------------------- other sources
def test_list_only_and_api_sources(make_pipeline):
    routes = {DOGDRIP_LIST: fixture_text("dogdrip_list.html"), LEMMY: fixture_text("lemmy_posts.json")}
    sources = {"dogdrip": {"min_likes": 20, "listings": {"board": {"max_pages": 1}}},
               "lemmy": {"communities": ["funny@lemmy.world"], "recent_sorts": ["TopDay"]}}
    pipeline, session, db = make_pipeline(routes, sources)
    summaries = {s.source: s for s in pipeline.collect(["dogdrip", "lemmy"])}
    assert summaries["dogdrip"].status == "ok" and summaries["lemmy"].status == "ok"
    dd = db.conn.execute("SELECT * FROM posts WHERE source = 'dogdrip'").fetchall()
    assert dd and all(r["body"] is None and r["views"] is None and r["detail_status"] == "list_only" for r in dd)
    assert all(r["published_at_approx"] == 1 for r in dd)
    lm = db.conn.execute("SELECT * FROM posts WHERE source = 'lemmy' ORDER BY source_post_id").fetchall()
    assert [r["detail_status"] for r in lm] == ["not_needed", "not_needed"]
    assert lm[1]["body"] == "Because he was outstanding in his field."
    assert session.count("https://www.dogdrip.net/dogdrip/") == 0       # never requests post pages


def test_cross_site_reupload_is_clustered_not_merged(make_pipeline):
    same_title = "엄마의 카톡 프로필 변천사"                                 # also on the dogdrip fixture
    routes = ruli_routes(**{RULI_POST + "77135357": ruli_post("77135357", title=same_title)})
    routes[DOGDRIP_LIST] = fixture_text("dogdrip_list.html")
    sources = {**RULI_SOURCES, "dogdrip": {"listings": {"board": {"max_pages": 1}}}}
    pipeline, _, db = make_pipeline(routes, sources)
    pipeline.collect(["ruliweb", "dogdrip"])
    rows = db.conn.execute("SELECT id, source, cluster_id FROM posts WHERE title = ?", (same_title,)).fetchall()
    assert {r["source"] for r in rows} == {"ruliweb", "dogdrip"}          # both copies kept
    assert len({r["cluster_id"] for r in rows}) == 1                      # ...and linked
    others = db.conn.execute("SELECT COUNT(DISTINCT cluster_id) FROM posts WHERE title != ?", (same_title,))
    assert others.fetchone()[0] == count(db) - 2                          # nothing else merged
    assert db.stats()["duplicates"] == {"clusters_with_reuploads": 1, "posts_in_them": 2}


def test_off_topic_board_is_kept_but_flagged(make_pipeline):
    routes = {BOBAE_WEEKLY: fixture_text("bobaedream_list_weekly.html"),
              BOBAE_POST + "*": lambda url: bobae_post(url.rsplit("=", 1)[1],
                                                       "국산차게시판" if url.endswith("1041253") else "유머게시판")}
    sources = {"bobaedream": {"allowed_categories": ["유머게시판", "신유머/이슈/움짤"],
                              "listings": {"best_realtime": {"enabled": False}, "best_monthly": {"enabled": False}}}}
    pipeline, _, db = make_pipeline(routes, sources)
    pipeline.collect(["bobaedream"])
    flagged = db.conn.execute("SELECT content_flags FROM posts WHERE source_post_id = '1041253'").fetchone()
    assert "off_topic" in json.loads(flagged[0])
    visible = db.query_posts(exclude_flags=["off_topic"])
    assert "1041253" not in {r["source_post_id"] for r in visible} and len(visible) == 3


# ---------------------------------------------------------------------------- CLI
def test_cli_export_top_stats(make_pipeline, tmp_path, capsys):
    pipeline, _, db = make_pipeline(
        {**ruli_routes(), DOGDRIP_LIST: fixture_text("dogdrip_list.html")},
        {**RULI_SOURCES, "dogdrip": {"listings": {"board": {"max_pages": 1}}}},
    )
    pipeline.collect(["ruliweb", "dogdrip"])
    db.close()
    config = tmp_path / "settings.toml"
    config.write_text(
        f'[general]\ndb_path = "{tmp_path / "test.db"}"\nlog_dir = "{tmp_path / "logs"}"\n'
        f'export_dir = "{tmp_path / "exports"}"\nlock_file = "{tmp_path / "x.lock"}"\n', encoding="utf-8")
    import main

    out = tmp_path / "out.jsonl"
    assert main.main(["--config", str(config), "export", "--format", "jsonl", "--out", str(out), "--all"]) == 0
    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 10
    required = {"source", "source_post_id", "url", "title", "body", "published_at", "collected_at", "category",
                "views", "likes", "dislikes", "comments_count", "ranking", "media_urls", "popularity_score",
                "first_seen_at", "last_seen_at", "content_category", "content_labels", "reuploads"}
    assert all(required <= set(item) for item in lines)
    dd = next(item for item in lines if item["source"] == "dogdrip")
    assert dd["views"] is None and dd["body"] is None                # missing stays null, never 0
    assert dd["url"].startswith("https://www.dogdrip.net/dogdrip/")
    scores = [item["popularity_score"] for item in lines]
    assert scores == sorted(scores, reverse=True)

    assert main.main(["--config", str(config), "top", "--limit", "3", "--all"]) == 0
    top = capsys.readouterr().out
    assert top.count("https://") == 3
    assert main.main(["--config", str(config), "stats"]) == 0
    stats = capsys.readouterr().out
    assert "ruliweb" in stats and "dogdrip" in stats and "last_success=" in stats


def test_archive_cursor_advances_even_when_limit_is_smaller_than_a_page(make_pipeline):
    page1 = fixture_text("bobaedream_list_hof.html")                     # 2 rows per page in the fixture
    page2 = page1.replace("No=388403", "No=111111").replace("No=986353", "No=222222")
    routes = {BOBAE_HOF + "1": page1, BOBAE_HOF + "2": page2,
              BOBAE_POST + "*": lambda url: bobae_post(url.rsplit("=", 1)[1])}
    sources = {"bobaedream": {"listings": {"hall_of_fame": {"max_pages": 1}}}}
    pipeline, session, db = make_pipeline(routes, sources)
    cursor = lambda: db.get_source_state("bobaedream").cursors.get("hall_of_fame")   # noqa: E731
    pipeline.collect(["bobaedream"], mode="archive", limit=1)               # 1 of 2 rows: page unfinished
    assert cursor() is None and count(db) == 1
    pipeline.collect(["bobaedream"], mode="archive", limit=1)               # known row is free, new row fills
    assert cursor() == 2 and count(db) == 2
    pipeline.collect(["bobaedream"], mode="archive", limit=1)
    assert count(db) == 3 and session.count(BOBAE_HOF + "2") == 1


def test_refresh_updates_numbers_of_posts_that_left_the_lists(make_pipeline, clock):
    pipeline, session, db = make_pipeline(ruli_routes(), RULI_SOURCES, clock=clock)
    pipeline.collect(["ruliweb"])
    clock.now += timedelta(hours=8)                                     # posts not seen for 8 hours
    session.routes[RULI_POST + "*"] = lambda url: ruli_post(url.rsplit("/", 1)[1], likes=999)
    summary = pipeline.refresh("ruliweb", max_age_days=3)
    assert summary.status == "ok"
    # only posts published within 3 days of the (moved) clock are refreshed
    recent = db.conn.execute("SELECT likes FROM posts WHERE published_at >= '2026-10-21T10:00:00+00:00'").fetchall()
    assert recent and all(r["likes"] == 999 for r in recent)
    assert summary.counters["posts_updated"] == len(recent)


def test_refresh_challenge_pauses_source(make_pipeline, clock):
    pipeline, session, db = make_pipeline(ruli_routes(), RULI_SOURCES, clock=clock)
    pipeline.collect(["ruliweb"])
    clock.now += timedelta(hours=8)
    session.routes[RULI_POST + "*"] = (403, fixture_text("challenge_cloudflare.html"))
    assert pipeline.refresh("ruliweb").status == "blocked"
    assert db.get_source_state("ruliweb").blocked_until is not None
    assert pipeline.collect(["ruliweb"])[0].status == "skipped"


def test_reclassify_applies_new_rules_to_stored_posts(make_pipeline):
    pipeline, _, db = make_pipeline(ruli_routes(), RULI_SOURCES)
    pipeline.collect(["ruliweb"])
    assert pipeline.reclassify() == 0                                  # same rules -> nothing changes
    from processing.classifier import KeywordClassifier
    pipeline.classifier = KeywordClassifier(rules={"categories": {"가족": {"keywords": ["테스트 게시글"]}}})
    assert pipeline.reclassify() == 4
    assert {r[0] for r in db.conn.execute("SELECT content_category FROM posts")} == {"가족"}
