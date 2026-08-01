# Changelog

## 0.5.0 -- 2026-08-01

Widens the third lens from "audit the standing instructions" into a full
**setup & effectiveness checkup**, and fixes three bugs found while doing
it -- two of which meant the plugin had been silently returning nothing
where it claimed to return data.

### Bugs fixed

**`effort` was read from a key that does not exist.** The reader asked for
`effort`; the on-disk key is `effortOverride`. Across 67 real session files
`effort` appeared 0 times and `effortOverride` 40 times, so the field was
`None` in every response the plugin has ever returned -- while the skill
docs instructed the caller to right-size against it. Now reads
`effortOverride`, with the old key kept as a defensive fallback.

**`memories.json` was parsed against a shape no export produces.** The
payload is a single-element **list** wrapping
`{conversations_memory, project_memories, account_uuid}`; the parser
assumed a bare object and called `.get` on it. Every real export therefore
fell through to a raw-JSON-preview branch and emitted a 2,000-character
truncated dump instead of memory. Verified against two live exports; the
same fix now parses ~19,000 characters of real memory where it previously
produced a dump. Per-project memory is keyed by project uuid (which joins
to `projects_index.json`) and carries no project name -- the old code
looked for a `project_name` field that does not exist.

**The docs promised local fields that were never returned.** `SKILL.md` and
`usage-efficiency.md` both said the analysis works from
`first_human_message`/`keywords`/`tool_names`/`message_count` across both
sources. Those existed only on the export side. The two sources now have an
explicit field-mapping table, and the local side actually supplies the
equivalents.

### New session data

`list_local_workspace` read 10 of a session file's ~34 keys. It now also
returns: `permission_mode` (Manual/Auto/Skip -- a cost signal, since Auto
consumes more usage than either of the others), `initial_message`,
`enabled_mcp_tool_names` / `enabled_mcp_server_labels`,
`remote_mcp_server_names`, `plugin_names`, `slash_command_names`,
`plugin_install_count`, `egress_allowed_domains`,
`web_fetch_allowed_url_hosts`, `fs_detected_file_count`, `cwd`, and
character counts for `system_prompt` and `memory_guidelines`.

The large always-loaded blobs are measured, never returned -- roughly
49,000 and 13,500 characters of Anthropic scaffolding the user cannot edit,
so the text would be unactionable and sensitive while the size is useful
context for judging the user's own instruction length.

### What a session actually did

The nested-transcript scan already ran on every call to tally models; it
now also extracts `tools_invoked`, `mcp_servers_invoked` (parsed from the
`mcp__<server>__<tool>` convention, so the tool tally doubles as a record
of which connectors were really used), `message_count`, `touched_dirs`
(parent directories only) and `url_hosts` (hosts only). No message text,
no file contents, no full URLs.

Crossing this against the configuration fields above -- what a session
*could* do versus what it *did* -- is where most of the new findings come
from.

The scan's line cap no longer truncates. Past the cap it continues at a
sampling stride instead of stopping, because taking a flat prefix biased
every distribution toward the opening of a session -- exactly wrong for the
long agentic sessions this is meant to measure. `transcript_sampled` flags
when sampling occurred.

### New checks

- **Memory** -- stale or contradicted entries, cross-topic pollution in a
  project's pool, duplication against standing instructions, and whether
  memory is on at all. Memory is now individual categorized entries rather
  than a daily synthesized summary; guidance to "wait a day for memory to
  settle" has been removed as stale.
- **Project and Space quality** -- existing Projects graded on whether
  their name, description and instructions match what they are for, with
  replacement text written for each defect found and silence on the ones
  already fine.
- **Access scope** -- what each Space and session can reach, broad grants
  versus dedicated working folders, and scope creep measured as directories
  and hosts actually touched versus what was granted. Quotes Anthropic's
  published guidance rather than inventing a standard.
- **Capability inventory** -- installed and enabled versus actually
  invoked, yielding installed-but-never-used, used-constantly, and
  needed-but-missing.
- **Effort, approval mode and surface choice** as explicit cost levers.

### Dashboard

New optional `start_here` field on the plan: a ranked shortlist of the
highest-impact fixes, rendered above every detailed section. Analysis stays
complete -- this only decides what the reader meets first.

### Corrected guidance

- **Scheduled tasks run remotely** and no longer need the machine awake
  with Desktop open, except when a task needs local files or apps.
  Auditing *existing* scheduled tasks is documented as a confirmed hard
  boundary: nothing schedule-shaped is stored on disk, because they execute
  server-side. Recommending *new* ones is unaffected.
- **Don't advise starting a fresh chat to avoid a context limit.** Long
  conversations auto-compact and that compaction does not consume usage
  tokens. Topic separation is the real reason to start fresh.
- **Project knowledge is cached**, so only new portions count against
  limits on reuse -- the highest-leverage efficiency change available, and
  now the stated rationale behind each proposed Project's file list.
- **Context is not shared between chats in a Project** unless it is in the
  knowledge base -- a common and costly misconception.

## 0.4.0 -- 2026-07-29

Makes time scoping a first-class part of every run. Previously the only
thing that read a timestamp was the instruction-variant ordering; there was
no way to ask "how did I use Claude last quarter" short of analyzing
everything and mentally discarding the rest.

**Every data-pulling tool now takes `since`/`until`.** That's
`list_local_workspace`, `get_project_membership`,
`get_instructions_inventory`, and `parse_export`. Omit both for the full
history -- the previous behavior, still the default. Both bounds accept a
relative age (`"90d"`, `"12w"`, `"6m"`, `"2y"`), an ISO date or datetime,
or epoch milliseconds. The relative form is preferred: it resolves against
the real clock server-side, so it can't be thrown off by an uncertain sense
of today's date.

The shared implementation is the new `time_window.py`. Design decisions
worth knowing, because each one is a way this could have failed quietly:

- **Matching is interval overlap, not creation date.** A chat started in
  January and still worked in March belongs in a March window. Filtering on
  creation date would silently drop exactly the long-running work most
  worth analyzing.
- **The two sources record time differently** -- local sessions in epoch
  milliseconds, export conversations in ISO 8601 strings -- so
  normalization lives in one place. Reading the local values as seconds
  yields 1970 dates that look like real, very stale data rather than an
  error.
- **Bad bounds widen the read, never empty it.** An unparseable bound, or
  a `since` after `until`, is reported in `time_window.errors` and the read
  proceeds unbounded. Returning zero rows is indistinguishable from "this
  account has no data," which is the worst available failure mode.
- **Undated records are kept, not dropped**, and counted as `undated_kept`
  so they can be disclosed. Excluding data that merely failed to prove it
  belongs would understate the result.
- **In `list_local_workspace` the window is applied before the
  Project/Space membership join**, so the session list and every grouping
  derived from it describe the same period rather than pairing a windowed
  chat count with an all-time Project breakdown.

**Every response now carries a `time_window` block** -- resolved bounds,
`total_before`, `kept`, `excluded`, `undated_kept`, and `errors` --
alongside plain-language notes. A windowed count presented as an account
total is simply wrong, and nothing in a rendered dashboard makes the
difference obvious on its own, so `render_dashboard`'s plan gained a
first-class `time_window` field that renders under the header. A run with
no window says "Covering all available history" instead, meaning omitting
the field on a scoped run doesn't just lose context -- it asserts something
false.

`get_instructions_inventory` is the case where a window does more than
filter. Because global custom instructions are stored per-session, scoping
to a period reports the instructions that were actually in force *then*,
and the session reach for that period -- which is how to ask "what were my
standing instructions last quarter," a question no view in the app answers.

Also in this release:

- `list_local_workspace` sessions now carry `created_at_iso` and
  `last_activity_at_iso` alongside the raw epoch-millisecond fields, so
  nothing downstream has to re-derive the conversion to present a date.
- SKILL.md gains Step 2a: ask the user for the full flow or a window
  *before* pulling anything, and pass the same window to every tool in the
  run.
- `_activity_sort_key` now delegates to `time_window.to_epoch_ms` rather
  than carrying its own parsing.

## 0.3.0 -- 2026-07-29

Narrows the plugin to its actual audience -- business users of the Claude
Desktop app -- and adds the one check from Claude Code's `/doctor` that
genuinely translates to that audience.

**Breaking: Claude Code CLI data is no longer read, anywhere.** Two
reasons. Terminal sessions, working directories, and git branches are
noise to a non-developer, and they inflated every count in the analysis
with activity from a different product. And Claude Code already covers
that surface better than this plugin could: `/doctor` (alias `/checkup`)
for installation and configuration health, `/usage` (alias `/cost`) for
billing-period cost, `/context` for context-window pressure. What went:

- `cli_dir()`, `read_cli_sessions()`, and the `cli_sessions` key in
  `list_local_workspace`'s response (removed outright rather than left
  empty -- an empty list reads as "no CLI usage found" rather than "never
  looked").
- The `cli` key in the access check, and its CLI-related warnings.
- `cli_session_id` on each session record -- a bridge to Desktop's Code
  tab, which is Claude Code embedded in Desktop and also out of scope.
- The `~/.claude/tasks/` join. Cowork's own scheduled tasks were never in
  local data at all; that's now stated plainly rather than left implied.

`test_no_cli_surface_remains` fails the build if a reader creeps back in.
Cowork's *own* nested per-session transcripts stay -- they share Claude
Code's JSONL layout but are Cowork's data, and they remain the only
per-message-accurate model signal Desktop has.

**Breaking: `usage_doctor` is renamed `check_data_access`.** The old name
invited confusion with Claude Code's `/doctor`, which does something
substantially different. This tool only reports whether the current
session can *see* the data files.

**New: `get_instructions_inventory` and a workspace-checkup lens.** The
most valuable thing `/doctor` does is audit `CLAUDE.md` -- deduplicating
it, trimming content Claude could derive for itself, and moving
always-loaded guidance into things that load on demand. The Desktop
equivalent of `CLAUDE.md` is standing instructions, which live in three
separate places that no single view brings together: global custom
instructions, each Space's `instructions`, each cloud Project's
`prompt_template`. The new tool joins all three and reports:

- Character counts, line counts, and how many sessions each blob actually
  reached.
- `duplicates_global` -- lines a Project repeats verbatim from the global
  text, paid for twice on every turn in that Project.
- `repeated_across_projects` -- the inverse: the same text pasted into
  several Projects, which usually belongs one level up.
- `totals.always_loaded_char_count` / `worst_case_char_count`.

Global custom instructions are stored per-session rather than in one
account file, so distinct texts on disk mean the user *edited them over
time*, not that two are competing. They're grouped into `variants` sorted
by recency, with `variants[0]` the best guess at what's live, and a note
saying so -- reporting it as a conflict would be wrong.

The judgment half lives in the new
`references/workspace-checkup.md`: which instruction content is derivable
and should be cut (a list of the Project's own files, background already in
the description) versus what must be kept (standing constraints, tone,
internal jargon, anything contradicting a default). Unlike `/doctor`, there
is no apply step and won't be one -- the text is the user's own writing
about their own business.

- **Bug fix:** `createdAt`/`lastActivityAt` are epoch **milliseconds as
  integers** on a real install, not ISO strings. Anything sorting sessions
  by recency has to handle that; `_activity_sort_key` does, and still
  accepts an ISO string defensively since this format is unversioned.
- `_extract_custom_instructions` no longer truncates -- it returns the full
  text and callers truncate what they return, so a length-based finding
  reports the real character count rather than a capped one.
- `check_data_access` now warns separately when the user-visible `~/Claude`
  output folder isn't in scope, since that's where Cowork writes
  scheduled-task output.

## 0.2.0 -- 2026-07-29

Chains existing native tools instead of duplicating them, fixes a real bug
found by direct inspection of a real Claude Desktop install, and fills the
one confirmed gap from an external chaining-spec review.

- **Bug fix:** `read_spaces()` expected `spaces.json` to be a bare JSON
  array; the real on-disk shape is `{"spaces": [...]}` (confirmed directly).
  This silently made `spaces`/`by_space_id` empty on every real install --
  fixed to accept both shapes, defensively (this format is
  reverse-engineered and unversioned). The existing test fixture made the
  same wrong assumption and has been corrected to the real shape, plus a
  new test covers the bare-list case for backward compatibility.
- **Bug fix:** `join_local_sessions` silently dropped a session's declared
  cloud-Project uuid if that uuid had no local `.project-cache` entry yet
  (never opened on this device, or synced after the cache snapshot), which
  misclassified real project members as `unfiled_session_ids`. Fixed to
  keep all declared uuids as membership regardless of cache presence, with
  the cache only used to resolve a *name*. `membership` gains
  `uncached_project_uuids` so a caller can tell "filed under an uncached
  project" apart from a normal hit.
- New environment-settings fields, sourced from data `read_local_sessions`
  already opens (no new file reads): `memory_enabled`/`skills_enabled`/
  `plugins_enabled` and `custom_instructions` (the account's global custom
  instructions text, extracted from `systemPromptRendererAppends`'s
  `<user_preferences>` block) per session in `list_local_workspace`, and
  `prompt_template` (a cloud Project's custom instructions) in
  `project_cache`.
- New `get_project_membership` tool: just the project/Space join key for
  local sessions (cloud Project uuid(s)/name(s), Space id/name, local
  folder paths), meant to chain after the harness's own
  `session_info.list_sessions()` (same `local_<uuid>` id format, no
  translation needed) and before `read_transcript` -- read only the
  sessions that actually matter instead of every transcript blind.
- `list_local_workspace` gains optional `session_ids`/`project_uuid`/
  `folder_path`/`fields` params to scope the response instead of always
  paying for the full inventory dump (all default to current unfiltered
  behavior -- additive, not breaking).
- `references/data-sources.md`: corrected the `spaces.json` shape and
  removed a stale, nonexistent-on-a-real-install `cowork_account_settings.json`
  entry; new sections document the environment-settings fields above, a
  confirmed hard boundary ("chat search enabled" and generic account
  feature toggles aren't answerable -- the only on-disk candidate is an
  unstable, undocumented 442-key internal GrowthBook cache, deliberately
  not read), and the chaining recipe for capability inventory
  (`list_skills`/`list_plugins`/`list_connectors`) and session/project
  correlation via the new tool -- all confirmed by direct inspection of a
  real Claude Desktop install rather than guessed at.
- `references/usage-efficiency.md`: names the concrete native tools for
  the "check for an existing skill/plugin/connector first" step, notes the
  confirmed gap (no raw MCP connector auth-status tool), and adds
  environment-settings hygiene as a finding category alongside model
  right-sizing.
- `SKILL.md`: points Step 3 at the new tool/params for scoping data
  gathering on a large account; Step 5 folds in environment-settings
  findings.

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
