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

import math
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
    whole inventory read.

    "Unrecognized" includes `NaN` and the infinities. Python's json module
    happily parses the non-standard `NaN`/`Infinity` literals, so a
    corrupted file really can put one here; letting it through would make
    `int()` raise downstream and would re-emit a token that a strict JSON
    parser on the other end of this tool call would reject."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
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
    that read as real, very stale data rather than as an error.

    The returned string always carries its `+00:00` offset, so no caller
    has to guess which zone it is in.

    A numerically valid epoch value can still be outside the range
    `datetime` can represent -- `1e18` milliseconds is roughly the year
    31,000,000, and a garbled field really does produce values like that.
    That raises `ValueError`/`OverflowError`/`OSError` depending on
    platform, so it is caught here and reported as "no usable timestamp",
    matching what this module promises everywhere else: malformed input
    degrades, it does not raise."""
    epoch_ms = to_epoch_ms(value)
    if epoch_ms is None:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000.0, UTC).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _iso(epoch_ms: float | None) -> str | None:
    return iso(epoch_ms)


# --------------------------------------------------------------------------------
# Local-time rendering
# --------------------------------------------------------------------------------
#
# UTC is the right storage and comparison form, and every `*_iso` value in
# this plugin stays UTC for that reason. It is the wrong *presentation*
# form: the audience here reads dates off a calendar in the timezone they
# live in, and "your heaviest day was Thursday" is simply false if Thursday
# was computed in UTC for someone six hours west of it.
#
# So anything a person will read as a date, a weekday, or a clock time gets
# a local companion built here. `datetime.fromtimestamp(...).astimezone()`
# resolves the offset that was in force *at that instant* from the OS
# timezone database, so a July sample keeps its summer-time offset even
# when the report is run in December -- which a single cached "current UTC
# offset" would get wrong for half the year.


def _local_datetime(value: Any) -> datetime | None:
    epoch_ms = to_epoch_ms(value)
    if epoch_ms is None:
        return None
    try:
        return datetime.fromtimestamp(epoch_ms / 1000.0).astimezone()
    except (ValueError, OverflowError, OSError):
        return None


def utc_offset_label(moment: datetime) -> str:
    """A UTC offset a non-technical reader can act on: `UTC-05:00`."""
    offset = moment.utcoffset() or timedelta(0)
    seconds = int(offset.total_seconds())
    sign = "-" if seconds < 0 else "+"
    seconds = abs(seconds)
    return f"UTC{sign}{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"


def local_iso(value: Any) -> str | None:
    """Any timestamp shape -> ISO 8601 in **this machine's** timezone, with
    the offset attached (`2026-08-08T07:15:00-05:00`), or None.

    Always returned *alongside* the UTC `iso()` form, never instead of it:
    the UTC value is what comparisons and joins use, and the local value is
    what a sentence shown to a person should be built from."""
    moment = _local_datetime(value)
    return moment.isoformat() if moment is not None else None


def local_day(value: Any) -> dict[str, Any] | None:
    """The calendar day one instant fell on **in this machine's timezone**,
    as `{"date", "weekday", "utc_offset"}`, or None when the timestamp is
    unusable.

    `utc_offset` travels with the day on purpose. A bare weekday with no
    zone attached is exactly the defect this exists to fix: it reads as a
    fact about the user's week while silently being a fact about UTC's."""
    moment = _local_datetime(value)
    if moment is None:
        return None
    return {
        "date": moment.strftime("%Y-%m-%d"),
        "weekday": moment.strftime("%A"),
        "utc_offset": utc_offset_label(moment),
    }


def local_timezone_label(value: Any = None) -> dict[str, Any]:
    """How to describe the zone every local field in a response is in.

    Anchored to a supplied instant rather than to "now" because the offset
    moves with daylight saving; pass the newest sample so the label matches
    the most recent thing being reported, and note that each timestamp
    carries its own offset for the rest."""
    moment = _local_datetime(value) or datetime.now().astimezone()
    return {
        "name": moment.tzname(),
        "utc_offset": utc_offset_label(moment),
        "source": "this machine's system timezone",
    }


# A bound written as a bare date -- or as a datetime with no offset --
# carries no timezone, so `parse_bound` anchors it at midnight **UTC**.
# Everything a person *reads* in this plugin is local (see `local_day`), so
# on a machine that isn't on UTC the two disagree by the size of the
# offset: "since 2026-08-01" actually begins at 7pm on July 31st for a
# reader six hours west, and the day buckets it selects are local days.
#
# The bound is deliberately left where it is -- midnight UTC is the
# documented, tested contract, and moving it would silently re-scope every
# existing caller and every saved analysis. What was wrong was that the
# mismatch was invisible, and that the note describing the window quoted a
# bare UTC-derived date with no zone marker at all. Both are now said out
# loud, with the local equivalent and the exact bound to pass instead.
_ZONELESS_SUGGESTION_TIME = "T00:00:00"


def _is_zoneless_bound(value: Any) -> bool:
    """True when a bound was given as a date or datetime string carrying no
    timezone, and was therefore anchored at UTC rather than at the reader's
    own midnight. A relative age ("90d") and an epoch number are not
    zone-less in this sense -- neither one names a calendar day."""
    if not isinstance(value, str) or _RELATIVE_RE.match(value):
        return False
    text = value.strip()
    if not text:
        return False
    try:
        float(text)
    except ValueError:
        pass
    else:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is None


def _zoneless_bound_note(name: str, raw: Any, epoch_ms: float | None) -> str | None:
    """The caveat for one zone-less bound, or None when there is nothing to
    say -- the bound named a zone, wasn't a date at all, or this machine is
    on UTC, in which case the two readings coincide and a warning would be
    noise."""
    if epoch_ms is None or not _is_zoneless_bound(raw):
        return None
    moment = _local_datetime(epoch_ms)
    if moment is None:
        return None
    offset = utc_offset_label(moment)
    if offset == "UTC+00:00":
        return None
    text = str(raw).strip()
    suggestion = (
        f"{text}{_ZONELESS_SUGGESTION_TIME}{offset[3:]}"
        if len(text) == 10
        else f"{text}{offset[3:]}"
    )
    return (
        f'`{name}` was given as "{text}", which names no timezone, so it was anchored '
        f"at midnight UTC -- {moment.isoformat()} on this machine's clock ({offset}). "
        "Every date, weekday, and day bucket a person reads elsewhere is a local one, "
        "so this window edge falls part-way through a local day rather than on its "
        f'boundary. Pass "{suggestion}" instead to line the window up with the '
        "reader's own calendar, and don't describe the bound as a plain date without "
        "saying which zone it is in."
    )


def parse_bound(value: Any, *, now_ms: float | None = None) -> tuple[float | None, str | None]:
    """Parse one window bound. Returns `(epoch_ms, error)` -- exactly one of
    which is None. Accepted forms:

    - `None` -- unbounded on that side.
    - A relative age: `"90d"`, `"12w"`, `"6m"`, `"2y"`. Resolved backwards
      from now. Months are 30 days and years are 365; these are for
      scoping a read, not for accounting, and the resolved absolute date is
      always reported back so the approximation is never hidden.
    - An ISO 8601 date or datetime: `"2026-01-01"`,
      `"2026-01-01T12:00:00Z"`. A bare date -- or any datetime written
      without an offset -- is midnight **UTC**, not local midnight. On a
      machine that isn't on UTC that edge lands part-way through a local
      day, while every day bucket and weekday this plugin shows a person is
      local; `resolve` reports that mismatch in `boundary_notes` and says
      which bound to pass instead. Give the bound an explicit offset
      (`"2026-01-01T00:00:00-05:00"`) when the window has to line up with
      the reader's own calendar days.
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
    that looks like "you have no data."

    Each bound comes back twice, the same way every other timestamp in this
    plugin does: `since`/`until` in UTC (what the filtering compares
    against) and `since_local`/`until_local` on this machine's clock (what
    a sentence shown to a person should quote), with `timezone` naming the
    zone. `boundary_notes` carries the caveat for a bound that named no
    timezone at all -- see `_zoneless_bound_note`; `summary_notes` repeats
    it, so a caller that already reports notes needs no extra wiring."""
    since_ms, since_err = parse_bound(since, now_ms=now_ms)
    until_ms, until_err = parse_bound(until, now_ms=now_ms)
    errors = [e for e in (since_err, until_err) if e]

    if since_ms is not None and until_ms is not None and since_ms > until_ms:
        errors.append(
            f"`since` ({_iso(since_ms)}) is after `until` ({_iso(until_ms)}) -- "
            "the window is empty, so it was ignored and the full history read instead."
        )
        since_ms = until_ms = None

    # Both renderings of each bound, same convention as every other
    # timestamp in this plugin: `since`/`until` are UTC and are what the
    # filtering actually compares against; `since_local`/`until_local` are
    # the same instants on this machine's clock, and are what a sentence
    # shown to a person should be built from.
    return {
        "since": _iso(since_ms),
        "until": _iso(until_ms),
        "since_local": local_iso(since_ms),
        "until_local": local_iso(until_ms),
        "since_ms": since_ms,
        "until_ms": until_ms,
        "bounded": since_ms is not None or until_ms is not None,
        "timezone": local_timezone_label(
            since_ms if since_ms is not None else until_ms
        ),
        "requested": {"since": since, "until": until},
        "errors": errors,
        # A zone-less bound is anchored at UTC while the days it selects are
        # local -- see `_zoneless_bound_note`. Empty when there is nothing
        # to disclose.
        "boundary_notes": [
            note
            for note in (
                _zoneless_bound_note("since", since, since_ms),
                _zoneless_bound_note("until", until, until_ms),
            )
            if note
        ],
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
        "since_local": window.get("since_local"),
        "until_local": window.get("until_local"),
        "timezone": window.get("timezone"),
        "bounded": bool(window.get("bounded")),
        "requested": window.get("requested"),
        "errors": window.get("errors") or [],
        "boundary_notes": window.get("boundary_notes") or [],
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
    notes.extend(summary.get("boundary_notes") or [])

    if summary.get("bounded") and summary.get("excluded"):
        bounds = [p[:10] for p in (summary.get("since"), summary.get("until")) if p]
        span = f"{' to '.join(bounds)} UTC" if bounds else "the requested window"
        notes.append(
            f"Time-scoped to {span}: {summary['kept']} of {summary['total_before']} "
            f"{noun} are in scope and {summary['excluded']} were excluded. Those bounds "
            "are UTC instants, and they are marked UTC on purpose: every date and "
            "weekday elsewhere in this response is a local one, so a day on the edge of "
            "the window can fall either side of them. Every count below describes the "
            "window only -- say so rather than presenting it as the account total."
        )
    if summary.get("undated_kept"):
        notes.append(
            f"{summary['undated_kept']} {noun} carry no usable timestamp and were kept "
            "rather than dropped, since they couldn't be ruled out of the window."
        )
    return notes
