"""Parsing of the date/time strings Korean community boards display."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")

_FULL = re.compile(
    r"(?P<y>\d{4})[.\-/]\s*(?P<mo>\d{1,2})[.\-/]\s*(?P<d>\d{1,2})"
    r"(?:\s*\((?:[월화수목금토일]|[A-Za-z]{2,3})\))?"         # optional weekday "(토)"
    r"(?:\s*\(?\s*(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?\)?)?"
)
_SHORT_YEAR = re.compile(r"^(?P<y>\d{2})\.(?P<mo>\d{1,2})\.(?P<d>\d{1,2})$")      # 26.10.20
_MONTH_DAY = re.compile(r"^(?P<mo>\d{1,2})[/.](?P<d>\d{1,2})$")                   # 10/20
_TIME_ONLY = re.compile(r"^(?P<h>\d{1,2}):(?P<mi>\d{2})(?::(?P<s>\d{2}))?$")      # 10:37
_RELATIVE = re.compile(r"^(?P<n>\d+)\s*(?P<unit>초|분|시간|일|주|개월|달|년)\s*전$")


def parse_full_datetime(text: str, tz: timezone = KST) -> datetime | None:
    """'2026.10.24 (09:14:22)', '2026.10.24 (토) 10:36', '2026-10-24 10:36' -> aware datetime."""
    m = _FULL.search(text or "")
    if not m:
        return None
    return datetime(
        int(m["y"]), int(m["mo"]), int(m["d"]),
        int(m["h"] or 0), int(m["mi"] or 0), int(m["s"] or 0), tzinfo=tz,
    )


def parse_list_time(text: str, now: datetime, tz: timezone = KST) -> tuple[datetime | None, bool]:
    """Parse the short time shown in list rows.

    Returns (datetime, approx). ``approx`` is True when the value is not exact to the minute
    (relative times, date-only values). ``now`` must be timezone-aware.
    """
    s = (text or "").strip()
    if not s:
        return None, False
    local_now = now.astimezone(tz)
    if s in ("방금 전", "방금"):
        return local_now.replace(second=0, microsecond=0), True
    m = _RELATIVE.match(s)
    if m:
        n = int(m["n"])
        unit = m["unit"]
        delta = {
            "초": timedelta(seconds=n), "분": timedelta(minutes=n), "시간": timedelta(hours=n),
            "일": timedelta(days=n), "주": timedelta(weeks=n), "개월": timedelta(days=30 * n),
            "달": timedelta(days=30 * n), "년": timedelta(days=365 * n),
        }[unit]
        return (local_now - delta).replace(second=0, microsecond=0), True
    m = _TIME_ONLY.match(s)
    if m:
        dt = local_now.replace(hour=int(m["h"]), minute=int(m["mi"]), second=int(m["s"] or 0), microsecond=0)
        if dt > local_now + timedelta(minutes=5):        # "23:50" seen at 00:10 -> yesterday
            dt -= timedelta(days=1)
        return dt, False
    m = _SHORT_YEAR.match(s)
    if m:
        return datetime(2000 + int(m["y"]), int(m["mo"]), int(m["d"]), tzinfo=tz), True
    m = _MONTH_DAY.match(s)
    if m:
        dt = datetime(local_now.year, int(m["mo"]), int(m["d"]), tzinfo=tz)
        if dt > local_now + timedelta(days=1):           # December post seen in January
            dt = dt.replace(year=dt.year - 1)
        return dt, True
    full = parse_full_datetime(s, tz)
    if full:
        return full, False
    return None, False


def parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    text = text.strip().replace("Z", "+00:00")
    # Python 3.10's fromisoformat only accepts 3 or 6 fractional digits: pad/trim to 6.
    text = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], text, count=1)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
