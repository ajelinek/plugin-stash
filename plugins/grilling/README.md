# grilling

Stress-tests a plan, decision, or idea by interviewing you about it relentlessly
— one question at a time, down each branch of the decision tree, until you and
Claude reach a shared understanding.

Based on Matt Pocock's `grill-me` skill.

Skills-only: no MCP server, no scripts, no dependencies. Nothing runs and
nothing leaves the machine; the plugin is guidance that changes how Claude
conducts the conversation.

## Prerequisites

None.

## Install

In Claude Desktop: **Customize → Plugins → (+) → Add marketplace**, enter
`ajelinek/shadetree-ai-plugins`, then install `grilling` from the list.
(Equivalent commands also work in a Desktop or Cowork chat window:
`/plugin marketplace add ajelinek/shadetree-ai-plugins` then
`/plugin install grilling@shadetree-ai-plugins`.)

Once installed, say "grill me on this plan" — or any other *grill* phrasing —
to start an interview.

## What it does

- Every question goes through the structured question tool, one per turn, so
  answering stays a click and "Other" always leaves free text available.
- Each question leads with Claude's own recommended answer and why, rather
  than handing you a blank prompt.
- Anything discoverable from the filesystem, tools, code, or docs gets looked
  up instead of asked — questions are reserved for things that are genuinely
  yours to decide.
- Decisions are resolved in dependency order, and an answer that invalidates a
  downstream branch re-derives it instead of asking questions that are now moot.
- Nothing is implemented, written, or executed until you explicitly confirm the
  summary at the end, including every point where you overrode a recommendation.

## What's inside

- `.claude-plugin/plugin.json` — plugin manifest.
- `skills/grilling/SKILL.md` — the interview rules, and the whole substance of
  the plugin.
