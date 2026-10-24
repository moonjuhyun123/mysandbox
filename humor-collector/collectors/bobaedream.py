"""보배드림 (www.bobaedream.co.kr) — 베스트글 실시간 / 주간 / 월간 / 명예의 전당.

Access notes (checked 2026-10-24):
- robots.txt: ``User-agent: * Allow: /`` (only Amazonbot is blocked).
- 베스트글 mixes every board (cars, politics...). The realtime list shows the source board,
  so non-humor rows are dropped before any post request. Weekly/monthly/hall-of-fame lists
  have no board column; the board is read from the post page and off-topic posts are kept
  with an ``off_topic`` flag (so they are not re-fetched every run) and excluded from
  ``top``/``export`` by default.
- 이용약관 제11조: 회원은 서비스로 얻은 정보를 회사 사전 승낙 없이 복제·변경·출판 등에 사용하거나
  타인에게 제공할 수 없음 / 영리행위 금지. Collected text is analysis reference only.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from collectors.base import BaseCollector, Listing, ParseError, ParseResult, parse_rows, to_int
from collectors.timeutil import parse_full_datetime, parse_list_time
from models.post import ListingEntry, Metrics, PostDetail
from processing.normalize import clean_body_text

BASE = "https://www.bobaedream.co.kr"
_COUNT_RE = re.compile(r"조회\s*([\d,]+).*?추천\s*([\d,]+)\s*\|?\s*(.*)$", re.S)
LIST_URL = BASE + "/list?code=best&vdate={vdate}&page={{page}}"


class BobaedreamCollector(BaseCollector):
    name = "bobaedream"
    display_name = "보배드림"
    homepage = BASE
    method = "html"
    needs_detail = True

    def default_listings(self) -> list[Listing]:
        return [
            Listing("best_realtime", "recent", "베스트글 실시간", LIST_URL.format(vdate=""), max_pages=2),
            Listing("best_weekly", "recent", "베스트글 주간 순위", LIST_URL.format(vdate="w"),
                    max_pages=1, ranked=True),
            Listing("best_monthly", "recent", "베스트글 월간 순위", LIST_URL.format(vdate="m"),
                    max_pages=1, ranked=True),
            Listing("hall_of_fame", "archive", "베스트글 명예의 전당 (역대 순위)", LIST_URL.format(vdate="h"),
                    max_pages=3, ranked=True),
        ]

    # ------------------------------------------------------------------ list
    def parse_listing(self, text: str, listing: Listing, now: datetime, page: int = 1) -> ParseResult:
        soup = BeautifulSoup(text, "lxml")
        table = soup.select_one("table#boardlist")
        if table is None:
            raise ParseError("table#boardlist not found")
        columns = _column_map(table)
        if "title" not in columns:
            raise ParseError(f"unexpected list header: {columns}")
        rows = table.select("tbody tr")
        return parse_rows(rows, lambda tr: self._parse_row(tr, columns, now, listing))

    def _parse_row(
        self, tr: Tag, columns: dict[str, int], now: datetime, listing: Listing
    ) -> ListingEntry | None:
        link = tr.select_one("a.bsubject")
        if link is None or not link.get("href"):
            return None                                     # notice / ad rows
        qs = parse_qs(urlsplit(link["href"]).query)
        post_no = (qs.get("No") or [None])[0]
        if not post_no or not post_no.isdigit():
            raise ParseError(f"post number missing in {link['href']!r}")
        cells = tr.find_all("td", recursive=False)

        def cell(key: str) -> Tag | None:
            idx = columns.get(key)
            return cells[idx] if idx is not None and idx < len(cells) else None

        category = None
        cat_cell = tr.select_one("td.category")      # only the realtime list has this column
        if cat_cell is not None:
            category = cat_cell.get("title") or cat_cell.get_text(strip=True) or None
        reply = tr.select_one("strong.totreply")
        date_cell = cell("date")
        published, approx = parse_list_time(date_cell.get_text(strip=True) if date_cell else "", now)
        if listing.kind == "archive" and approx:
            # Hall of fame rows show "MM/DD" without a year and span several years:
            # leave the date unknown until the post page gives the full date.
            published, approx = None, False
        rank_cell = cell("rank")
        return ListingEntry(
            source_post_id=post_no,
            url=f"{BASE}/view?code=best&No={post_no}",
            title=(link.get("title") or link.get_text(" ", strip=True)).strip(),
            metrics=Metrics(
                views=_int(cell("views")),
                likes=_int(cell("likes")),
                comments_count=to_int(reply.get_text()) if reply else None,
            ),
            ranking=_int(rank_cell),
            category=category,
            published_at=published,
            published_at_approx=approx if published else False,
        )

    def accept(self, entry: ListingEntry) -> bool:
        if self.off_topic(entry.category):
            return False
        return super().accept(entry)

    # ---------------------------------------------------------------- detail
    def parse_detail(self, text: str, entry: ListingEntry, now: datetime) -> PostDetail:
        soup = BeautifulSoup(text, "lxml")
        body_el = soup.select_one(".bodyCont")
        head = soup.select_one(".writerProfile dt")
        if body_el is None or head is None:
            raise ParseError("post body (.bodyCont) or header not found")
        title_el = head.find("strong")
        comments = None
        if title_el is not None:
            reply = title_el.find("em")
            if reply is not None:
                comments = to_int(reply.get_text())
                reply.extract()
        views = likes = published = None
        count_el = soup.select_one(".countGroup")
        if count_el is not None:
            m = _COUNT_RE.search(count_el.get_text(" ", strip=True))
            if m:
                views, likes = to_int(m.group(1)), to_int(m.group(2))
                published = parse_full_datetime(m.group(3))
        board = None
        for a in soup.select(".Ustorytitle a[href*='code=']"):
            board = a.get_text(strip=True) or board
        media = []
        for el in body_el.select("img[src], video[src], video source[src], iframe[src]"):
            src = urljoin(BASE, el["src"])
            if src not in media:
                media.append(src)
        return PostDetail(
            title=title_el.get_text(" ", strip=True) if title_el is not None else None,
            body=clean_body_text(body_el.get_text("\n")),
            published_at=published,
            category=board,
            metrics=Metrics(views=views, likes=likes, comments_count=comments),
            media_urls=media,
        )


def _column_map(table: Tag) -> dict[str, int]:
    """Map header labels to column indexes; the realtime and ranked lists differ."""
    mapping: dict[str, int] = {}
    for idx, th in enumerate(table.select("thead th, thead td")):
        label = th.get_text(" ", strip=True)
        if label.startswith("순위"):
            mapping["rank"] = idx
        elif label.startswith("제목"):
            mapping["title"] = idx
        elif label.startswith("등록일"):
            mapping["date"] = idx
        elif label.startswith("추천"):
            mapping["likes"] = idx
        elif label.startswith("조회"):
            mapping["views"] = idx
    return mapping


def _int(el: Tag | None) -> int | None:
    return to_int(el.get_text()) if el is not None else None
