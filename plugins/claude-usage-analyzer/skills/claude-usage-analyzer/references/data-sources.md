# Where this plugin's data actually comes from

Two entirely separate local stores exist, plus a third thing (the account
export) that's different from both. `local_data.py` reads the first two;
`export_data.py` reads the third. Don't conflate them.

None of this is a documented public API -- it was reverse-engineered by
direct filesystem inspection on macOS, plus docs/community reports for
Windows (unverified -- treat as "probe for existence," which is exactly
what `usage_doctor`/`list_local_workspace` do, not as ground truth). Each
section below separates what this plugin's code actually reads from what
was found on disk but deliberately isn't read yet -- don't assume the
latter is available data just because it's cataloged here.

## 1. Claude Code CLI (`~/.claude`)

| macOS/Linux | Windows | Contents |
|---|---|---|
| `~/.claude/projects/<encoded-cwd>/*.jsonl` | `%USERPROFILE%\.claude\projects\<encoded-cwd>\*.jsonl` | One file per CLI session. Path is the working directory with `/` -> `-`. `read_cli_sessions` reads header fields (`cwd`, `timestamp`, `entrypoint`, `gitBranch`) from the first few lines, plus a cheap total line count -- never the full message content, *except* for one field: every assistant-turn record's `message.model` (confirmed present directly), tallied into a `models_used` count per model id. A session can switch models mid-conversation, so this is a tally across the session, not a single value -- it's the model actually used for that specific reply, not just "the session's model." Assistant records also carry `message.usage` (input/output token counts) if a future pass wants per-model cost, not just counts. |
| `.../bridge-pointer.json` | same | Present only if that folder was ever opened from Desktop's Code tab -- links a CLI session to a Desktop-side one. |
| `~/.claude/tasks/<session-uuid>/*.json` | same | TaskCreate/TaskUpdate items for that session. The only real "chats vs. tasks" signal for CLI sessions -- Desktop/Cowork sessions never populate this. |

`entrypoint` values seen in the header: `cli` (typed in a terminal),
`claude-desktop` (opened via Desktop's Code tab), `sdk-cli` (spawned
programmatically). This is the only field that tells you *how* a
CLI-family session was launched.

**Seen on disk but not read by this plugin:** `~/.claude/sessions/*.json`
(per-OS-process state keyed by PID, not by project -- not useful for a
per-project inventory), `~/.claude/session-env/<uuid>/` (per-session
environment/isolation state), `~/.claude/daemon/roster.json` (currently-running
CLI worker processes -- `pid`/`cwd`/`sessionId`, good for "what's live right
now" but out of scope for a historical usage analysis), `~/.claude/daemon/dispatch`
and `control.key` (IPC socket/auth for the daemon -- not inventory data at all).

## 2. Claude Desktop / Cowork

### Base app-data directory (checked in this order by `desktop_dir_candidates`)

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/` (consumer install) or `.../Claude-3p/` (managed/3P deployments, no Anthropic account -- documented officially at [claude.com/docs/third-party/claude-desktop/data-storage](https://claude.com/docs/third-party/claude-desktop/data-storage); same internal folder *names*, different root) |
| Windows | `%APPDATA%\Claude\` (traditional installer), or the MSIX-redirected `...\AppData\Local\Packages\Claude_*\LocalCache\Roaming\Claude\` (glob the `Claude_*` id -- it varies by build/channel, never hardcode it), or `Claude-3p` variants (older/legacy builds auto-migrate `%APPDATA%\Claude-3p` to the `%LOCALAPPDATA%` layout on first launch after upgrade). Windows silently redirects plain `%APPDATA%\Claude` writes to the MSIX path when the app is Store-packaged -- there's an open upstream report about code/docs citing only the plain path when this redirect is in effect (anthropics/claude-code#58421, community-reported, unverified against a real Windows install). `desktop_dir_candidates()` checks every variant rather than trusting one. |
| Linux | Not an officially supported Desktop platform; `~/.config/Claude` is checked defensively, not assumed |

### Inside it (only the parts this plugin reads)

| Path (relative to the base dir) | Contents |
|---|---|
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/spaces.json` | The registry for folder-bound ("Local") Projects, called **Spaces** internally: `{id, name, description, instructions, folders:[{path}]}` per entry. |
| `.../.project-cache/<project-uuid>/metadata.json` | Cached metadata for non-folder-bound **cloud** Projects opened recently from this device: `{uuid, name, description, synced_at}`. Only a subset of the account's real cloud Projects are cached here -- whichever were opened on this machine. Sibling `docs/`, `files/`, `memory.md` subfolders (cached project knowledge/files) exist alongside `metadata.json` but aren't read. |
| `.../local_<uuid>.json` | Cowork + Chat-tab session metadata (title, timestamps, `userSelectedFolders`, `userSelectedProjectUuids`, `cliSessionId`, and a top-level `model` + `effort` -- e.g. `claude-sonnet-5` / `high`/`xhigh` -- confirmed present, the session's *configured default*, not necessarily what every message actually used if it was changed mid-session). Excludes `claude-code-sessions/<uuid>.json`, which is the Code tab (CLI-in-Desktop) -- a different surface, not read here (its real transcript is the bridged CLI `.jsonl` file in section 1, already carrying its own `models_used`; the Code-tab metadata file itself additionally carries PR info per session, also not read). |
| `.../local_<uuid>/.claude/projects/<encoded-cwd>/*.jsonl` | Confirmed directly: Cowork keeps its own nested per-session transcript, *same CLI-format JSONL* as section 1 (`message.model` per assistant turn). This is the per-message-accurate source for a Cowork/Chat session -- a real sample came back a different (cheaper) model than the session's configured default, so sub-agents/background steps can run a different model than the one set on the session. `read_cowork_session_transcripts` tallies this, keyed by the same `local_<uuid>` id as the sibling metadata file above, for `build_inventory` to join in. The rest of that same `local_<uuid>/` directory (`uploads/`, `uploads-tmp/`, `outputs/` -- that session's own scratch space) isn't read. |

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
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/cowork-clientdata-cache.json`, `cowork-gb-cache.json` | App/feature-flag caches (GrowthBook etc). Not project data. |
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/artifacts/`, `agent/` | Scratch/working dirs for the app itself. |
| `local-agent-mode-sessions/<uuid>/cowork_account_settings.json` | Display name, locale, memory toggle. |
| `local-agent-mode-sessions/.../<sessionId>/audit.jsonl` + `.audit-key` | Per-session HMAC-chained audit log of tool calls/permission decisions. Signing key is OS-keychain-encrypted -- don't try to read it. |
| `local-agent-mode-sessions/.../memory/` (top-level, not per-Space) | Global Cowork memory: a global `CLAUDE.md`-style instructions file plus `memory/` notes, independent of any Space/Project. See "Known limitations" below -- neither this nor the per-Space memory files are wired up yet. |
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/spaces/<space-id>/memory/*.md` + `MEMORY.md` | Per-Space memory notes Claude has written about that Space. Same "not read yet" status. |
| `claude-code/`, `claude-code-vm/` | The Claude Code binary + VM workspace for the Code tab. Not data. |
| `vm_bundles/` | Cached Cowork sandbox VM image. Not data. |
| `IndexedDB/https_claude.ai_0.indexeddb.leveldb/`, `Local Storage/leveldb/`, `Session Storage/` | Chromium renderer storage for the embedded claude.ai web view. Snappy-compressed LevelDB SST files -- plain `strings`/grep mostly fails (only schema key names surface, not values). Not worth a LevelDB+Snappy dependency for this use case; see "Known limitations." |
| `Cookies`, `buddy-tokens.json` | **Secrets. Never read, print, or hash these into any skill output.** |

### 2a. User-visible output folder

| macOS/Linux | Windows (inferred, unverified) | Notes |
|---|---|---|
| `~/Claude/` (current convention) | `%USERPROFILE%\Claude\` | Cowork writes Artifacts and scheduled-task outputs here for the user to browse directly. **Not removed if the app-data directory is deleted.** |
| `~/Documents/Claude/` (legacy installs) | `%USERPROFILE%\Documents\Claude\` | Older fallback path per docs; `output_dir_candidates()` checks this too. |
| `~/.Trash/Claude` (macOS only) | -- | Cowork's own output folder survives deleting the app-data directory, and can end up in Trash without the user realizing it still exists -- check here if the folder above is missing before concluding it never existed. |

## 3. Cross-referencing chats -> Projects (the join `join_local_sessions` does)

There is no single index. For every local session:

1. If `userSelectedProjectUuids` is non-empty -> belongs to that cloud
   Project (join on `uuid` against `.project-cache`).
2. Else if `userSelectedFolders` matches (exact or prefix) a Space's
   `folders[].path` -> belongs to that Space.
3. Else -> unfiled (surfaced in `membership.unfiled_session_ids`, not
   silently dropped).

Reconstruct real membership for the export the same way when its own
per-conversation project link is empty (see section 4) -- don't just
report those chats as project-less because the raw link field was blank.

Separately, if a per-chat "tasks" signal is wanted: take that session's
`cliSessionId` and look for `~/.claude/tasks/<cliSessionId>/*.json` (section
1). In practice this is almost always empty for Cowork/Chat sessions --
TaskCreate to-dos are a CLI-session artifact, not a Cowork one. Cowork's own
"Tasks" (the scheduled/background kind) don't show up in any of these files
at all -- that's a live app feature (Settings -> Cowork -> Scheduled Tasks,
or a `scheduled-tasks`-style MCP tool if one is connected), not local
filesystem data.

## 4. Local files vs. the account data export -- complementary, not redundant

| | Account export (`conversations.json` + `projects/*.json`) | Local (this plugin's `list_local_workspace`) |
|---|---|---|
| Product surface | **claude.ai web browser chats only.** Confirmed by format: export message objects are flat `{uuid, text, sender, content, ...}` with no `cwd`/`entrypoint`/`cliSessionId` -- the classic web/API shape. | **Desktop app** (Cowork, Chat tab, Code tab) plus the standalone **CLI**. Zero overlap with web chats. |
| Project registry | The full cloud Project list *at export time*, including ones with zero local cache footprint. | `.project-cache` only holds Projects opened from *this device* recently -- can both undercount (never opened here) and include Projects newer than any export. |
| Memory | `memories.json` -- one synthesized narrative per project + a global one, frozen at export time. | Per-Space/Project markdown notes, more granular, updated live rather than export-time-frozen (this plugin doesn't currently read these files -- see Known limitations below). |
| Freshness | Frozen at export time. | Live, this-device-only. |
| Cross-device | Aggregates every web session on the account, any device. | This device's Desktop/CLI activity only. |

**Bottom line:** combine both when both are available. Use the export for
the authoritative cloud Project registry and web chat history; use local
data for Cowork Spaces, Chat/Code-tab sessions, CLI sessions, and
current-state signals. Join Projects on `uuid` to de-duplicate overlap.

## 5. Where model-usage data actually lives

A summary of the confirmed facts scattered through sections 1-2 above, in
one place, since it's easy to assume this is uniformly available when
it isn't:

| Source | Model available? | Where |
|---|---|---|
| CLI transcripts (`~/.claude/projects/*.jsonl`) | **Yes, per message.** | `message.model` on every assistant-turn record, plus `message.usage` (token counts). Most granular -- a session can switch models mid-conversation. |
| Nested Cowork transcripts (`local_<uuid>/.claude/projects/.../*.jsonl`) | **Yes, per message.** | Same JSONL format as the CLI store, confirmed directly -- a Cowork session isn't necessarily one model throughout either (sub-agents/background steps can run a cheaper model). |
| Desktop metadata (`local_<uuid>.json`, both Cowork/Chat and Code-tab stores) | **Yes, session-level.** | Top-level `model` + `effort` fields -- the session's *configured default*, not necessarily every message if it was changed mid-session. Use this when a quick default is good enough, or as a fallback when no nested transcript matched. |
| Account export (`conversations.json`) | **No.** | Confirmed by direct inspection -- every conversation- and message-level key checked, plus a raw-text regex scan for any key containing "model" across the whole file: zero hits. The web export format simply doesn't record which model generated a response. Not recoverable from export data at all; `parse_export`'s `stats.notes` says so when it detects this. |

For model-usage analysis (see
[usage-efficiency.md](../references/usage-efficiency.md)): pull from the
CLI-format transcripts (top-level or nested-in-Cowork) for per-message
accuracy, or `default_model`/`effort` for a quick session-level fallback.
If the only source in play is the account export, this signal isn't
recoverable this run -- say so rather than reporting an empty result as if
nothing was found.

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
