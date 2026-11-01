"""Meme / 짤 trend detection on community titles."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
