"""Data shapes passed between collectors, processing and storage.

Rule: a metric the site does not show is ``None`` (stored as SQL NULL).
Never substitute 0 or an estimate for a missing number.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Metrics:
    views: int | None = None
    likes: int | None = None
    dislikes: int | None = None
    comments_count: int | None = None

    def merged_with(self, other: "Metrics | None") -> "Metrics":
        """Values from ``other`` win when present (detail page is fresher than the list)."""
        if other is None:
            return Metrics(self.views, self.likes, self.dislikes, self.comments_count)
        return Metrics(
            views=other.views if other.views is not None else self.views,
            likes=other.likes if other.likes is not None else self.likes,
            dislikes=other.dislikes if other.dislikes is not None else self.dislikes,
            comments_count=(
                other.comments_count if other.comments_count is not None else self.comments_count
            ),
        )

    def as_dict(self) -> dict[str, int | None]:
        return {
            "views": self.views,
            "likes": self.likes,
            "dislikes": self.dislikes,
            "comments_count": self.comments_count,
        }


@dataclass
class ListingEntry:
    """One row of a best/popular list page (or one item of an API list response)."""

    source_post_id: str
    url: str
    title: str
    metrics: Metrics = field(default_factory=Metrics)
    ranking: int | None = None          # explicit rank shown by the site, else None
    category: str | None = None         # board / 분류 name as the site shows it
    published_at: datetime | None = None
    published_at_approx: bool = False   # True when derived from "3 시간 전" / "10:37" style text
    body: str | None = None             # only for sources whose list already carries the body (APIs)
    media_urls: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class PostDetail:
    """What a post page adds on top of the list row."""

    title: str | None = None
    body: str | None = None
    published_at: datetime | None = None
    category: str | None = None
    metrics: Metrics = field(default_factory=Metrics)
    media_urls: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)


@dataclass
class PostRecord:
    """A post ready to be stored."""

    source: str
    source_post_id: str
    url: str
    title: str
    body: str | None
    published_at: datetime | None
    published_at_approx: bool
    collected_at: datetime
    category: str | None
    metrics: Metrics
    ranking: int | None
    listing: str
    media_urls: list[str]
    detail_status: str                  # ok | failed | not_needed
    extra: dict = field(default_factory=dict)
    content_category: str | None = None
    content_labels: list[str] = field(default_factory=list)
    content_flags: list[str] = field(default_factory=list)
    classifier: str | None = None
