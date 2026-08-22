#!/usr/bin/env python3
"""
normalize_transcript.py -- Detect the format of a raw meeting transcript and
normalize it into one JSON object on stdout: a flat list of {source, speaker,
timestamp_sec, end_sec, text} segments plus stats (word count, speaker list,
meeting duration, a recommended recap length/structure tier, the links and
images shared during the meeting, and warnings about thin, unstructured, or
multi-session input).

Optionally takes a second input -- the meeting's chat log (`--chat`) -- and
merges it into the *same* chronologically ordered segments list, so a message
typed at 14:03 lands next to what was being said at 14:03. Every segment
carries a `source` field ("transcript" or "chat") so the two never blur
together: the recap can tell a spoken commitment from a typed one, and can
tag anything that didn't come from the transcript.

Crucially, chat is merged for *ordering and context only*. Every statistic --
word count, speaker list, start/end/duration, session breaks, day markers,
and the recommended tier -- is computed from transcript segments alone. A
chatty meeting must not get bumped into a longer recap tier just because
people typed a lot, so that purity is enforced here in code rather than left
as a rule for a reader to remember.

Shared links and images are the deliberate exception, and not a hole in that
rule: they're collected from transcript *and* chat, because chat is where
people actually paste a doc link or drop a screenshot -- ignoring it would
miss most of them. They are artifacts to look up later, not statistics, so
they can't stretch a duration or bump a tier. Each one carries the `source`
of the segment it came from, so a link posted in chat stays taggable as such.

Stdlib only -- no pip install, no venv, no container needed to run this.

Why this exists: raw transcripts show up in wildly different shapes (WebVTT
export, SRT, a pasted "Name: text" chat log, Otter/Zoom/Teams timestamp-
prefixed lines, or just an unstructured paste with no speaker markers at
all). Guessing at structure freehand for every request wastes effort and is
easy to get subtly wrong; this script does the mechanical parsing once so
the recap-writing skill can reason about clean, structured data -- and so it
can flag *up front* when a transcript is too short, too unstructured, or too
long for a flat recap to stay skimmable, instead of silently producing one
anyway.

Formats detected: "vtt", "srt", "labeled" (timestamp-optional "Speaker:
text" lines), "plain" (no speaker/timestamp structure -- paragraphs only).

Meeting duration is computed from the transcript's own recorded timing --
the first segment's start time through the *last segment's recorded end
time* (not its start time) whenever the format actually records cue end
times (WebVTT/SRT do; a plain timestamp-prefixed line only records when a
turn started, not when it ended). Word-count-based estimation is used only
as a last resort, when the transcript carries no timestamps at all, and is
always labeled as an estimate via `duration_basis` rather than presented
with the same confidence as a real, recorded span.

Usage:
    python3 normalize_transcript.py <path>       # read a file
    python3 normalize_transcript.py -             # read stdin (default)
    python3 normalize_transcript.py <path> --format labeled   # skip detection

    # with the meeting chat merged in, anchored to when recording started:
    python3 normalize_transcript.py <path> --chat chat.json \
        --chat-anchor 2026-08-07T14:00:00Z

The chat file is JSON -- either a bare list or {"messages": [...]} -- whose
entries carry a sender (sender/from/speaker/displayName), an ISO 8601
timestamp (timestamp/createdDateTime/time/sentDateTime), and text
(text/content/body). Anything that doesn't parse as JSON falls back to the
"Speaker: text" line parser, which yields chat with no timestamps.

Exit codes:
    0 -- ran to completion; stdout is the normalized JSON.
    1 -- fatal error (unreadable file, empty input); stdout is a JSON object
         with a "fatal_error" key.
"""
import argparse
import datetime as dt
import json
import re
import sys

TIMESTAMP_ARROW_RE = re.compile(r"\d{2}:\d{2}:\d{2}[.,]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[.,]\d{3}")
VTT_VOICE_TAG_RE = re.compile(r"^<v\s+([^>]+)>(.*?)(?:</v>)?$", re.DOTALL)
CUE_SPEAKER_RE = re.compile(r"^([A-Za-z][\w'.\- ]{0,40}?):\s+(.+)$", re.DOTALL)
LABELED_LINE_TS_RE = re.compile(
    r"^\[?(\d{1,2}(?::\d{2}){1,2})\]?\s+([A-Za-z][\w'.\- ]{0,40}?):\s+(.+)$"
)
LABELED_LINE_RE = re.compile(r"^([A-Za-z][\w'.\- ]{0,40}?):\s+(.+)$")
DAY_MARKER_RE = re.compile(r"\bday\s+(\d+|one|two|three|four|five|six|seven)\b", re.IGNORECASE)

# Links pasted into a meeting chat or read out in a transcript. Deliberately
# permissive on the tail (URLs carry query strings, fragments, and encoded
# characters) and cleaned up afterwards by _trim_url, since prose punctuation
# ("see https://example.com/spec.") would otherwise get swallowed into the URL.
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'`\\]+", re.IGNORECASE)
URL_TRAILING_PUNCT = ".,;:!?\"'>»”’"
URL_CLOSERS = {")": "(", "]": "[", "}": "{"}
# A single-label host ("wiki") is valid -- internal links look like that --
# but a dangling "www." left over from trimming prose punctuation is not.
VALID_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9\-.]*[a-z0-9])?$")

# Hosts that can't be reached from outside the network the meeting happened
# on. NOTE: this only catches *network*-private hosts -- an auth-walled page
# on a public host (a company Confluence, a private Google Doc) looks public
# here and needs the skill's own judgment; see references/extraction-rules.md.
PRIVATE_HOST_SUFFIXES = (".local", ".internal", ".intranet", ".corp", ".lan", ".test", ".localhost")
PRIVATE_IP_RE = re.compile(r"^(?:10\.|127\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.)")

# How much of the surrounding utterance to keep on each side of a link or
# image, so the skill can tell what it *was* from what was said about it
# rather than guessing from the URL slug or the filename.
LINK_CONTEXT_WINDOW_CHARS = 120

# Images shared during a meeting reach a transcript in a handful of shapes:
# a chat-pasted URL ending in an image extension, markdown or HTML image
# syntax (Teams chat bodies arrive as HTML), or an attachment line naming a
# file ("Jane shared a file: mockup.png", "[Screenshot 2025-03-04 at
# 11.02.13.png]"). All four are collected; see extract_shared_images().
IMAGE_EXTENSIONS = (
    "png", "jpg", "jpeg", "gif", "webp", "svg", "bmp", "tif", "tiff", "heic", "heif", "avif",
)
_IMAGE_EXT_GROUP = "|".join(IMAGE_EXTENSIONS)
IMAGE_EXT_RE = re.compile(rf"\.(?:{_IMAGE_EXT_GROUP})$", re.IGNORECASE)
MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]\n]*)\]\(\s*<?([^)\s>]+)>?(?:\s+[^)\n]*)?\)")
HTML_IMG_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
HTML_SRC_RE = re.compile(r"\bsrc\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)
HTML_ALT_RE = re.compile(r"\balt\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
# A delimited filename may contain spaces -- macOS screenshot names always do.
DELIMITED_IMAGE_FILE_RE = re.compile(
    rf"[\[(<\"'“]\s*([^\[\]()<>\"'\n]*?\.(?:{_IMAGE_EXT_GROUP}))\s*[\])>\"'”]", re.IGNORECASE
)
# A bare (undelimited) filename or path must not contain spaces, or it would
# swallow the prose in front of it ("here is the mockup.png").
BARE_IMAGE_FILE_RE = re.compile(
    rf"(?<![\w@.\-/\\])((?:[A-Za-z]:\\|~/|\.{{1,2}}/|/)?[\w.\-/\\]*[\w\-]\.(?:{_IMAGE_EXT_GROUP}))(?![\w.])",
    re.IGNORECASE,
)
# Someone saying an image was shared, with no filename or link to go with it.
# Weak evidence by design: surfaced separately from real image references so
# the skill can *say* an image existed without pretending it can open one.
_IMAGE_NOUN = (
    r"(?:images?|screen ?shots?|photos?|pictures?|graphics?|diagrams?|mock-?ups?"
    r"|whiteboards?|snapshots?)"
)
IMAGE_MENTION_RE = re.compile(
    rf"\b(?:shar\w+|post\w+|sent|sending|attach\w+|upload\w+|past\w+|dropp?\w*"
    rf"|here'?s|look at|see)\b"
    rf"[^.\n]{{0,30}}?\b{_IMAGE_NOUN}\b",
    re.IGNORECASE,
)

SHORT_TRANSCRIPT_WORD_THRESHOLD = 150

# Rough conversational speech rate, used ONLY to estimate a meeting's
# duration when the transcript has no timestamps at all. Recorded
# timestamps always take precedence over this estimate -- see
# `duration_basis` in compute_stats().
ESTIMATED_WORDS_PER_MINUTE = 150

# A gap at least this long between one segment's (recorded or inferred) end
# and the next segment's start is treated as a likely break between
# sessions (a lunch break, end of a workshop block, overnight) rather than
# a pause within one continuous conversation. Tune freely.
SESSION_GAP_THRESHOLD_SEC = 20 * 60

# The sliding ruler: how much structure and length a recap should have,
# keyed off the meeting's actual duration. Ordered shortest-to-longest;
# `max_duration_sec` is the upper bound of each tier (None = open-ended).
# This is the tunable "variable" the recap-writing skill reads from --
# retuning the recap format means editing these numbers, not the reasoning
# in references/output-template.md.
#
# The entry-point word budget barely grows past "extended" on purpose: once
# a meeting has enough content that it wouldn't fit a handful of scannable
# topic clusters, the answer is to push detail into per-session/per-day
# sub-sections (see references/output-template.md), not to keep inflating
# the one summary everyone reads first.
LENGTH_TIERS = [
    {
        "name": "micro",
        "label": "Micro (≤15 min)",
        "max_duration_sec": 15 * 60,
        "structure": "flat",
        "cluster_unit": "topics",
        "cluster_count_range": "0 (omit)",
        "entry_point_word_budget": {"min": 40, "max": 80},
    },
    {
        "name": "short",
        "label": "Short (15-30 min)",
        "max_duration_sec": 30 * 60,
        "structure": "flat",
        "cluster_unit": "topics",
        "cluster_count_range": "0-2",
        "entry_point_word_budget": {"min": 80, "max": 150},
    },
    {
        "name": "standard",
        "label": "Standard (30-60 min)",
        "max_duration_sec": 60 * 60,
        "structure": "flat",
        "cluster_unit": "topics",
        "cluster_count_range": "3-5",
        "entry_point_word_budget": {"min": 150, "max": 350},
    },
    {
        "name": "extended",
        "label": "Extended (1-3 hr)",
        "max_duration_sec": 3 * 3600,
        "structure": "flat",
        "cluster_unit": "topics",
        "cluster_count_range": "4-6",
        "entry_point_word_budget": {"min": 250, "max": 450},
    },
    {
        "name": "half_day",
        "label": "Half-day (3-5 hr)",
        "max_duration_sec": 5 * 3600,
        "structure": "hierarchical",
        "cluster_unit": "sessions",
        "cluster_count_range": "3-6",
        "entry_point_word_budget": {"min": 200, "max": 350},
    },
    {
        "name": "full_day",
        "label": "Full-day (5-8 hr)",
        "max_duration_sec": 8 * 3600,
        "structure": "hierarchical",
        "cluster_unit": "sessions",
        "cluster_count_range": "4-8",
        "entry_point_word_budget": {"min": 250, "max": 400},
    },
    {
        "name": "multi_day",
        "label": "Multi-day (>8 hr, or spans multiple days)",
        "max_duration_sec": None,
        "structure": "hierarchical",
        "cluster_unit": "days",
        "cluster_count_range": "1 chunk/day + cross-day rollup",
        "entry_point_word_budget": {"min": 300, "max": 500},
    },
]


def _timestamp_to_seconds(ts):
    """Parses H:MM:SS(.mmm) / MM:SS / HH:MM:SS,mmm into seconds. Missing
    higher units are assumed zero (e.g. "12:04" -> 0h 12m 4s)."""
    parts = [float(p) for p in ts.replace(",", ".").split(":")]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    hours, minutes, seconds = parts
    return hours * 3600 + minutes * 60 + seconds


def _first_token(s):
    parts = s.strip().split()
    return parts[0] if parts else ""


def extract_inline_speaker(cue_text):
    """Pulls a speaker out of one cue's text, if present, via either WebVTT's
    <v Name>...</v> voice tag or a plain "Name: text" prefix."""
    stripped = cue_text.strip()
    voice_match = VTT_VOICE_TAG_RE.match(stripped)
    if voice_match:
        return voice_match.group(1).strip(), voice_match.group(2).strip()
    speaker_match = CUE_SPEAKER_RE.match(stripped)
    if speaker_match:
        return speaker_match.group(1).strip(), speaker_match.group(2).strip()
    return None, stripped


def _parse_cue_blocks(text):
    """Shared block parsing for VTT/SRT: split on blank lines, find the
    "-->" timestamp line in each block, join everything after it as the cue
    text. Captures both the cue's start AND its recorded end time (WebVTT
    cue-settings text that sometimes trails the end timestamp, e.g.
    "align:start position:0%", is discarded via _first_token)."""
    segments = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        ts_line_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if ts_line_index is None:
            continue
        start_ts, _, end_ts = lines[ts_line_index].partition("-->")
        start_token = _first_token(start_ts)
        end_token = _first_token(end_ts)
        cue_text = " ".join(lines[ts_line_index + 1:]).strip()
        if not cue_text:
            continue
        speaker, remaining = extract_inline_speaker(cue_text)
        segments.append({
            "source": "transcript",
            "speaker": speaker,
            "timestamp_sec": _timestamp_to_seconds(start_token) if start_token else None,
            "end_sec": _timestamp_to_seconds(end_token) if end_token else None,
            "text": remaining,
        })
    return segments


def parse_vtt(text):
    return _parse_cue_blocks(text)


def parse_srt(text):
    return _parse_cue_blocks(text)


def parse_labeled(text):
    segments = []
    for raw_line in text.replace("\r\n", "\n").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = LABELED_LINE_TS_RE.match(line)
        if match:
            segments.append({
                "source": "transcript",
                "speaker": match.group(2).strip(),
                "timestamp_sec": _timestamp_to_seconds(match.group(1)),
                "end_sec": None,  # a leading timestamp marks only when the turn started
                "text": match.group(3).strip(),
            })
            continue
        match = LABELED_LINE_RE.match(line)
        if match:
            segments.append({
                "source": "transcript",
                "speaker": match.group(1).strip(),
                "timestamp_sec": None,
                "end_sec": None,
                "text": match.group(2).strip(),
            })
            continue
        if segments:
            # Continuation of the previous speaker's turn (wrapped line).
            segments[-1]["text"] += " " + line
        # A leading unmatched line (e.g. a title) before any speaker turn is dropped.
    return segments


def parse_plain(text):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.replace("\r\n", "\n")) if p.strip()]
    if not paragraphs and text.strip():
        paragraphs = [text.strip()]
    return [
        {
            "source": "transcript",
            "speaker": None,
            "timestamp_sec": None,
            "end_sec": None,
            "text": " ".join(p.split()),
        }
        for p in paragraphs
    ]


_PARSERS = {"vtt": parse_vtt, "srt": parse_srt, "labeled": parse_labeled, "plain": parse_plain}

# Field-name aliases, so a chat log can be handed over roughly as the source
# system produced it instead of being reshaped first. Microsoft Graph uses
# createdDateTime/from/body; other exports use time/sender/text.
CHAT_SENDER_KEYS = ("sender", "from", "speaker", "displayName", "author", "user")
CHAT_TIME_KEYS = ("timestamp", "createdDateTime", "time", "sentDateTime", "date")
# `bodyPreview` is last on purpose: Microsoft Graph truncates it with an
# ellipsis, so it's a fallback for a message that was never read in full,
# never a preferred source.
CHAT_TEXT_KEYS = ("text", "content", "body", "message", "bodyPreview")

ISO_DATETIME_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"           # date
    r"[T ](\d{2}):(\d{2})(?::(\d{2}))?"   # time (seconds optional)
    r"(?:\.(\d+))?"                       # fractional seconds
    r"\s*(Z|[+-]\d{2}:?\d{2})?$"          # offset (optional)
)


def parse_iso_datetime(value):
    """Parses an ISO 8601 datetime into a timezone-aware datetime.

    Deliberately hand-rolled rather than using datetime.fromisoformat: this
    script ships in a skills-only plugin with no lockfile and no `uv run`, so
    it runs on whatever `python3` the client happens to have. fromisoformat
    only learned to accept a trailing 'Z' in 3.11, and macOS still ships 3.9.
    Returns None for anything unparseable -- the caller degrades to unanchored
    chat rather than failing the whole run.
    """
    if not isinstance(value, str):
        return None
    match = ISO_DATETIME_RE.match(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second, fraction, offset = match.groups()
    microsecond = int((fraction or "0").ljust(6, "0")[:6])

    if offset in (None, "Z", "z"):
        # datetime.UTC (which ruff's UP017 wants here) landed in 3.11, and the
        # whole reason this function is hand-rolled is to keep working on the
        # 3.9 that macOS still ships. The alias would undo that.
        tzinfo = dt.timezone.utc  # noqa: UP017
    else:
        normalized = offset.replace(":", "")
        delta = dt.timedelta(hours=int(normalized[1:3]), minutes=int(normalized[3:5]))
        tzinfo = dt.timezone(-delta if normalized[0] == "-" else delta)

    try:
        return dt.datetime(
            int(year), int(month), int(day),
            int(hour), int(minute), int(second or 0), microsecond,
            tzinfo=tzinfo,
        )
    except ValueError:
        return None


def _first_present(mapping, keys):
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


HTML_ANCHOR_RE = re.compile(r"<a\b[^>]*>(.*?)</a\s*>", re.IGNORECASE | re.DOTALL)
HTML_HREF_RE = re.compile(r"\bhref\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)


def _flatten_chat_text(value):
    """Graph returns a message body as {"content": "...", "contentType": ...};
    other exports use a bare string. Strips HTML tags, since Teams message
    bodies commonly come back as contentType 'html'.

    Images and hyperlinks are rewritten to their plain-text equivalents
    *before* the generic tag strip, not swallowed by it. This matters more
    than it looks: a Teams chat is HTML, and the usual way an image or a
    link reaches a meeting chat is `<img src=...>` or `<a href=...>`, not a
    pasted bare URL. Stripping those tags wholesale would delete exactly the
    artifacts extract_shared_links/extract_shared_images exist to collect,
    and the loss would be invisible -- the message would still read fine.
    """
    if isinstance(value, dict):
        value = _first_present(value, CHAT_TEXT_KEYS) or ""
    text = re.sub(r"<br\s*/?>", " ", str(value))

    def _image_to_markdown(match):
        src = HTML_SRC_RE.search(match.group(0))
        if not src:
            return " "
        alt = HTML_ALT_RE.search(match.group(0))
        return f" ![{alt.group(1).strip() if alt else ''}]({src.group(1).strip()}) "

    def _anchor_to_text(match):
        href = HTML_HREF_RE.search(match.group(0))
        label = " ".join(re.sub(r"<[^>]+>", "", match.group(1)).split())
        if not href:
            return f" {label} "
        url = href.group(1).strip()
        # Teams renders a pasted URL as <a href="URL">URL</a>; emitting
        # "URL (URL)" there would be noise, and the label alone is enough.
        if not label or url in label:
            return f" {url} "
        return f" {label} ({url}) "

    text = HTML_IMG_RE.sub(_image_to_markdown, text)
    text = HTML_ANCHOR_RE.sub(_anchor_to_text, text)
    text = re.sub(r"<[^>]+>", "", text)
    return " ".join(text.split())


def _flatten_chat_sender(value):
    """Graph nests the sender as {"user": {"displayName": ...}}; flatter
    exports use a bare name string."""
    while isinstance(value, dict):
        nested = _first_present(value, CHAT_SENDER_KEYS)
        if nested is None:
            return None
        value = nested
    return str(value).strip() or None


def parse_chat(text, anchor=None):
    """Normalizes a chat log into segments shaped like transcript segments.

    `anchor` is the wall-clock time that transcript second 0 corresponds to
    (normally when recording started). With it, each message gets a
    `timestamp_sec` on the transcript's own clock so the two can interleave;
    without it, messages keep their wall-clock string and a null
    `timestamp_sec`, and the caller appends rather than interleaves.

    A message sent before the anchor gets a negative `timestamp_sec`. That's
    intentional -- pre-meeting chat really did happen before the recording
    started, and sorting puts it first, where it belongs.
    """
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        # Not JSON -- fall back to the "Speaker: text" line parser. No usable
        # wall-clock times, so this path is always unanchored.
        return [{**seg, "source": "chat", "sent_at": None} for seg in parse_labeled(text)]

    if isinstance(payload, dict):
        payload = _first_present(payload, ("messages", "value", "items", "chat")) or []
    if not isinstance(payload, list):
        return []

    segments = []
    for entry in payload:
        if not isinstance(entry, dict):
            entry = {"text": entry}
        # Teams meeting chats are full of system events -- "X joined", "call
        # ended", "recording started" -- carrying messageType
        # "unknownFutureValue" and an eventDetail rather than a real body.
        # They're noise in a recap, and filtering on the type is sturdier
        # than hoping their body renders empty.
        if entry.get("messageType", "message") != "message" or entry.get("eventDetail"):
            continue
        body = _flatten_chat_text(_first_present(entry, CHAT_TEXT_KEYS) or "")
        if not body:
            continue
        raw_time = _first_present(entry, CHAT_TIME_KEYS)
        sent_at = parse_iso_datetime(raw_time) if raw_time else None
        segments.append({
            "source": "chat",
            "speaker": _flatten_chat_sender(_first_present(entry, CHAT_SENDER_KEYS)),
            "timestamp_sec": (sent_at - anchor).total_seconds() if (sent_at and anchor) else None,
            "end_sec": None,
            "text": body,
            "sent_at": sent_at.isoformat() if sent_at else (raw_time or None),
        })
    return segments


def merge_segments(transcript_segments, chat_segments):
    """Interleaves chat into the transcript chronologically, returning the
    merged list and the indices the transcript segments landed at.

    Interleaving needs chat messages placed on the transcript's clock, which
    means both an anchor and a transcript that actually has timestamps. When
    either is missing, chat is appended after the transcript instead of being
    dropped or given a made-up position -- an honest "here, but unplaced"
    beats a confident wrong order.

    Transcript segments without their own timestamp (a `labeled` paste, a
    `plain` block) sort by the last timestamp seen before them, so they stay
    put relative to their own neighbours instead of being flung to one end.
    """
    if not chat_segments:
        return list(transcript_segments), list(range(len(transcript_segments))), "none"

    chat_is_placeable = any(s["timestamp_sec"] is not None for s in chat_segments)
    transcript_is_timed = any(s["timestamp_sec"] is not None for s in transcript_segments)

    if not (chat_is_placeable and transcript_is_timed):
        merged = list(transcript_segments) + list(chat_segments)
        return merged, list(range(len(transcript_segments))), "unanchored"

    ordered = []
    carried = 0.0
    for position, seg in enumerate(transcript_segments):
        if seg["timestamp_sec"] is not None:
            carried = seg["timestamp_sec"]
        ordered.append((carried, 0, position, seg))
    for position, seg in enumerate(chat_segments):
        # An unparseable timestamp among otherwise-timed chat sorts to the
        # end rather than silently landing at second zero.
        when = seg["timestamp_sec"]
        ordered.append((float("inf") if when is None else when, 1, position, seg))

    ordered.sort(key=lambda item: item[:3])
    merged = [item[3] for item in ordered]
    transcript_indices = [i for i, item in enumerate(ordered) if item[1] == 0]
    return merged, transcript_indices, "anchored"


def detect_format(text):
    if text.lstrip("﻿ \t\r\n").startswith("WEBVTT"):
        return "vtt"
    lines = [line for line in text.splitlines() if line.strip()]
    has_arrow = any(TIMESTAMP_ARROW_RE.search(line) for line in lines[1:4])
    if lines and lines[0].strip().isdigit() and has_arrow:
        return "srt"
    sample = lines[:40]
    labeled_hits = sum(
        1 for line in sample if LABELED_LINE_TS_RE.match(line) or LABELED_LINE_RE.match(line)
    )
    if labeled_hits >= 2:
        return "labeled"
    return "plain"


def detect_session_breaks(segments, indices=None):
    """Flags gaps that look like a break between sessions rather than a
    pause within one conversation. Uses each segment's recorded end time
    when available, falling back to its start time (the best we can do for
    formats that don't record an end per turn).

    `indices` maps each passed segment to its position in the *output*
    segments list. It matters once chat is interleaved: these are transcript
    segments only (chat must never invent a session break), but the reported
    indices have to point at the merged list the reader actually sees.
    Defaults to the segments' own positions."""
    if indices is None:
        indices = range(len(segments))
    breaks = []
    previous_end = None
    previous_index = None
    for i, seg in zip(indices, segments):
        start = seg.get("timestamp_sec")
        if start is None:
            continue
        if previous_end is not None:
            gap = start - previous_end
            if gap >= SESSION_GAP_THRESHOLD_SEC:
                breaks.append({
                    "after_segment_index": previous_index,
                    "before_segment_index": i,
                    "gap_sec": gap,
                })
        previous_end = seg.get("end_sec") if seg.get("end_sec") is not None else start
        previous_index = i
    return breaks


def detect_day_markers(segments, indices=None):
    """Best-effort textual hints ("Day 2", "Day Three") that a transcript
    spans multiple calendar days. A hint, not a verdict -- a passing remark
    can false-positive, so it's surfaced as evidence for the skill's own
    judgment rather than silently forcing multi-day handling.

    See detect_session_breaks for what `indices` is for. Scanning transcript
    text only also keeps someone typing "day 2" in chat from forcing an
    entire recap into the Multi-day tier."""
    if indices is None:
        indices = range(len(segments))
    hints = []
    for i, seg in zip(indices, segments):
        match = DAY_MARKER_RE.search(seg["text"])
        if match:
            hints.append({
                "segment_index": i,
                "timestamp_sec": seg.get("timestamp_sec"),
                "matched_text": match.group(0),
            })
    return hints


def _trim_url(url):
    """Strips prose punctuation the URL regex over-captured. Closing brackets
    are only dropped when unbalanced, so a real URL that legitimately contains
    parentheses -- Wikipedia's `/wiki/Mercury_(planet)` is the classic case --
    keeps them, while "(see https://example.com)" doesn't keep the wrapper."""
    while url:
        last = url[-1]
        if last in URL_TRAILING_PUNCT:
            url = url[:-1]
            continue
        opener = URL_CLOSERS.get(last)
        if opener and url.count(last) > url.count(opener):
            url = url[:-1]
            continue
        break
    return url


def _url_host(url):
    """Bare hostname, lowercased -- no scheme, userinfo, port, path, or query."""
    rest = re.sub(r"^https?://", "", url, flags=re.IGNORECASE)
    rest = re.split(r"[/?#]", rest, maxsplit=1)[0]
    return rest.split("@")[-1].split(":", 1)[0].lower()


def _looks_private_host(host):
    if not host or host == "localhost":
        return True
    if host.endswith(PRIVATE_HOST_SUFFIXES):
        return True
    if PRIVATE_IP_RE.match(host):
        return True
    return "." not in host  # a bare single-label hostname, e.g. "wiki"


def _link_dedup_key(url):
    """Case-folds scheme and host (case-insensitive by spec) but NOT the path
    (which is case-sensitive on most servers), so two genuinely different deep
    links don't collapse into one row."""
    match = re.match(r"^(https?://)([^/?#]*)(.*)$", url, re.IGNORECASE)
    if not match:
        return url.rstrip("/")
    return match.group(1).lower() + match.group(2).lower() + match.group(3).rstrip("/")


def _link_context(text, start, end):
    """The words immediately around a link, so the skill can describe it from
    what was actually said rather than inferring meaning from the URL slug.
    Trims to whole words -- the window is far wider than any partial word, so
    this can never cut into the URL itself."""
    left = max(0, start - LINK_CONTEXT_WINDOW_CHARS)
    right = min(len(text), end + LINK_CONTEXT_WINDOW_CHARS)
    snippet = text[left:right]
    if left > 0 and " " in snippet:
        snippet = snippet.split(" ", 1)[1]
    if right < len(text) and " " in snippet:
        snippet = snippet.rsplit(" ", 1)[0]
    snippet = " ".join(snippet.split())
    if left > 0:
        snippet = "… " + snippet
    if right < len(text):
        snippet = snippet + " …"
    return snippet


def _is_image_url(url):
    """True when a URL's *path* ends in a known image extension. Query strings
    and fragments are ignored, so a signed CDN link still resolves. An
    extensionless image URL is undetectable this way and stays a plain link."""
    path = re.split(r"[?#]", re.sub(r"^https?://", "", url, flags=re.IGNORECASE), maxsplit=1)[0]
    return bool(IMAGE_EXT_RE.search(path))


def _image_candidates(text):
    """Every image reference in one utterance as (span, reference, alt_text,
    source), in reading order.

    Matched in priority order -- markdown, HTML, image URL, delimited
    filename, bare filename -- with each accepted match claiming its span so
    the same image can't be counted twice by two patterns (the URL inside
    `![shot](https://.../a.png)` is already covered by the markdown match).
    """
    claimed = []
    candidates = []

    def claim(start, end):
        if any(start < other_end and end > other_start for other_start, other_end in claimed):
            return False
        claimed.append((start, end))
        return True

    for match in MARKDOWN_IMAGE_RE.finditer(text):
        if claim(*match.span()):
            candidates.append(
                (match.span(), match.group(2).strip(), match.group(1).strip() or None, "markdown")
            )
    for match in HTML_IMG_RE.finditer(text):
        src = HTML_SRC_RE.search(match.group(0))
        if src and claim(*match.span()):
            alt = HTML_ALT_RE.search(match.group(0))
            alt_text = alt.group(1).strip() if alt else ""
            candidates.append((match.span(), src.group(1).strip(), alt_text or None, "html"))
    for match in URL_RE.finditer(text):
        url = _trim_url(match.group(0))
        if url and _is_image_url(url) and claim(match.start(), match.start() + len(url)):
            candidates.append(((match.start(), match.start() + len(url)), url, None, "url"))
    for match in DELIMITED_IMAGE_FILE_RE.finditer(text):
        if claim(*match.span(1)):
            candidates.append((match.span(1), match.group(1).strip(), None, "attachment"))
    for match in BARE_IMAGE_FILE_RE.finditer(text):
        if claim(*match.span(1)):
            candidates.append((match.span(1), match.group(1), None, "filename"))

    candidates.sort(key=lambda candidate: candidate[0][0])
    return candidates


def _classify_image_reference(reference):
    """(kind, retrieval) -- what the reference *is*, and whether the skill can
    actually open it. `name_only` is the one that can't be: a filename with no
    path and no URL says an image existed, not where to find it."""
    lowered = reference.lower()
    if lowered.startswith("data:"):
        return "data_uri", "inline_data"
    if lowered.startswith(("http://", "https://", "www.")):
        return "url", "remote_url"
    if "/" in reference or "\\" in reference:
        return "path", "local_path"
    return "filename", "name_only"


def extract_shared_links(segments):
    """Collects URLs shared during the meeting, in first-mention order, with
    who shared each one, what was said around it, and whether it came from the
    transcript or from chat.

    Runs over the MERGED segments on purpose -- chat is where links actually
    get pasted, and a links table that ignored it would miss most of them.
    That doesn't breach the transcript-only rule for statistics: a link is an
    artifact to look up later, not a measure of how long the meeting ran.

    Only finds links that survived into the transcript as text -- a chat
    paste or an export that inlines chat messages. A URL that was *spoken*
    aloud ("go to acme dot com slash pricing") is not detected, and neither
    is one in an attachment the transcript never quotes.

    Repeat mentions of the same URL collapse into one entry with a bumped
    `mention_count`, attributed to whoever shared it first. A bare `www.`
    link is given an `https://` scheme so the output is clickable -- that's
    normalization, and the only part of the URL not taken verbatim.

    Image URLs are deliberately excluded -- they belong to
    extract_shared_images() instead, so a screenshot doesn't get one row in
    the links table and another in the images table.
    """
    links = []
    seen = {}
    for index, segment in enumerate(segments):
        text = segment.get("text") or ""
        image_spans = [span for span, _, _, _ in _image_candidates(text)]
        for match in URL_RE.finditer(text):
            url = _trim_url(match.group(0))
            if any(match.start() < end and match.start() + len(url) > start
                   for start, end in image_spans):
                continue  # already collected as an image
            if url.lower().startswith("www."):
                url = "https://" + url
            host = _url_host(url)
            if not VALID_HOST_RE.match(host):
                continue  # a scheme with no real host behind it
            key = _link_dedup_key(url)
            if key in seen:
                seen[key]["mention_count"] += 1
                continue
            entry = {
                "url": url,
                "host": host,
                "likely_private_host": _looks_private_host(host),
                "shared_by": segment.get("speaker"),
                "source": segment.get("source", "transcript"),
                "segment_index": index,
                "timestamp_sec": segment.get("timestamp_sec"),
                "context": _link_context(text, match.start(), match.end()),
                "mention_count": 1,
            }
            seen[key] = entry
            links.append(entry)
    return links


def extract_shared_images(segments):
    """Collects images shared during the meeting, in first-mention order, with
    who shared each one, what was said around it, whether it came from the
    transcript or chat, and -- crucially -- whether it can actually be opened
    (`retrieval`).

    Runs over the MERGED segments for the same reason as links, only more so:
    a screenshot is almost always dropped into the chat rather than described
    out loud.

    Same hard limit as links: this only sees what survived into the transcript
    or chat *as text*. An image that exists only inside the meeting platform
    leaves no reference to find. Bare `www.` references get an `https://`
    scheme so the output is clickable; nothing else is rewritten.

    Repeat mentions collapse into one entry with a bumped `mention_count`,
    attributed to whoever shared it first.
    """
    images = []
    seen = {}
    for index, segment in enumerate(segments):
        text = segment.get("text") or ""
        for (start, end), reference, alt_text, source in _image_candidates(text):
            kind, retrieval = _classify_image_reference(reference)
            if reference.lower().startswith("www."):
                reference = "https://" + reference
            host = _url_host(reference) if kind == "url" else None
            if host is not None and not VALID_HOST_RE.match(host):
                continue  # a scheme with no real host behind it
            key = f"{kind}:" + (
                _link_dedup_key(reference) if kind == "url" else reference.lower().lstrip("./")
            )
            if key in seen:
                seen[key]["mention_count"] += 1
                if alt_text and not seen[key]["alt_text"]:
                    seen[key]["alt_text"] = alt_text
                continue
            entry = {
                "reference": reference,
                "kind": kind,
                "retrieval": retrieval,
                "reference_form": source,
                "alt_text": alt_text,
                "host": host,
                "likely_private_host": _looks_private_host(host) if host is not None else None,
                "shared_by": segment.get("speaker"),
                "source": segment.get("source", "transcript"),
                "segment_index": index,
                "timestamp_sec": segment.get("timestamp_sec"),
                "context": _link_context(text, start, end),
                "mention_count": 1,
            }
            seen[key] = entry
            images.append(entry)
    return images


def detect_image_mentions(segments):
    """Utterances that say an image was shared without naming one ("I just
    dropped a screenshot in the chat"). Evidence that something visual existed
    that neither transcript nor chat captured -- not a row in the images
    table, and never something to describe as if it had been seen."""
    mentions = []
    for index, segment in enumerate(segments):
        text = segment.get("text") or ""
        if _image_candidates(text):
            continue  # a real reference is in this utterance; not a bare mention
        match = IMAGE_MENTION_RE.search(text)
        if match:
            mentions.append({
                "segment_index": index,
                "timestamp_sec": segment.get("timestamp_sec"),
                "shared_by": segment.get("speaker"),
                "source": segment.get("source", "transcript"),
                "matched_text": match.group(0),
                "context": _link_context(text, match.start(), match.end()),
            })
    return mentions


def recommend_tier(duration_sec, duration_basis, day_marker_hints):
    """Looks up the sliding-ruler tier (see LENGTH_TIERS) for this meeting's
    duration. Explicit day markers ("Day 2") win outright, since a single
    file's timestamp span can't be trusted to reflect a whole multi-day
    event's true length."""
    if day_marker_hints:
        matched = LENGTH_TIERS[-1]
    elif duration_sec is None:
        matched = None
    else:
        matched = next(
            (
                t for t in LENGTH_TIERS
                if t["max_duration_sec"] is None or duration_sec <= t["max_duration_sec"]
            ),
            LENGTH_TIERS[-1],
        )

    if matched is None:
        return {
            "name": "unknown",
            "label": "Unknown (not enough data to estimate meeting length)",
            "max_duration_sec": None,
            "structure": "flat",
            "cluster_unit": "topics",
            "cluster_count_range": "3-5",
            "entry_point_word_budget": {"min": 150, "max": 350},
            "basis": duration_basis,
        }

    return {**matched, "basis": duration_basis}


def compute_stats(
    segments, indices=None, chat_segments=None, chat_alignment="none", merged_segments=None
):
    """Computes every statistic from `segments`, which must be the TRANSCRIPT
    segments only -- chat is reported alongside but never folded in.

    That separation is the whole point of the split: `duration_sec` and the
    tier it drives describe how long people were in the room, and a meeting
    where fifty links got pasted is not thereby a longer meeting. Chat is
    summarized in its own fields so it's still visible.

    `merged_segments` (transcript + chat, in reading order) is used for the
    one thing that legitimately spans both: collecting shared links and
    images, which are overwhelmingly pasted in chat. Those are artifacts, not
    statistics -- they carry their own `source` tag and can't move a tier.
    Defaults to `segments`, so a transcript-only run needs no extra argument.
    """
    chat_segments = chat_segments or []
    if merged_segments is None:
        merged_segments = segments
    if indices is None:
        indices = range(len(segments))
    indices = list(indices)
    speakers = sorted({s["speaker"] for s in segments if s["speaker"]})
    starts = [s["timestamp_sec"] for s in segments if s["timestamp_sec"] is not None]
    ends = [s["end_sec"] for s in segments if s.get("end_sec") is not None]
    word_count = sum(len(s["text"].split()) for s in segments)

    start_sec = min(starts) if starts else None
    # Prefer the latest *recorded end* time across all segments; only fall
    # back to the latest known start time when the format never records an
    # end at all (see parse_labeled).
    end_sec = max(ends) if ends else (max(starts) if starts else None)

    if start_sec is not None and end_sec is not None and end_sec > start_sec:
        duration_sec = end_sec - start_sec
        duration_basis = "recorded_end_times" if ends else "start_times_only"
    elif word_count:
        duration_sec = (word_count / ESTIMATED_WORDS_PER_MINUTE) * 60
        duration_basis = "estimated_from_word_count"
    else:
        duration_sec = None
        duration_basis = "unknown"

    day_marker_hints = detect_day_markers(segments, indices)
    shared_links = extract_shared_links(merged_segments)
    shared_images = extract_shared_images(merged_segments)
    image_mentions = detect_image_mentions(merged_segments)

    warnings = []
    if word_count < SHORT_TRANSCRIPT_WORD_THRESHOLD:
        warnings.append(
            f"Very short transcript ({word_count} words) -- there may not be enough "
            "content for a real decisions/action-items recap. Say so plainly rather "
            "than padding the output to look complete."
        )
    if not speakers:
        warnings.append(
            "No speaker labels detected -- action-item ownership and per-person "
            "attribution will be unreliable. Prefer UNASSIGNED over guessing who said what."
        )
    if len(segments) <= 1 and word_count > 300:
        warnings.append(
            "No structural segmentation was found (single unbroken block) -- topic "
            "and speaker boundaries could not be detected mechanically."
        )
    if duration_basis == "estimated_from_word_count":
        warnings.append(
            "No timestamps found -- meeting length is a rough estimate from word "
            f"count (~{ESTIMATED_WORDS_PER_MINUTE} words/min). Treat the recommended "
            "recap tier below as a starting point, not a precise measurement."
        )
    name_only_images = sum(1 for image in shared_images if image["retrieval"] == "name_only")
    if name_only_images:
        warnings.append(
            f"{name_only_images} image(s) are referenced by filename only, with no path or "
            "URL -- they can't be opened. Describe them from what the transcript or chat "
            "says, or ask the user to share the files, rather than inferring content from "
            "a filename."
        )
    if image_mentions:
        warnings.append(
            f"{len(image_mentions)} utterance(s) mention an image being shared without "
            "naming a file or link. Note that something visual was shared and wasn't "
            "captured -- never describe an image nobody can see."
        )
    if chat_alignment == "unanchored":
        warnings.append(
            "Chat messages could not be placed on the transcript's clock (no "
            "--chat-anchor, or the transcript has no timestamps), so they are "
            "appended after the transcript rather than interleaved. Their order "
            "relative to what was being said is unknown -- don't infer that a "
            "chat message responded to a particular remark."
        )
    elif chat_alignment == "anchored":
        warnings.append(
            "Chat is interleaved using --chat-anchor as transcript second 0. If "
            "the anchor was the scheduled start rather than when recording "
            "actually began, every chat message is offset by that difference -- "
            "treat adjacency as approximate, not as proof of what prompted what."
        )

    chat_senders = sorted({s["speaker"] for s in chat_segments if s.get("speaker")})

    return {
        "segment_count": len(segments),
        "word_count": word_count,
        "speaker_count": len(speakers),
        "speakers": speakers,
        "has_speakers": bool(speakers),
        "has_timestamps": bool(starts),
        "start_sec": start_sec,
        "end_sec": end_sec,
        "duration_sec": duration_sec,
        "duration_basis": duration_basis,
        "possible_session_breaks": detect_session_breaks(segments, indices),
        "day_marker_hints": day_marker_hints,
        "recommended_tier": recommend_tier(duration_sec, duration_basis, day_marker_hints),
        "shared_links": shared_links,
        "shared_link_count": len(shared_links),
        "shared_images": shared_images,
        "shared_image_count": len(shared_images),
        "image_mentions_without_reference": image_mentions,
        "chat_message_count": len(chat_segments),
        "chat_senders": chat_senders,
        "chat_word_count": sum(len(s["text"].split()) for s in chat_segments),
        "chat_alignment": chat_alignment,
        "warnings": warnings,
    }


def normalize(text, format_override=None, chat_text=None, chat_anchor=None):
    fmt = format_override or detect_format(text)
    transcript_segments = _PARSERS[fmt](text)

    anchor = parse_iso_datetime(chat_anchor) if chat_anchor else None
    chat_segments = parse_chat(chat_text, anchor=anchor) if chat_text else []
    segments, transcript_indices, chat_alignment = merge_segments(
        transcript_segments, chat_segments
    )

    result = {
        "format_detected": fmt,
        "segments": segments,
        "stats": compute_stats(
            transcript_segments,
            indices=transcript_indices,
            chat_segments=chat_segments,
            chat_alignment=chat_alignment,
            merged_segments=segments,
        ),
    }
    if chat_anchor and anchor is None:
        result["stats"]["warnings"].insert(
            0,
            f"--chat-anchor {chat_anchor!r} is not a parseable ISO 8601 datetime, so "
            "it was ignored and chat could not be interleaved.",
        )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Detect format and normalize a meeting transcript into structured JSON."
    )
    parser.add_argument(
        "path", nargs="?", default="-", help="Transcript file path, or '-' for stdin (default)"
    )
    parser.add_argument(
        "--format", choices=sorted(_PARSERS), default=None, help="Skip auto-detection"
    )
    parser.add_argument(
        "--chat",
        default=None,
        metavar="PATH",
        help="Meeting chat log to merge in (JSON list, {'messages': [...]}, or labeled lines)",
    )
    parser.add_argument(
        "--chat-anchor",
        default=None,
        metavar="ISO8601",
        help=(
            "Wall-clock time of transcript second 0 (normally when recording started, "
            "e.g. 2026-08-07T14:00:00Z). Required to interleave chat rather than append it."
        ),
    )
    args = parser.parse_args(argv)

    def read(path, what):
        if path == "-":
            return sys.stdin.read(), None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                return f.read(), None
        except OSError as exc:
            return None, {"fatal_error": f"unreadable_{what}", "detail": str(exc)}

    text, error = read(args.path, "file")
    if error:
        print(json.dumps(error))
        return 1

    if not text.strip():
        print(json.dumps({"fatal_error": "empty_input"}))
        return 1

    chat_text = None
    if args.chat:
        chat_text, error = read(args.chat, "chat_file")
        if error:
            print(json.dumps(error))
            return 1

    result = normalize(
        text,
        format_override=args.format,
        chat_text=chat_text,
        chat_anchor=args.chat_anchor,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
