"""Local HTML galleries for `trends` and `youtube` (images are hot-linked, nothing is downloaded).

YouTube results are written to their OWN page, never next to community posts (Branding
Guidelines: no YouTube brand features beside possibly objectionable content), in the API's
order, with full titles, uncropped thumbnails, fetch time next to the numbers, "YouTube" shown
as the source of every item, and footer links to the YouTube Terms of Service and the Google
Privacy Policy (Developer Policies III.A, III.C.5, III.E.4.f, III.F.2).
"""
from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from processing.trends import Trend

YOUTUBE_TOS = "https://www.youtube.com/t/terms"
GOOGLE_PRIVACY = "http://www.google.com/policies/privacy"

_CSS = """
:root{--bg:#fafafa;--card:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e6;--accent:#c4302b}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1f1f23;--fg:#ececf0;--muted:#a0a0a8;--line:#33333a}}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,-apple-system,"Apple SD Gothic Neo","Malgun Gothic",sans-serif}
main{max-width:1100px;margin:0 auto}h1{font-size:22px}h2{font-size:18px;margin:28px 0 4px}h3{font-size:16px}
.meta{color:var(--muted);font-size:13px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:12px;margin-top:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.card img{width:100%;aspect-ratio:16/10;object-fit:cover;background:var(--line)}
.card img.whole{object-fit:contain;aspect-ratio:auto;min-width:120px;min-height:70px}
.card .body{padding:8px 10px}.card a{color:inherit;text-decoration:none}.card a:hover{text-decoration:underline}
.tag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:4px;border:1px solid var(--line);color:var(--muted)}
.yt{border-color:var(--accent);color:var(--accent);font-weight:600}
section.youtube{border-left:3px solid var(--accent);padding-left:12px;margin-top:14px}
footer{margin-top:32px;color:var(--muted);font-size:12px}footer a{color:inherit}
"""


def _page(title: str, body: str, footer: str = "") -> str:
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{escape(title)}</title><style>{_CSS}</style></head><body><main>{body}"
            f"{f'<footer>{footer}</footer>' if footer else ''}</main></body></html>")


def _safe_url(url: str | None) -> str | None:
    """Only http(s) links from collected data reach src/href (no javascript:, data:, ...)."""
    if url and url.strip().lower().startswith(("https://", "http://")):
        return escape(url.strip())
    return None


def _img(url: str | None, css_class: str = "") -> str:
    safe = _safe_url(url)
    if not safe:
        return ""
    cls = f' class="{css_class}"' if css_class else ""
    return f'<img src="{safe}"{cls} alt="" loading="lazy" referrerpolicy="no-referrer">'


def _youtube_section(query: str, rows: Sequence[Mapping]) -> str:
    header = (f'<h3>검색어: {escape(query)}</h3>'
              '<p class="meta">검색어는 이 도구가 정한 것입니다(설정의 키워드 또는 커뮤니티 트렌드 감지 결과이며, '
              "YouTube 데이터가 아닙니다). 아래 목록은 YouTube가 준 순서와 내용 그대로입니다. "
              "조회수순 검색 결과는 YouTube 안내대로 일부가 빠져 있을 수 있습니다.</p>")
    if not rows:
        return f'<section class="youtube">{header}<p class="meta">결과 없음</p></section>'
    cards = []
    for r in rows:
        url = f"https://www.youtube.com/watch?v={escape(r['video_id'])}"
        views = f"조회수 {r['view_count']:,}" if r["view_count"] is not None else "조회수 비공개"
        likes = f" · 좋아요 {r['like_count']:,}" if r["like_count"] is not None else ""
        cards.append(
            f'<div class="card"><a href="{url}" target="_blank" rel="noopener">{_img(r["thumbnail_url"], "whole")}</a>'
            f'<div class="body"><span class="tag yt">YouTube</span> '
            f'<a href="{url}" target="_blank" rel="noopener">{escape(r["title"])}</a>'
            f'<div class="meta">{escape(r["channel_title"] or "")} · 게시 {escape((r["published_at"] or "")[:10])}</div>'
            f'<div class="meta">{views}{likes} ({escape(r["fetched_at"])} 기준, YouTube 제공)</div>'
            f'<div class="meta"><a href="{url}" target="_blank" rel="noopener">YouTube에서 보기</a></div>'
            f"</div></div>")
    return f'<section class="youtube">{header}<div class="grid">{"".join(cards)}</div></section>'


def write_youtube_html(path: str | Path, groups: Mapping[str, Sequence[Mapping]]) -> Path:
    parts = ["<h1>영상 검색 결과</h1>",
             '<p class="meta">출처: YouTube (YouTube API Services). 숫자는 YouTube가 준 값 그대로이며, '
             "다른 출처와 섞거나 점수를 매기지 않았습니다. 이 파일은 보관 기한이 지나면 자동으로 삭제됩니다.</p>"]
    parts += [_youtube_section(q, rows) for q, rows in groups.items()]
    footer = (f'이 도구는 YouTube API Services를 사용합니다. 사용 시 <a href="{YOUTUBE_TOS}">YouTube 서비스 약관</a>에 '
              f'동의하는 것으로 간주됩니다. <a href="{GOOGLE_PRIVACY}">Google 개인정보처리방침</a>. '
              "썸네일은 브라우저가 YouTube/Google 서버(i.ytimg.com)에서 직접 불러옵니다. "
              "도구의 이용약관·개인정보 고지: docs/TERMS_AND_PRIVACY.md")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page("영상 검색 결과", "".join(parts), footer), encoding="utf-8")
    return out


def write_trends_html(path: str | Path, trends: Iterable[Trend], *, window_days: int,
                      youtube_page: str | None = None) -> Path:
    """Community-only page. When YouTube lookups were made they live in ``youtube_page``."""
    parts = [f"<h1>최근 {window_days}일 반복되는 밈·짤 문구</h1>",
             '<p class="meta">우리 커뮤니티 수집 데이터(루리웹·개드립·보배드림·Bluesky·Lemmy)의 글 제목에서 '
             "여러 번 반복된 문구입니다. 이미지는 원본 사이트에서 직접 불러옵니다.</p>"]
    if youtube_page:
        parts.append(f'<p><a href="{escape(youtube_page)}">이 문구들의 영상 검색 결과 보기 (별도 페이지) →</a></p>')
    for i, t in enumerate(trends, 1):
        burst = f"이전 기간 {t.baseline_docs}건 (x{t.ratio})" if t.ratio is not None else "이전 기록이 아직 부족해 급증 여부는 판단하지 않음"
        parts.append(f"<h2>{i}. {escape(t.phrase)}</h2>"
                     f'<p class="meta">{t.docs}개 글 · {escape(", ".join(t.sources))} · {burst}</p>')
        cards = []
        for e in t.examples:
            href = _safe_url(e.url) or "#"
            cards.append(f'<div class="card"><a href="{href}" target="_blank" rel="noopener">{_img(e.image)}</a>'
                         f'<div class="body"><span class="tag">{escape(e.source)}</span> '
                         f'<a href="{href}" target="_blank" rel="noopener">{escape(e.title)}</a>'
                         f'<div class="meta">추천 {e.likes if e.likes is not None else "-"}</div></div></div>')
        parts.append(f'<div class="grid">{"".join(cards)}</div>')
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page("밈·짤 트렌드", "".join(parts)), encoding="utf-8")
    return out
