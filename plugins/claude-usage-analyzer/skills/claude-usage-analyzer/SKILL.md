---
name: claude-usage-analyzer
description: >-
  Read-only analysis of how the user actually uses Claude -- local Claude
  Desktop/Cowork data on this machine, plus an optional claude.ai account
  data export -- rendered as one HTML dashboard with three lenses: a
  workspace reorganization proposal, a usage & best-practices review
  grounded in real plan consumption, and a setup & effectiveness checkup
  covering standing instructions, memory, access scope and
  installed-versus-actually-used capabilities. Built for business users of
  the Claude Desktop app -- Claude Code CLI sessions are deliberately
  excluded, since Claude Code ships its own /doctor, /usage and /context for
  that. Every history tool takes an optional since/until window; settings
  are current state and take none. Analysis and dashboard generation only --
  it never moves, renames or deletes a chat or Project.
when_to_use: >-
  Trigger on "analyze my Claude usage/chats/projects", "organize my Claude
  workspace", "audit/review my Claude usage", "reorganize my chats", "find
  patterns in my conversations", "how am I using Claude", "am I using the
  right models", "how can I optimize my Claude usage", "am I hitting my usage
  limits", "why do I keep running out of usage", "how much of my plan am I
  using", "review my custom instructions", "review my Claude memory", "is my
  Claude set up correctly", "what skills should I build", "which connectors
  am I actually using", "what folders can Claude see", "what does Claude have
  access to", or when handed a claude.ai data export folder.
compatibility: "Claude Desktop and Cowork; requires uv on PATH"
license: MIT
---

# Claude Usage Analyzer

**Skill version: 0.6.2** (matches `.claude-plugin/plugin.json`). Bump this
line, in lockstep with that file's `version`, any time this skill's
content changes -- it's the quickest way to confirm an installed copy
has picked up the latest instructions rather than a stale cached one.

## Who this is for

**Business users of the Claude Desktop app.** That framing decides real
things, not just tone:

- **Claude Code CLI data is out of scope and never read** -- not "not
  yet." See
  [data-sources.md section 0](references/data-sources.md#0-what-is-deliberately-out-of-scope-claude-code)
  for why, including which native commands (`/doctor`, `/usage`,
  `/context`) already cover that surface better. If the user asking is
  themselves a developer who wants their CLI usage analyzed, point them at
  those rather than stretching this skill.
- **Write for someone who has never opened a terminal.** No working
  directories, no git branches, no token math presented as the headline.
  Model names are fine; explain the tiering in plain terms when it matters
  to a recommendation.

## What this is

A read-only analysis of the user's own Claude usage, combining up to two
sources of data, ending in a single reviewable deliverable: a **live HTML
dashboard** covering three independent lenses on the same data:

1. A **workspace reorganization** proposal (Projects to create, which
   existing chats move into each, each Project's name/description/custom
   instructions, and what file/folder structure it needs).
2. A **usage & best-practices review**: how many Projects/chats exist and
   how they break down, which models got used where and whether any chat
   used a more expensive/complex model than the task actually needed,
   prompting/context patterns worth a look, and evidence-based candidates
   for automation (a recurring manual workflow that could become a Skill,
   slash command, or scheduled task) -- explicitly split into an existing
   public skill/plugin/connector the user should just install/connect,
   versus a custom one worth building from scratch when nothing existing
   fits.
3. A **setup & effectiveness checkup**: is the workspace itself configured
   well, or is it quietly costing the user on every turn? Six areas --
   standing instructions (global / Space / Project, audited for
   duplication and derivable content), **memory** (what Claude has
   actually remembered -- read straight off this machine, no export
   required -- and whether it has gone stale or cross-topic),
   **Project and Space quality** (does each have a name, description and
   instructions that match what it's for), **access scope** (which folders
   and hosts each session can reach), **capability inventory** (what's
   installed and enabled versus what's actually used), and settings
   hygiene. This is the Desktop/Cowork counterpart of what Claude Code's
   `/doctor` does to `CLAUDE.md`, widened to the rest of the setup.

This version does analysis and dashboard generation **only**. It
deliberately does not (yet):

- **Execute** the plan -- actually creating/renaming Projects or moving
  chats in the claude.ai UI. That needs its own browser-driven build and is
  future work; for now, hand the reviewed plan over as a checklist.
- **Full automation mining** -- a dedicated program that clusters every
  recurring workflow across the account and builds out Skill/scheduled-task
  recommendations in depth. The usage & best-practices lens above does
  flag automation *candidates* it notices as a side effect of the same
  analysis, with evidence, but it doesn't go looking for them exhaustively
  or design the automation itself -- that deeper pass is still future work.

Say so plainly if the user asks for either -- don't half-build them.

## Step 1: Check data access

Call `check_data_access` first, every session. It reports which local data
locations (Claude Desktop's app-data directory, the user-visible `~/Claude`
output folder) are visible right now, per-platform.

This is a *visibility* check, not a health check -- don't present it to the
user as the Desktop equivalent of `/doctor`. The actual config review is
Step 6.

**If `check_data_access` finds nothing:** this is not proof the user has no
Claude history. Read `references/data-sources.md` before saying anything
further -- the short version: this MCP server only sees what the current
session can see. If the session is inside a folder-scoped Cowork Space or
Project, those folders have to be added to that Space/Project's file access
scope explicitly; the plugin has no broader access on its own. Tell the
user exactly which folder(s) the `desktop_candidates` field lists for their
platform, and that they need to add them via the Space/Project's
file/folder settings, then restart the session.

Don't proceed to a full analysis on a guessed-at inventory -- get real
access (or an explicit "local data isn't available, export-only" decision)
first.

## Step 2: Decide the scope -- time window, then data sources

### 2a. Time window

**Ask before pulling anything.** Every tool that reads history --
`list_local_workspace`, `get_project_membership`,
`get_instructions_inventory`, `get_plan_usage_history`, `parse_export` --
takes the same `since`/`until` pair, and the answer changes what every
number in the dashboard means. Two choices to put to the user:

- **The full flow** -- all available history. The right default for a first
  run, or for "how am I using Claude" in general.
- **A time window** -- e.g. the last quarter, since a workspace
  reorganization, or a specific date range. The right choice for "what
  changed since," for a recurring review, and for any account big enough
  that all-time averages hide the current picture.

Pass the same window to every tool in the run. Mixing an all-time chat
count with a windowed model breakdown produces a dashboard that quietly
contradicts itself.

**Two things a window does not scope, and both need saying out loud.**
`get_desktop_settings` takes no `since`/`until` at all -- settings are
current state, and nothing on disk records when a toggle was flipped or a
folder trusted. And `get_instructions_inventory`'s `memory` block is
current state too: a memory file has one last-modified date, not a history,
so only each owner's `session_count` is windowed. On a scoped run, say
plainly that those parts describe the account *as it is today* while the
rest of the report covers the chosen period -- otherwise the dashboard's
date range implies a precision they don't have.

Both bounds accept a relative age (`"90d"`, `"6m"`), an ISO date
(`"2026-01-01"`), or epoch milliseconds. **Prefer the relative form** --
it's resolved server-side against the real clock, so it can't be thrown off
by an uncertain sense of today's date. Sessions and conversations are
matched on *overlap*, not creation date: a chat begun in January and still
being worked in March is in a March window.

**A bare date is anchored at midnight UTC**, while every date the user
reads is local -- so off UTC, the window edge lands part-way through a
local day. The response's `time_window.boundary_notes` says so and gives
the exact bound to pass instead (`"2026-01-01T00:00:00-05:00"`), and
`since_local`/`until_local` show where the bounds actually fell on the
user's clock. Pass an explicit offset when the window is meant to line up
with their calendar, and never state a window bound as a bare date with no
zone attached.

Every response carries a `time_window` block -- the resolved bounds, how
many records were excluded, and any errors. A bad bound widens the read and
reports the problem rather than returning an empty result, so check
`time_window.errors` before believing a suspiciously small result. Carry
the resolved window into the dashboard's `time_window` field at Step 7, and
state it in plain language when presenting: a windowed count offered as an
account total is simply wrong.

### 2b. Data sources

Two independent sources, not mutually exclusive:

1. **Local data** (`list_local_workspace`) -- whatever `check_data_access`
   found readable. Always available if Step 1 succeeded; no user action
   needed beyond the folder-scope fix above.
2. **A claude.ai account export** (`conversations.json` etc.) -- covers
   claude.ai web chats, which local data never sees at all (see
   references/data-sources.md section 3 for exactly how the two overlap and
   where each undercounts). Ask the user:
   - If they already have an unzipped export folder, get its path and skip
     to Step 3.
   - Otherwise, offer to try requesting it via browser automation -- see
     references/export-acquisition.md. This is opt-in and asked explicitly
     every time (no carried-over approval from an earlier session). The
     zip arrives by email on claude.ai's own schedule, not instantly --
     the account settings page itself never shows a ready/pending status,
     only email does. The same reference doc covers discovering the
     claude.ai account's own email address and checking whether an
     already-connected mailbox (Gmail, Outlook/Microsoft 365, etc.) is
     that same account; if so, it covers offering to search for and act on
     the export-ready message automatically (opt-in, with an optional
     scheduled recheck), instead of just waiting on the user to notice it.
     If no browser-automation tool is available, no mailbox match is
     confirmed, or the user declines any piece of it, either proceed
     local-data-only, or pause and ask the user directly for the download
     link from the export-ready email (or the unzipped folder path once
     they've downloaded it themselves). Most client accounts already have
     a mail connector enabled for this, so expect the automated email
     check to be available -- and throughout this whole exchange, narrate
     in plain, non-technical language (see references/export-acquisition.md's
     "Plain-language narration" section): never leave a "want me to check?"
     unanswered about what's being checked, and always state outright
     whether email access is available, rather than going quiet on it.

Say plainly which source(s) ended up in play before presenting any analysis
-- local-only, export-only, or both -- and, alongside it, the time window
from 2a. Both change how complete the picture is.

## Step 3: Gather the data

Pass the Step 2a window (`since`/`until`) to every call below -- the same
values each time.

- Call `list_local_workspace` if local data is in play. On a large account,
  don't always pull the full unfiltered dump -- its `session_ids`/
  `project_uuid`/`folder_path`/`fields` params scope it down, and if all
  that's needed is which Project/Space a set of sessions belongs to (e.g.
  after calling the harness's own `session_info.list_sessions()`, whose
  session ids are already in this same `local_<uuid>` format), call the
  cheaper `get_project_membership(session_ids)` instead -- see
  [references/data-sources.md](references/data-sources.md) section 7 for
  the full chaining recipe, what's confirmed available vs. not, and which
  questions belong to a native command rather than this plugin.
- Call `parse_export(export_dir)` if an export is in play. Check the
  returned `stats.notes` first: a thin corpus (under 10 conversations) or a
  missing per-conversation project link changes how much confidence to put
  in the analysis, and both need to be said plainly before presenting
  output, not silently absorbed. On a windowed run, "thin" may just mean
  the window is narrow -- `stats.notes` says which, so read it rather than
  concluding the account is barely used.

After gathering, sanity-check every response's `time_window`: the same
resolved bounds across all of them, no `errors`, and an `excluded` count
that matches what you'd expect. This is the cheapest place to catch a scope
mistake -- after Step 7 it's baked into a dashboard the user reads as fact.

**Every date and weekday you show the user is a local one.** Each instant
comes back five ways: `<x>_iso` in UTC for comparing and joining, and
`<x>_local_iso` / `<x>_local_date` / `<x>_local_weekday` / `<x>_utc_offset`
on this machine's clock. Build every sentence from the local fields;
`check_data_access`, `list_local_workspace`, `get_instructions_inventory`
and `get_desktop_settings` each carry a `timezone` block naming the zone.
A weekday computed in UTC is a wrong answer for someone reading their own
calendar, not a caveat on a right one -- on the verification machine the
account's first launch is Sunday in UTC and **Saturday** locally. The one
exception is the `time_window` bounds themselves, which stay UTC and are
labelled UTC.

## Step 4: Analyze (reorganization lens)

See [references/reorganization.md](references/reorganization.md) for the
full method: reading chat -> Project/Space membership, spotting topic
clusters, and separating stale one-offs from active work worth structuring.

**Claude Desktop records which Space each chat is in, and the tools read
that record.** Membership is not reconstructed: a session's `spaceId`
resolves through `spaces.json`, and only a chat the app recorded no Space
for falls back to matching its folder against Space folders. Every session
row -- in `list_local_workspace` and `get_project_membership` alike, from
the same shared map -- says which happened:

- `space_source: "app_record"` -- the user's own filing. A fact.
- `space_source: "inferred_from_folder"` -- **this plugin's guess.**
  `space_matched_folder` says what it matched.
- `space_source: "none"` -- not in any Space.

**Never present a guess as the user's filing.** Say "Claude has this filed
under X" only for `app_record`; for an inferred row, say it was matched
from the folder the chat was working in. That distinction decides how much
confidence a reorganization proposal deserves, and where a finding leans on
inferred rows, put it in the plan's `notes`. Quote `space_label` -- always
readable prose, never a bare uuid -- rather than `space_id`.

This matters because it was wrong until 0.6.2: the old folder-prefix
reconstruction disagreed with the app on every chat that had a record,
collapsing 36 chats filed under a nested Space into the broad
cloud-storage Space above it and reporting two real Spaces as empty. An
empty Space, or a large unfiled count, is exactly the kind of finding to
check against `space_source` before reporting it.

For each proposed Project, work out all of: **name**, **description**,
**custom instructions** (the Project-level prompt/instructions text), which
**existing chats** move into it, and what **file/folder structure**
(reference docs, knowledge files) it needs and why. This is the actual
analytical work -- deciding "these conversations are one real project"
takes understanding the content, not just clustering keywords; the tools
above hand you structured data, not conclusions.

## Step 5: Analyze (usage & best-practices lens)

See [references/usage-efficiency.md](references/usage-efficiency.md) for
the full method: right-sizing **model** choice against task complexity,
right-sizing the **effort** dial (low/medium/high/xhigh/max -- the largest
under-examined cost/quality lever), **approval mode** (`permission_mode` --
Auto is reported to cost more than Manual or Skip, but that is received
guidance nothing local can verify, so never let it carry a finding alone),
**surface choice** (Cowork
costs significantly more than chat; simple questions belong in chat),
context/prompting patterns worth a look, and flagging (with evidence, not
a deep build-out) recurring manual workflows that look like automation
candidates.

**Lead with real consumption, not proxies.** `get_plan_usage_history` is
the only ground truth this skill has about what the user's plan is actually
being spent on: it returns the sampled five-hour and seven-day usage
windows Desktop records, plus any extra usage. Call it whenever this lens
runs. Everything else here -- model tier, the effort dial, approval mode,
Cowork-versus-chat surface choice -- is a *proxy* for cost, inferred from
how a session was configured rather than measured. So: open with what the
real consumption history shows (how close the user gets to the limit, when,
and how often), then use the proxies to explain *why* and what to change.
**There are two limits and both get reported.** `get_plan_usage_history`
analyses the five-hour window and the seven-day window in the same shape,
under `limit_events.five_hour` and `limit_events.seven_day`. Never report
one without the other: for a business user the weekly cap is usually the
one that matters more, because running out of the five-hour window costs
the rest of an afternoon while running out of the weekly one can cost the
rest of the week.

**Its dates follow the same local-time convention as everything else**
(Step 3): quote the `*_local_date` / `*_local_weekday` values, not the UTC
ones. It matters most here -- on the verification machine UTC bucketing
moved the weekly usage peak from Thursday to Friday, which is a wrong
answer to "when do I run out?", not a caveated one.

If the history is unavailable or too thin to read (a new install, a short
window), say so and fall back to the proxies. Either way, state which one
a finding rests on -- "you hit the five-hour limit on nine days last month"
and "these chats used a heavier model than the task needed" are different
kinds of claim, and the user should never have to guess which they're
being shown.

Two corrections worth carrying into any advice you give here, because
older guidance gets both wrong:

- **Don't tell the user to start a fresh chat to avoid a context limit.**
  Long conversations auto-compact and that compaction doesn't consume usage
  tokens. Start a fresh chat for *topic separation*, which keeps memory and
  search clean -- not because of length.
- **Scheduled tasks run remotely.** They no longer need the machine awake
  with Desktop open, except when a task needs local files or apps.

Model data isn't uniform across sources -- Cowork/Chat local sessions carry
a per-message `models_used` tally (or a `default_model`/`effort` fallback);
the claude.ai account export never carries model data at all (confirmed,
not just an occasional gap). See
[references/data-sources.md](references/data-sources.md) section 4 before
reporting an empty model-usage result, so it's clear whether nothing was
found or the source simply can't say.

When an automation candidate or connector-friction pattern turns up,
don't default to "build a custom Skill" -- check whether an existing
public Skill, plugin, or connector already covers it first, and label the
result clearly as one of two kinds: *already available* (install/connect
something that exists) vs. *worth building custom* (nothing existing
fits). See usage-efficiency.md's "Recommending skills, plugins &
connectors" section for how.

This lens works from the same compact per-session/per-conversation
summaries as Step 4 -- `models_used`/`default_model`, `first_human_message`,
`keywords`, `tool_names`, `message_count` -- not full transcripts, so it
stays cheap even on a large account. Only open a specific underlying file
directly when a summary is genuinely ambiguous and it changes a finding
you're about to report.

## Step 6: Analyze (setup & effectiveness checkup lens)

Call `get_instructions_inventory()` **and** `get_desktop_settings()`, then
see [references/workspace-checkup.md](references/workspace-checkup.md) for
the full method. Six areas:

1. **Standing instructions** -- the three always-loaded layers (global
   custom instructions, each Space's `instructions`, each cloud Project's
   `prompt_template`): lines duplicated between a Project and the global
   text, the same text pasted into several Projects, and instructions
   describing things Claude can already see for itself. That last one is
   `/doctor`'s most valuable check and needs real reading rather than a
   count -- keep standing constraints, tone, jargon definitions and
   anything contradicting a sensible default; cut what's derivable.
2. **Memory** -- what Claude has actually remembered. **This is on disk
   and `get_instructions_inventory` returns it**, in its `memory` block:
   account-level memory, per-Space memory, and each cached cloud Project's
   memory, each named through the same join the instruction layers use,
   with character counts, last-modified dates and the same
   duplicates-against-global check. A plain local run answers "review my
   Claude memory" with no export at all. An export's `memories.json` is
   the complement, not the prerequisite: it comes from the account rather
   than this device, so where the two disagree **the export wins** -- say
   which source a finding came from. Checks: stale or contradicted
   entries, cross-topic pollution (`repeated_across_memory`), duplication
   against standing instructions, and whether memory is on at all. Two
   traps worth telling the user outright: deleting a conversation does
   *not* delete the memory it produced, and incognito chats still appear
   in Team/Enterprise exports.
3. **Project and Space quality** -- grade existing ones on name,
   description and instructions. Report only concrete defects, and write
   the replacement text for each; stay silent on the ones already fine.
4. **Access scope** -- what each Space/session can reach
   (`user_selected_folders`, `egress_allowed_domains`,
   `web_fetch_allowed_url_hosts`), broad grants, and scope creep
   (`touched_dirs`/`url_hosts` versus what was granted and asked for).
   Anthropic publishes this guidance; the reference doc quotes it directly.
   Quote `access_scope.broad_grants` for the headline figure -- it is
   deduplicated across settings and keeps standing grants apart from
   session-scoped ones -- and never re-add the per-setting `broad_count`s
   yourself. Each remote-session grant now names the **Space** it belongs
   to (`space_label`); never show the user a raw `session_01...` id.
5. **Capability inventory** -- what's installed and enabled
   (`enabled_mcp_server_labels`, `plugin_names`, `remote_mcp_server_names`)
   versus what was actually invoked (`tools_invoked`,
   `mcp_servers_invoked`). Installed-but-never-used, used-constantly, and
   needed-but-missing.
6. **Settings hygiene** -- the per-session `memory_enabled` /
   `skills_enabled` / `plugins_enabled` toggles, plus the app-wide
   preferences `get_desktop_settings` reports. Name every one of those in
   plain language, never by its on-disk key: `keepAwakeEnabled` is a
   string the user cannot find anywhere in the app. The reference doc
   carries the wording to use, and says where the in-app location is
   genuinely unknown rather than inventing a menu path.
   `get_desktop_settings` also reports `scheduled_tasks`, read from a real
   file on disk -- an empty list means "this device records none", which
   is not the same as "the feature doesn't exist" or "the account has
   none".

**Areas 4, 5 and 6 need `get_desktop_settings`.** Until now those three saw
only *per-session* data, which meant they could only describe folders,
capabilities and toggles that some session happened to use. Anything
standing at the account level was invisible: a folder the user granted once
and left granted, or an extension installed and then never enabled in any
session, simply never appeared. `get_desktop_settings` supplies that
account-level layer -- the standing folder grants and consent memory
(area 4), the installed extension inventory (area 5), and the app-wide
preferences (area 6). Read the two together: the per-session data says what
was *used*, the settings say what is *available*, and the gap between them
is most of what's worth reporting here -- a broad standing folder grant
nothing ever needed, an extension installed and then never switched on.

Report these as `findings`; propose the edits, never make them. An honest
"the workspace looks fine" is a good outcome here -- don't manufacture
findings to fill the section.

**Then rank.** This lens can produce a lot, and the goal is a dashboard the
user actually acts on. Analyze everything, then put roughly five
highest-impact items into the plan's `start_here` list (Step 7), ordered
most important first. Nothing gets dropped to make room -- `start_here`
only decides what the reader meets first.

## Step 7: Render the dashboard

Build the `plan` dict per `render_dashboard`'s documented shape (Step 4's
`projects`/`leftovers`, plus the `findings`/`model_usage`/`recommendations`
that Steps 5 and 6 produced, plus the `start_here` shortlist) and call it.
`start_here` renders above every detailed section and is the difference
between a dashboard that gets acted on and one that gets skimmed -- set it
whenever the run found anything worth prioritizing. Set `time_window` to a readable
rendering of the Step 2a window (e.g. `"2026-05-01 to 2026-07-29 (last 90
days)"`) whenever the run was scoped -- it renders under the header, and
leaving it out makes every count below read as an account total. See
[references/dashboard.md](references/dashboard.md) for the exact fields, the
Artifact-publish-if-available fallback logic, and how the history log makes
re-runs show progress over time.

## Step 8: Present and get explicit review

Stop after rendering. Point the user at the dashboard (its path, and the
live Artifact link if one was published) and ask them to confirm, edit, or
reject pieces of the plan. Don't treat "I rendered it" as the same as "they
reviewed it" -- nothing past this point happens until they've reacted, and
there is no Step past this point yet regardless (see "What this is" above).

## Handling sensitive content

Local session data and an account export both contain the user's actual
conversation history -- business details, personal context, third-party
information. Analyze it in this session to produce the dashboard; don't
restate large verbatim chunks in the dashboard or in chat beyond what's
needed as evidence (a chat name, date, and one-line excerpt is enough).
`list_local_workspace` already redacts secret-shaped keys and heavy tool
schemas before you ever see them -- don't work around that by reading the
underlying files directly.

## Non-goals

- Not a bulk-mover or bulk-deleter -- this skill only ever proposes; Step 8
  is where it stops. That includes the workspace checkup: it proposes
  instruction edits, it never applies them.
- Not an analyzer of Claude Code CLI usage -- see "Who this is for."
- Not a substitute for the user's own judgment on naming/scope, or on
  which model to use -- the dashboard is a strong starting proposal, not a
  final answer.
- Not full automation mining, and not plan execution -- see "What this is."
