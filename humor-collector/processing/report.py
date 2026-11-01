"""Local HTML galleries for `trends` and `youtube` (images are hot-linked, nothing is downloaded).

YouTube results always appear in their own section, in the API's order, each card labelled
"YouTube" and linked to the video (Developer Policies III.C.5, III.F.2).
"""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from processing.trends import Trend

_CSS = """
:root{--bg:#fafafa;--card:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e6;--accent:#c4302b}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1f1f23;--fg:#ececf0;--muted:#a0a0a8;--line:#33333a}}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}
main{max-width:1100px;margin:0 auto}h1{font-size:22px}h2{font-size:18px;margin:28px 0 4px}
.meta{color:var(--muted);font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin-top:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;aspect-ratio:16/10;object-fit:cover;background:var(--line)}
.card .body{padding:8px 10px}.card a{color:inherit;text-decoration:none}.card a:hover{text-decoration:underline}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:4px;border:1px solid var(--line);color:var(--muted)}
.yt{border-color:var(--accent);color:var(--accent);font-weight:600}
section.youtube{border-left:3px solid var(--accent);padding-left:12px;margin-top:14px}
"""


def _page(title: str, body: str) -> str:
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>{body}</main></body></html>")


def _safe_url(url: str | None) -> str | None:
    """Only http(s) links from collected data reach src/href (no javascript:, data:, ...)."""
    if url and url.strip().lower().startswith(("https://", "http://")):
        return escape(url.strip())
    return None


def _img(url: str | None) -> str:
    safe = _safe_url(url)
    if not safe:
        return ""
    return f'<img src="{safe}" alt="" loading="lazy" referrerpolicy="no-referrer">'


def _youtube_section(query: str, rows: Sequence[Mapping]) -> str:
    if not rows:
        return (f'<section class="youtube"><h3>YouTube 검색: {escape(query)}</h3>'
                f'<p class="meta">결과 없음</p></section>')
    fetched = rows[0]["fetched_at"]
    cards = []
    for r in rows:
        url = f"https://www.youtube.com/watch?v={escape(r['video_id'])}"
        views = f"조회수 {r['view_count']:,}" if r["view_count"] is not None else "조회수 비공개"
        cards.append(
            f'<div class="card"><a href="{url}" target="_blank" rel="noopener">{_img(r["thumbnail_url"])}</a>'
            f'<div class="body"><span class="tag yt">YouTube</span> '
            f'<a href="{url}" target="_blank" rel="noopener">{escape(r["title"])}</a>'
            f'<div class="meta">{escape(r["channel_title"] or "")} · {views} · {escape((r["published_at"] or "")[:10])}</div>'
            f"</div></div>")
    return (f'<section class="youtube"><h3>YouTube 검색: {escape(query)}</h3>'
            f'<p class="meta">출처: YouTube (YouTube Data API, {escape(fetched)} 조회, 29일 후 자동 삭제). '
            f'YouTube가 준 순서 그대로입니다.</p><div class="grid">{"".join(cards)}</div></section>')


def write_trends_html(path: str | Path, trends: Iterable[Trend], *, window_days: int,
                      youtube: Mapping[str, Sequence[Mapping]] | None = None) -> Path:
    parts = [f"<h1>최근 {window_days}일 반복되는 밈·짤 문구</h1>",
             '<p class="meta">우리 커뮤니티 수집 데이터(루리웹·개드립·보배드림·Bluesky·Lemmy)의 글 제목에서 '
             "여러 번 반복된 문구입니다. 이미지는 원본 사이트에서 직접 불러옵니다.</p>"]
    for i, t in enumerate(trends, 1):
        parts.append(f"<h2>{i}. {escape(t.phrase)}</h2>"
                     f'<p class="meta">{t.docs}개 글 · {escape(", ".join(t.sources))} · 이전 기간 {t.baseline_docs}건 (x{t.ratio})</p>')
        cards = []
        for e in t.examples:
            href = _safe_url(e.url) or "#"
            cards.append(f'<div class="card"><a href="{href}" target="_blank" rel="noopener">{_img(e.image)}</a>'
                         f'<div class="body"><span class="tag">{escape(e.source)}</span> '
                         f'<a href="{href}" target="_blank" rel="noopener">{escape(e.title)}</a>'
                         f'<div class="meta">추천 {e.likes if e.likes is not None else "-"}</div></div></div>')
        parts.append(f'<div class="grid">{"".join(cards)}</div>')
        if youtube is not None and t.phrase in youtube:
            parts.append(_youtube_section(t.phrase, youtube[t.phrase]))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page("밈·짤 트렌드", "".join(parts)), encoding="utf-8")
    return out


def write_youtube_html(path: str | Path, groups: Mapping[str, Sequence[Mapping]]) -> Path:
    parts = ["<h1>YouTube 인기 영상</h1>",
             '<p class="meta">YouTube Data API 검색 결과입니다. 숫자는 YouTube가 준 값 그대로이며, '
             "다른 출처와 섞거나 점수를 매기지 않았습니다.</p>"]
    parts += [_youtube_section(q, rows) for q, rows in groups.items()]
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page("YouTube 인기 영상", "".join(parts)), encoding="utf-8")
    return out
