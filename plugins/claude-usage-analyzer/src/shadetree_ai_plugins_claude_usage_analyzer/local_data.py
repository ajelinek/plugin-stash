"""Read-only inventory of local Claude Desktop/Cowork data on this machine.

Everything here is metadata-first and defensive: Claude Desktop's on-disk
layout is not a documented public API, it was reverse-engineered by direct
filesystem inspection (see skills/claude-usage-analyzer/references/data-sources.md
for the full writeup and provenance), and it is a *cache* the Desktop app
maintains for itself, not a source of truth this module can assume is
complete or stable across versions. Every reader here is best-effort: a
missing/malformed file is skipped, never raised.

Scope is deliberately **Claude Desktop / Cowork only** -- this plugin is
built for business users of the Desktop app, not for developers using the
Claude Code CLI. Claude Code's own ``~/.claude`` store, its Desktop "Code
tab" counterpart (``claude-code-sessions/``), and everything derived from
them are out of scope on purpose and are never read; the CLI already ships
its own ``/doctor``, ``/usage``, and ``/context`` commands for that
surface. Don't reintroduce them here.

The two stores that *are* in scope:

- Claude Desktop / Cowork -- keyed by account/org UUID, under an
  OS-specific app-data directory. Cowork "Projects" that are folder-bound
  are called "Spaces" internally (``spaces.json``); non-folder-bound cloud
  Projects are cached separately (``.project-cache/<uuid>/metadata.json``).
  Each Cowork/Chat session also keeps its own nested per-session transcript
  (``local_<uuid>/.claude/projects/.../*.jsonl``) alongside its
  ``local_<uuid>.json`` metadata -- confirmed directly. That transcript is
  Cowork's own data (it happens to share Claude Code's JSONL layout), and
  it is the only per-message-accurate model signal Desktop has, so it stays
  in scope.
- The claude.ai account data export (``conversations.json`` etc.) is a
  *different* thing entirely, handled by ``export_data.py`` -- it covers
  claude.ai web chats only and has zero overlap with what lives here.
"""

from __future__ import annotations

import json
import math
import os
import platform
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any
from urllib.parse import urlparse

from shadetree_ai_plugins_common import check_path

from . import time_window

# --------------------------------------------------------------------------------
# Candidate paths (per-OS, defensive -- probe for existence, never assume)
# --------------------------------------------------------------------------------


def desktop_dir_candidates() -> list[Path]:
    home = Path.home()
    system = platform.system()

    if system == "Darwin":
        return [
            home / "Library/Application Support/Claude",
            home / "Library/Application Support/Claude-3p",
        ]

    if system == "Windows":
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData/Roaming")))
        localappdata = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
        candidates = [appdata / "Claude", appdata / "Claude-3p"]
        packages_dir = localappdata / "Packages"
        if packages_dir.is_dir():
            for entry in sorted(packages_dir.iterdir()):
                if entry.name.startswith("Claude_"):
                    candidates.append(entry / "LocalCache/Roaming/Claude")
        return candidates

    # Desktop does not officially support Linux; check the XDG-conventional spot
    # defensively rather than assuming it can never exist here.
    return [home / ".config/Claude"]


def output_dir_candidates() -> list[Path]:
    home = Path.home()
    candidates = [home / "Claude", home / "Documents/Claude"]
    if platform.system() == "Darwin":
        candidates.append(home / ".Trash/Claude")
    return candidates


# --------------------------------------------------------------------------------
# Timestamp rendering -- one convention, everywhere a date reaches a person
# --------------------------------------------------------------------------------
#
# `get_plan_usage_history` established the convention: every instant that
# reaches a caller comes back as a UTC form (for comparison and joining), a
# local form with its offset attached (for anything a person reads), and
# the local calendar day and weekday derived from that local form. It was
# applied to exactly one tool, which meant `get_desktop_settings` reported
# a first-launch date that was off by a calendar day and a weekday on this
# machine, and `check_data_access` emitted a bare epoch-seconds float with
# no rendering at all.
#
# This is the one helper that produces the whole set, so a new field can't
# ship with half of it. It never invents a value: an unusable timestamp
# yields None in every key rather than a plausible-looking date.


def _instant_fields(prefix: str, value: Any) -> dict[str, Any]:
    """One instant under `<prefix>_iso`, `<prefix>_local_iso`,
    `<prefix>_local_date`, `<prefix>_local_weekday`, `<prefix>_utc_offset`.

    Quote the local ones. The UTC one is for comparison and joining only --
    a weekday computed in a zone the reader doesn't live in is a wrong
    answer, not a caveat on a right one."""
    day = time_window.local_day(value)
    return {
        f"{prefix}_iso": time_window.iso(value),
        f"{prefix}_local_iso": time_window.local_iso(value),
        f"{prefix}_local_date": day["date"] if day else None,
        f"{prefix}_local_weekday": day["weekday"] if day else None,
        f"{prefix}_utc_offset": day["utc_offset"] if day else None,
    }


# What to say about local vs UTC in any response that carries both. One
# string, so the three tools that need it can't drift into three different
# explanations of the same thing.
LOCAL_TIME_NOTE = (
    "Every `*_local_iso` time, `*_local_date` and `*_local_weekday` in this response "
    "is in this machine's own timezone ({name}, {offset}); the `*_iso` value beside "
    "each one is the same instant in UTC, for comparison and joining only. Build "
    "anything the user reads -- a date, a weekday, a time of day -- from the local "
    "values."
)


def _local_time_note(anchor: Any = None) -> str:
    label = time_window.local_timezone_label(anchor)
    return LOCAL_TIME_NOTE.format(name=label["name"], offset=label["utc_offset"])


# --------------------------------------------------------------------------------
# Strict-JSON safety -- drop non-finite numbers, count them, say so
# --------------------------------------------------------------------------------
#
# Python's json module parses the non-standard `NaN`/`Infinity` literals
# happily, so a corrupt file on someone's disk really can put one into a
# response -- and `json.dumps(..., allow_nan=False)`, which is what a
# strict client parser on the other end of a tool call amounts to, rejects
# the result outright. `_percent` already handles this for the one field
# that is parsed as a number; the pass-through paths (a preference value, a
# scheduled-task entry, an `installedAt` copied through verbatim) do not.
#
# The treatment is the same as `_percent`'s and for the same reason:
# **drop, don't clamp.** Substituting 0, or the largest representable
# float, manufactures a plausible-looking figure out of file corruption.
# The value reads as "not recorded" and the count is surfaced so it is said
# out loud rather than looking like an absent field.


def _drop_non_finite(value: Any) -> tuple[Any, int]:
    """Recursively replace `NaN`/`Infinity`/`-Infinity` with None, counting
    each one. Structure is preserved -- a dropped list item stays as a None
    in place rather than shortening the list, since a silently shorter list
    is a second, quieter lie about the same data."""
    if isinstance(value, bool):
        return value, 0
    if isinstance(value, float) and not math.isfinite(value):
        return None, 1
    if isinstance(value, dict):
        dropped = 0
        out: dict[Any, Any] = {}
        for key, item in value.items():
            out[key], count = _drop_non_finite(item)
            dropped += count
        return out, dropped
    if isinstance(value, list):
        dropped = 0
        items: list[Any] = []
        for item in value:
            cleaned, count = _drop_non_finite(item)
            items.append(cleaned)
            dropped += count
        return items, dropped
    return value, 0


# --------------------------------------------------------------------------------
# Access check -- existence/readability only, never reads content
# --------------------------------------------------------------------------------


def _probe_path(path: str) -> dict[str, Any]:
    """`check_path` plus the timestamp rendering every other date in this
    plugin gets. `check_path` returns `modified` as a raw epoch-**seconds**
    float, which is not a date anyone can read and is one unit conversion
    away from being read as 1970. The raw value stays (it is what
    arithmetic wants); the renderings ride alongside it."""
    probe = check_path(path)
    if "modified" not in probe:
        return probe
    return {**probe, **_instant_fields("modified", probe["modified"])}


def check_data_access() -> dict[str, Any]:
    """Report which Claude Desktop/Cowork data locations are visible to
    *this* session. A location reading as missing does not necessarily mean
    it doesn't exist on this machine -- inside a folder-scoped Cowork Space
    or Project, it may simply not be attached to this session yet. This
    function can only report what it can see; interpreting that ambiguity
    for the user is the calling skill's job.

    The user-visible output folder is *read from Claude's own settings*
    (`coworkUserFilesPath` in `claude_desktop_config.json`) whenever that
    file is readable, and only guessed from the per-OS candidate list when
    it isn't. `output_dir_source` says which happened, so the caller can
    tell the user "Claude is configured to write here" rather than "it's
    probably here" -- a distinction that matters before anyone goes looking
    for a missing scheduled-task result.

    `data_files` reports up front whether the two account-level lenses are
    available this run -- plan usage history (`get_plan_usage_history`) and
    Desktop settings (`get_desktop_settings`) -- so a caller learns that
    here instead of on a failed call.

    Every probed path carries its last-modified time in all four renderings
    (`modified_iso`, `modified_local_iso`, `modified_local_date`,
    `modified_local_weekday`) beside the raw `modified` epoch-seconds
    float, which is the only one of them a person can't read. Quote a local
    one; `timezone` names the zone they're in.

    Deliberately does not probe Claude Code's `~/.claude` -- the CLI is out
    of scope for this plugin (see the module docstring)."""
    desktop = [_probe_path(str(p)) for p in desktop_dir_candidates()]
    desktop_found = next((d for d in desktop if d["exists"]), None)
    base_dir = Path(desktop_found["path"]) if desktop_found else None

    # Prefer the configured path, and always probe it first so `output[0]`
    # is the authoritative entry when there is one. The old candidate list
    # stays behind it as the fallback -- on a machine where the config file
    # is missing or unreadable, a guess is still better than nothing.
    configured_output = read_configured_output_dir(base_dir)
    candidate_paths = [str(p) for p in output_dir_candidates()]
    if configured_output is not None:
        candidate_paths = [configured_output] + [
            p for p in candidate_paths if p != configured_output
        ]
    output = [_probe_path(p) for p in candidate_paths]
    output_found = any(o["exists"] for o in output)

    if configured_output is not None:
        output_entry: dict[str, Any] | None = output[0]
        output_dir_source: str | None = "claude_desktop_config.json"
    else:
        output_entry = next((o for o in output if o["exists"]), None)
        output_dir_source = "candidate_probe" if output_entry else None
    output_dir = output_entry["path"] if output_entry else None

    any_found = desktop_found is not None or output_found

    warnings: list[str] = []
    if not desktop_found:
        warnings.append(
            "No Claude Desktop app-data directory is visible to this session. If "
            "Claude Desktop/Cowork is actually used on this machine, this session is "
            "most likely running inside a folder-scoped Cowork Space or Project that "
            "hasn't been given access to that folder -- add the path(s) listed in "
            "`desktop_candidates` to this Space/Project's file access scope, then "
            "restart the session. Without it there are no chats, Spaces, or Projects "
            "to analyze from local data; only an account data export can stand in."
        )
    if not output_found:
        message = (
            "The user-visible Claude output folder isn't visible to this session "
            "(see `output_candidates`). That's where Cowork writes Artifacts and "
            "scheduled-task results, so anything produced by a scheduled task will "
            "be missing from the analysis. Add it to this session's file access "
            "scope if scheduled-task output matters to the question being asked."
        )
        if output_dir_source == "claude_desktop_config.json":
            message += (
                f" Claude's own settings name {output_dir} as that folder, so this is "
                "a real configured path this session can't reach -- not a guess, and "
                "not evidence the folder doesn't exist."
            )
        warnings.append(message)

    return {
        "platform": platform.system(),
        # Every `modified_*` field below is rendered against this zone. A
        # last-modified date is the one thing in this response a person
        # reads as a date, so it gets the same treatment as every other
        # timestamp in this plugin rather than staying a bare epoch float.
        "timezone": time_window.local_timezone_label(),
        "desktop_candidates": desktop,
        "desktop_base_dir": desktop_found["path"] if desktop_found else None,
        "output_candidates": output,
        # Where the output folder came from: "claude_desktop_config.json"
        # (Claude's own configured value -- confirmed), "candidate_probe"
        # (found by guessing the conventional locations -- unconfirmed), or
        # None (neither). Say which when reporting the path.
        "output_dir": output_dir,
        "output_dir_source": output_dir_source,
        "data_files": _account_file_availability(base_dir),
        "warnings": warnings,
        "ok": any_found,
    }


def find_desktop_base_dir() -> Path | None:
    for p in desktop_dir_candidates():
        if p.is_dir():
            return p
    return None


# --------------------------------------------------------------------------------
# Redaction -- never let a secret-shaped key or a heavy tool-schema blob through
# --------------------------------------------------------------------------------

_SECRET_KEY_MARKERS = ("token", "secret", "auth", "cookie", "key")
_HEAVY_KEYS = ("enabledMcpTools", "remoteMcpServersConfig")


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SECRET_KEY_MARKERS)


def redact(value: Any) -> Any:
    """Recursively strip secret-shaped keys and known heavy tool-schema blobs."""
    if isinstance(value, dict):
        return {
            k: redact(v)
            for k, v in value.items()
            if k not in _HEAVY_KEYS and not _is_secret_key(k)
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def _normalize_folders(raw: Any) -> list[str]:
    """`userSelectedFolders` is inconsistently typed -- sometimes bare path
    strings, sometimes `{path: ...}` dicts. Normalize to a flat path list."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for entry in raw:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
            out.append(entry["path"])
    return out


_CUSTOM_INSTRUCTIONS_TAG_RE = re.compile(
    r"<user_preferences>(.*?)</user_preferences>", re.DOTALL
)
_CUSTOM_INSTRUCTIONS_MAX_CHARS = 4000


def _extract_custom_instructions(renderer_appends: Any) -> str | None:
    """`systemPromptRendererAppends` is a list of rendered prompt-injection
    blocks; one of them, when the account has global custom instructions
    set, is a literal `<user_preferences>...</user_preferences>` block
    (confirmed directly against a real file). Returns the **full,
    untruncated** text -- callers decide their own truncation, since a
    length-based finding (see `build_instructions_inventory`) needs the real
    character count, not a capped one.

    Deliberately does not extract `<project_instructions>` from this same
    field -- that's a per-session duplicate of data already captured
    authoritatively elsewhere (Space `instructions` in spaces.json, cloud
    Project `prompt_template`)."""
    if not isinstance(renderer_appends, list):
        return None
    for entry in renderer_appends:
        if not isinstance(entry, str):
            continue
        match = _CUSTOM_INSTRUCTIONS_TAG_RE.search(entry)
        if match:
            return match.group(1).strip()
    return None


def _truncate(text: str | None, max_chars: int = _CUSTOM_INSTRUCTIONS_MAX_CHARS) -> str | None:
    if text is None or len(text) <= max_chars:
        return text
    return text[:max_chars] + " ...[truncated]"


_INITIAL_MESSAGE_PREVIEW_CHARS = 400
_MAX_NAME_LIST = 200
# Defined here rather than beside the transcript scanner because it is used
# as a default argument below, which is evaluated at definition time.
_MAX_URL_HOSTS = 60

# `enabledMcpTools` is keyed `local:<Server display name>:<tool>` with a
# *bool* value -- confirmed against a real session. Two consequences worth
# knowing before using it: a key being present does not mean the tool was
# enabled (check the value), and the server segment is a display label
# ("Control Chrome"), not the `mcp__<server>__<tool>` slug transcripts use
# ("claude-in-chrome"). The two therefore do NOT join on a shared
# identifier -- match them by eye, or not at all.
_ENABLED_TOOL_KEY_PARTS = 3


def _capped_strings(raw: Any, limit: int = _MAX_NAME_LIST) -> list[str]:
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, str) and entry][:limit]


def _length_of(raw: Any) -> int | None:
    """Size of a str/list/dict, or None when the field is simply absent --
    a distinction that matters, since 0 would read as "empty" rather than
    "this session doesn't record it"."""
    if isinstance(raw, (str, list, dict)):
        return len(raw)
    return None


def _names_of(raw: Any, limit: int = _MAX_NAME_LIST) -> list[str]:
    """Names out of the several shapes these config fields actually take:
    a `{name: enabled}` mapping (only the enabled ones count), a list of
    `{"name": ...}` dicts, or a plain list of strings."""
    if isinstance(raw, dict):
        return sorted(k for k, v in raw.items() if isinstance(k, str) and v)[:limit]
    if isinstance(raw, list):
        names: list[str] = []
        for entry in raw:
            if isinstance(entry, str) and entry:
                names.append(entry)
            elif isinstance(entry, dict) and isinstance(entry.get("name"), str):
                names.append(entry["name"])
        return names[:limit]
    return []


def _enabled_mcp_server_labels(raw: Any) -> list[str]:
    """Server display labels from `enabledMcpTools`' `local:<Server>:<tool>`
    keys. Server labels can contain spaces and colons are the delimiter, so
    split with a bounded `split` and take the middle field."""
    if not isinstance(raw, dict):
        return []
    labels: set[str] = set()
    for key, enabled in raw.items():
        if not enabled or not isinstance(key, str):
            continue
        parts = key.split(":", _ENABLED_TOOL_KEY_PARTS - 1)
        if len(parts) == _ENABLED_TOOL_KEY_PARTS and parts[1]:
            labels.add(parts[1])
    return sorted(labels)


def _plugin_names_of(slash_commands: Any) -> list[str]:
    """`pluginInstallPaths` points at hashed temp directories
    (`.../claude-hostloop-plugins/4bc8832a52bf73d1`), so its basenames are
    not usable names. `slashCommands` entries are `<plugin>:<command>`
    strings, which is where a readable plugin name actually comes from."""
    if not isinstance(slash_commands, list):
        return []
    names: set[str] = set()
    for entry in slash_commands:
        if not isinstance(entry, str):
            continue
        plugin, delimiter, _ = entry.partition(":")
        if delimiter and plugin:
            names.add(plugin)
    return sorted(names)


def _hosts_of(raw: Any, limit: int = _MAX_URL_HOSTS) -> list[str]:
    if not isinstance(raw, list):
        return []
    hosts: set[str] = set()
    for entry in raw:
        if isinstance(entry, str) and (host := _url_host(entry)):
            hosts.add(host)
    return sorted(hosts)[:limit]


def _load_json(path: Path) -> Any | None:
    """Read one JSON file, or None. The single choke point every reader
    here goes through, so every failure mode a file on someone else's disk
    can present has to end up as None rather than an exception:

    - `OSError` -- absent, a directory, no permission, a dead symlink.
    - `json.JSONDecodeError` -- truncated (an app killed mid-write leaves
      exactly this), or a shape change that isn't valid JSON at all.
    - `UnicodeDecodeError` -- bytes that aren't UTF-8. Not hypothetical:
      a partially-written or disk-corrupted file lands here rather than in
      the decoder, and it is *not* a `JSONDecodeError`, so catching only
      the two above took down the whole tool call on one bad byte.
    - `RecursionError` -- pathological nesting depth in a hostile or
      corrupt file.

    `ValueError` would cover the middle two in one line; they are named
    individually because the guarantee this function makes is worth
    stating rather than inferring."""
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return None


# --------------------------------------------------------------------------------
# Transcript scanning -- Cowork's nested per-session transcripts (see
# read_cowork_session_transcripts below) are JSONL: one JSON object per line,
# assistant turns carrying `message.model`. Confirmed directly against real
# Cowork session transcripts -- a session isn't necessarily one model
# throughout; sub-agents/background steps can run a cheaper model mid-session,
# so this tallies model usage rather than assuming a single value per session.
# --------------------------------------------------------------------------------

_MODEL_TALLY_MAX_LINES = 20_000

# Past `_MODEL_TALLY_MAX_LINES` the scan keeps going, but only parses every
# Nth line. Taking a flat "first N lines" prefix biases every distribution
# below toward the *start* of a session -- which is exactly wrong for the
# long, agentic Cowork sessions this plugin cares most about, where tool use
# and model switches accumulate later. Sampling the tail keeps the scan
# bounded while still seeing the whole file; `sampled` says it happened so a
# caller can qualify a finding rather than quietly over-trusting it.
_TAIL_SAMPLE_STRIDE = 10

# Tool-call inputs that name a filesystem path or a URL. Only the *shape* of
# what was reached is kept (parent directory, URL host) -- never file
# contents, never a full URL with its query string.
_TOOL_PATH_INPUT_KEYS = ("file_path", "path", "notebook_path")
_TOOL_URL_INPUT_KEYS = ("url",)

_MAX_TOUCHED_DIRS = 60


def _model_of_transcript_record(record: dict[str, Any]) -> str | None:
    """Assistant-turn records carry the model under `message.model`; check
    a top-level `model` too, defensively -- never look at message content."""
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("model"), str):
        return message["model"]
    if isinstance(record.get("model"), str):
        return record["model"]
    return None


_MCP_TOOL_PREFIX = "mcp__"


def _mcp_server_of(tool_name: str) -> str | None:
    """`mcp__<server>__<tool>` is the naming convention for a tool coming
    from an MCP server -- confirmed against real transcripts (e.g.
    `mcp__claude-in-chrome__navigate`). That makes the tool tally double as
    a record of which *connectors* a session actually used, with no separate
    lookup. A bare name (`Read`, `Edit`) is a built-in, not a connector, and
    returns None. Server names can themselves contain single underscores, so
    split on the `__` delimiter rather than on `_`."""
    if not tool_name.startswith(_MCP_TOOL_PREFIX):
        return None
    remainder = tool_name[len(_MCP_TOOL_PREFIX) :]
    server, delimiter, _ = remainder.partition("__")
    if not delimiter or not server:
        return None
    return server


def _mcp_servers_of(tool_counts: Counter[str]) -> set[str]:
    return {
        server for name in tool_counts if (server := _mcp_server_of(name)) is not None
    }


def _url_host(value: str) -> str | None:
    """Host only -- a full URL carries paths and query strings that are user
    data; the host alone answers "a site you didn't mention"."""
    try:
        host = urlparse(value).hostname
    except ValueError:
        return None
    return host or None


def _harvest_tool_use(block: dict[str, Any], out: _TranscriptScan) -> None:
    """Record one `tool_use` content block: its tool name, plus the parent
    directory of any path input and the host of any URL input. Confirmed
    shape -- `{"type": "tool_use", "name": ..., "input": {...}}` -- against
    real Cowork transcripts."""
    name = block.get("name")
    if isinstance(name, str) and name:
        out.tool_counts[name] += 1

    raw_input = block.get("input")
    if not isinstance(raw_input, dict):
        return

    for key in _TOOL_PATH_INPUT_KEYS:
        value = raw_input.get(key)
        if isinstance(value, str) and value and len(out.touched_dirs) < _MAX_TOUCHED_DIRS:
            # Platform-native flavour on purpose: this is the local machine's
            # own data, so a Windows path must parse as one. PurePosixPath
            # would read `C:\Users\x\f.txt` as a single component and report
            # its parent as `.`.
            out.touched_dirs.add(str(PurePath(value).parent))

    for key in _TOOL_URL_INPUT_KEYS:
        value = raw_input.get(key)
        if isinstance(value, str) and value and len(out.url_hosts) < _MAX_URL_HOSTS:
            host = _url_host(value)
            if host:
                out.url_hosts.add(host)


@dataclass
class _TranscriptScan:
    """Everything one pass over a transcript collects. Counts and names only
    -- no message text, no file contents, no full URLs."""

    model_counts: Counter[str] = field(default_factory=Counter)
    tool_counts: Counter[str] = field(default_factory=Counter)
    touched_dirs: set[str] = field(default_factory=set)
    url_hosts: set[str] = field(default_factory=set)
    message_count: int = 0
    line_count: int = 0
    sampled: bool = False

    def merge(self, other: _TranscriptScan) -> None:
        self.model_counts.update(other.model_counts)
        self.tool_counts.update(other.tool_counts)
        self.touched_dirs |= other.touched_dirs
        self.url_hosts |= other.url_hosts
        self.message_count += other.message_count
        self.line_count += other.line_count
        self.sampled = self.sampled or other.sampled


def _scan_jsonl(path: Path, max_model_lines: int) -> _TranscriptScan:
    """One pass over a JSONL transcript collecting the signals in
    `_TranscriptScan`: the per-message model tally, which tools were
    actually *invoked* (as opposed to merely enabled -- see
    `read_local_sessions`' `enabled_mcp_tool_names`), how many messages
    there were, and the directories and URL hosts the session reached.

    Never reads message text, file contents, or full URLs. The exact total
    line count is always returned -- iterating text lines is cheap even past
    the cap; only the JSON-parse work is bounded, and past the cap it
    continues at `_TAIL_SAMPLE_STRIDE` rather than stopping, so the result
    describes the whole file instead of just its opening.

    Raises `OSError` if `path` can't be opened at all -- callers decide
    whether that means skipping just this file's contribution or the
    whole session it belongs to; a malformed *line* within an openable
    file is always skipped silently instead, never raised."""
    scan = _TranscriptScan()
    with path.open(encoding="utf-8") as f:
        for line_number, line in enumerate(f, start=1):
            scan.line_count = line_number
            if line_number > max_model_lines:
                if line_number % _TAIL_SAMPLE_STRIDE:
                    continue
                scan.sampled = True
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(record, dict):
                continue

            model = _model_of_transcript_record(record)
            if model:
                scan.model_counts[model] += 1

            if record.get("type") in ("assistant", "user"):
                scan.message_count += 1

            message = record.get("message")
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    _harvest_tool_use(block, scan)
    return scan


# --------------------------------------------------------------------------------
# Desktop / Cowork readers
# --------------------------------------------------------------------------------


def read_spaces(base_dir: Path) -> list[dict[str, Any]]:
    """Every `local-agent-mode-sessions/<account>/<org>/spaces.json` under
    base_dir -- folder-bound ("Local") Projects, called Spaces internally.
    Confirmed real shape is `{"spaces": [...]}`, not a bare list -- accept a
    bare list too, defensively, since this is reverse-engineered and
    unversioned (same posture as `_normalize_folders` below)."""
    spaces: list[dict[str, Any]] = []
    for spaces_path in sorted(base_dir.glob("local-agent-mode-sessions/*/*/spaces.json")):
        data = _load_json(spaces_path)
        if isinstance(data, dict):
            data = data.get("spaces")
        if not isinstance(data, list):
            continue
        for entry in data:
            if not isinstance(entry, dict):
                continue
            spaces.append(
                {
                    "id": entry.get("id"),
                    "name": entry.get("name"),
                    "description": entry.get("description"),
                    "instructions": entry.get("instructions"),
                    "folders": _normalize_folders(entry.get("folders")),
                    "source_file": str(spaces_path),
                }
            )
    return spaces


def read_project_cache(base_dir: Path) -> list[dict[str, Any]]:
    """Cached metadata for non-folder-bound cloud Projects opened recently
    from this device: `.project-cache/<uuid>/metadata.json`.
    `prompt_template` is that Project's custom instructions text, confirmed
    present directly on a real file -- the cloud-Project equivalent of a
    Space's `instructions` field in `spaces.json`."""
    projects: list[dict[str, Any]] = []
    for meta_path in sorted(
        base_dir.glob("local-agent-mode-sessions/*/*/.project-cache/*/metadata.json")
    ):
        data = _load_json(meta_path)
        if not isinstance(data, dict):
            continue
        projects.append(
            {
                "uuid": data.get("uuid"),
                "name": data.get("name"),
                "description": data.get("description"),
                # `synced_at` is written as an ISO string. It still gets the
                # standard rendering, because "written as ISO" says nothing
                # about which zone it is in, and this is a date a caller is
                # told to quote ("last synced on ...").
                "synced_at": data.get("synced_at"),
                **_instant_fields("synced_at", data.get("synced_at")),
                "prompt_template": data.get("prompt_template"),
                "source_file": str(meta_path),
            }
        )
    return projects


# --------------------------------------------------------------------------------
# Local memory -- what Claude has *remembered*, and where it actually lives
# --------------------------------------------------------------------------------
#
# Memory is **not** export-only. Three distinct stores sit on this disk,
# each confirmed by direct inspection on a real macOS install:
#
#   local-agent-mode-sessions/<account>/<org>/memory/*.md
#       Account-level memory -- `CLAUDE.md` on the install this was
#       confirmed against. The *directory* is globbed rather than that one
#       filename, because nothing guarantees the name and a reader that
#       hard-codes it goes silently empty the day it changes.
#   local-agent-mode-sessions/<account>/<org>/spaces/<space-id>/memory/*.md
#       Per-Space memory: one directory per folder-bound Space, holding
#       several `.md` files each (a `MEMORY.md` index plus topic files, on
#       a real install). `<space-id>` is the same `id` `spaces.json`
#       carries, so `read_spaces` already supplies the name join.
#   local-agent-mode-sessions/<account>/<org>/.project-cache/<uuid>/memory.md
#       Per-cloud-Project memory, keyed by the same `<uuid>`
#       `read_project_cache` already joins on.
#
# Earlier research concluded memory content was available only from an
# account export. That was wrong, and it mattered: the default local-only
# run of this plugin had no memory lens at all while "review my Claude
# memory" was a shipped trigger phrase.
#
# This is the user's own remembered context -- customers, health, travel,
# family, business detail. It gets the same handling as every other
# sensitive body of text here: read-only, capped, counted, never dumped
# wholesale, and never written back.

_MEMORY_DEFAULT_MAX_CHARS = 2000
_MEMORY_MAX_FILES = 200
_MEMORY_MAX_FILE_BYTES = 1_000_000
_MEMORY_STALE_DAYS = 90.0

# `**/*.md` rather than `*.md`: `**` also matches zero directories, so this
# keeps the confirmed flat layout working while still finding a file if a
# future build nests topic files one level down. The Space and cloud-Project
# patterns are anchored under `spaces/` and `.project-cache/`, so neither can
# reach the unrelated `memory` directories a bundled Python environment has
# under `rpm/.../site-packages/` (confirmed present on this machine).
_MEMORY_SOURCES = (
    ("account", "local-agent-mode-sessions/*/*/memory/**/*.md"),
    ("space", "local-agent-mode-sessions/*/*/spaces/*/memory/**/*.md"),
    ("cloud_project", "local-agent-mode-sessions/*/*/.project-cache/*/memory.md"),
)


def _memory_owner_id(path: Path, scope: str) -> str | None:
    """The Space id / cloud-Project uuid a memory file belongs to, taken
    from the path rather than from any field inside the file -- the
    directory name *is* the join key, and the file itself is free-form
    markdown with nothing to join on."""
    if scope == "cloud_project":
        return path.parent.name
    if scope == "space":
        parts = path.parts
        try:
            index = len(parts) - 1 - parts[::-1].index("spaces")
        except ValueError:
            return None
        return parts[index + 1] if index + 1 < len(parts) else None
    return None


def _memory_file_entry(
    path: Path, scope: str, max_chars: int, now_ms: float
) -> dict[str, Any]:
    """One memory file, measured and capped.

    Same defensive posture as `_load_json`: a file that is absent between
    the glob and the read, unreadable, not UTF-8, or implausibly large
    comes back as an entry with `readable: false` and a `problem`, never as
    an exception and never as a silent omission -- a memory file that
    exists but couldn't be read is itself worth reporting."""
    text: str | None = None
    size_bytes: int | None = None
    modified_ms: float | None = None
    problem: str | None = None
    try:
        stat = path.stat()
        size_bytes = stat.st_size
        modified_ms = stat.st_mtime * 1000.0
    except OSError:
        problem = "unreadable"
    if problem is None and size_bytes is not None and size_bytes > _MEMORY_MAX_FILE_BYTES:
        problem = "too_large"
    if problem is None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            problem = "unreadable"

    return {
        "scope": scope,
        "owner_id": _memory_owner_id(path, scope),
        "file_name": path.name,
        "source_file": str(path),
        "readable": problem is None,
        "problem": problem,
        "size_bytes": size_bytes,
        "char_count": len(text) if text else 0,
        "line_count": len(text.splitlines()) if text else 0,
        # Memory files carry no timestamp of their own, so the filesystem
        # mtime is the only staleness signal there is. It is the moment
        # Claude last *wrote* this file, which is what "is this memory
        # still current" actually turns on.
        "modified_at_ms": int(modified_ms) if modified_ms is not None else None,
        **_instant_fields("modified_at", modified_ms),
        "days_since_modified": (
            round((now_ms - modified_ms) / _MS_PER_DAY, 1)
            if modified_ms is not None
            else None
        ),
        "text": _truncate(text, max_chars),
        "truncated": bool(text and len(text) > max_chars),
    }


def clamp_max_chars(value: Any, default: int) -> int:
    """A caller-supplied character cap, forced back into a range that
    actually caps something.

    `_truncate` compares `len(text) <= max_chars` and then slices
    `text[:max_chars]`, so a **negative** cap doesn't truncate -- it slices
    from the end and hands back all but the last few characters of the
    file. On the memory path that is the whole of the user's remembered
    business and personal context escaping a cap that looks like it is
    holding. A non-integer or missing value falls back to the default; a
    negative one becomes 0, which is the honest reading of "cap this at
    less than nothing"."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(0, value)


def _read_memory_files(base_dir: Path, max_chars: int) -> dict[str, Any]:
    now_ms = datetime.now(UTC).timestamp() * 1000.0
    matched: list[tuple[str, Path]] = [
        (scope, path)
        for scope, pattern in _MEMORY_SOURCES
        for path in sorted(base_dir.glob(pattern))
    ]
    files = [
        _memory_file_entry(path, scope, max_chars, now_ms)
        for scope, path in matched[:_MEMORY_MAX_FILES]
    ]
    return {
        "files": files,
        "found_count": len(matched),
        "read_count": len(files),
    }


def read_memory_files(
    base_dir: Path, max_chars: int = _MEMORY_DEFAULT_MAX_CHARS
) -> dict[str, Any]:
    """Every local memory file under `base_dir`, from all three stores.

    Returns `{"files": [...], "found_count": int, "read_count": int}`.
    `found_count` is how many files the globs matched and `read_count` how
    many were actually returned -- they differ only when
    `_MEMORY_MAX_FILES` bites, and the caller says so rather than
    presenting a truncated list as a complete one.

    `max_chars` caps each file's returned `text` and is clamped (see
    `clamp_max_chars`); `char_count` is always the true, uncapped length.

    Routed through `redact` as a thin wrapper over the real reader, the
    same way `build_plan_usage_history` is, so the guarantee covers every
    return path including any degraded one added later rather than however
    many inline calls someone remembered to write. This reader especially:
    it handles the most sensitive text this plugin ever touches -- Claude's
    own remembered notes about the user's business, health, family and
    customers -- and it was the one new reader that shipped outside the
    filter."""
    capped = clamp_max_chars(max_chars, _MEMORY_DEFAULT_MAX_CHARS)
    return redact(_read_memory_files(base_dir, capped))


# --------------------------------------------------------------------------------
# Remote session -> Space lookup
# --------------------------------------------------------------------------------
#
# `claude_desktop_config.json`'s `remoteSessionFolderGrants` is keyed by
# raw `session_01...` ids. A non-technical user cannot act on one of those:
# it names nothing they have ever seen in the app. This file is the lookup
# table that turns each id into a Space, which they have:
#
#   local-agent-mode-sessions/<account>/<org>/remote-session-spaces.json
#   {"entries": [{"sessionId", "spaceId", "folders", "memoryEnabled"}]}
#
# Confirmed on a real install: 6 entries, every `spaceId` resolvable
# through `spaces.json`.

REMOTE_SESSION_SPACES_FILENAME = "remote-session-spaces.json"


def read_remote_session_spaces(base_dir: Path) -> dict[str, Any]:
    """The `session_01...` -> Space lookup, keyed by session id.

    Only the ids and the Space link are taken. This file is a routing
    table, not a transcript -- there is nothing in it worth reporting for
    its own sake, and everything it holds is only useful joined to
    something a person recognizes."""
    by_session_id: dict[str, dict[str, Any]] = {}
    source_files: list[str] = []
    for path in sorted(
        base_dir.glob(f"local-agent-mode-sessions/*/*/{REMOTE_SESSION_SPACES_FILENAME}")
    ):
        data = _load_json(path)
        raw = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(raw, list):
            continue
        source_files.append(str(path))
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            session_id = entry.get("sessionId")
            if not isinstance(session_id, str):
                continue
            by_session_id[session_id] = {
                "session_id": session_id,
                "space_id": entry.get("spaceId"),
                "folders": _normalize_folders(entry.get("folders")),
                "memory_enabled": entry.get("memoryEnabled"),
                "source_file": str(path),
            }
    return {
        "available": bool(source_files),
        "source_files": source_files,
        "entry_count": len(by_session_id),
        "by_session_id": by_session_id,
    }


# --------------------------------------------------------------------------------
# Scheduled tasks
# --------------------------------------------------------------------------------
#
#   local-agent-mode-sessions/<account>/<org>/scheduled-tasks.json
#   {"scheduledTasks": [], "recordedSkips": {},
#    "sundayAliasBoundaryStamped": bool, "dayFieldsOrBoundaryStamped": bool}
#
# Earlier research recorded, as a confirmed hard boundary, that nothing
# schedule-shaped is stored on disk because scheduled tasks execute
# server-side. That is false: the file and its schema exist. It is empty on
# the install this was confirmed against, which is almost certainly how an
# empty array got read as an absent feature -- a mistake worth naming,
# because it is the failure mode of every reverse-engineered reader here.
#
# What this reader must NOT do is over-correct. `scheduledTasks` has never
# been observed populated, so the shape of a task *entry* is unverified.
# Entries are therefore passed through verbatim-but-redacted and flagged as
# unverified, and no field of an entry is named, parsed, or promised.
#
# CRITICAL: an identically-named file exists under `claude-code-sessions/`.
# That is the Claude Code CLI surface, out of scope for this plugin
# entirely (see the module docstring). The glob below is anchored to
# `local-agent-mode-sessions/` and must stay that way.

SCHEDULED_TASKS_FILENAME = "scheduled-tasks.json"
_SCHEDULED_TASKS_KNOWN_KEYS = (
    "scheduledTasks",
    "recordedSkips",
    "sundayAliasBoundaryStamped",
    "dayFieldsOrBoundaryStamped",
)
_SCHEDULED_TASKS_MAX_ENTRIES = 50
# ...and a cap on how big any one entry may be. Capping the *count* alone
# bounded nothing that matters: a scheduled task carries the prompt text it
# runs, so a single populated entry can be arbitrarily large, and it comes
# through verbatim precisely because its shape is unverified. An entry over
# the cap is replaced by a measurement of itself -- no field of it named,
# no fragment of it quoted, which would be a partial verbatim dump under
# another name.
_SCHEDULED_TASK_MAX_ENTRY_CHARS = 2000


def _capped_task_entry(entry: Any) -> tuple[Any, bool]:
    """One task entry, or a measurement of it when it is too big to pass
    through. Returns `(entry, was_replaced)`."""
    size = _json_size(entry)
    if size is None or size <= _SCHEDULED_TASK_MAX_ENTRY_CHARS:
        return entry, False
    return {
        "omitted": True,
        "reason": (
            "This scheduled-task entry is larger than the "
            f"{_SCHEDULED_TASK_MAX_ENTRY_CHARS}-character per-entry cap, so it was "
            "measured instead of returned. Task entries come through verbatim "
            "because their shape is unverified, and a task carries whatever prompt "
            "text it runs -- which can be any size at all. Nothing here names or "
            "quotes a field of it."
        ),
        "size_chars": size,
        "top_level_field_count": len(entry) if isinstance(entry, dict) else None,
    }, True


def read_scheduled_tasks(base_dir: Path) -> dict[str, Any]:
    """`scheduled-tasks.json`, parsed defensively and asserting nothing
    about a task entry's shape.

    Returns what can actually be established: whether the file exists, how
    many tasks it lists, the identifiers under `recordedSkips`, the two
    boundary-stamp flags, and any top-level field this plugin doesn't
    recognize. `task_entry_shape_verified` is `False` and stays `False`
    until someone confirms a populated array on a real install.

    An empty `scheduledTasks` is reported as an empty list, never as "this
    feature does not exist" -- that inference is exactly the one that put a
    false boundary in the docs in the first place.

    Two caps, not one. `_SCHEDULED_TASKS_MAX_ENTRIES` bounds how many
    entries come back (`tasks_truncated`); `_SCHEDULED_TASK_MAX_ENTRY_CHARS`
    bounds how big any one of them may be (`oversized_task_count`). Capping
    the count alone bounded nothing that matters -- a task carries the
    prompt text it runs, so one populated entry can be arbitrarily large,
    and entries pass through verbatim precisely because their shape is
    unverified."""
    files: list[dict[str, Any]] = []
    tasks: list[Any] = []
    skip_ids: list[str] = []
    unrecognized_fields: list[str] = []
    flags: dict[str, Any] = {}
    truncated = False

    for path in sorted(
        base_dir.glob(f"local-agent-mode-sessions/*/*/{SCHEDULED_TASKS_FILENAME}")
    ):
        data = _load_json(path)
        if not isinstance(data, dict):
            files.append({"source_file": str(path), "readable": False})
            continue
        raw_tasks = data.get("scheduledTasks")
        raw_tasks = raw_tasks if isinstance(raw_tasks, list) else []
        raw_skips = data.get("recordedSkips")
        raw_skips = raw_skips if isinstance(raw_skips, dict) else {}
        files.append(
            {
                "source_file": str(path),
                "readable": True,
                "scheduled_task_count": len(raw_tasks),
                "recorded_skip_count": len(raw_skips),
            }
        )
        tasks.extend(raw_tasks)
        skip_ids.extend(str(k) for k in raw_skips)
        for name in ("sundayAliasBoundaryStamped", "dayFieldsOrBoundaryStamped"):
            if name in data:
                flags[name] = data[name]
        unrecognized_fields.extend(
            name for name in data if name not in _SCHEDULED_TASKS_KNOWN_KEYS
        )

    if len(tasks) > _SCHEDULED_TASKS_MAX_ENTRIES:
        tasks = tasks[:_SCHEDULED_TASKS_MAX_ENTRIES]
        truncated = True

    # Redact first, then measure: the cap is on what would actually be
    # returned, and a secret-shaped key must not be able to buy an entry
    # its way past the size check either.
    capped: list[Any] = []
    oversized = 0
    for entry in redact(tasks):
        entry, replaced = _capped_task_entry(entry)
        capped.append(entry)
        oversized += int(replaced)

    return {
        "available": any(f["readable"] for f in files),
        "file_count": len(files),
        "files": files,
        "scheduled_task_count": sum(
            f.get("scheduled_task_count", 0) for f in files if f["readable"]
        ),
        # Verbatim apart from redaction and the per-entry size cap, because
        # naming fields inside an entry would be inventing a schema no one
        # has seen.
        "tasks": capped,
        "tasks_truncated": truncated,
        "oversized_task_count": oversized,
        "max_task_entry_chars": _SCHEDULED_TASK_MAX_ENTRY_CHARS,
        "task_entry_shape_verified": False,
        "recorded_skip_count": len(skip_ids),
        "recorded_skip_ids": sorted(set(skip_ids)),
        "boundary_flags": flags,
        "unrecognized_top_level_fields": sorted(set(unrecognized_fields)),
    }


def read_local_sessions(base_dir: Path) -> list[dict[str, Any]]:
    """Cowork + Chat-tab session metadata (`local_<uuid>.json`), redacted.
    Deliberately excludes `claude-code-sessions/` -- that's the Code tab
    (CLI-in-Desktop), out of scope for this plugin along with the rest of
    the Claude Code surface (see the module docstring).
    `default_model`/`effort` are this session's *configured default*
    (confirmed present at the top level of this file) -- not necessarily
    what every message actually used if it was changed mid-session; join
    against `read_cowork_session_transcripts` for per-message accuracy.

    `effort` reads the on-disk key **`effortOverride`**. There is no
    `effort` key: across 67 real session files it was present 0 times, while
    `effortOverride` was present 40 times. The response key stays `effort`
    because that is what the skill docs and callers already reference; only
    the source key was ever wrong. Absent on a majority of sessions, so
    `None` here means "not set", not "not readable".

    `memory_enabled`/`skills_enabled`/`plugins_enabled` and
    `custom_instructions` (the account's global custom instructions text,
    extracted from `systemPromptRendererAppends`) are this same session's
    stored configuration/environment fields, confirmed present directly on
    a real file -- these are per-session (same pattern as `default_model`),
    not a separate aggregated "account settings" object, so a caller can see
    directly if a value disagrees across sessions rather than trusting one
    collapsed value.

    The configuration fields below describe what a session was *able* to do.
    What it actually did lives in `read_cowork_session_transcripts`; the two
    are meant to be crossed, not confused:

    - `permission_mode` -- Manual/Auto/Skip. A cost signal, not just a
      safety one: Auto consumes more usage than the others because of its
      extra per-action safety-checking pass.
    - `enabled_mcp_tool_names` / `remote_mcp_server_names` / `plugin_names`
      / `slash_command_names` -- the capability surface that was switched
      on. Names only: the raw `enabledMcpTools`/`remoteMcpServersConfig`
      blobs carry full tool schemas and are stripped by `redact` (see
      `_HEAVY_KEYS`), which is why these are summarized here under separate
      keys rather than passed through.
    - `egress_allowed_domains` / `web_fetch_allowed_url_hosts` -- the
      session's network reach, hosts only.
    - `fs_detected_file_count` / `cwd` -- filesystem footprint.
    - `system_prompt_char_count` / `memory_guidelines_char_count` -- sizes
      only, never content. Both are large (~49k and ~13.5k characters
      respectively on a real session) and both are Anthropic's own
      scaffolding that the user cannot edit, so their *text* would produce
      no actionable finding while pulling a lot of sensitive material into
      context. The counts still matter: they are what every turn pays
      before any of the user's own instructions are added.

    Field presence varies by session and was verified on a single machine
    (67 sessions). Treat a missing field as normal and never let one
    produce a finding on its own."""
    sessions: list[dict[str, Any]] = []
    for session_path in sorted(base_dir.glob("local-agent-mode-sessions/*/*/local_*.json")):
        data = _load_json(session_path)
        if not isinstance(data, dict):
            continue
        sessions.append(
            {
                "id": session_path.stem,
                "title": data.get("title"),
                "created_at": data.get("createdAt"),
                "last_activity_at": data.get("lastActivityAt"),
                # Both raw fields above are epoch milliseconds. The `_iso`
                # companions exist so a caller can read and present a date
                # without re-deriving that -- getting it wrong silently
                # yields 1970, which looks like real (very stale) data.
                # The `_local_*` ones are the same instants on this
                # machine's clock, and are the ones to quote: "you started
                # this chat on Thursday" is false if Thursday was worked out
                # in a zone the reader doesn't live in.
                **_instant_fields("created_at", data.get("createdAt")),
                **_instant_fields("last_activity_at", data.get("lastActivityAt")),
                "is_archived": data.get("isArchived"),
                "user_selected_folders": _normalize_folders(data.get("userSelectedFolders")),
                "user_selected_project_uuids": data.get("userSelectedProjectUuids") or [],
                # The app's OWN record of which Space this chat was filed in,
                # written by Desktop itself and resolvable straight through
                # `spaces.json`. Present on a majority of real sessions (59 of
                # 97 on the install this was confirmed against); absent means
                # "this chat is in no Space", not "unreadable". Earlier
                # versions of this module concluded no such field existed and
                # reconstructed membership from folder prefixes instead, which
                # disagreed with the app on every session that has one -- see
                # `join_local_sessions`.
                "space_id": data.get("spaceId"),
                # Who set it. Observed value on a real install is "user" for
                # every session; carried through because it is the app's own
                # statement of provenance and costs nothing to keep.
                "space_id_set_by": data.get("spaceIdSetBy"),
                "default_model": data.get("model"),
                # `effortOverride` first -- see the docstring. The `effort`
                # fallback is defensive only: it has never been observed on
                # disk, but this layout is a private cache that can change
                # between Desktop versions, so don't hard-fail if it returns.
                "effort": data.get("effortOverride") or data.get("effort"),
                "permission_mode": data.get("permissionMode"),
                "memory_enabled": data.get("memoryEnabled"),
                "skills_enabled": data.get("skillsEnabled"),
                "plugins_enabled": data.get("pluginsEnabled"),
                "custom_instructions": _truncate(
                    _extract_custom_instructions(data.get("systemPromptRendererAppends"))
                ),
                "initial_message": _truncate(
                    data.get("initialMessage"), _INITIAL_MESSAGE_PREVIEW_CHARS
                ),
                # Capability surface -- what was switched on, names only.
                "enabled_mcp_tool_names": _names_of(data.get("enabledMcpTools")),
                "enabled_mcp_server_labels": _enabled_mcp_server_labels(
                    data.get("enabledMcpTools")
                ),
                "remote_mcp_server_names": _names_of(data.get("remoteMcpServersConfig")),
                "plugin_names": _plugin_names_of(data.get("slashCommands")),
                "plugin_install_count": _length_of(data.get("pluginInstallPaths")),
                "slash_command_names": _capped_strings(data.get("slashCommands")),
                # Reach -- folders come from `user_selected_folders` above.
                "egress_allowed_domains": _capped_strings(data.get("egressAllowedDomains")),
                "web_fetch_allowed_url_hosts": _hosts_of(data.get("webFetchAllowedUrls")),
                "fs_detected_file_count": _length_of(data.get("fsDetectedFiles")),
                "cwd": data.get("cwd"),
                # Always-loaded weight -- sizes only, never the text.
                "system_prompt_char_count": _length_of(data.get("systemPrompt")),
                "memory_guidelines_char_count": _length_of(
                    data.get("memoryGuidelinesTemplate")
                ),
                "source_file": str(session_path),
            }
        )
    return sessions


_COWORK_TRANSCRIPT_GLOB = "local-agent-mode-sessions/*/*/local_*/.claude/projects/*/*.jsonl"


def read_cowork_session_transcripts(base_dir: Path) -> dict[str, dict[str, Any]]:
    """Cowork keeps its own nested per-session transcript at
    `local-agent-mode-sessions/<account>/<org>/local_<uuid>/.claude/
    projects/<encoded-cwd>/*.jsonl` -- confirmed directly, scoped to that
    one session's sandboxed working directory. This is Cowork's own data
    (it happens to share Claude Code's JSONL layout, see `_scan_jsonl`), and
    it's the only per-message-accurate model signal Desktop has. Keyed by
    session id (the `local_<uuid>` directory name, matching
    `read_local_sessions`' `id`) so a caller can join per-message model
    accuracy onto that session's metadata. A session can have more than one
    matching transcript file (e.g. it touched more than one working
    directory); tallies are summed across all of them.

    This is also the only source for what a session *actually did*, as
    opposed to what it was merely configured to be able to do:
    `tools_invoked`/`message_count` here versus
    `enabled_mcp_tool_names`/`plugin_names` in `read_local_sessions`. The
    gap between those two pairs is the point -- see the capability-inventory
    check in references/workspace-checkup.md."""
    scans: dict[str, _TranscriptScan] = {}
    for jsonl_path in sorted(base_dir.glob(_COWORK_TRANSCRIPT_GLOB)):
        session_id = jsonl_path.parents[3].name
        try:
            scan = _scan_jsonl(jsonl_path, _MODEL_TALLY_MAX_LINES)
        except OSError:
            continue
        scans.setdefault(session_id, _TranscriptScan()).merge(scan)

    return {
        session_id: {
            "models_used": dict(scan.model_counts.most_common()),
            "transcript_event_count": scan.line_count,
            "message_count": scan.message_count,
            "tools_invoked": dict(scan.tool_counts.most_common()),
            "mcp_servers_invoked": sorted(_mcp_servers_of(scan.tool_counts)),
            "touched_dirs": sorted(scan.touched_dirs),
            "url_hosts": sorted(scan.url_hosts),
            # True when the transcript ran past `_MODEL_TALLY_MAX_LINES` and
            # its tail was sampled rather than fully parsed. The tallies
            # above still describe the whole file, but under-count the
            # sampled portion -- say so rather than presenting an
            # exact-looking number as if it were complete.
            "transcript_sampled": scan.sampled,
        }
        for session_id, scan in scans.items()
    }


def _best_folder_match(
    session_folders: list[str], space_folders: list[str]
) -> tuple[int, str] | None:
    """The most specific of `space_folders` that contains any of
    `session_folders`, as `(prefix_length, folder)`; `None` when none does.

    Two rules earn their keep here:

    - **Most specific wins.** Spaces nest: an "OneDrive-Personal" Space
      scoped to a whole cloud-storage root contains a "Vacation - July 2026"
      Space several levels below it. Returning the *first* match in file
      order let the broad parent swallow every Space beneath it, so the
      nested ones looked empty. Returning the longest match files the chat
      where the user would.
    - **Match on a path boundary.** "/a/b" contains "/a/b/c" but must not
      claim "/a/bc", which is an unrelated sibling directory.
    """
    best: tuple[int, str] | None = None
    for spf in space_folders:
        if not spf:
            continue
        stem = spf.rstrip("/")
        if not stem:
            # A Space scoped to bare "/" would contain every session on the
            # machine, which is the swallowing failure this function exists
            # to prevent. Skip it rather than file everything under it.
            continue
        for sf in session_folders:
            if not sf:
                continue
            candidate = sf.rstrip("/")
            if candidate == stem or candidate.startswith(stem + "/"):
                if best is None or len(stem) > best[0]:
                    best = (len(stem), spf)
                break
    return best


def _folder_matches_space(session_folders: list[str], space_folders: list[str]) -> bool:
    return _best_folder_match(session_folders, space_folders) is not None


# How a chat ended up attributed to a Space, in words a person who has never
# opened a terminal can act on. `space_source` is the machine-readable half;
# these are what a report should actually print next to a count.
#
# The distinction matters to the reorganization lens: "Claude recorded this"
# is a fact about the user's workspace, while "matched from the folder" is
# this plugin's guess and can be wrong. A dashboard that presents the two the
# same way is overstating what it knows.
SPACE_SOURCE_NOTES = {
    "app_record": (
        "Claude's own record of the Space this chat was filed in -- taken "
        "straight from the app, not worked out afterwards"
    ),
    "inferred_from_folder": (
        "a best guess: Claude recorded no Space for this chat, so it was "
        "matched to the Space that owns the folder the chat was working in"
    ),
    "none": "this chat is not in any Space",
}

# What to say when a Space id can't be turned into a name. A bare uuid names
# nothing the user has ever seen in the app, so never hand one back as if it
# were an answer.
_SPACE_ATTRIBUTION_UNRESOLVED_LABELS = {
    "space_id_not_in_spaces_json": (
        "a Space Claude no longer lists -- most likely deleted in the app "
        "after this chat was filed in it"
    ),
    # Listed, and blank. Kept apart from the line above because telling
    # someone a Space was deleted while it sits unnamed in their sidebar is
    # a wrong answer, not a vaguer one.
    "space_listed_without_a_name": (
        "a Space Claude lists without a name -- it still exists and was never "
        "given one, so there is no name to quote"
    ),
    "no_space": "not in any Space",
}


def _space_attribution(
    space_id: str | None,
    space_name: str | None,
    source: str,
    matched_folder: str | None = None,
    *,
    space_listed: bool = False,
) -> dict[str, Any]:
    """One session's Space attribution, with its provenance attached.

    Same shape and vocabulary as `_resolve_session_space` uses for
    remote-session grants (`space_resolved`/`space_resolution`/
    `space_label`), plus `space_source` -- where the attribution came
    from -- so a caller can tell a recorded fact from an inference.

    `space_listed` separates the two ways a name can be missing: an id
    `spaces.json` doesn't carry at all (deleted), and one it carries with
    no name on it (never named). Both are unresolved; only the first is a
    deletion, and only a caller told which is which can say the right
    sentence."""
    if not space_id:
        resolution = "no_space"
    elif isinstance(space_name, str) and space_name.strip():
        resolution = "matched"
    elif space_listed:
        resolution = "space_listed_without_a_name"
    else:
        resolution = "space_id_not_in_spaces_json"
    return {
        "space_id": space_id,
        "space_name": space_name if resolution == "matched" else None,
        "space_resolved": resolution == "matched",
        "space_resolution": resolution,
        "space_label": (
            space_name
            if resolution == "matched"
            else _SPACE_ATTRIBUTION_UNRESOLVED_LABELS[resolution]
        ),
        "space_source": source,
        "space_source_note": SPACE_SOURCE_NOTES[source],
        # True only when the app itself recorded the Space. False means this
        # is a guess -- say so before presenting it as the user's filing.
        "space_from_app_record": source == "app_record",
        # Which Space folder the guess matched, when it is a guess. `None`
        # for a recorded attribution, so it never reads as evidence.
        "space_matched_folder": matched_folder,
    }


def join_local_sessions(
    sessions: list[dict[str, Any]],
    spaces: list[dict[str, Any]],
    project_cache: list[dict[str, Any]],
) -> dict[str, Any]:
    """Which chat belongs to which Project/Space.

    **The app writes this down.** A session file carries an explicit
    `spaceId` (surfaced as `space_id` by `read_local_sessions`) that resolves
    straight through `spaces.json`, and that record is authoritative here.
    Earlier versions of this function believed no such field existed and
    rebuilt membership by matching folder prefixes; on the install this was
    corrected against, that reconstruction disagreed with the app on every
    one of the 59 sessions that had a recorded Space -- collapsing 36 chats
    filed under a nested Space into the broad cloud-storage-root Space above
    it, and reporting the nested Spaces as empty.

    Precedence, highest first:

    1. `space_id` recorded by the app -- taken as-is, source `app_record`.
    2. `user_selected_project_uuids` -- cloud Project membership. Trusted
       even when a uuid has no `.project-cache` entry yet (never opened on
       this device, or synced after the cache snapshot): the uuid is the real
       signal, the cache only resolves a *name*. Dropping uncached uuids
       previously pushed real project members into `unfiled`.
    3. Folder matching -- only for a session the app recorded no Space for.
       This is a guess and is marked as one (source `inferred_from_folder`).
       It picks the most specific matching Space folder, on a path boundary;
       see `_best_folder_match`.
    4. Otherwise `unfiled`.

    A session lands in exactly one of these buckets, so the four are safe to
    sum. `space_attribution_by_session_id` carries an entry for *every*
    session, including those with no Space at all, so a caller never has to
    guess what a missing key meant. `unresolved_space_ids` lists recorded
    Space ids that `spaces.json` no longer names -- most likely Spaces
    deleted in the app."""
    projects_by_uuid = {p["uuid"]: p for p in project_cache if p.get("uuid")}
    spaces_by_id = {s["id"]: s for s in spaces if s.get("id")}

    by_project: dict[str, list[str]] = {}
    by_space: dict[str, list[str]] = {}
    unfiled: list[str] = []
    uncached_project_uuids: set[str] = set()
    unresolved_space_ids: set[str] = set()
    attribution: dict[str, dict[str, Any]] = {}

    for session in sessions:
        session_id = session["id"]

        recorded_space_id = session.get("space_id")
        if isinstance(recorded_space_id, str) and recorded_space_id:
            space_name = spaces_by_id.get(recorded_space_id, {}).get("name")
            if space_name is None:
                unresolved_space_ids.add(recorded_space_id)
            by_space.setdefault(recorded_space_id, []).append(session_id)
            attribution[session_id] = _space_attribution(
                recorded_space_id,
                space_name,
                "app_record",
                space_listed=recorded_space_id in spaces_by_id,
            )
            continue

        project_uuids = session["user_selected_project_uuids"]
        if project_uuids:
            for uuid in project_uuids:
                by_project.setdefault(uuid, []).append(session_id)
                if uuid not in projects_by_uuid:
                    uncached_project_uuids.add(uuid)
            attribution[session_id] = _space_attribution(None, None, "none")
            continue

        matched_space_id = None
        matched_folder = None
        best_depth = -1
        for space in spaces:
            match = _best_folder_match(session["user_selected_folders"], space["folders"])
            if match is not None and match[0] > best_depth:
                best_depth, matched_folder = match
                matched_space_id = space["id"]
        if matched_space_id:
            by_space.setdefault(matched_space_id, []).append(session_id)
            attribution[session_id] = _space_attribution(
                matched_space_id,
                spaces_by_id.get(matched_space_id, {}).get("name"),
                "inferred_from_folder",
                matched_folder,
                space_listed=matched_space_id in spaces_by_id,
            )
            continue

        unfiled.append(session_id)
        attribution[session_id] = _space_attribution(None, None, "none")

    return {
        "by_project_uuid": by_project,
        "by_space_id": by_space,
        "unfiled_session_ids": unfiled,
        "uncached_project_uuids": sorted(uncached_project_uuids),
        "unresolved_space_ids": sorted(unresolved_space_ids),
        "space_attribution_by_session_id": attribution,
        "projects_by_uuid": projects_by_uuid,
        "spaces_by_id": spaces_by_id,
    }


# --------------------------------------------------------------------------------
# Project/Space membership -- the join key only, nothing else
# --------------------------------------------------------------------------------


def build_project_membership(
    session_ids: list[str] | None = None,
    since: Any = None,
    until: Any = None,
) -> dict[str, Any]:
    """One row per local session (optionally filtered to `session_ids`,
    which match `read_local_sessions`' `id` format -- the same `local_<uuid>`
    format the harness's own `session_info.list_sessions` uses): which cloud
    Project(s)/Space it belongs to, plus its local folder paths -- the join
    key only. Chain `session_info.list_sessions()` -> this -> `read_transcript`
    for only the sessions that actually matter, instead of reading every
    transcript blind (see references/data-sources.md section 3).

    A session can declare more than one cloud Project uuid (the underlying
    join already supports this -- see `join_local_sessions`), so
    `cloud_project_uuids`/`cloud_project_names` are parallel lists rather
    than a single optional field; a `None` in `cloud_project_names` means
    that uuid has no local `.project-cache` entry yet (see
    `uncached_project_uuids`).

    `since`/`until` scope this to sessions active in a time window (see
    `time_window.resolve` for accepted forms); the resolved window and what
    it excluded come back in `time_window`."""
    window = time_window.resolve(since, until)
    base_dir = find_desktop_base_dir()
    if base_dir is None:
        _, empty_summary = time_window.apply(window, [], "created_at", "last_activity_at")
        return {"rows": [], "uncached_project_uuids": [], "time_window": empty_summary}

    sessions = read_local_sessions(base_dir)
    sessions, window_summary = time_window.apply(
        window, sessions, "created_at", "last_activity_at"
    )
    spaces = read_spaces(base_dir)
    project_cache = read_project_cache(base_dir)
    membership = join_local_sessions(sessions, spaces, project_cache)
    projects_by_uuid = membership["projects_by_uuid"]
    attribution = membership["space_attribution_by_session_id"]

    session_project_uuids: dict[str, list[str]] = {}
    for uuid, ids in membership["by_project_uuid"].items():
        for sid in ids:
            session_project_uuids.setdefault(sid, []).append(uuid)

    wanted = set(session_ids) if session_ids is not None else None

    rows: list[dict[str, Any]] = []
    for session in sessions:
        session_id = session["id"]
        if wanted is not None and session_id not in wanted:
            continue

        project_uuids = session_project_uuids.get(session_id, [])

        rows.append(
            {
                "session_id": session_id,
                "cloud_project_uuids": project_uuids,
                "cloud_project_names": [
                    projects_by_uuid.get(uuid, {}).get("name") for uuid in project_uuids
                ],
                # Space attribution and its provenance come from the shared
                # join, not a second lookup -- this row and the matching
                # `list_local_workspace` session now cannot disagree.
                **attribution[session_id],
                "local_folder_paths": session["user_selected_folders"],
                "is_archived": bool(session.get("is_archived")),
            }
        )

    return {
        "rows": rows,
        "uncached_project_uuids": membership["uncached_project_uuids"],
        "unresolved_space_ids": membership["unresolved_space_ids"],
        "space_source_notes": SPACE_SOURCE_NOTES,
        "time_window": window_summary,
    }


# --------------------------------------------------------------------------------
# Top-level inventory
# --------------------------------------------------------------------------------


def _project_fields(entry: dict[str, Any], keep_key: str, fields: set[str]) -> dict[str, Any]:
    """Projection for the `fields` param -- keeps `keep_key` (the join key,
    `id` or `session_id`) regardless of whether it was explicitly requested."""
    return {k: v for k, v in entry.items() if k == keep_key or k in fields}


def build_inventory(
    session_ids: list[str] | None = None,
    project_uuid: str | None = None,
    folder_path: str | None = None,
    fields: list[str] | None = None,
    since: Any = None,
    until: Any = None,
) -> dict[str, Any]:
    """Everything list_local_workspace needs in one call: Desktop
    Spaces/Projects/sessions and the chat->Project/Space join.
    Redacted throughout -- safe to hand to a caller/log.

    Every `local_sessions` entry carries its Space attribution and that
    attribution's provenance (`space_source`, `space_from_app_record`,
    `space_label`, ... -- see `join_local_sessions`), merged from the same
    join `membership` reports and `build_project_membership` returns, so the
    two tools cannot answer the same question differently.

    Optional scoping, applied as a post-filter over the already-read data
    (cheap at this scale -- dozens to low hundreds of sessions, no need to
    push filtering into the disk-read layer). All default to unfiltered
    (current) behavior:
    - `session_ids` -- keep only these `local_sessions`.
    - `project_uuid` -- keep only `local_sessions` whose membership resolves
      to this cloud Project uuid.
    - `folder_path` -- keep only `local_sessions` whose own folders, or
      their matched Space's folders, overlap this path.
    - `fields` -- projection over every `local_sessions` entry; each entry's
      join key (`id`) is always kept regardless of whether it's in
      `fields`.
    - `since`/`until` -- keep only sessions *active* during the window (see
      `time_window.resolve` for accepted forms, and `time_window.overlaps`
      for why this is interval overlap rather than "created within"). This
      one is applied first, before the membership join, so every count
      downstream -- `membership`, the Space/Project groupings -- describes
      the same window rather than a mix. The resolved window and what it
      excluded come back in `time_window`; repeat that alongside any count
      taken from this response."""
    window = time_window.resolve(since, until)
    access = check_data_access()
    result: dict[str, Any] = {
        "access": access,
        # Every session row carries `created_at_local_iso` /
        # `created_at_local_date` / `created_at_local_weekday` (and the same
        # for `last_activity_at`) beside the UTC `*_iso` form. Quote the
        # local ones: this block names the zone they are in.
        "timezone": time_window.local_timezone_label(),
        "local_time_note": _local_time_note(),
        "spaces": [],
        "project_cache": [],
        "local_sessions": [],
        "membership": None,
        "time_window": time_window.apply(window, [], "created_at", "last_activity_at")[1],
    }

    base_dir = find_desktop_base_dir()
    if base_dir is not None:
        spaces = read_spaces(base_dir)
        project_cache = read_project_cache(base_dir)
        local_sessions = read_local_sessions(base_dir)
        local_sessions, result["time_window"] = time_window.apply(
            window, local_sessions, "created_at", "last_activity_at"
        )

        # Join in what each session *actually did*. Every key the transcript
        # scan produces is carried through -- listing them individually here
        # is how the tool-use and reach signals silently went missing once
        # already, since the scan can grow a new field without this loop
        # noticing.
        transcript_signals = read_cowork_session_transcripts(base_dir)
        for session in local_sessions:
            match = transcript_signals.get(session["id"])
            if match:
                session.update(match)

        membership = join_local_sessions(local_sessions, spaces, project_cache)

        # Push the join's own answer back onto every session row. Without
        # this, `local_sessions` carried only the raw `space_id` off disk --
        # null for every chat the app filed by folder rather than by
        # record -- while `get_project_membership` reported a name for the
        # same session. Two tools, one fact, two answers. They read from the
        # same `attribution` map now, so they cannot drift again.
        attribution = membership["space_attribution_by_session_id"]
        for session in local_sessions:
            session.update(attribution[session["id"]])

        if session_ids is not None:
            wanted_ids = set(session_ids)
            local_sessions = [s for s in local_sessions if s["id"] in wanted_ids]

        if project_uuid is not None:
            member_ids = set(membership["by_project_uuid"].get(project_uuid, []))
            local_sessions = [s for s in local_sessions if s["id"] in member_ids]

        if folder_path is not None:
            matched_ids: set[str] = {
                s["id"]
                for s in local_sessions
                if _folder_matches_space(s["user_selected_folders"], [folder_path])
            }
            for space_id, ids in membership["by_space_id"].items():
                space = membership["spaces_by_id"].get(space_id, {})
                if _folder_matches_space(space.get("folders", []), [folder_path]):
                    matched_ids.update(ids)
            local_sessions = [s for s in local_sessions if s["id"] in matched_ids]

        result["spaces"] = spaces
        result["project_cache"] = project_cache
        result["local_sessions"] = [redact(s) for s in local_sessions]
        result["membership"] = membership

    if fields is not None:
        field_set = set(fields)
        result["local_sessions"] = [
            _project_fields(s, "id", field_set) for s in result["local_sessions"]
        ]

    return result


# --------------------------------------------------------------------------------
# Standing-instructions inventory -- the three layers of always-loaded text
# --------------------------------------------------------------------------------
#
# Claude Code's own `/doctor` audits `CLAUDE.md` for bloat and duplication:
# it trims content Claude could derive for itself and moves always-loaded
# guidance into things that load on demand. The Desktop/Cowork equivalent of
# `CLAUDE.md` is standing instructions, and they live in three separate
# places that no single view brings together:
#
#   1. Global custom instructions -- every session, account-wide.
#   2. A Space's `instructions` -- every session in that folder-bound Project.
#   3. A cloud Project's `prompt_template` -- every session in that Project.
#
# This function is the join. It is mechanical on purpose: character counts,
# exact-line overlap, and how many sessions each blob actually reaches. It
# deliberately makes no judgment about whether any given text *should* be
# trimmed -- that's the calling skill's job (see
# references/workspace-checkup.md), and it needs the content to decide.

_DUPLICATE_LINE_MIN_CHARS = 12
_DUPLICATE_SAMPLE_LIMIT = 5


def _normalize_lines(text: str | None) -> list[str]:
    """Lines reduced to a comparable form for exact-duplicate detection:
    whitespace collapsed, lowercased, and anything shorter than
    `_DUPLICATE_LINE_MIN_CHARS` dropped so that blank lines, bare bullets,
    and one-word headings don't register as meaningful duplication."""
    if not text:
        return []
    out: list[str] = []
    for raw in text.splitlines():
        normalized = " ".join(raw.split()).lower()
        if len(normalized) >= _DUPLICATE_LINE_MIN_CHARS:
            out.append(normalized)
    return out


def _overlap(lines: list[str], other: set[str]) -> dict[str, Any]:
    shared = [line for line in dict.fromkeys(lines) if line in other]
    return {
        "line_count": len(shared),
        "char_count": sum(len(line) for line in shared),
        "sample_lines": shared[:_DUPLICATE_SAMPLE_LIMIT],
    }


def _activity_sort_key(value: Any) -> float:
    """Sortable form of `lastActivityAt`/`createdAt`, which are epoch
    **milliseconds as integers** on a real Desktop install (confirmed
    directly), not ISO strings. Anything unrecognized sorts last rather
    than raising -- see `time_window.to_epoch_ms` for the normalization."""
    return time_window.to_epoch_ms(value) or 0.0


def _instructions_entry(text: str | None, max_chars: int) -> dict[str, Any]:
    return {
        "text": _truncate(text, max_chars),
        "char_count": len(text) if text else 0,
        "line_count": len(text.splitlines()) if text else 0,
        "truncated": bool(text and len(text) > max_chars),
    }


_FRONT_MATTER_DELIMITER = "---"


def _memory_content_lines(text: str | None) -> list[str]:
    """`_normalize_lines`, minus the template furniture memory files share.

    Memory files are written to a house template: a YAML front-matter block
    (`name:`/`description:`/`type:`), and section headings written as
    `**Purpose & Context**` rather than with `#`. Both are confirmed on
    real files. Comparing raw normalized lines therefore reports
    `type: project` as "the same thing remembered under four different
    Spaces", which is a finding about the template and not about the user
    -- exactly the kind of confident-sounding noise the cross-topic-drift
    signal exists to avoid producing. Structure is stripped so that what's
    left is remembered *content*.

    Used only for the memory duplication signals. The instruction layers
    keep plain `_normalize_lines`: they are hand-written prose with no
    template behind them, and their existing counts are a shipped
    contract."""
    if not text:
        return []
    lines = text.splitlines()
    # Strip a leading YAML front-matter block, if there is one. Only a
    # *leading* one: a bare `---` further down is a horizontal rule.
    if lines and lines[0].strip() == _FRONT_MATTER_DELIMITER:
        for index in range(1, len(lines)):
            if lines[index].strip() == _FRONT_MATTER_DELIMITER:
                lines = lines[index + 1 :]
                break
    return [
        normalized
        for normalized in (" ".join(raw.split()).lower() for raw in lines)
        if len(normalized) >= _DUPLICATE_LINE_MIN_CHARS
        and not _is_structural_line(normalized)
    ]


def _is_structural_line(normalized: str) -> bool:
    """A markdown heading, a rule, or a whole line of nothing but
    emphasized text -- the last being how these files write section
    headings. Structure, not content."""
    if not normalized or set(normalized) <= set("-=_*# "):
        return True
    if normalized.startswith("#"):
        return True
    body = normalized.rstrip(":").strip()
    return (
        body.startswith(("**", "__"))
        and body.endswith(("**", "__"))
        and len(body) > 4
    )


def _empty_memory_block() -> dict[str, Any]:
    """The `memory` block's shape when nothing could be read at all.

    A fresh dict rather than a module constant: this is handed straight
    back to a caller, and a shared mutable default is the kind of bug that
    only shows up on the second call in a session."""
    return {
        "available": False,
        "file_count": 0,
        "files_found_count": 0,
        "file_list_truncated": False,
        "total_char_count": 0,
        "account": {
            "file_count": 0,
            "char_count": 0,
            "files": [],
            "duplicates_global": _overlap([], set()),
        },
        "spaces": [],
        "cloud_projects": [],
        "repeated_across_memory": [],
        "unreadable_file_count": 0,
        "stale_file_count": 0,
        "stale_after_days": _MEMORY_STALE_DAYS,
        **_instant_fields("newest_modified_at", None),
        **_instant_fields("oldest_modified_at", None),
        "timezone": time_window.local_timezone_label(None),
        "source": "local_disk",
    }


_MEMORY_UNRESOLVED_LABELS = {
    "space_id_not_in_spaces_json": (
        "a Space that is no longer listed in spaces.json -- most likely deleted "
        "in the app while its memory files were left behind on disk"
    ),
    "project_uuid_not_cached": (
        "a cloud Project whose metadata isn't cached on this device -- its memory "
        "is here but its name is only known to the account"
    ),
    "no_owner_id": "an unidentified owner (no id could be read from the file's path)",
    # A Space/Project that IS listed, and carries no name. Distinct from
    # "not listed" on purpose: telling a user a Space was deleted when it
    # is sitting in their sidebar unnamed is a wrong answer, not a vaguer
    # one. This is also the hole a bare uuid used to escape through -- an
    # owner in this state was scored `name_resolved: True` with a `label`
    # of None, and the None fell through to a `str(uuid)` fallback in
    # `repeated_across_memory[].owners`.
    "space_listed_without_a_name": (
        "a Space that Claude lists without a name -- it exists in the app and was "
        "never given one, so there is no name to quote"
    ),
    "project_listed_without_a_name": (
        "a cloud Project cached on this device without a name -- it exists and was "
        "never given one, so there is no name to quote"
    ),
}


def _owner_resolution(owner_id: str | None, names: dict[str, Any], missing: str) -> str:
    """How well one memory owner's id resolves to something a person can
    read. `missing` is the resolution to use when the id isn't in `names`
    at all -- which is a different situation, and a different sentence,
    from being listed under a blank name."""
    if not owner_id:
        return "no_owner_id"
    if owner_id not in names:
        return missing
    name = names[owner_id]
    if not isinstance(name, str) or not name.strip():
        return (
            "project_listed_without_a_name"
            if missing == "project_uuid_not_cached"
            else "space_listed_without_a_name"
        )
    return "matched"


def _memory_owner_entry(
    owner_id: str | None,
    id_key: str,
    name: str | None,
    resolution: str,
    session_count: int,
    files: list[dict[str, Any]],
    global_lines: set[str],
) -> dict[str, Any]:
    """One Space's or cloud Project's memory, grouped and named.

    `label` is always something a person can act on, and is never empty:
    when the id can't be resolved to a name it says *why* -- "a Space that
    is no longer in spaces.json" -- rather than handing back a bare uuid,
    which names nothing the user has ever seen in the app. That includes
    the case where the owner is listed with no name at all, which used to
    produce a `label` of None and let the uuid out through a downstream
    fallback."""
    lines: list[str] = []
    for entry in files:
        lines.extend(_memory_content_lines(entry.get("text")))
    modified = [f["modified_at_ms"] for f in files if f["modified_at_ms"] is not None]
    stale = [
        f
        for f in files
        if f["days_since_modified"] is not None
        and f["days_since_modified"] > _MEMORY_STALE_DAYS
    ]
    return {
        id_key: owner_id,
        "name": name if resolution == "matched" else None,
        "name_resolved": resolution == "matched",
        "label": name if resolution == "matched" else _MEMORY_UNRESOLVED_LABELS[resolution],
        "resolution": resolution,
        "session_count": session_count,
        "file_count": len(files),
        "char_count": sum(f["char_count"] for f in files),
        **_instant_fields("newest_modified_at", max(modified) if modified else None),
        **_instant_fields("oldest_modified_at", min(modified) if modified else None),
        "stale_file_count": len(stale),
        "files": files,
        # The same overlap check the instruction layers get: memory that
        # merely restates the standing instructions is paid for twice.
        "duplicates_global": _overlap(lines, global_lines),
    }


def _memory_block(
    base_dir: Path,
    spaces: list[dict[str, Any]],
    project_cache: list[dict[str, Any]],
    membership: dict[str, Any],
    global_lines: set[str],
    max_chars: int,
) -> tuple[dict[str, Any], list[str]]:
    """The local memory lens: every memory file on this device, attributed
    to the Space or cloud Project that owns it. Returns the block and the
    notes to append to the caller's `notes`."""
    read = read_memory_files(base_dir, max_chars)
    files = read["files"]

    space_names = {s["id"]: s.get("name") for s in spaces if s.get("id")}
    project_names = {p["uuid"]: p.get("name") for p in project_cache if p.get("uuid")}
    space_sessions = {k: len(v) for k, v in membership["by_space_id"].items()}
    project_sessions = {k: len(v) for k, v in membership["by_project_uuid"].items()}

    by_scope: dict[str, dict[str | None, list[dict[str, Any]]]] = {
        "account": {}, "space": {}, "cloud_project": {}
    }
    for entry in files:
        by_scope.setdefault(entry["scope"], {}).setdefault(
            entry["owner_id"], []
        ).append(entry)

    account_files = [f for group in by_scope["account"].values() for f in group]

    space_entries: list[dict[str, Any]] = []
    for owner_id, group in sorted(by_scope["space"].items(), key=lambda kv: str(kv[0])):
        resolution = _owner_resolution(
            owner_id, space_names, "space_id_not_in_spaces_json"
        )
        space_entries.append(
            _memory_owner_entry(
                owner_id,
                "space_id",
                space_names.get(owner_id) if owner_id else None,
                resolution,
                space_sessions.get(owner_id, 0),
                group,
                global_lines,
            )
        )

    project_entries: list[dict[str, Any]] = []
    for owner_id, group in sorted(
        by_scope["cloud_project"].items(), key=lambda kv: str(kv[0])
    ):
        resolution = _owner_resolution(owner_id, project_names, "project_uuid_not_cached")
        project_entries.append(
            _memory_owner_entry(
                owner_id,
                "project_uuid",
                project_names.get(owner_id) if owner_id else None,
                resolution,
                project_sessions.get(owner_id, 0),
                group,
                global_lines,
            )
        )

    account_lines: list[str] = []
    for entry in account_files:
        account_lines.extend(_memory_content_lines(entry.get("text")))

    # The cross-topic-drift signal: the same remembered line turning up
    # under two different Spaces/Projects means memory that was meant to be
    # scoped has spread, which is the finding the checkup is looking for and
    # which no single file can show on its own.
    owners_by_line: dict[str, list[str]] = {}
    # `label` is guaranteed non-empty by `_memory_owner_entry`, so there is
    # no `or str(owner_id)` fallback here any more. That fallback was the
    # last route a bare uuid had into a response: an owner listed with no
    # name scored `matched`, produced a `label` of None, and fell through
    # to its raw id right here -- in the one field a person reads as a list
    # of the Spaces their memory has spread across.
    labelled: list[tuple[str, list[dict[str, Any]]]] = [
        ("account memory", account_files),
        *((e["label"], e["files"]) for e in space_entries),
        *((e["label"], e["files"]) for e in project_entries),
    ]
    for label, group in labelled:
        lines: list[str] = []
        for entry in group:
            lines.extend(_memory_content_lines(entry.get("text")))
        for line in dict.fromkeys(lines):
            owners_by_line.setdefault(line, []).append(label)
    repeated = sorted(
        (
            {"line": line, "owners": owners, "owner_count": len(owners)}
            for line, owners in owners_by_line.items()
            if len(owners) > 1
        ),
        key=lambda r: r["owner_count"],
        reverse=True,
    )

    unreadable = [f for f in files if not f["readable"]]
    stale = [
        f
        for f in files
        if f["days_since_modified"] is not None
        and f["days_since_modified"] > _MEMORY_STALE_DAYS
    ]
    orphaned = [
        e for e in space_entries if e["resolution"] == "space_id_not_in_spaces_json"
    ]
    unnamed = [
        e
        for e in [*space_entries, *project_entries]
        if e["resolution"]
        in ("space_listed_without_a_name", "project_listed_without_a_name")
    ]
    modified = [f["modified_at_ms"] for f in files if f["modified_at_ms"] is not None]

    block = {
        "available": bool(files),
        "file_count": len(files),
        "files_found_count": read["found_count"],
        "file_list_truncated": read["found_count"] > read["read_count"],
        "total_char_count": sum(f["char_count"] for f in files),
        "account": {
            "file_count": len(account_files),
            "char_count": sum(f["char_count"] for f in account_files),
            "files": account_files,
            "duplicates_global": _overlap(account_lines, global_lines),
        },
        "spaces": space_entries,
        "cloud_projects": project_entries,
        "repeated_across_memory": repeated,
        "unreadable_file_count": len(unreadable),
        "stale_file_count": len(stale),
        "stale_after_days": _MEMORY_STALE_DAYS,
        **_instant_fields("newest_modified_at", max(modified) if modified else None),
        **_instant_fields("oldest_modified_at", min(modified) if modified else None),
        "timezone": time_window.local_timezone_label(max(modified) if modified else None),
        "source": "local_disk",
    }

    notes: list[str] = []
    if not files:
        notes.append(
            "No memory files were found on this device. Memory may still exist on "
            "the account -- these files are written by the app as it runs, so a "
            "fresh install, a second machine, or memory turned off for every Space "
            "all look the same here. Absence of files is not proof of absent memory."
        )
    else:
        notes.append(
            f"{len(files)} local memory file(s) totalling "
            f"{sum(f['char_count'] for f in files):,} characters were read from this "
            "device: account-level memory, per-Space memory, and per-cloud-Project "
            "memory. This is Claude's own remembered notes about the user -- it is "
            "not the same thing as the custom instructions above, which the user "
            "wrote deliberately. Memory accumulates on its own, which is exactly why "
            "it is worth reviewing."
        )
        notes.append(
            "Local memory files are the copy this device has, and they are what a "
            "local-only run can see. An account data export also contains memory. "
            "Where the two disagree, **the export is authoritative** -- it comes from "
            "the account, while these files are a per-device cache that is only as "
            "current as the last time the app synced or wrote on this machine. Use "
            "the local files for a same-day review with no export in hand, and say "
            "which source a finding came from."
        )
    if orphaned:
        notes.append(
            f"{len(orphaned)} Space memory folder(s) belong to a Space id that is no "
            "longer in spaces.json -- most likely Spaces deleted in the app whose "
            "memory was left behind on disk. Their remembered content is still here "
            "and still readable. Report them by that description, never by the bare "
            "id, which names nothing the user has ever seen."
        )
    if unnamed:
        notes.append(
            f"{len(unnamed)} memory owner(s) are listed by Claude with no name at "
            "all. They still exist -- this is not the deleted-Space case above -- so "
            "there is simply no name to quote. Describe them by their `label` and by "
            "what their memory is about; never substitute the raw id, and don't tell "
            "the user the Space was deleted."
        )
    if stale:
        notes.append(
            f"{len(stale)} memory file(s) haven't been written in over "
            f"{int(_MEMORY_STALE_DAYS)} days. The date is the file's last-modified "
            "time, which is when Claude last wrote it -- it is a prompt to check "
            "whether the content is still true, not evidence that it isn't."
        )
    if repeated:
        notes.append(
            f"{len(repeated)} remembered line(s) appear under two or more different "
            "Spaces/Projects. Memory that was meant to be scoped to one piece of "
            "work has spread across others -- worth reading before deciding whether "
            "it belongs at the account level instead, or should be trimmed."
        )
    if unreadable:
        notes.append(
            f"{len(unreadable)} memory file(s) exist but couldn't be read this run "
            "(see each file's `problem`). They are listed rather than dropped, "
            "because a memory file that exists and can't be read is itself worth "
            "knowing about -- their content is not counted in any total above."
        )
    if block["file_list_truncated"]:
        notes.append(
            f"Only the first {read['read_count']} of {read['found_count']} memory "
            "files are listed. Every count above covers the listed files only -- "
            "don't present them as account totals."
        )
    return block, notes


def build_instructions_inventory(
    max_chars: int = _CUSTOM_INSTRUCTIONS_MAX_CHARS,
    since: Any = None,
    until: Any = None,
    max_memory_chars: int = _MEMORY_DEFAULT_MAX_CHARS,
) -> dict[str, Any]:
    """Every layer of standing context on this machine in one place --
    global custom instructions, each Space's `instructions`, each cached
    cloud Project's `prompt_template`, **and Claude's own local memory
    files** -- with the character counts, session reach, and line-level
    duplication needed to audit them for bloat.

    Memory lives here rather than in a tool of its own, deliberately. It is
    the fourth layer of the same thing every other layer here measures:
    text that gets carried into a session without the user typing it. It is
    keyed by the same Space ids and cloud-Project uuids the other layers
    are, so it reuses this function's existing name join and session-reach
    counts instead of duplicating them; and the strongest finding it
    produces -- memory restating what the instructions already say -- can
    only be computed with both in hand. Two tools would have meant two
    half-sources and no obvious place to ask "what standing context am I
    carrying".

    Returns:

    - `global_instructions` -- `{"variants": [...], "variant_count": int}`.
      Global custom instructions are stored per-session, not in one account
      file, so distinct texts are grouped into variants sorted by most
      recent session activity first (`variants[0]` is the best guess at the
      *current* text). More than one variant means the text changed over
      time, not that two are in force at once -- say so rather than
      reporting a conflict.
    - `spaces` / `cloud_projects` -- one entry each, with `session_count`
      (how many local sessions this text actually reached) and
      `duplicates_global`, the lines this text shares verbatim with the
      current global instructions. That overlap is the direct analogue of
      `/doctor`'s dedupe check: text repeated at two levels is paid for
      twice on every turn.
    - `repeated_across_projects` -- lines appearing verbatim in two or more
      Spaces/Projects. The inverse signal: standing context being pasted
      into Project after Project probably belongs in global instructions (or
      a Skill) instead.
    - `memory` -- every local memory file, attributed to its owner:
      `account` (the account-level `memory/` directory), `spaces` (one
      entry per Space memory folder), and `cloud_projects` (one per cached
      Project's `memory.md`). Each owner carries `label`, `session_count`,
      `char_count`, its files (with `text`, last-modified in UTC and local
      form, and `days_since_modified` for staleness), and
      `duplicates_global` -- the same overlap check the instruction layers
      get, because memory that merely restates the standing instructions is
      paid for twice. `repeated_across_memory` is the cross-topic-drift
      signal: lines remembered under two or more different
      Spaces/Projects.

      An id that can't be resolved to a name is reported by `label` as what
      it is ("a Space that is no longer listed in spaces.json") and never
      as a bare uuid, which names nothing the user has seen in the app.

      **Local memory vs export memory.** Both exist. These files are the
      copy *this device* has -- written by the app as it runs, and only as
      current as the last time it ran here. An account data export also
      contains memory, and it comes from the account itself. Where the two
      disagree the **export is authoritative**; the local files are what a
      default, export-free run can see, and they are enough for a same-day
      review as long as the answer says which source it came from. No
      memory file being present is not evidence the account has none.
    - `totals` -- `always_loaded_char_count` (the global text, which every
      session pays for) and `worst_case_char_count` (global plus the single
      largest Project/Space text, i.e. the most standing instruction text
      any one session can be carrying). Memory is deliberately *not* folded
      into these: it is loaded on demand rather than pinned to every turn,
      so adding it would overstate what a session actually pays for.

    `max_chars` caps each returned `text`/`instructions`/`prompt_template`
    string; `max_memory_chars` separately caps each memory file's `text`,
    defaulting lower because memory is bulkier and there are more files of
    it. Every `char_count` is the true, uncapped length either way.

    `since`/`until` scope which sessions are consulted. That changes the
    answer in a useful way rather than just trimming it: because the global
    text is stored per-session, windowing to (say) last quarter reports the
    instructions that were actually in force *then*, and the session-reach
    counts for that period. It is genuinely how to ask "what were my
    standing instructions during Q1," which no view in the app can answer.

    `since`/`until` do **not** scope the `memory` block, and it says so
    rather than pretending otherwise: a memory file is current state with
    one last-modified date, not a record of what was remembered during a
    period. The `session_count` attached to each memory owner *is*
    windowed, because it comes from the same session join as everything
    else here. Say which is which when reporting a windowed run.

    Both caps are clamped (see `clamp_max_chars`). A negative value used to
    slice from the *end* of the text rather than truncating it, so
    `max_memory_chars=-1` returned essentially every memory file on the
    machine in full while the response still looked capped."""
    max_chars = clamp_max_chars(max_chars, _CUSTOM_INSTRUCTIONS_MAX_CHARS)
    max_memory_chars = clamp_max_chars(max_memory_chars, _MEMORY_DEFAULT_MAX_CHARS)
    window = time_window.resolve(since, until)
    base_dir = find_desktop_base_dir()
    empty_global: dict[str, Any] = {"variants": [], "variant_count": 0}
    if base_dir is None:
        return {
            "timezone": time_window.local_timezone_label(),
            "global_instructions": empty_global,
            "spaces": [],
            "cloud_projects": [],
            "repeated_across_projects": [],
            "memory": _empty_memory_block(),
            "totals": {"always_loaded_char_count": 0, "worst_case_char_count": 0},
            "time_window": time_window.apply(window, [], "created_at", "last_activity_at")[1],
            "notes": [
                "No Claude Desktop app-data directory is visible to this session, so "
                "no standing instructions could be read. See check_data_access."
            ],
        }

    sessions = read_local_sessions(base_dir)
    sessions, window_summary = time_window.apply(
        window, sessions, "created_at", "last_activity_at"
    )
    spaces = read_spaces(base_dir)
    project_cache = read_project_cache(base_dir)
    membership = join_local_sessions(sessions, spaces, project_cache)

    # `read_local_sessions` truncates `custom_instructions` for the inventory;
    # re-extract untruncated here so character counts are the real ones.
    variants: dict[str, dict[str, Any]] = {}
    for session in sessions:
        data = _load_json(Path(session["source_file"]))
        text = (
            _extract_custom_instructions(data.get("systemPromptRendererAppends"))
            if isinstance(data, dict)
            else None
        )
        if not text:
            continue
        variant = variants.setdefault(
            text,
            {**_instructions_entry(text, max_chars), "session_count": 0,
             "latest_activity_at": None,
             **_instant_fields("latest_activity_at", None),
             "sample_session_ids": []},
        )
        variant["session_count"] += 1
        activity = session.get("last_activity_at") or session.get("created_at")
        if activity is not None and _activity_sort_key(activity) > _activity_sort_key(
            variant["latest_activity_at"]
        ):
            # The raw epoch value is what the sort needs; the renderings are
            # what a caller quotes when saying which text is the current one
            # and when it was last in use.
            variant["latest_activity_at"] = activity
            variant.update(_instant_fields("latest_activity_at", activity))
        if len(variant["sample_session_ids"]) < _DUPLICATE_SAMPLE_LIMIT:
            variant["sample_session_ids"].append(session["id"])

    ordered_variants = sorted(
        variants.values(),
        key=lambda v: (_activity_sort_key(v["latest_activity_at"]), v["session_count"]),
        reverse=True,
    )
    current_global = ordered_variants[0] if ordered_variants else None
    global_lines = set(
        _normalize_lines(current_global["text"] if current_global else None)
    )

    space_session_counts = {k: len(v) for k, v in membership["by_space_id"].items()}
    project_session_counts = {k: len(v) for k, v in membership["by_project_uuid"].items()}

    space_entries: list[dict[str, Any]] = []
    for space in spaces:
        lines = _normalize_lines(space.get("instructions"))
        space_entries.append(
            {
                "id": space.get("id"),
                "name": space.get("name"),
                "session_count": space_session_counts.get(space.get("id"), 0),
                "folder_count": len(space.get("folders") or []),
                **_instructions_entry(space.get("instructions"), max_chars),
                "duplicates_global": _overlap(lines, global_lines),
            }
        )

    project_entries: list[dict[str, Any]] = []
    for project in project_cache:
        lines = _normalize_lines(project.get("prompt_template"))
        project_entries.append(
            {
                "uuid": project.get("uuid"),
                "name": project.get("name"),
                "session_count": project_session_counts.get(project.get("uuid"), 0),
                **_instructions_entry(project.get("prompt_template"), max_chars),
                "duplicates_global": _overlap(lines, global_lines),
            }
        )

    owners_by_line: dict[str, list[str]] = {}
    for entry, label in [
        *((s, s.get("name") or s.get("id")) for s in spaces),
        *((p, p.get("name") or p.get("uuid")) for p in project_cache),
    ]:
        text = entry.get("instructions") or entry.get("prompt_template")
        for line in dict.fromkeys(_normalize_lines(text)):
            owners_by_line.setdefault(line, []).append(str(label))
    repeated = sorted(
        (
            {"line": line, "owners": owners, "owner_count": len(owners)}
            for line, owners in owners_by_line.items()
            if len(owners) > 1
        ),
        key=lambda r: r["owner_count"],
        reverse=True,
    )

    global_chars = current_global["char_count"] if current_global else 0
    largest_scoped = max(
        (e["char_count"] for e in [*space_entries, *project_entries]), default=0
    )

    memory, memory_notes = _memory_block(
        base_dir, spaces, project_cache, membership, global_lines, max_memory_chars
    )
    # Anchor the zone label to the most recent instant this response
    # actually carries, so the offset shown is the one that was in force
    # then rather than whatever daylight-saving state today happens to be
    # in (see `time_window.local_timezone_label`). `_activity_sort_key`
    # normalizes the two timestamp shapes a session can carry.
    zone_anchor = max(
        (v["latest_activity_at"] for v in ordered_variants),
        key=_activity_sort_key,
        default=None,
    )

    notes: list[str] = time_window.summary_notes(window_summary, "sessions")
    if not ordered_variants:
        notes.append(
            "No global custom instructions were found in any local session"
            + (" in this time window." if window_summary["bounded"] else ".")
            + " That means none are set, or no session on this device has been "
            "opened since they were -- it is not proof the account has none."
        )
    if len(ordered_variants) > 1:
        notes.append(
            f"{len(ordered_variants)} different global custom-instruction texts appear "
            "across local sessions. These are almost certainly the same instructions "
            "edited over time, not competing values -- variants[0] (most recent "
            "session activity) is the best guess at what's in force now."
        )
    if not project_cache:
        notes.append(
            "No cloud Projects are cached on this device, so their custom "
            "instructions could not be checked. `.project-cache` only holds Projects "
            "opened on this machine -- the account may have many more."
        )
    notes.extend(memory_notes)
    notes.append(_local_time_note(zone_anchor))

    return {
        "timezone": time_window.local_timezone_label(zone_anchor),
        "global_instructions": {
            "variants": ordered_variants,
            "variant_count": len(ordered_variants),
        },
        "spaces": space_entries,
        "cloud_projects": project_entries,
        "repeated_across_projects": repeated,
        "memory": memory,
        "totals": {
            "always_loaded_char_count": global_chars,
            "worst_case_char_count": global_chars + largest_scoped,
        },
        "time_window": window_summary,
        "notes": notes,
    }


# --------------------------------------------------------------------------------
# Account-level files at the Desktop base-dir root
# --------------------------------------------------------------------------------
#
# Everything above this line is *per-session* data, globbed out of
# `local-agent-mode-sessions/`. The four files below sit at the root of the
# Desktop app-data directory and describe the account and the app as a
# whole, which is why they answer questions no amount of session data can:
#
#   plan-usage-history.json     -- what the plan was actually consuming
#   claude_desktop_config.json  -- standing grants and app-wide toggles
#   extensions-installations.json -- what is *installed* (not what ran)
#   config.json                 -- first launch, locale, theme (allowlisted;
#                                  this file also holds OAuth token caches)
#
# All four were confirmed by direct inspection on a real macOS install and,
# like everything else here, are an undocumented private cache: read
# defensively, treat every field as optional, never raise on a missing or
# malformed one.

PLAN_USAGE_HISTORY_FILENAME = "plan-usage-history.json"
DESKTOP_CONFIG_FILENAME = "claude_desktop_config.json"
EXTENSIONS_FILENAME = "extensions-installations.json"
APP_CONFIG_FILENAME = "config.json"


def read_configured_output_dir(base_dir: Path | None) -> str | None:
    """The user-visible Claude output folder as *Claude itself* has it
    configured: `coworkUserFilesPath` in `claude_desktop_config.json`
    (confirmed directly -- `/Users/<user>/Claude` on a real install).

    This is the authoritative answer to "where does Cowork write things",
    and it replaces guessing from a candidate list. Returns None when the
    file is missing, unreadable, or doesn't carry the key -- callers fall
    back to probing (see `check_data_access`) rather than reporting
    nothing."""
    if base_dir is None:
        return None
    data = _load_json(base_dir / DESKTOP_CONFIG_FILENAME)
    if not isinstance(data, dict):
        return None
    configured = data.get("coworkUserFilesPath")
    if isinstance(configured, str) and configured.strip():
        return configured.strip()
    return None


def _account_file_availability(base_dir: Path | None) -> dict[str, Any]:
    """Which account-level files exist, and therefore which of the two
    account-level lenses can run this session. Existence/readability only --
    nothing here reads content.

    Reported by `check_data_access` so a caller learns up front that (say)
    plan usage history isn't available on this machine, instead of finding
    out from an empty `get_plan_usage_history` response later."""
    filenames = {
        "plan_usage_history": PLAN_USAGE_HISTORY_FILENAME,
        "desktop_config": DESKTOP_CONFIG_FILENAME,
        "extensions_installations": EXTENSIONS_FILENAME,
        "app_config": APP_CONFIG_FILENAME,
    }
    files = {
        name: (
            _probe_path(str(base_dir / filename))
            if base_dir is not None
            else {"path": None, "exists": False}
        )
        for name, filename in filenames.items()
    }

    usage_available = bool(files["plan_usage_history"]["exists"])
    settings_available = any(
        files[name]["exists"]
        for name in ("desktop_config", "extensions_installations", "app_config")
    )

    notes: list[str] = []
    if base_dir is None:
        notes.append(
            "No Claude Desktop app-data directory is visible, so neither the plan "
            "usage history nor the Desktop settings lens can run this session."
        )
    else:
        if not usage_available:
            notes.append(
                "No plan usage history file on this machine, so get_plan_usage_history "
                "has nothing to read. The app writes it as it runs, so a fresh install "
                "or a machine the user rarely opens Claude on simply won't have one -- "
                "that is not evidence the account has low usage."
            )
        if not settings_available:
            notes.append(
                "None of the account-level settings files are readable, so "
                "get_desktop_settings can't report standing folder grants, installed "
                "extensions, or app-wide toggles this session."
            )

    return {
        "files": files,
        "plan_usage_history_available": usage_available,
        "desktop_settings_available": settings_available,
        "notes": notes,
    }


# --------------------------------------------------------------------------------
# Plan usage history -- the only ground truth about real plan consumption
# --------------------------------------------------------------------------------
#
# `plan-usage-history.json` is Claude Desktop's own rolling record of how
# full the user's usage windows were, sampled roughly every 15 minutes
# while the app is running. Confirmed shape (version 2):
#
#   {"version": 2, "samples": [{"t": <epoch ms>, "org": "<uuid>",
#                               "u": {"fh": 0-100, "sd": 0-100, "xu": 0-100}}]}
#
#   fh -- the five-hour usage window, as a percentage of the plan's limit
#   sd -- the seven-day usage window, same units
#   xu -- extra usage (purchased top-up), absent entirely on most samples
#
# Everything else this plugin measures -- message counts, model tallies,
# session counts -- is a *proxy* for consumption. This file is the real
# thing, and it is the Desktop counterpart of the Claude Code CLI's
# `/usage`. Percentages are of the user's own plan limits: not dollars, not
# tokens, not a bill.

_USAGE_NEAR_LIMIT_PCT = 90
# Contiguous at-or-near-limit samples collapse into one event, but only
# while the samples are actually contiguous. Two hours of silence between
# them is a closed app or a sleeping machine, not one continuous four-day
# limit event -- and the five-hour window itself has rolled well on by then.
_USAGE_RUN_BREAK_MS = 2 * 60 * 60 * 1000
# The seven-day window needs its own, much longer break, because the two
# windows keep different time. Five hours of not using Claude empties the
# five-hour window, so a two-hour hole in the samples genuinely separates
# two limit events. The seven-day window drains at about 14% a day: being
# at 95% of the weekly cap on Monday night and still at 95% on Tuesday
# morning is one continuous week of being nearly out, not two events. A
# day and a half absorbs an overnight, a closed laptop, and a weekend
# morning without merging two different weeks.
_USAGE_SEVEN_DAY_RUN_BREAK_MS = 36 * 60 * 60 * 1000
_USAGE_NOTABLE_GAP_MS = 2 * 60 * 60 * 1000
_USAGE_MAX_GAPS_REPORTED = 10
_USAGE_MAX_EVENTS = 50
_USAGE_STALE_CURRENT_HOURS = 6
_MS_PER_MINUTE = 60_000.0
_MS_PER_HOUR = 3_600_000.0
_MS_PER_DAY = 86_400_000.0

# Above this, a "percentage" is not a reading this file could plausibly
# have written. The real values are 0-100; the ceiling is set an order of
# magnitude clear of that so a future schema that expresses extra usage
# differently isn't quietly discarded, while a garbled `1e+308` -- which
# would otherwise be rendered to a business user as "1e+308% of your
# plan" -- is.
_USAGE_PCT_MAX_PLAUSIBLE = 1000.0


def _percent(value: Any) -> float | int | None:
    """A usage percentage, or None when the key is simply absent -- which
    `xu` is on most samples (159 of 544 on a real install). None must not
    collapse to 0: "no extra usage recorded" and "this build didn't write
    the field" are different claims.

    A present-but-unusable reading is **dropped**, not clamped, and comes
    back as None too. Two shapes qualify: `NaN`/`Infinity` (Python's json
    parses those non-standard literals happily, and re-emitting one
    produces a response a strict JSON parser rejects outright), and a
    finite number so far outside the plan-percentage range that it cannot
    be a reading. Dropping rather than clamping is deliberate: clamping
    `1e+308` to 100 would manufacture "you hit your limit" out of file
    corruption, and there is no honest number to substitute. The reader
    counts what it dropped -- see `read_plan_usage_history` -- so it is
    said out loud rather than looking like an absent field."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    if not 0 <= value <= _USAGE_PCT_MAX_PLAUSIBLE:
        return None
    return value


def _reading_is_unusable(value: Any) -> bool:
    """The key was present but carries something `_percent` had to drop.
    Distinguishes "this build didn't write the field" (absent, expected,
    silent) from "this file says NaN" (present, wrong, worth reporting)."""
    return value is not None and _percent(value) is None


def read_plan_usage_history(base_dir: Path) -> dict[str, Any]:
    """`plan-usage-history.json`, normalized: one row per sample, sorted
    oldest first, with the raw epoch-millisecond `at` and its `at_iso`
    companion side by side (same convention as `read_local_sessions`'
    `created_at`/`created_at_iso` -- reading the raw value as seconds
    silently yields 1970 dates that look like real, very stale data).

    Every row carries three renderings of the same instant: `at` (epoch
    milliseconds, for arithmetic), `at_iso` (UTC, for comparison and for
    joining against everything else this plugin returns), and
    `at_local_iso` (this machine's timezone, offset attached, for anything
    a person will read). Days and weekdays are derived from the local one --
    see `_usage_daily_high_water`.

    Returns `{"version", "source_file", "samples", "unusable_sample_count",
    "unusable_reading_count"}`. `version` is the file's own schema version
    (2 on a real install) -- worth surfacing, because this is a private
    format that can change shape between Desktop builds. A missing or
    malformed file yields an empty `samples` list, never a raise.

    The two `unusable_*` counts are what was thrown away and why it isn't
    silent: a sample whose `t` cannot be rendered as a real date at all
    (absent, non-numeric, `NaN`, or an epoch value outside the range a
    calendar can express) is skipped whole, and an individual percentage
    that isn't a usable number is dropped from an otherwise fine sample.
    Both would previously have propagated -- as an exception, or as a
    `NaN` token in the response that a strict JSON parser rejects.

    Rows keep the raw `org` uuid for grouping only. It must never reach a
    response -- `build_plan_usage_history` maps it to an opaque label."""
    path = base_dir / PLAN_USAGE_HISTORY_FILENAME
    data = _load_json(path)
    version: Any = None
    if isinstance(data, dict):
        version = data.get("version")
        raw_samples = data.get("samples")
    elif isinstance(data, list):
        # Defensive: a bare list is not what version 2 writes, but this
        # layout is unversioned in the sense that matters -- nothing
        # promises the wrapper stays. Same posture as `read_spaces`.
        raw_samples = data
    else:
        raw_samples = None

    samples: list[dict[str, Any]] = []
    unusable_samples = 0
    unusable_readings = 0
    if isinstance(raw_samples, list):
        for entry in raw_samples:
            if not isinstance(entry, dict):
                unusable_samples += 1
                continue
            at = time_window.to_epoch_ms(entry.get("t"))
            at_iso = time_window.iso(at)
            # Both have to work. A value can normalize to a number and
            # still be un-renderable as a date (1e18 ms is roughly the year
            # 31,000,000); keeping such a row would put a None where every
            # consumer below expects a date string.
            if at is None or at_iso is None:
                unusable_samples += 1
                continue
            usage = entry.get("u") if isinstance(entry.get("u"), dict) else {}
            unusable_readings += sum(
                1 for key in ("fh", "sd", "xu") if _reading_is_unusable(usage.get(key))
            )
            org = entry.get("org")
            samples.append(
                {
                    "at": int(at),
                    "at_iso": at_iso,
                    "at_local_iso": time_window.local_iso(at),
                    "org": org if isinstance(org, str) else None,
                    "five_hour_pct": _percent(usage.get("fh")),
                    "seven_day_pct": _percent(usage.get("sd")),
                    "extra_usage_pct": _percent(usage.get("xu")),
                }
            )
    samples.sort(key=lambda row: row["at"])

    return {
        "version": version,
        "source_file": str(path),
        "samples": samples,
        "unusable_sample_count": unusable_samples,
        "unusable_reading_count": unusable_readings,
    }


def _usage_point(row: dict[str, Any] | None) -> dict[str, Any]:
    """One sample as a presentable point -- no org, no internal keys."""
    if row is None:
        return {
            "at": None,
            "at_iso": None,
            "at_local_iso": None,
            "five_hour_pct": None,
            "seven_day_pct": None,
            "extra_usage_pct": None,
        }
    return {
        "at": row["at"],
        "at_iso": row["at_iso"],
        "at_local_iso": row["at_local_iso"],
        "five_hour_pct": row["five_hour_pct"],
        "seven_day_pct": row["seven_day_pct"],
        "extra_usage_pct": row["extra_usage_pct"],
    }


def _usage_peak(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Highest observed value of one usage window, when it happened, and
    which day of the user's own week that was.

    `pct` is None when no sample carried the field at all -- which is a
    different statement from "the peak was 0". The local date and weekday
    ride along because a peak is the one figure most likely to be repeated
    back as a sentence ("you ran out on Thursday afternoon"), and building
    that sentence from the UTC timestamp is how it comes out wrong."""
    best: dict[str, Any] | None = None
    for row in rows:
        value = row[key]
        if value is None:
            continue
        if best is None or value > best[key]:
            best = row
    day = time_window.local_day(best["at"]) if best else None
    return {
        "pct": best[key] if best else None,
        "at": best["at"] if best else None,
        "at_iso": best["at_iso"] if best else None,
        "at_local_iso": best["at_local_iso"] if best else None,
        "local_date": day["date"] if day else None,
        "local_weekday": day["weekday"] if day else None,
    }


def _usage_cadence(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the app actually sampled, and where it stopped.

    The gaps are the load-bearing part. Desktop only writes this file while
    it is running, so a gap is the app being closed or the machine asleep --
    it is emphatically *not* a period of zero usage, and reading it that way
    turns "I was on holiday" into "you barely use Claude"."""
    if len(rows) < 2:
        return {
            "median_interval_minutes": None,
            "shortest_interval_minutes": None,
            "longest_gap_hours": None,
            "notable_gap_count": 0,
            "largest_gaps": [],
        }

    pairs = list(zip(rows[:-1], rows[1:], strict=True))
    deltas = sorted(later["at"] - earlier["at"] for earlier, later in pairs)
    gaps = [
        {
            "from_at_iso": earlier["at_iso"],
            "to_at_iso": later["at_iso"],
            "from_at_local_iso": earlier["at_local_iso"],
            "to_at_local_iso": later["at_local_iso"],
            "gap_hours": round((later["at"] - earlier["at"]) / _MS_PER_HOUR, 2),
        }
        for earlier, later in pairs
        if later["at"] - earlier["at"] > _USAGE_NOTABLE_GAP_MS
    ]
    gaps.sort(key=lambda g: g["gap_hours"], reverse=True)

    return {
        "median_interval_minutes": round(deltas[len(deltas) // 2] / _MS_PER_MINUTE, 1),
        "shortest_interval_minutes": round(deltas[0] / _MS_PER_MINUTE, 1),
        "longest_gap_hours": round(deltas[-1] / _MS_PER_HOUR, 2),
        "notable_gap_count": len(gaps),
        "largest_gaps": gaps[:_USAGE_MAX_GAPS_REPORTED],
    }


# The two usage windows, described symmetrically so neither can quietly be
# the only one that gets analysed. For each: the sample field, the field
# name its peak is reported under, the *other* window's field (reported at
# the end of an event, because "you ran out of your five hours and your
# week was already at 88%" is a materially different situation from the
# same event at 20%), how long a hole in the samples has to be before one
# event becomes two, and a plain-language name for prose.
@dataclass(frozen=True)
class _UsageWindow:
    key: str
    label: str
    peak_field: str
    other_key: str
    other_at_end_field: str
    run_break_ms: int


_FIVE_HOUR_WINDOW = _UsageWindow(
    key="five_hour_pct",
    label="five-hour",
    peak_field="peak_five_hour_pct",
    other_key="seven_day_pct",
    other_at_end_field="seven_day_pct_at_end",
    run_break_ms=_USAGE_RUN_BREAK_MS,
)
_SEVEN_DAY_WINDOW = _UsageWindow(
    key="seven_day_pct",
    label="seven-day",
    peak_field="peak_seven_day_pct",
    other_key="five_hour_pct",
    other_at_end_field="five_hour_pct_at_end",
    run_break_ms=_USAGE_SEVEN_DAY_RUN_BREAK_MS,
)


def _usage_event(run: list[dict[str, Any]], window: _UsageWindow) -> dict[str, Any]:
    peak = _usage_peak(run, window.key)
    start_day = time_window.local_day(run[0]["at"])
    end_day = time_window.local_day(run[-1]["at"])
    span_ms = run[-1]["at"] - run[0]["at"]
    return {
        "window": window.label,
        "start_at": run[0]["at"],
        "start_at_iso": run[0]["at_iso"],
        "start_at_local_iso": run[0]["at_local_iso"],
        "start_local_date": start_day["date"] if start_day else None,
        "start_local_weekday": start_day["weekday"] if start_day else None,
        "end_at": run[-1]["at"],
        "end_at_iso": run[-1]["at_iso"],
        "end_at_local_iso": run[-1]["at_local_iso"],
        "end_local_date": end_day["date"] if end_day else None,
        "end_local_weekday": end_day["weekday"] if end_day else None,
        "duration_minutes": round(span_ms / _MS_PER_MINUTE, 1),
        # A seven-day event routinely spans days, and "4,320.0 minutes" is
        # not a figure anyone can read. Both units, always, so neither
        # window needs the caller to do arithmetic.
        "duration_hours": round(span_ms / _MS_PER_HOUR, 2),
        "sample_count": len(run),
        window.peak_field: peak["pct"],
        "peak_at_iso": peak["at_iso"],
        "peak_at_local_iso": peak["at_local_iso"],
        window.other_at_end_field: run[-1][window.other_key],
        # True only at a full 100 -- the difference between "you ran out"
        # and "you got close", which is the whole point of the near-limit
        # threshold.
        "hit_ceiling": peak["pct"] is not None and peak["pct"] >= 100,
    }


def _usage_limit_events(
    rows: list[dict[str, Any]], near_pct: float, window: _UsageWindow
) -> list[dict[str, Any]]:
    """Runs where one usage window sat at or near its limit, collapsed into
    one event each.

    Sampling every ~15 minutes means a single afternoon at 100% produces a
    dozen samples; reporting those as a dozen "limit hits" would badly
    overstate how often the user actually ran out. A run ends when a sample
    drops below the threshold *or* when the samples stop being contiguous
    for longer than that window's own run-break (see `_UsageWindow`, and
    `_USAGE_SEVEN_DAY_RUN_BREAK_MS` for why the two differ)."""
    events: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []
    for row in rows:
        value = row[window.key]
        at_limit = value is not None and value >= near_pct
        if run and (not at_limit or row["at"] - run[-1]["at"] > window.run_break_ms):
            events.append(_usage_event(run, window))
            run = []
        if at_limit:
            run.append(row)
    if run:
        events.append(_usage_event(run, window))
    return events


def _usage_limit_event_block(
    rows: list[dict[str, Any]], near_pct: float, window: _UsageWindow
) -> dict[str, Any]:
    """One window's limit events plus its headline counts, in the identical
    shape for both windows so a caller cannot read one and miss the
    other."""
    events = _usage_limit_events(rows, near_pct, window)
    return {
        "window": window.label,
        "run_break_hours": round(window.run_break_ms / _MS_PER_HOUR, 1),
        "event_count": len(events),
        "ceiling_event_count": sum(1 for e in events if e["hit_ceiling"]),
        "events": events[:_USAGE_MAX_EVENTS],
        "events_truncated": len(events) > _USAGE_MAX_EVENTS,
    }


def _usage_daily_high_water(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The highest each usage window reached on each day, plus that day's
    weekday name -- enough for a caller to describe a weekly rhythm ("you
    run hot on Mondays") without being handed every sample.

    Days are **this machine's local calendar days**, not UTC ones. That is
    a deliberate correction: these buckets exist so a person can be told
    which of *their* days ran hot, and on a machine six hours west of UTC,
    UTC bucketing moved the reported weekly peak from Thursday to Friday
    and changed 25 of 30 day labels on real data. A weekday computed in a
    zone the reader does not live in is not a caveat, it is a wrong answer.
    Each row carries `utc_offset` so the zone is never left to inference,
    and `first_sample_iso`/`last_sample_iso` keep the UTC form alongside
    the local one for anything doing comparisons.

    A day with no samples is simply absent -- see the gap caveat in
    `_usage_cadence`."""
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = time_window.local_day(row["at"])
        if day is None:
            continue
        entry = by_day.setdefault(
            day["date"],
            {
                "date": day["date"],
                "weekday": day["weekday"],
                "utc_offset": day["utc_offset"],
                "sample_count": 0,
                "peak_five_hour_pct": None,
                "peak_seven_day_pct": None,
                "peak_extra_usage_pct": None,
                "first_sample_iso": row["at_iso"],
                "last_sample_iso": row["at_iso"],
                "first_sample_local_iso": row["at_local_iso"],
                "last_sample_local_iso": row["at_local_iso"],
            },
        )
        entry["sample_count"] += 1
        entry["last_sample_iso"] = row["at_iso"]
        entry["last_sample_local_iso"] = row["at_local_iso"]
        for source_key, target_key in (
            ("five_hour_pct", "peak_five_hour_pct"),
            ("seven_day_pct", "peak_seven_day_pct"),
            ("extra_usage_pct", "peak_extra_usage_pct"),
        ):
            value = row[source_key]
            if value is not None and (
                entry[target_key] is None or value > entry[target_key]
            ):
                entry[target_key] = value
    return [by_day[day] for day in sorted(by_day)]


def _usage_extra(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Whether purchased extra usage was ever consumed, and when.

    `xu` is absent from most samples rather than zero, so the two counts
    below are reported separately: "never above zero on the samples that
    recorded it" is a much weaker claim than "never used", and only the
    first is provable from this file."""
    reported = [row for row in rows if row["extra_usage_pct"] is not None]
    used = [row for row in reported if row["extra_usage_pct"] > 0]
    peak = _usage_peak(reported, "extra_usage_pct")
    return {
        "ever_used": bool(used),
        "peak_pct": peak["pct"],
        # No timestamp when the peak is zero: "extra usage peaked at 0% on
        # August 7th" reads as an event, and there was none.
        "peak_at_iso": peak["at_iso"] if used else None,
        "peak_at_local_iso": peak["at_local_iso"] if used else None,
        "first_used_at_iso": used[0]["at_iso"] if used else None,
        "first_used_at_local_iso": used[0]["at_local_iso"] if used else None,
        "last_used_at_iso": used[-1]["at_iso"] if used else None,
        "last_used_at_local_iso": used[-1]["at_local_iso"] if used else None,
        "samples_reporting_field": len(reported),
        "samples_missing_field": len(rows) - len(reported),
    }


def _usage_org_labels(rows: list[dict[str, Any]]) -> dict[str | None, str]:
    """Opaque labels for the organization uuids in this file, in first-seen
    order. The uuid itself is an account identifier and never goes into a
    response -- "organization 1" is all a caller needs to keep two
    organizations' numbers from being averaged together."""
    labels: dict[str | None, str] = {}
    for row in rows:
        if row["org"] not in labels:
            labels[row["org"]] = f"organization {len(labels) + 1}"
    return labels


def _usage_unavailable(
    *,
    source_file: str | None,
    schema_version: Any,
    window_summary: dict[str, Any],
    notes: list[str],
    total_sample_count: int = 0,
    coverage: dict[str, Any] | None = None,
    available: bool = False,
) -> dict[str, Any]:
    """The response shape when there is nothing to analyse.

    Kept as one function so every degraded path returns the *same* keys as
    the populated one. A caller that has to check whether `peaks` exists
    before reading it will eventually forget to, and the failure mode --
    reading `None` as "zero usage" -- is the exact misreading this whole
    module is built to prevent."""
    return {
        "available": available,
        "source_file": source_file,
        "schema_version": schema_version,
        "timezone": time_window.local_timezone_label(),
        "sample_count": 0,
        "total_sample_count": total_sample_count,
        "coverage": coverage,
        "cadence": None,
        "current": None,
        "peaks": None,
        "limit_events": None,
        "daily_high_water": [],
        "extra_usage": None,
        "organizations": [],
        "time_window": window_summary,
        "notes": notes,
    }


def _build_plan_usage_history(since: Any = None, until: Any = None) -> dict[str, Any]:
    window = time_window.resolve(since, until)
    base_dir = find_desktop_base_dir()
    empty_summary = time_window.apply(window, [], "at", "at")[1]

    if base_dir is None:
        return _usage_unavailable(
            source_file=None,
            schema_version=None,
            window_summary=empty_summary,
            notes=[
                "No Claude Desktop app-data directory is visible to this session, so "
                "there is no plan usage history to read. See check_data_access -- "
                "this is usually a folder-access problem, not an absent file."
            ],
        )

    raw = read_plan_usage_history(base_dir)
    all_rows = raw["samples"]
    if not all_rows:
        return _usage_unavailable(
            source_file=raw["source_file"],
            schema_version=raw["version"],
            window_summary=empty_summary,
            notes=[
                "Claude Desktop's plan usage history file is missing, empty, or in a "
                "shape this plugin doesn't recognize, so real plan consumption can't "
                "be reported this run. Every other usage signal in this plugin is a "
                "proxy -- message and session counts -- so say the consumption "
                "numbers are unavailable rather than substituting one."
            ],
        )

    rows, window_summary = time_window.apply(window, all_rows, "at", "at")
    notes: list[str] = time_window.summary_notes(window_summary, "usage samples")

    file_first_day = time_window.local_day(all_rows[0]["at"])
    file_last_day = time_window.local_day(all_rows[-1]["at"])

    if not rows:
        notes.append(
            "The requested window contains no usage samples at all, though "
            f"{len(all_rows)} exist outside it "
            f"({all_rows[0]['at_local_iso']} to {all_rows[-1]['at_local_iso']}, this "
            "machine's local time). Report that as 'no data recorded for that "
            "period', not as 'no usage'."
        )
        return _usage_unavailable(
            available=True,
            source_file=raw["source_file"],
            schema_version=raw["version"],
            total_sample_count=len(all_rows),
            coverage={
                "first_sample_at_iso": all_rows[0]["at_iso"],
                "last_sample_at_iso": all_rows[-1]["at_iso"],
                "first_sample_at_local_iso": all_rows[0]["at_local_iso"],
                "last_sample_at_local_iso": all_rows[-1]["at_local_iso"],
                "first_sample_local_date": file_first_day["date"] if file_first_day else None,
                "last_sample_local_date": file_last_day["date"] if file_last_day else None,
                "in_window": False,
            },
            window_summary=window_summary,
            notes=notes,
        )

    labels = _usage_org_labels(rows)
    cadence = _usage_cadence(rows)
    multi_org = len(labels) > 1
    five_hour_events = _usage_limit_event_block(
        rows, _USAGE_NEAR_LIMIT_PCT, _FIVE_HOUR_WINDOW
    )
    seven_day_events = _usage_limit_event_block(
        rows, _USAGE_NEAR_LIMIT_PCT, _SEVEN_DAY_WINDOW
    )

    organizations = []
    for org, label in labels.items():
        org_rows = [row for row in rows if row["org"] == org]
        organizations.append(
            {
                "label": label,
                "sample_count": len(org_rows),
                "first_sample_iso": org_rows[0]["at_iso"],
                "last_sample_iso": org_rows[-1]["at_iso"],
                "first_sample_local_iso": org_rows[0]["at_local_iso"],
                "last_sample_local_iso": org_rows[-1]["at_local_iso"],
                "peak_five_hour_pct": _usage_peak(org_rows, "five_hour_pct")["pct"],
                "peak_seven_day_pct": _usage_peak(org_rows, "seven_day_pct")["pct"],
                "current": _usage_point(org_rows[-1]),
            }
        )

    span_ms = rows[-1]["at"] - rows[0]["at"]
    current_row = rows[-1]
    now_ms = datetime.now(UTC).timestamp() * 1000.0
    current_age_hours = round((now_ms - current_row["at"]) / _MS_PER_HOUR, 2)
    window_first_day = time_window.local_day(rows[0]["at"])
    window_last_day = time_window.local_day(current_row["at"])
    timezone = time_window.local_timezone_label(current_row["at"])

    notes.append(
        "These percentages are shares of this account's own plan limits -- how full "
        "the five-hour and seven-day usage windows were. They are not dollars, not "
        "tokens, and not a bill. Never present them as cost."
    )
    notes.append(
        f"Every date, weekday, and `*_local_iso` time in this response is in this "
        f"machine's own timezone ({timezone['name']}, {timezone['utc_offset']}). The "
        "`*_at_iso` values beside them are the same instants written in UTC, for "
        "comparison and joining only. Build anything the user reads -- a weekday, a "
        "date, a time of day -- from the local values: a weekday computed in UTC is "
        "a wrong answer for someone reading their own calendar, not a caveat on a "
        "right one."
    )
    notes.append(
        "Claude Desktop only writes this file while it is running, so a gap between "
        "samples means the app was closed or the machine was asleep -- NOT that usage "
        "was zero. Treating a gap as idle time is the single easiest way to misread "
        "this data. The longest gap in this window was "
        f"{cadence['longest_gap_hours']} hours."
        if cadence["longest_gap_hours"] is not None
        else "Claude Desktop only writes this file while it is running, so a gap "
        "between samples means the app was closed or the machine was asleep -- NOT "
        "that usage was zero."
    )
    notes.append(
        "There are two separate limits and both are reported here, in the same shape, "
        "under `limit_events`: the five-hour window reached or neared its limit in "
        f"{five_hour_events['event_count']} run(s) "
        f"({five_hour_events['ceiling_event_count']} of them at a full 100%), and the "
        f"seven-day window in {seven_day_events['event_count']} run(s) "
        f"({seven_day_events['ceiling_event_count']} at 100%). For a business user "
        "the seven-day one is usually the more important of the two: running out of "
        "the five-hour window costs the rest of an afternoon, running out of the "
        "weekly window can cost the rest of the week. Report both; if the seven-day "
        "count is the higher one, lead with it."
    )
    notes.append(
        "The samples on this machine run from "
        f"{file_first_day['date'] if file_first_day else 'an unknown date'} to "
        f"{file_last_day['date'] if file_last_day else 'an unknown date'} "
        f"({round((all_rows[-1]['at'] - all_rows[0]['at']) / _MS_PER_DAY, 1)} days, "
        f"{len(all_rows)} samples); this response covers {len(rows)} of them, "
        f"{window_first_day['date'] if window_first_day else '?'} to "
        f"{window_last_day['date'] if window_last_day else '?'}. What that earliest "
        "date *means* is not recorded anywhere: it could be when Claude Desktop was "
        "first run on this machine, when the app started keeping this file, or where "
        "some internal trimming rule cut it. Say 'the record on this machine starts "
        "here' and attribute no cause to it. Either way it is a local file -- this "
        "machine only, and only while Desktop was running -- so usage from the phone, "
        "the web app, or another computer is not in it, and it is not account history."
    )
    if current_age_hours > _USAGE_STALE_CURRENT_HOURS:
        notes.append(
            f"The most recent sample is {current_age_hours} hours old, so 'current' "
            "below is the last known reading, not a live one."
        )
    if raw["unusable_sample_count"]:
        notes.append(
            f"{raw['unusable_sample_count']} entr(ies) in the file were skipped "
            "because they carry no timestamp this plugin can read as a real date. "
            "They are not counted anywhere below, so every figure here describes "
            "slightly less than the file holds."
        )
    if raw["unusable_reading_count"]:
        notes.append(
            f"{raw['unusable_reading_count']} individual percentage reading(s) were "
            "dropped as unusable (not a number, or far outside any plausible "
            "percentage). They were dropped rather than clamped -- there is no honest "
            "value to substitute -- so they read as 'not recorded' below. A nonzero "
            "count here means the file is at least partly corrupt; treat the totals "
            "as a floor."
        )
    if multi_org:
        notes.append(
            f"Samples from {len(labels)} organizations appear in this file. The "
            "top-level peaks and current values mix them together; use the "
            "`organizations` block for a per-organization read. Organizations are "
            "labelled opaquely on purpose -- the file identifies them only by an "
            "account uuid, which this plugin does not surface."
        )

    return {
        "available": True,
        "source_file": raw["source_file"],
        "schema_version": raw["version"],
        "timezone": timezone,
        "sample_count": len(rows),
        "total_sample_count": len(all_rows),
        # How much history is on disk and how much of it this response
        # covers. Named for what it is -- coverage -- rather than
        # "retention": nothing in this file establishes that the earliest
        # sample is where a retention rule cut it, and asserting a
        # mechanism the data can't show is how a plausible number becomes
        # a false one.
        "coverage": {
            "first_sample_at": rows[0]["at"],
            "first_sample_at_iso": rows[0]["at_iso"],
            "first_sample_at_local_iso": rows[0]["at_local_iso"],
            "first_sample_local_date": window_first_day["date"] if window_first_day else None,
            "last_sample_at": current_row["at"],
            "last_sample_at_iso": current_row["at_iso"],
            "last_sample_at_local_iso": current_row["at_local_iso"],
            "last_sample_local_date": window_last_day["date"] if window_last_day else None,
            "span_days": round(span_ms / _MS_PER_DAY, 2),
            "in_window": True,
            "file_first_sample_at_iso": all_rows[0]["at_iso"],
            "file_last_sample_at_iso": all_rows[-1]["at_iso"],
            "file_first_sample_at_local_iso": all_rows[0]["at_local_iso"],
            "file_last_sample_at_local_iso": all_rows[-1]["at_local_iso"],
            "file_first_sample_local_date": file_first_day["date"] if file_first_day else None,
            "file_last_sample_local_date": file_last_day["date"] if file_last_day else None,
            "unusable_sample_count": raw["unusable_sample_count"],
            "unusable_reading_count": raw["unusable_reading_count"],
        },
        "cadence": cadence,
        "current": {**_usage_point(current_row), "age_hours": current_age_hours},
        "peaks": {
            "five_hour": _usage_peak(rows, "five_hour_pct"),
            "seven_day": _usage_peak(rows, "seven_day_pct"),
        },
        # Both windows, same shape, neither nested under the other. The
        # five-hour one used to be the only one computed, which left the
        # limit that can strand someone for a week entirely invisible.
        "limit_events": {
            "near_limit_threshold_pct": _USAGE_NEAR_LIMIT_PCT,
            "five_hour": five_hour_events,
            "seven_day": seven_day_events,
        },
        "daily_high_water": _usage_daily_high_water(rows),
        "extra_usage": _usage_extra(rows),
        "organizations": organizations,
        "time_window": window_summary,
        "notes": notes,
    }


def build_plan_usage_history(since: Any = None, until: Any = None) -> dict[str, Any]:
    """Derived, decision-useful aggregates over `plan-usage-history.json`
    (see `read_plan_usage_history` for the raw shape): where consumption
    peaked, when each of the two windows ran out, the per-day high-water
    marks, and how much of the story this file can actually tell.

    Unlike every other reader here, samples are matched on the sample
    timestamp falling *inside* the window rather than on interval overlap:
    a usage sample is a point in time, not a span, so there is nothing to
    overlap with. The standard `time_window` block still comes back with
    the resolved bounds, the excluded count, and any bad-bound errors.

    Percentages are of the user's own plan limits. Not dollars, not tokens,
    not a bill -- never present them as cost.

    **Two timestamp forms, always both.** `*_at_iso` is UTC, for comparison
    and joining; `*_local_iso`, `*_local_date` and `weekday` are this
    machine's own timezone, and are the ones any sentence shown to a person
    must be built from. The `timezone` block at the top names the zone.

    **No raw sample series.** An earlier version returned a downsampled
    chart series and a `max_samples` knob. Nothing consumed it -- not the
    renderer, not the skill, not any reference doc -- while it made up
    roughly two thirds of the response a model had to read, and its
    even-spacing downsampler dropped extrema, so a chart drawn from it
    could omit every limit hit and contradict the `peaks` block sitting
    beside it. The aggregates here are what the analysis actually uses."""
    # Every reader in this plugin routes its output through `redact` before
    # it leaves. Nothing in this response is secret-shaped today; the point
    # is that the guarantee holds without anyone having to re-check that
    # after the next field is added.
    return redact(_build_plan_usage_history(since, until))


# --------------------------------------------------------------------------------
# Desktop settings -- standing grants, toggles, installed extensions
# --------------------------------------------------------------------------------
#
# Everything the per-session readers above can see is, by definition, a
# session that happened. That leaves three blind spots this section fills:
#
#   1. A folder granted once and trusted ever after (a *standing* grant)
#      looks identical to nothing at all in session data.
#   2. An extension installed and never enabled appears in no session's
#      `enabled_mcp_server_labels`, so "installed but unused" -- the most
#      actionable capability finding there is -- was invisible.
#   3. App-wide toggles (auto-approve, scheduled tasks, browser control)
#      are set once, account-wide, and are not per-session fields.

_PREFERENCE_ACCESS_KEYS = (
    "localAgentModeTrustedFolders",
    "remoteFolderConsentMemory",
    "remoteSessionFolderGrants",
)

# Toggles worth reporting by name, confirmed present on a real install.
# Anything not listed here still comes back, under `unrecognized` -- see
# `build_desktop_settings`.
_PREFERENCE_KNOWN_KEYS = (
    "allowAllBrowserActions",
    "coworkBrowserToolsEnabled",
    "dispatchCodeTasksPermissionMode",
    "bypassPermissionsGateByAccount",
    "coworkScheduledTasksEnabled",
    "ccdScheduledTasksEnabled",
    "coworkWebSearchEnabled",
    "coworkModelAutoFallbackByAccount",
    "keepAwakeEnabled",
    "coworkHipaaRestricted",
    "orgWorkAcrossAppsDisabled",
    "sidebarMode",
    "coworkPreferredBrowser",
)

# These are `{account uuid: value}` maps on disk. The uuid is an account
# identifier with no analytic value, so it is replaced with an opaque label.
_BY_ACCOUNT_PREFERENCE_KEYS = (
    "bypassPermissionsGateByAccount",
    "coworkModelAutoFallbackByAccount",
)

# Directory names that, granted whole, hand over a whole category of a
# person's files rather than one piece of work. Used to *flag*, never to
# score -- how bad a broad grant is depends entirely on what's in it, which
# this module deliberately does not look at.
_BROAD_GRANT_TOP_LEVEL_DIRS = {
    "documents", "downloads", "desktop", "pictures", "movies", "music",
    "library", "public", "dropbox", "onedrive", "google drive", "icloud drive",
}
_BROAD_GRANT_CODE_DIRS = {
    "code", "src", "dev", "developer", "projects", "repos", "repositories",
    "git", "workspace", "work",
}
_CLOUD_STORAGE_ROOT_MARKERS = (
    "onedrive", "dropbox", "google drive", "googledrive", "icloud",
    "box sync", "sharepoint", "cloudstorage",
)
_CLOUD_STORAGE_CONTAINER = "cloudstorage"
_UNRECOGNIZED_VALUE_MAX_CHARS = 200


def _grant_breadth(path: str) -> dict[str, Any]:
    """Describe how broad one standing folder grant is.

    Deliberately a flag with a plain-language reason, not a score: "your
    whole OneDrive" and "one client project folder" are obviously different
    exposures, but ranking them numerically would invent a judgment this
    module has no basis for. The calling skill decides what matters."""
    cleaned = path.rstrip("/\\") or path
    candidate = PurePath(cleaned)
    home = PurePath(str(Path.home()))
    reasons: list[str] = []
    depth: int | None = None

    if candidate == home:
        reasons.append("your entire home folder -- every file you own")
        depth = 0
    else:
        try:
            relative = candidate.relative_to(home)
        except ValueError:
            relative = None

        if relative is None:
            if len(candidate.parts) <= 2:
                reasons.append("a whole drive or filesystem root")
        else:
            parts = list(relative.parts)
            depth = len(parts)
            lowered = [part.lower() for part in parts]
            if depth == 1 and lowered[0] in _BROAD_GRANT_TOP_LEVEL_DIRS:
                reasons.append(f"the whole {parts[0]} folder")
            if depth == 1 and lowered[0] in _BROAD_GRANT_CODE_DIRS:
                reasons.append(f"an entire code directory ({parts[0]})")

        lowered_all = [part.lower() for part in candidate.parts]
        last = lowered_all[-1] if lowered_all else ""
        parent = lowered_all[-2] if len(lowered_all) > 1 else ""
        if any(last.startswith(marker) for marker in _CLOUD_STORAGE_ROOT_MARKERS) or (
            parent == _CLOUD_STORAGE_CONTAINER
        ):
            reasons.append(
                "a whole cloud-storage account root -- everything synced to it, "
                "including whatever gets added later"
            )

    return {
        "path": path,
        "depth_below_home": depth,
        "looks_broad": bool(reasons),
        "breadth_reasons": reasons,
    }


def _grant_list(raw: Any) -> list[dict[str, Any]]:
    return [_grant_breadth(p) for p in _normalize_folders(raw)]


def _grant_section(raw: Any, label: str) -> dict[str, Any]:
    grants = _grant_list(raw)
    return {
        "setting": label,
        "count": len(grants),
        "broad_count": sum(1 for g in grants if g["looks_broad"]),
        "grants": grants,
    }


def _by_account_summary(raw: Any) -> Any:
    """A `{account uuid: value}` preference, with the uuids replaced by
    opaque labels. Passed through unchanged when it isn't that shape."""
    if not isinstance(raw, dict):
        return raw
    # `any_enabled` is computed over usable values only. `bool(float("nan"))`
    # is True, so a corrupt entry would otherwise be dropped from
    # `values_by_account` on the way out (see `_drop_non_finite`) while the
    # summary it fed still asserted "yes, this is switched on somewhere" --
    # a claim derived from corruption, surviving the drop that was supposed
    # to remove it.
    usable = [
        value
        for value in raw.values()
        if not (isinstance(value, float) and not math.isfinite(value))
    ]
    return {
        "account_count": len(raw),
        "values_by_account": {
            f"account {index}": value
            for index, value in enumerate(raw.values(), start=1)
        },
        "any_enabled": any(bool(v) for v in usable),
        "unusable_value_count": len(raw) - len(usable),
    }


def _json_size(value: Any) -> int | None:
    try:
        return len(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return None


def _preference_value_summary(value: Any) -> Any:
    """Scalars pass through; containers are reduced to their shape.

    Unrecognized preferences exist to catch schema drift, and for that a
    caller needs to know the key appeared and roughly what it holds -- not
    to receive an arbitrarily large blob of UI state that no one asked
    for."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) <= _UNRECOGNIZED_VALUE_MAX_CHARS:
            return value
        return {
            "type": "string",
            "char_count": len(value),
            "preview": value[:_UNRECOGNIZED_VALUE_MAX_CHARS] + " ...[truncated]",
        }
    if isinstance(value, list):
        return {"type": "list", "item_count": len(value), "size_chars": _json_size(value)}
    if isinstance(value, dict):
        return {"type": "object", "field_count": len(value), "size_chars": _json_size(value)}
    return {"type": type(value).__name__}


# Top-level keys of `claude_desktop_config.json` this module actually
# consumes. `preferences` is unpacked into `settings`/`access_scope`;
# `coworkUserFilesPath` becomes `user_files_path`. Everything else at the
# top level is, by definition, something this plugin doesn't know about --
# including `mcpServers`, which exists on some installs and not others
# (absent on the machine this was confirmed against).
_DESKTOP_CONFIG_HANDLED_TOP_LEVEL_KEYS = ("preferences", "coworkUserFilesPath")


def read_desktop_preferences(base_dir: Path) -> dict[str, Any]:
    """`claude_desktop_config.json`, split into the parts that answer
    different questions: the configured output folder, standing folder
    grants, named toggles, and everything else.

    Two levels of "everything else", both surfaced by
    `build_desktop_settings` and neither dropped:

    - keys inside `preferences` that aren't in `_PREFERENCE_KNOWN_KEYS`;
    - keys at the file's **top level** that aren't in
      `_DESKTOP_CONFIG_HANDLED_TOP_LEVEL_KEYS` -- `mcpServers` being the
      obvious candidate.

    The second used to be silently discarded, which made a documented
    promise ("an unrecognized `mcpServers` key would show up in
    `unrecognized_settings`") untrue, and silent discarding of unknown keys
    is precisely how a reverse-engineered reader like this goes stale
    without anyone noticing. `top_level` is returned separately from
    `preferences` so the caller can keep the two namespaces apart."""
    path = base_dir / DESKTOP_CONFIG_FILENAME
    data = _load_json(path)
    if not isinstance(data, dict):
        return {
            "available": False,
            "source_file": str(path),
            "preferences": {},
            "top_level": {},
        }
    preferences = data.get("preferences")
    return {
        "available": True,
        "source_file": str(path),
        "preferences": preferences if isinstance(preferences, dict) else {},
        "top_level": {
            key: value
            for key, value in data.items()
            if key not in _DESKTOP_CONFIG_HANDLED_TOP_LEVEL_KEYS
        },
    }


def read_installed_extensions(base_dir: Path) -> dict[str, Any]:
    """`extensions-installations.json` -- what is *installed*, which no
    session file can tell you.

    This is the authoritative list, and `enabled_mcp_server_labels` in
    `read_local_sessions` is not: an extension installed but never enabled
    in any session appears nowhere in session data at all. Crossing the two
    is the only way to find something that has been sitting installed and
    unused, which is the whole reason this reader exists.

    `manifest` is measured and dropped, never returned -- it is a large
    nested blob (roughly 1.9 KB per extension on a real install) that
    produces no finding a size can't.

    `signature_status` is passed through as a drift signal for maintainers
    and nothing more. The only value observed on a real install is
    `unsigned`, and it is on Anthropic's own first-party chrome-control
    extension -- so an unsigned extension is the normal case, not a
    security finding, and this module cannot verify any extension's
    provenance. `build_desktop_settings` attaches a note saying exactly
    that whenever any extension reports it, because the field's obvious
    reading ("unsigned means unsafe") is one this plugin cannot
    substantiate and must not lead a non-technical user to."""
    path = base_dir / EXTENSIONS_FILENAME
    data = _load_json(path)
    if not isinstance(data, dict):
        return {"available": False, "source_file": str(path), "count": 0, "installed": []}
    raw = data.get("extensions")
    if not isinstance(raw, dict):
        return {"available": False, "source_file": str(path), "count": 0, "installed": []}

    installed: list[dict[str, Any]] = []
    for extension_id, entry in sorted(raw.items()):
        if not isinstance(entry, dict):
            continue
        installed_at = entry.get("installedAt")
        signature = entry.get("signatureInfo")
        manifest = entry.get("manifest")
        installed.append(
            {
                "id": entry.get("id") if isinstance(entry.get("id"), str) else extension_id,
                "version": entry.get("version"),
                "source": entry.get("source"),
                "signature_status": (
                    signature.get("status") if isinstance(signature, dict) else None
                ),
                # `installedAt` is an ISO string on a real install, but the
                # epoch form is normalized alongside it anyway -- every
                # other timestamp in this plugin comes back both ways, and
                # the schema is private enough that the shape could change.
                "installed_at": installed_at,
                "installed_at_ms": (
                    int(ms)
                    if (ms := time_window.to_epoch_ms(installed_at)) is not None
                    and time_window.iso(installed_at) is not None
                    else None
                ),
                **_instant_fields("installed_at", installed_at),
                "manifest_size_chars": _json_size(manifest) if manifest is not None else None,
                "manifest_top_level_field_count": (
                    len(manifest) if isinstance(manifest, dict) else None
                ),
            }
        )
    return {
        "available": True,
        "source_file": str(path),
        "count": len(installed),
        "installed": installed,
    }


# Only these three keys are ever read out of `config.json`. That file also
# holds `oauth:tokenCache` and `oauth:tokenCacheV2` -- live credentials --
# so it is never loaded wholesale into a response, and every key that
# survives the allowlist is checked against `_is_secret_key` as well. Two
# independent gates, because one typo in an allowlist would otherwise be
# enough to leak a token.
_APP_CONFIG_ALLOWLIST = ("first_launch_at", "locale", "userThemeMode")


def _read_app_config_allowlisted(path: Path) -> dict[str, Any]:
    """The allowlisted subset of `config.json`, and nothing else. Returns
    `{}` when the file is missing or malformed.

    Kept as its own function so the guarantee is directly testable: hand it
    a config containing `oauth:tokenCache` and nothing secret-shaped can
    come back out."""
    data = _load_json(path)
    if not isinstance(data, dict):
        return {}
    return {
        key: data[key]
        for key in _APP_CONFIG_ALLOWLIST
        if key in data and not _is_secret_key(key)
    }


def read_app_config(base_dir: Path) -> dict[str, Any]:
    """First launch, locale, and theme from `config.json` -- allowlisted.

    `first_launch_at` (epoch milliseconds) is the useful one: it is the
    denominator for every rate this plugin can compute. "Forty chats" means
    something different in a week-old install than in a year-old one.

    `account_tenure_days` is computed **only** from a first-launch value
    that renders as a real date. A garbled field can normalize to a
    perfectly good number and still be un-renderable (`1e18` milliseconds
    is roughly the year 31,000,000), and deriving a tenure from one
    produced figures around 11.5 billion days sitting beside a `null` date
    -- an obviously broken number in the one field a report is most likely
    to quote as a plain fact. A first launch in the future gets the same
    treatment: there is no honest tenure to report, so it comes back None
    and `first_launch_at_usable` says why the denominator is missing."""
    path = base_dir / APP_CONFIG_FILENAME
    allowlisted = _read_app_config_allowlisted(path)
    if not allowlisted:
        return {"available": False, "source_file": str(path)}

    first_launch = allowlisted.get("first_launch_at")
    rendered = _instant_fields("first_launch_at", first_launch)
    first_launch_ms = time_window.to_epoch_ms(first_launch)
    usable = first_launch_ms is not None and rendered["first_launch_at_iso"] is not None

    tenure_days = None
    if usable:
        now_ms = datetime.now(UTC).timestamp() * 1000.0
        elapsed = round((now_ms - first_launch_ms) / _MS_PER_DAY, 1)
        tenure_days = elapsed if elapsed >= 0 else None

    return {
        "available": True,
        "source_file": str(path),
        "first_launch_at": first_launch,
        **rendered,
        # False means the file carries a first-launch value this plugin
        # can't read as a date, so there is no verified start date behind
        # any per-week or per-month rate in this run.
        "first_launch_at_usable": usable and tenure_days is not None,
        "account_tenure_days": tenure_days,
        "locale": allowlisted.get("locale"),
        "user_theme_mode": allowlisted.get("userThemeMode"),
    }


# What to say about a remote-session grant whose Space can't be named. Each
# one describes the *situation*, because "session_01SNta..." tells a user
# nothing and a report that prints it has not resolved anything.
_SESSION_SPACE_UNRESOLVED_LABELS = {
    "session_not_listed": (
        "an unidentified Space -- this session isn't listed in "
        "remote-session-spaces.json on this device"
    ),
    "no_space_id_recorded": (
        "an unidentified Space -- the session is listed but carries no Space id"
    ),
    "space_id_not_in_spaces_json": (
        "a Space that is no longer listed in spaces.json -- most likely deleted "
        "in the app after this grant was made"
    ),
    "space_listed_without_a_name": (
        "a Space that spaces.json lists without a name -- it still exists and was "
        "never given one, so there is no name to quote"
    ),
}


def _resolve_session_space(
    session_id: str,
    session_spaces: dict[str, Any],
    space_names: dict[str, Any],
) -> dict[str, Any]:
    """Turn one `session_01...` grant key into something a person can act on.

    Every outcome is named. An unresolvable id comes back with
    `space_label` describing *why* it couldn't be resolved rather than the
    raw id -- a bare uuid in a report reads as an answer while being none,
    which is the defect this exists to fix."""
    lookup = session_spaces["by_session_id"].get(session_id)
    space_id = lookup.get("space_id") if lookup else None
    name = space_names.get(space_id) if isinstance(space_id, str) else None

    if lookup is None:
        resolution = "session_not_listed"
    elif not isinstance(space_id, str) or not space_id:
        resolution = "no_space_id_recorded"
    elif isinstance(name, str) and name.strip():
        resolution = "matched"
    elif space_id in space_names:
        # Listed, and blank -- not deleted. Different sentence.
        resolution = "space_listed_without_a_name"
    else:
        resolution = "space_id_not_in_spaces_json"

    return {
        "space_id": space_id,
        "space_name": name if resolution == "matched" else None,
        "space_resolved": resolution == "matched",
        "space_resolution": resolution,
        "space_label": (
            name if resolution == "matched" else _SESSION_SPACE_UNRESOLVED_LABELS[resolution]
        ),
        # The folders and memory switch this session was actually started
        # with, from the lookup table -- not from the grant list, which only
        # records what was approved.
        "space_folders": lookup.get("folders") if lookup else [],
        "space_memory_enabled": lookup.get("memory_enabled") if lookup else None,
    }


def build_desktop_settings() -> dict[str, Any]:
    """One consolidated, redacted view of the account-level Desktop files:
    standing folder grants, app-wide toggles, installed extensions, and the
    allowlisted slice of `config.json`.

    No `since`/`until`. These are current-state settings, not a time
    series -- there is no history of when a toggle was flipped, so a window
    would have nothing to filter and would only imply a precision this data
    doesn't have.

    `unrecognized_settings` spans **both** namespaces in
    `claude_desktop_config.json`: unknown keys inside `preferences`, and
    unknown keys at the file's top level (`mcpServers` among them). The
    latter used to be dropped without a trace.

    `access_scope.broad_grants` is the deduplicated, lifetime-separated
    view of broad folder access: one entry per distinct path, standing
    grants and session-scoped grants kept apart, because summing the
    per-setting counts double-counts a folder listed in two settings and
    calling a session grant "standing" is simply wrong.

    `access_scope.remote_session_folder_grants.by_session` carries the
    Space each `session_01...` id belongs to, resolved through
    `remote-session-spaces.json` and then `spaces.json`. Quote
    `space_label`; an id that can't be resolved gets a `space_label` saying
    why, never the bare id, which names nothing the user has seen.

    `scheduled_tasks` reads `scheduled-tasks.json` -- a file the docs
    previously asserted did not exist, on the strength of an empty array
    read as an absent feature. It reports the task count, the
    `recordedSkips` identifiers and the two boundary-stamp flags.
    **`task_entry_shape_verified` is `False`**: the array has never been
    observed populated, so entries come through verbatim-but-redacted and
    no field inside one is named or relied on.

    Every date here comes back in both forms, the same convention
    `get_plan_usage_history` uses: `*_iso` in UTC for comparison, and
    `*_local_iso`/`*_local_date`/`*_local_weekday` on this machine's clock
    for anything a person reads, with `timezone` naming the zone. This tool
    used to emit UTC only, which on a machine west of UTC reported a
    first-launch date that was wrong by a calendar day and a weekday -- in
    the one figure the docs tell an analyst to quote back.

    `unusable_number_count` is how many `NaN`/`Infinity` values were
    dropped out of the pass-through paths (see `_drop_non_finite`).
    Dropped, never clamped, and counted rather than silent.

    Every section degrades to `available: false` with a note when its file
    is missing; a fresh install, a different platform, or a session without
    file access is normal, not an error."""
    base_dir = find_desktop_base_dir()
    notes: list[str] = []

    if base_dir is None:
        return {
            "available": False,
            "desktop_base_dir": None,
            "timezone": time_window.local_timezone_label(),
            "user_files_path": None,
            "access_scope": None,
            "settings": {},
            "unrecognized_settings": {},
            "unusable_number_count": 0,
            "scheduled_tasks": {
                "available": False,
                "file_count": 0,
                "scheduled_task_count": 0,
                "task_entry_shape_verified": False,
            },
            "extensions": {"available": False, "count": 0, "installed": []},
            "app_config": {"available": False},
            "notes": [
                "No Claude Desktop app-data directory is visible to this session, so "
                "no account-level settings could be read. See check_data_access -- "
                "this is usually a folder-access problem, not an absent install."
            ],
        }

    config = read_desktop_preferences(base_dir)
    preferences = config["preferences"]

    known: dict[str, Any] = {}
    for key in _PREFERENCE_KNOWN_KEYS:
        if key not in preferences:
            continue
        value = preferences[key]
        known[key] = (
            _by_account_summary(value) if key in _BY_ACCOUNT_PREFERENCE_KEYS else value
        )

    handled = set(_PREFERENCE_KNOWN_KEYS) | set(_PREFERENCE_ACCESS_KEYS)
    unrecognized: dict[str, Any] = {}
    # Counted per namespace, not as one total. The same counter used to
    # feed a note that called every withheld key a "preference key", so a
    # top-level `globalShortcutToken` was reported to the analyst as
    # something it isn't -- and "check your preferences" is then the wrong
    # place to send them looking.
    withheld_preference_keys = 0
    withheld_top_level_keys = 0
    for key, value in preferences.items():
        if key in handled:
            continue
        if _is_secret_key(key):
            withheld_preference_keys += 1
            continue
        unrecognized[key] = _preference_value_summary(value)

    # ...and the file's own top level, which used to be dropped entirely.
    # An `mcpServers` key added by a future Desktop build would have gone
    # unnoticed here despite the docs promising it wouldn't. Values are
    # summarized like any other unrecognized key, which also means a server
    # block's `env`/`args` never come through verbatim.
    for key, value in config["top_level"].items():
        if _is_secret_key(key):
            withheld_top_level_keys += 1
            continue
        # A top-level key can, in principle, share a name with a preference
        # key. They are different settings; label rather than clobber.
        unrecognized[f"{key} (top level)" if key in unrecognized else key] = (
            _preference_value_summary(value)
        )

    # `remoteSessionFolderGrants` is keyed by raw `session_01...` ids, which
    # name nothing a user has ever seen. `remote-session-spaces.json` is the
    # lookup table that turns each one into a Space, and `spaces.json` turns
    # that Space id into its name -- so the grant can be reported as "the
    # Vacation - July 2026 Space" instead of an opaque token.
    session_spaces = read_remote_session_spaces(base_dir)
    space_names = {s["id"]: s.get("name") for s in read_spaces(base_dir) if s.get("id")}

    session_grants_raw = preferences.get("remoteSessionFolderGrants")
    session_grants: list[dict[str, Any]] = []
    session_grant_paths: list[dict[str, Any]] = []
    if isinstance(session_grants_raw, dict):
        for session_id, paths in session_grants_raw.items():
            grants = _grant_list(paths)
            session_grant_paths.extend(grants)
            session_grants.append(
                {
                    "session_id": session_id,
                    "path_count": len(grants),
                    "grants": grants,
                    **_resolve_session_space(session_id, session_spaces, space_names),
                }
            )

    resolved_session_count = sum(1 for g in session_grants if g["space_resolved"])
    unique_session_paths = {g["path"] for g in session_grant_paths}
    trusted_section = _grant_section(
        preferences.get("localAgentModeTrustedFolders"),
        "localAgentModeTrustedFolders",
    )
    consent_section = _grant_section(
        preferences.get("remoteFolderConsentMemory"), "remoteFolderConsentMemory"
    )

    # Deduplicated across settings, and split by how long the grant lasts.
    #
    # Both distinctions were being lost. Summing the three sections' broad
    # counts double-counts a folder that is both standing-trusted and
    # remembered-consented -- one folder, one exposure, counted twice --
    # and folding the session-scoped grants into that total called them
    # "standing", which they are not: a session grant applies to one remote
    # session and does not carry forward on its own. Keeping them separate
    # is what makes the two sentences in `notes` consistent with each other.
    standing_broad_paths = {
        g["path"]
        for g in trusted_section["grants"] + consent_section["grants"]
        if g["looks_broad"]
    }
    session_broad_paths = {
        g["path"] for g in session_grant_paths if g["looks_broad"]
    } - standing_broad_paths

    access_scope = {
        "trusted_folders": trusted_section,
        "remote_folder_consent": consent_section,
        "broad_grants": {
            "standing_path_count": len(standing_broad_paths),
            "standing_paths": sorted(standing_broad_paths),
            "session_scoped_path_count": len(session_broad_paths),
            "session_scoped_paths": sorted(session_broad_paths),
            "note": (
                "Distinct paths, deduplicated across settings. Standing paths were "
                "approved once and stay approved; session-scoped paths were granted "
                "to one remote session and are listed separately because they are "
                "not standing grants. Do not add the two together and call the sum "
                "'standing grants'."
            ),
        },
        "remote_session_folder_grants": {
            "setting": "remoteSessionFolderGrants",
            "session_count": len(session_grants),
            "unique_path_count": len(unique_session_paths),
            "broad_path_count": sum(
                1
                for path in unique_session_paths
                if any(g["looks_broad"] for g in session_grant_paths if g["path"] == path)
            ),
            "resolved_space_count": resolved_session_count,
            "unresolved_space_count": len(session_grants) - resolved_session_count,
            "space_lookup_available": session_spaces["available"],
            "by_session": session_grants,
        },
    }

    scheduled_tasks = read_scheduled_tasks(base_dir)
    extensions = read_installed_extensions(base_dir)
    app_config = read_app_config(base_dir)
    configured_output = read_configured_output_dir(base_dir)

    if not config["available"]:
        notes.append(
            "claude_desktop_config.json is missing or unreadable, so standing folder "
            "grants and app-wide toggles couldn't be checked. Per-session folder "
            "access (list_local_workspace's user_selected_folders) still works and is "
            "the weaker signal -- it shows folders a session used, not folders Claude "
            "is standing-trusted with."
        )
    else:
        notes.append(
            "Trusted folders and remembered consents are STANDING grants: they were "
            "approved once and stay approved for future sessions until removed in "
            "Settings. That makes them a much stronger scope signal than the "
            "per-session folder list, which only shows what one chat happened to open."
        )
        if standing_broad_paths:
            notes.append(
                f"{len(standing_broad_paths)} distinct standing grant(s) cover a "
                "whole category of files (a home folder, a full cloud-storage "
                "account, or a whole code directory) rather than one piece of work. "
                "That is a count of distinct folders, not of settings entries -- a "
                "folder that is both trusted and remembered-consented is one "
                "exposure, not two. Flagged, not scored: whether it's a problem "
                "depends on what's in them."
            )
        if session_broad_paths:
            notes.append(
                f"A further {len(session_broad_paths)} broad folder(s) were granted "
                "to a single remote session rather than standing-trusted. These are "
                "NOT standing grants -- they don't carry forward to future sessions "
                "on their own -- so they are a weaker finding than the ones above "
                "and must not be described as standing."
            )
        if session_grants:
            unresolved = len(session_grants) - resolved_session_count
            notes.append(
                f"{len(session_grants)} remote session(s) hold folder grants. "
                f"{resolved_session_count} of them resolve to a named Space via "
                "remote-session-spaces.json; quote `space_label` (or `space_name`), "
                "never the `session_id` -- a `session_01...` token names nothing the "
                "user has ever seen in the app."
                + (
                    f" {unresolved} could not be resolved; each says why in its own "
                    "`space_label` rather than falling back to the raw id."
                    if unresolved
                    else ""
                )
            )
        elif not session_spaces["available"]:
            notes.append(
                "No remote-session-spaces.json is readable on this device, so any "
                "remote-session grant would have had to be reported by its raw "
                "session id. There are none this run."
            )
    if scheduled_tasks["available"]:
        if scheduled_tasks["scheduled_task_count"]:
            notes.append(
                f"{scheduled_tasks['scheduled_task_count']} scheduled task(s) are "
                "recorded on this device. **The shape of a task entry is "
                "UNVERIFIED** -- this plugin has never observed a populated list on "
                "a real install, so entries are passed through verbatim (minus "
                "redaction) and no field of one is named or promised. Read them, "
                "don't parse them, and don't build a count or a claim on any "
                "individual field."
            )
        else:
            notes.append(
                "scheduled-tasks.json exists on this device and lists no scheduled "
                "tasks. Report exactly that. It does NOT mean the feature is absent "
                "-- the file and its schema are here -- and it is not proof the "
                "account has none: tasks run server-side, and an empty list may mean "
                "none are scheduled or may mean this device isn't where they are "
                "recorded. Cross it against the `coworkScheduledTasksEnabled` and "
                "`ccdScheduledTasksEnabled` toggles in `settings` before saying "
                "anything about whether the user schedules work."
            )
    else:
        notes.append(
            "No scheduled-tasks.json is readable on this device, so nothing can be "
            "said about scheduled tasks either way this run."
        )
    if scheduled_tasks.get("oversized_task_count"):
        notes.append(
            f"{scheduled_tasks['oversized_task_count']} scheduled-task entr(ies) were "
            f"larger than the {scheduled_tasks['max_task_entry_chars']}-character "
            "per-entry cap and come back as a measurement of themselves rather than "
            "verbatim. A task carries whatever prompt text it runs, so one entry can "
            "be any size at all. Say the entry exists and was too large to include; "
            "don't describe what is in it."
        )
    if withheld_preference_keys or withheld_top_level_keys:
        where = " and ".join(
            part
            for part in (
                f"{withheld_preference_keys} inside the `preferences` block"
                if withheld_preference_keys
                else "",
                f"{withheld_top_level_keys} at the config file's top level"
                if withheld_top_level_keys
                else "",
            )
            if part
        )
        notes.append(
            f"{withheld_preference_keys + withheld_top_level_keys} setting key(s) were "
            f"withheld because their names look secret-shaped "
            f"(token/secret/auth/cookie/key): {where}. The two are different "
            "namespaces and are counted separately -- calling a top-level key a "
            "'preference' would send anyone looking for it to the wrong place. "
            "Nothing credential-like is ever returned by this plugin."
        )
    if unrecognized:
        notes.append(
            f"{len(unrecognized)} setting key(s) aren't ones this plugin knows by "
            "name -- from the `preferences` block and from the config file's top "
            "level alike (`mcpServers`, if this install has one, lands here). "
            "They're listed under `unrecognized_settings` (values summarized, not "
            "dumped) rather than dropped, because that list is how this plugin "
            "notices Claude Desktop shipped new settings -- silently dropping new "
            "keys is how a reader like this goes stale without anyone noticing. "
            "Don't turn an unrecognized key into a finding on its own."
        )
    if not extensions["available"]:
        notes.append(
            "No extensions-installations.json, so the installed-extension list is "
            "unavailable this run. Don't fall back to the per-session "
            "`enabled_mcp_server_labels` and call it 'installed' -- that field only "
            "shows what a session switched on."
        )
    elif extensions["count"]:
        notes.append(
            f"{extensions['count']} extension(s) are INSTALLED. Cross this against "
            "`enabled_mcp_server_labels` and `mcp_servers_invoked` from "
            "list_local_workspace: an extension that appears here and in neither of "
            "those has been installed and never used, which no other data source can "
            "show."
        )
        unsigned = sum(
            1 for e in extensions["installed"] if e["signature_status"] == "unsigned"
        )
        if unsigned:
            notes.append(
                f"{unsigned} of {extensions['count']} installed extension(s) report "
                "`signature_status: unsigned`. **That is normal and is not a security "
                "finding on its own.** Anthropic's own first-party chrome-control "
                "extension reports `unsigned` on a real install -- it is the only "
                "value observed in practice -- so the field says nothing about "
                "whether an extension is trustworthy, and this plugin has no way to "
                "verify an extension's provenance. Never tell a user an extension is "
                "unsafe on this basis. It is reported only so a maintainer notices if "
                "the field ever starts carrying real values."
            )
    if not app_config["available"]:
        notes.append(
            "config.json is missing or unreadable, so first-launch date, locale, and "
            "theme are unavailable. Any per-week or per-month rate computed elsewhere "
            "in this run has no verified start date behind it."
        )
    elif not app_config["first_launch_at_usable"]:
        notes.append(
            "config.json is readable, but its first-launch value isn't something this "
            "plugin can read as a real date, so `account_tenure_days` is null rather "
            "than a number derived from it. A rejected timestamp must not become a "
            "tenure: doing that produced figures in the billions of days sitting "
            "beside a null date. Any per-week or per-month rate in this run has no "
            "verified start date behind it -- say so instead of quoting one."
        )
    notes.append(_local_time_note(app_config.get("first_launch_at")))

    result = {
        "available": True,
        "desktop_base_dir": str(base_dir),
        # Named once, at the top, because three different blocks below
        # carry dates a person will read: extension install dates, the
        # account's first launch, and each probed file's last-modified
        # time. All of them come back in UTC *and* local form.
        "timezone": time_window.local_timezone_label(
            app_config.get("first_launch_at")
        ),
        "user_files_path": {
            "path": configured_output,
            "source": DESKTOP_CONFIG_FILENAME if configured_output else None,
            "setting": "coworkUserFilesPath",
        },
        "access_scope": access_scope,
        "settings": known,
        "unrecognized_settings": unrecognized,
        "settings_source_file": config["source_file"],
        "scheduled_tasks": scheduled_tasks,
        "extensions": extensions,
        "app_config": app_config,
        "notes": notes,
    }
    # Second line of defence. The allowlist above is the real guarantee;
    # this is the belt to its braces, and it also covers any future field
    # added to this response without thinking about it.
    #
    # Then the same treatment for non-finite numbers. Unlike the usage
    # reader, most of what this tool returns is a *pass-through* -- a
    # preference value, an unrecognized key's scalar, a per-account map's
    # value, an `installedAt` copied through verbatim, a scheduled-task
    # entry of unverified shape -- and every one of those paths can carry a
    # bare `NaN`/`Infinity` straight out of a corrupt file into a response
    # that `json.dumps(..., allow_nan=False)` refuses. Dropped and counted,
    # never clamped: there is no honest number to put in a corrupt one's
    # place, and manufacturing one is worse than reporting nothing.
    cleaned, dropped = _drop_non_finite(redact(result))
    cleaned["unusable_number_count"] = dropped
    if dropped:
        cleaned["notes"].append(
            f"{dropped} value(s) in the settings files are not numbers a response can "
            "carry (`NaN` or `Infinity`, which Claude Desktop's own files can hold and "
            "which a strict JSON reader rejects outright). They were dropped rather "
            "than replaced with a plausible-looking number, so they read as 'not "
            "recorded'. A nonzero count here means at least one settings file is "
            "partly corrupt -- worth saying out loud rather than treating the fields "
            "as simply absent."
        )
    return cleaned
