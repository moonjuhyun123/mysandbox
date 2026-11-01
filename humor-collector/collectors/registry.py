"""Known sources: implemented adapters and sources deliberately left out (with the reason)."""
from __future__ import annotations

from dataclasses import dataclass

from collectors.base import BaseCollector
from collectors.bluesky import BlueskyCollector
from collectors.bobaedream import BobaedreamCollector
from collectors.dogdrip import DogdripCollector
from collectors.lemmy import LemmyCollector
from collectors.ruliweb import RuliwebCollector

COLLECTORS: dict[str, type[BaseCollector]] = {
    cls.name: cls
    for cls in (RuliwebCollector, BobaedreamCollector, DogdripCollector, LemmyCollector, BlueskyCollector)
}


@dataclass(frozen=True)
class UnsupportedSource:
    display_name: str
    reason: str
    checked: str
    revisit: str


# Observed from a cloud (US datacenter) IP with an honest User-Agent. Results from another
# network can differ; re-check before enabling anything here. Never add challenge bypasses.
UNSUPPORTED: dict[str, UnsupportedSource] = {
    "fmkorea": UnsupportedSource(
        "에펨코리아",
        "/best answers HTTP 430 '에펨코리아 보안 시스템' with a Cloudflare Turnstile CAPTCHA; "
        "robots.txt allows generic bots only on /, /best, /best2, /humor.",
        "2026-10-24",
        "Only if the list and post pages answer 200 without a challenge from your server.",
    ),
    "theqoo": UnsupportedSource(
        "더쿠",
        "/hot answers 403 '더쿠 - 보안 검사중' (CAPTCHA page); robots.txt Content-Signal ai-train=no.",
        "2026-10-24",
        "Only if pages answer 200 without a challenge.",
    ),
    "instiz": UnsupportedSource(
        "인스티즈",
        "Even robots.txt is behind a Cloudflare 'Just a moment...' challenge (403).",
        "2026-10-24",
        "Only if reachable without a challenge.",
    ),
    "natepann": UnsupportedSource(
        "네이트판",
        "robots.txt: 'User-agent: * Disallow: /' (only named search engines are allowed).",
        "2026-10-24",
        "Not without written permission from the operator.",
    ),
    "humoruniv": UnsupportedSource(
        "웃긴대학",
        "List pages redirect to a JavaScript cookie interstitial (msg.html?mode=1904); "
        "robots.txt disallows /board/best/ and /*.php.",
        "2026-10-24",
        "If list.html pages answer directly without the interstitial.",
    ),
    "dcinside": UnsupportedSource(
        "디시인사이드",
        "gall.dcinside.com returns an empty 200 body to a non-browser User-Agent; collecting would "
        "mean impersonating a browser. Python library dc-api (MIT) is unmaintained since 2021/2022.",
        "2026-10-24",
        "If the site serves an identified bot UA (robots.txt itself allows dcbest/hit).",
    ),
    "ppomppu": UnsupportedSource(
        "뽐뿌",
        "Humor board answered HTTP 403 to our User-Agent.",
        "2026-10-24",
        "If the board answers 200.",
    ),
    "9gag": UnsupportedSource(
        "9GAG",
        "No official API; pages, /v1 JSON and /rss all answer a Cloudflare challenge (403).",
        "2026-10-24",
        "Only with an official API.",
    ),
    "reddit": UnsupportedSource(
        "Reddit",
        "robots.txt disallows all crawling; the Data API needs an approved OAuth app (Responsible "
        "Builder Policy) and an account; secondary sources report RSS ending 2026-11-13 and public "
        "Data API access ending March 2027 (not verified on Reddit's own pages).",
        "2026-10-24",
        "Needs your Reddit account/app approval - not done without your consent.",
    ),
    "youtube": UnsupportedSource(
        "YouTube",
        "Not a collector: API policies forbid aggregating/merging YouTube data and deriving scores, and "
        "cap storage at 30 days. Use the lookup tool `main.py youtube ...` (needs YOUTUBE_API_KEY); "
        "results are kept apart and purged after 29 days. Channel RSS is robots-disallowed.",
        "2026-11-01",
        "Lookup only, with your API key.",
    ),
    "instagram": UnsupportedSource(
        "Instagram",
        "robots.txt 'User-agent: * Disallow: /' and Terms ban automated collection; Graph API needs your "
        "professional account + Meta app; hashtag top media needs App Review and Business Verification.",
        "2026-11-01",
        "Only Business Discovery of chosen creator accounts, with your accounts and consent.",
    ),
    "threads": UnsupportedSource(
        "Threads",
        "robots.txt disallows; keyword_search (TOP) needs threads_keyword_search App Review and gives no "
        "like counts.",
        "2026-11-01",
        "Only with a Meta app and App Review you approve.",
    ),
    "tiktok": UnsupportedSource(
        "TikTok",
        "Terms ban automated collection; Research API is for non-profit/academic researchers in eligible "
        "regions (not Korea); Display API returns only your own videos.",
        "2026-11-01",
        "No compliant path for others' popular videos.",
    ),
    "x": UnsupportedSource(
        "X (Twitter)",
        "robots.txt disallows all and the Terms ban crawling; API reads are pay-per-use "
        "($0.005 per post read).",
        "2026-11-01",
        "Only if you approve a paid developer account.",
    ),
    "naver": UnsupportedSource(
        "네이버 (카페·블로그·뉴스·웹툰)",
        "Cafe/blog/news-ranking/webtoon-comment pages are robots-disallowed and the Terms ban automated "
        "collection; the Search API has no popularity metric and limits storage and AI use.",
        "2026-11-01",
        "No compliant path for popular posts.",
    ),
    "todayhumor": UnsupportedSource(
        "오늘의유머",
        "Even robots.txt is behind a Cloudflare challenge.",
        "2026-11-01",
        "Only if reachable without a challenge.",
    ),
}


def build_collector(name: str, options: dict | None = None) -> BaseCollector:
    if name not in COLLECTORS:
        if name in UNSUPPORTED:
            raise KeyError(f"{name} is not supported: {UNSUPPORTED[name].reason}")
        raise KeyError(f"unknown source {name!r}; known: {', '.join(sorted(COLLECTORS))}")
    return COLLECTORS[name](options)
