"""Bluesky - Korean popularity feeds via the official public AppView API (no account, no key).

Access notes (checked 2026-11-01):
- ``https://public.api.bsky.app/robots.txt`` allows crawling the public API.
- ``app.bsky.feed.getFeed`` on community feed generators returns posts with full text, image
  links and like/reply/repost/quote counts. Used by default:
  * "한국어/한글 30 리포스트 이상 잼얘 모음" - Korean posts of the last 7 days with 30+ reposts
  * "한국어 리포스트 많은 순" - Korean posts of the last 3 days by repost count
  ("하루치 밀린 블스 따라잡기" was checked and rejected: almost all of it is adult content.)
- About half of the authors in these feeds carry the ``!no-unauthenticated`` self-label (they
  opted out of logged-out viewing); the public API still returns them, so we drop them here.
  Posts or authors with adult labels are dropped as well.
- Bluesky's developer guidelines require honouring deletions: ``refresh --source bluesky``
  re-reads stored posts with ``app.bsky.feed.getPosts`` and deletes the ones that are gone or
  whose author opted out since.
- Authors can publish ``app.bsky.actor.contentVisibilityDeclaration`` (record "self") with
  ``hideFromAlgorithmicRecommendations: true``; Bluesky's 2026-09-01 post asks "funniest posts"
  aggregators to check it. It is not part of feed views, so before storing a new author's post we
  resolve the author's DID document (plc.directory or did:web) and read the record from the
  author's own PDS with ``com.atproto.repo.getRecord`` ("RecordNotFound" = not set, per the
  lexicon). Authors who set it are skipped; when the check is inconclusive the post is not stored
  this run; refresh re-checks stored posts. Verified live 2026-11-01: an author with the
  declaration set was found on their PDS while the public AppView answered "RecordNotFound" for
  the same record - the AppView is not a reliable source for it.
- Author handles and display names are not stored (the post URL contains the author's DID).
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping
from ipaddress import ip_address
from urllib.parse import quote, urlencode, urlsplit

from collectors.base import BaseCollector, Listing, ParseError, ParseResult, parse_rows
from collectors.timeutil import parse_iso
from models.post import ListingEntry, Metrics

API = "https://public.api.bsky.app/xrpc"
DEFAULT_FEEDS: dict[str, tuple[str, str]] = {
    "jaem_30_reposts": (
        "at://did:plc:plqo3qfexrv2vwlbnxobff7c/app.bsky.feed.generator/aaaju46ivv6sg",
        "한국어/한글 30 리포스트 이상 잼얘 모음",
    ),
    "ko_most_reposted": (
        "at://did:plc:vt44edfzaat5jbqhfhjusqwt/app.bsky.feed.generator/aaahkq4fquf34",
        "한국어 리포스트 많은 순",
    ),
}
OPT_OUT_LABELS = {"!no-unauthenticated"}
ADULT_LABELS = {"porn", "sexual", "nudity", "graphic-media", "gore", "corpse", "!hide"}
GET_POSTS_BATCH = 25
VISIBILITY_COLLECTION = "app.bsky.actor.contentVisibilityDeclaration"
PLC_DIRECTORY = "https://plc.directory"


def _label_values(labels: Iterable[Mapping] | None) -> set[str]:
    return {lab.get("val", "") for lab in labels or [] if not lab.get("neg")}


def _split_uri(uri: str) -> tuple[str, str]:
    """at://did:plc:xyz/app.bsky.feed.post/3abc -> ("did:plc:xyz", "3abc")"""
    if not uri.startswith("at://") or "/app.bsky.feed.post/" not in uri:
        raise ParseError(f"not a post uri: {uri!r}")
    did, rkey = uri[5:].split("/app.bsky.feed.post/", 1)
    return did, rkey


def did_document_url(did: str) -> str | None:
    if did.startswith("did:plc:") and did[8:].isalnum():
        return f"{PLC_DIRECTORY}/{did}"
    if did.startswith("did:web:"):
        host = did[8:]
        if host and ":" not in host and "/" not in host and _public_host(host):
            return f"https://{host}/.well-known/did.json"
    return None


def _public_host(host: str) -> bool:
    """Refuse IP literals and local names: a DID document is untrusted input (no SSRF)."""
    host = host.strip("[]").lower()
    try:
        ip_address(host)
        return False
    except ValueError:
        pass
    return "." in host and not host.endswith((".local", ".localhost", ".internal", ".lan")) and host != "localhost"


def pds_endpoint(doc_text: str) -> str | None:
    """https://host of the account's PDS from a DID document, or None."""
    try:
        doc = json.loads(doc_text)
    except ValueError:
        return None
    for svc in doc.get("service", []) if isinstance(doc, dict) else []:
        if str(svc.get("id", "")).endswith("#atproto_pds") and svc.get("type") == "AtprotoPersonalDataServer":
            endpoint = svc.get("serviceEndpoint")
            if isinstance(endpoint, str) and endpoint.startswith("https://"):
                host = urlsplit(endpoint).netloc
                if host and "@" not in host and _public_host(host.split(":")[0]):
                    return f"https://{host}"
    return None


def _ok_only(status: int | None, text: str) -> bool:
    return status == 200


def _definitive_record(status: int | None, text: str) -> bool:
    """Only answers parse_visibility can decide on may be cached."""
    return parse_visibility(status, text) is not None


def parse_visibility(status: int | None, text: str) -> bool | None:
    """True/False for hideFromAlgorithmicRecommendations; None when it could not be determined."""
    if status == 200:
        try:
            return bool(json.loads(text)["value"].get("hideFromAlgorithmicRecommendations", False))
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
    if status == 400 and "RecordNotFound" in text:
        return False                       # lexicon: "Consumers must treat a missing record as false"
    return None


def _media(embed: Mapping | None) -> tuple[list[str], dict[str, Any]]:
    """Image/video links of an embed view, plus link-card / quoted-post context."""
    urls: list[str] = []
    extra: dict[str, Any] = {}
    if not embed:
        return urls, extra
    kind = embed.get("$type", "")
    if kind.startswith("app.bsky.embed.images"):
        urls += [img["fullsize"] for img in embed.get("images", []) if img.get("fullsize")]
    elif kind.startswith("app.bsky.embed.gallery"):
        urls += [img["fullsize"] for img in embed.get("items", []) if img.get("fullsize")]
    elif kind.startswith("app.bsky.embed.video"):
        if embed.get("thumbnail"):
            urls.append(embed["thumbnail"])
    elif kind.startswith("app.bsky.embed.external"):
        ext = embed.get("external") or {}
        extra["link"] = {"url": ext.get("uri"), "title": ext.get("title")}
    elif kind.startswith("app.bsky.embed.recordWithMedia"):
        media_urls, media_extra = _media(embed.get("media"))
        urls += media_urls
        extra.update(media_extra)
        extra["quotes_a_post"] = True
    elif kind.startswith("app.bsky.embed.record"):
        # the quoted post belongs to someone else, whose opt-out / labels / visibility declaration
        # we have not checked: keep only the fact that it is a quote, never the quoted text
        extra["quotes_a_post"] = True
    return urls, extra


class BlueskyCollector(BaseCollector):
    name = "bluesky"
    display_name = "Bluesky"
    homepage = "https://bsky.app"
    method = "official-api"
    needs_detail = False
    list_has_body = True
    batch_refresh = True

    @property
    def page_size(self) -> int:
        return min(int(self.options.get("page_size", 50)), 100)

    def feeds(self) -> dict[str, tuple[str, str]]:
        chosen = self.options.get("feeds", list(DEFAULT_FEEDS))
        feeds = {k: DEFAULT_FEEDS[k] for k in chosen if k in DEFAULT_FEEDS}
        for custom in self.options.get("extra_feeds", []):
            feeds[custom["name"]] = (custom["uri"], custom.get("label", custom["name"]))
        return feeds

    def default_listings(self) -> list[Listing]:
        return [
            Listing(name=key, kind="recent", label=label,
                    url=f"{API}/app.bsky.feed.getFeed?" + urlencode({"feed": uri, "limit": self.page_size}),
                    max_pages=1, ranked=True)
            for key, (uri, label) in self.feeds().items()
        ]

    def listings(self, kind: str | None = None) -> list[Listing]:
        # feeds page with an opaque cursor, not page numbers: one page per run
        return [replace(lst, max_pages=1) for lst in super().listings(kind)]

    def listing_url(self, listing: Listing, page: int) -> str:
        return listing.url

    # ------------------------------------------------------------------ list
    def parse_listing(self, text: str, listing: Listing, now: datetime, page: int = 1) -> ParseResult:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParseError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict) or "feed" not in data:
            raise ParseError("response has no 'feed' field")
        items = list(enumerate(data["feed"], start=1))
        return parse_rows(items, lambda item: self._entry(item[1]["post"], listing.label, item[0]))

    def _entry(self, post: Mapping, feed_label: str | None, rank: int | None) -> ListingEntry:
        did, rkey = _split_uri(post["uri"])
        record = post.get("record") or {}
        text = (record.get("text") or "").strip()
        first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
        media, extra = _media(post.get("embed"))
        author_labels = _label_values((post.get("author") or {}).get("labels"))
        post_labels = _label_values(post.get("labels"))
        skip = None
        if author_labels & OPT_OUT_LABELS:
            skip = "author opted out of logged-out viewing (!no-unauthenticated)"
        elif (author_labels | post_labels) & ADULT_LABELS:
            skip = "adult label: " + ",".join(sorted((author_labels | post_labels) & ADULT_LABELS))
        langs = record.get("langs") or []
        extra.update({
            "at_uri": post["uri"],
            "reposts": post.get("repostCount"),
            "quotes": post.get("quoteCount"),
            "langs": langs,
            "skip_reason": skip,
        })
        return ListingEntry(
            source_post_id=f"{did}/{rkey}",
            url=f"https://bsky.app/profile/{did}/post/{rkey}",
            title=(first_line[:80] or "(텍스트 없음)"),
            body=text,
            media_urls=media,
            metrics=Metrics(likes=post.get("likeCount"), comments_count=post.get("replyCount")),
            ranking=rank,
            category=feed_label,
            published_at=parse_iso(record.get("createdAt")) or parse_iso(post.get("indexedAt")),
            extra=extra,
        )

    def pre_store_check(self, entry: ListingEntry, fetch: Callable[..., tuple[int | None, str]]) -> str | None:
        if not self.options.get("honor_visibility_declaration", True):
            return None
        did = self.author_key(entry)
        cache = self.__dict__.setdefault("_hide_from_recs", {})
        if did not in cache:
            cache[did] = self._read_declaration(did, fetch)
        hide = cache[did]
        if hide is None:
            return "visibility declaration could not be checked"
        if hide:
            return "author asked to be hidden from algorithmic recommendations"
        return None

    def author_key(self, entry: ListingEntry) -> str:
        return entry.source_post_id.split("/", 1)[0]

    @staticmethod
    def _read_declaration(did: str, fetch: Callable[..., tuple[int | None, str]]) -> bool | None:
        doc_url = did_document_url(did)
        if doc_url is None:
            return None
        status, text = fetch(doc_url, _ok_only)
        pds = pds_endpoint(text) if status == 200 else None
        if pds is None:
            return None
        query = urlencode({"repo": did, "collection": VISIBILITY_COLLECTION, "rkey": "self"})
        return parse_visibility(*fetch(f"{pds}/xrpc/com.atproto.repo.getRecord?{query}", _definitive_record))

    def accept(self, entry: ListingEntry) -> bool:
        if entry.extra.get("skip_reason"):
            return False
        wanted = self.options.get("languages", ["ko"])
        langs = entry.extra.get("langs") or []
        if wanted and langs and not set(langs) & set(wanted):
            return False
        return super().accept(entry)

    # --------------------------------------------------------------- refresh
    def refresh_urls(self, source_post_ids: list[str]) -> list[tuple[str, list[str]]]:
        """getPosts requests (<= 25 posts each) for stored posts, with the ids they cover."""
        out = []
        for i in range(0, len(source_post_ids), GET_POSTS_BATCH):
            batch = source_post_ids[i : i + GET_POSTS_BATCH]
            query = "&".join("uris=" + quote(self._at_uri(pid), safe="") for pid in batch)
            out.append((f"{API}/app.bsky.feed.getPosts?{query}", batch))
        return out

    def parse_refresh(self, text: str) -> dict[str, ListingEntry]:
        """Posts still visible, keyed by source_post_id. Posts missing from the answer are gone."""
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ParseError(f"invalid JSON: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("posts"), list):
            raise ParseError("getPosts answer has no 'posts' list")      # never read as "all deleted"
        found = {}
        for post in data["posts"]:
            entry = self._entry(post, None, None)
            found[entry.source_post_id] = entry
        return found

    @staticmethod
    def _at_uri(source_post_id: str) -> str:
        did, rkey = source_post_id.split("/", 1)
        return f"at://{did}/app.bsky.feed.post/{rkey}"
