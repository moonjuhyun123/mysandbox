"""Trending meme / 짤 detection on our community data.

A meme spreads as many different posts whose titles repeat its name with varying spacing and
particles ("담배피는 눈사람.jpg", "담배 피는 눈사람 근황", "눈사람이 담배피는 짤"). So we look for
phrases - character n-grams of the space-free title that begin at a word start - which

- occur in at least ``min_docs`` different posts (re-upload clusters count once) in the last
  ``window_days`` days, or in 2 posts from 2+ different communities,
- are much more frequent now than in the preceding ``baseline_days`` (burst ratio; skipped while
  the database holds less than ``min_baseline_days`` of earlier history), and
- are not just generic board words ("근황", "레전드", "모음"... see ``stop_phrases``).

Overlapping phrases are reduced to one per set of posts: the longest phrase shared by exactly
those posts, and a longer phrase is dropped when a shorter one inside it covers more posts
("담배피는 눈사람이" -> "담배피는 눈사람").
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
from processing.normalize import clean_title, normalize_text

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
    min_baseline_days: float = 3.0     # less earlier history than this: no burst test yet
    min_docs: int = 3
    min_sources_if_two_docs: int = 2   # 2 posts are enough when they come from 2+ communities
    min_len: int = 4
    max_len: int = 40
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
    ratio: float | None         # None while there is not enough earlier history
    examples: list[TrendExample] = field(default_factory=list)


@dataclass
class _Title:
    """A title as space-free normalized text, the positions where words start in it, and the
    map back to the cleaned original (for display)."""
    cleaned: str
    text: str
    starts: list[int]
    index: list[int]

    def find_at_word(self, gram: str) -> int | None:
        for s in self.starts:
            if self.text.startswith(gram, s):
                return s
        return None

    def span(self, gram: str) -> tuple[int, int] | None:
        s = self.find_at_word(gram)
        if s is None:
            return None
        return self.index[s], self.index[s + len(gram) - 1] + 1

    def grams(self, lo: int, hi: int) -> set[str]:
        out = set()
        for s in self.starts:
            for e in range(s + lo, min(s + hi, len(self.text)) + 1):
                out.add(self.text[s:e])
        return out


def analyze_title(title: str) -> _Title:
    cleaned = clean_title(title)           # NFKC first, so decomposed (NFD) Hangul is composed
    text, index, starts = [], [], []
    in_word = False
    for i, ch in enumerate(cleaned):
        norm = normalize_text(ch)
        if norm:
            if not in_word:
                starts.append(len(text))
            for c in norm:
                text.append(c)
                index.append(i)
        in_word = bool(norm) or ch.isalnum()
    return _Title(cleaned, "".join(text), starts, index)


def display_phrase(title: str, key: str) -> str:
    """The phrase as written (with its spaces) in a title containing ``key`` at a word start."""
    t = analyze_title(title)
    span = t.span(key)
    return t.cleaned[span[0] : span[1]].strip() if span else key


def _to_word_end(t: _Title, key: str) -> str:
    """The phrase extended to the end of its last word, normalized ("근황레전" -> "근황레전드")."""
    span = t.span(key)
    if span is None:
        return key
    end = span[1]
    while end < len(t.cleaned) and t.cleaned[end].isalnum():
        end += 1
    return normalize_text(t.cleaned[span[0] : end])


def _strip_stops(text: str, stops: Sequence[str]) -> str:
    for stop in sorted(stops, key=len, reverse=True):
        text = text.replace(stop, "")
    return text


def _is_generic(gram: str, stops: Sequence[str]) -> bool:
    if sum(ch.isdigit() for ch in gram) * 2 >= len(gram):
        return True
    if any(gram in stop for stop in stops):      # a piece of a stop phrase ("iphone" of "iphone듀오")
        return True
    return len(_strip_stops(gram, stops)) < 2


def _when(row: Mapping) -> datetime | None:
    return parse_iso(row["published_at"]) or parse_iso(row["first_seen_at"])


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


@dataclass
class _Doc:
    key: str                                  # cluster id or post id
    rows: list[tuple[Mapping, _Title]]

    def grams(self, lo: int, hi: int) -> set[str]:
        out: set[str] = set()
        for _, t in self.rows:
            out |= t.grams(lo, hi)
        return out


def _docs(rows: Iterable[Mapping]) -> list[_Doc]:
    grouped: dict[str, _Doc] = {}
    for row in rows:
        t = analyze_title(row["title"] or "")
        if not t.text:
            continue
        key = f"c{row['cluster_id']}" if row["cluster_id"] else f"p{row['id']}"
        grouped.setdefault(key, _Doc(key, [])).rows.append((row, t))
    return list(grouped.values())


def _score(row: Mapping) -> float:
    return row["popularity_score"] if row["popularity_score"] is not None else -1.0


def find_trends(rows: Sequence[Mapping], now: datetime, cfg: TrendConfig | None = None,
                limit: int = 20) -> list[Trend]:
    cfg = cfg or TrendConfig()
    window_start = now - timedelta(days=cfg.window_days)
    baseline_start = window_start - timedelta(days=cfg.baseline_days)
    recent_rows, baseline_rows, oldest = [], [], None
    for row in rows:
        when = _when(row)
        if when is None:
            continue
        if when >= window_start:
            recent_rows.append(row)
        elif when >= baseline_start:
            baseline_rows.append(row)
            oldest = when if oldest is None or when < oldest else oldest

    # burst test only with enough earlier history; divide by the history we actually have
    span_days = (window_start - oldest).total_seconds() / 86400 if oldest else 0.0
    base_span = span_days if span_days >= cfg.min_baseline_days else None

    recent_docs = _docs(recent_rows)
    recent_df: dict[str, list[_Doc]] = {}
    for doc in recent_docs:
        for gram in doc.grams(cfg.min_len, cfg.max_len):
            recent_df.setdefault(gram, []).append(doc)
    baseline_df: dict[str, int] = {}
    if base_span:
        wanted = {g for g, docs in recent_df.items() if len(docs) >= 2}
        for doc in _docs(baseline_rows):
            for gram in doc.grams(cfg.min_len, cfg.max_len) & wanted:
                baseline_df[gram] = baseline_df.get(gram, 0) + 1

    def ratio_of(gram: str) -> float | None:
        if not base_span:
            return None
        return (len(recent_df[gram]) / cfg.window_days) / ((baseline_df.get(gram, 0) + 1) / base_span)

    candidates: dict[str, frozenset[str]] = {}
    for gram, docs in recent_df.items():
        sources = {r["source"] for d in docs for r, _ in d.rows}
        enough = len(docs) >= cfg.min_docs or (len(docs) >= 2 and len(sources) >= cfg.min_sources_if_two_docs)
        if not enough:
            continue
        sample = next(t for d in docs for _, t in d.rows if t.find_at_word(gram) is not None)
        if _is_generic(gram, cfg.stop_phrases) or _is_generic(_to_word_end(sample, gram), cfg.stop_phrases):
            continue
        ratio = ratio_of(gram)
        if ratio is not None and ratio < cfg.min_ratio:
            continue
        candidates[gram] = frozenset(d.key for d in docs)

    # one phrase per set of posts: the longest phrase shared by exactly those posts
    by_docs: dict[frozenset[str], str] = {}
    for gram in sorted(candidates, key=lambda g: (-len(g), g)):
        by_docs.setdefault(candidates[gram], gram)
    kept = list(by_docs.values())
    # a longer phrase is redundant when a phrase inside it covers more (or the same) posts
    kept = [g for g in kept
            if not any(o != g and o in g and candidates[g] <= candidates[o] for o in kept)]

    def only_old_core(gram: str) -> bool:
        """An always-common phrase plus generic leftovers ("아이폰듀오" + "리뷰") is not new."""
        if not base_span:
            return False
        for n in range(cfg.min_len, len(gram)):
            for i in range(len(gram) - n + 1):
                sub = gram[i : i + n]
                if sub in recent_df and (ratio_of(sub) or 0) < cfg.min_ratio:
                    if len(_strip_stops(gram.replace(sub, "", 1), cfg.stop_phrases)) < 2:
                        return True
        return False

    kept = [g for g in kept if not only_old_core(g)]

    trends = []
    for gram in kept:
        docs = recent_df[gram]
        examples: list[TrendExample] = []
        ranked_docs = sorted(docs, key=lambda d: -max(_score(r) for r, _ in d.rows))
        phrase = None
        for doc in ranked_docs:
            # within a re-upload cluster prefer a copy whose title actually has the phrase
            with_phrase = [(r, t) for r, t in doc.rows if t.find_at_word(gram) is not None]
            row, t = max(with_phrase or doc.rows, key=lambda rt: _score(rt[0]))
            if phrase is None and with_phrase:
                span = t.span(gram)
                phrase = t.cleaned[span[0] : span[1]].strip()
            if len(examples) < cfg.max_examples:
                examples.append(TrendExample(row["id"], row["source"], row["title"], row["url"], _image(row),
                                             row["likes"], row["popularity_score"]))
        ratio = ratio_of(gram)
        all_rows = [r for d in docs for r, _ in d.rows]
        trends.append(Trend(
            phrase=phrase or gram, key=gram, docs=len(docs), posts=len(all_rows),
            sources=sorted({r["source"] for r in all_rows}), baseline_docs=baseline_df.get(gram, 0),
            ratio=round(ratio, 1) if ratio is not None else None, examples=examples,
        ))
    trends.sort(key=lambda t: (-(t.docs * math.log1p(t.ratio if t.ratio is not None else 10.0)), -len(t.key), t.key))
    return trends[:limit]
