# Workspace checkup -- auditing standing instructions and settings

A third lens, alongside [reorganization.md](reorganization.md) (how chats
should be restructured) and [usage-efficiency.md](usage-efficiency.md) (is
day-to-day usage efficient). This one asks: **is the workspace itself
configured well, or is it quietly costing the user on every single turn?**

Like the other two lenses, this produces `findings` entries for the same
`plan` dict Step 6 renders. It never changes anything.

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

For the settings checks below you also need `list_local_workspace`'s
per-session `memory_enabled`/`skills_enabled`/`plugins_enabled`.

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

### Instructions nobody is using

A Space or Project with a substantial `char_count` and a `session_count` of
zero is text that was written and then abandoned. Worth surfacing as
cleanup, low severity -- it costs nothing per turn, but it's a sign the
Project itself may be stale (which feeds the reorganization lens).

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
