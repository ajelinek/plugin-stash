---
name: claude-usage-analyzer
description: >
  Analyzes how the user actually uses Claude -- local Claude Desktop/Cowork/
  CLI data on this machine, plus an optional claude.ai account data export --
  and produces a live, self-updating HTML dashboard covering both a
  workspace reorganization (which chats belong in which Projects, each
  Project's name/description/custom instructions, what file/folder structure
  it needs, and what's stale vs. active) and a usage & best-practices review
  (project/chat counts, model-usage breakdown, chats that used a more
  expensive/complex model than the task needed, prompting/context patterns,
  and recommended public skills/plugins/connectors vs. custom ones worth
  building). Uses this plugin's bundled
  usage_doctor / list_local_workspace / parse_export / render_dashboard MCP
  tools. Trigger on "analyze my Claude usage/chats/projects", "organize my
  Claude workspace", "audit my Claude usage", "reorganize my chats", "find
  patterns in my conversations", "how am I using Claude", "am I using the
  right models", "how can I optimize my Claude usage", or when handed a
  claude.ai data export folder. Analysis and dashboard generation only --
  it never moves, renames, or deletes a chat or Project itself.
---

# Claude Usage Analyzer

## What this is

A read-only analysis of the user's own Claude usage, combining up to two
sources of data, ending in a single reviewable deliverable: a **live HTML
dashboard** covering two independent lenses on the same data:

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

Call `usage_doctor` first, every session. It reports which local data
locations (Claude Code CLI's `~/.claude`, Claude Desktop's app-data
directory, the user-visible `~/Claude` output folder) are visible right now,
per-platform.

**If `usage_doctor` finds nothing:** this is not proof the user has no
Claude history. Read `references/data-sources.md` before saying anything
further -- the short version:

- Running via the **Claude Code CLI** in a terminal/IDE already has ordinary
  filesystem access from its working directory; a doctor failure here is a
  genuine "not installed/not used on this machine" signal.
- Running as an **installed Desktop/Cowork plugin**, this MCP server only
  sees what the current session can see. If the session is inside a
  folder-scoped Cowork Space or Project, those folders have to be added to
  that Space/Project's file access scope explicitly -- the plugin has no
  broader access on its own. Tell the user exactly which folder(s)
  `usage_doctor`'s `desktop_candidates`/`cli` fields list for their platform,
  and that they need to add them via the Space/Project's file/folder
  settings, then restart the session.

Don't proceed to a full analysis on a guessed-at inventory -- get real
access (or an explicit "local data isn't available, export-only" decision)
first.

## Step 2: Decide what data to use

Two independent sources, not mutually exclusive:

1. **Local data** (`list_local_workspace`) -- whatever `usage_doctor` found
   readable. Always available if Step 1 succeeded; no user action needed
   beyond the folder-scope fix above.
2. **A claude.ai account export** (`conversations.json` etc.) -- covers
   claude.ai web chats, which local data never sees at all (see
   references/data-sources.md section 4 for exactly how the two overlap and
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
     they've downloaded it themselves).

Say plainly which source(s) ended up in play before presenting any analysis
-- local-only, export-only, or both -- since that changes how complete the
picture is.

## Step 3: Gather the data

- Call `list_local_workspace` if local data is in play.
- Call `parse_export(export_dir)` if an export is in play. Check the
  returned `stats.notes` first: a thin corpus (under 10 conversations) or a
  missing per-conversation project link changes how much confidence to put
  in the analysis, and both need to be said plainly before presenting
  output, not silently absorbed.

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
the full method: right-sizing model choice against task complexity using
each session/conversation's model-usage signal, spotting context/prompting
patterns worth a look, and flagging (with evidence, not a deep build-out)
recurring manual workflows that look like automation candidates.

Model data isn't uniform across sources -- CLI and Cowork/Chat local
sessions carry a per-message `models_used` tally (or a `default_model`/
`effort` fallback); the claude.ai account export never carries model data
at all (confirmed, not just an occasional gap). See
[references/data-sources.md](references/data-sources.md) section 5 before
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

## Step 6: Render the dashboard

Build the `plan` dict per `render_dashboard`'s documented shape (including
this step's `findings` and `model_usage`, alongside Step 4's `projects`/
`leftovers`) and call it. See [references/dashboard.md](references/dashboard.md)
for the exact fields, the Artifact-publish-if-available fallback logic,
and how the history log makes re-runs show progress over time.

## Step 7: Present and get explicit review

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

- Not a bulk-mover or bulk-deleter -- this skill only ever proposes; Step 7
  is where it stops.
- Not a substitute for the user's own judgment on naming/scope, or on
  which model to use -- the dashboard is a strong starting proposal, not a
  final answer.
- Not full automation mining, and not plan execution -- see "What this is."
