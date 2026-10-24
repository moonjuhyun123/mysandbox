"""Lemmy (federated Reddit-like forum) — official public HTTP API v3, no account needed.

Access notes (checked 2026-10-24):
- ``GET {instance}/api/v3/post/list?community_name=...&sort=TopDay&limit=..&page=..`` returns
  JSON with title, body (markdown, null for image posts), media url, ap_id (canonical link),
  published time and counts (upvotes, downvotes, comments). There is no view count.
- lemmy.world robots.txt sets ``Crawl-delay: 60`` and does not disallow ``/api``; the HTTP
  client honours the delay (one request per minute to that host).
- Sorts ``TopDay``/``TopWeek`` serve "recent popular", ``TopYear``/``TopAll`` serve "past popular".
"""
from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlencode, urlsplit

from collectors.base import BaseCollector, Listing, ParseError, ParseResult, parse_rows
from collectors.timeutil import parse_iso
from models.post import ListingEntry, Metrics

DEFAULT_COMMUNITIES = ["funny@lemmy.world", "jokes@lemmy.world"]
RECENT_SORTS = ["TopDay", "TopWeek"]
ARCHIVE_SORTS = ["TopAll"]


class LemmyCollector(BaseCollector):
    name = "lemmy"
    display_name = "Lemmy"
    homepage = "https://join-lemmy.org"
    method = "official-api"
    needs_detail = False
    list_has_body = True

    @property
    def instance(self) -> str:
        return self.options.get("instance", "https://lemmy.world").rstrip("/")

    @property
    def page_size(self) -> int:
        return int(self.options.get("page_size", 20))

    def default_listings(self) -> list[Listing]:
        listings = []
        communities = self.options.get("communities", DEFAULT_COMMUNITIES)
        for kind, sorts in (("recent", self.options.get("recent_sorts", RECENT_SORTS)),
                            ("archive", self.options.get("archive_sorts", ARCHIVE_SORTS))):
            for community in communities:
                for sort in sorts:
                    query = urlencode({"community_name": community, "sort": sort,
                                       "limit": self.page_size, "type_": "All"})
                    listings.append(Listing(
                        name=f"{community}:{sort}",
                        kind=kind,
                        label=f"{community} sorted by {sort}",
                        url=f"{self.instance}/api/v3/post/list?{query}&page={{page}}",
                        max_pages=int(self.options.get("max_pages", 1)),
                        ranked=True,
                    ))
        return listings

    def parse_listing(self, text: str, listing: Listing, now: datetime, page: int = 1) -> ParseResult:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParseError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict) or "posts" not in data:
            raise ParseError("response has no 'posts' field")
        offset = (page - 1) * self.page_size
        items = list(enumerate(data["posts"], start=1))
        return parse_rows(items, lambda item: self._parse_item(item[1], offset + item[0]))

    def _parse_item(self, item: dict, rank: int) -> ListingEntry | None:
        post = item["post"]
        counts = item.get("counts") or {}
        community = item.get("community") or {}
        if post.get("removed") or post.get("deleted"):
            return None
        community_name = community.get("name")
        actor = community.get("actor_id")
        if community_name and actor:
            community_name = f"{community_name}@{urlsplit(actor).netloc}"
        media = [post["url"]] if post.get("url") else []
        return ListingEntry(
            source_post_id=str(post["id"]),
            url=post.get("ap_id") or f"{self.instance}/post/{post['id']}",
            title=post["name"],
            body=post.get("body") or "",       # the API returns the whole post: "" = no text
            media_urls=media,
            metrics=Metrics(
                likes=counts.get("upvotes"),
                dislikes=counts.get("downvotes"),
                comments_count=counts.get("comments"),
            ),
            ranking=rank,
            category=community_name,
            published_at=parse_iso(post.get("published")),
            extra={
                "score": counts.get("score"),
                "nsfw": bool(post.get("nsfw")),
                "instance": urlsplit(self.instance).netloc,
            },
        )
