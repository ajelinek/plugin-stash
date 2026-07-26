# Usage & best-practices analysis

A second, independent lens on the same data Step 3 already gathered --
alongside (not instead of) the reorganization proposal in
[reorganization.md](reorganization.md). Where that lens asks "how should
existing chats/Projects be restructured," this one asks "is the user's
day-to-day usage actually efficient, and what would make it more so."

This produces the `findings` and `model_usage` sections of the same
`plan` dict Step 6 renders -- see the `render_dashboard` tool's docstring
for the exact shape.

## Work from the compact summaries, not raw transcripts

`list_local_workspace`'s CLI sessions and `parse_export`'s
`conversations.jsonl` already carry everything this lens needs per
chat/session, cheaply: `first_human_message`, `keywords`, `tool_names`,
`message_count`, and a `models_used` tally (`{model: count}`, aggregated
file-side, never full message content) -- CLI sessions always carry this;
export conversations carry it only when that export's schema happens to
expose a per-message model field. Reason from these fields across the
whole corpus; only open a specific underlying jsonl/export file directly,
and only for the one chat in question, when a summary is genuinely
ambiguous and it actually changes a finding you're about to report.
Re-reading everything defeats the point of the paged summaries and will
blow up context on a large account.

If every conversation in an export shows an empty `models_used`, check
`parse_export`'s `stats.notes` -- that means this export schema doesn't
expose a per-message model field, and model-usage findings for claude.ai
web chats aren't available this run. Local CLI session data is usually
the more reliable source for that signal; say so rather than reporting a
misleadingly empty model-usage section as if nothing was found.

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
2. Cross the `models_used` tally per session/conversation against the task
   signal in `first_human_message`/`keywords`/`tool_names`:
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
  chats as evidence and leave the actual recommendation (Skill vs.
  scheduled task vs. neither) as a single line, not a build-out.
- **Tool/connector friction** -- repeated asks that a session's available
  tools/connectors couldn't actually satisfy (visible as a chat that
  stalls or pivots right after asking for something) suggest a connector
  or plugin worth setting up permanently rather than working around it
  each time.

## Feeding the plan

Add to the same `plan` dict Step 6 renders:

- **`model_usage`** -- `[{"model": str, "count": int}]`, aggregated across
  every session/conversation in play this run. Sort by count, descending.
- **`findings`** -- `[{"title": str, "severity": "info"|"warning"|
  "critical" (optional), "evidence": [str, ...], "recommendation": str}]`,
  one entry per pattern actually found. Don't pad this list with
  decorative non-findings just to fill the section -- an honest empty
  list is a fine result if nothing stood out.

Then return to the main skill file's Step 6 (render) and Step 7 (review).
