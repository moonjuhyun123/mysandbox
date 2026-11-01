"""URL and text normalization used for de-duplication."""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Query parameters that never identify a post (navigation, tracking, view mode).
NOISE_PARAMS = {
    "page", "m", "t", "vdate", "cmt", "sort_index", "order_type", "listStyle", "list_type",
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "gclid",
    "ref", "from", "od", "po", "category", "groupCd",
}
HOST_PREFIXES = ("www.", "m.", "mobile.")


def normalize_url(url: str) -> str:
    """Canonical form for duplicate detection: scheme-less, lowercase host without www./m.,
    no fragment, no navigation/tracking params, remaining params sorted, no trailing slash."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    for prefix in HOST_PREFIXES:
        if host.startswith(prefix):
            host = host[len(prefix):]
            break
    if host.endswith(":80") or host.endswith(":443"):
        host = host.rsplit(":", 1)[0]
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if k not in NOISE_PARAMS
    )
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit(("", host, path, urlencode(query), "")).lstrip("/")


# Prefixes like "ai)", "롤)", "[유머]", "(펌)" and suffixes like ".jpg", "ㅋㅋㅋ" carry no identity.
_TAG_PREFIX = re.compile(r"^\s*(?:\[[^\]]{1,10}\]|\([^)]{1,10}\)|[^\s)]{1,6}\))\s*")
_FILE_SUFFIX = re.compile(r"\.(?:jpe?g|png|gif|webp|mp4|avi|manhwa|txt|swf)\b", re.IGNORECASE)
_LAUGH = re.compile(r"[ㅋㅎㄷㅠㅜ]{2,}")
_NON_WORD = re.compile(r"[^0-9a-z가-힣]+")


def normalize_text(text: str | None) -> str:
    """Lowercase, NFKC, keep only letters/digits (Korean + latin)."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).lower()
    return _NON_WORD.sub("", text)


def clean_title(title: str | None) -> str:
    """NFKC title without board tags ("[펌]", "롤)"), file suffixes (".jpg") and laughter ("ㅋㅋ"),
    spaces kept so word boundaries survive."""
    if not title:
        return ""
    t = unicodedata.normalize("NFKC", title)
    for _ in range(2):                      # "[펌] 롤) 제목" has two tags
        t = _TAG_PREFIX.sub("", t)
    t = _FILE_SUFFIX.sub(" ", t)
    t = _LAUGH.sub(" ", t)
    return t.strip()


def normalize_title(title: str | None) -> str:
    return normalize_text(clean_title(title))


def shingles(text: str, k: int = 3) -> set[str]:
    if len(text) < k:
        return {text} if text else set()
    return {text[i : i + k] for i in range(len(text) - k + 1)}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def clean_body_text(text: str) -> str:
    """Collapse whitespace inside lines, drop empty lines."""
    lines = [re.sub(r"[ \t ​]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)
