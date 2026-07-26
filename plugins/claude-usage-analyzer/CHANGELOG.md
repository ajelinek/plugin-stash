# Changelog

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
- Analysis and dashboard generation only -- plan execution and automation
  mining are deliberately out of scope for this version.
