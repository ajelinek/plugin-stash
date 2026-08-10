---
name: handoff
description: >
  Compact the current conversation into a handoff document another agent can
  pick up, list the handoffs already on disk, or resume one. Manual only.
argument-hint: "[create|list|resume] [name, focus, or handoff to resume]"
disable-model-invocation: true
---

# Handoff

A handoff is a **transit document**: one markdown file, written to the OS temp
directory rather than the workspace, that a fresh agent reads to continue work
this session started. Reach for it when the work has to *travel* — a new
harness, a different directory, a colleague, or a side task forked off while
this session keeps going. When nothing is travelling, `/compact` is the better
move.

## Dispatch

The first argument selects the operation:

| Arguments | Operation |
|---|---|
| `list` | [List](#list) the handoffs on disk |
| `resume [latest\|fragment]` | [Resume](#resume) one |
| `create [focus]`, anything else, or nothing | [Create](#create) one |

Anything that isn't `list` or `resume` is a **focus description**, not a verb —
`/handoff sort out the auth flow next` creates a handoff aimed at that. With no
arguments at all, create one and derive the focus from the session.

## Where handoffs live

The OS temp directory: `${TMPDIR:-/tmp}` on macOS and Linux, `%TEMP%` on
Windows. Resolve it before every operation rather than assuming — `$TMPDIR` on
macOS is a long per-user path, not `/tmp`.

Files are named `handoff-YYYYMMDD-HHMMSS-<slug>.md`, so a plain filename sort
is newest-last and the slug stays scannable. The slug is three to five
kebab-case words drawn from the focus.

## Create

1. Resolve the temp directory and build the filename. Take the timestamp from
   the system clock and the slug from the focus arguments, or from the work in
   flight when none were passed.
2. Write the document. Cover, in this order: what the next session is for; what
   is in flight right now and why; decisions already made that constrain it;
   the immediate next step; a **Suggested skills** section naming what the next
   agent should invoke.
3. **Reference, don't copy.** Specs, plans, ADRs, issues, commits and diffs go
   in as paths or URLs. Anything already written down stays where it is.
4. **Redact** API keys, tokens, passwords and personal data before writing.
5. **Downgrade what wasn't verified.** The next agent treats this document as a
   contract and won't re-check it, so write "not verified" rather than stating
   an assumption as fact. A belief recorded as a fact becomes a false premise
   for everything that follows.
6. Report the **absolute path** back, and note that temp is not durable — some
   environments clear it between sessions and `/private/tmp` goes on reboot. If
   the next session isn't starting soon, copy the file somewhere durable now.

Tailor the whole document to the focus that was passed. The common complaint
about handoffs is that they capture the what and not the why; the focus is what
tells you which reasoning is worth keeping.

## List

Glob the temp directory for `handoff-*.md` and show them **newest first**: the
timestamp and slug from the filename, a one-line summary read from the
document's opening, and the full path. Say so plainly when there are none.

## Resume

Pick the file first:

- `resume` with no argument, or `resume latest` — the newest handoff.
- `resume <fragment>` — the handoff whose slug contains that fragment. If
  several match, list them and ask which one; never guess.
- An exact filename or absolute path is taken as given.

Then read it and **continue the work** — invoke the skills its suggested-skills
section names, follow the references it points at, and pick up from the
immediate next step. Don't stop to summarize the document back; the point of
resuming is that the work carries on.

---

Based on Matt Pocock's `handoff` skill.
