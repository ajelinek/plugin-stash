# claude-usage-analyzer

Read-only analysis of how you actually use Claude: local Claude
Desktop/Cowork data on this machine, plus an optional claude.ai account
data export, turned into a live, self-updating HTML dashboard covering
three lenses -- a workspace reorganization proposal (which chats belong in
which Projects, each Project's name/description/custom instructions, what
file/folder structure it needs, and what's stale vs. active), a usage &
best-practices review (project/chat counts, a model-usage breakdown, chats
that used a more expensive/complex model than the task needed,
prompting/context patterns, and recommended public skills/plugins/
connectors vs. custom ones worth building -- all anchored to Desktop's own
record of when you actually hit your five-hour and weekly limits -- both
reported, since the weekly one is the one that can cost you a week --
rather than inferred from message counts), and a workspace checkup (the
standing custom instructions loaded into every conversation, audited for
bloat and duplication across the global/Space/Project layers, plus what
Claude has actually remembered about you, what it is standing-trusted to
reach, and which extensions are installed but never used). It never
moves, renames, or deletes a chat or Project itself -- it only reads and
proposes; acting on the plan is always a separate, explicit step the user
drives.

Any run can cover all available history or a chosen time window -- every
data tool that reads history takes the same `since`/`until` pair, and every
response reports what that window resolved to and how much it excluded, so
a scoped count is never mistaken for an account total. (Account-level
settings are the exception: they're current state, so there's nothing to
window.)

**Built for business users of the Claude Desktop app.** Claude Code CLI
sessions are deliberately excluded -- Claude Code already ships `/doctor`,
`/usage`, and `/context` for that surface, and mixing terminal activity
into this report would both distort the counts and bury a non-developer in
irrelevance.

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
see Claude Desktop's app-data directory. Installed as a Desktop/Cowork
plugin inside a folder-scoped Space or Project, that folder has to be added
to the Space/Project's file access scope explicitly -- the
`check_data_access` tool (and a SessionStart hook) explain exactly which
folders and why on first run if they aren't visible yet.

## Install

In Claude Desktop: **Customize → Plugins → (+) → Add marketplace**, enter
`ajelinek/shadetree-ai-plugins`, then install `claude-usage-analyzer` from
the list. (Equivalent commands also work in a Desktop or Cowork chat
window: `/plugin marketplace add ajelinek/shadetree-ai-plugins` then
`/plugin install claude-usage-analyzer@shadetree-ai-plugins`.)

Ask Claude something like "analyze my Claude usage" or "help me organize my
Claude projects" to start once installed, or run `/analyze-usage`
(optionally with a window: `/analyze-usage 90d`).

## What's inside

- `.claude-plugin/plugin.json` / `.mcp.json` -- plugin + MCP server
  manifest (one server, `shadetree_ai_plugins_claude_usage_analyzer`).
- `fastmcp.json` -- local dev only (`fastmcp run fastmcp.json`), not used
  by the installed plugin.
- `hooks/check_uv.py` (bundled copy) + `hooks/check_data_access.py` -- two
  SessionStart hooks: the shared `uv`-on-PATH check, and a plugin-specific
  check that warns (with the folder-scope explanation above) if no known
  Claude data location is visible to this session at all. Note that hooks
  are a Cowork feature and are greyed out in the Chat tab, so a Chat-tab
  user never sees either warning -- `check_data_access` is the check that
  always runs.
- `commands/analyze-usage.md` -- `/analyze-usage`, an optional slash
  command that runs the skill's workflow start to finish. Takes a time
  window (`/analyze-usage 90d`, `/analyze-usage all`); with no argument it
  asks first, since that choice changes what every number means.
- `skills/claude-usage-analyzer/SKILL.md` -- the full workflow: check
  access, decide what data to use (local/export/both), gather it, analyze
  all three lenses (reorganization, usage & best-practices, workspace
  checkup), render the dashboard, present for review.
- `skills/claude-usage-analyzer/references/data-sources.md` -- exactly
  where every local path comes from, per platform, and how local data and
  an account export combine/overlap. Section 1c covers the seven
  account-level files (plan usage history, Desktop settings, installed
  extensions, the three allowlisted keys read from `config.json`, scheduled
  tasks, the session->Space lookup, and the three local memory stores) and
  carries the standing warning that these are undocumented app internals
  verified on one machine, with the procedure for re-verifying them. It
  also records the two "confirmed boundaries" earlier releases documented
  that turned out to be false, and the failure mode behind both.
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
  usage & best-practices lens: leading with real plan consumption and
  treating model tier, effort, approval mode and surface choice as proxies
  that explain it, turning limit-hit runs into an actionable finding,
  right-sizing model choice per task, context/prompting
  patterns worth flagging, evidence-based automation candidates, and
  recommending an existing public skill/plugin/connector vs. a custom one
  worth building; the `model_usage`/`findings`/`recommendations` part of
  the `plan` shape.
- `skills/claude-usage-analyzer/references/workspace-checkup.md` -- the
  workspace-checkup lens: auditing standing instructions across the
  global/Space/Project layers for cross-layer duplication, text repeated
  Project-to-Project, and content Claude could already derive for itself;
  plus local memory, standing folder grants,
  installed-versus-enabled-versus-invoked capabilities, and the app-wide
  toggles -- each named in plain language rather than by its on-disk key,
  because a business user cannot find `keepAwakeEnabled` anywhere in the
  app. Adapted from what Claude Code's `/doctor` does to `CLAUDE.md`.
- `skills/claude-usage-analyzer/references/dashboard.md` -- rendering,
  the Artifact-publish-if-available fallback logic, and re-running over
  time.
- `src/shadetree_ai_plugins_claude_usage_analyzer/local_data.py` --
  cross-platform, read-only probing/parsing of local Desktop/Cowork data,
  redacted throughout. Includes a per-message `models_used` tally via
  Cowork's own nested per-session transcript, a `default_model`/`effort`
  fallback from Desktop session metadata, `memory_enabled`/
  `skills_enabled`/`plugins_enabled`/`custom_instructions` per session, the
  chat->Project/Space membership join (including cloud Projects whose local
  metadata cache hasn't synced yet -- see `uncached_project_uuids`), and the
  four-layer standing-context inventory (the three instruction layers plus
  Claude's own local memory files, from all three on-disk stores) with
  cross-layer duplication detection. Also the two account-level readers:
  the plan usage history (sampled five-hour/seven-day window fullness, with
  at-limit runs collapsed into events for both windows and every date
  reported in this machine's own timezone) and the Desktop settings
  (standing folder grants with a plain-language breadth flag and a
  deduplicated, standing-versus-session-scoped summary, remote-session
  grants resolved to the Space they belong to, scheduled tasks as this
  device records them, installed extensions with the manifest measured and
  dropped, app-wide toggles, and a strict three-key allowlist over
  `config.json` -- which also holds OAuth token caches and is never read
  wholesale).
- `src/shadetree_ai_plugins_claude_usage_analyzer/export_data.py` --
  parses a claude.ai account export into paged files instead of one huge
  in-context blob, including a per-conversation `models_used` tally where
  the export schema carries one.
- `src/shadetree_ai_plugins_claude_usage_analyzer/time_window.py` -- the
  shared `since`/`until` scoping used by every data-pulling tool:
  normalizing the two different timestamp shapes the two sources use,
  interval-overlap matching, and the per-response `time_window` report.
- `src/shadetree_ai_plugins_claude_usage_analyzer/dashboard.py` -- renders
  the HTML dashboard and keeps a local history log across runs.
- `src/shadetree_ai_plugins_common` -- bundled copy of the repo's shared helpers
  (logging, `~/.shadetree-ai-plugins/claude-usage-analyzer/` state dir).
- `tests/test_server.py` -- in-memory tests against synthetic fixture
  directories; no real Claude Desktop install required. Includes a
  regression guard that fails if a Claude Code CLI data reader is ever
  reintroduced, one that fails if the server gains a network call, and one
  that plants OAuth token caches in a fixture `config.json` and asserts
  nothing credential-shaped survives into a response. To run them, from
  the **repo root**:

  ```bash
  uv sync --all-packages && uv run pytest
  ```

  The `uv sync --all-packages` is not optional on a fresh clone: this is a
  uv workspace, and it does not install its members by default, so
  `uv run pytest` on its own fails to import the package. Scope it to this
  plugin with `uv run pytest plugins/claude-usage-analyzer/tests`.

## Tools

| Tool | Purpose |
|---|---|
| `check_data_access` | Preflight: which local Desktop data locations are visible to this session, per-platform. Call first. Checks *visibility only* -- it is not a Desktop `/doctor`. |
| `list_local_workspace` | Redacted inventory of local Desktop/Cowork usage, including a per-session `models_used` tally (from each Cowork/Chat session's nested transcript), a `default_model`/`effort` fallback, `memory_enabled`/`skills_enabled`/`plugins_enabled`/`custom_instructions` per session, and chat->Project/Space membership taken from Claude's own record of where each chat was filed (with each attribution marked as recorded or inferred -- see `get_project_membership`). Optional `session_ids`/`project_uuid`/`folder_path`/`fields` params scope the response instead of always returning the full dump. Every timestamp comes back in this machine's own timezone alongside the UTC form. |
| `get_project_membership` | Just the project/Space join key for local sessions (cloud Project uuid/name, Space id/name, local folder paths) -- meant to chain after the harness's own `session_info.list_sessions()` and before `read_transcript`, without pulling the full inventory. Space membership comes from the `spaceId` Claude Desktop writes on the session; only a chat with no recorded Space falls back to matching its working folder, and every row says which it was (`space_source`, `space_from_app_record`, `space_matched_folder`) so a guess is never presented as the user's own filing. Rows match `list_local_workspace` field for field -- both read the same join. |
| `get_instructions_inventory` | All four layers of standing context (global custom instructions, Space `instructions`, cloud Project `prompt_template`, **and Claude's own local memory files**) in one place, with character counts, session reach, and line-level duplication both against the global text and across Projects/memory. This is also *the* place to get memory -- it's on disk, so a plain local run reads it with no export needed. Backs the workspace-checkup lens. |
| `get_plan_usage_history` | What the plan was **actually** consuming: Claude Desktop's own rolling record of how full the five-hour and seven-day usage windows got, sampled roughly every 15 minutes while the app runs. The Desktop counterpart of Claude Code's `/usage`, and the only ground truth here about real consumption -- every other usage signal is a proxy. Returns peaks, at-or-near-limit runs collapsed into single events for **both** the five-hour and seven-day windows, and per-day high-water marks. Every date and weekday is in this machine's own timezone, with the UTC form alongside. |
| `get_desktop_settings` | Account-level settings in one view: standing folder grants (approved once and still in force -- each remote-session grant resolved to the Space it belongs to rather than left as a raw session id), app-wide permission and feature toggles, what this device records about scheduled tasks, and the authoritative list of installed extensions. Per-session data cannot see any of these. No `since`/`until` -- current state, not history. |
| `locate_export_download` | Find an already-downloaded claude.ai export zip (matched by content, not filename) and unpack it -- used by the scheduled-recheck flow. |
| `parse_export` | Parse a claude.ai account data export into compact paged files. Never carries model-usage data -- confirmed absent from this export format. |
| `render_dashboard` | Render an already-reasoned-about reorganization + usage/best-practices plan (including skill/plugin/connector recommendations) into a self-contained, re-renderable HTML dashboard with a progress-over-time history log. |

Every tool that pulls sessions, conversations, or usage samples --
`list_local_workspace`, `get_project_membership`,
`get_instructions_inventory`, `parse_export`, `get_plan_usage_history` --
also takes `since`/`until`. Both accept a relative age (`"90d"`, `"6m"`),
an ISO date, or epoch milliseconds; omit both for the full history.
Matching is interval overlap, so a chat begun before the window but
continued inside it still counts -- except in `get_plan_usage_history`,
where a sample is a point in time rather than a span and so is matched on
falling inside the window. A bad or backwards bound widens the read and
reports the problem rather than silently returning nothing.

`get_desktop_settings` takes no window at all: settings are current state,
and nothing on disk records when a toggle was flipped or a folder trusted,
so a window would have nothing to filter and would imply a precision the
data doesn't have. `get_instructions_inventory`'s memory block is current
state for the same reason -- a memory file has one last-modified date, not
a history -- so a window scopes only how many sessions each memory owner
reached.

See `skills/claude-usage-analyzer/SKILL.md` for the full usage guidance and
each reference doc above for the details behind each step.

## Not built yet (by design, this version)

- **Executing** the plan -- actually creating/renaming Projects or moving
  chats in the claude.ai UI. Hand the reviewed plan over as a checklist for
  now.
- **Applying** any workspace-checkup recommendation. The checkup proposes
  instruction edits with the specific lines quoted; you make the change in
  the app. Unlike Claude Code's `/doctor`, there is no fix-it step, and
  that's deliberate -- the text is your own writing about your own
  business.
- **Full automation mining** -- a dedicated program that exhaustively
  clusters recurring workflows across the account and designs the
  automation. The usage & best-practices lens flags automation
  *candidates* it notices along the way, with evidence, but doesn't go
  looking for them exhaustively or build the automation out itself.
- **Account-wide consumption.** `get_plan_usage_history` reads a local
  file Desktop keeps for itself, so it covers **this machine only**, and
  goes back only as far as the record on that machine happens to start
  (about a month on the install it was verified against -- what that start
  date *means* isn't recorded anywhere, so the analysis reports the date
  and claims no cause). Usage from the phone, the claude.ai web app, or a
  second computer isn't in it, and there's no API here to fill the gap.
  The analysis says so rather than presenting a machine-local figure as an
  account total.
- **Any dollar figure.** The usage windows are percentages of the user's
  own plan limits -- there is no price, no token count and no plan tier in
  this data, so a cost estimate would be invented. The plugin reports how
  full a limit got and what it cost the user in *interruptions*, never in
  money.
- **A full audit of existing scheduled tasks.** Partly available since
  0.6.1: `scheduled-tasks.json` does exist on disk (earlier releases
  documented, wrongly, that it didn't -- it was simply empty on the machine
  that was checked), so the plugin reports what this device records. What
  is still genuinely missing: the account's tasks rather than this device's,
  run history, and the per-task model/effort/approval settings where the
  real cost would be -- those run server-side and aren't stored locally.
  Recommending *new* tasks is unaffected.
