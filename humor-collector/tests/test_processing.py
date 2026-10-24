"""Normalization, duplicate rules, per-source scoring, classification, time parsing."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from collectors.timeutil import KST, parse_full_datetime, parse_list_time
from processing.classifier import KeywordClassifier, LLMClassifier
from processing.dedup import Candidate, DedupConfig, compare, find_duplicate
from processing.normalize import normalize_title, normalize_url
from processing.scoring import ScoringConfig, compute_scores

NOW = datetime(2026, 10, 24, 2, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------- normalize
@pytest.mark.parametrize("a,b", [
    ("https://www.bobaedream.co.kr/view?code=best&No=1&vdate=", "http://bobaedream.co.kr/view?No=1&code=best"),
    ("https://m.site.com/board/1/?page=3#comment", "https://site.com/board/1"),
    ("https://site.com/read?id=5&utm_source=x", "https://site.com/read?id=5"),
])
def test_normalize_url_equivalents(a, b):
    assert normalize_url(a) == normalize_url(b)


def test_normalize_url_keeps_identity_params():
    assert normalize_url("https://site.com/view?No=1") != normalize_url("https://site.com/view?No=2")


def test_normalize_title_strips_tags_suffixes_and_laughs():
    assert normalize_title("[펌] 롤) 아줌마 특.jpg ㅋㅋㅋ") == "아줌마특"
    assert normalize_title("이슬람 혐오에 진심인 국가") == normalize_title("이슬람  혐오에 진심인 국가!!")


# ----------------------------------------------------------------------- dedup
def cand(id_, title, body=None, published="2026-10-24T00:00:00+00:00", source="a", cluster=None):
    return Candidate(id_, source, normalize_title(title), body, published, cluster)


CFG = DedupConfig()
LONG_BODY = ("명절에 할머니 댁에 갔는데 할머니가 용돈을 주시면서 엄마한테 맡기지 말라고 하셨다. "
             "그런데 엄마가 옆에서 듣고 있었고 결국 용돈은 저금통으로 들어갔다. 다음 명절에는 "
             "할머니가 몰래 주머니에 넣어주셨는데 세탁기에 들어가서 다 젖어버렸다는 슬픈 이야기. "
             "그래서 이번 설에는 봉투에 이름까지 써서 받았는데 동생이 자기 것과 바꿔치기해서 또 사라졌다. "
             "결국 할머니는 내년부터 계좌이체로 주시겠다고 선언하셨다")


def test_same_text_on_two_sites_is_duplicate():
    a = cand(1, "할머니 용돈 대참사", LONG_BODY, source="ruliweb")
    b = cand(2, "할머니 용돈 대참사.jpg", LONG_BODY, source="bobaedream")
    assert compare(a, b, CFG).reason == "title+body"


def test_rewritten_title_with_same_long_body_is_duplicate():
    a = cand(1, "명절 썰", LONG_BODY)
    b = cand(2, "할머니가 용돈 주신 이야기 ㅋㅋ", LONG_BODY + " 끝.")
    assert compare(a, b, CFG).reason == "body"


def test_similar_title_but_different_body_is_not_duplicate():
    a = cand(1, "편의점 알바 첫날 썰", "첫날부터 사장님이 도시락을 주셔서 감동받았다는 이야기입니다 정말로요")
    b = cand(2, "편의점 알바 첫날 썰", "손님이 컵라면 물을 너무 많이 부어서 바닥이 물바다가 된 이야기입니다 진짜")
    assert compare(a, b, CFG) is None


def test_image_posts_need_identical_long_title():
    assert compare(cand(1, "엄마의 카톡 프로필 변천사"), cand(2, "엄마의 카톡 프로필 변천사.jpg"), CFG)
    assert compare(cand(1, "아줌마 특"), cand(2, "아줌마 특"), CFG) is None          # too short/generic
    assert compare(cand(1, "엄마의 카톡 프로필 변천사"), cand(2, "아빠의 카톡 프로필 변천사"), CFG) is None


def test_far_apart_posts_are_not_merged():
    a = cand(1, "엄마의 카톡 프로필 변천사", published="2026-10-24T00:00:00+00:00")
    b = cand(2, "엄마의 카톡 프로필 변천사", published="2025-01-01T00:00:00+00:00")
    assert compare(a, b, CFG) is None


def test_find_duplicate_picks_best_match():
    post = cand(10, "할머니 용돈 대참사", LONG_BODY)
    others = [cand(1, "전혀 다른 글 제목입니다"), cand(2, "할머니 용돈 대참사", LONG_BODY, cluster=2)]
    assert find_duplicate(post, others, CFG).candidate.id == 2


# --------------------------------------------------------------------- scoring
def row(id_, source, likes=None, views=None, comments=None, dislikes=None, ranking=None, cluster=None):
    return {"id": id_, "source": source, "likes": likes, "views": views, "comments_count": comments,
            "dislikes": dislikes, "ranking": ranking, "ranking_listing": None, "cluster_id": cluster}


def test_scores_are_relative_to_each_source():
    small = [row(i, "small", likes=10 * i, views=100 * i, comments=i) for i in range(1, 21)]
    big = [row(100 + i, "big", likes=1000 * i, views=10000 * i, comments=100 * i) for i in range(1, 21)]
    scores = compute_scores(small + big)
    assert scores[20] == pytest.approx(scores[120])             # each source's top post scores the same
    assert scores[1] == pytest.approx(scores[101])
    assert scores[20] > scores[10] > scores[1]


def test_missing_metrics_are_left_out_not_zero():
    rows = [row(1, "s", likes=100, views=None), row(2, "s", likes=100, views=5000), row(3, "s", likes=1, views=10)]
    scores = compute_scores(rows, ScoringConfig(shrink_k=0))
    assert scores[1] is not None and scores[1] > scores[3]
    assert compute_scores([row(9, "s")])[9] is None             # nothing known -> no score


def test_rank_only_raises_and_cross_site_bonus():
    base = [row(i, "s", likes=i) for i in range(1, 11)]
    plain = compute_scores(base)[5]
    ranked = compute_scores([*base[:4], row(5, "s", likes=5, ranking=1), *base[5:]])[5]
    far_rank = compute_scores([*base[:4], row(5, "s", likes=5, ranking=1500), *base[5:]])[5]
    assert ranked > plain and far_rank == plain
    clustered = compute_scores([*base, row(50, "other", likes=3, cluster=5)] +
                               [dict(r, cluster_id=5) if r["id"] == 5 else r for r in base])
    assert clustered[5] > plain


def test_dislikes_penalise():
    a = compute_scores([row(1, "s", likes=100, dislikes=0), row(2, "s", likes=50)])[1]
    b = compute_scores([row(1, "s", likes=100, dislikes=100), row(2, "s", likes=50)])[1]
    assert b < a


# ------------------------------------------------------------------ classifier
@pytest.fixture(scope="module")
def keyword():
    return KeywordClassifier()


def test_keyword_categories(keyword):
    r = keyword.classify("엄마가 끓여준 라면 먹고 생긴 일", "엄마: 라면 먹을래?")
    assert {"가족", "음식·다이어트"} <= set(r.labels)
    assert keyword.classify("회사 회식 메뉴 정하기", None).primary in ("직장·학교", "음식·다이어트")
    assert keyword.classify("ㅁㄴㅇㄹ", None).primary == "기타"


def test_keyword_dialogue_and_flags(keyword):
    body = "엄마: 밥 먹었니\n나: 아직\n엄마: 왜\n나: 다이어트 중"
    r = keyword.classify("엄마랑 나눈 대화", body)
    assert "대화형 유머" in r.labels
    assert "politics" in keyword.classify("대통령 연설 요약", None).flags
    assert "nsfw" in keyword.classify("일반 제목", None, source_category="후방주의방").flags


def test_llm_classifier_uses_model_answer_and_falls_back(keyword):
    good = LLMClassifier(lambda prompt: json.dumps({"primary": "가족", "labels": ["가족"], "flags": []}),
                         fallback=keyword)
    assert good.classify("아무 제목", None).primary == "가족"
    assert good.classify("아무 제목", None).method == "llm-v1"
    bad = LLMClassifier(lambda prompt: "죄송하지만 분류할 수 없습니다", fallback=keyword)
    result = bad.classify("엄마가 끓여준 라면", None)
    assert result.method.endswith("(llm fallback)") and result.primary in ("가족", "음식·다이어트")
    with pytest.raises(Exception):
        LLMClassifier(lambda p: "{}").classify("x", None)


# ----------------------------------------------------------------- time parsing
@pytest.mark.parametrize("text,expected,approx", [
    ("09:14", datetime(2026, 10, 24, 9, 14, tzinfo=KST), False),
    ("23:50", datetime(2026, 10, 23, 23, 50, tzinfo=KST), False),       # later than now -> yesterday
    ("15 시간 전", datetime(2026, 10, 23, 20, 0, tzinfo=KST), True),
    ("방금 전", datetime(2026, 10, 24, 11, 0, tzinfo=KST), True),
    ("26.10.20", datetime(2026, 10, 20, tzinfo=KST), True),
    ("10/20", datetime(2026, 10, 20, tzinfo=KST), True),
    ("12/30", datetime(2025, 12, 30, tzinfo=KST), True),                 # December seen in October -> last year
])
def test_parse_list_time(text, expected, approx):
    assert parse_list_time(text, NOW) == (expected, approx)


def test_parse_full_datetime_formats():
    assert parse_full_datetime("2026.10.24 (09:14:22)") == datetime(2026, 10, 24, 9, 14, 22, tzinfo=KST)
    assert parse_full_datetime("2026.10.24 (토) 10:36") == datetime(2026, 10, 24, 10, 36, tzinfo=KST)
    assert parse_full_datetime("작성일 없음") is None
