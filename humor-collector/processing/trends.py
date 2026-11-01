"""Trending meme / 짤 detection on our community data.

A meme spreads as many different posts whose titles repeat its name with varying spacing and
particles ("담배피는 눈사람.jpg", "담배 피는 눈사람 근황", "눈사람이 담배피는 짤"). So we look for
character n-grams of the space-free normalized title that

- occur in at least ``min_docs`` different posts (re-upload clusters count once) in the last
  ``window_days`` days, and
- are much more frequent now than in the preceding ``baseline_days`` (burst ratio), and
- are not just generic board words ("근황", "레전드", "모음"... see ``stop_phrases``).

Overlapping n-grams are collapsed to the longest phrase that covers the same posts.
YouTube API data is deliberately NOT an input: its policies forbid aggregating it or deriving
new data from it (Developer Policies III.E.2, III.E.4.h).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Sequence

from collectors.timeutil import parse_iso
from processing.normalize import normalize_text, normalize_title

DEFAULT_STOP_PHRASES = (
    "근황", "레전드", "이유", "후기", "모음", "정리", "사람", "진짜", "요즘", "최근", "오늘", "어제", "결과",
    "한국", "일본", "중국", "미국", "유머", "웃긴", "웃김", "만화", "짤방", "영상", "사진", "모습", "상황",
    "반응", "특징", "근데", "그냥", "이거", "그거", "저거", "하는", "되는", "있는", "없는", "같은", "하면",
    "에서", "으로", "까지", "보다", "처럼", "대한민국", "우리나라", "외국인", "사람들", "리뷰", "사용기", "감상",
    "소감", "정보", "공개", "출시", "발표", "manhwa", "jpg", "gif", "mp4", "webp", "png",
)


@dataclass
class TrendConfig:
    window_days: int = 7
    baseline_days: int = 28
    min_docs: int = 3
    min_sources_if_two_docs: int = 2   # 2 posts are enough when they come from 2+ communities
    min_len: int = 4
    max_len: int = 14
    min_ratio: float = 3.0
    stop_phrases: Sequence[str] = DEFAULT_STOP_PHRASES
    max_examples: int = 4


@dataclass
class TrendExample:
    post_id: int
    source: str
    title: str
    url: str
    image: str | None
    likes: int | None
    score: float | None


@dataclass
class Trend:
    phrase: str                 # as written in a real title, e.g. "담배피는 눈사람"
    key: str                    # space-free normalized form
    docs: int                   # distinct posts (re-upload clusters) in the window
    posts: int                  # rows including re-uploads
    sources: list[str]
    baseline_docs: int
    ratio: float
    examples: list[TrendExample] = field(default_factory=list)


@dataclass
class _Doc:
    key: str                    # cluster id or post id
    text: str                   # normalized title
    rows: list[Mapping]


def _when(row: Mapping) -> datetime | None:
    return parse_iso(row["published_at"]) or parse_iso(row["first_seen_at"])


def _ngrams(text: str, lo: int, hi: int) -> set[str]:
    grams = set()
    for n in range(lo, min(hi, len(text)) + 1):
        for i in range(len(text) - n + 1):
            grams.add(text[i : i + n])
    return grams


def _strip_stops(text: str, stops: Sequence[str]) -> str:
    for stop in sorted(stops, key=len, reverse=True):
        text = text.replace(stop, "")
    return text


def _is_generic(gram: str, stops: Sequence[str]) -> bool:
    if sum(ch.isdigit() for ch in gram) * 2 >= len(gram):
        return True
    return len(_strip_stops(gram, stops)) < 2


def _docs(rows: Iterable[Mapping]) -> list[_Doc]:
    grouped: dict[str, _Doc] = {}
    for row in rows:
        text = normalize_title(row["title"])
        if not text:
            continue
        key = f"c{row['cluster_id']}" if row["cluster_id"] else f"p{row['id']}"
        doc = grouped.setdefault(key, _Doc(key, text, []))
        doc.rows.append(row)
    return list(grouped.values())


def _locate(title: str, key: str) -> tuple[int, int] | None:
    """Character span of ``key`` (space-free normalized) inside the original title."""
    chars, index = [], []
    for i, ch in enumerate(title):
        for c in normalize_text(ch):
            chars.append(c)
            index.append(i)
    pos = "".join(chars).find(key)
    if pos == -1:
        return None
    return index[pos], index[pos + len(key) - 1] + 1


def display_phrase(title: str, key: str) -> str:
    """Recover the phrase as written (with its spaces) from a title containing ``key``."""
    span = _locate(title, key)
    return title[span[0] : span[1]].strip() if span else key


def _to_word_end(title: str, key: str) -> str:
    """The phrase extended to the end of its last word, normalized ("근황레전" -> "근황레전드")."""
    span = _locate(title, key)
    if span is None:
        return key
    end = span[1]
    while end < len(title) and title[end].isalnum():
        end += 1
    return normalize_text(title[span[0] : end])


def _starts_at_word(title: str, key: str) -> bool:
    """Memes are word sequences: the phrase must begin at a word start ("한 사은품" in
    "굉장한 사은품" does not). Its end may cut a particle ("눈사람이" -> "눈사람")."""
    span = _locate(title, key)
    return span is not None and (span[0] == 0 or not title[span[0] - 1].isalnum())


def _image(row: Mapping) -> str | None:
    """First attached image, else a list thumbnail (list-only sources such as dogdrip)."""
    media = row["media_urls"]
    media = json.loads(media or "[]") if isinstance(media, str) else (media or [])
    if media:
        return media[0]
    try:
        extra = row["extra"]
    except (KeyError, IndexError):
        return None
    extra = json.loads(extra or "{}") if isinstance(extra, str) else (extra or {})
    return extra.get("thumbnail_url")


def find_trends(rows: Sequence[Mapping], now: datetime, cfg: TrendConfig | None = None,
                limit: int = 20) -> list[Trend]:
    cfg = cfg or TrendConfig()
    window_start = now - timedelta(days=cfg.window_days)
    baseline_start = window_start - timedelta(days=cfg.baseline_days)
    recent_rows, baseline_rows = [], []
    for row in rows:
        when = _when(row)
        if when is None:
            continue
        if when >= window_start:
            recent_rows.append(row)
        elif when >= baseline_start:
            baseline_rows.append(row)

    recent_docs, baseline_docs = _docs(recent_rows), _docs(baseline_rows)
    recent_df: dict[str, list[_Doc]] = {}
    for doc in recent_docs:
        for gram in _ngrams(doc.text, cfg.min_len, cfg.max_len):
            recent_df.setdefault(gram, []).append(doc)
    baseline_df: dict[str, int] = {}
    if baseline_docs:
        wanted = {g for g, docs in recent_df.items() if len(docs) >= 2}
        for doc in baseline_docs:
            for gram in _ngrams(doc.text, cfg.min_len, cfg.max_len) & wanted:
                baseline_df[gram] = baseline_df.get(gram, 0) + 1

    candidates: dict[str, tuple[list[_Doc], float]] = {}
    for gram, docs in recent_df.items():
        sources = {r["source"] for d in docs for r in d.rows}
        enough = len(docs) >= cfg.min_docs or (len(docs) >= 2 and len(sources) >= cfg.min_sources_if_two_docs)
        if not enough or _is_generic(gram, cfg.stop_phrases):
            continue
        starting = [r["title"] for d in docs for r in d.rows if _starts_at_word(r["title"], gram)]
        if not starting or _is_generic(_to_word_end(starting[0], gram), cfg.stop_phrases):
            continue
        recent_rate = len(docs) / cfg.window_days
        base_rate = (baseline_df.get(gram, 0) + 1) / cfg.baseline_days
        ratio = recent_rate / base_rate
        if baseline_docs and ratio < cfg.min_ratio:
            continue
        candidates[gram] = (docs, ratio)

    def rate_ratio(gram: str) -> float:
        return (len(recent_df[gram]) / cfg.window_days) / ((baseline_df.get(gram, 0) + 1) / cfg.baseline_days)

    def only_old_core(gram: str) -> bool:
        """True if the gram is an always-common phrase plus a few generic characters
        ("아이폰듀오" + "리뷰"): the old core explains it, nothing new is trending."""
        if not baseline_docs:
            return False
        for n in range(cfg.min_len, len(gram)):
            for i in range(len(gram) - n + 1):
                sub = gram[i : i + n]
                if sub in recent_df and rate_ratio(sub) < cfg.min_ratio:
                    if len(_strip_stops(gram.replace(sub, "", 1), cfg.stop_phrases)) < 3:
                        return True
        return False


    # keep the longest phrase for a given set of posts: drop a gram when a longer kept gram
    # contains it and covers (almost) the same posts
    kept: list[str] = []
    for gram in sorted(candidates, key=lambda g: (-len(g), g)):
        docs = {d.key for d in candidates[gram][0]}
        absorbed = False
        for longer in kept:
            if gram in longer:
                longer_docs = {d.key for d in candidates[longer][0]}
                if len(longer_docs) >= 0.8 * len(docs):
                    absorbed = True
                    break
        if not absorbed and any({d.key for d in candidates[k][0]} == docs for k in kept):
            absorbed = True                       # same posts as a longer phrase already kept
        if not absorbed:
            kept.append(gram)
    kept = [g for g in kept if not only_old_core(g)]   # after merging, so fragments go with their phrase

    trends = []
    for gram in kept:
        docs, ratio = candidates[gram]
        rows_in = [r for d in docs for r in d.rows]
        best = sorted(rows_in, key=lambda r: (r["popularity_score"] is None, -(r["popularity_score"] or 0)))
        examples, seen_docs = [], set()
        for r in best:
            doc_key = f"c{r['cluster_id']}" if r["cluster_id"] else f"p{r['id']}"
            if doc_key in seen_docs:
                continue
            seen_docs.add(doc_key)
            examples.append(TrendExample(r["id"], r["source"], r["title"], r["url"], _image(r),
                                         r["likes"], r["popularity_score"]))
            if len(examples) >= cfg.max_examples:
                break
        trends.append(Trend(
            phrase=display_phrase(examples[0].title, gram) if examples else gram,
            key=gram,
            docs=len(docs),
            posts=len(rows_in),
            sources=sorted({r["source"] for r in rows_in}),
            baseline_docs=baseline_df.get(gram, 0),
            ratio=round(ratio, 1),
            examples=examples,
        ))
    trends.sort(key=lambda t: (-(t.docs * math.log1p(t.ratio)), -len(t.key), t.key))
    return trends[:limit]

