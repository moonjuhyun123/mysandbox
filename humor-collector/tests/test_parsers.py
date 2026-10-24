"""Offline parser tests against sanitized fixtures captured from the real sites (2026-10-24)."""
from __future__ import annotations

from datetime import datetime

import pytest

from collectors.base import ParseError
from collectors.registry import build_collector
from collectors.timeutil import KST
from tests.conftest import NOW, fixture_text


def listing(collector, name):
    return next(lst for lst in collector.listings() if lst.name == name)


# ------------------------------------------------------------------- ruliweb
def test_ruliweb_list_extracts_title_link_and_metrics():
    c = build_collector("ruliweb", {"allowed_boards": ["300143"]})
    result = c.parse_listing(fixture_text("ruliweb_list.html"), listing(c, "humor_best"), NOW)
    assert result.errors == []
    assert len(result.entries) == 9
    first = result.entries[0]
    assert first.source_post_id == "300143/77134407"
    assert first.url == "https://bbs.ruliweb.com/community/board/300143/read/77134407"
    assert first.title == "편의점 알바 첫날 사장님이 남긴 메모"
    assert (first.metrics.likes, first.metrics.views, first.metrics.comments_count) == (235, 13584, 71)
    assert first.metrics.dislikes is None                      # not on the list -> NULL, not 0
    assert first.ranking == 1                                   # top row of the best list
    assert first.published_at == datetime(2026, 10, 24, 9, 14, tzinfo=KST)
    assert not first.published_at_approx
    plain = result.entries[3]
    assert plain.ranking is None                                # chronological rows carry no rank
    older = result.entries[-1]
    assert older.published_at_approx and older.published_at.date().isoformat() == "2026-10-20"   # "26.10.20"


def test_ruliweb_filters_other_boards_and_low_likes():
    c = build_collector("ruliweb", {"allowed_boards": ["300143"], "min_likes": 10})
    entries = c.parse_listing(fixture_text("ruliweb_list.html"), listing(c, "humor_best"), NOW).entries
    market = [e for e in entries if e.extra["board_id"] == "1020"]
    assert len(market) == 1 and not c.accept(market[0])
    low = [e for e in entries if e.extra["board_id"] == "300143" and e.metrics.likes < 10]
    assert low and not any(c.accept(e) for e in low)
    assert [e.metrics.likes for e in entries if c.accept(e)] == [235, 89, 16, 13]


def test_ruliweb_detail_extracts_body_and_all_counters():
    c = build_collector("ruliweb")
    entry = c.parse_listing(fixture_text("ruliweb_list.html"), listing(c, "humor_best"), NOW).entries[0]
    d = c.parse_detail(fixture_text("ruliweb_post.html"), entry, NOW)
    assert d.title == "편의점 알바 첫날 사장님이 남긴 메모"
    assert d.body.splitlines()[0] == "알바 첫날 출근했더니 계산대 위에 메모가 있었다."
    assert "사장님: 그리고 컵라면 물은 반만" in d.body
    assert (d.metrics.likes, d.metrics.dislikes, d.metrics.views, d.metrics.comments_count) == (243, 11, 14354, 71)
    assert d.published_at == datetime(2026, 10, 24, 9, 14, 22, tzinfo=KST)
    assert d.category == "유머 게시판"
    assert d.extra["board_category"] == "유머"
    assert d.media_urls == ["https://i3.ruliweb.com/img/26/10/24/fixture-image.webp"]


def test_ruliweb_one_broken_row_does_not_lose_the_others():
    html = fixture_text("ruliweb_list.html").replace("/best/board/300143/read/77133864", "/best/board/broken", 1)
    c = build_collector("ruliweb")
    result = c.parse_listing(html, listing(c, "humor_best"), NOW)
    assert len(result.entries) == 8
    assert len(result.errors) == 1 and "unexpected post link" in result.errors[0]


def test_ruliweb_layout_change_raises():
    c = build_collector("ruliweb")
    with pytest.raises(ParseError):
        c.parse_listing("<html><body><p>점검중</p></body></html>", listing(c, "humor_best"), NOW)
    with pytest.raises(ParseError):
        c.parse_detail("<html><body></body></html>", None, NOW)


# ---------------------------------------------------------------- bobaedream
def test_bobaedream_realtime_list_and_category_filter():
    c = build_collector("bobaedream", {"allowed_categories": ["유머게시판", "신유머/이슈/움짤"]})
    result = c.parse_listing(fixture_text("bobaedream_list_realtime.html"), listing(c, "best_realtime"), NOW)
    assert result.errors == []
    assert len(result.entries) == 6
    first = result.entries[0]
    assert first.source_post_id == "1042339"
    assert first.url == "https://www.bobaedream.co.kr/view?code=best&No=1042339"
    assert first.title == "할머니가 손주 용돈 주는 방법"
    assert first.category == "유머게시판"
    assert (first.metrics.likes, first.metrics.views) == (17, 62)
    assert first.metrics.comments_count is None                 # no "(n)" shown
    assert result.entries[1].metrics.comments_count == 2
    accepted = [e for e in result.entries if c.accept(e)]
    assert len(accepted) == 4
    rejected = {e.category for e in result.entries if not c.accept(e)}
    assert rejected and not rejected & {"유머게시판", "신유머/이슈/움짤"}


def test_bobaedream_weekly_ranked_columns():
    c = build_collector("bobaedream")
    result = c.parse_listing(fixture_text("bobaedream_list_weekly.html"), listing(c, "best_weekly"), NOW)
    ranks = [e.ranking for e in result.entries]
    assert ranks == [1, 2, 3, 30]
    first = result.entries[0]
    assert (first.metrics.likes, first.metrics.views, first.metrics.comments_count) == (2098, 121805, 310)
    assert first.category is None                               # ranked lists have no board column
    assert first.published_at_approx and first.published_at.month == 10 and first.published_at.day == 20


def test_bobaedream_hall_of_fame_keeps_unknown_year_as_null():
    c = build_collector("bobaedream")
    result = c.parse_listing(fixture_text("bobaedream_list_hof.html"), listing(c, "hall_of_fame"), NOW)
    assert [e.ranking for e in result.entries] == [1471, 1472]
    assert all(e.published_at is None for e in result.entries)  # "MM/DD" of an unknown year


def test_bobaedream_detail():
    c = build_collector("bobaedream")
    d = c.parse_detail(fixture_text("bobaedream_post.html"), None, NOW)
    assert d.title == "할머니가 손주 용돈 주는 방법"
    assert (d.metrics.views, d.metrics.likes, d.metrics.comments_count) == (644, 21, 2)
    assert d.published_at == datetime(2026, 10, 24, 10, 36, tzinfo=KST)
    assert d.category == "신유머/이슈/움짤"
    assert "할머니가 먼저" in d.body
    assert d.media_urls == ["https://file1.bobaedream.co.kr/humor/2026/10/24/10/fixture_1.jpg"]


# -------------------------------------------------------------------- dogdrip
def test_dogdrip_list_only_rows():
    c = build_collector("dogdrip")
    result = c.parse_listing(fixture_text("dogdrip_list.html"), listing(c, "board"), NOW)
    assert result.errors == []
    titles = [e.title for e in result.entries]
    assert "개드립 이용 규칙과 공지" not in titles               # notice skipped
    assert len(result.entries) == 6                             # ad slot skipped
    first = result.entries[0]
    assert first.source_post_id == "731183863"
    assert first.url == "https://www.dogdrip.net/dogdrip/731183863"
    assert (first.metrics.likes, first.metrics.comments_count, first.metrics.views) == (120, 106, None)
    assert [e.ranking for e in result.entries[:3]] == [1, 2, None]
    assert first.published_at_approx                            # "15 시간 전"
    assert first.published_at == datetime(2026, 10, 23, 20, 0, tzinfo=KST)
    assert not c.needs_detail and first.body is None


# ---------------------------------------------------------------------- lemmy
def test_lemmy_api_items():
    c = build_collector("lemmy", {"communities": ["funny@lemmy.world"], "page_size": 20})
    lst = listing(c, "funny@lemmy.world:TopDay")
    assert lst.url.startswith("https://lemmy.world/api/v3/post/list?community_name=funny%40lemmy.world&sort=TopDay")
    result = c.parse_listing(fixture_text("lemmy_posts.json"), lst, NOW)
    assert [e.source_post_id for e in result.entries] == ["1001", "1002"]   # removed post skipped
    img, joke = result.entries
    assert img.body == "" and img.media_urls == ["https://lemmy.world/pictrs/image/fixture-1.webp"]
    assert joke.body == "Because he was outstanding in his field."
    assert joke.url == "https://lemmy.world/post/1002"
    assert joke.category == "jokes@lemmy.world"
    assert (joke.metrics.likes, joke.metrics.dislikes, joke.metrics.comments_count, joke.metrics.views) == (200, 2, 20, None)
    assert [e.ranking for e in result.entries] == [1, 2]
    page2 = c.parse_listing(fixture_text("lemmy_posts.json"), lst, NOW, page=2)
    assert [e.ranking for e in page2.entries] == [21, 22]


def test_lemmy_invalid_json():
    c = build_collector("lemmy")
    with pytest.raises(ParseError):
        c.parse_listing("<html>error</html>", c.listings()[0], NOW)
