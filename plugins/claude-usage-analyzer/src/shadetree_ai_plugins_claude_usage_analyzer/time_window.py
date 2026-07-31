"""Time-window scoping, shared by every data-pulling tool in this plugin.

An analysis run is almost never "everything, forever." The useful questions
are scoped: "how did I use Claude last quarter," "what changed since we set
this workspace up," "show me the last 90 days." So every tool that pulls
conversations or sessions accepts the same `since`/`until` pair, and every
one of them reports back what the window actually resolved to and how much
it excluded -- a filtered count presented as a total is how an analysis
quietly lies.

Two different timestamp shapes have to be normalized here, because the two
data sources disagree:

- **Local Desktop/Cowork sessions** carry `createdAt`/`lastActivityAt` as
  **epoch milliseconds, as integers** (confirmed directly on a real
  install).
- **The claude.ai account export** carries `created_at`/`updated_at` as
  **ISO 8601 strings**.

`to_epoch_ms` takes either. Everything downstream compares milliseconds.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

# A bound may be given relatively ("90d") instead of as a date, so a caller
# that isn't sure of today's date can still scope a run correctly. Month and
# year are deliberately approximate -- see `parse_bound`.
_RELATIVE_RE = re.compile(r"^\s*(\d+)\s*([dwmy])\s*$", re.IGNORECASE)
_UNIT_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}

# Below this, a numeric timestamp is epoch *seconds*, not milliseconds:
# 1e11 ms is 1973, while 1e11 s is the year 5138, so nothing real is
# ambiguous. Desktop writes milliseconds; this only guards a hand-passed
# value or a future format change.
_SECONDS_CUTOFF = 1e11


def to_epoch_ms(value: Any) -> float | None:
    """Normalize any timestamp shape this plugin encounters into epoch
    milliseconds. Returns None for anything unrecognized rather than
    raising -- a malformed timestamp on one session must never take down a
    whole inventory read."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) * 1000.0 if abs(value) < _SECONDS_CUTOFF else float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            numeric = float(text)
        except ValueError:
            pass
        else:
            return to_epoch_ms(numeric)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp() * 1000.0
    return None


def iso(value: Any) -> str | None:
    """Any timestamp shape -> a readable ISO 8601 UTC string, or None. Use
    this rather than formatting a raw field: the local ones are epoch
    milliseconds, and treating them as seconds silently yields 1970 dates
    that read as real, very stale data rather than as an error."""
    epoch_ms = to_epoch_ms(value)
    if epoch_ms is None:
        return None
    return datetime.fromtimestamp(epoch_ms / 1000.0, UTC).isoformat()


def _iso(epoch_ms: float | None) -> str | None:
    return iso(epoch_ms)


def parse_bound(value: Any, *, now_ms: float | None = None) -> tuple[float | None, str | None]:
    """Parse one window bound. Returns `(epoch_ms, error)` -- exactly one of
    which is None. Accepted forms:

    - `None` -- unbounded on that side.
    - A relative age: `"90d"`, `"12w"`, `"6m"`, `"2y"`. Resolved backwards
      from now. Months are 30 days and years are 365; these are for
      scoping a read, not for accounting, and the resolved absolute date is
      always reported back so the approximation is never hidden.
    - An ISO 8601 date or datetime: `"2026-01-01"`,
      `"2026-01-01T12:00:00Z"`. A bare date is midnight UTC.
    - Epoch milliseconds (or seconds) as a number.

    A relative form is the safer choice when the caller isn't certain of
    today's date -- which, in a long-running session, is a real risk."""
    if value is None:
        return None, None

    if isinstance(value, str):
        match = _RELATIVE_RE.match(value)
        if match:
            amount, unit = int(match.group(1)), match.group(2).lower()
            base = now_ms if now_ms is not None else datetime.now(UTC).timestamp() * 1000
            delta = timedelta(days=amount * _UNIT_DAYS[unit]).total_seconds() * 1000
            return base - delta, None

    epoch_ms = to_epoch_ms(value)
    if epoch_ms is None:
        return None, (
            f"Could not read {value!r} as a time bound. Use a relative age like "
            '"90d"/"6m", an ISO date like "2026-01-01", or epoch milliseconds.'
        )
    return epoch_ms, None


def resolve(since: Any = None, until: Any = None, *, now_ms: float | None = None) -> dict[str, Any]:
    """Resolve a `since`/`until` pair into the window every filter here
    uses. An unparseable or backwards bound is reported in `errors` and
    treated as unbounded rather than raising -- a bad bound should degrade
    to a wider read with a visible complaint, never a silent empty result
    that looks like "you have no data.\""""
    since_ms, since_err = parse_bound(since, now_ms=now_ms)
    until_ms, until_err = parse_bound(until, now_ms=now_ms)
    errors = [e for e in (since_err, until_err) if e]

    if since_ms is not None and until_ms is not None and since_ms > until_ms:
        errors.append(
            f"`since` ({_iso(since_ms)}) is after `until` ({_iso(until_ms)}) -- "
            "the window is empty, so it was ignored and the full history read instead."
        )
        since_ms = until_ms = None

    return {
        "since": _iso(since_ms),
        "until": _iso(until_ms),
        "since_ms": since_ms,
        "until_ms": until_ms,
        "bounded": since_ms is not None or until_ms is not None,
        "requested": {"since": since, "until": until},
        "errors": errors,
    }


UNBOUNDED = resolve()


def overlaps(window: dict[str, Any], start: Any = None, end: Any = None) -> bool:
    """Does an item spanning `start`..`end` fall inside `window`?

    This is interval *overlap*, not "created within" -- a chat started in
    January and still being worked in March belongs in a March window.
    `start` is typically `createdAt`, `end` typically `lastActivityAt`
    (or `updated_at` on the export side); either may be missing, in which
    case the other stands in for both.

    An item with no usable timestamp at all is **included**. Dropping data
    that merely failed to prove it belongs would understate the result;
    `apply` counts these separately so the caller can say so out loud."""
    if not window.get("bounded"):
        return True

    start_ms = to_epoch_ms(start)
    end_ms = to_epoch_ms(end)
    if start_ms is None and end_ms is None:
        return True
    lower = start_ms if start_ms is not None else end_ms
    upper = end_ms if end_ms is not None else start_ms

    since_ms, until_ms = window.get("since_ms"), window.get("until_ms")
    if since_ms is not None and upper < since_ms:
        return False
    return not (until_ms is not None and lower > until_ms)


def apply(
    window: dict[str, Any],
    items: list[Any],
    start_key: str,
    end_key: str,
) -> tuple[list[Any], dict[str, Any]]:
    """Filter `items` (dicts) to `window`, returning the survivors plus a
    reportable summary. Always call this rather than filtering inline: the
    summary is what stops a windowed count from being presented as a total.

    The returned summary carries the resolved window, `total_before`,
    `kept`, `excluded`, and `undated_kept` -- items with no usable
    timestamp that were kept because they couldn't be ruled out."""
    kept: list[Any] = []
    undated = 0
    for item in items:
        start = item.get(start_key) if isinstance(item, dict) else None
        end = item.get(end_key) if isinstance(item, dict) else None
        if not overlaps(window, start, end):
            continue
        if to_epoch_ms(start) is None and to_epoch_ms(end) is None:
            undated += 1
        kept.append(item)

    return kept, {
        "since": window.get("since"),
        "until": window.get("until"),
        "bounded": bool(window.get("bounded")),
        "requested": window.get("requested"),
        "errors": window.get("errors") or [],
        "total_before": len(items),
        "kept": len(kept),
        "excluded": len(items) - len(kept),
        "undated_kept": undated,
    }


def summary_notes(summary: dict[str, Any], noun: str) -> list[str]:
    """Plain-language caveats to repeat alongside any count derived from a
    windowed read. Empty when the read was unbounded and clean."""
    notes: list[str] = []
    notes.extend(summary.get("errors") or [])

    if summary.get("bounded") and summary.get("excluded"):
        span = " to ".join(
            p[:10] for p in (summary.get("since"), summary.get("until")) if p
        ) or "the requested window"
        notes.append(
            f"Time-scoped to {span}: {summary['kept']} of {summary['total_before']} "
            f"{noun} are in scope and {summary['excluded']} were excluded. Every count "
            "below describes the window only -- say so rather than presenting it as "
            "the account total."
        )
    if summary.get("undated_kept"):
        notes.append(
            f"{summary['undated_kept']} {noun} carry no usable timestamp and were kept "
            "rather than dropped, since they couldn't be ruled out of the window."
        )
    return notes
