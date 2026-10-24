"""개드립 (www.dogdrip.net) — LIST-ONLY adapter.

Access notes (checked 2026-10-24 from a cloud datacenter IP):
- robots.txt (Cloudflare managed): ``User-agent: * Allow: /``, ``Crawl-delay: 10``,
  ``Content-Signal: search=yes, ai-train=no, use=reference``.
- Board list pages ``/dogdrip?page=N`` returned 200 (served from the site's page cache).
- Post pages (``/dogdrip/<id>``, ``/<id>``), ``?sort_index=popular`` and the Rhymix RSS
  endpoint returned a Cloudflare "Just a moment..." challenge (403) or a 403 error page.
  We do not attempt to pass the challenge, so the body is not collected (``body`` = NULL,
  ``detail_status`` = ``list_only``). The list still gives title, 추천수, 댓글수 and an
  approximate time, which is useful as a popularity signal and for cross-site re-upload
  detection. If post pages become reachable from your server, add ``parse_detail`` with
  a fixture taken from a real page and set ``needs_detail = True``.
- No 조회수 is shown on the list -> views stay NULL.
"""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup, Tag

from collectors.base import BaseCollector, Listing, ParseError, ParseResult, parse_rows, to_int
from collectors.timeutil import parse_list_time
from models.post import ListingEntry, Metrics

BASE = "https://www.dogdrip.net"


class DogdripCollector(BaseCollector):
    name = "dogdrip"
    display_name = "개드립"
    homepage = BASE
    method = "html"
    needs_detail = False

    def default_listings(self) -> list[Listing]:
        return [
            Listing("board", "recent", "개드립 게시판 최신 페이지", f"{BASE}/dogdrip?page={{page}}", max_pages=2),
            Listing("board_archive", "archive", "개드립 게시판 과거 페이지", f"{BASE}/dogdrip?page={{page}}",
                    max_pages=3),
        ]

    def parse_listing(self, text: str, listing: Listing, now: datetime, page: int = 1) -> ParseResult:
        soup = BeautifulSoup(text, "lxml")
        board = soup.select_one("ul.ed.list")
        if board is None:
            raise ParseError("ul.ed.list not found")
        rows = [li for li in board.find_all("li", recursive=False) if "notice" not in (li.get("class") or [])]
        popular_rank = iter(range(1, len(rows) + 1))
        return parse_rows(rows, lambda li: self._parse_row(li, now, popular_rank))

    def _parse_row(self, li: Tag, now: datetime, popular_rank) -> ListingEntry | None:
        link = li.select_one("a.title-link[data-document-srl]")
        if link is None:
            return None                                    # ad slot
        srl = link["data-document-srl"].strip()
        if not srl.isdigit():
            raise ParseError(f"bad document srl {srl!r}")
        title = link.get_text(" ", strip=True)
        heading = link.find_parent("h5")
        comments_el = heading.select_one("span.text-primary") if heading else None
        likes = None
        thumbs = li.select_one(".list-meta i.fa-thumbs-up")
        if thumbs is not None:
            holder = thumbs.find_parent("span").find_parent("span")
            likes = to_int(re.sub(r"\s+", "", holder.get_text())) if holder else None
        time_el = li.select_one(".list-meta span.text-muted")
        published, approx = parse_list_time(time_el.get_text(" ", strip=True) if time_el else "", now)
        is_popular = "popular-item" in (li.get("class") or [])
        thumb = li.select_one("img.webzine-thumbnail")
        return ListingEntry(
            source_post_id=srl,
            url=f"{BASE}/dogdrip/{srl}",
            title=title,
            metrics=Metrics(likes=likes, comments_count=to_int(comments_el.get_text()) if comments_el else None),
            ranking=next(popular_rank) if is_popular else None,
            category="개드립",
            published_at=published,
            published_at_approx=approx if published else False,
            extra={
                "popular_badge": is_popular,
                "thumbnail_url": thumb.get("src") if thumb is not None else None,
                "list_only_reason": "post pages behind Cloudflare challenge (checked 2026-10-24)",
            },
        )
