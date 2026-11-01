"""Meme / 짤 trend detection on community titles."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import unicodedata

from processing.trends import TrendConfig, display_phrase, find_trends

NOW = datetime(2026, 11, 1, 3, 0, tzinfo=timezone.utc)
_ids = iter(range(1, 10_000))


def post(title, source="ruliweb", days_ago=1, cluster=None, score=50.0, media=None):
    pid = next(_ids)
    when = (NOW - timedelta(days=days_ago)).isoformat()
    return {"id": pid, "source": source, "title": title, "url": f"https://example.test/{pid}",
            "published_at": when, "first_seen_at": when, "cluster_id": cluster or pid,
            "popularity_score": score, "likes": 10, "media_urls": f'["https://img.test/{pid}.jpg"]'
            if media is None else media}


def phrases(trends):
    return [t.phrase for t in trends]


def test_spacing_and_particle_variants_form_one_trend():
    rows = [
        post("담배피는 눈사람.jpg", "dogdrip", score=80),
        post("담배 피는 눈사람 근황", "ruliweb"),
        post("[펌] 담배피는 눈사람이 화제", "bobaedream"),
        post("고양이 택배상자 점령기"),
    ]
    trends = find_trends(rows, NOW)
    assert len(trends) == 1
    t = trends[0]
    assert t.key == "담배피는눈사람" and t.phrase == "담배피는 눈사람"   # spelled as in the best post
    assert t.docs == 3 and t.sources == ["bobaedream", "dogdrip", "ruliweb"]
    assert t.examples[0].image == f"https://img.test/{rows[0]['id']}.jpg"


def test_generic_board_words_are_not_trends():
    rows = [post(f"{x} 근황 레전드 모음") for x in ("ㄱ", "ㄴ", "ㄷ", "ㄹ")] + \
           [post(f"대한민국 {x}") for x in ("하나", "둘셋", "넷다섯")]
    assert find_trends(rows, NOW) == []


def test_always_common_phrase_is_not_a_burst():
    old = [post(f"아이폰 듀오 후기 {i}", days_ago=8 + i) for i in range(20)]
    new = [post(f"아이폰 듀오 리뷰 {i}", days_ago=1) for i in range(3)]
    burst = [post(f"음료수 주르륵 {x}", days_ago=1) for x in ("짤", "원본", "패러디")]
    assert phrases(find_trends(old + new + burst, NOW)) == ["음료수 주르륵"]


def test_reuploads_count_once_but_two_sites_are_enough():
    same = [post("선정이 딸이에요 음료수 주르륵", s, cluster=999) for s in ("dogdrip", "ruliweb", "bobaedream")]
    assert find_trends(same, NOW) == []                       # one post, re-uploaded
    two_sites = [post("선정이 딸이에요 음료수 주르륵", "dogdrip"), post("선정이 딸이에요 짤 원본", "ruliweb")]
    assert phrases(find_trends(two_sites, NOW)) == ["선정이 딸이에요"]


def test_two_posts_on_one_site_are_not_enough():
    rows = [post("선정이 딸이에요 음료수"), post("선정이 딸이에요 원본")]
    assert find_trends(rows, NOW) == []


def test_phrase_must_start_at_a_word():
    rows = [post("어느 위스키의 굉장한 사은품", "dogdrip"), post("어느 위스키의 대단한 사은품.jpg", "ruliweb")]
    assert phrases(find_trends(rows, NOW)) == ["어느 위스키의"]   # not "한 사은품"


def test_old_posts_outside_window_are_ignored():
    rows = [post("담배피는 눈사람", days_ago=d) for d in (10, 11, 12)]
    assert find_trends(rows, NOW, TrendConfig(window_days=7)) == []
    assert phrases(find_trends(rows, NOW, TrendConfig(window_days=14))) == ["담배피는 눈사람"]


def test_display_phrase_keeps_original_spacing():
    assert display_phrase("[펌] 담배 피는 눈사람.jpg ㅋㅋ", "담배피는눈사람") == "담배 피는 눈사람"
    assert display_phrase("없는 제목", "담배피는눈사람") == "담배피는눈사람"


def test_example_image_falls_back_to_list_thumbnail():
    rows = [post("음료수 주르륵 짤", "dogdrip", media="[]"), post("음료수 주르륵 원본", "ruliweb", media="[]")]
    rows[0]["extra"] = '{"thumbnail_url": "https://thumb.test/1.jpg"}'
    rows[1]["extra"] = "{}"
    images = {e.source: e.image for e in find_trends(rows, NOW)[0].examples}
    assert images == {"dogdrip": "https://thumb.test/1.jpg", "ruliweb": None}


def test_html_gallery_escapes_text_and_drops_unsafe_urls(tmp_path):
    from processing.report import write_trends_html
    rows = [post('<script>alert(1)</script> 음료수 주르륵', "dogdrip", media='["javascript:alert(1)"]'),
            post("음료수 주르륵 원본", "ruliweb")]
    page = write_trends_html(tmp_path / "t.html", find_trends(rows, NOW), window_days=7).read_text(encoding="utf-8")
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert "javascript:" not in page
    assert 'src="https://img.test/' in page



# ---- regressions found in review
def test_long_meme_name_is_kept_whole():
    """The owner's own example is 15 characters once normalized; it must not be cut off."""
    rows = [post("예나 선정이 딸이에요 음료수 주르륵", s) for s in ("dogdrip", "ruliweb", "bobaedream")]
    assert phrases(find_trends(rows, NOW)) == ["예나 선정이 딸이에요 음료수 주르륵"]


def test_phrase_with_particle_does_not_replace_the_wider_phrase():
    rows = [post(f"담배피는 눈사람이 {x}") for x in ("화제", "등장", "또", "인기")]
    rows.append(post("담배피는 눈사람 원본", score=99))
    [t] = find_trends(rows, NOW)
    assert t.phrase == "담배피는 눈사람" and t.docs == 5
    assert t.examples[0].score == 99


def test_new_word_on_an_old_core_is_still_a_burst():
    old = [post(f"사이버트럭 {w} {i}", days_ago=8 + i) for i, w in enumerate(["후기", "주행", "가격"] * 8)]
    new = [post(f"사이버트럭 폭발 {x}", days_ago=1) for x in ("영상", "원인", "현장", "뉴스", "속보", "사진")]
    assert "사이버트럭 폭발" in phrases(find_trends(old + new, NOW))


def test_short_history_does_not_inflate_bursts():
    """Only 2 days of earlier history: no burst test yet, so steady phrases are not called x12."""
    steady = [post(f"아이폰 듀오 {i}", days_ago=d) for i, d in enumerate([1, 1, 2, 2, 3, 3, 8, 8.5, 9])]
    trends = find_trends(steady, NOW)
    assert trends and trends[0].ratio is None


def test_decomposed_hangul_titles_are_matched():
    rows = [post(unicodedata.normalize("NFD", t), s) for t, s in
            [("담배피는 눈사람.jpg", "dogdrip"), ("담배 피는 눈사람 근황", "ruliweb"), ("담배피는 눈사람이 화제", "bobaedream")]]
    assert phrases(find_trends(rows, NOW)) == ["담배피는 눈사람"]


def test_mid_word_fragment_is_never_a_phrase():
    rows = [post("굉장한 사은품 도착", "dogdrip"), post("대단한 사은품 도착", "ruliweb")]
    assert phrases(find_trends(rows, NOW)) == ["사은품 도착"]


def test_display_title_comes_from_a_copy_that_has_the_phrase():
    rows = [post("담배피는 눈사람.jpg", cluster=500, score=10), post("이거 보고 빵 터짐", cluster=500, score=95),
            post("담배피는 눈사람 원본"), post("담배 피는 눈사람 근황")]
    [t] = find_trends(rows, NOW)
    assert t.phrase in ("담배피는 눈사람", "담배 피는 눈사람") and t.docs == 3
    assert all("눈사람" in e.title for e in t.examples)


def test_user_stop_phrases_are_normalized(tmp_path):
    import main
    from types import SimpleNamespace
    cfg = main.trend_config({"trends": {"extra_stop_phrases": ["iPhone 듀오"]}}, SimpleNamespace(days=None))
    rows = [post(f"iPhone 듀오 {x}", s) for x, s in (("후기", "dogdrip"), ("리뷰", "ruliweb"), ("사용기", "bobaedream"))]
    assert find_trends(rows, NOW, cfg) == []
