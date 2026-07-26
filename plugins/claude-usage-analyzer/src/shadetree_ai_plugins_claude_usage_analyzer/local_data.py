"""Read-only inventory of local Claude Desktop/Cowork/CLI data on this machine.

Everything here is metadata-first and defensive: Claude Desktop's on-disk
layout is not a documented public API, it was reverse-engineered by direct
filesystem inspection (see skills/claude-usage-analyzer/references/data-sources.md
for the full writeup and provenance), and it is a *cache* the Desktop app
maintains for itself, not a source of truth this module can assume is
complete or stable across versions. Every reader here is best-effort: a
missing/malformed file is skipped, never raised.

Three product surfaces, three different local stores:

- Claude Code CLI (``~/.claude``) -- one JSONL file per terminal session,
  keyed by working directory.
- Claude Desktop / Cowork -- keyed by account/org UUID, under an
  OS-specific app-data directory. Cowork "Projects" that are folder-bound
  are called "Spaces" internally (``spaces.json``); non-folder-bound cloud
  Projects are cached separately (``.project-cache/<uuid>/metadata.json``).
- The claude.ai account data export (``conversations.json`` etc.) is a
  *different* thing entirely, handled by ``export_data.py`` -- it covers
  claude.ai web chats only and has zero overlap with what lives here.
"""

from __future__ import annotations

import json
import os
import platform
from collections import Counter
from pathlib import Path
from typing import Any

from shadetree_ai_plugins_common import check_path

# --------------------------------------------------------------------------------
# Candidate paths (per-OS, defensive -- probe for existence, never assume)
# --------------------------------------------------------------------------------


def cli_dir() -> Path:
    return Path.home() / ".claude"


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
# Access check ("doctor") -- existence/readability only, never reads content
# --------------------------------------------------------------------------------


def check_data_access() -> dict[str, Any]:
    """Report what's visible to *this* session across CLI/Desktop/output
    locations. A location reading as missing does not necessarily mean it
    doesn't exist on this machine -- inside a folder-scoped Cowork Space or
    Project, it may simply not be attached to this session yet. This
    function can only report what it can see; interpreting that ambiguity
    for the user is the calling skill's job."""
    cli = check_path(str(cli_dir()))
    desktop = [check_path(str(p)) for p in desktop_dir_candidates()]
    output = [check_path(str(p)) for p in output_dir_candidates()]

    desktop_found = next((d for d in desktop if d["exists"]), None)
    any_found = cli["exists"] or desktop_found is not None or any(o["exists"] for o in output)

    warnings: list[str] = []
    if not any_found:
        warnings.append(
            "No known Claude data location is visible to this session. If Claude "
            "Desktop and/or the CLI are actually used on this machine, this session "
            "is most likely running inside a folder-scoped Cowork Space or Project "
            "that hasn't been given access to those folders -- add them to this "
            "Space/Project's file access scope and restart. Running via the Claude "
            "Code CLI from a terminal/IDE doesn't need this: it already has ordinary "
            "filesystem access from its working directory."
        )
    elif not desktop_found:
        warnings.append(
            "Claude Code CLI data is visible but no Claude Desktop app-data directory "
            "is. If this account also uses Claude Desktop/Cowork, add its app-data "
            "folder to this session's scope to include those chats/Spaces in the "
            "analysis; otherwise this is expected for a CLI-only setup."
        )

    return {
        "platform": platform.system(),
        "cli": cli,
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


def _load_json(path: Path) -> Any | None:
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------------
# Desktop / Cowork readers
# --------------------------------------------------------------------------------


def read_spaces(base_dir: Path) -> list[dict[str, Any]]:
    """Every `local-agent-mode-sessions/<account>/<org>/spaces.json` under
    base_dir -- folder-bound ("Local") Projects, called Spaces internally."""
    spaces: list[dict[str, Any]] = []
    for spaces_path in sorted(base_dir.glob("local-agent-mode-sessions/*/*/spaces.json")):
        data = _load_json(spaces_path)
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
    from this device: `.project-cache/<uuid>/metadata.json`."""
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
                "source_file": str(meta_path),
            }
        )
    return projects


def read_local_sessions(base_dir: Path) -> list[dict[str, Any]]:
    """Cowork + Chat-tab session metadata (`local_<uuid>.json`), redacted.
    Deliberately excludes `claude-code-sessions/` -- that's the Code tab
    (CLI-in-Desktop), a different product surface from Cowork/Chat."""
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
                "is_archived": data.get("isArchived"),
                "user_selected_folders": _normalize_folders(data.get("userSelectedFolders")),
                "user_selected_project_uuids": data.get("userSelectedProjectUuids") or [],
                "cli_session_id": data.get("cliSessionId"),
                "source_file": str(session_path),
            }
        )
    return sessions


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
    single index for this, see references/data-sources.md section 3."""
    projects_by_uuid = {p["uuid"]: p for p in project_cache if p.get("uuid")}
    spaces_by_id = {s["id"]: s for s in spaces if s.get("id")}

    by_project: dict[str, list[str]] = {}
    by_space: dict[str, list[str]] = {}
    unfiled: list[str] = []

    for session in sessions:
        session_id = session["id"]
        project_uuids = [u for u in session["user_selected_project_uuids"] if u in projects_by_uuid]
        if project_uuids:
            for uuid in project_uuids:
                by_project.setdefault(uuid, []).append(session_id)
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
        "projects_by_uuid": projects_by_uuid,
        "spaces_by_id": spaces_by_id,
    }


# --------------------------------------------------------------------------------
# CLI readers
# --------------------------------------------------------------------------------

_CLI_HEADER_FIELDS = ("cwd", "timestamp", "entrypoint", "gitBranch")
_CLI_HEADER_SCAN_LINES = 5
_MODEL_TALLY_MAX_LINES = 20_000


def _model_of_cli_record(record: dict[str, Any]) -> str | None:
    """Assistant-turn records carry the model under `message.model`; check
    a top-level `model` too, defensively -- never look at message content."""
    message = record.get("message")
    if isinstance(message, dict) and isinstance(message.get("model"), str):
        return message["model"]
    if isinstance(record.get("model"), str):
        return record["model"]
    return None


def read_cli_sessions(cli_root: Path) -> list[dict[str, Any]]:
    """One entry per `~/.claude/projects/<encoded-cwd>/*.jsonl` session --
    header fields only (cwd/timestamp/entrypoint/gitBranch), a cheap total
    line count, and a per-model usage tally (model id + count only, never
    message content, capped at `_MODEL_TALLY_MAX_LINES` per session so one
    very long-running session can't blow up scan time)."""
    sessions: list[dict[str, Any]] = []
    for jsonl_path in sorted(cli_root.glob("projects/*/*.jsonl")):
        header: dict[str, Any] = {}
        line_count = 0
        model_counts: Counter[str] = Counter()
        try:
            with jsonl_path.open(encoding="utf-8") as f:
                for line_count, line in enumerate(f, start=1):  # noqa: B007
                    # Total line_count always stays exact (the loop itself is
                    # cheap) -- only the more expensive JSON-parse/model-tally
                    # work is capped, so one very long session can't blow up
                    # scan time while event_count stays accurate.
                    if line_count > _MODEL_TALLY_MAX_LINES:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(record, dict):
                        continue
                    if line_count <= _CLI_HEADER_SCAN_LINES:
                        for field in _CLI_HEADER_FIELDS:
                            if field not in header and field in record:
                                header[field] = record[field]
                    model = _model_of_cli_record(record)
                    if model:
                        model_counts[model] += 1
        except OSError:
            continue

        session_id = jsonl_path.stem
        bridge_pointer_path = jsonl_path.parent / "bridge-pointer.json"
        task_dir = cli_root / "tasks" / session_id

        sessions.append(
            {
                "session_id": session_id,
                "project_dir_encoded": jsonl_path.parent.name,
                "cwd": header.get("cwd"),
                "entrypoint": header.get("entrypoint"),
                "git_branch": header.get("gitBranch"),
                "first_timestamp": header.get("timestamp"),
                "event_count": line_count,
                "bridged_to_desktop": bridge_pointer_path.exists(),
                "task_item_count": (
                    len(list(task_dir.glob("*.json"))) if task_dir.is_dir() else 0
                ),
                "models_used": dict(model_counts.most_common()),
                "source_file": str(jsonl_path),
            }
        )
    return sessions


# --------------------------------------------------------------------------------
# Top-level inventory
# --------------------------------------------------------------------------------


def build_inventory() -> dict[str, Any]:
    """Everything list_local_workspace needs in one call: CLI sessions,
    Desktop Spaces/Projects/sessions, and the reconstructed chat->Project
    join. Redacted throughout -- safe to hand to a caller/log."""
    access = check_data_access()
    result: dict[str, Any] = {
        "access": access,
        "cli_sessions": [],
        "spaces": [],
        "project_cache": [],
        "local_sessions": [],
        "membership": None,
    }

    if access["cli"]["exists"] and access["cli"]["readable"]:
        result["cli_sessions"] = read_cli_sessions(cli_dir())

    base_dir = find_desktop_base_dir()
    if base_dir is not None:
        spaces = read_spaces(base_dir)
        project_cache = read_project_cache(base_dir)
        local_sessions = read_local_sessions(base_dir)
        result["spaces"] = spaces
        result["project_cache"] = project_cache
        result["local_sessions"] = [redact(s) for s in local_sessions]
        result["membership"] = join_local_sessions(local_sessions, spaces, project_cache)

    return result
