"""FastMCP server bundled with the shadetree-ai-plugins 'claude-usage-analyzer'
plugin.

Four tools, each thin over one of this package's modules:

- `usage_doctor` -- read-only access check across CLI/Desktop/output data
  locations (see local_data.py). Call this first in a session.
- `list_local_workspace` -- redacted inventory of local Desktop/Cowork/CLI
  usage: Spaces, cached cloud Projects, Chat/Cowork sessions, CLI sessions,
  and the reconstructed chat->Project membership.
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
        "Read-only analysis of local Claude Desktop/Cowork/CLI usage plus an optional "
        "claude.ai account data export, rendered as a self-updating HTML dashboard. "
        "Never moves, renames, or deletes a chat or Project -- it only reads and "
        "proposes. Call usage_doctor first each session."
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
def usage_doctor() -> dict:
    """Preflight check: which local Claude data locations (CLI ~/.claude,
    Claude Desktop app-data, the user-visible ~/Claude output folder) are
    visible to this session, per-platform. Call this first -- if nothing
    is visible, this is almost always a Cowork Space/Project that hasn't
    had those folders added to its scope yet (see the warnings field and
    references/data-sources.md), not proof the data doesn't exist."""
    result = local_data.check_data_access()
    result["state_dir"] = _default_state_dir()
    return result


@mcp.tool(annotations=_READ_ONLY)
def list_local_workspace() -> dict:
    """Redacted inventory of local Claude Desktop/Cowork/CLI usage: CLI
    session headers (cwd, entrypoint, task-item counts), Desktop Spaces
    (folder-bound Projects), cached cloud Project metadata, Chat/Cowork
    session metadata, and the reconstructed chat->Project/Space membership
    (there is no single index for this -- see
    references/data-sources.md section 3 for the join logic used here).
    Secret-shaped keys and heavy MCP tool-schema blobs are stripped before
    this ever returns. This is a cache, not authoritative -- say so before
    presenting counts as exact."""
    return local_data.build_inventory()


@mcp.tool(annotations=_WRITE_LOCAL)
def parse_export(export_dir: str, out_dir: str | None = None) -> dict:
    """Parse a claude.ai account data export (Settings > Account > Export
    Data, unzipped -- must contain conversations.json) into
    conversations.jsonl / projects_index.json / memory_context.md /
    stats.json under out_dir (defaults to a plugin state subdirectory),
    instead of holding the raw export in context. Check the returned
    stats.notes for data-quality caveats (thin corpus, missing
    project links, etc.) before analyzing further."""
    resolved_out_dir = out_dir or str(Path(_default_state_dir()) / "export-parsed")
    return export_data.parse_export(export_dir, resolved_out_dir)


@mcp.tool(annotations=_WRITE_LOCAL)
def render_dashboard(plan: dict[str, Any], out_path: str | None = None) -> dict:
    """Render an already-reasoned-about reorganization plan into a
    self-contained HTML dashboard -- this tool does no clustering or
    analysis itself, it only presents structured input the caller
    produced. `plan` shape:

    {
      "title": str, "subtitle": str (optional),
      "data_sources": [str, ...] (e.g. ["Local Desktop/Cowork", "CLI",
        "Account export (2026-07-20)"]),
      "stat_tiles": [{"label": str, "value": str, "status": "good"|
        "warning"|"critical" (optional)}],
      "projects": [{"name": str, "description": str,
        "instructions": str (optional, the Project's custom
        instructions), "chats": [str, ...], "files": [str, ...],
        "status": str (optional, e.g. "new"/"existing"/"rename")}],
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
