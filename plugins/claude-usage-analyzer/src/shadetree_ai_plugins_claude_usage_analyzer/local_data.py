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
import os
import platform
import re
from collections import Counter
from dataclasses import dataclass, field
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
# Access check -- existence/readability only, never reads content
# --------------------------------------------------------------------------------


def check_data_access() -> dict[str, Any]:
    """Report which Claude Desktop/Cowork data locations are visible to
    *this* session. A location reading as missing does not necessarily mean
    it doesn't exist on this machine -- inside a folder-scoped Cowork Space
    or Project, it may simply not be attached to this session yet. This
    function can only report what it can see; interpreting that ambiguity
    for the user is the calling skill's job.

    Deliberately does not probe Claude Code's `~/.claude` -- the CLI is out
    of scope for this plugin (see the module docstring)."""
    desktop = [check_path(str(p)) for p in desktop_dir_candidates()]
    output = [check_path(str(p)) for p in output_dir_candidates()]

    desktop_found = next((d for d in desktop if d["exists"]), None)
    output_found = any(o["exists"] for o in output)
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
        warnings.append(
            "The user-visible Claude output folder isn't visible to this session "
            "(see `output_candidates`). That's where Cowork writes Artifacts and "
            "scheduled-task results, so anything produced by a scheduled task will "
            "be missing from the analysis. Add it to this session's file access "
            "scope if scheduled-task output matters to the question being asked."
        )

    return {
        "platform": platform.system(),
        "desktop_candidates": desktop,
        "desktop_base_dir": desktop_found["path"] if desktop_found else None,
        "output_candidates": output,
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
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
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
                "synced_at": data.get("synced_at"),
                "prompt_template": data.get("prompt_template"),
                "source_file": str(meta_path),
            }
        )
    return projects


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
                "created_at_iso": time_window.iso(data.get("createdAt")),
                "last_activity_at_iso": time_window.iso(data.get("lastActivityAt")),
                "is_archived": data.get("isArchived"),
                "user_selected_folders": _normalize_folders(data.get("userSelectedFolders")),
                "user_selected_project_uuids": data.get("userSelectedProjectUuids") or [],
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


def _folder_matches_space(session_folders: list[str], space_folders: list[str]) -> bool:
    for sf in session_folders:
        for spf in space_folders:
            if sf == spf or sf.startswith(spf.rstrip("/") + "/"):
                return True
    return False


def join_local_sessions(
    sessions: list[dict[str, Any]],
    spaces: list[dict[str, Any]],
    project_cache: list[dict[str, Any]],
) -> dict[str, Any]:
    """Reconstruct which chat belongs to which Project/Space -- there is no
    single index for this, see references/data-sources.md section 3.

    A session's `user_selected_project_uuids` entry is trusted as membership
    even when that uuid has no `.project-cache` entry yet (never opened on
    this device, or synced after the cache snapshot) -- the uuid is still the
    real signal here, the cache is only used to resolve a *name*. Dropping
    uncached uuids previously meant those sessions fell through to
    folder-matching or `unfiled`, misclassifying real project members."""
    projects_by_uuid = {p["uuid"]: p for p in project_cache if p.get("uuid")}
    spaces_by_id = {s["id"]: s for s in spaces if s.get("id")}

    by_project: dict[str, list[str]] = {}
    by_space: dict[str, list[str]] = {}
    unfiled: list[str] = []
    uncached_project_uuids: set[str] = set()

    for session in sessions:
        session_id = session["id"]
        project_uuids = session["user_selected_project_uuids"]
        if project_uuids:
            for uuid in project_uuids:
                by_project.setdefault(uuid, []).append(session_id)
                if uuid not in projects_by_uuid:
                    uncached_project_uuids.add(uuid)
            continue

        matched_space_id = None
        for space in spaces:
            if _folder_matches_space(session["user_selected_folders"], space["folders"]):
                matched_space_id = space["id"]
                break
        if matched_space_id:
            by_space.setdefault(matched_space_id, []).append(session_id)
            continue

        unfiled.append(session_id)

    return {
        "by_project_uuid": by_project,
        "by_space_id": by_space,
        "unfiled_session_ids": unfiled,
        "uncached_project_uuids": sorted(uncached_project_uuids),
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
    spaces_by_id = membership["spaces_by_id"]

    session_project_uuids: dict[str, list[str]] = {}
    for uuid, ids in membership["by_project_uuid"].items():
        for sid in ids:
            session_project_uuids.setdefault(sid, []).append(uuid)
    session_space_id: dict[str, str] = {}
    for space_id, ids in membership["by_space_id"].items():
        for sid in ids:
            session_space_id[sid] = space_id

    wanted = set(session_ids) if session_ids is not None else None

    rows: list[dict[str, Any]] = []
    for session in sessions:
        session_id = session["id"]
        if wanted is not None and session_id not in wanted:
            continue

        project_uuids = session_project_uuids.get(session_id, [])
        space_id = session_space_id.get(session_id)

        rows.append(
            {
                "session_id": session_id,
                "cloud_project_uuids": project_uuids,
                "cloud_project_names": [
                    projects_by_uuid.get(uuid, {}).get("name") for uuid in project_uuids
                ],
                "space_id": space_id,
                "space_name": spaces_by_id.get(space_id, {}).get("name") if space_id else None,
                "local_folder_paths": session["user_selected_folders"],
                "is_archived": bool(session.get("is_archived")),
            }
        )

    return {
        "rows": rows,
        "uncached_project_uuids": membership["uncached_project_uuids"],
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
    Spaces/Projects/sessions and the reconstructed chat->Project join.
    Redacted throughout -- safe to hand to a caller/log.

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


def build_instructions_inventory(
    max_chars: int = _CUSTOM_INSTRUCTIONS_MAX_CHARS,
    since: Any = None,
    until: Any = None,
) -> dict[str, Any]:
    """Every layer of standing instructions on this machine in one place --
    global custom instructions, each Space's `instructions`, and each cached
    cloud Project's `prompt_template` -- with the character counts, session
    reach, and line-level duplication needed to audit them for bloat.

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
    - `totals` -- `always_loaded_char_count` (the global text, which every
      session pays for) and `worst_case_char_count` (global plus the single
      largest Project/Space text, i.e. the most standing instruction text
      any one session can be carrying).

    `max_chars` caps each returned `text`/`instructions`/`prompt_template`
    string; `char_count` is always the true, uncapped length.

    `since`/`until` scope which sessions are consulted. That changes the
    answer in a useful way rather than just trimming it: because the global
    text is stored per-session, windowing to (say) last quarter reports the
    instructions that were actually in force *then*, and the session-reach
    counts for that period. It is genuinely how to ask "what were my
    standing instructions during Q1," which no view in the app can answer."""
    window = time_window.resolve(since, until)
    base_dir = find_desktop_base_dir()
    empty_global: dict[str, Any] = {"variants": [], "variant_count": 0}
    if base_dir is None:
        return {
            "global_instructions": empty_global,
            "spaces": [],
            "cloud_projects": [],
            "repeated_across_projects": [],
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
             "latest_activity_at": None, "sample_session_ids": []},
        )
        variant["session_count"] += 1
        activity = session.get("last_activity_at") or session.get("created_at")
        if activity is not None and _activity_sort_key(activity) > _activity_sort_key(
            variant["latest_activity_at"]
        ):
            variant["latest_activity_at"] = activity
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

    return {
        "global_instructions": {
            "variants": ordered_variants,
            "variant_count": len(ordered_variants),
        },
        "spaces": space_entries,
        "cloud_projects": project_entries,
        "repeated_across_projects": repeated,
        "totals": {
            "always_loaded_char_count": global_chars,
            "worst_case_char_count": global_chars + largest_scoped,
        },
        "time_window": window_summary,
        "notes": notes,
    }
