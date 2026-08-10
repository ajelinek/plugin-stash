# meeting-recap

Turns a meeting transcript into a short, skimmable recap — decisions, action
items with owners and due dates, topics, and open questions — readable in
under a minute.

There is no shortage of "summarize this meeting" skills. What almost none of
them do: enforce a real length limit (most rely on "be concise," which isn't
a rule anything can be held to), or signal uncertainty about what they
extracted (a guessed owner name and a quoted one end up looking equally
confident). This one's edge is discipline — a short recap by default, hard
`UNASSIGNED`/`TBD` fallbacks instead of invented names or dates, explicit
markers on anything inferred, and source tags on anything that didn't come
from the transcript itself.

Skills-only: no MCP server, no dependencies. The one bundled script is
stdlib-only Python.

## Prerequisites

`python3` on PATH. That's it for the normal path.

Retrieving a meeting you *haven't* got a transcript for additionally needs a
**Microsoft 365 connector** enabled in Claude. Without one, hand over a file
or a paste — that path is unchanged and fully supported.

## Install

In Claude Desktop: **Customize → Plugins → (+) → Add marketplace**, enter
`ajelinek/shadetree-ai-plugins`, then install `meeting-recap` from the list.
(Equivalent commands also work in a Desktop or Cowork chat window:
`/plugin marketplace add ajelinek/shadetree-ai-plugins` then
`/plugin install meeting-recap@shadetree-ai-plugins`.)

## What it does

Paste a transcript, point at a file, or just name the meeting:

> *"Recap this morning's architecture sync"*

With a Microsoft 365 connector available, that second form finds the meeting
on your calendar, confirms which one if there's any ambiguity, and pulls the
Teams transcript, the meeting chat, referenced files, and related email
itself.

- **The recap stays short as the meeting gets longer.** A sliding ruler
  scales structure from a 10-minute standup to a multi-day offsite; past a
  point extra content goes into skippable per-session sections rather than
  inflating the summary everyone reads first.
- **Nothing gets invented.** No owner name that isn't a real participant, no
  due date that wasn't stated. Missing ones say `UNASSIGNED` and `TBD`.
- **Decisions are distinguished from discussion.** "Let's go with B" is a
  decision; "what if we tried B" is an open question. A decision reversed
  later in the meeting is recorded at its final state.
- **Chat is merged into the timeline but stays labeled.** A commitment typed
  in chat is a real commitment and gets recorded — tagged `[chat]`, because
  a reader needs to know it was typed rather than said.
- **Files and email are context-only.** They resolve what "the deck" meant;
  they can't put anything in the recap that the meeting didn't cover.
- **Meeting length can't be gamed by chat volume.** Every statistic driving
  the recap's size comes from transcript segments alone, enforced in the
  parser rather than left as a rule to remember.

## Known gaps

- **Transcript retrieval is often blocked by the tenant, not the skill.**
  Verified against a live tenant: Graph access to Teams transcripts is an
  admin setting that's frequently off; meetings you didn't organize return a
  permissions error; and a `.vtt` sitting in SharePoint can't be read at all
  because the connector rejects its MIME type. In each case the skill names
  the specific failure and asks for the file rather than retrying. Calendar,
  chat, files, and email work regardless.
- **Chat interleaving is approximate** — transcript times are relative to
  when recording started, chat times are absolute, and the anchor bridging
  them is the scheduled start. Adjacency is never treated as causation.
- **Google Meet transcripts aren't reachable** — they live in Google Drive
  and there's no Drive connector. Export the file and hand it over.

## What's inside

- `.claude-plugin/plugin.json` — plugin manifest.
- `skills/meeting-recap/SKILL.md` — the four-step flow, non-goals, and limits.
- `skills/meeting-recap/references/sources.md` — the Microsoft 365 retrieval
  chain, the chat anchor, and temp-file handling.
- `skills/meeting-recap/references/extraction-rules.md` — decision-vs-discussion
  heuristics, action-item rules, source provenance, confidence signaling.
- `skills/meeting-recap/references/output-template.md` — the sliding ruler,
  recap templates, source tags, worked examples.
- `skills/meeting-recap/scripts/normalize_transcript.py` — stdlib-only format
  detection, normalization, chat merge, duration, and tier recommendation.
- `tests/test_normalize_transcript.py` — parser tests, including the
  stats-purity guarantees.
