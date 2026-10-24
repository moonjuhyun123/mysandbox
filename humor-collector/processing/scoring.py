"""Popularity score normalised per community.

Raw numbers are not comparable across sites (a weekly-best post on one site has 100k views,
a hit post on another 15k), so every metric is converted to a *percentile within its own
source* first, then combined:

    pct_m      = mid-rank percentile of log1p(value) among that source's stored posts,
                 shrunk toward 0.5 when the source has few posts: (n*pct + k*0.5) / (n + k)
    metric     = weighted mean of pct_m over the metrics the site actually provides
                 (default weights likes 0.5, comments 0.25, views 0.25; missing ones are
                 left out and the weights renormalised - never treated as 0)
    metric    *= 1 - dislike_penalty * (1 - likes / (likes + dislikes))   if dislikes are shown
    rank bonus = rank_weight * max(0, 1 - (rank - 1) / rank_horizon) * (1 - metric)
                 (a site-assigned rank can only raise the score, never lower it)
    cross-site = + cross_source_bonus per extra community in the same re-upload cluster
    score      = 100 * min(1, metric + rank bonus + cross-site)

Percentiles are recomputed for all posts after each collection run, so scores move as
the reference population grows. Age is not modelled: a 1-hour-old post simply has fewer
likes yet; its numbers are refreshed on later runs while it stays on the best lists.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Mapping, Sequence

METRICS = ("likes", "comments_count", "views")


@dataclass
class ScoringConfig:
    weights: dict[str, float] = field(
        default_factory=lambda: {"likes": 0.5, "comments_count": 0.25, "views": 0.25}
    )
    shrink_k: float = 10.0
    dislike_penalty: float = 0.15
    rank_weight: float = 0.15
    rank_horizon: int = 100
    cross_source_bonus: float = 0.05
    cross_source_bonus_max: float = 0.10


def _percentile(sorted_values: Sequence[float], x: float, k: float) -> float:
    n = len(sorted_values)
    if n == 0:
        return 0.5
    pct = (bisect_left(sorted_values, x) + bisect_right(sorted_values, x)) / 2 / n
    return (n * pct + k * 0.5) / (n + k)


def compute_scores(rows: Sequence[Mapping], cfg: ScoringConfig | None = None) -> dict[int, float | None]:
    cfg = cfg or ScoringConfig()
    dist: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    cluster_sources: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        for metric in METRICS:
            value = row[metric]
            if value is not None:
                dist[row["source"]][metric].append(math.log1p(max(value, 0)))
        if row["cluster_id"] is not None:
            cluster_sources[row["cluster_id"]].add(row["source"])
    for per_metric in dist.values():
        for values in per_metric.values():
            values.sort()

    scores: dict[int, float | None] = {}
    for row in rows:
        parts = []
        for metric, weight in cfg.weights.items():
            value = row[metric]
            if value is None or weight <= 0:
                continue
            pct = _percentile(dist[row["source"]][metric], math.log1p(max(value, 0)), cfg.shrink_k)
            parts.append((weight, pct))
        if not parts:
            scores[row["id"]] = None
            continue
        metric_score = sum(w * p for w, p in parts) / sum(w for w, _ in parts)
        likes, dislikes = row["likes"], row["dislikes"]
        if likes is not None and dislikes is not None and likes + dislikes > 0:
            metric_score *= 1 - cfg.dislike_penalty * (1 - likes / (likes + dislikes))
        score = metric_score
        if row["ranking"] is not None and row["ranking"] > 0:
            rank_score = max(0.0, 1 - (row["ranking"] - 1) / cfg.rank_horizon)
            score += cfg.rank_weight * rank_score * (1 - metric_score)
        if row["cluster_id"] is not None:
            extra_sources = len(cluster_sources[row["cluster_id"]]) - 1
            score += min(cfg.cross_source_bonus * extra_sources, cfg.cross_source_bonus_max)
        scores[row["id"]] = round(100 * min(1.0, score), 2)
    return scores
