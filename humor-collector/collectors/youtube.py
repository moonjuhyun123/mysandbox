"""YouTube lookup through the official YouTube Data API v3 (owner's API key).

This is deliberately NOT a collector feeding the community database. YouTube API Services
Developer Policies (last updated 2026-10-15) restrict what may be done with API data:
- III.E.2: do not aggregate API data or combine data from different content owners to gain
  insights -> YouTube results are never used for trend detection or cross-source rankings;
- III.E.4.h: no new or derived data/metrics (e.g. no score from likes/views) -> raw numbers only,
  shown exactly as returned, in the API's own order;
- III.E.4.d: data fetched with only an API key may be stored temporarily, at most 30 days ->
  rows are kept in their own table and purged after ``retention_days`` (default 29);
- III.C.5 / III.F.2: do not merge YouTube results with other sources; label YouTube as source.
What it does: search popular recent videos for keywords (e.g. meme names found by
``main.py trends``), list today's chart, and show a video's top comments (display only, no
author names). Patterns are spotted by the person looking at the results.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from collectors.http import FetchError, PoliteHttpClient

API = "https://www.googleapis.com/youtube/v3"
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "userRateLimitExceeded"}


class YouTubeError(Exception):
    def __init__(self, message: str, reason: str | None = None):
        super().__init__(message)
        self.reason = reason


class QuotaExceeded(YouTubeError):
    """Daily quota used up (or rate-limited): stop calling until it resets (midnight Pacific)."""


@dataclass
class YouTubeVideo:
    video_id: str
    title: str
    channel_title: str | None
    published_at: str | None
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    duration: str | None
    thumbnail_url: str | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass
class YouTubeComment:
    text: str
    like_count: int | None
    published_at: str | None
    reply_count: int | None


def api_key_from_env(var: str = "YOUTUBE_API_KEY") -> str:
    key = os.environ.get(var, "").strip()
    if not key:
        raise YouTubeError(
            f"no YouTube API key: set the {var} environment variable (Google Cloud console -> "
            "enable 'YouTube Data API v3' -> Credentials -> API key)", reason="noKey")
    return key


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _thumbnail(snippet: dict) -> str | None:
    thumbs = snippet.get("thumbnails") or {}
    for size in ("high", "medium", "default"):
        if thumbs.get(size, {}).get("url"):
            return thumbs[size]["url"]
    return None


class YouTubeAPI:
    def __init__(self, http: PoliteHttpClient, api_key: str):
        self.http = http
        self.api_key = api_key
        http.secrets.add(api_key)            # never written to logs or the error table
        self.calls: dict[str, int] = {}

    # ------------------------------------------------------------------ calls
    def search_video_ids(self, query: str, *, region: str = "KR", language: str = "ko", days: int = 7,
                         short_only: bool = True, max_results: int = 25, now: datetime | None = None) -> list[str]:
        """Most-viewed recent videos for a query, in YouTube's order (search.list, 1 search call)."""
        now = now or datetime.now(timezone.utc)
        params = {
            "part": "snippet", "type": "video", "q": query, "order": "viewCount",
            "regionCode": region, "relevanceLanguage": language, "safeSearch": "moderate",
            "publishedAfter": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "maxResults": min(max(max_results, 1), 50),
        }
        if short_only:
            params["videoDuration"] = "short"          # under 4 minutes (includes Shorts)
        data = self._get("search", params)
        return [item["id"]["videoId"] for item in data.get("items", []) if item.get("id", {}).get("videoId")]

    def videos(self, video_ids: list[str]) -> list[YouTubeVideo]:
        """Title and raw statistics for up to 50 ids per call, kept in the given order."""
        found: dict[str, YouTubeVideo] = {}
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i : i + 50]
            data = self._get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(batch),
                                        "maxResults": 50})
            for item in data.get("items", []):
                found[item["id"]] = self._video(item)
        return [found[v] for v in video_ids if v in found]

    def most_popular(self, *, region: str = "KR", category_id: str | None = None,
                     max_results: int = 25) -> list[YouTubeVideo]:
        params = {"part": "snippet,statistics,contentDetails", "chart": "mostPopular", "regionCode": region,
                  "maxResults": min(max(max_results, 1), 50)}
        if category_id:
            params["videoCategoryId"] = category_id
        return [self._video(item) for item in self._get("videos", params).get("items", [])]

    def top_comments(self, video_id: str, max_results: int = 20) -> list[YouTubeComment]:
        """Most relevant top-level comments; author names are dropped (YouTube ToS: no harvesting of
        information that identifies a person)."""
        data = self._get("commentThreads", {"part": "snippet", "videoId": video_id, "order": "relevance",
                                            "textFormat": "plainText", "maxResults": min(max(max_results, 1), 100)})
        out = []
        for item in data.get("items", []):
            snip = item.get("snippet", {})
            top = (snip.get("topLevelComment") or {}).get("snippet", {})
            out.append(YouTubeComment(text=top.get("textOriginal") or top.get("textDisplay") or "",
                                      like_count=_int(top.get("likeCount")), published_at=top.get("publishedAt"),
                                      reply_count=_int(snip.get("totalReplyCount"))))
        return out

    # -------------------------------------------------------------- internals
    @staticmethod
    def _video(item: dict) -> YouTubeVideo:
        snip, stats, details = item.get("snippet", {}), item.get("statistics", {}), item.get("contentDetails", {})
        return YouTubeVideo(
            video_id=item["id"], title=snip.get("title", ""), channel_title=snip.get("channelTitle"),
            published_at=snip.get("publishedAt"), view_count=_int(stats.get("viewCount")),
            like_count=_int(stats.get("likeCount")), comment_count=_int(stats.get("commentCount")),
            duration=details.get("duration"), thumbnail_url=_thumbnail(snip),
        )

    def _get(self, method: str, params: dict) -> dict:
        self.calls[method] = self.calls.get(method, 0) + 1
        try:
            resp = self.http.get(f"{API}/{method}", params={**params, "key": self.api_key},
                                 accept="application/json")
        except FetchError as exc:
            raise self._error(exc) from None
        try:
            return json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise YouTubeError(f"invalid JSON from {method}: {exc}") from None

    def _error(self, exc: FetchError) -> YouTubeError:
        reason, message = None, str(exc)
        try:
            err = json.loads(exc.body or "")["error"]
            message = err.get("message") or message
            reason = (err.get("errors") or [{}])[0].get("reason")
        except (ValueError, KeyError, TypeError, IndexError):
            pass
        message = self.http.scrub(f"YouTube API error ({exc.status}, {reason}): {message}")
        if reason in QUOTA_REASONS:
            return QuotaExceeded(message, reason)
        return YouTubeError(message, reason)
