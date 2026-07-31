# Usage & best-practices analysis

A second, independent lens on the same data Step 3 already gathered --
alongside (not instead of) the reorganization proposal in
[reorganization.md](reorganization.md). Where that lens asks "how should
existing chats/Projects be restructured," this one asks "is the user's
day-to-day usage actually efficient, and what would make it more so."

This produces the `findings`, `model_usage`, and `recommendations`
sections of the same `plan` dict Step 6 renders -- see the
`render_dashboard` tool's docstring for the exact shape.

## Everything here is scoped to the run's time window

If Step 2a set a `since`/`until`, every count, tally, and "recurring across
N chats" claim below describes that window and nothing else. Two things
follow. A pattern needs to recur *within* the window to count as recurring
-- three occurrences in a 90-day window is a stronger signal than three
across four years, and the same three chats mean different things in each
case. And every headline number needs the window attached when presented;
`time_window` on each tool response has the resolved bounds and the
excluded count to quote.

## Work from the compact summaries, not raw transcripts

`list_local_workspace`'s local sessions and `parse_export`'s
`conversations.jsonl` already carry everything this lens needs per
chat/session, cheaply: `first_human_message`, `keywords`, `tool_names`,
`message_count`. Reason from these fields across the whole corpus; only
open a specific underlying jsonl/export file directly, and only for the
one chat in question, when a summary is genuinely ambiguous and it
actually changes a finding you're about to report. Re-reading everything
defeats the point of the paged summaries and will blow up context on a
large account.

Model data specifically is not uniformly available -- see
[data-sources.md section 5](data-sources.md#5-where-model-usage-data-actually-lives)
for the full picture; the short version:

- **Cowork/Chat local sessions** carry a `models_used` tally
  (`{model: count}`, per-message accurate -- a session can switch models
  mid-conversation, e.g. a sub-agent/background step running a cheaper
  model), joined in from that session's own nested transcript. They also
  carry `default_model`/`effort` -- the session's configured default -- as
  a fallback when no `models_used` is present (no nested transcript
  matched, e.g. a chat with no agentic work).
- **Export conversations never carry this** -- confirmed directly, not
  just an occasional schema gap. If `parse_export`'s `stats.notes` flags
  a missing per-message model field, model-usage findings for claude.ai
  web chats simply aren't available this run; say so plainly rather than
  reporting a misleadingly empty model-usage section as if nothing was
  found. Local Cowork/Chat data is the only source for this signal.

## Model right-sizing

The goal is spotting a *mismatch* between task complexity and the model
tier used for it, not auditing every choice -- treat each result as "here's
a pattern worth a look," never as a certain verdict. Only the user knows
if a given choice was deliberate (a compliance requirement, deliberate
experimentation, etc.).

1. **Look up the current model lineup before judging anything.** Model
   names, tiers, and capabilities change over time, and this doc will go
   stale if it hardcodes them -- don't rely on memorized model names or
   pricing. Each session, check for a reference already available in this
   environment (e.g. a "claude-api"-style skill, or Anthropic's own
   published model-selection guidance via `WebSearch`/`WebFetch` if
   nothing local is available) to learn today's rough tiering: typically
   a fast/lightweight tier meant for classification, extraction, and
   simple Q&A/chat; a general-purpose mid tier; and a frontier tier meant
   for hard multi-step reasoning or agentic work. Reason about *relative*
   tiering from whatever that turns up, not from any specific example
   named in this doc.
2. Cross the `models_used` tally (or `default_model`/`effort` when that's
   all a session has) per session/conversation against the task signal in
   `first_human_message`/`keywords`/`tool_names`:
   - A frontier-tier model used repeatedly for short, simple asks (a
     one-line factual question, a single formatting/classification pass,
     no tool use, few messages) is the clearest over-provisioned pattern
     -- flag it, and note roughly how many sessions/messages fit it.
   - A lightweight model used for long, clearly agentic/multi-step work
     (many tool calls, many back-and-forth corrections, a `message_count`
     far above this account's typical chat) is the inverse pattern --
     worth flagging too, since it often shows up as the user
     re-explaining/correcting rather than the model actually failing
     less. Note it as a possible under-provisioning pattern.
   - Don't flag a single occurrence as a pattern -- look for it recurring
     across multiple sessions/conversations before reporting a finding.
3. Report each finding with concrete evidence (chat/session names or
   dates, not verbatim content) and a one-line recommendation (e.g. "these
   N short lookup-style chats used the frontier tier -- the lightweight
   tier would likely handle them at a fraction of the cost/latency").

## Other patterns worth flagging

- **Context/session hygiene** -- a single chat that clearly drifted across
  several unrelated topics (checkable via `keywords` shifting partway
  through, or a `message_count` far above the norm) instead of starting a
  fresh chat or a Project; standing context (the same background,
  constraints, or instructions) being retyped chat after chat instead of
  living in a Project's custom instructions/memory.
- **Prompting** -- a chat with an unusually high `message_count` relative
  to how simple its `first_human_message` looks can mean the initial ask
  was under-specified and needed many corrective follow-ups; not a
  certainty, but worth a look.
- **Automation candidates** -- the same multi-step manual process
  described near-identically across multiple chats (a recurring
  `keywords`/`tool_names` combination, similar `first_human_message`
  phrasing) is a candidate for a Skill, a slash command, or a scheduled
  task instead of being re-explained every time. This is a lighter,
  evidence-based flag only -- not the full automation-mining program the
  main skill file's Non-goals section still defers; cite the repeated
  chats as evidence. Turn this into a concrete recommendation using the
  "Recommending skills, plugins & connectors" section below rather than
  leaving it as a vague "consider automating this."
- **Tool/connector friction** -- repeated asks that a session's available
  tools/connectors couldn't actually satisfy (visible as a chat that
  stalls or pivots right after asking for something) suggest a connector
  or plugin worth setting up permanently rather than working around it
  each time -- also feeds the section below.
- **Environment-settings hygiene** -- `list_local_workspace`'s
  `memory_enabled`/`skills_enabled`/`plugins_enabled`/`custom_instructions`
  per session (see
  [data-sources.md section 6](data-sources.md#6-environment-settings-custom-instructions-and-what-is-genuinely-not-answerable))
  are configuration, not usage, but worth a look: memory or skills turned
  off for a Space/Project where the chat content clearly could have used
  them (repeated re-explaining of the same standing context); a global
  `custom_instructions` that's stale, missing, or contradicts a pattern
  visible across many chats. Same evidence bar as everything else here --
  a recurring pattern, not a one-off guess -- and these are per-session
  values, so check whether they actually vary across a Space/Project's
  sessions before reporting a single verdict for the whole thing.

  The deeper version of this -- auditing the standing-instruction text
  itself for bloat and cross-layer duplication, backed by
  `get_instructions_inventory` -- is its own lens: see
  [workspace-checkup.md](workspace-checkup.md). Report a given finding in
  one lens or the other, not both.

## Recommending skills, plugins & connectors

When an automation candidate or tool/connector-friction pattern turns up,
don't default straight to "build a custom Skill." Check what already
exists first, and be explicit in the dashboard about which case you're in
-- the user needs to know whether the ask is "install/connect this
existing thing" or "this is worth building from scratch."

1. **Check for an existing match before proposing something custom.**
   Where available, call the harness's own capability-inventory tools
   directly -- `list_skills`, `list_plugins`, `list_connectors` -- rather
   than duplicating them as plugin logic (see
   [data-sources.md section 7](data-sources.md#7-chaining-with-native-harness-tools-instead-of-duplicating-them)
   for the confirmed shapes and join keys). Exact names/availability still
   vary by environment, so fall back to searching (`ToolSearch` or
   equivalent) for whatever's actually there if those specific names
   aren't present. Use whichever combination is available to check:
   - **Skills** -- does a public skill already cover this recurring
     workflow?
   - **Plugins** -- does an existing marketplace plugin already bundle
     the MCP tools this workflow keeps needing?
   - **Connectors** -- is there a data source (email, calendar, a
     specific SaaS product) this workflow keeps manually re-supplying by
     hand that a connector would supply directly, and is a connector for
     it already available in this environment but just not connected yet?

   Confirmed gap, don't try to work around it: there's no callable tool
   for raw MCP connector connection/auth status (is a connected connector
   actually authenticated right now) -- that's plausibly harness-level
   state, not something this discovery step can see. If that specific
   question comes up, say it isn't answerable this run rather than
   inferring it from `connected: true/false` alone.
2. **If no discovery capability is available in this environment at
   all**, say so plainly and skip straight to the custom-build case below
   -- don't guess at what marketplace listings might exist from memory;
   they change, and a guess here risks recommending something that
   doesn't actually exist or is out of date.
3. **Label every recommendation as exactly one of two kinds, never
   blurred:**
   - **Already available** -- an existing public skill, plugin, or
     connector was found that covers this. Name it, name where it comes
     from (which marketplace/registry), and say what manual work it
     would replace (cite the recurring chats as evidence). The ask is
     "install/connect this," full stop -- not "build something."
   - **Worth building custom** -- no existing match was found (or nothing
     fits closely enough), and the pattern is recurring/costly enough to
     be worth a bespoke Skill or plugin. Say so explicitly. It's fine to
     point at Skill-authoring tooling available in this environment (e.g.
     a `skill-creator`-style skill) as the next step, without building it
     out yourself in this pass -- that's a separate, deliberate follow-up
     the user opts into, not something this analysis does automatically.
4. Keep the bar for "worth recommending" the same as other findings here:
   a recurring pattern across multiple chats/sessions, or a genuinely
   strong single signal (e.g. a chat where the user pastes a long email
   thread by hand right after asking something an email connector would
   answer directly) -- not a one-off, and not decorative padding to fill
   the section.

## Feeding the plan

Add to the same `plan` dict Step 6 renders:

- **`model_usage`** -- `[{"model": str, "count": int}]`, aggregated across
  every session/conversation in play this run. Sort by count, descending.
- **`findings`** -- `[{"title": str, "severity": "info"|"warning"|
  "critical" (optional), "evidence": [str, ...], "recommendation": str}]`,
  one entry per pattern actually found. Don't pad this list with
  decorative non-findings just to fill the section -- an honest empty
  list is a fine result if nothing stood out.
- **`recommendations`** -- `[{"title": str, "kind": "existing"|"custom",
  "item_type": "skill"|"plugin"|"connector", "source": str (optional,
  only for `"existing"` -- which marketplace/registry it came from),
  "evidence": [str, ...], "rationale": str}]`, one entry per skill/
  plugin/connector recommendation from the section above -- again, an
  honest empty list beats padding.

Then return to the main skill file's Step 6 (render) and Step 7 (review).
