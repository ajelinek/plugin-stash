# Where this plugin's data actually comes from

One local store (Claude Desktop / Cowork), plus a separate thing (the
claude.ai account export) that isn't local at all. `local_data.py` reads
the first; `export_data.py` reads the second. Don't conflate them.

None of this is a documented public API -- it was reverse-engineered by
direct filesystem inspection on macOS, plus docs/community reports for
Windows (unverified -- treat as "probe for existence," which is exactly
what `check_data_access`/`list_local_workspace` do, not as ground truth).
Each section below separates what this plugin's code actually reads from
what was found on disk but deliberately isn't read yet -- don't assume the
latter is available data just because it's cataloged here.

Cutting across all of it: every tool that pulls sessions or conversations
accepts the same `since`/`until` time window, and reports what that window
excluded. See section 8 -- the two sources record timestamps in two
different shapes, and conflating them fails silently.

## 0. What is deliberately out of scope: Claude Code

**Claude Code CLI data (`~/.claude`) is not read by this plugin, at all.**
Not "not yet" -- it's excluded on purpose, and reintroducing a reader for
it is a regression (`test_no_cli_surface_remains` guards this). Two
reasons:

1. **Audience.** This plugin is for business users of the Claude Desktop
   app. A report about terminal sessions, working directories, and git
   branches is noise to someone who has never opened a terminal, and it
   inflates every count in the analysis with activity from a different
   product.
2. **Claude Code already does this better for its own surface.** It ships
   `/doctor` (alias `/checkup`) for installation and configuration health,
   `/usage` (alias `/cost`) for billing-period usage and cost, and
   `/context` for context-window pressure. Duplicating any of those here
   would be worse than the built-in and out of date within a release.

Also out of scope for the same reason: Desktop's **Code tab**
(`claude-code-sessions/`), which is Claude Code embedded in Desktop.

What is *not* excluded, despite looking similar: Cowork's own nested
per-session transcripts (`local_<uuid>/.claude/projects/.../*.jsonl`, see
section 1). Those are Cowork's data. They happen to share Claude Code's
JSONL file layout, and they are the only per-message-accurate model signal
Desktop has -- so they stay.

If a user genuinely wants their CLI usage analyzed, point them at
`/doctor` and `/usage` in Claude Code rather than extending this plugin.

## 1. Claude Desktop / Cowork

### Base app-data directory (checked in this order by `desktop_dir_candidates`)

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/` (consumer install) or `.../Claude-3p/` (managed/3P deployments, no Anthropic account -- documented officially at [claude.com/docs/third-party/claude-desktop/data-storage](https://claude.com/docs/third-party/claude-desktop/data-storage); same internal folder *names*, different root) |
| Windows | `%APPDATA%\Claude\` (traditional installer), or the MSIX-redirected `...\AppData\Local\Packages\Claude_*\LocalCache\Roaming\Claude\` (glob the `Claude_*` id -- it varies by build/channel, never hardcode it), or `Claude-3p` variants (older/legacy builds auto-migrate `%APPDATA%\Claude-3p` to the `%LOCALAPPDATA%` layout on first launch after upgrade). Windows silently redirects plain `%APPDATA%\Claude` writes to the MSIX path when the app is Store-packaged -- there's an open upstream report about code/docs citing only the plain path when this redirect is in effect (anthropics/claude-code#58421, community-reported, unverified against a real Windows install). `desktop_dir_candidates()` checks every variant rather than trusting one. |
| Linux | Not an officially supported Desktop platform; `~/.config/Claude` is checked defensively, not assumed |

### Inside it (only the parts this plugin reads)

| Path (relative to the base dir) | Contents |
|---|---|
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/spaces.json` | The registry for folder-bound ("Local") Projects, called **Spaces** internally. **Real on-disk shape, confirmed directly: `{"spaces": [...]}` -- a dict wrapper, not a bare list.** (`read_spaces` originally assumed a bare list, which silently made `spaces`/`by_space_id` empty on every real install -- fixed to accept both, defensively, since this is unversioned.) Per entry: `{id, name, description, instructions, folders:[{path}]}` -- `instructions` is that Space's custom instructions text. |
| `.../.project-cache/<project-uuid>/metadata.json` | Cached metadata for non-folder-bound **cloud** Projects opened recently from this device: `{uuid, name, description, synced_at, prompt_template}` -- `prompt_template` (confirmed present) is that Project's custom instructions text, the cloud-Project equivalent of a Space's `instructions` above. Only a subset of the account's real cloud Projects are cached here -- whichever were opened on this machine. Sibling `docs/`, `files/`, `memory.md` subfolders (cached project knowledge/files) exist alongside `metadata.json` but aren't read. |
| `.../local_<uuid>.json` | Cowork + Chat-tab session metadata (title, timestamps, `userSelectedFolders`, `userSelectedProjectUuids`, and a top-level `model` + `effort` -- e.g. `claude-sonnet-5` / `high`/`xhigh` -- confirmed present, the session's *configured default*, not necessarily what every message actually used if it was changed mid-session). Also carries this session's stored environment/config fields (see section 6): `memoryEnabled`/`skillsEnabled`/`pluginsEnabled` (booleans) and `systemPromptRendererAppends`, one entry of which is a literal `<user_preferences>...</user_preferences>` block -- the account's global custom instructions text. `createdAt`/`lastActivityAt` are **epoch milliseconds as integers**, confirmed directly -- not ISO strings; `_activity_sort_key` handles both defensively. Excludes `claude-code-sessions/<uuid>.json` (Desktop's Code tab) and `cliSessionId` -- both are Claude Code, out of scope per section 0. |
| `.../local_<uuid>/.claude/projects/<encoded-cwd>/*.jsonl` | Confirmed directly: Cowork keeps its own nested per-session transcript, one JSON object per line with `message.model` on each assistant turn. This is **Cowork's data**, not Claude Code's -- it merely shares the same JSONL layout (see section 0). This is the per-message-accurate source for a Cowork/Chat session -- a real sample came back a different (cheaper) model than the session's configured default, so sub-agents/background steps can run a different model than the one set on the session. `read_cowork_session_transcripts` tallies this, keyed by the same `local_<uuid>` id as the sibling metadata file above, for `build_inventory` to join in. The rest of that same `local_<uuid>/` directory (`uploads/`, `uploads-tmp/`, `outputs/` -- that session's own scratch space) isn't read. |

`userSelectedFolders` is inconsistently typed -- sometimes bare path
strings, sometimes `{path: ...}` dicts. `_normalize_folders` handles both.

Every `local_*.json` is redacted before this plugin ever returns it: keys
containing `token`/`secret`/`auth`/`cookie`/`key` (case-insensitive) are
stripped, and `enabledMcpTools`/`remoteMcpServersConfig` (50-150KB of tool
JSON-Schema per file, pure noise here) are dropped outright.

### Other files under the base dir, not read by this plugin

Cataloged for completeness/future extension, not because anything here
currently consumes them:

| Path | Contents |
|---|---|
| `ant-did` | Random per-device identifier (telemetry only). |
| `config.json` | App-level prefs: locale, theme, `lastKnownAccountUuid`, `oauth:tokenCache*`. **Secret -- never read/print the token-cache fields.** |
| `bridge-state.json` | Keyed by `<org-uuid>:<account-uuid>`, tracks Code-tab<->Cowork bridge sync state. Not project data. |
| `claude_desktop_config.json` | MCP server definitions the user configured manually. |
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/cowork-clientdata-cache.json`, `cowork-gb-cache.json` | App/feature-flag caches. `cowork-gb-cache.json` specifically is a GrowthBook feature-flag cache -- confirmed directly, 442 entries keyed by opaque internal names (`tengu_*`, `claude_code_*`, etc.), unstable and undocumented. Not project data, and not a real "user setting" source -- see section 6 for why this isn't read even for the environment-settings fields. |
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/artifacts/`, `agent/` | Scratch/working dirs for the app itself. |
| `local-agent-mode-sessions/.../<sessionId>/audit.jsonl` + `.audit-key` | Per-session HMAC-chained audit log of tool calls/permission decisions. Signing key is OS-keychain-encrypted -- don't try to read it. |
| `local-agent-mode-sessions/.../memory/` (top-level, not per-Space) | Global Cowork memory: a global `CLAUDE.md`-style instructions file plus `memory/` notes, independent of any Space/Project. See "Known limitations" below -- neither this nor the per-Space memory files are wired up yet. |
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/spaces/<space-id>/memory/*.md` + `MEMORY.md` | Per-Space memory notes Claude has written about that Space. Same "not read yet" status. |
| `claude-code/`, `claude-code-vm/` | The Claude Code binary + VM workspace for the Code tab. Not data. |
| `vm_bundles/` | Cached Cowork sandbox VM image. Not data. |
| `IndexedDB/https_claude.ai_0.indexeddb.leveldb/`, `Local Storage/leveldb/`, `Session Storage/` | Chromium renderer storage for the embedded claude.ai web view. Snappy-compressed LevelDB SST files -- plain `strings`/grep mostly fails (only schema key names surface, not values). Not worth a LevelDB+Snappy dependency for this use case; see "Known limitations." |
| `Cookies`, `buddy-tokens.json` | **Secrets. Never read, print, or hash these into any skill output.** |

### 1a. User-visible output folder

| macOS/Linux | Windows (inferred, unverified) | Notes |
|---|---|---|
| `~/Claude/` (current convention) | `%USERPROFILE%\Claude\` | Cowork writes Artifacts and scheduled-task outputs here for the user to browse directly. **Not removed if the app-data directory is deleted.** |
| `~/Documents/Claude/` (legacy installs) | `%USERPROFILE%\Documents\Claude\` | Older fallback path per docs; `output_dir_candidates()` checks this too. |
| `~/.Trash/Claude` (macOS only) | -- | Cowork's own output folder survives deleting the app-data directory, and can end up in Trash without the user realizing it still exists -- check here if the folder above is missing before concluding it never existed. |

## 2. Cross-referencing chats -> Projects (the join `join_local_sessions` does)

There is no single index. For every local session:

1. If `userSelectedProjectUuids` is non-empty -> belongs to that cloud
   Project (join on `uuid` against `.project-cache`).
2. Else if `userSelectedFolders` matches (exact or prefix) a Space's
   `folders[].path` -> belongs to that Space.
3. Else -> unfiled (surfaced in `membership.unfiled_session_ids`, not
   silently dropped).

Reconstruct real membership for the export the same way when its own
per-conversation project link is empty (see section 3) -- don't just
report those chats as project-less because the raw link field was blank.

A per-chat "tasks" signal is **not available** from local data. Cowork's
"Tasks" (the scheduled/background kind) don't appear in any file under the
app-data directory -- that's a live app feature (Settings -> Cowork ->
Scheduled Tasks, or a `scheduled-tasks`-style MCP tool if one is
connected). What a scheduled task *produced* may show up in the
user-visible output folder (section 1a), but the task definitions and their
run history don't. Say "not available from local data" rather than
reporting zero scheduled tasks.

## 3. Local files vs. the account data export -- complementary, not redundant

| | Account export (`conversations.json` + `projects/*.json`) | Local (this plugin's `list_local_workspace`) |
|---|---|---|
| Product surface | **claude.ai web browser chats only.** Confirmed by format: export message objects are flat `{uuid, text, sender, content, ...}` with no `cwd`/`entrypoint`/`cliSessionId` -- the classic web/API shape. | **Desktop app only** -- Cowork and the Chat tab. Zero overlap with web chats. |
| Project registry | The full cloud Project list *at export time*, including ones with zero local cache footprint. | `.project-cache` only holds Projects opened from *this device* recently -- can both undercount (never opened here) and include Projects newer than any export. |
| Memory | `memories.json` -- one synthesized narrative per project + a global one, frozen at export time. | Per-Space/Project markdown notes, more granular, updated live rather than export-time-frozen (this plugin doesn't currently read these files -- see Known limitations below). |
| Freshness | Frozen at export time. | Live, this-device-only. |
| Cross-device | Aggregates every web session on the account, any device. | This device's Desktop activity only. |

**Bottom line:** combine both when both are available. Use the export for
the authoritative cloud Project registry and web chat history; use local
data for Cowork Spaces, Chat-tab sessions, and current-state signals. Join Projects on `uuid` to de-duplicate overlap.

## 4. Where model-usage data actually lives

A summary of the confirmed facts scattered through sections 1-2 above, in
one place, since it's easy to assume this is uniformly available when
it isn't:

| Source | Model available? | Where |
|---|---|---|
| Nested Cowork transcripts (`local_<uuid>/.claude/projects/.../*.jsonl`) | **Yes, per message.** | `message.model` on every assistant-turn record. The most granular signal available -- a Cowork session isn't necessarily one model throughout (sub-agents/background steps can run a cheaper model). Records also carry `message.usage` (input/output token counts) if a future pass wants per-model cost, not just counts. |
| Desktop metadata (`local_<uuid>.json`, the Cowork/Chat store) | **Yes, session-level.** | Top-level `model` + `effort` fields -- the session's *configured default*, not necessarily every message if it was changed mid-session. Use this when a quick default is good enough, or as a fallback when no nested transcript matched. |
| Account export (`conversations.json`) | **No.** | Confirmed by direct inspection -- every conversation- and message-level key checked, plus a raw-text regex scan for any key containing "model" across the whole file: zero hits. The web export format simply doesn't record which model generated a response. Not recoverable from export data at all; `parse_export`'s `stats.notes` says so when it detects this. |

For model-usage analysis (see
[usage-efficiency.md](usage-efficiency.md)): pull from the nested Cowork
transcripts for per-message accuracy, or `default_model`/`effort` for a
quick session-level fallback.
If the only source in play is the account export, this signal isn't
recoverable this run -- say so rather than reporting an empty result as if
nothing was found.

## 5. Standing instructions -- the three layers, and how to read them together

Three separate places hold text that gets prepended to a conversation, and
no single view in the app brings them together:

| Layer | Where it lives on disk | Scope |
|---|---|---|
| Global custom instructions | `local_<uuid>.json` -> `systemPromptRendererAppends` -> the `<user_preferences>...</user_preferences>` entry (section 1) | Every session, account-wide |
| Space `instructions` | `spaces.json` (section 1) | Every session in that folder-bound Project |
| Cloud Project `prompt_template` | `.project-cache/<uuid>/metadata.json` (section 1) | Every session in that Project |

`get_instructions_inventory` is the join across all three. Two things about
the global layer specifically that are easy to get wrong:

- **It is stored per-session, not in one account-level file.** There is no
  "current global custom instructions" file to read (see section 6 --
  `cowork_account_settings.json` does not exist). Every session file carries
  a snapshot of what the text was *when that session ran*.
- **So multiple distinct texts on disk means it was edited over time, not
  that two are in force at once.** `get_instructions_inventory` groups
  identical texts into `variants` sorted by most recent session activity;
  `variants[0]` is the best available guess at what's live now. Never
  report multiple variants as a conflict or a misconfiguration.

The tool also computes two mechanical overlap signals, which are what turn
this from an inventory into an audit:

- `duplicates_global` -- lines a Space/Project repeats verbatim from the
  current global text. Text at two levels is paid for on every turn twice.
- `repeated_across_projects` -- lines appearing verbatim in two or more
  Spaces/Projects, i.e. standing context being pasted in Project after
  Project that probably belongs one level up.

Line matching is exact after whitespace-collapse and lowercasing, and
ignores lines under 12 characters so blank lines and bare bullets don't
register. It will not catch a reworded duplicate -- that's a judgment call
for the analysis, not something to mechanize. See
[workspace-checkup.md](workspace-checkup.md) for turning any of this into
findings.

## 6. Environment settings, custom instructions, and what's genuinely not answerable

An earlier chaining-spec pass (v2 of the "Environment-Audit Skill" doc) asked
whether memory/skills/plugins toggles, "chat search enabled," and global/
per-Project custom instructions were answerable at all, without being able to
check a real filesystem itself. Confirmed directly against a real install:

- **Buildable, and already wired up:** `read_local_sessions` already opens
  every `local_<uuid>.json` (section 1) -- that same file carries
  `memoryEnabled`/`skillsEnabled`/`pluginsEnabled` (booleans) and
  `systemPromptRendererAppends`, a list of rendered prompt-injection blocks
  where one entry (when the account has global custom instructions set) is a
  literal `<user_preferences>...</user_preferences>` block. `list_local_workspace`
  surfaces these as `memory_enabled`/`skills_enabled`/`plugins_enabled`/
  `custom_instructions` per session -- per-session, not collapsed into one
  account-wide value, since that's the same pattern already used for
  `default_model`/`effort` and lets a caller notice if a value actually
  disagrees across sessions rather than trusting a guess.

  Deliberately not extracted from this same field: `<project_instructions>`
  -- it's a per-session, presence-dependent duplicate of a Space's
  `instructions` (section 1) or a cloud Project's `prompt_template`
  (also section 1), both of which are already authoritative and already
  returned.

- **Confirmed does not exist:** `cowork_account_settings.json`, despite being
  a plausible-sounding filename -- there is no separate account-settings
  file. The real source for memory/skills/plugins state is the per-session
  field above, not a dedicated settings file.

- **Confirmed hard boundary, not a TODO:** "chat search enabled" and any
  other generic account preference/feature-toggle. The only on-disk
  candidate anywhere near this is `cowork-gb-cache.json` (see the "Other
  files" table above) -- a 442-entry internal GrowthBook feature-flag cache
  keyed by opaque names (`tengu_*`, `claude_code_*`, ...). That's an
  engineering/experimentation cache, not a stable, documented, user-facing
  settings source, and it churns across releases -- don't parse it, and say
  "not currently available from local data" plainly rather than guessing at
  a value from it.

## 7. Chaining with native harness tools instead of duplicating them

Several things a caller might reach for this plugin to do are already
better served by something that exists outside it -- don't re-implement
them here:

- **Anything about Claude Code** -- see section 0. `/doctor`, `/usage`, and
  `/context` cover installation health, billing-period cost, and
  context-window pressure for that surface. This plugin's
  `check_data_access` is *not* a Desktop `/doctor`: it only reports whether
  this session can see the data files. The Desktop-side equivalent of
  `/doctor`'s actual recommendations is the analysis in
  [workspace-checkup.md](workspace-checkup.md), driven by
  `get_instructions_inventory` -- not a tool that fixes anything.
- **Scheduled tasks** -- Cowork's `/schedule` creates them and the
  Settings -> Cowork -> Scheduled Tasks panel manages them. Their
  definitions and run history are not on disk (section 2), so this plugin
  cannot inventory them. Recommend `/schedule` when an automation candidate
  turns up; don't try to enumerate what already exists.
- **Turning a workflow into a skill** -- Cowork ships `/skill-creator`.
  That's the follow-up to point at when the analysis recommends a custom
  skill; this plugin never builds one itself.
- **"What skills/plugins/connectors are available?"** -- call the harness's
  own `list_skills`/`list_plugins`/`list_connectors` directly (no plugin
  tool involved). Join `plugins[].skills[].name` against
  `resolved_skills[].name` to flag bundled-vs-standalone skills, and treat
  `connectors[].connected` as ground truth for connector state. Confirmed
  gap, out of scope here: there's no callable tool for raw MCP server
  connection/auth status -- that's plausibly harness-level state, not
  filesystem-visible, so don't build around it.
- **"Which of my session_info sessions belong to which Project/folder?"** --
  the harness's `session_info.list_sessions()` already returns
  `local_<uuid>`-format session ids, the exact same format
  `read_local_sessions`' `id` field uses (confirmed: no translation needed).
  Call `get_project_membership(session_ids)` with those ids to get just the
  join key (cloud Project uuid/name, Space id/name, local folder paths),
  then `session_info.read_transcript` only for the sessions that actually
  matter to the question being asked -- never read every transcript blind.
  `list_local_workspace`'s own `session_ids`/`project_uuid`/`folder_path`/
  `fields` params exist for the same reason: scoping down instead of always
  paying for the full inventory dump.

## 8. Time-scoping every read

Every data-pulling tool -- `list_local_workspace`, `get_project_membership`,
`get_instructions_inventory`, `parse_export` -- takes the same `since`/`until`
pair, implemented once in `time_window.py`. Omit both for the full history.

**Why it has to be shared.** The two sources record time differently, and
getting that wrong is silent rather than loud:

| Source | Fields | Shape |
|---|---|---|
| Local Desktop/Cowork sessions | `createdAt` / `lastActivityAt` | **Epoch milliseconds, as integers** (confirmed directly) |
| Account export conversations | `created_at` / `updated_at` | **ISO 8601 strings** |

Reading the local values as epoch *seconds* yields 1970 dates, which look
like real, very stale data rather than an error. `time_window.to_epoch_ms`
normalizes both shapes; `list_local_workspace` also returns
`created_at_iso`/`last_activity_at_iso` companions so nothing downstream has
to re-derive it.

**Accepted bounds:** a relative age (`"90d"`, `"12w"`, `"6m"`, `"2y"`), an
ISO date or datetime (`"2026-01-01"`), or epoch milliseconds. Months are 30
days and years 365 -- approximations for scoping a read, and the resolved
absolute date always comes back so the approximation is never hidden.
Prefer the relative form when today's date isn't certain.

**Matching is interval overlap, not creation date.** A chat created in
January and still worked in March belongs in a March window. An item with
no usable timestamp at all is *kept*, not dropped -- excluding data that
merely failed to prove it belongs would understate the result -- and
counted separately as `undated_kept` so it can be disclosed.

**Failures widen, they never empty.** An unparseable bound, or a `since`
later than `until`, is reported in `time_window.errors` and the read
proceeds unbounded. The alternative -- returning zero rows -- is
indistinguishable from "this account has no data," which is the single
worst way for this to fail.

**Every response reports its own scope.** The `time_window` block carries
the resolved bounds, `total_before`, `kept`, `excluded`, `undated_kept`, and
`errors`. `time_window.summary_notes` turns that into plain-language caveats
for the dashboard's notes. Repeat them: a windowed count presented as an
account total is simply wrong, and nothing in the rendered output makes the
difference obvious on its own -- which is why `render_dashboard`'s plan has
a first-class `time_window` field that renders under the header.

**Order matters in `list_local_workspace`.** The window is applied before
the Project/Space membership join, so the session list and every grouping
derived from it describe the same period. Filtering afterwards would pair a
windowed chat count with an all-time Project breakdown.

**`get_instructions_inventory` is the interesting case.** Because global
custom instructions are stored per-session (section 5), windowing doesn't
just trim the result -- it answers "what were my standing instructions
during that period, and how many sessions did each reach." No view in the
app can answer that.

## Known limitations

- Global Cowork memory (top-level `local-agent-mode-sessions/.../memory/`)
  and per-Space/Project markdown memory files (`spaces/<id>/memory/*.md`,
  `MEMORY.md`, `.project-cache/<uuid>/memory.md`) all exist on disk but
  aren't read by this plugin yet -- `memory_context.md` from `parse_export`
  (the export's `memories.json`) is the only memory source currently wired
  up.
- `IndexedDB`/`Local Storage`/`Session Storage` (the embedded claude.ai web
  view's Chromium storage) are Snappy-compressed LevelDB and are not read at
  all -- not worth a LevelDB+Snappy dependency for this use case.
- `Cookies`, `buddy-tokens.json`, and `config.json`'s `oauth:tokenCache*`
  fields are real credentials -- this plugin never opens or reads them, full
  stop, regardless of the generic secret-key redaction applied to the files
  it does read.
- This is all a cache, not authoritative. Say so in the dashboard's data
  quality notes rather than presenting counts as exact.

## Sources

- [User identity and local data -- Claude.ai Documentation](https://claude.com/docs/third-party/claude-desktop/data-storage) (official; describes the 3P variant, whose folder *names* match the consumer build confirmed on disk)
- [Explore the .claude directory -- Claude Code Docs](https://code.claude.com/docs/en/claude-directory)
- Community-reported GitHub issues for the Windows MSIX path-redirect
  behavior: anthropics/claude-code#58421, #57998, #69663 -- not official
  docs, verify against a real Windows install before relying on them.
