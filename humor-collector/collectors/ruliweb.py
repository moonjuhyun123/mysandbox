"""루리웹 (bbs.ruliweb.com) — 유머 BEST / 힛갤.

Access notes (checked 2026-10-24):
- robots.txt allows ``/best/...`` list pages and ``/.../read/<id>`` post pages for generic bots,
  but disallows ``orderby=``, ``range=``, ``view=``, ``cate=`` query params, so the
  "1D / 7D / 30D 추천순" variants are NOT used. Only the default lists with ``?page=N``.
- List rows already carry 추천수, 조회수 and 댓글수; the post page adds body, 비추천, exact time.
- Board RSS (``/community/board/300143/rss``) exists but only lists the newest posts with no
  popularity numbers, so it cannot select popular posts.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from collectors.base import BaseCollector, Listing, ParseError, ParseResult, parse_rows, to_int
from collectors.timeutil import parse_full_datetime, parse_list_time
from models.post import ListingEntry, Metrics, PostDetail
from processing.normalize import clean_body_text

BASE = "https://bbs.ruliweb.com"
_READ_RE = re.compile(r"/board/(\d+)/read/(\d+)")
_VIEWS_RE = re.compile(r"조회\s*([\d,]+)")


class RuliwebCollector(BaseCollector):
    name = "ruliweb"
    display_name = "루리웹"
    homepage = BASE
    method = "html"
    needs_detail = True

    def default_listings(self) -> list[Listing]:
        return [
            Listing("humor_best", "recent", "유머 BEST (실시간 등록순)",
                    f"{BASE}/best/humor_only/now?page={{page}}", max_pages=3),
            Listing("hit_gallery", "recent", "힛갤 (선정된 인기글)",
                    f"{BASE}/best/selection?page={{page}}", max_pages=2),
            Listing("humor_best_archive", "archive", "유머 BEST 과거 페이지",
                    f"{BASE}/best/humor_only/now?page={{page}}", max_pages=5),
            Listing("hit_gallery_archive", "archive", "힛갤 과거 페이지",
                    f"{BASE}/best/selection?page={{page}}", max_pages=5),
        ]

    # ------------------------------------------------------------------ list
    def parse_listing(self, text: str, listing: Listing, now: datetime, page: int = 1) -> ParseResult:
        soup = BeautifulSoup(text, "lxml")
        table = soup.select_one("table.board_list_table")
        if table is None:
            raise ParseError("table.board_list_table not found")
        rows = [tr for tr in table.select("tr.table_body") if "notice" not in (tr.get("class") or [])]
        return parse_rows(rows, lambda tr: self._parse_row(tr, now))

    def _parse_row(self, tr: Tag, now: datetime) -> ListingEntry | None:
        link = tr.select_one("a.subject_link")
        if link is None or not link.get("href"):
            return None
        match = _READ_RE.search(link["href"])
        if not match:
            raise ParseError(f"unexpected post link {link['href']!r}")
        board_id, post_id = match.groups()
        title_el = link.select_one(".text_over")
        if title_el is None:
            raise ParseError("title element missing")
        title = title_el.get_text(" ", strip=True)
        rank = None
        classes = tr.get("class") or []
        if "best_top_row" in classes:
            rank_el = link.find("span")
            rank = to_int(rank_el.get_text()) if rank_el is not None else None
        reply = link.select_one(".num_reply")
        time_el = tr.select_one("td.time")
        published, approx = parse_list_time(time_el.get_text(strip=True) if time_el else "", now)
        return ListingEntry(
            source_post_id=f"{board_id}/{post_id}",
            url=f"{BASE}/community/board/{board_id}/read/{post_id}",
            title=title,
            metrics=Metrics(
                views=_cell_int(tr, "td.hit"),
                likes=_cell_int(tr, "td.recomd"),
                comments_count=to_int(reply.get_text()) if reply else None,
            ),
            ranking=rank,
            published_at=published,
            published_at_approx=approx if published else False,
            extra={"board_id": board_id, "top_row": rank is not None},
        )

    def accept(self, entry: ListingEntry) -> bool:
        allowed_boards = self.options.get("allowed_boards")
        if allowed_boards and entry.extra.get("board_id") not in allowed_boards:
            return False
        return super().accept(entry)

    # ---------------------------------------------------------------- detail
    def parse_detail(self, text: str, entry: ListingEntry, now: datetime) -> PostDetail:
        soup = BeautifulSoup(text, "lxml")
        content = soup.select_one(".view_content")
        title_el = soup.select_one(".subject_inner_text")
        if content is None or title_el is None:
            raise ParseError("post body (.view_content) or title not found")
        media = []
        for el in content.select("img[src], video[src], video source[src], iframe[src]"):
            src = urljoin(BASE, el["src"])
            if src not in media:
                media.append(src)
        info = soup.select_one(".user_info")
        views = None
        if info is not None:
            m = _VIEWS_RE.search(info.get_text(" ", strip=True))
            views = to_int(m.group(1)) if m else None
        date_el = soup.select_one(".regdate")
        board_el = soup.select_one("#board_name")
        cat_el = soup.select_one(".category_text")
        reply_el = soup.select_one(".comment_count .reply_count") or soup.select_one("strong.reply_count")
        return PostDetail(
            title=title_el.get_text(" ", strip=True),
            body=clean_body_text(content.get_text("\n")),
            published_at=parse_full_datetime(date_el.get_text(" ", strip=True)) if date_el else None,
            category=board_el.get_text(strip=True) if board_el else None,
            metrics=Metrics(
                views=views,
                likes=_text_int(soup.select_one(".like_value")),
                dislikes=_text_int(soup.select_one(".dislike_value")),
                comments_count=_text_int(reply_el),
            ),
            media_urls=media,
            extra={"board_category": cat_el.get_text(strip=True).strip("[]") if cat_el else None},
        )


def _cell_int(tr: Tag, selector: str) -> int | None:
    return _text_int(tr.select_one(selector))


def _text_int(el: Tag | None) -> int | None:
    return to_int(el.get_text()) if el is not None else None
