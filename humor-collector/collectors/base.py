"""Adapter interface every site collector implements.

A collector only knows the site: which list pages exist, how to build their URLs and how
to turn HTML/JSON into ``ListingEntry`` / ``PostDetail``. Fetching, rate limiting, storage,
de-duplication, scoring and error bookkeeping live in the pipeline, so parsers stay pure
functions of text that can be tested offline against fixtures.

Adding a site = subclass ``BaseCollector``, implement ``default_listings``,
``parse_listing`` (and ``parse_detail`` if the list lacks the body), register it in
``collectors/registry.py`` and add a ``[sources.<name>]`` block to the config.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, ClassVar, Iterable, TypeVar

from models.post import ListingEntry, PostDetail

log = logging.getLogger(__name__)
T = TypeVar("T")


class ParseError(Exception):
    """Page structure did not match what the parser expects."""


@dataclass(frozen=True)
class Listing:
    name: str              # stable id, used for cursors and stored with each observation
    kind: str              # "recent" (today/this week) or "archive" (older best posts, paged)
    label: str             # human description
    url: str               # absolute URL template; ``{page}`` is replaced by the page number
    max_pages: int = 1
    ranked: bool = False   # rows carry an explicit rank shown by the site


@dataclass
class ParseResult:
    entries: list[ListingEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)   # row-level problems; the rest still counts


def parse_rows(rows: Iterable[T], parse_one: Callable[[T], ListingEntry | None]) -> ParseResult:
    """Apply ``parse_one`` to every row; one broken row never discards the others."""
    result = ParseResult()
    for index, row in enumerate(rows):
        try:
            entry = parse_one(row)
        except Exception as exc:  # noqa: BLE001 - any row failure is recorded, not fatal
            result.errors.append(f"row {index}: {type(exc).__name__}: {exc}")
            continue
        if entry is not None:
            result.entries.append(entry)
    return result


def to_int(text: str | None) -> int | None:
    """'1,234' -> 1234; '' / '-' / None -> None (never 0 for missing)."""
    if text is None:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


class BaseCollector(ABC):
    name: ClassVar[str]
    display_name: ClassVar[str]
    homepage: ClassVar[str]
    method: ClassVar[str]                  # "official-api" | "rss" | "html"
    needs_detail: ClassVar[bool] = True    # list rows lack the body -> fetch each post page
    list_has_body: ClassVar[bool] = False  # list/API rows already carry the full post (no detail needed)

    def __init__(self, options: dict[str, Any] | None = None):
        self.options = dict(options or {})

    # ---------------------------------------------------------------- listings
    @abstractmethod
    def default_listings(self) -> list[Listing]:
        ...

    def listings(self, kind: str | None = None) -> list[Listing]:
        """Default listings with per-listing overrides from config:
        ``[sources.<name>.listings.<listing>] max_pages = 2, enabled = false``."""
        overrides: dict[str, dict] = self.options.get("listings", {})
        out = []
        for listing in self.default_listings():
            conf = overrides.get(listing.name, {})
            if not conf.get("enabled", True):
                continue
            if "max_pages" in conf:
                listing = replace(listing, max_pages=int(conf["max_pages"]))
            if kind is None or listing.kind == kind:
                out.append(listing)
        return out

    def listing_url(self, listing: Listing, page: int) -> str:
        return listing.url.format(page=page)

    @abstractmethod
    def parse_listing(self, text: str, listing: Listing, now: datetime, page: int = 1) -> ParseResult:
        ...

    def accept(self, entry: ListingEntry) -> bool:
        """Cheap filter before any detail request (board allow-list, minimum likes...)."""
        min_likes = self.options.get("min_likes")
        if min_likes is not None and entry.metrics.likes is not None and entry.metrics.likes < min_likes:
            return False
        return True

    # ------------------------------------------------------------------ detail
    def detail_url(self, entry: ListingEntry) -> str:
        return entry.url

    def parse_detail(self, text: str, entry: ListingEntry, now: datetime) -> PostDetail:
        raise NotImplementedError(f"{self.name} does not fetch post pages")

    def off_topic(self, category: str | None) -> bool:
        """True if the post comes from a board outside the configured humor boards."""
        allowed = self.options.get("allowed_categories")
        if not allowed or category is None:
            return False
        return category not in allowed
