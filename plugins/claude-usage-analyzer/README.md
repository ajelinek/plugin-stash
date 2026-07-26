# claude-usage-analyzer

Read-only analysis of how you actually use Claude: local Claude Desktop/
Cowork/CLI data on this machine, plus an optional claude.ai account data
export, turned into a live, self-updating HTML dashboard covering two
lenses -- a workspace reorganization proposal (which chats belong in which
Projects, each Project's name/description/custom instructions, what
file/folder structure it needs, and what's stale vs. active) and a usage &
best-practices review (project/chat counts, a model-usage breakdown,
chats that used a more expensive/complex model than the task needed,
prompting/context patterns, and evidence-based automation candidates). It
never moves, renames, or deletes a chat or Project itself -- it only reads
and proposes; acting on the plan is always a separate, explicit step the
user drives.

This plugin is primarily its skill
(`skills/claude-usage-analyzer/SKILL.md`) -- the actual clustering/analysis
reasoning lives there, since deciding "these chats are one real project"
takes understanding the content, not just running a script. The bundled
MCP tools are deliberately thin: check access, read/parse data, render the
dashboard.

## Prerequisites

The machine needs `uv` installed and on `PATH`
(https://docs.astral.sh/uv/getting-started/installation/).

Full local-data analysis also needs this session to actually be able to
see `~/.claude` (Claude Code CLI) and/or Claude Desktop's app-data
directory. Installed as a Desktop/Cowork plugin inside a folder-scoped
Space or Project, those folders have to be added to that Space/Project's
file access scope explicitly -- the `usage_doctor` tool (and a SessionStart
hook) explain exactly which folders and why on first run if they aren't
visible yet.

## Install

In Claude Desktop: **Customize → Plugins → (+) → Add marketplace**, enter
`ajelinek/shadetree-ai-plugins`, then install `claude-usage-analyzer` from
the list. (Equivalent commands also work in a Desktop or Cowork chat
window: `/plugin marketplace add ajelinek/shadetree-ai-plugins` then
`/plugin install claude-usage-analyzer@shadetree-ai-plugins`.)

Ask Claude something like "analyze my Claude usage" or "help me organize my
Claude projects" to start once installed.

## What's inside

- `.claude-plugin/plugin.json` / `.mcp.json` -- plugin + MCP server
  manifest (one server, `shadetree_ai_plugins_claude_usage_analyzer`).
- `fastmcp.json` -- local dev only (`fastmcp run fastmcp.json`), not used
  by the installed plugin.
- `hooks/check_uv.py` (symlink) + `hooks/check_data_access.py` -- two
  SessionStart hooks: the shared `uv`-on-PATH check, and a plugin-specific
  check that warns (with the folder-scope explanation above) if no known
  Claude data location is visible to this session at all.
- `skills/claude-usage-analyzer/SKILL.md` -- the full workflow: check
  access, decide what data to use (local/export/both), gather it, analyze
  both lenses (reorganization, then usage & best-practices), render the
  dashboard, present for review.
- `skills/claude-usage-analyzer/references/data-sources.md` -- exactly
  where every local path comes from, per platform, and how local data and
  an account export combine/overlap.
- `skills/claude-usage-analyzer/references/export-acquisition.md` --
  requesting an account export via an already-available browser-automation
  tool (opt-in, asked every time; this plugin bundles no browser of its
  own), then closing the loop once claude.ai emails the download link:
  checking whether an already-connected mailbox (Gmail, Outlook/Microsoft
  365, etc.) is the same account and, if so, offering to search for and
  act on the export-ready message automatically (opt-in, with an optional
  scheduled recheck); otherwise pausing to ask the user for the download
  link or the unzipped folder path directly.
- `skills/claude-usage-analyzer/references/reorganization.md` -- the
  reorganization-lens analysis method and the `projects`/`leftovers` part
  of the `plan` shape that feeds the dashboard.
- `skills/claude-usage-analyzer/references/usage-efficiency.md` -- the
  usage & best-practices lens: right-sizing model choice per task from
  each session/conversation's `models_used` tally, context/prompting
  patterns worth flagging, and evidence-based automation candidates; the
  `model_usage`/`findings` part of the `plan` shape.
- `skills/claude-usage-analyzer/references/dashboard.md` -- rendering,
  the Artifact-publish-if-available fallback logic, and re-running over
  time.
- `src/shadetree_ai_plugins_claude_usage_analyzer/local_data.py` --
  cross-platform, read-only probing/parsing of local Desktop/Cowork/CLI
  data (including a per-session `models_used` tally for CLI sessions),
  redacted throughout.
- `src/shadetree_ai_plugins_claude_usage_analyzer/export_data.py` --
  parses a claude.ai account export into paged files instead of one huge
  in-context blob, including a per-conversation `models_used` tally where
  the export schema carries one.
- `src/shadetree_ai_plugins_claude_usage_analyzer/dashboard.py` -- renders
  the HTML dashboard and keeps a local history log across runs.
- `src/shadetree_ai_plugins_common` -- symlink to the repo's shared helpers
  (logging, `~/.shadetree-ai-plugins/claude-usage-analyzer/` state dir).
- `tests/test_server.py` -- in-memory tests against synthetic fixture
  directories (`uv run pytest` from repo root -- no real Claude
  Desktop/CLI install required).

## Tools

| Tool | Purpose |
|---|---|
| `usage_doctor` | Preflight: which local data locations are visible to this session, per-platform. Call first. |
| `list_local_workspace` | Redacted inventory of local Desktop/Cowork/CLI usage, including a per-CLI-session `models_used` tally, plus reconstructed chat->Project/Space membership. |
| `locate_export_download` | Find an already-downloaded claude.ai export zip (matched by content, not filename) and unpack it -- used by the scheduled-recheck flow. |
| `parse_export` | Parse a claude.ai account data export into compact paged files, including a per-conversation `models_used` tally where the export schema carries one. |
| `render_dashboard` | Render an already-reasoned-about reorganization + usage/best-practices plan into a self-contained, re-renderable HTML dashboard with a progress-over-time history log. |

See `skills/claude-usage-analyzer/SKILL.md` for the full usage guidance and
each reference doc above for the details behind each step.

## Not built yet (by design, this version)

- **Executing** the plan -- actually creating/renaming Projects or moving
  chats in the claude.ai UI. Hand the reviewed plan over as a checklist for
  now.
- **Full automation mining** -- a dedicated program that exhaustively
  clusters recurring workflows across the account and designs the
  automation. The usage & best-practices lens flags automation
  *candidates* it notices along the way, with evidence, but doesn't go
  looking for them exhaustively or build the automation out itself.
