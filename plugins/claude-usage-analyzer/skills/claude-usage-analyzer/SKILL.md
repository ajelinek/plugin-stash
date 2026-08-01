---
name: claude-usage-analyzer
description: >
  Analyzes how the user actually uses Claude -- local Claude Desktop/Cowork
  data on this machine, plus an optional claude.ai account data export --
  and produces a live, self-updating HTML dashboard covering a workspace
  reorganization (which chats belong in which Projects, each Project's
  name/description/custom instructions, what file/folder structure it
  needs, and what's stale vs. active), a usage & best-practices review
  (project/chat counts, model-usage breakdown, chats that used a more
  expensive/complex model than the task needed, prompting/context patterns,
  and recommended public skills/plugins/connectors vs. custom ones worth
  building), and a setup & effectiveness checkup (standing custom
  instructions audited for bloat and duplication across the
  global/Space/Project layers; memory content reviewed for staleness and
  cross-topic drift; each Project graded on whether its name, description
  and instructions match what it's actually for; which folders and hosts
  each session can reach; and which connectors, skills and plugins are
  installed or enabled versus actually used). Built
  for business users of the Claude Desktop app -- Claude Code CLI sessions
  are deliberately excluded, since Claude Code ships its own /doctor,
  /usage, and /context for that. Uses this plugin's bundled
  check_data_access / list_local_workspace / get_project_membership /
  get_instructions_inventory / parse_export / render_dashboard MCP tools.
  Trigger on "analyze my Claude usage/chats/projects", "organize my Claude
  workspace", "audit my Claude usage", "reorganize my chats", "find
  patterns in my conversations", "how am I using Claude", "am I using the
  right models", "how can I optimize my Claude usage", "review my custom
  instructions", "is my Claude set up correctly", "what skills should I
  build", "which connectors am I actually using", "review my Claude
  memory", "what folders can Claude see", or when handed a claude.ai data
  export folder. Analysis
  Every data tool takes an optional since/until time window, so a run can
  cover all history or just a period ("the last 90 days", "since January").
  Analysis and dashboard generation only -- it never moves, renames, or
  deletes a chat or Project itself.
---

# Claude Usage Analyzer

**Skill version: 0.5.0** (matches `.claude-plugin/plugin.json`). Bump this
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
   actually remembered, and whether it has gone stale or cross-topic),
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

**Ask before pulling anything.** Every data tool
(`list_local_workspace`, `get_project_membership`,
`get_instructions_inventory`, `parse_export`) takes the same `since`/`until`
pair, and the answer changes what every number in the dashboard means. Two
choices to put to the user:

- **The full flow** -- all available history. The right default for a first
  run, or for "how am I using Claude" in general.
- **A time window** -- e.g. the last quarter, since a workspace
  reorganization, or a specific date range. The right choice for "what
  changed since," for a recurring review, and for any account big enough
  that all-time averages hide the current picture.

Pass the same window to every tool in the run. Mixing an all-time chat
count with a windowed model breakdown produces a dashboard that quietly
contradicts itself.

Both bounds accept a relative age (`"90d"`, `"6m"`), an ISO date
(`"2026-01-01"`), or epoch milliseconds. **Prefer the relative form** --
it's resolved server-side against the real clock, so it can't be thrown off
by an uncertain sense of today's date. Sessions and conversations are
matched on *overlap*, not creation date: a chat begun in January and still
being worked in March is in a March window.

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

## Step 4: Analyze (reorganization lens)

See [references/reorganization.md](references/reorganization.md) for the
full method: reconstructing chat -> Project/Space membership when the
direct link is missing or partial, spotting topic clusters, and separating
stale one-offs from active work worth structuring.

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
Auto costs *more* usage than Manual or Skip), **surface choice** (Cowork
costs significantly more than chat; simple questions belong in chat),
context/prompting patterns worth a look, and flagging (with evidence, not
a deep build-out) recurring manual workflows that look like automation
candidates.

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

Call `get_instructions_inventory()` and see
[references/workspace-checkup.md](references/workspace-checkup.md) for the
full method. Six areas:

1. **Standing instructions** -- the three always-loaded layers (global
   custom instructions, each Space's `instructions`, each cloud Project's
   `prompt_template`): lines duplicated between a Project and the global
   text, the same text pasted into several Projects, and instructions
   describing things Claude can already see for itself. That last one is
   `/doctor`'s most valuable check and needs real reading rather than a
   count -- keep standing constraints, tone, jargon definitions and
   anything contradicting a sensible default; cut what's derivable.
2. **Memory** -- from `parse_export`'s `stats.memory` and
   `memory_context.md`. Stale or contradicted entries, cross-topic
   pollution in a project's pool, duplication against standing
   instructions, and whether memory is on at all. Memory content is
   **export-only**. Two traps worth telling the user outright: deleting a
   conversation does *not* delete the memory it produced, and incognito
   chats still appear in Team/Enterprise exports.
3. **Project and Space quality** -- grade existing ones on name,
   description and instructions. Report only concrete defects, and write
   the replacement text for each; stay silent on the ones already fine.
4. **Access scope** -- what each Space/session can reach
   (`user_selected_folders`, `egress_allowed_domains`,
   `web_fetch_allowed_url_hosts`), broad grants, and scope creep
   (`touched_dirs`/`url_hosts` versus what was granted and asked for).
   Anthropic publishes this guidance; the reference doc quotes it directly.
5. **Capability inventory** -- what's installed and enabled
   (`enabled_mcp_server_labels`, `plugin_names`, `remote_mcp_server_names`)
   versus what was actually invoked (`tools_invoked`,
   `mcp_servers_invoked`). Installed-but-never-used, used-constantly, and
   needed-but-missing.
6. **Settings hygiene** -- the per-session `memory_enabled` /
   `skills_enabled` / `plugins_enabled` toggles.

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
