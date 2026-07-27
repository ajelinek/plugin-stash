# Changelog

## 0.1.2 -- 2026-07-27

- `references/data-sources.md` expanded into a full file-reference guide
  for local Claude Code CLI/Desktop/Cowork data: every path this plugin
  actually reads keeps its existing tie to the reading code, plus a new
  catalog of adjacent files seen on disk but deliberately not read
  (`bridge-state.json`, `claude_desktop_config.json`, feature-flag/audit
  caches, global/per-Space memory files, `IndexedDB`, `Cookies`,
  `buddy-tokens.json`, etc.), Windows MSIX path-redirect provenance, and a
  `Sources` section citing the official docs and community GitHub issues
  this was reverse-engineered against.

## 0.1.1 -- 2026-07-27

- `SKILL.md` now shows its own version number right under the title,
  kept in lockstep with `plugin.json`'s `version` -- an easy way to
  confirm an installed/cached copy actually picked up the latest skill
  content instead of a stale one.

## 0.1.0 -- 2026-07-25

Initial release.

- `usage_doctor`, `list_local_workspace`, `locate_export_download`,
  `parse_export`, `render_dashboard` MCP tools.
- Read-only, cross-platform probing of local Claude Desktop/Cowork/CLI data
  (`~/.claude`, Desktop app-data incl. Windows MSIX/3P variants, the
  user-visible `~/Claude` output folder), with redaction of secret-shaped
  keys and heavy MCP tool-schema blobs before anything is ever returned.
- claude.ai account data export parsing (`conversations.json`/
  `memories.json`/`projects.json`) into paged files, with defensive
  handling of a missing per-conversation project link.
- Self-contained HTML dashboard renderer with a local history log so
  re-running analysis later shows progress over time.
- `skills/claude-usage-analyzer/SKILL.md` + reference docs covering data
  sourcing, opt-in browser-assisted export requesting, the reorganization
  analysis method, and dashboard rendering/publishing.
- Closing the export-request loop: claude.ai only ever announces a ready
  export by email (the settings page itself has no ready/pending status),
  so the acquisition flow discovers the account's own email address and
  checks whether an already-connected mailbox (Gmail, Outlook/Microsoft
  365, etc.) is that same account. If so, it offers (opt-in, every time)
  to search for and act on the export-ready message automatically, with an
  optional scheduled recheck when a scheduling capability is also
  available. Otherwise it pauses and asks the user directly for the
  download link or the unzipped folder path.
- SessionStart hooks: shared `uv`-on-PATH check, plus a new
  `check_data_access.py` explaining Desktop Space/Project folder-scope
  requirements when no local data is visible.
- A second, independent "usage & best-practices" analysis lens alongside
  the reorganization proposal: project/chat counts, a model-usage
  breakdown, chats that used a more expensive/complex model than the task
  needed, context/prompting patterns worth a look, and evidence-based
  (not exhaustive) automation candidates. `list_local_workspace`'s CLI
  sessions now carry a per-message `models_used` tally (model id ->
  message count); `parse_export` carries the same field defensively, but
  it's confirmed empty in practice -- the claude.ai web export format
  doesn't record which model generated a response at all (checked
  directly: every conversation/message key, plus a raw-text regex for any
  "model"-containing key, found zero hits), and `stats.notes` says so.
  New `skills/claude-usage-analyzer/references/usage-efficiency.md`
  covers the method, including looking up the current model lineup each
  session (a "claude-api"-style skill or Anthropic's own docs) rather than
  hardcoding model names that would go stale. `render_dashboard`'s plan
  shape gains `model_usage` and `findings` sections for this.
- Model-usage data confirmed for Cowork/Chat local sessions too, not just
  CLI: each keeps its own nested per-session CLI-format transcript
  (`local_<uuid>/.claude/projects/.../*.jsonl`, same `message.model`
  format as the top-level CLI store) -- a session isn't necessarily one
  model throughout, since sub-agents/background steps can run a cheaper
  model mid-session. `list_local_workspace` now joins this in as
  `models_used`, falling back to each session's `default_model`/`effort`
  (its configured default, from `local_<uuid>.json`) when no nested
  transcript matched. New `skills/claude-usage-analyzer/references/
  data-sources.md` section 5 summarizes where model data does and doesn't
  live across every source.
- The usage & best-practices lens now also recommends skills, plugins,
  and connectors -- explicitly split into *already available* (an
  existing public skill/plugin/connector the user should just
  install/connect, found via whatever discovery capability the
  environment offers) versus *worth building custom* (nothing existing
  fits, so a bespoke Skill/plugin is the recommendation instead).
  `render_dashboard`'s plan shape gains a `recommendations` section for
  this.
- Analysis and dashboard generation only -- plan execution and full
  automation mining are deliberately out of scope for this version.
- The export-acquisition flow now treats an already-connected mail
  connector (Gmail, Outlook/Microsoft 365, etc.) as the expected case
  rather than a long-shot, since most client accounts already have one
  enabled, while still verifying it's the same mailbox as the claude.ai
  account before using it. Adds an explicit plain-language narration
  requirement throughout that flow -- since the person on the other end
  is typically a non-technical business user, every "want me to check?"
  now states what's being checked in the same breath, and email access
  (available, unavailable, or mismatched) is always stated outright
  rather than left implicit.
