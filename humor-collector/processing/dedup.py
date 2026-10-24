"""Duplicate and re-upload detection.

Three levels, from cheap and certain to expensive and conservative:
1. same (source, source_post_id)  -> same row, numbers are updated     (storage UNIQUE)
2. same normalized URL            -> same row                          (storage UNIQUE)
3. similar title/body             -> separate rows linked by ``cluster_id``

Level 3 never merges or deletes rows: every copy keeps its own source link and numbers,
and the cluster tells "this joke was popular on N communities". Rules are deliberately
strict, because merging two different posts is worse than missing a re-upload:

- both bodies have text: title similarity >= ``title_ratio`` AND body 3-gram Jaccard >= ``body_jaccard``
- both bodies are long: body Jaccard >= ``body_only_jaccard`` alone (title was rewritten)
- otherwise (image posts, list-only sources): normalized titles identical and at least
  ``min_exact_title_len`` characters, published within ``window_days`` of each other.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Mapping

from rapidfuzz import fuzz

from collectors.timeutil import parse_iso
from processing.normalize import jaccard, normalize_text, shingles


@dataclass
class DedupConfig:
    title_ratio: float = 90.0
    body_jaccard: float = 0.6
    body_only_jaccard: float = 0.85
    min_body_len: int = 30
    long_body_len: int = 150
    min_exact_title_len: int = 8
    window_days: int = 30
    max_candidates: int = 5000


@dataclass
class Candidate:
    id: int
    source: str
    title_norm: str
    body: str | None
    published_at: str | None
    cluster_id: int | None = None

    @classmethod
    def from_row(cls, row: Mapping) -> "Candidate":
        return cls(row["id"], row["source"], row["title_norm"] or "", row["body"], row["published_at"],
                   row["cluster_id"])


@dataclass
class Match:
    candidate: Candidate
    reason: str
    similarity: float


def compare(a: Candidate, b: Candidate, cfg: DedupConfig) -> Match | None:
    if not _within_window(a.published_at, b.published_at, cfg.window_days):
        return None
    ta, tb = a.title_norm, b.title_norm
    title_sim = fuzz.ratio(ta, tb) if ta and tb else 0.0
    ba, bb = normalize_text(a.body), normalize_text(b.body)
    if len(ba) >= cfg.min_body_len and len(bb) >= cfg.min_body_len:
        body_sim = jaccard(shingles(ba), shingles(bb))
        if title_sim >= cfg.title_ratio and body_sim >= cfg.body_jaccard:
            return Match(b, "title+body", (title_sim / 100 + body_sim) / 2)
        if len(ba) >= cfg.long_body_len and len(bb) >= cfg.long_body_len and body_sim >= cfg.body_only_jaccard:
            return Match(b, "body", body_sim)
        return None
    if ta and ta == tb and len(ta) >= cfg.min_exact_title_len:
        return Match(b, "exact-title", 1.0)
    return None


def find_duplicate(post: Candidate, candidates: Iterable[Candidate], cfg: DedupConfig) -> Match | None:
    best: Match | None = None
    for cand in candidates:
        if cand.id == post.id:
            continue
        match = compare(post, cand, cfg)
        if match and (best is None or match.similarity > best.similarity):
            best = match
    return best


def _within_window(a: str | None, b: str | None, days: int) -> bool:
    da, db = parse_iso(a), parse_iso(b)
    if da is None or db is None:
        return True        # unknown date: rely on the text rules alone
    return abs(da - db) <= timedelta(days=days)
