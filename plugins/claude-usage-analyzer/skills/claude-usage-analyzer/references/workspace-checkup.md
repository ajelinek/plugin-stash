# Setup & effectiveness checkup

A third lens, alongside [reorganization.md](reorganization.md) (how chats
should be restructured) and [usage-efficiency.md](usage-efficiency.md) (is
day-to-day usage efficient). This one asks: **is the workspace itself
configured well, or is it quietly costing the user on every single turn?**

It covers six areas:

1. **Standing instructions** -- the three always-loaded layers, audited for
   duplication and derivable content.
2. **Memory** -- what Claude has actually remembered, read straight off
   this machine, and whether it has gone stale or cross-contaminated.
3. **Project and Space quality** -- does each one have a name, description
   and instructions that match what it is actually for?
4. **Access scope** -- what Claude is standing-trusted with, and which
   folders and hosts each session actually reached.
5. **Capability inventory** -- what is installed and enabled versus what is
   actually used.
6. **Settings hygiene** -- the per-session toggles, the app-wide ones, and
   what this device records about scheduled tasks.

**Areas 4, 5 and 6 were structurally blind until 0.6.0.** They ran on
per-session data alone, which can only describe folders, capabilities and
toggles that some session happened to use. Anything standing at the account
level -- a folder trusted once and left trusted, an extension installed and
never switched on, an app-wide setting flipped years ago -- appeared
nowhere and so was reported as nothing. `get_desktop_settings` supplies
that layer; see Step 1.

Like the other two lenses, this produces `findings` entries for the same
`plan` dict the main skill file renders. It never changes anything.

## Where this comes from

Claude Code's `/doctor` (alias `/checkup`) does this for developers. Its
most useful checks aren't about installation at all -- they're about
`CLAUDE.md`, the file that gets loaded into every session:

> Deduplicates local `CLAUDE.md` files against checked-in ones, trims
> checked-in `CLAUDE.md` files by cutting content Claude could derive from
> the codebase, and migrates the always-loaded guidance that remains into
> skills and nested `CLAUDE.md` files that load on demand. The trim cuts
> sections such as directory layouts, dependency lists, and architecture
> overviews, and keeps pitfalls, rationale, and conventions that differ
> from tool defaults. [...] Reports findings first and asks for
> confirmation before changing anything.
> -- [Claude Code commands reference](https://code.claude.com/docs/en/commands)

The Desktop/Cowork equivalent of `CLAUDE.md` is **standing instructions**,
and there are three layers of them (see
[data-sources.md section 5](data-sources.md#5-standing-instructions----the-three-layers-and-how-to-read-them-together)).
The checks below are that same idea, translated. What does *not* translate
-- installation paths, duplicate installs, hooks, release channels -- stays
out. A business user has no installation to diagnose.

## Step 1: Get the data

Call `get_instructions_inventory()`, passing the run's `since`/`until` if
one was set. One call gives you every layer with character counts, session
reach, and the two mechanical overlap signals.

A window here is more than a filter. Because the global text is stored
per-session, scoping to a period reports the instructions that were
actually in force *then*, and the session reach for that period -- which is
how to ask "what were my standing instructions last quarter," a question
the app itself can't answer. It also changes what `session_count` of zero
means: unused *in this window*, not unused ever. Say which.

Read its `notes` first and repeat any of them alongside your findings --
particularly "no cloud Projects are cached on this device," which means the
Project layer is undercounted, not clean.

For every other check below you also need `list_local_workspace`. Its
per-session fields split into two groups that are meant to be **crossed,
not confused**:

- **What a session was configured to be able to do** --
  `permission_mode`, `effort`, `default_model`,
  `memory_enabled`/`skills_enabled`/`plugins_enabled`,
  `enabled_mcp_tool_names`, `enabled_mcp_server_labels`,
  `remote_mcp_server_names`, `plugin_names`, `slash_command_names`,
  `user_selected_folders`, `egress_allowed_domains`,
  `web_fetch_allowed_url_hosts`, `cwd`,
  `system_prompt_char_count`, `memory_guidelines_char_count`.
- **What it actually did** -- `tools_invoked`, `mcp_servers_invoked`,
  `touched_dirs`, `url_hosts`, `message_count`, `models_used`.

The gap between the two is where most findings in this lens come from.

Two caveats that apply to every check here:

- These are **per-session** values. Check whether one actually varies
  across a Space/Project's sessions before reporting a single verdict for
  the whole thing.
- A field being absent is normal, not a finding. Presence varies by
  session, and was verified on a single machine. Never build a finding on
  a missing field alone.

If a session's `transcript_sampled` is true, its transcript ran past the
scan cap and the tail was sampled -- the tallies still describe the whole
session but under-count. Say so rather than quoting the number as exact.

For the memory checks, `get_instructions_inventory`'s **`memory` block is
the default source** -- memory content is on disk and a plain local run
reads it. Its `max_memory_chars` parameter caps each file's returned text
(default 2000, lower than `max_chars` because memory files are bulkier and
there are more of them); every `char_count` is the true uncapped length
regardless.

**"Memory content is export-only" was written here, and it was false.**
There are three memory stores on disk -- account-level, per-Space, and per
cached cloud Project -- holding 15 files and 28KB on the verification
machine ([data-sources.md section
1c](data-sources.md#1c-account-level-files----the-account-and-the-app-not-a-session)).
The practical cost of the wrong claim was that a default run, with no
export in hand, skipped this whole area and said nothing.

`parse_export`'s `stats.memory` and `memory_context.md` are still worth
having when an export is in play: they cover the account rather than one
device, and **the export wins where the two disagree**. Say which source a
finding came from.

### And call `get_desktop_settings()` -- the account-level layer

Areas 4, 5 and 6 need this. It takes **no `since`/`until`**: these are
current-state settings, and nothing on disk records when a toggle was
flipped or a folder trusted, so there is no history to window. On a scoped
run, say plainly that this section describes the account as it is *today*
while the rest of the report covers the chosen period -- otherwise the
dashboard's date range silently implies a precision this data doesn't have.

| Block | Feeds | What per-session data could never see |
|---|---|---|
| `access_scope` -- `trusted_folders`, `remote_folder_consent`, `remote_session_folder_grants` | Area 4 | A **standing** grant. Approved once, in force until removed in Settings, and invisible in `user_selected_folders` if no recent session happened to open it. |
| `extensions` | Area 5 | What is **installed**. An extension never enabled in any session appears in no session file at all. |
| `settings` + `unrecognized_settings` | Area 6 | App-wide toggles. Set once, account-wide; not per-session fields. |
| `scheduled_tasks` | Area 6 | What this device records about scheduled tasks. The toggles say the feature is on; this file says whether anything is scheduled here. |
| `app_config` | All three | `first_launch_at`, the denominator for any rate you quote. Forty chats means something different in a week-old install than a year-old one. |

Read the per-session data and the settings **together**. The per-session
fields say what was *used*; the settings say what is *available*; the gap
between them is most of what is worth reporting in these three areas.

Same caveats as everywhere else: any block can come back
`available: false` when its file is missing (a fresh install, a different
platform, or a session without access to the app-data folder), and that is
normal rather than an error. Read the `notes` and repeat the relevant ones.

**Check the right thing when a block is empty.** `check_data_access`'s
`data_files` probes exactly four files -- `plan-usage-history.json`,
`claude_desktop_config.json`, `extensions-installations.json` and
`config.json` -- and rolls them into `plan_usage_history_available` and
`desktop_settings_available`. It knows nothing about memory,
`scheduled-tasks.json` or `remote-session-spaces.json`, so
`desktop_settings_available: true` means the settings *tool* can run and
says nothing about whether those blocks found anything. For those, read
the block's own flags instead: `memory.available`/`file_count`/
`unreadable_file_count`, `scheduled_tasks.available`/`file_count`/
`files[].readable`, and
`access_scope.remote_session_folder_grants.space_lookup_available`. See
[data-sources.md section 1c](data-sources.md#1c-account-level-files----the-account-and-the-app-not-a-session)
for the full table of which flag answers which empty block.

**One thing not to read as evidence: this plugin's SessionStart warning.**
The hook that warns when no Claude data directory is visible only fires
where hooks run -- hooks and sub-agents are Cowork features, greyed out in
the Chat tab -- so a user working in the Chat tab never sees it no matter
how badly scoped their session is. Its silence proves nothing. Call
`check_data_access` and read the result; don't treat "no warning appeared"
as confirmation that everything is visible.

## Step 2: The checks

Each one below is a *candidate* finding. Apply the same evidence bar as
everywhere else in this skill: a real, recurring pattern, not a one-off,
and never a decorative finding to fill the section. An honest "the
workspace looks fine" is a good result and should be said plainly.

### Duplication between layers

`duplicates_global` on each Space/Project lists lines repeated verbatim
from the current global instructions. Every one of those lines is sent
twice on every turn in that Project. Report the Projects with the largest
overlap and recommend deleting the duplicated lines from the Project (the
global text already covers them) -- not the other way around.

### The same text pasted into Project after Project

`repeated_across_projects` is the inverse signal. If the same paragraph
appears in four Projects, it isn't Project-specific context -- it's
standing preference that belongs in global custom instructions once, or in
a Skill if it's a procedure rather than a fact. Recommend the move, and say
which Projects it can then be deleted from.

### Instructions that describe things Claude can already see

This is `/doctor`'s trim check, and it's the highest-value one. Read the
actual instruction text and look for content that restates what's already
available to the session:

- A list of the files or folders in the Project's own knowledge base.
- A description of what a connected data source contains.
- Background that's already stated in the Project *description*.
- Anything Claude would find by reading the Project's attached files.

Recommend cutting those. Keep -- explicitly, and say so, so the user
doesn't over-trim: standing constraints ("never quote a price without
checking the rate card"), tone and format preferences, definitions of
internal jargon, and anything that contradicts a sensible default. The
distinction is *derivable* versus *not derivable*, not *long* versus
*short*.

### Length, in context of reach

`totals.always_loaded_char_count` is what every session on the account
pays. `worst_case_char_count` adds the largest Project text on top. Long
standing instructions are not automatically a problem -- but long
instructions that are mostly derivable content, or that reach thousands of
sessions, are worth flagging with the character count as evidence. Report
the number rather than an adjective.

Useful context when doing so: `system_prompt_char_count` and
`memory_guidelines_char_count` on each session are the scaffolding that is
already loaded before any of the user's own text is added -- on a real
session, roughly 49,000 and 13,500 characters respectively. **The user
cannot edit either**, so neither is ever a finding on its own. Quote them
only to put the user's own numbers in proportion: a 400-character standing
instruction is not what is filling the context window, and saying so plainly
is more honest than implying every character is worth agonizing over.

### Instructions nobody is using

A Space or Project with a substantial `char_count` and a `session_count` of
zero is text that was written and then abandoned. Worth surfacing as
cleanup, low severity -- it costs nothing per turn, but it's a sign the
Project itself may be stale (which feeds the reorganization lens).

### Memory

Memory is a **separate system from standing instructions**, and it is where
a lot of quiet drift accumulates. Since **2026-07-10** it is individual
categorized entries that Claude reads and updates during conversations --
not a summary regenerated on a daily cycle. Any advice along the lines of
"wait a day for memory to settle" is wrong; do not write it.

**Where to get it.** `get_instructions_inventory`'s `memory` block, on any
local run. It gives you, per owner -- the account, each Space, each cached
cloud Project -- a `label` you can say out loud, `file_count`,
`char_count`, `session_count` (how many sessions that owner actually
reached in the window), `stale_file_count`, last-modified dates, and
`duplicates_global`: the same overlap check the instruction layers get,
because memory that merely restates the standing instructions is paid for
twice. Block-level, it adds `repeated_across_memory`,
`unreadable_file_count`, and a `timezone`.

Each file also carries its `text`, capped at `max_memory_chars` (default
2,000; clamped, so a nonsense cap can't return more than intended rather
than less). Read it only for the checks that genuinely need the words.

**The names are already resolved for you.** An owner whose id can't be
matched comes back with a `label` describing what it is -- "a Space that is
no longer listed in spaces.json -- most likely deleted in the app while its
memory files were left behind on disk" -- never a bare uuid. Use `label`
verbatim; it is written for the user, and orphaned memory from a deleted
Space is a real finding (content still on disk for work that no longer
exists) that a uuid would render unreportable.

**Where the local files stop.** They are this device's copy, written by the
app as it runs. Memory the account holds that this machine never wrote is
not here, and neither is anything written since the app last ran here. An
export covers those. **No local memory files is not evidence of no
memory** -- a fresh install, a second machine, or memory switched off
everywhere all look identical from disk.

**Staleness is a filesystem date.** Memory files carry no timestamp of
their own, so `days_since_modified` comes from the file's last-modified
time -- when Claude last *wrote* it. Files past
`stale_after_days` (90) are counted in `stale_file_count`. That is a prompt
to check whether the content is still true, not evidence that it isn't; say
it that way.

**One caveat before you report `repeated_across_memory` as drift.** It
already filters out the shared template's furniture -- YAML front matter
and heading lines -- because without that filter it reports things like
`type: project` as "remembered under four different Spaces", which is a
finding about the file format, not the user. On the verification machine
the honest count after filtering was **zero**. Zero is a good result here;
don't go looking for a way to make it non-zero.

Checks:

- **Cross-topic pollution.** A project's memory carrying categories or
  facts that belong to unrelated work -- `repeated_across_memory` is the
  mechanical version, listing lines remembered under two or more different
  Spaces/Projects. Memory never crosses those boundaries on its own, so
  this means the owner itself is too broad, which feeds the reorganization
  lens.
- **Memory duplicating the standing instructions.** `duplicates_global` on
  each owner: lines the memory repeats verbatim from the current global
  custom instructions. Claude wrote down something the user already told it
  in a place that loads every turn. Recommend deleting the memory entry,
  not the instruction.
- **Memory left behind by a deleted Space.** An owner whose
  `name_resolved` is false and whose `resolution` is
  `space_id_not_in_spaces_json`. Report it by its `label`.
  **Check the resolution, not just the flag.** Since 0.6.2 an owner that
  *is* listed and simply carries no name resolves as
  `space_listed_without_a_name` / `project_listed_without_a_name`, and that
  is a different finding: the Space is sitting unnamed in the user's
  sidebar, not deleted. Recommending they recover orphaned memory from a
  Space that never went anywhere is a wrong answer, not a vague one. The
  fix to suggest there is "give this Space a name", not "this memory is
  orphaned".
- **Stale or contradicted entries.** Memory asserting something the recent
  conversations plainly contradict, or describing work that finished long
  ago.
- **Memory off on an account that clearly needs it.** Chats repeatedly
  re-establishing the same background is the signal.
- **Lopsided distribution.** One project holding many times the memory of
  every other usually means it has become the catch-all.

Two documented traps worth telling the user about outright, because both
surprise people:

- **Deleting a conversation does not delete the memory it produced.**
  Memory entries survive separately and must be deleted separately.
- **Incognito chats are still included in Team/Enterprise data exports.**
  Incognito is a memory control, not a privacy guarantee.

And the hygiene actions to recommend, in the user's own words: review and
edit entries under Settings, **pause** to keep what exists while stopping
new entries, **reset** to delete everything -- irreversible, and it takes
project memories with it.

Memory is the most sensitive material in the corpus -- it is Claude's
synthesized profile of the user. Evidence is a short quoted line and a
character count, never a pasted block.

### Project and Space quality

Every Space carries `name`/`description`/`instructions`/`folders`; every
cached cloud Project carries `name`/`description`/`prompt_template`. That
is enough to grade what already exists, not just to propose new ones.

**Report only concrete defects, and write the replacement text for each one
you report.** Stay silent on Projects that are already fine -- do not
produce a rewrite for every Project on the account.

The defects worth reporting:

- **No description**, on a Project with real activity.
- **No instructions** (`instructions`/`prompt_template` empty) on a Project
  whose chats clearly share standing context.
- **A name that doesn't describe the work** -- "Stuff", "Test", "New
  Project", or a name whose chats are plainly about something else.
- **Drift** -- the description says one thing and the last several months
  of chats in it say another.

For each, propose the actual replacement name/description/instructions
text, not a note saying one is needed.

### Access scope

Anthropic publishes specific guidance here, so this check quotes a
documented standard rather than inventing one
([Use Claude Cowork safely](https://support.claude.com/en/articles/13364135-use-claude-cowork-safely)):

> "Consider creating a dedicated working folder for Claude rather than
> granting broad access"
> "Be cautious about granting access to sensitive information like financial
> documents, credentials, or personal records"
> "Review what access you've granted, and consider whether that level of
> access is appropriate"
> "Watch for unexpected patterns: Is Claude accessing files or websites you
> didn't mention?"

**Start with the standing grants, not the per-session folder list.** This
is the ordering change 0.6.0 made, and it matters: `user_selected_folders`
only tells you what one chat happened to open. `get_desktop_settings`'s
`access_scope` tells you what Claude is *allowed* to open without asking
again, which is the thing the third quote above ("review what access you've
granted") is actually about.

| Setting | What it means in plain terms |
|---|---|
| `localAgentModeTrustedFolders` | "Always allow" was clicked on this folder. It stays allowed. |
| `remoteFolderConsentMemory` | A remembered consent decision for remote sessions. |
| `remoteSessionFolderGrants` | Folder grants recorded against one remote session, kept account-side. The counts are **siblings of** `by_session`, not fields inside it: `session_count`, `unique_path_count`, `broad_path_count`, `resolved_space_count`, `unresolved_space_count`, `space_lookup_available`. `by_session` itself is the list of per-session entries. |

Each grant comes back with `looks_broad` and a plain-language
`breadth_reasons` -- "your entire home folder", "a whole cloud-storage
account root", "an entire code directory". It is a **flag, not a score**,
deliberately: how much a broad grant matters depends entirely on what is
inside it, and neither the tool nor you have looked.

**Quote `access_scope.broad_grants`, and never add the per-setting
`broad_count`s together.** That block is the deduplicated view: one entry
per *distinct path*, with standing grants
(`standing_path_count`/`standing_paths` -- trusted folders plus remembered
consents) kept apart from session-scoped ones
(`session_scoped_path_count`/`session_scoped_paths` -- remote-session
grants, minus any that are also standing). Summing the sections instead
gets both halves wrong: a folder listed in two settings is **one exposure
counted twice**, and folding the session-scoped grants into the total calls
them "standing" when a session grant does not carry forward on its own.

Recounted on the verification machine on 2026-09-06:
`trusted_folders.broad_count` 2 + `remote_folder_consent.broad_count` 0 +
`remote_session_folder_grants.broad_path_count` 1 = a naive **3**, against
`broad_grants`' **2 standing and 0 session-scoped**. The one broad
remote-session path is a cloud-storage root that is *also* a standing
trusted folder -- the same exposure showing up twice -- so deduplication
folds it in rather than listing it again. The overstatement is small on
this machine, and saying that plainly is the point: the reason not to sum
the sections is that the sum answers a different question, not that it is
reliably dramatic. (An earlier revision of this passage quoted a naive 5
against a true 2 and called it a 2.5x overstatement. Nothing on the
verification machine produces a 5 -- that figure came from a synthetic
test fixture and was written up as a real observation, inside the very
passage arguing against overstated numbers. Recount before quoting any
figure here.)

**Every remote-session grant names its Space.** Each `by_session` entry
carries `space_label` -- the Space's real name where the id resolves, and a
plain-language explanation where it doesn't ("a Space that is no longer
listed in spaces.json -- most likely deleted in the app after this grant
was made"). Quote that. **Never print the raw `session_id`**: a
`session_01...` token names nothing the user has ever seen in the app, and
a finding that shows one has not answered "which Space is this?" All six
resolved on the verification machine.

**How to spot a stale or over-broad standing grant:**

- **Stale.** A trusted folder that appears in `access_scope` but in no
  session's `user_selected_folders` or `touched_dirs` for the whole period
  under review. Nothing has needed it and it is still granted. This is the
  finding that was impossible before -- a folder nothing touches leaves no
  trace anywhere else.
- **Over-broad.** `looks_broad` is true, and the sessions that *did* use
  that area only ever touched one subdirectory of it. The grant is a whole
  category; the work was one folder. Quote both: the granted path and the
  `touched_dirs` that were actually needed.
- **Accumulated.** Many single-purpose grants from one-off tasks, never
  cleaned up. Report the count and the pattern rather than listing thirty
  paths.
- **Not a finding on its own:** a broad grant that is obviously the user's
  actual working root, or a grant on a folder they clearly work in daily.
  Say nothing.

**Writing the finding for a non-technical reader.** Name the folder the way
they think of it, say what it lets Claude do, say what evidence you have,
and give them the exact place to change it. Not this:

> `localAgentModeTrustedFolders` contains
> `/Users/x/Library/CloudStorage/OneDrive-Personal` (looks_broad: true,
> depth_below_home: 2).

This:

> **Claude has standing access to your whole personal OneDrive.** That
> means every file synced to it, including anything added later, without
> being asked again. Over the last 90 days the only thing any chat actually
> opened in there was the "Client Proposals" folder. If that's all you need
> it for, you can remove the top-level access and grant that one folder
> instead, in Claude's settings, in the list of folders you've allowed it
> to open.

No paths in tool-key form, no `snake_case` field names, no depth numbers,
and **no invented menu path** -- describe the setting so they can recognise
it, rather than naming a screen this plugin has never seen. The same rule
that governs the toggles in area 6.
And keep the tone matter-of-fact -- a broad grant is very often deliberate,
and the finding is "confirm this is what you intended," never "you have
done something wrong."

Then the per-session checks, in descending order of confidence:

1. **Review what's granted.** Present, in plain language, what each Space
   and session can reach: `user_selected_folders`, plus
   `egress_allowed_domains` and `web_fetch_allowed_url_hosts` for network
   reach. This is presentation, not judgment, and it directly implements
   the third quote above. An `egress_allowed_domains` of `["*"]` means
   unrestricted outbound reach and is worth stating plainly.
2. **Broad grants.** A Space or session bound to a home directory, a drive
   root, or an entire Documents tree rather than a dedicated working
   folder. High confidence, quotes the first line above directly. Check
   this against the standing grants above rather than reporting the same
   folder twice in two different voices.
3. **Scope creep.** Cross `touched_dirs` and `url_hosts` (what the session
   actually reached) against `user_selected_folders` and the stated task in
   `initial_message`. Directories touched well outside the granted working
   folder, or hosts unrelated to anything asked for, are what the fourth
   quote is describing. Note that `touched_dirs` is parent directories
   only, and `url_hosts` is hosts only -- deliberately, so this check never
   needs file contents or full URLs.
4. **Sensitive-looking folder names.** Granted folders whose names suggest
   credentials, financial documents, tax records, medical records.
   **This one is a name heuristic and nothing more** -- it will sometimes
   flag something harmless and sometimes miss something real. Word it as
   "worth a look", never as an assertion, and never imply the folder's
   contents were examined, because they were not.

This is the one area of this skill that shades from effectiveness into
security. Keep the tone matter-of-fact: a broad grant is very often
deliberate, and the finding is "confirm this is what you intended," not
"you have done something wrong."

### Capability inventory -- installed vs. actually used

Cross what was available against what was used -- across **three** levels,
not two:

| Level | Where from | What it proves |
|---|---|---|
| **Installed** | `get_desktop_settings`'s `extensions` | It is on this machine. Nothing more. |
| **Enabled** | `enabled_mcp_server_labels`, `enabled_mcp_tool_names`, `remote_mcp_server_names`, `plugin_names`, `slash_command_names` | Some session switched it on. |
| **Invoked** | `mcp_servers_invoked`, `tools_invoked` | It actually ran. |

**`extensions` is the authoritative answer to "installed," and per-session
data is not.** This is the correction 0.6.0 made, and it is the difference
between this check working and quietly not working. An extension that was
installed and then never enabled in *any* session appears in no session
file at all -- not as a false value, but as an absence -- so the old
two-column version could only ever find things that had been switched on at
least once. The installed-but-never-used case, which is the whole point of
the check and the most common finding on a mature account, was invisible.
If a previous run reported no unused capabilities, that is why.

Each installed extension carries a version, a `source`, a
`signature_status`, and an install date. The install date is what makes the
finding land: "installed in June, never once enabled" is a sentence the
user can act on, where "unused" alone is not.

**`signature_status` is not a security signal, and must never be shown to
the user.** `unsigned` is the only value ever observed on a real install,
and the extension reporting it is **Anthropic's own first-party
chrome-control extension** -- so unsigned is the normal case, not a
warning. This plugin cannot verify any extension's provenance, so it has no
basis for a claim either way. Telling a non-technical user their extension
is unsigned invites exactly one conclusion -- "mine is unsafe" -- and that
conclusion would be unsupported. The field is kept as a drift signal for
maintainers, so someone notices if it ever starts carrying real values.
`get_desktop_settings` emits a note saying this whenever any extension
reports it; don't turn the note into a finding either.

Where the harness exposes its own `list_skills`/`list_plugins`/
`list_connectors`, use those too -- see
[data-sources.md section 7](data-sources.md#7-chaining-with-native-harness-tools-instead-of-duplicating-them).

**Do not expect the two sides to join on a shared identifier.**
`enabled_mcp_tool_names` keys are display labels (`Control Chrome`) while
invoked tool names are slugs (`mcp__claude-in-chrome__navigate`, which
yields the server `claude-in-chrome`). Match them by eye and say so if a
match is uncertain -- do not manufacture a precise-looking join.

Three findings fall out:

- **Installed but never invoked**, across many sessions -- clutter. The
  common case on a mature account. Low severity, easy win. Distinguish the
  two versions when you report it: *installed and never even enabled* (it
  has done nothing since the day it arrived) is a stronger, simpler finding
  than *enabled but never called* (something switched it on and it still
  never ran).
- **Invoked constantly** -- worth making permanent at the Space/Project
  level rather than being re-enabled ad hoc.
- **Repeatedly needed but never available** -- the capability gap. This one
  hands off to [usage-efficiency.md](usage-efficiency.md)'s "Recommending
  skills, plugins & connectors" section, which decides *already available*
  versus *worth building custom*. Report the gap here or the
  recommendation there, not both.

One boundary that has not moved: **there is no callable tool for raw MCP
connector auth status.** Whether a connected connector is actually
authenticated right now is not answerable -- don't infer it from
`connected: true/false`.

### Settings hygiene

Two sources, at two levels. First the per-session toggles from
`list_local_workspace` (this repeats what
[usage-efficiency.md](usage-efficiency.md) already says about
environment settings -- report it in whichever lens fits your narrative, not
in both):

- `memory_enabled` off in a Space whose chats clearly re-establish the same
  context repeatedly.
- `skills_enabled` or `plugins_enabled` off where the chat content shows
  work a skill/plugin would have handled.
- No global custom instructions at all (`variant_count` is 0) on an account
  with substantial usage -- often the single highest-leverage fix
  available.

These are per-session values, so check whether they actually vary across a
Project's sessions before reporting one verdict for the whole thing.

#### The app-wide toggles

`get_desktop_settings`'s `settings` block carries the settings that are not
per-session at all -- set once, account-wide, and therefore invisible to
everything above. They come back **keyed by their on-disk names**, which is
right for a response and wrong for a sentence.

**Never put an on-disk key in front of the user.** `keepAwakeEnabled` is a
string from a JSON file. It appears nowhere in the Claude Desktop
interface, it is not searchable there, and a finding that names it has told
a non-technical user to go find something that, as far as they can see,
does not exist. Use the middle column below. Keep the key for your own
cross-referencing and for the maintainer reading the raw response.

**On where to change it: this plugin does not know, and neither should you
pretend to.** These settings are read from a config file. Nothing on disk
records where a setting appears in the app's UI, and no published Anthropic
doc enumerates them, so **no per-setting menu path here is established**. A
confidently wrong menu path is worse than none: it sends the user looking
for a screen that isn't there, and it costs the report the credibility of
every other finding in it. Say instead "in Claude's
settings" and describe the switch in the words below, so they can recognise
it when they see it -- or say plainly that you're not sure where it lives
and ask them to search Settings for it. If you are working in a session
that can actually see the app's current UI, check there before naming a
location; don't infer one from the key name.

| On-disk key (for you) | Call it this (to the user) | Worth a look when |
|---|---|---|
| `keepAwakeEnabled` | "Keep this computer awake so Claude can keep working" | See below -- the likeliest obsolete setting on the list. |
| `allowAllBrowserActions` | "Let Claude take any action in your browser without asking" | On, standing, account-wide. It is the browser equivalent of Auto approval and deserves the same "confirm this is what you intended" framing as a broad folder grant. |
| `dispatchCodeTasksPermissionMode` | "How much Claude checks with you before running a coding task" | Standing permission posture. Report what it is (the observed value is a mode name like `auto`); only flag it against evidence, never on the name alone. |
| `bypassPermissionsGateByAccount` | "Skip the permission prompt entirely" (recorded per account on this machine) | Same. This one is a per-account map; `any_enabled` is the field to read, and the account uuids are replaced with opaque labels before you ever see them. |
| `coworkScheduledTasksEnabled` | "Let Claude run tasks on a schedule" | Off on an account where the usage lens found a good automation candidate -- the recommendation needs the switch on first. |
| `ccdScheduledTasksEnabled` | "Scheduled tasks for the developer/Code surface" -- **hedge this one**: the `ccd` prefix is unexplained anywhere on disk, so the mapping is inferred from the name | Rarely worth raising with a business user at all. If you do, say what you're unsure about. |
| `coworkWebSearchEnabled` | "Let Claude search the web" | Off while chats repeatedly ask for current information. |
| `coworkBrowserToolsEnabled` | "Let Claude use your browser" | Context for a browser-control extension that is installed but never invoked (area 5). |
| `coworkPreferredBrowser` | "Which browser Claude uses" (observed value: a browser name) | Same. Context only, never a finding on its own. |
| `coworkModelAutoFallbackByAccount` | "Automatically switch to a smaller model when the main one is unavailable" | On, when the usage lens is reasoning about model tiers -- fallback means the model that ran isn't always the one selected, which weakens a model-mismatch finding. Worth knowing before you write one. |
| `coworkHipaaRestricted` | "HIPAA restrictions on this account" | Organization policy, not user error. Report as context. **Never recommend turning it off.** |
| `orgWorkAcrossAppsDisabled` | "Your organization has turned off working across apps" | Same -- policy, not a setting the user chose. **Never recommend turning it off.** |
| `sidebarMode` | "Which view Claude opens in" (observed value: `chat`) | Presentation only. Not a finding. |

These plain-language names are written from the on-disk key names and the
values observed on one machine, not from any published documentation of the
settings -- Anthropic documents none of them. They are written to be
*recognisable* when the user looks, which is a lower bar than authoritative
and the right one here. Where a name's meaning is genuinely uncertain, the
row says so; carry that uncertainty into the finding rather than dropping it.

Two settings are named as folder-access controls rather than toggles and
appear under `access_scope`, not here: `localAgentModeTrustedFolders`
("folders you told Claude it can always open") and
`remoteFolderConsentMemory` ("folder permissions Claude remembered from a
previous session"). Area 4 covers how to word those.

`unrecognized_settings` is **not** a source of findings. It exists so
maintainers notice when Desktop ships a new preference this plugin doesn't
know; a key appearing there says nothing about the user. Don't mention it
in the dashboard.

#### `keepAwakeEnabled` -- likely obsolete, and worth saying so

This is the one setting here worth proactively raising. It keeps the
machine awake so Desktop can keep running -- which mattered when scheduled
tasks needed the app open. **They don't any more.** Since 2026-07-07
scheduled tasks run on Anthropic's servers, on their own cadence, "even
when your computer is asleep or the Claude Desktop app is closed" (see
[data-sources.md](data-sources.md#two-boundaries-this-doc-got-wrong----and-the-failure-mode-behind-both));
0.5.0 already corrected this plugin's guidance, and this check is that
correction applied to the setting itself.

So: `keepAwakeEnabled` on is very likely a leftover from that older model,
and its cost is real and physical -- a laptop that doesn't sleep, on
battery, for a reason that expired.

Two honest caveats, both of which belong in the finding:

- **The exception survives.** A scheduled task that needs local files or
  local apps still runs locally, and still needs the machine awake. If the
  user has tasks like that, this setting is doing its job.
- **This plugin can only half-check.** `get_desktop_settings`'s
  `scheduled_tasks` says what *this device* records -- and on the
  verification machine that was an empty list. That is not enough to clear
  the setting: the tasks run server-side, the file records nothing about
  whether a task needs local files, and an empty list may equally mean
  "none scheduled" or "not recorded here". (The claim that scheduled tasks
  are wholly unreadable from disk was itself false -- see
  [data-sources.md](data-sources.md#two-boundaries-this-doc-got-wrong----and-the-failure-mode-behind-both).)
  So this stays worded as a question, not a correction: "Claude is set to
  keep this computer awake. Scheduled tasks now run on Anthropic's servers
  whether or not your computer is on, so this is usually no longer needed
  -- unless you have a scheduled task that works with files on this
  machine. Worth checking your scheduled tasks in Claude." Never assert
  that it is safe to turn off.

#### What this device records about scheduled tasks

`get_desktop_settings`'s `scheduled_tasks` block reads a real file
(`scheduled-tasks.json`). It belongs beside the two scheduled-task toggles,
because they answer different halves of one question: the toggle says the
feature is switched on, the file says whether anything is actually
scheduled here.

Three rules, and they are the whole of what can honestly be said:

- **An empty list means "this device records no scheduled tasks."** Not
  that the feature is absent -- the file and its schema are right there --
  and not that the account has none, because the work runs server-side and
  this may simply not be where it is recorded. Word it that way or don't
  raise it.
- **`task_entry_shape_verified` is `False`.** A populated array has never
  been observed on a real install, so entries come back verbatim and no
  field inside one is named or promised. Read them; never parse a field out
  of one, and never build a count or a claim on one.
- **Cross it with the toggles before saying anything about whether the
  user schedules work.** "Scheduled tasks are switched on and this device
  records none" is an honest sentence. "You don't use scheduled tasks" is
  not one this data supports.

This block is also the answer to why `keepAwakeEnabled` can't be cleared
outright -- see the section above it.

## Step 3: Report, never change

`/doctor` reports findings first and asks before changing anything, and it
has direct write access to the files it's talking about. This plugin has
less latitude, not more: it is read-only by construction, and the text in
question is the user's own writing about their own business. Propose edits
as findings with the specific lines quoted; the user makes the change in
the app. There is no "apply" step here and shouldn't be one.

## Feeding the plan

Add `findings` entries in the shape
[usage-efficiency.md](usage-efficiency.md) already documents:
`{"title", "severity", "evidence": [...], "recommendation"}`.

For this lens specifically:

- **Evidence** should be the Project/Space name plus the actual duplicated
  or derivable line, quoted short. A character count is evidence too. Don't
  paste whole instruction blocks into the dashboard.
- **Severity** -- `warning` for duplication and derivable content that's
  actively costing tokens on every turn; `info` for abandoned instructions
  and stylistic cleanup. Reserve `critical` for something genuinely wrong,
  like instructions that contradict each other across layers.
- **Recommendation** should name the exact place to make the change
  (which Project's instructions, or the global ones) and what to do there.

## Don't overwhelm -- rank before you present

This lens can now produce a lot. The rule is **analyze everything, present
with restraint**: run every check above, then put the handful that actually
matter into the plan's `start_here` list, which renders at the top of the
dashboard ahead of every detailed section.

- Aim for about **five** entries, ordered most important first.
- Each one is a pointer to a finding detailed further down, not a new
  finding that appears nowhere else.
- Rank by impact, not by severity label. Several of the cheapest wins here
  -- an absent Project description, a connector installed but never used --
  are naturally `info` severity and still belong at the top.
- Each entry carries `title`, `action`, and where possible `where` (the
  exact place to make the change) and `impact` (one short phrase on why).

Nothing gets dropped to make room. The full findings list stays complete
below; `start_here` only decides what the reader meets first.
