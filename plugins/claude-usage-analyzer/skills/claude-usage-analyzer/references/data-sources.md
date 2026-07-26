# Where this plugin's data actually comes from

Two entirely separate local stores exist, plus a third thing (the account
export) that's different from both. `local_data.py` reads the first two;
`export_data.py` reads the third. Don't conflate them.

None of this is a documented public API -- it was reverse-engineered by
direct filesystem inspection on macOS, plus docs/community reports for
Windows (unverified -- treat as "probe for existence," which is exactly
what `usage_doctor`/`list_local_workspace` do, not as ground truth).

## 1. Claude Code CLI (`~/.claude`)

| macOS/Linux | Windows | Contents |
|---|---|---|
| `~/.claude/projects/<encoded-cwd>/*.jsonl` | `%USERPROFILE%\.claude\projects\<encoded-cwd>\*.jsonl` | One file per CLI session. Path is the working directory with `/` -> `-`. `read_cli_sessions` only reads the first few lines for header fields (`cwd`, `timestamp`, `entrypoint`, `gitBranch`) plus a cheap line count -- never the full message content. |
| `.../bridge-pointer.json` | same | Present only if that folder was ever opened from Desktop's Code tab -- links a CLI session to a Desktop-side one. |
| `~/.claude/tasks/<session-uuid>/*.json` | same | TaskCreate/TaskUpdate items for that session. The only real "chats vs. tasks" signal for CLI sessions -- Desktop/Cowork sessions never populate this. |

`entrypoint` values seen in the header: `cli` (typed in a terminal),
`claude-desktop` (opened via Desktop's Code tab), `sdk-cli` (spawned
programmatically). This is the only field that tells you *how* a
CLI-family session was launched.

## 2. Claude Desktop / Cowork

### Base app-data directory (checked in this order by `desktop_dir_candidates`)

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/` (consumer install) or `.../Claude-3p/` (managed/3P deployments, no Anthropic account) |
| Windows | `%APPDATA%\Claude\` (traditional installer), or the MSIX-redirected `...\AppData\Local\Packages\Claude_*\LocalCache\Roaming\Claude\` (glob the `Claude_*` id -- it varies by build/channel, never hardcode it), or `Claude-3p` variants |
| Linux | Not an officially supported Desktop platform; `~/.config/Claude` is checked defensively, not assumed |

### Inside it (only the parts this plugin reads)

| Path (relative to the base dir) | Contents |
|---|---|
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/spaces.json` | The registry for folder-bound ("Local") Projects, called **Spaces** internally: `{id, name, description, instructions, folders:[{path}]}` per entry. |
| `.../.project-cache/<project-uuid>/metadata.json` | Cached metadata for non-folder-bound **cloud** Projects opened recently from this device: `{uuid, name, description, synced_at}`. Only a subset of the account's real cloud Projects are cached here -- whichever were opened on this machine. |
| `.../local_<uuid>.json` | Cowork + Chat-tab session metadata (title, timestamps, `userSelectedFolders`, `userSelectedProjectUuids`, `cliSessionId`). Excludes `claude-code-sessions/<uuid>.json`, which is the Code tab (CLI-in-Desktop) -- a different surface, not read here. |

`userSelectedFolders` is inconsistently typed -- sometimes bare path
strings, sometimes `{path: ...}` dicts. `_normalize_folders` handles both.

Every `local_*.json` is redacted before this plugin ever returns it: keys
containing `token`/`secret`/`auth`/`cookie`/`key` (case-insensitive) are
stripped, and `enabledMcpTools`/`remoteMcpServersConfig` (50-150KB of tool
JSON-Schema per file, pure noise here) are dropped outright.

### 2a. User-visible output folder

`~/Claude/` (current convention) or `~/Documents/Claude/` (legacy). On
macOS, also check `~/.Trash/Claude` -- Cowork's own output folder survives
deleting the app-data directory, and can end up in Trash without the user
realizing it still exists.

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

## 4. Local files vs. the account data export -- complementary, not redundant

| | Account export (`conversations.json` + `projects/*.json`) | Local (this plugin's `list_local_workspace`) |
|---|---|---|
| Product surface | **claude.ai web browser chats only.** | **Desktop app** (Cowork, Chat tab, Code tab) plus the standalone **CLI**. Zero overlap with web chats. |
| Project registry | The full cloud Project list *at export time*, including ones with zero local cache footprint. | `.project-cache` only holds Projects opened from *this device* recently -- can both undercount (never opened here) and include Projects newer than any export. |
| Memory | `memories.json` -- one synthesized narrative per project + a global one, frozen at export time. | Per-Space/Project markdown notes, more granular, updated live rather than export-time-frozen (this plugin doesn't currently read these files -- see Known limitations below). |
| Freshness | Frozen at export time. | Live, this-device-only. |
| Cross-device | Aggregates every web session on the account, any device. | This device's Desktop/CLI activity only. |

**Bottom line:** combine both when both are available. Use the export for
the authoritative cloud Project registry and web chat history; use local
data for Cowork Spaces, Chat/Code-tab sessions, CLI sessions, and
current-state signals. Join Projects on `uuid` to de-duplicate overlap.

## Known limitations

- Per-Space/Project markdown memory files (`spaces/<id>/memory/*.md`) exist
  on disk but aren't read by this plugin yet -- `memory_context.md` from
  `parse_export` (the export's `memories.json`) is the only memory source
  currently wired up.
- `IndexedDB`/`Local Storage` (the embedded claude.ai web view's Chromium
  storage) are Snappy-compressed LevelDB and are not read at all -- not
  worth a LevelDB+Snappy dependency for this use case.
- This is all a cache, not authoritative. Say so in the dashboard's data
  quality notes rather than presenting counts as exact.
