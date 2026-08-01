# Setup & effectiveness checkup

A third lens, alongside [reorganization.md](reorganization.md) (how chats
should be restructured) and [usage-efficiency.md](usage-efficiency.md) (is
day-to-day usage efficient). This one asks: **is the workspace itself
configured well, or is it quietly costing the user on every single turn?**

It covers six areas:

1. **Standing instructions** -- the three always-loaded layers, audited for
   duplication and derivable content.
2. **Memory** -- what Claude has actually remembered, and whether it has
   gone stale or cross-contaminated.
3. **Project and Space quality** -- does each one have a name, description
   and instructions that match what it is actually for?
4. **Access scope** -- which folders and hosts each session can reach.
5. **Capability inventory** -- what is installed and enabled versus what is
   actually used.
6. **Settings hygiene** -- the per-session toggles.

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

For the memory checks you need `parse_export`'s `stats.memory` (sizes and
category names) and its `memory_context.md` (the text itself). **Memory
content is export-only** -- local data carries the on/off flag and nothing
more.

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

`parse_export`'s `stats.memory` gives you, without reading a word of the
text: whether memory exists at all, the account-level character count and
its category names, and per-project character counts and categories keyed
by project uuid (which joins to `projects_index.json`). Read the text in
`memory_context.md` only for the checks that genuinely need it.

Checks:

- **Cross-topic pollution.** A project's memory carrying categories or
  facts that belong to unrelated work. Memory never crosses project
  boundaries on its own, so this means the project itself is too broad --
  which feeds the reorganization lens.
- **Stale or contradicted entries.** Memory asserting something the recent
  conversations plainly contradict, or describing work that finished long
  ago.
- **Duplication against standing instructions.** A fact stated in both
  memory and a Project's instructions is being paid for twice on every
  turn, exactly like the cross-layer duplication above.
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

Four checks, in descending order of confidence:

1. **Review what's granted.** Present, in plain language, what each Space
   and session can reach: `user_selected_folders`, plus
   `egress_allowed_domains` and `web_fetch_allowed_url_hosts` for network
   reach. This is presentation, not judgment, and it directly implements
   the third quote above. An `egress_allowed_domains` of `["*"]` means
   unrestricted outbound reach and is worth stating plainly.
2. **Broad grants.** A Space or session bound to a home directory, a drive
   root, or an entire Documents tree rather than a dedicated working
   folder. High confidence, quotes the first line above directly.
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

Cross what was available against what was used:

| Available | Used |
|---|---|
| `enabled_mcp_server_labels`, `remote_mcp_server_names` | `mcp_servers_invoked` |
| `enabled_mcp_tool_names` | `tools_invoked` |
| `plugin_names`, `slash_command_names` | `tools_invoked` |

Where the harness exposes its own `list_skills`/`list_plugins`/
`list_connectors`, use those too -- see
[data-sources.md section 7](data-sources.md#7-chaining-with-native-harness-tools-instead-of-duplicating-them).

**Do not expect the two sides to join on a shared identifier.**
`enabled_mcp_tool_names` keys are display labels (`Control Chrome`) while
invoked tool names are slugs (`mcp__claude-in-chrome__navigate`, which
yields the server `claude-in-chrome`). Match them by eye and say so if a
match is uncertain -- do not manufacture a precise-looking join.

Three findings fall out:

- **Installed/enabled but never invoked**, across many sessions -- clutter.
  The common case on a mature account. Low severity, easy win.
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

From `list_local_workspace` (this repeats what
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
