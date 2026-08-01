"""FastMCP server bundled with the shadetree-ai-plugins 'claude-usage-analyzer'
plugin.

Scope is Claude Desktop / Cowork -- this plugin is for business users of
the Desktop app. Claude Code CLI data is deliberately out of scope and is
never read; the CLI ships its own `/doctor`, `/usage`, and `/context`
commands for that surface, and duplicating them here would give a business
user a report about a product they don't use.

Seven tools, each thin over one of this package's modules:

- `check_data_access` -- read-only access check across the Desktop app-data
  and user-visible output locations (see local_data.py). Call this first in
  a session.
- `list_local_workspace` -- redacted inventory of local Desktop/Cowork
  usage: Spaces, cached cloud Projects, Chat/Cowork sessions, and the
  reconstructed chat->Project membership. Optional filter/projection params
  (session_ids/project_uuid/folder_path/fields) scope this down instead of
  always paying for the full dump.
- `get_project_membership` -- just the project/Space join key for local
  sessions (no titles/transcripts/model data), meant to chain after the
  harness's own `session_info.list_sessions` and before `read_transcript`.
- `get_instructions_inventory` -- all three layers of standing instructions
  (global custom instructions, Space `instructions`, cloud Project
  `prompt_template`) in one place, with character counts, session reach,
  and line-level duplication. Backs the workspace checkup in
  references/workspace-checkup.md.
- `locate_export_download` -- find an already-downloaded claude.ai export
  zip (verified by content, not filename) and unpack it, for the
  scheduled-recheck flow in references/export-acquisition.md.
- `parse_export` -- parse a claude.ai account data export
  (conversations.json etc.) into compact paged files instead of a single
  huge in-context blob.
- `render_dashboard` -- render a reorganization plan (already reasoned
  about by the caller -- this tool does no clustering/analysis of its own)
  into a self-contained, re-renderable HTML dashboard, with a local
  history log so re-runs show progress over time.

None of these tools move, rename, or delete a chat or Project -- this
plugin is read-only analysis. See skills/claude-usage-analyzer/SKILL.md
for the full workflow these are meant to be called in.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from shadetree_ai_plugins_common import get_logger, state_dir

from . import dashboard, export_data, local_data

logger = get_logger("shadetree-ai-plugins-claude-usage-analyzer")

mcp = FastMCP(
    name="shadetree-ai-plugins-claude-usage-analyzer",
    instructions=(
        "Read-only analysis of local Claude Desktop/Cowork usage plus an optional "
        "claude.ai account data export, rendered as a self-updating HTML dashboard. "
        "Claude Code CLI data is out of scope and never read. Never moves, renames, "
        "or deletes a chat or Project -- it only reads and proposes. Call "
        "check_data_access first each session."
    ),
    mask_error_details=False,
)

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_WRITE_LOCAL = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}


def _default_state_dir() -> str:
    override = os.environ.get("SHADETREE_AI_PLUGINS_CLAUDE_USAGE_ANALYZER_STATE_DIR")
    if override:
        return override
    return str(state_dir("claude-usage-analyzer"))


@mcp.tool(annotations=_READ_ONLY)
def check_data_access() -> dict:
    """Preflight check: which local Claude Desktop/Cowork data locations
    (the Desktop app-data directory, the user-visible ~/Claude output
    folder) are visible to this session, per-platform. Call this first --
    if nothing is visible, this is almost always a Cowork Space/Project
    that hasn't had those folders added to its scope yet (see the warnings
    field and references/data-sources.md), not proof the data doesn't
    exist.

    Not to be confused with Claude Code's own `/doctor`, which checks a CLI
    installation. This checks only whether this plugin can *see* the data;
    the equivalent config-health review for Desktop is the workspace
    checkup in references/workspace-checkup.md."""
    result = local_data.check_data_access()
    result["state_dir"] = _default_state_dir()
    return result


@mcp.tool(annotations=_READ_ONLY)
def list_local_workspace(
    session_ids: list[str] | None = None,
    project_uuid: str | None = None,
    folder_path: str | None = None,
    fields: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Redacted inventory of local Claude Desktop/Cowork usage: Desktop
    Spaces (folder-bound Projects), cached cloud Project metadata, and
    Chat/Cowork session metadata -- each with a `default_model`/`effort`
    (the session's configured default), `memory_enabled`/`skills_enabled`/
    `plugins_enabled`, and `custom_instructions` (the account's global
    custom instructions text, when set) -- plus, when a nested per-session
    transcript exists, a `models_used` tally of model id -> message count
    joined in for per-message accuracy (a session isn't necessarily one
    model throughout) -- and the reconstructed chat->Project/Space
    membership (there is no single index for this -- see
    references/data-sources.md section 3 for the join logic used here).
    Claude Code CLI sessions are deliberately not included. Secret-shaped
    keys and heavy MCP tool-schema blobs are stripped before this ever
    returns. This is a cache, not authoritative -- say so before
    presenting counts as exact.

    All params are optional and default to the full unfiltered inventory.
    Pass `session_ids`/`project_uuid`/`folder_path` to scope `local_sessions`
    down instead of pulling everything (avoids the write-to-file-and-grep
    workaround a large, unfiltered dump forces) -- or, if all that's needed
    is the project/Space join key for a known set of session ids, call
    `get_project_membership` instead, it's cheaper. `fields` projects every
    `local_sessions` entry down to just the named keys (the join key, `id`,
    is always kept).

    `since`/`until` scope this run to a time window. Both accept a relative
    age ("90d", "6m"), an ISO date ("2026-01-01"), or epoch milliseconds;
    omit both for the full history. Prefer the relative form when today's
    date isn't certain. The response's `time_window` reports what the
    bounds resolved to, how many records were excluded, and any bad-bound
    errors -- repeat that alongside any count taken from this response,
    since a windowed count presented as an account total is simply wrong.

    Sessions are matched on *overlap* with the window, not creation date --
    a chat started in January and still worked in March is in a March
    window. The window is applied before the Project/Space membership join,
    so every grouping in the response describes the same period. Each
    session also carries `created_at_iso`/`last_activity_at_iso` alongside
    the raw epoch-millisecond fields; use those for anything you present."""
    return local_data.build_inventory(
        session_ids, project_uuid, folder_path, fields, since, until
    )


@mcp.tool(annotations=_READ_ONLY)
def get_project_membership(
    session_ids: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """The project/Space join key for local Desktop/Cowork sessions, and
    nothing else -- no titles, no transcripts, no model data (call
    `list_local_workspace` for the full inventory, or the harness's own
    `session_info.read_transcript` for content). Chain
    `session_info.list_sessions()` -> this tool -> `read_transcript` for only
    the sessions that actually matter to the question being asked, instead of
    reading every transcript blind; `session_ids` matches `session_info`'s own
    `local_<uuid>` id format directly, no translation needed.

    Returns `{"rows": [...], "uncached_project_uuids": [...]}`. Each row:
    `session_id`, `cloud_project_uuids`/`cloud_project_names` (parallel lists
    -- a session can declare more than one cloud Project; a `None` name means
    that uuid has no local `.project-cache` entry yet), `space_id`/
    `space_name`, `local_folder_paths` (the reliable signal -- always
    populated), `is_archived`. Omit `session_ids` to get every local session's
    membership row.

    `since`/`until` scope this run to a time window. Both accept a relative
    age ("90d", "6m"), an ISO date ("2026-01-01"), or epoch milliseconds;
    omit both for the full history. Prefer the relative form when today's
    date isn't certain. The response's `time_window` reports what the
    bounds resolved to, how many records were excluded, and any bad-bound
    errors -- repeat that alongside any count taken from this response,
    since a windowed count presented as an account total is simply wrong."""
    return local_data.build_project_membership(session_ids, since, until)


@mcp.tool(annotations=_READ_ONLY)
def get_instructions_inventory(
    max_chars: int = 4000,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Every layer of standing instructions on this machine in one place --
    the account's global custom instructions, each Space's `instructions`,
    and each cached cloud Project's `prompt_template` -- with the character
    counts, session reach, and line-level duplication needed to audit them
    for bloat. This is the Desktop/Cowork analogue of what Claude Code's
    `/doctor` does to `CLAUDE.md`; see
    references/workspace-checkup.md for how to turn this into findings.

    Returns:
    - `global_instructions` -- `{"variants": [...], "variant_count": int}`.
      Global instructions are stored per-session rather than in one account
      file, so distinct texts are grouped into variants sorted by most
      recent session activity. `variants[0]` is the best guess at what's in
      force now; more than one variant means the text was *edited over
      time*, not that two are competing -- don't report it as a conflict.
    - `spaces` / `cloud_projects` -- one entry each with `char_count`,
      `line_count`, `session_count` (how many local sessions this text
      actually reached), and `duplicates_global` (lines shared verbatim
      with the current global text -- paid for twice on every turn).
    - `repeated_across_projects` -- lines appearing verbatim in two or more
      Spaces/Projects; the inverse signal, standing context pasted into
      Project after Project that probably belongs in global instructions or
      a Skill.
    - `totals` -- `always_loaded_char_count` (what every session pays for)
      and `worst_case_char_count` (global plus the largest single
      Project/Space text).
    - `notes` -- data-quality caveats to repeat before presenting findings.

    `max_chars` caps each returned text string; every `char_count` is the
    true uncapped length regardless. This tool reports counts and overlap
    only -- deciding what should be trimmed needs the content, and is the
    caller's judgment, not this tool's.

    `since`/`until` scope this run to a time window. Both accept a relative
    age ("90d", "6m"), an ISO date ("2026-01-01"), or epoch milliseconds;
    omit both for the full history. Prefer the relative form when today's
    date isn't certain. The response's `time_window` reports what the
    bounds resolved to, how many records were excluded, and any bad-bound
    errors -- repeat that alongside any count taken from this response,
    since a windowed count presented as an account total is simply wrong.

    Because the global text is stored per-session, a window here does more
    than trim: it answers "what were my standing instructions during that
    period, and how many sessions did each reach" -- a question no view in
    the app can answer."""
    return local_data.build_instructions_inventory(max_chars, since, until)


@mcp.tool(annotations=_WRITE_LOCAL)
def locate_export_download(
    search_dirs: list[str] | None = None, out_dir_base: str | None = None
) -> dict:
    """Look for an already-downloaded claude.ai export zip and unpack it --
    used by the scheduled-recheck flow in
    references/export-acquisition.md after a browser tool has (re)visited
    the export settings page and triggered a download. Matches by content
    (a `conversations.json` member inside the zip), never by filename --
    that naming isn't a documented convention. Defaults `search_dirs` to
    this OS's Downloads folder; unpacks into its own subdirectory (named
    after the zip) under out_dir_base, defaulting to a plugin state
    subdirectory.

    Returns one of:
    - `{"found": false}` -- no candidate yet; not an error, the download
      may simply not have landed. The caller should try again later
      rather than treat this as failure.
    - `{"found": true, "ambiguous": true, "candidates": [...]}` -- more
      than one zip looks like an export; ask the user which one rather
      than guessing.
    - `{"found": true, "ambiguous": false, "export_dir": ..., "zip_path":
      ...}` -- unpacked and ready to hand straight to `parse_export`."""
    resolved_search_dirs = search_dirs or [str(Path.home() / "Downloads")]
    resolved_out_dir_base = out_dir_base or str(Path(_default_state_dir()) / "export-downloads")
    return export_data.locate_export_download(resolved_search_dirs, resolved_out_dir_base)


@mcp.tool(annotations=_WRITE_LOCAL)
def parse_export(
    export_dir: str,
    out_dir: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Parse a claude.ai account data export (Settings > Account > Export
    Data, unzipped -- must contain conversations.json) into
    conversations.jsonl / projects_index.json / memory_context.md /
    stats.json under out_dir (defaults to a plugin state subdirectory),
    instead of holding the raw export in context. Each conversation record
    in conversations.jsonl includes a `models_used` tally (model id ->
    message count) when the export schema happens to carry one. Check the
    returned stats.notes for data-quality caveats (thin corpus, missing
    project links, no per-message model field, etc.) before analyzing
    further.

    `since`/`until` scope this run to a time window. Both accept a relative
    age ("90d", "6m"), an ISO date ("2026-01-01"), or epoch milliseconds;
    omit both for the full history. Prefer the relative form when today's
    date isn't certain. The response's `time_window` reports what the
    bounds resolved to, how many records were excluded, and any bad-bound
    errors -- repeat that alongside any count taken from this response,
    since a windowed count presented as an account total is simply wrong.

    The window is applied before anything is written, so the paged files on
    disk and every number in `stats` describe the same period. Conversations
    are matched on overlap of `created_at`..`updated_at`, so a chat begun
    before the window but continued inside it still counts."""
    resolved_out_dir = out_dir or str(Path(_default_state_dir()) / "export-parsed")
    return export_data.parse_export(export_dir, resolved_out_dir, since, until)


@mcp.tool(annotations=_WRITE_LOCAL)
def render_dashboard(plan: dict[str, Any], out_path: str | None = None) -> dict:
    """Render an already-reasoned-about reorganization plan into a
    self-contained HTML dashboard -- this tool does no clustering or
    analysis itself, it only presents structured input the caller
    produced. `plan` shape:

    {
      "title": str, "subtitle": str (optional),
      "data_sources": [str, ...] (e.g. ["Local Desktop/Cowork",
        "Account export (2026-07-20)"]),
      "time_window": str (optional but strongly encouraged whenever the
        run was scoped -- a human-readable rendering of the window the
        data tools resolved, e.g. "2026-05-01 to 2026-07-29 (last 90
        days)". Rendered prominently under the header; omitting it on a
        scoped run leaves every count below looking like an account
        total. Omit only when the run really did cover all history),
      "stat_tiles": [{"label": str, "value": str, "status": "good"|
        "warning"|"critical" (optional)}],
      "start_here": [{"title": str, "action": str, "where": str
        (optional, the exact place to make the change -- e.g. "Global
        custom instructions" or a Project name), "impact": str
        (optional, one short phrase on why it's worth doing)}]
        (optional but strongly encouraged: the ranked shortlist of the
        highest-impact fixes this run found, most important first,
        rendered at the top of the dashboard before every detailed
        section. Aim for about five. Every other section stays complete
        -- this one exists so a reader meets the shortlist before the
        full inventory, not so findings get dropped. Each entry should
        point at something detailed further down rather than introducing
        a finding that appears nowhere else. Omit entirely if the run
        genuinely found nothing worth prioritizing),
      "projects": [{"name": str, "description": str,
        "instructions": str (optional, the Project's custom
        instructions), "chats": [str, ...], "files": [str, ...],
        "status": str (optional, e.g. "new"/"existing"/"rename")}],
      "model_usage": [{"model": str, "count": int}] (optional, sorted
        descending by count -- from the usage & best-practices lens),
      "findings": [{"title": str, "severity": str (optional, "info"/
        "warning"/"critical"), "evidence": [str, ...] (optional),
        "recommendation": str}] (optional, one entry per usage/
        best-practices pattern actually found -- model right-sizing,
        automation candidates, prompting/context patterns; see
        references/usage-efficiency.md),
      "recommendations": [{"title": str, "kind": "existing"|"custom",
        "item_type": "skill"|"plugin"|"connector", "source": str
        (optional, only for "existing" -- which marketplace/registry it
        came from), "evidence": [str, ...] (optional), "rationale": str}]
        (optional, one entry per skill/plugin/connector recommendation --
        "existing" means install/connect something that already exists
        publicly, "custom" means it's worth building from scratch; see
        references/usage-efficiency.md's "Recommending skills, plugins &
        connectors" section),
      "leftovers": [{"name": str, "note": str}],
      "notes": [str, ...] (data-quality caveats),
      "generated_at": str (optional ISO timestamp; defaults to now)
    }

    Writes to out_path (defaults to a plugin state subdirectory) and
    appends a stat_tiles snapshot to a local history log every call, so
    re-running this later shows progress over time in the dashboard
    itself. Returns {path, html, history_runs} -- if an Artifact-
    publishing capability is available in this session, hand it `html`
    directly to publish/update a live version; otherwise tell the user
    the dashboard was saved at `path` and they can open it in a browser."""
    resolved_out_path = out_path or str(Path(_default_state_dir()) / "dashboard.html")
    history_path = str(Path(_default_state_dir()) / "history.jsonl")
    return dashboard.render_dashboard(plan, resolved_out_path, history_path)
