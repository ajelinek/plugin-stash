# handoff

Compacts the conversation you're in into a **handoff document** — one markdown
file in your OS temp directory that a fresh agent can read to pick the work up.

Based on Matt Pocock's `handoff` skill.

Skills-only: no MCP server, no scripts, no dependencies. Nothing runs and
nothing leaves the machine; the file is written locally and stays there.

## Prerequisites

None.

## Install

In Claude Desktop: **Customize → Plugins → (+) → Add marketplace**, enter
`ajelinek/shadetree-ai-plugins`, then install `handoff` from the list.
(Equivalent commands also work in a Desktop or Cowork chat window:
`/plugin marketplace add ajelinek/shadetree-ai-plugins` then
`/plugin install handoff@shadetree-ai-plugins`.)

## Usage

```
/handoff sort out the auth flow next   # create, aimed at that focus
/handoff                               # create, focus derived from the session
/handoff list                          # what's on disk, newest first
/handoff resume latest                 # resume the newest and carry on
/handoff resume auth                   # resume by slug fragment
```

The skill never fires on its own — you invoke it.

## What it does

- Writes `handoff-YYYYMMDD-HHMMSS-<slug>.md` to the OS temp directory, so a
  filename sort is chronological and same-topic handoffs never collide.
- Carries the live thread — what's in flight, why, what's next — plus a
  suggested-skills section naming what the next agent should reach for.
- References specs, plans, issues, commits and diffs by path or URL instead of
  copying them, keeping the file small and the settled detail in one place.
- Redacts keys, tokens, passwords and personal data, and marks unverified
  claims as unverified rather than stating them as fact.
- `resume` reads the document and continues the work directly, rather than
  displaying it and waiting.

## When to reach for it

When the work has to **travel**: swapping harness, moving to another directory
or repo, sending it to a colleague, or forking a side task while this session
keeps going. When nothing is travelling, `/compact` covers the ordinary
end-of-phase case better.

## A note on temp

Temp is deliberate — a handoff is a transit document, not an artifact you
maintain — but it isn't durable. Some environments clear it between sessions
and `/private/tmp` goes on reboot. If the next session isn't starting soon,
copy the file somewhere durable as soon as it's written.
