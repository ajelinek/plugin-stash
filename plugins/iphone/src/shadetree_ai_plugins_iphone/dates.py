"""Shared date-argument parsing for the imessage and icallhistory servers.

Both servers accept the same `since`/`until` vocabulary (ISO 8601, 'today'/
'yesterday', or 'N days ago') so a caller scoping both to the same window gets
identical semantics. Apple-epoch conversions differ per database (chat.db uses
nanoseconds, CallHistory.storedata uses seconds) and stay in each server module.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

APPLE_EPOCH_OFFSET = 978307200  # 2001-01-01 00:00:00 UTC, in Unix epoch seconds

_RELATIVE_RE = re.compile(
    r"^\s*(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago\s*$", re.IGNORECASE
)
_UNIT_SECONDS = {
    "second": 1,
    "minute": 60,
    "hour": 3600,
    "day": 86400,
    "week": 86400 * 7,
    "month": 86400 * 30,
    "year": 86400 * 365,
}


def parse_when(value: str) -> datetime:
    """Parse a date argument. Accepts ISO 8601 ('2026-06-30', '2026-06-30T14:00:00'),
    the shorthands 'today'/'now'/'yesterday', or relative phrases like '7 days ago'."""
    text = value.strip()
    low = text.lower()

    if low == "now":
        return datetime.now().astimezone()
    if low == "today":
        return datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    if low == "yesterday":
        return (datetime.now().astimezone() - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    m = _RELATIVE_RE.match(text)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        return datetime.now().astimezone() - timedelta(seconds=n * _UNIT_SECONDS[unit])

    iso = text
    if iso.endswith("Z"):
        iso = iso[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(
            f"Could not parse date {value!r}. Use ISO 8601 (2026-06-30), "
            f"'today'/'yesterday', or 'N days ago'."
        ) from exc
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def _is_bare_day(value: str) -> bool:
    """True for date args that name a whole calendar day rather than an instant:
    'today', 'yesterday', or a bare ISO date with no time component."""
    low = value.strip().lower()
    if low in ("today", "yesterday"):
        return True
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()))


def until_boundary(value: str) -> datetime:
    """Parse an `until` argument, extending whole-day values to the end of that day
    so `until="2026-06-30"` includes all of June 30th instead of stopping at its
    midnight (parse_when's normal return for a bare day)."""
    dt = parse_when(value)
    if _is_bare_day(value):
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat(timespec="seconds") if dt else None
