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

## Read this before writing down that the app doesn't store something

Three releases in a row shipped a confident claim about something Claude
Desktop does *not* record. All three were false on a machine where the
feature was actually being used:

| Release | The claim | What was on disk |
|---|---|---|
| 0.5.0 | `memories.json` is an object | A single-element **list** -- every real export fell through to a raw-JSON dump |
| 0.6.1 | Scheduled tasks aren't readable; memory is export-only | `scheduled-tasks.json` with a schema and an empty array; 15 memory files in 3 stores, the largest under a glob nobody had tried |
| 0.6.2 | "There is no single index" for which Space a chat is in (section 2) | A `spaceId` key on 59 of 97 sessions, resolving straight through `spaces.json` |

**The mechanism is identical every time: an empty array, a null field and
an unpopulated file are indistinguishable from an absent feature.** `[]` is
exactly what a live feature with nothing in it looks like. `null` is
exactly what a real field looks like on the sessions that don't use it --
38 of 97 sessions here carry no `spaceId`, so a spot check that landed on
those would have "confirmed" the field doesn't exist.

So, before writing that something isn't stored:

1. **Use the feature first, then look.** File a chat into a Space, schedule
   a task, let Claude remember something -- and only then re-read the file.
   A claim about what *isn't* stored is worth exactly as much as the
   machine it was checked on, and a machine where the feature was never
   used cannot support one at all.
2. **Union the keys across every record, not the first one.** This is the
   one command that would have caught the `spaceId` miss, and it costs
   nothing:

   ```bash
   cd ~/Library/Application\ Support/Claude
   python3 - <<'PY'
   import collections, glob, json
   paths = glob.glob('local-agent-mode-sessions/*/*/local_*.json')
   seen = collections.Counter()
   for p in paths:
       seen.update(json.load(open(p)).keys())
   print(len(paths), 'files')
   for key, n in seen.most_common():
       print(f'{n:4d}/{len(paths)}  {key}')
   PY
   ```

   Print key *names* and counts, never values -- these files carry the
   user's own prompts.
3. **Write down what you looked at.** "I globbed X and found nothing" is a
   search result, and belongs in the doc as one. "Confirmed boundary" is
   for something established positively: a documented architectural fact,
   or a file whose contents you read and understood.

The cost is not theoretical. The 0.6.2 entry above made this plugin's
primary lens describe the wrong workspace to the user (section 2), and it
survived two releases of doc passes because nothing in the output looked
broken.

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
| `.../.project-cache/<project-uuid>/metadata.json` | Cached metadata for non-folder-bound **cloud** Projects opened recently from this device: `{uuid, name, description, synced_at, prompt_template}` -- `prompt_template` (confirmed present) is that Project's custom instructions text, the cloud-Project equivalent of a Space's `instructions` above. Only a subset of the account's real cloud Projects are cached here -- whichever were opened on this machine. The sibling **`memory.md` is read** (one of the three memory stores -- see section 1c); the sibling `docs/` and `files/` subfolders (cached project knowledge/files) are not. |
| `.../local_<uuid>.json` | Cowork + Chat-tab session metadata (title, timestamps, **`spaceId`** and `spaceIdSetBy` -- the app's own record of which Space this chat was filed in, and the authoritative answer to that question (section 2) -- `userSelectedFolders`, `userSelectedProjectUuids`, a top-level `model`, and the effort level under **`effortOverride`** -- e.g. `claude-sonnet-5` / `high`/`xhigh`; both are the session's *configured default*, not necessarily what every message actually used if it was changed mid-session. See section 1b: there is no `effort` key, and reading one was a real bug). Roughly 34 keys in total -- section 1b is the full inventory of what's read and why. Also carries this session's stored environment/config fields (see section 6): `memoryEnabled`/`skillsEnabled`/`pluginsEnabled` (booleans) and `systemPromptRendererAppends`, one entry of which is a literal `<user_preferences>...</user_preferences>` block -- the account's global custom instructions text. `createdAt`/`lastActivityAt` are **epoch milliseconds as integers**, confirmed directly -- not ISO strings; `_activity_sort_key` handles both defensively. Excludes `claude-code-sessions/<uuid>.json` (Desktop's Code tab) and `cliSessionId` -- both are Claude Code, out of scope per section 0. |
| `plan-usage-history.json` | Claude Desktop's own rolling record of how full the five-hour and seven-day usage windows were, sampled roughly every 15 minutes while the app runs. **The only ground truth this plugin has about real plan consumption** -- everything else it measures is a proxy. Read by `read_plan_usage_history`/`build_plan_usage_history`, behind `get_plan_usage_history`. Full field semantics in section 1c. |
| `claude_desktop_config.json` | The app's own configured output folder (`coworkUserFilesPath`) plus a ~27-key `preferences` block: standing folder grants and app-wide toggles. Read by `read_desktop_preferences`/`read_configured_output_dir`, behind `get_desktop_settings` and `check_data_access`. Section 1c. **This file used to be cataloged here as "MCP server definitions the user configured manually," which is wrong on a current build -- see section 1c.** |
| `extensions-installations.json` | What is *installed*, which no session file can answer. Read by `read_installed_extensions`, behind `get_desktop_settings`. Section 1c. |
| `.../scheduled-tasks.json` | What this device records about scheduled tasks: `{scheduledTasks: [], recordedSkips: {}, sundayAliasBoundaryStamped, dayFieldsOrBoundaryStamped}`. Read by `read_scheduled_tasks`, behind `get_desktop_settings`. Empty on the verification machine -- which is not the same as absent, and used to be documented as absent. The identically named file under `claude-code-sessions/` is Claude Code and is out of scope (section 0). Section 1c. |
| `.../remote-session-spaces.json` | `{"entries": [{sessionId, spaceId, folders, memoryEnabled}]}` -- the lookup that turns a `session_01...` folder grant into a Space a person recognizes. Read by `read_remote_session_spaces`, joined into `get_desktop_settings`. Section 1c. |
| `.../memory/**/*.md`, `.../spaces/<space-id>/memory/**/*.md`, `.../.project-cache/<uuid>/memory.md` | Claude's own remembered notes, in three stores: account-level, per-Space, per cached cloud Project. Read by `read_memory_files`, returned in `get_instructions_inventory`'s `memory` block. **Memory content is on disk** -- this doc previously said it was export-only. Section 1c. |
| `config.json` | **Three allowlisted keys only** -- `first_launch_at`, `locale`, `userThemeMode` -- read by `_read_app_config_allowlisted`/`read_app_config`. The same file holds live OAuth token caches, so it is never loaded wholesale. Section 1c. |
| `.../local_<uuid>/.claude/projects/<encoded-cwd>/*.jsonl` | Confirmed directly: Cowork keeps its own nested per-session transcript, one JSON object per line with `message.model` on each assistant turn. This is **Cowork's data**, not Claude Code's -- it merely shares the same JSONL layout (see section 0). This is the per-message-accurate source for a Cowork/Chat session -- a real sample came back a different (cheaper) model than the session's configured default, so sub-agents/background steps can run a different model than the one set on the session. `read_cowork_session_transcripts` tallies this, keyed by the same `local_<uuid>` id as the sibling metadata file above, for `build_inventory` to join in. It is also the only source for **what a session actually did**, as opposed to what it was configured to be able to do: `tools_invoked` (per-tool counts), `mcp_servers_invoked` (parsed from the `mcp__<server>__<tool>` naming convention, which makes the tool tally double as a record of which connectors were really used), `message_count`, `touched_dirs` (**parent directories only**, from tool inputs like `file_path`) and `url_hosts` (**hosts only**, never full URLs). Message text and file contents are never read. Past a line cap the scan continues at a sampling stride rather than stopping, so tallies describe the whole file rather than just its opening; `transcript_sampled` flags when that happened. The rest of that same `local_<uuid>/` directory isn't read. Note that `outputs/` is **not** scratch space, as this doc previously called it: it holds 1,244 real files on the re-check machine -- the deliverables those sessions actually produced. That makes it the most legible evidence of what a Space was for, and the natural sort key for the reorganization lens. Not reading it is a gap, not a boundary; see the open-questions section. |

`userSelectedFolders` is inconsistently typed -- sometimes bare path
strings, sometimes `{path: ...}` dicts. `_normalize_folders` handles both.

Every `local_*.json` is redacted before this plugin ever returns it: keys
containing `token`/`secret`/`auth`/`cookie`/`key` (case-insensitive) are
stripped, and the raw `enabledMcpTools`/`remoteMcpServersConfig` blobs
(50-150KB of tool JSON-Schema per file, pure noise here) are dropped
outright -- summarized *name* lists derived from them are returned instead,
under different keys, so the signal survives without the bulk.

#### 1b. The rest of `local_<uuid>.json` -- field inventory, counted on one machine

**These are observations, not a schema.** Counts below are out of the **97
session files** on the machine this was last re-counted against
(2026-09-05); an earlier pass counted 67 on the same install and those
figures stood in this table until 0.6.2. Presence genuinely varies per
session, so **treat an absent field as normal, never as a finding** -- and
re-count rather than quoting these if a number is load-bearing.

Individual files carried between **27 and 40** top-level keys, and **48
distinct keys** appear across all 97. Before 0.5.0 this plugin read 10 of
them.

| On-disk key | Present | Returned as | Why it matters |
|---|---|---|---|
| `spaceId` | 59/97 | `space_id` | **The app's own record of which Space this chat is in**, and the authoritative input to the membership join (section 2). Reading `spaceIdSetBy` beside it gives the app's statement of provenance -- `"user"` on all 59 here. This is the field a 0.6.1-era survey concluded did not exist. |
| `effortOverride` | 60/97 | `effort` | The five-level effort dial. **There is no `effort` key** -- it was 0/97. Reading it was a real bug: the field was silently `None` in every response while the docs told the caller to right-size against it. |
| `permissionMode` | 87/97 | `permission_mode` | Manual/Auto/Skip. Read as a cost signal on the received claim that Auto costs more than the other two -- **unverified, and unverifiable from local data** (see usage-efficiency.md, "Approval mode"). The mode itself is on disk; the cost difference is not. |
| `enabledMcpTools` | 97/97 | `enabled_mcp_tool_names`, `enabled_mcp_server_labels` | A **dict** keyed `local:<Server display name>:<tool>` with a **bool** value -- a present key does not mean enabled. The server segment is a display label ("Control Chrome"), *not* the `mcp__<server>__<tool>` slug transcripts use. |
| `remoteMcpServersConfig` | 97/97 | `remote_mcp_server_names` | List of `{uuid, name, url, tools}`. Remote connectors configured for the session. |
| `slashCommands` | 97/97 | `slash_command_names`, `plugin_names` | List of `<plugin>:<command>` strings. This is the only readable source of **plugin names** -- see next row. |
| `pluginInstallPaths` | 88/97 | `plugin_install_count` | Paths into hashed temp dirs (`.../claude-hostloop-plugins/4bc8832a52bf73d1`), so the basenames are opaque and useless as names. Count only. |
| `initialMessage` | 97/97 | `initial_message` | The session's opening ask -- the local counterpart of the export's `first_human_message`. Truncated. |
| `egressAllowedDomains` | 97/97 | `egress_allowed_domains` | Network reach. A value of `["*"]` means unrestricted outbound. |
| `webFetchAllowedUrls` | 61/97 | `web_fetch_allowed_url_hosts` | Reduced to **hosts only** -- full URLs carry paths and query strings. |
| `fsDetectedFiles` | 51/97 | `fs_detected_file_count` | Count only. |
| `cwd` | 97/97 | `cwd` | The session's working directory. |
| `systemPrompt` | 97/97 | `system_prompt_char_count` | **Size only.** 48,432-49,585 characters (median 49,336) of Anthropic scaffolding the user cannot edit -- the text would be unactionable and sensitive; the number is useful context for weighing the user's own instruction length. |
| `memoryGuidelinesTemplate` | 97/97 | `memory_guidelines_char_count` | **Size only**, same reasoning. 13,526 characters on every session here. |

Read but not listed above because they were already documented: `title`,
`createdAt`, `lastActivityAt`, `isArchived`, `userSelectedFolders`,
`userSelectedProjectUuids` (16/97), `model`, `memoryEnabled`,
`skillsEnabled`, `pluginsEnabled`, `systemPromptRendererAppends` -- all
97/97 except where noted.

Present on disk and **deliberately not** membership signals, in case a
future pass mistakes one for the Space link: `cwd` (a per-session scratch
directory under the session's own folder), `resolvedFolderKinds`,
`chromeTabGroupId` and `sessionType`.

#### 1c. Account-level files -- the account and the app, not a session

Seven files describe the account and the app itself rather than a session
that happened. That is a different kind of evidence, and the reason there
are two account-level tools (`get_plan_usage_history`,
`get_desktop_settings`) rather than more fields on `list_local_workspace`.

Four sit at the *top* of the app-data directory:
`plan-usage-history.json`, `claude_desktop_config.json`,
`extensions-installations.json`, `config.json`.

Three more sit one level in, under
`local-agent-mode-sessions/<account-uuid>/<org-uuid>/` beside the session
files -- `scheduled-tasks.json`, `remote-session-spaces.json`, and the
local **memory** stores. All three were added in 0.6.1, and two of them
falsify a boundary this doc previously stated as confirmed. They are
documented at the end of this section, under the same warning as the rest.

##### What `check_data_access`'s `data_files` actually probes -- and what it doesn't

`data_files` exists so a caller learns up front that a lens has nothing to
read, rather than finding out from an empty response later. It probes
**four** files, all at the top of the app-data directory:

| `data_files.files` key | File |
|---|---|
| `plan_usage_history` | `plan-usage-history.json` |
| `desktop_config` | `claude_desktop_config.json` |
| `extensions_installations` | `extensions-installations.json` |
| `app_config` | `config.json` |

and rolls them into two booleans: `plan_usage_history_available` (the first
file), and `desktop_settings_available` (**any** of the other three).

**It does not probe the three sources added in 0.6.1** -- local memory,
`scheduled-tasks.json`, or `remote-session-spaces.json` -- and it knows
nothing about session files or `spaces.json`. So `desktop_settings_available:
true` says the settings *tool* can run; it says nothing about whether its
`scheduled_tasks` block found a file, and `data_files` says nothing at all
about an empty `memory` block.

Consequence, and it matters because the rest of these docs tell you to
check `data_files` before concluding anything from an empty block: for
memory, scheduled tasks and the session->Space lookup, **read the block's
own `available` flag, `source_file` and notes instead.** Each of those
readers reports its own availability:

| Empty block | What actually tells you why |
|---|---|
| `get_instructions_inventory` -> `memory` | its `available`, `file_count`, `files_found_count`, `unreadable_file_count`, `source` |
| `get_desktop_settings` -> `scheduled_tasks` | its `available`, `file_count`, `files[].readable`, `scheduled_task_count` |
| `get_desktop_settings` -> `access_scope.remote_session_folder_grants` | `space_lookup_available`, `resolved_space_count`, `unresolved_space_count` |
| `list_local_workspace` -> `local_sessions` / `membership` | `access.desktop_base_dir` and the `time_window` block |

Widening `data_files` to cover the newer sources is a code change, not a
doc change, and it hasn't been made -- which is exactly why this table is
here rather than a sentence saying "check `data_files`".

**Read "These field names were verified once, on one machine" at the end of
this section before building anything on the names below.**

##### `plan-usage-history.json` -- real plan consumption

The Desktop counterpart of the Claude Code CLI's `/usage`, and **the only
ground truth this plugin has about what the plan is actually being spent
on**. Message counts, session counts and model tallies are all proxies for
consumption; this file is the measurement.

Confirmed shape (48KB on the verification machine):

```json
{"version": 2,
 "samples": [{"t": 1786108979600,
              "org": "<org uuid>",
              "u": {"fh": 0, "sd": 0, "xu": 0}}]}
```

| Key | Meaning |
|---|---|
| `t` | **Epoch milliseconds**, as an integer -- same shape and same trap as `createdAt` (section 8). `read_plan_usage_history` returns `at` and `at_iso` side by side so nothing downstream re-derives it. |
| `org` | Organization uuid. Used only to group samples; it is mapped to an opaque label (`organization 1`) and **never reaches a response**. |
| `u.fh` | The **five-hour usage window**, 0-100. |
| `u.sd` | The **seven-day usage window**, 0-100. |
| `u.xu` | **Extra usage** (purchased top-up), 0-100. See below -- usually absent, not zero. |

**These are percentages of the user's own plan limits.** Not dollars, not
tokens, not a bill. "100%" means "you'd run out here." Nothing in this
plugin should ever convert them to money.

**`xu` is absent from most samples, and absent is not zero.** 159 of 545
samples carried the key at the 2026-09-05 count; the other 386 had no
`xu` at all. `_percent()` returns `None` rather than `0` for a missing key,
and the tool reports `samples_missing_field` alongside, because "never
above zero on the samples that recorded it" is the only provable claim.
Collapsing absent to zero would let the analysis assert the user has never
bought extra usage when the file simply doesn't say.

**A gap between samples means the app was closed, not that usage was
zero.** Desktop writes this file only while it is running. On the
verification machine the median sampling interval was 15.0 minutes and the
largest gap was 33.2 hours -- a machine asleep overnight, not a day of no
work. Reading gaps as idleness turns a holiday into "you barely use
Claude", and it is the single easiest way to misread this file.
`get_plan_usage_history` returns `cadence.largest_gaps` and says this in
its own notes; repeat it rather than quietly averaging across a gap.

Two more properties of the file worth stating to the user outright:

- **It is a rolling local record, not account history.** 545 samples
  spanning 29.3 days (2026-08-07 to 2026-09-05) on the verification
  machine -- 546 across 30.0 days when re-counted a day later, which is the
  useful thing to notice about every count in this section: the file grows
  while the app runs, so quote it with the date it was read. **Say what is observed, not why.** Earlier versions of this doc
  and of the tool called the start of the file "a retention limit"; that
  is a mechanism the data does not establish. The earliest sample could be
  when Claude Desktop was first run on this machine, when the app started
  keeping this file, or where some internal trimming rule cut it -- nothing
  on disk distinguishes them. Report "the record on this machine starts
  here" and attribute no cause. It is certainly *not* the date the user
  started using Claude. The response block is named `coverage` for this
  reason (it was `retention` until 0.6.1 -- a field name that asserted the
  claim in the most prominent place available).
- **It covers this machine only.** Usage from the phone, the claude.ai web
  app, or a second computer is not in it. A user who works mostly on
  another device will look lighter here than they are.

`build_plan_usage_history` collapses contiguous at-or-near-limit samples
(>= 90%) into single `limit_events` rather than reporting each 15-minute
sample as its own limit hit -- one busy afternoon is one event, not twenty.
Peaks on the verification machine: `fh` reached 100, `sd` reached 80.

**Both windows are analysed, in the same shape.** Until 0.6.1 only `fh`
was, which left the seven-day cap -- the one that can strand a user for
days rather than hours -- entirely invisible: no event, no framing, no
guidance. `limit_events` is now
`{near_limit_threshold_pct, five_hour: {...}, seven_day: {...}}`, with
identical key sets on both sub-blocks (`window`, `run_break_hours`,
`event_count`, `ceiling_event_count`, `events`, `events_truncated`).

The one deliberate asymmetry is the **run break**, and each block reports
its own `run_break_hours` so it isn't hidden: **2 hours** for the
five-hour window, **36 hours** for the seven-day one. Five hours of not
using Claude empties the five-hour window, so a two-hour hole in the
samples genuinely separates two events. The seven-day window drains at
roughly 14% a day -- 95% on Monday night and 95% on Tuesday morning is one
continuous week of being nearly out, not two events.

**Days and weekdays are this machine's local calendar days, not UTC
ones.** Every instant comes back twice: `*_at_iso` (UTC, offset `+00:00`
attached, for joining and comparison) and `*_at_local_iso` (local, real
offset attached), with `*_local_date` / `weekday` / `utc_offset` wherever a
calendar day is implied, and a top-level `timezone` block naming the zone.
This was a real correction, not a refinement: on the verification machine
(US Central) UTC bucketing moved the reported seven-day peak from Thursday
20 August to Friday 21 August and changed the weekday label on 22 of ~30
days. Build anything a person reads from the local values. The offset is
resolved per instant from the OS timezone database, so a span crossing a
daylight-saving boundary is labelled correctly on both sides of it.

There is **no raw sample series** and no `max_samples` parameter. Both were
removed in 0.6.1: nothing in the plugin consumed the series, it was roughly
two thirds of the default response, and its even-spacing downsampler
preserved no extrema -- so a chart drawn from it could omit every limit hit
and show a maximum contradicting the `peaks` block in the same response.
Use the aggregates.

Unlike every other reader here, samples are matched on the timestamp
falling **inside** the `since`/`until` window rather than on interval
overlap (section 8). A usage sample is a point in time, not a span, so
there is nothing to overlap with -- which also means a window narrower
than the sampling gap can legitimately come back empty.

##### `claude_desktop_config.json` -- output folder, standing grants, app toggles

**This entry was wrong until 0.6.0**, and the correction matters more than
most: this file was cataloged as "MCP server definitions the user
configured manually" and listed among files this plugin does not read. On a
current build that description does not match what is in it, and there may
be **no `mcpServers` key at all** -- there was none on the verification
machine.

Two top-level keys were observed: `coworkUserFilesPath` (a string) and
`preferences` (27 keys). `read_desktop_preferences` does not require
`mcpServers` and does not special-case it; if a build does write one it
lands in `unrecognized_settings` like any other unfamiliar key, which is
exactly what that block is for.

**That last sentence was untrue when 0.6.0 shipped it, and the code was
fixed in 0.6.1 rather than the claim softened.** `unrecognized_settings`
was built only from unknown keys *inside* `preferences`; unknown keys at
the file's own **top level** -- exactly where `mcpServers` would sit --
were read past and discarded without a trace. So the one key the doc named
as the example was the one case that would not have appeared. It now spans
both namespaces: `read_desktop_preferences` returns a `top_level` dict of
every top-level key outside `preferences`/`coworkUserFilesPath`, and
`build_desktop_settings` folds those into `unrecognized_settings` beside
the unknown preference keys. Same treatment either way -- values
summarized, never dumped, so an MCP server block's `env`/`args` never come
through verbatim, and a secret-shaped top-level key is withheld and
counted. On the (theoretical) collision between a top-level key and a
preference of the same name, the top-level one is labelled
`"<key> (top level)"` rather than overwriting. Silently dropping unknown
keys is the precise failure mode that lets a reverse-engineered reader go
stale unnoticed, which is why the fix went in the code.

- **`coworkUserFilesPath`** -- the user-visible Claude output folder, as
  *Claude itself* has it configured (`/Users/<user>/Claude` on the
  verification machine). This is authoritative, and it replaces guessing
  from the candidate list in section 1a. `check_data_access` probes it
  first and reports `output_dir_source` as either
  `"claude_desktop_config.json"` (configured -- confirmed) or
  `"candidate_probe"` (guessed). Say which: "Claude is configured to write
  here" and "it's probably here" are different statements, and the
  difference matters before anyone goes hunting for a missing
  scheduled-task result.
- **Standing folder grants** -- three preferences, and the reason
  `get_desktop_settings` exists at all for the access-scope check:

  | Preference | Shape | What it is |
  |---|---|---|
  | `localAgentModeTrustedFolders` | list of paths (10 on the verification machine) | Folders trusted once and trusted ever after. |
  | `remoteFolderConsentMemory` | list (4 entries) | Remembered consent decisions for remote sessions. |
  | `remoteSessionFolderGrants` | object keyed by session id | Per-session grants, kept at the account level. |

  These are **standing** grants -- approved once, in force for future
  sessions until removed in Settings. A folder trusted months ago and never
  opened since appears in no session's `user_selected_folders`, so it is
  invisible to per-session data entirely. `_grant_breadth` flags each path
  with `looks_broad` and a plain-language `breadth_reasons`; it is a flag,
  never a score, because how much a broad grant matters depends on what is
  inside it and this module deliberately does not look.
- **App-wide toggles.** Reported by name:
  `allowAllBrowserActions`, `coworkBrowserToolsEnabled`,
  `coworkPreferredBrowser`, `dispatchCodeTasksPermissionMode`,
  `bypassPermissionsGateByAccount`, `coworkScheduledTasksEnabled`,
  `ccdScheduledTasksEnabled`, `coworkWebSearchEnabled`,
  `coworkModelAutoFallbackByAccount`, `keepAwakeEnabled`,
  `coworkHipaaRestricted`, `orgWorkAcrossAppsDisabled`, `sidebarMode`.
  The two `*ByAccount` keys are `{account uuid: value}` maps; the uuids are
  replaced with opaque labels (`account 1`) before anything is returned.
- **Everything else** comes back under `unrecognized_settings` with values
  *summarized* rather than dumped (a list becomes
  `{type, item_count, size_chars}`). On the verification machine that was
  11 keys, including `epitaxyPrefs`, `launchPreviewPersistedWorkspaces`,
  `remoteToolsDeviceName`, `chicagoEnabled`, `dockBounceEnabled`,
  `ccAutoArchiveOnPrClose` and `ccRemoteControlDefaultEnabled`. Keeping
  them is deliberate: Desktop ships new settings regularly, and silently
  dropping unknown keys is exactly how a reader like this one goes stale
  without anyone noticing. **An unrecognized key is a drift signal for
  maintainers, never a finding about the user.** A preference whose *name*
  looks secret-shaped is withheld entirely and counted.

##### `extensions-installations.json` -- what is installed

```json
{"extensions": {"<ext id>": {"id", "version", "hash", "installedAt",
                             "manifest", "signatureInfo", "source"}}}
```

**Authoritative for "installed"**, which `enabled_mcp_server_labels` on a
session is not. An extension installed and never enabled in any session
appears nowhere in session data at all -- so "installed months ago, never
used once", the most actionable capability finding available, was
structurally invisible before this file was read. See
[workspace-checkup.md](workspace-checkup.md)'s capability-inventory check
for how to cross it against what was enabled and what was actually
invoked.

`read_installed_extensions` returns id, version, `source`,
`signatureInfo.status`, and the install date; `installedAt` is an **ISO
string** on a real install (`2026-06-29T12:35:15.199Z`), unlike every other
timestamp in this plugin, so it is normalized to `installed_at_ms` and
`installed_at_iso` alongside the raw value rather than trusted to be one
shape. `manifest` is **measured and dropped** -- roughly 1.9KB of nested
JSON per extension that yields no finding a size can't. `hash` is not
returned.

One extension was present on the verification machine
(`ant.dir.ant.anthropic.chrome-control` 0.1.6, source `registry`,
signature `unsigned`), so the multi-extension path is reasoned about but
thinly observed.

**`signature_status: unsigned` is normal and is not a security finding on
its own.** The field is kept -- dropping it would lose the only signal that
would show it ever starting to carry real values -- but its obvious reading
is one this plugin cannot substantiate, so the guidance travels with it.
`unsigned` is the only value ever observed on a real install, and the
extension reporting it is **Anthropic's own first-party chrome-control
extension**. Nothing here verifies any extension's provenance. So: never
tell a user an extension is unsafe on this basis, and never surface the
field to them at all. It is a drift signal for maintainers.
`build_desktop_settings` attaches a note saying exactly this whenever any
extension reports `unsigned`, so the guidance reaches the analyst in the
response rather than only here.

##### `config.json` -- three keys, and a hard stop

**This file holds live credentials.** Alongside ordinary preferences it
carries `oauth:tokenCache` and `oauth:tokenCacheV2`. Nineteen top-level
keys were present on the verification machine; **three are ever read**:

| Key | Returned as | Why |
|---|---|---|
| `first_launch_at` | `first_launch_at` (+ the full local rendering, section 8), `first_launch_at_usable`, `account_tenure_days` | The denominator for any rate. "Forty chats" means something different in a week-old install than in a year-old one. |
| `locale` | `locale` | Presentation. |
| `userThemeMode` | `user_theme_mode` | Presentation. |

The guarantee is two independent gates, and it is written that way on
purpose: `_read_app_config_allowlisted` returns only keys in
`_APP_CONFIG_ALLOWLIST`, **and** re-checks each survivor against
`_is_secret_key`, so one typo in the allowlist is not enough to leak a
token. `read_app_config` then names the three fields it puts in its
response independently. Never widen the allowlist without deciding, in
writing, that the new key cannot be credential-shaped.

**A timestamp that doesn't render produces no tenure.** `first_launch_at`
comes off an unversioned file and is not guaranteed to be sane.
`account_tenure_days` is computed only when the value both normalizes *and*
renders, and a first launch in the future gets the same treatment -- there
is no honest tenure either way. `first_launch_at_usable` says which
happened, and `build_desktop_settings` adds a note when it is false, so the
run's per-week rates aren't quoted against a start date nothing verified.
(Before 0.6.2 a nonsense value produced a tenure of roughly 11.5 billion
days. `installed_at_ms` had the same shape one field over -- an epoch
number kept beside an `installed_at_iso` of `None` -- and now clears with
it.)

##### Three things `get_desktop_settings` does to keep its own output honest

All three came out of 0.6.2, all three are on the response, and all three
are worth repeating to the user when they fire:

- **`unusable_number_count`.** A config file is JSON the app wrote, and
  Python's parser accepts `NaN`/`Infinity`/`-Infinity` where strict JSON
  does not -- so a corrupt value could make the whole response
  unserializable to any strict consumer. Every such number is replaced by
  `None` and counted here; list length is preserved, so a dropped item
  doesn't quietly become a shorter list. Nothing is clamped -- a bad number
  is not silently turned into a plausible one. Related: `any_enabled` in
  the per-account settings summary now ignores non-finite values, because
  `bool(float("nan"))` is `True` and the boolean *derived* from a dropped
  value was surviving to assert "yes, this is switched on somewhere";
  `unusable_value_count` reports it.
- **Withheld secret-shaped keys are counted per namespace.**
  `preferences` keys and config-file top-level keys are separate
  namespaces, and the note names them separately ("1 inside the
  `preferences` block", "1 at the config file's top level"). One combined
  count sent anyone looking for the key to the wrong place.
- **Nothing here is a finding.** All three are statements about this
  plugin's reading of the file, not about the user's setup. Say so if you
  surface them at all.

##### `scheduled-tasks.json` -- the file that "does not exist"

```
local-agent-mode-sessions/<account-uuid>/<org-uuid>/scheduled-tasks.json
{"scheduledTasks": [], "recordedSkips": {},
 "sundayAliasBoundaryStamped": true, "dayFieldsOrBoundaryStamped": true}
```

125 bytes on the verification machine: the file exists, the schema exists,
and `scheduledTasks` is an **empty array**. See "Two boundaries this doc
got wrong" under Known limitations -- an empty array read as an absent
feature is how this was previously written up as a confirmed hard boundary.

Read by `read_scheduled_tasks`, surfaced as `get_desktop_settings`'s
`scheduled_tasks` block: `available`, `file_count`, `files`,
`scheduled_task_count`, `tasks`, `tasks_truncated`,
`task_entry_shape_verified`, `recorded_skip_count`, `recorded_skip_ids`,
`boundary_flags`, `unrecognized_top_level_fields`.

**`task_entry_shape_verified` is `False` and means it.** The array has
never been observed populated on a real install, so the shape of a task
*entry* is unverified: entries come back verbatim-minus-redaction, and no
field inside one is named, parsed, or promised. Read them; don't parse
them. Over-correcting from "the file doesn't exist" to "here is the task
schema" would be the same mistake in the other direction.

**And an unverified entry has no size bound either.** A task carries
whatever prompt text it runs, so one entry can be arbitrarily large.
Since 0.6.2 an entry over `max_task_entry_chars` (2,000) is replaced by a
*measurement of itself* -- `{omitted, reason, size_chars,
top_level_field_count}` -- rather than truncated, because a truncation is
still a partial verbatim dump of unverified content, and it names no field
of the entry. Redaction runs **before** the measurement, so a
secret-shaped key cannot buy an entry past the check.
`oversized_task_count` and `max_task_entry_chars` come back on the block
with a note. Report that an entry exists and was too large to include;
don't describe what is in it.

Two field names look inconsistent with the rest of this plugin and are
deliberate: `recorded_skip_ids` (not `..._keys`) and
`unrecognized_top_level_fields` (not `unrecognized_keys`). `redact()` runs
over this response and strips any key whose name contains `key`, so either
of the natural names would have been silently deleted on the way out.

**CRITICAL for anyone extending this:** an identically named
`scheduled-tasks.json` also exists under `claude-code-sessions/`. That is
the Claude Code CLI surface, out of scope for this plugin entirely
(section 0). The glob is anchored to `local-agent-mode-sessions/` and must
stay that way; `test_no_cli_surface_remains` guards the boundary.

What an empty list does and doesn't prove: it means **this device records
no scheduled tasks**. It does not mean the feature is absent -- the file
and its schema are right here -- and it is not proof the account has none,
because the work runs server-side and this may simply not be where they are
recorded. Cross it against the `coworkScheduledTasksEnabled` /
`ccdScheduledTasksEnabled` toggles before saying anything about whether the
user schedules work.

##### `remote-session-spaces.json` -- turning a session id into a Space

```
local-agent-mode-sessions/<account-uuid>/<org-uuid>/remote-session-spaces.json
{"entries": [{"sessionId", "spaceId", "folders", "memoryEnabled"}]}
```

1,655 bytes / 6 entries on the verification machine. This is the lookup
table that makes `claude_desktop_config.json`'s `remoteSessionFolderGrants`
reportable at all: that setting is keyed by raw `session_01...` ids, which
name nothing a user has ever seen in the app. `read_remote_session_spaces`
keys it by session id, and `build_desktop_settings` joins it -- then
`spaces.json` for the id -> name step -- into every entry under
`access_scope.remote_session_folder_grants.by_session`, which gains
`space_id`, `space_name`, `space_resolved`, `space_resolution`,
`space_label`, `space_folders`, `space_memory_enabled`. The block itself
gains `resolved_space_count`, `unresolved_space_count`,
`space_lookup_available`. All 6 resolved on the verification machine.

**Quote `space_label`, never `session_id`.** Five outcomes are named and
none of them ever returns a bare id -- an unresolvable one gets a label
describing the situation instead:

| `space_resolution` | `space_label` |
|---|---|
| `matched` | the Space's real name |
| `space_id_not_in_spaces_json` | "a Space that is no longer listed in spaces.json -- most likely deleted in the app after this grant was made" |
| `space_listed_without_a_name` | "a Space that spaces.json lists without a name -- it still exists and was never given one, so there is no name to quote" |
| `session_not_listed` | "an unidentified Space -- this session isn't listed in remote-session-spaces.json on this device" |
| `no_space_id_recorded` | "an unidentified Space -- the session is listed but carries no Space id" |

A raw uuid in a report reads as an answer while being none.

`space_listed_without_a_name` was added in 0.6.2, and it is a correction
rather than a refinement. A Space listed with a **null name** previously
scored as resolved, produced a `label` of `None`, and fell through a
`str(uuid)` fallback -- so the one case that most needed a sentence was the
one that emitted a raw uuid. It is kept apart from
`space_id_not_in_spaces_json` deliberately: telling someone a Space was
deleted while it sits unnamed in their sidebar is a wrong answer, not a
vaguer one. The same distinction now exists on the two sibling paths that
carried the same wrong sentence -- session -> Space attribution (section 2)
and memory owners (below).

##### Local memory -- three stores, and it is *not* export-only

Memory content is on disk. This doc and `workspace-checkup.md` both said it
was export-only; that was false, and the correction is worth more than the
feature, because "export-only" meant a default local run silently skipped a
whole checkup area.

| Store | Glob (under the base dir) | On the verification machine |
|---|---|---|
| Per-Space | `local-agent-mode-sessions/*/*/spaces/<space-id>/memory/**/*.md` | **11 files, 16,622 bytes** across 5 Spaces |
| Cloud Project | `local-agent-mode-sessions/*/*/.project-cache/<project-uuid>/memory.md` | 3 files, 11,789 bytes |
| Account-level | `local-agent-mode-sessions/*/*/memory/**/*.md` | 1 file (`CLAUDE.md`), 20 bytes |

**15 files, 28,431 bytes total** (28,291 characters). The per-Space store
is the biggest and is one directory per folder-bound Space holding
*several* `.md` files -- a `MEMORY.md` index plus topic files -- not one
file each. Twelve such directories existed; seven were empty. A reader
built against only the account and cloud-Project paths would have found 41%
of the bytes and missed the tier closest to the user's actual work.

The join is already solved in both directions: `<space-id>` is the `id` in
`spaces.json`, and `<project-uuid>` is the uuid `read_project_cache`
already keys on. That is why memory is returned by
**`get_instructions_inventory`** (its `memory` block) rather than by a tool
of its own -- it is the fourth layer of the same thing that tool already
measures, text carried into a session the user didn't type this turn, and
its strongest finding (memory restating what the standing instructions
already say) is only computable with both in hand.

Response shape, per owner: `label`, `session_count`, `file_count`,
`char_count`, `files`, `duplicates_global`, `stale_file_count`,
`newest_/oldest_modified_at_local_iso`. Per file: `text` (capped by
`max_memory_chars`, default 2000 -- separate from `max_chars` because
memory is bulkier and there are more files of it), `char_count`,
`line_count`, `size_bytes`, `readable`, `problem`, and last-modified in
UTC, local, local date and weekday form plus `days_since_modified`. Block
totals add `repeated_across_memory`, `unreadable_file_count`,
`stale_file_count` / `stale_after_days` (90), and `timezone`.

Four things to know before building on it:

- **Staleness comes from the filesystem mtime.** Memory files carry no
  timestamp of their own, so this is the only signal there is. It is when
  Claude last *wrote* the file -- a prompt to check whether the content is
  still true, not evidence that it isn't.
- **`repeated_across_memory` filters template furniture.** Memory files
  share a house template, so a naive line comparison reports YAML front
  matter (`type: project`) and section headings (`**Purpose & context**`)
  as "remembered under four different Spaces" -- a finding about the
  template, not the user. `_memory_content_lines` strips a leading front
  matter block and heading-shaped lines; on the verification machine that
  took the count from 5 to **0**, which is the honest answer there. The
  instruction layers keep the plain `_normalize_lines` comparison: they are
  hand-written prose with no template behind them.
- **Memory is excluded from `totals`.** It loads on demand rather than on
  every turn, so folding it into `always_loaded_char_count` would overstate
  what a session pays for.
- **`since`/`until` do not scope it.** A memory file is current state with
  one last-modified date, not a series; only each owner's `session_count`
  is windowed.

Where an owner id can't be resolved to a name, `label` says what the thing
is ("a Space that is no longer listed in spaces.json -- most likely deleted
in the app while its memory files were left behind on disk") rather than
printing a uuid. Five resolutions are named -- `matched`,
`space_id_not_in_spaces_json`, `project_uuid_not_cached`,
`space_listed_without_a_name`, `project_listed_without_a_name`, plus
`no_owner_id` when no id could be read from the path. The two
`..._listed_without_a_name` outcomes were added in 0.6.2 and closed a real
hole: an owner listed with a **null name** scored as resolved, produced a
`label` of `None`, and the `None` fell through a `str(uuid)` fallback into
`repeated_across_memory[].owners`. `label` is now guaranteed non-empty and
that fallback is deleted rather than merely unreachable, and `name` is
nulled unless the resolution is `matched`, so `name` / `name_resolved` /
`label` cannot disagree with each other.

Two reader-side guarantees worth knowing if you extend this:
`read_memory_files` is a thin wrapper over the reader that returns
`redact(...)`, so every return path -- including any degraded one added
later -- is covered; and `max_memory_chars`/`max_chars` are clamped through
`clamp_max_chars`. A negative cap did not skip truncation, it sliced from
the *end* (`text[:-1]`), returning nearly the whole file while still
reporting `truncated: True`.

And local files are the copy *this device* holds: an
account export's `memories.json` (section 3a) comes from the account, so
**where the two disagree the export wins**. No local memory files is not
evidence the account has none.

##### These field names were verified once, on one machine

Everything in this section -- all seven files, the four at the base-dir
root and the three under `local-agent-mode-sessions/` -- was confirmed by
direct inspection of **a single macOS Claude Desktop install, in September
2026**. These are undocumented
app internals with no compatibility promise; `plan-usage-history.json`
carries a `version` field precisely because its shape is expected to
change. Field names, key names, units, and which keys are present at all
can drift between app builds, and a build that renames `fh` or moves
`preferences` will not announce it.

**This warning is earned, not boilerplate.** This plugin's own 0.5.0
release was largely bug fixes from exactly this failure mode -- `effort`
read from a key that does not exist (0 hits across 97 real session files),
`memories.json` parsed against a shape no export produces, `spaces.json`
assumed to be a bare list. Each one silently returned nothing while the
docs told the caller to reason from it. A reader that returns `None` and a
reader that is broken look identical from the outside. 0.6.2 added a fourth
and worse case, in the other direction: a field that *did* exist and was
never opened (section 2).

**A count in this doc is a reading with a date on it, not a constant.** The
session-file figures in section 1b were re-counted on 2026-09-05 and stand
at 97 files; the same install read 67 when they were first written. Quoting
a stale count as "confirmed" is how a measurement turns into a claim -- if
a number matters to a decision, re-run the command rather than citing this
file.

How to re-verify, on macOS, without changing anything:

```bash
cd ~/Library/Application\ Support/Claude
python3 -c "import json;d=json.load(open('plan-usage-history.json'));\
print(d.get('version'), len(d['samples']), d['samples'][-1])"
python3 -c "import json;d=json.load(open('claude_desktop_config.json'));\
print(list(d), sorted(d.get('preferences', {})))"
python3 -c "import json;d=json.load(open('extensions-installations.json'));\
print({k: sorted(v) for k, v in d['extensions'].items()})"
# the three per-account files (one <account>/<org> pair per install)
python3 -c "import json,glob;\
[print(p, list(json.load(open(p)))) for p in \
 glob.glob('local-agent-mode-sessions/*/*/scheduled-tasks.json')]"
python3 -c "import json,glob;\
[print(p, sorted({k for e in json.load(open(p))['entries'] for k in e})) \
 for p in glob.glob('local-agent-mode-sessions/*/*/remote-session-spaces.json')]"
# Use find, not ls with `**`. macOS ships bash 3.2, which has no globstar,
# so `memory/**/*.md` silently matches nothing and the two largest memory
# stores read as absent -- reproducing the very false negative this check
# exists to catch.
find local-agent-mode-sessions -path '*/memory/*' -name '*.md' | wc -l
find local-agent-mode-sessions -path '*/spaces/*/memory/*' -name '*.md' | wc -l
find local-agent-mode-sessions -path '*/.project-cache/*/memory.md' | wc -l
```

Do **not** print the *contents* of a memory file while re-verifying -- it
is Claude's synthesized profile of the user, the most sensitive material
this plugin touches. Count files and sizes.

Do **not** cat `config.json` -- print `list(json.load(...))` if the key
list is needed, and never the values.

What to check, in order: the file still exists where this section says;
the top-level wrapper is still the same shape (`{"version", "samples"}`,
`{"extensions": {...}}`, `{"coworkUserFilesPath", "preferences"}`,
`{"scheduledTasks", "recordedSkips", ...}`, `{"entries": [...]}`); the
per-sample keys are still `t`/`org`/`u` with `fh`/`sd`/`xu` inside `u`; the
units still look like percentages rather than raw token counts; and the
memory globs still match files where the tables above say they do.

**And check the empty ones especially.** `scheduledTasks` being `[]` and a
Space `memory/` directory being empty are the states that produced two
false "confirmed boundaries" in this file. An empty container is a reading,
not an absence -- if one is ever observed populated, the entry above needs
updating with what a real entry looks like, not just a count. If any
of that has moved, fix the reader *and* this section together -- a reader
patched without the doc is how the next reader gets written against the
wrong shape again. Every reader here already degrades to
`available: false` with a note rather than raising, so drift shows up as a
lens quietly reporting nothing, which is exactly why re-verification has to
be deliberate rather than triggered by an error.

Cross-platform status: unverified. Windows and Linux paths for these four
files are assumed to follow the same base-dir candidates as everything else
in section 1, and are probed for existence rather than assumed.

### Other files under the base dir, not read by this plugin

Cataloged for completeness/future extension, not because anything here
currently consumes them:

| Path | Contents |
|---|---|
| `ant-did` | Random per-device identifier (telemetry only). |
| `config.json` -- everything except the three allowlisted keys | The other 16 top-level keys on a real install: `lastKnownAccountUuid`, updater state, `dxt:allowlist*` caches, window/layout state -- and `oauth:tokenCache`/`oauth:tokenCacheV2`. **Secret -- never read or print the token-cache fields.** Section 1c covers the three keys that *are* read and the two gates that keep the rest out. |
| `bridge-state.json` | Keyed by `<org-uuid>:<account-uuid>`, tracks Code-tab<->Cowork bridge sync state. Not project data. |
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/cowork-clientdata-cache.json`, `cowork-gb-cache.json` | App/feature-flag caches. `cowork-gb-cache.json` specifically is a GrowthBook feature-flag cache: `{claudeJsonFilename, cachedGrowthBookFeatures}`, the second of which is a dict keyed by opaque internal names (`tengu_*`, `claude_code_*`, etc.). It held **514** entries when re-counted on 2026-09-06, against 442 a release earlier -- the churn is the point, and it is why no count here should be quoted as a fact about the app. Unstable and undocumented. Not project data, and not a real "user setting" source -- see section 6 for why this isn't read even for the environment-settings fields. |
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/agent/` | **Not app scratch space -- a real user session, and this doc said otherwise until 0.6.2.** See below. |
| `local-agent-mode-sessions/<account-uuid>/<org-uuid>/artifacts/` | Exists on the re-check machine (created 2026-07-08) but is **empty** -- 0 files. An earlier survey cataloged it as an app working directory and a later re-check recorded it as not existing at all; both were wrong. Empty is not absent: what it holds when a feature is in use is still unverified, so treat this as unknown and look again on a machine that has used it. |
| `local-agent-mode-sessions/.../<sessionId>/audit.jsonl` + `.audit-key` | Per-session HMAC-chained audit log of tool calls/permission decisions. Signing key is OS-keychain-encrypted -- don't try to read it. |
| `claude-code/`, `claude-code-vm/` | The Claude Code binary + VM workspace for the Code tab. Not data. |
| `vm_bundles/` | Cached Cowork sandbox VM image. Not data. |
| `IndexedDB/https_claude.ai_0.indexeddb.leveldb/`, `Local Storage/leveldb/`, `Session Storage/` | Chromium renderer storage for the embedded claude.ai web view. Snappy-compressed LevelDB SST files -- plain `strings`/grep mostly fails (only schema key names surface, not values). Not worth a LevelDB+Snappy dependency for this use case; see "Known limitations." |
| `Cookies`, `buddy-tokens.json` | **Secrets. Never read, print, or hash these into any skill output.** |

#### The `agent/` directory holds a session, and no lens can see it

Verified directly on 2026-09-05 and re-verified on 2026-09-06, and it
contradicts what this table said for three releases.
`local-agent-mode-sessions/<account>/<org>/agent/` held one session file,
`local_ditto_<org-uuid>.json` (323,724 bytes, **36** top-level keys), beside
its own `local_ditto_<org-uuid>/` working directory. The file is a
**session file in the same format as every other one**:

- `sessionType: "agent"`, no `title`, no `spaceId`
- a real `initialMessage` (2,875 characters -- a user's own prompt), a
  `model` (`claude-sonnet-4-6`), `createdAt`/`lastActivityAt` in epoch ms
- `userSelectedFolders: ["/Users/<user>/code"]`
- a sibling `local_ditto_<org-uuid>/` directory with its own nested
  `.claude/projects/<encoded-cwd>/*.jsonl` transcript, its own
  `audit.jsonl`, and an `outputs/` folder holding three real deliverables

**It is not read.** `read_local_sessions` globs
`local-agent-mode-sessions/*/*/local_*.json`; this file sits one level
deeper, at `*/*/agent/local_*.json`, so it is invisible to every lens --
its work appears in no chat count, no model tally, no membership bucket.
Its nested transcript is out of reach for the same reason:
`_COWORK_TRANSCRIPT_GLOB` is anchored at `local-agent-mode-sessions/*/*/
local_*/.claude/...`, and this one sits under `agent/` first. That is a
gap, not a policy: nothing about it is out of scope the way Claude Code
is.

Not fixed in 0.6.2, deliberately, because two questions have to be answered
first and neither can be answered from one observation:

1. **Is it a sub-agent of another session?** The filename carries the *org*
   uuid rather than its own, which no ordinary session does. If it is
   spawned by a parent session, counting it adds that work twice.
2. **Is there ever more than one?** One directory, one file, one machine.
   A reader written to that is a reader written to a sample of one.

Until both are settled, the honest description is the one above: a real
session, deliberately not read, with the reason recorded. Do **not** repeat
the old line that `agent/` is the app's own scratch space -- it isn't, and
believing it is what kept anyone from opening the file.

### 1a. User-visible output folder

**Ask Claude's own settings first.** `coworkUserFilesPath` in
`claude_desktop_config.json` (section 1c) is the configured answer; the
candidate list below is the fallback for when that file is missing or
unreadable. `check_data_access`'s `output_dir_source` says which was used
-- `"claude_desktop_config.json"` or `"candidate_probe"` -- and that
distinction belongs in anything reported to the user.

| macOS/Linux | Windows (inferred, unverified) | Notes |
|---|---|---|
| `~/Claude/` (current convention) | `%USERPROFILE%\Claude\` | Cowork writes Artifacts and scheduled-task outputs here for the user to browse directly. **Not removed if the app-data directory is deleted.** |
| `~/Documents/Claude/` (legacy installs) | `%USERPROFILE%\Documents\Claude\` | Older fallback path per docs; `output_dir_candidates()` checks this too. |
| `~/.Trash/Claude` (macOS only) | -- | Cowork's own output folder survives deleting the app-data directory, and can end up in Trash without the user realizing it still exists -- check here if the folder above is missing before concluding it never existed. |

## 2. Which Space (or cloud Project) a chat is in -- the join `join_local_sessions` does

**The app writes this down.** A session file carries an explicit `spaceId`
-- and a `spaceIdSetBy` beside it naming who set it (`"user"` on all 59
that had one here) -- which resolves straight through `spaces.json`. That
is the app's own record of where the user filed the chat, and it is
authoritative. `read_local_sessions` surfaces it as `space_id` /
`space_id_set_by`.

Precedence, highest first. A session lands in exactly one bucket, so the
four are safe to add up:

1. **Recorded `space_id`** -> that Space. `space_source: "app_record"`.
2. **Non-empty `user_selected_project_uuids`** -> that cloud Project (join
   on `uuid` against `.project-cache`). Trusted even when a uuid has no
   cache entry -- the uuid is the real signal, the cache only supplies a
   *name*, and dropping uncached uuids used to push real Project members
   into `unfiled`. Uncached ones come back in `uncached_project_uuids`.
3. **Folder match**, and *only* for a session the app recorded no Space
   for: the session's `userSelectedFolders` against each Space's
   `folders[].path`. This is a guess and is labelled one:
   `space_source: "inferred_from_folder"`.
4. Otherwise **unfiled** (`membership.unfiled_session_ids`, never silently
   dropped).

Cloud Projects and Spaces did not co-occur on any session here -- all 16
sessions carrying `userSelectedProjectUuids` were in the group with no
`spaceId` -- but the precedence is written out rather than assumed, because
nothing on disk promises that.

### What was shipped instead, and why it is the most expensive bug this plugin has had

Until 0.6.2 this section said, in bold, that **"there is no single index"**
for chat -> Space membership, and the reader rebuilt it from step 3 alone:
match `userSelectedFolders` against each Space's folders by prefix, take
the **first** Space in file order.

That reconstruction disagreed with the app on **every one of the 59
sessions that had a record**. Worked example, session
`local_0a3a3b90-ef9f-4652-9a92-3adf2cefee0e`:

| | Value |
|---|---|
| Its one folder | `/Users/<user>/Library/CloudStorage/OneDrive-Personal/Vacations/July 2026` |
| The app's own `spaceId` | **Vacation - July 2026** |
| What the prefix join reported | OneDrive-Personal |

The "OneDrive-Personal" Space is scoped to the whole cloud-storage root, so
by prefix it *contains* every Space nested beneath it, and being early in
file order it won. Thirty-six vacation chats collapsed into it. The
reorganization lens -- this plugin's headline output -- consequently told
the user that "Vacation - July 2026" and "ACTs 2027" were **empty Spaces**
and that they had 43 loose chats sitting in a generic cloud-storage folder.
Every number in that lens was wrong, and none of it looked wrong: the
Spaces were real, the folder was real, and the counts added up.

Two rules now keep the fallback honest (`_best_folder_match`), and both
exist because of that failure:

- **Most specific wins.** Spaces nest. Among the Space folders that contain
  the session's folder, the **longest** one is chosen, not the first found.
  That files the chat where the user would file it.
- **Match on a path boundary.** `/a/b` contains `/a/b/c`, but must never
  claim `/a/bc`, which is an unrelated sibling directory. The old check was
  a bare string prefix.

A Space scoped to bare `/` is skipped by the fallback entirely rather than
swallowing every session on the machine.

### Provenance travels with every attribution -- report it

The join returns `space_attribution_by_session_id`, with an entry for
**every** session including the ones in no Space, so a caller never has to
interpret a missing key. Both `list_local_workspace` (on each
`local_sessions` row) and `get_project_membership` (on each row) splice
that same map in, so the two tools cannot answer this question differently
-- which they did until 0.6.2, when `list_local_workspace` reported
`space_id: null` for all 97 sessions while `get_project_membership` named a
Space for the same chats.

| Field | What it is |
|---|---|
| `space_id` / `space_name` | The id, and the name **only** when it resolved. |
| `space_resolved` / `space_resolution` | Whether the id turned into a name, and which of four outcomes: `matched`, `space_id_not_in_spaces_json`, `space_listed_without_a_name`, `no_space`. |
| `space_label` | Always readable prose -- the Space's real name, or a sentence explaining why there isn't one. **Never a bare uuid.** Quote this. |
| `space_source` | `app_record` / `inferred_from_folder` / `none`. |
| `space_source_note` | The same thing as a sentence a non-technical reader can act on; the full map is returned as `space_source_notes`. |
| `space_from_app_record` | `True` only when the app itself recorded it. |
| `space_matched_folder` | Which Space folder the guess matched -- populated **only** for a guess, so it never reads as evidence for a recorded fact. |

`unresolved_space_ids` lists recorded ids `spaces.json` no longer names --
most likely Spaces deleted in the app after chats were filed in them. It
was empty on this machine.

**Say which kind of attribution a finding rests on.** "Claude filed this
chat under X" and "we matched this chat to X from the folder it was working
in" are different claims, and the reorganization lens is where the
difference gets acted on -- see
[reorganization.md](reorganization.md).

### What the join returns on the machine this was corrected against

97 sessions, 11 Spaces, 7 cached cloud Projects:

| Sessions | Space | Source |
|---|---|---|
| 36 | Vacation - July 2026 | `app_record` |
| 8 | CUST - Pico Construction | `app_record` |
| 5 | ACTs 2027 | `app_record` |
| 3 | BetterBelly | `app_record` |
| 2 | OneDrive-Personal | `app_record` |
| 2 | skills-stash | `app_record` |
| 1 each | Claude Training, LinkedIn, CUST - Nick Becker | `app_record` |
| 1 | OneDrive-Personal | `inferred_from_folder` |
| 1 | ShadeTree | `inferred_from_folder` |
| 36 | not in any Space | `none` |

`by_space_id` 61 + `by_project_uuid` 16 + `unfiled` 20 = 97. One Space
(`CRM`) genuinely holds no sessions; before the fix, three did.

The two inferred rows are the fallback doing its job: each session's folder
is *exactly* a Space's own root folder rather than merely underneath it,
which is about as strong as an inference gets -- and it is still an
inference, still tagged `inferred_from_folder`, and still carries
`space_matched_folder` so a report can say "we matched this from the
folder; Claude didn't record a Space for it."

Of the 38 sessions with no `spaceId`: 28 have no folders at all (Chat-tab
sessions, most likely), and of the 10 that do, 8 point at folders that
belong to no Space -- so "no Space" is the correct answer for them, not a
gap in the reader.

### For the export, the reconstruction is still all there is

An account export carries no equivalent record. When its own
per-conversation project link is empty, reconstruct membership from content
(section 3, and [reorganization.md](reorganization.md)) -- but that is an
inference about export data, and it should be presented the way
`inferred_from_folder` is here, not as the account's own filing.

### A per-chat "tasks" signal is still not available

Nothing joins a scheduled task to the chat that created it. But the flat
claim that used to sit here -- that Cowork's scheduled tasks "don't appear
in any file under the app-data directory" -- was **false**.
`scheduled-tasks.json` exists under
`local-agent-mode-sessions/<account>/<org>/` and `get_desktop_settings`
reads it (section 1c). It was empty on the machine that claim was verified
against, which is how an empty array got written up as an absent feature.

So: report what the file says (`scheduled_task_count`, and the
`recordedSkips` identifiers), never "this account has no scheduled tasks."
Run history and per-task settings -- model, effort, approval mode, where
the real cost would be -- are still not on disk, and the tasks execute
server-side, so an empty list may equally mean none are scheduled or that
this device isn't where they are recorded. What a scheduled task *produced*
may still show up in the user-visible output folder (section 1a).

## 3. Local files vs. the account data export -- complementary, not redundant

| | Account export (`conversations.json` + `projects/*.json`) | Local (this plugin's `list_local_workspace`) |
|---|---|---|
| Product surface | **claude.ai web browser chats only.** Confirmed by format: export message objects are flat `{uuid, text, sender, content, ...}` with no `cwd`/`entrypoint`/`cliSessionId` -- the classic web/API shape. | **Desktop app only** -- Cowork and the Chat tab. Zero overlap with web chats. |
| Project registry | The full cloud Project list *at export time*, including ones with zero local cache footprint. | `.project-cache` only holds Projects opened from *this device* recently -- can both undercount (never opened here) and include Projects newer than any export. |
| Memory | `memories.json` (section 3a) -- account-wide and frozen at export time. **Authoritative where the two disagree**, because it comes from the account rather than one device. | Markdown memory files in three stores on disk, read since 0.6.1 by `get_instructions_inventory` (section 1c). More granular (per Space, per cloud Project, plus account-level), updated live, and readable with **no export at all** -- but this device only, and only as current as the last time the app wrote here. |
| Freshness | Frozen at export time. | Live, this-device-only. |
| Cross-device | Aggregates every web session on the account, any device. | This device's Desktop activity only. |

**Bottom line:** combine both when both are available. Use the export for
the authoritative cloud Project registry and web chat history; use local
data for Cowork Spaces, Chat-tab sessions, and current-state signals. Join Projects on `uuid` to de-duplicate overlap.

### 3a. `memories.json` -- confirmed shape

The export's memory payload. It is no longer the *only* source of memory
content -- local memory files are read too (section 1c) -- but it is the
authoritative one, because it comes from the account rather than from
whatever one device happened to write. Verified against two real exports:

```json
[{"conversations_memory": "...",
  "project_memories": {"<project-uuid>": "..."},
  "account_uuid": "..."}]
```

Three things to know, each of which was previously gotten wrong:

- **The payload is a single-element list, not an object.** An earlier
  version of this plugin called `.get` on it, so every real export fell
  through to a raw-JSON-preview branch and emitted a truncated dump instead
  of memory. If memory output ever looks like JSON again, check this first.
- **Per-project memory is keyed by project uuid and carries no project
  name.** The uuid is the join key back to `projects_index.json`; don't
  expect a readable name in this file.
- **Memory is categorized, rendered as markdown with bold headers**
  (`**Work context**`, `**Top of mind**`, `**Purpose & context**`, ...).
  Since 2026-07-10 memory is individual categorized entries that Claude
  reads and updates during conversations -- **not** a summary regenerated
  on a ~24-hour cycle. Any guidance saying otherwise, or telling a user to
  "wait a day for memory to settle", is stale and should not be repeated.

`parse_export` returns two views of this: `stats.memory` (availability,
character counts, and category names -- account-level and per project uuid)
for cheap auditing, and `memory_context.md` (the full text) for when a
finding genuinely needs to read it. Prefer the former; memory text is the
most sensitive material in an export.

## 4. Where model-usage data actually lives

A summary of the confirmed facts scattered through sections 1-2 above, in
one place, since it's easy to assume this is uniformly available when
it isn't.

First, a boundary that is easy to blur: **a model tally is not a
consumption measurement.** Which model ran how many messages is a proxy for
what the plan was spent on -- a good one, but inferred. The measurement
lives in `plan-usage-history.json` (section 1c). When the two are both
available, the measurement leads and the tally explains it; see
[usage-efficiency.md](usage-efficiency.md).

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
no single view in the app brings them together. (A fourth kind of standing
text -- Claude's own **memory** -- is returned by the same tool but is not
one of these layers: the user didn't write it, and it loads on demand
rather than on every turn. Section 1c covers it.)

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
  file *under that name*. The real source for memory/skills/plugins state
  is still the per-session field above, not a settings file.

- **Partly answerable since 0.6.0, and this entry has been narrowed
  accordingly.** It used to say that "chat search enabled" *and any other
  generic account preference or feature toggle* was a confirmed hard
  boundary. The first half still holds; the second half does not.
  `claude_desktop_config.json`'s `preferences` block (section 1c) is a
  real, stable-looking, user-facing settings source holding 27 keys on the
  verification machine -- standing folder grants, browser and scheduled-task
  switches, permission-gate settings -- and `get_desktop_settings` reads it.
  So: an account-level toggle is worth *looking for there* before
  concluding it isn't answerable.

  What remains a genuine boundary is a toggle that appears in **neither**
  that file nor the per-session fields -- "chat search enabled" is still
  one of them. The only other on-disk candidate is `cowork-gb-cache.json`
  (see the "Other files" table above), an internal GrowthBook feature-flag
  cache keyed by opaque names (`tengu_*`, `claude_code_*`, ...) whose entry
  count moved from 442 to 514 between two releases. That is an engineering/experimentation cache, not a user-facing
  settings source, and it churns across releases -- don't parse it, and say
  "not currently available from local data" plainly rather than guessing a
  value out of it.

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
- **Scheduled tasks** -- Cowork's `/schedule` creates them and the app's
  own Scheduled page manages them. This plugin can now report what
  `scheduled-tasks.json` records on this device (section 1c) -- a count,
  the skip identifiers, and entries it deliberately does not parse -- but
  it cannot inventory the account's tasks, read their run history, or see
  the per-task model/effort/approval settings where the real cost lives.
  Recommend `/schedule` when an automation candidate turns up, and point
  the user at the app for anything about existing tasks rather than
  answering from this file alone.
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
`get_instructions_inventory`, `parse_export`, `get_plan_usage_history` --
takes the same `since`/`until` pair, implemented once in `time_window.py`.
Omit both for the full history.

Two exceptions to note before assuming uniformity:

- **`get_plan_usage_history` matches on the sample timestamp falling
  inside the window**, not on interval overlap, because a usage sample is
  a point in time rather than a span (section 1c). A window narrower than
  the sampling gap can legitimately come back empty -- and the response
  says so, distinguishing "no data recorded for that period" from "no
  usage."
- **`get_desktop_settings` takes no window at all.** Settings are
  current state, not a time series: nothing on disk records when a toggle
  was flipped or a folder trusted, so a window would have nothing to
  filter and would imply a precision the data doesn't have. On a windowed
  run, that section of the report is *not* windowed -- say so rather than
  letting the header's date range imply it is.

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

### Every instant this plugin returns, in the same five renderings

Since 0.6.2 an instant is rendered once, by one helper, everywhere. For a
field named `<x>` you get:

| Field | What it is |
|---|---|
| `<x>` or `<x>_ms` | the raw value -- epoch ms (or epoch seconds for a filesystem `modified`). For arithmetic, not for reading. |
| `<x>_iso` | UTC. For comparing and joining. |
| `<x>_local_iso` | the same instant on this machine's clock, offset attached. |
| `<x>_local_date` | the local calendar date. |
| `<x>_local_weekday` | the local weekday name. |
| `<x>_utc_offset` | e.g. `UTC-05:00`. |

**Build every sentence a person reads from the local fields.** A weekday
computed in UTC is a wrong answer for someone reading their own calendar,
not a caveated right one -- on the verification machine the account's first
launch is Sunday 2026-06-28 in UTC and **Saturday 2026-06-27** locally.
`check_data_access`, `list_local_workspace`, `get_instructions_inventory`
and `get_desktop_settings` each carry a `timezone` block naming the zone
those fields are in; `list_local_workspace` also carries a
`local_time_note` saying it in a sentence.

Where 0.6.2 found the convention missing, and fixed it: session
`created_at`/`last_activity_at`, cloud-Project `synced_at`,
`get_desktop_settings`' `first_launch_at`/`installed_at`, every probed
path's `modified` in `check_data_access` (a raw epoch-**seconds** float
before, in all three blocks), memory owners' `newest`/`oldest_modified_at`,
and instruction variants' `latest_activity_at` (raw epoch ms, rendered
nowhere at all).

Two things are deliberately *not* local. `parse_export`'s
`stats.date_range` and per-conversation `created_at` are the export's own
strings, passed through as they arrive -- they get no local companion, so
say which they are. And the `time_window` bounds themselves stay UTC; see
next.

### A bare date bound is midnight **UTC**, and says so

`parse_bound("2026-08-01")` resolves to `2026-08-01T00:00:00+00:00`, not to
local midnight. That is a deliberate contract -- every existing caller,
every saved analysis, and the plugin's own tests are anchored to it -- but
it means that on a machine east or west of UTC the window edge falls
part-way through a local day while every date the user reads is local.

The mismatch used to be invisible. It is now disclosed: `resolve()` returns
`since_local`/`until_local`/`timezone` beside the bounds, plus
`boundary_notes` whenever a bound named no timezone, and every tool that
reports `notes` repeats it. On the verification machine (UTC-05:00) that
note reads:

> `since` was given as "2026-08-01", which names no timezone, so it was
> anchored at midnight UTC -- 2026-07-31T19:00:00-05:00 on this machine's
> clock (UTC-05:00). ... Pass "2026-08-01T00:00:00-05:00" instead ...

So: **pass a bound with an explicit offset when the window is meant to line
up with the user's calendar**, and never print a window bound as a bare
date with no zone marker. The note is suppressed on a machine already on
UTC, where the two coincide.

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

**Its `memory` block is the exception inside the exception: the window does
not scope it.** A memory file is current state carrying one last-modified
date, not a record of what was remembered during a period, so windowing it
would either drop files that are still in force or imply a history the
files don't hold. Only each memory owner's `session_count` is windowed. On
a scoped run, say which figures are windowed and which are current state --
the same caveat `get_desktop_settings` needs, for the same reason.

## Known limitations

- Local memory **is** read (all three stores -- section 1c), by
  `get_instructions_inventory`. What it cannot see: memory the account
  holds that this device never wrote, and any memory written since the last
  time the app ran here. The export's `memories.json` covers those and wins
  where the two disagree.
- `IndexedDB`/`Local Storage`/`Session Storage` (the embedded claude.ai web
  view's Chromium storage) are Snappy-compressed LevelDB and are not read at
  all -- not worth a LevelDB+Snappy dependency for this use case.
- `Cookies` and `buddy-tokens.json` are real credentials -- this plugin
  never opens them at all, regardless of the generic secret-key redaction
  applied to the files it does read. `config.json` **is** opened, but only
  through the three-key allowlist in section 1c: its `oauth:tokenCache*`
  fields are never read, never returned, and never hashed into any output.
  If you are extending that reader, the allowlist and the second
  `_is_secret_key` gate are the guarantee -- don't route around them.
- This is all a cache, not authoritative. Say so in the dashboard's data
  quality notes rather than presenting counts as exact.

### Three boundaries this doc got wrong -- and the failure mode behind all three

Three things were written here as **confirmed hard boundaries**, in bold,
with instructions to state them plainly to the user. All three were false:

1. **"There are no schedule-shaped files anywhere under the Desktop
   app-data directory."** `scheduled-tasks.json` exists, with a schema
   (section 1c). It was *empty* on the machine the claim was verified
   against.
2. **"Memory content is export-only."** Fifteen memory files, 28KB, in
   three stores on disk (section 1c). The largest store -- per-Space memory
   -- sits under a directory pattern the earlier survey didn't glob, and
   seven of the twelve Space memory directories are empty, so a spot check
   could easily land on nothing.
3. **"There is no single index"** of which Space a chat is in (section 2).
   There is: an explicit `spaceId` the app writes on the session, present
   on 59 of 97 sessions here. The 38 without one look exactly like a field
   that doesn't exist. This is the one that cost the most -- it made the
   reorganization lens describe a workspace the user does not have.

**The failure mode is the same one every time: an empty array, a null
field, or a differently shaped container read as an absent feature.** The
first instance was 0.5.0's `memories.json`, parsed as an object when the
payload is a single-element list -- every real export fell through to a
raw-JSON dump. Each time, the wrong conclusion looked exactly like a
correct one from the outside, and each time it went into the docs as
settled fact rather than as an observation. The checklist that would have
caught all four is at the top of this file, under "Read this before writing
down that the app doesn't store something."

Rules that follow, for anyone extending this plugin:

- **An empty array is a reading, not an absence.** `[]` means "this
  container is empty on this machine right now". It never means the feature
  doesn't exist, and it is not evidence about the account.
- **A file you didn't find is not a file that isn't there.** Glob the
  parent directories and look, rather than concluding from one path that
  didn't match. Both misses above were pattern misses.
- **Reserve "confirmed boundary" for something you established
  positively** -- a documented architectural fact, or a file whose contents
  you read and understood. "I looked and didn't see it" is a search result.
  Write down what you observed and where you looked, so the next person can
  tell the difference.
- **Re-verify the claim next to the one you're changing.** 0.6.0's docs
  pass added 309 lines to this file and left the false scheduled-tasks
  boundary sitting in it untouched, in the same release that read three new
  files out of the same directory tree.

What is still true, and is a genuine architectural fact rather than a
search result: **scheduled tasks run remotely.** Since
2026-07-07 they execute on Anthropic's servers on their own cadence, so
they run "even when your computer is asleep or the Claude Desktop app is
closed" -- there is nothing for the device to cache. (The surviving
exception is a task that needs local files or apps, which still runs
locally.) Two consequences:

- Any older guidance that a scheduled task requires the machine awake with
  Desktop open is **wrong for the general case**. Don't repeat it.
- A task's per-task **model, effort and approval mode** -- which is where
  the real cost lives, e.g. a daily digest quietly running on the top tier
  at high effort in Auto approval -- is not in this file and not on disk at
  all. Neither is run history. If the user asks, point them at the app's
  Scheduled page rather than guessing, and don't extrapolate from
  `scheduled-tasks.json`, which records none of it.

What is *not* out of scope: **recommending new** scheduled tasks as
automation candidates. That stays in
[usage-efficiency.md](usage-efficiency.md). And a partial audit of existing
ones is now possible -- what this device records, with the entry shape
explicitly unverified -- rather than nothing at all.

One partial signal worth knowing: scheduled-task *output* lands in the
user-visible Claude output folder (section 1a). If that folder isn't in the
session's file-access scope, anything a scheduled task produced is missing
from the analysis entirely -- `check_data_access` already warns about this.

## Sources

- [User identity and local data -- Claude.ai Documentation](https://claude.com/docs/third-party/claude-desktop/data-storage) (official; describes the 3P variant, whose folder *names* match the consumer build confirmed on disk)
- [Explore the .claude directory -- Claude Code Docs](https://code.claude.com/docs/en/claude-directory)
- Community-reported GitHub issues for the Windows MSIX path-redirect
  behavior: anthropics/claude-code#58421, #57998, #69663 -- not official
  docs, verify against a real Windows install before relying on them.
