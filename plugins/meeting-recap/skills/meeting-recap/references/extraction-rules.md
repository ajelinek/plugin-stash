# Extraction Rules

These are the judgment calls that turn normalized segments into a trustworthy
recap. They exist because the survey of ~18 competing meeting-recap skills
done to build this one found the same handful of failure modes recurring
almost everywhere: invented owner names, decisions presented with the same
confidence as guesses, and no distinction between "we decided X" and
"someone floated the idea of X."

## Source provenance: what each source is allowed to support

A recap may draw on up to four sources: the **transcript**, the meeting
**chat**, **files** shared or referenced, and related **email**. They are not
equal evidence, and flattening them into one undifferentiated pile is the
fastest way to produce a recap that is confident and wrong.

| Source | May source a decision / action item / open question? | Role |
|---|---|---|
| Transcript | Yes — primary | What was actually said in the meeting |
| Chat | Yes | People genuinely commit in chat, and a typed commitment that was never spoken is real |
| Files | **No** | Resolves what "the deck" or "the spec" refers to |
| Email | **No** | Resolves context, names, and prior threads |

**Files and email are context-only.** They can tell you that "the Q3 doc"
means a specific document, that "Marcus" is Marcus Reed, or what an acronym
expands to. They can never be the sole basis for an item in the recap, no
matter how clearly the document states something. A decision recorded in a
spec but never discussed in the meeting was not made in this meeting.

The failure mode this prevents: an email thread that merely happened near
the meeting in time gets absorbed, and the recap starts describing a wider
world than the meeting covered. When you catch yourself writing an item you
can't point to in the transcript or chat, that item doesn't belong.

### Tagging non-transcript content

Anything not sourced from the transcript carries a tag in the output —
`[chat]` on an item that came from a typed message, `[from <file/thread>]`
on a clarification. See "Source tags" in
[output-template.md](output-template.md) for the exact form.

This is the same principle as the uncertainty markers below: a reader
skimming a recap has no way to know that "Marcus will own the rollout" was
typed in chat by someone else on Marcus's behalf, rather than said by
Marcus, unless the recap tells them.

### What chat is not

`normalize_transcript.py` merges chat into the segments list
chronologically, so a message sits beside what was being said at the time.
That adjacency is **approximate and non-load-bearing**:

- With `stats.chat_alignment` = `anchored`, everything is offset by however
  long recording lagged the meeting's scheduled start — usually a minute or
  two, sometimes more.
- With `unanchored`, chat couldn't be placed at all and was simply appended.
  Its position means *nothing*.

So never write that a chat message was responding to a particular remark,
that someone "immediately replied in chat," or that a link was posted "in
response to" a question — unless the message text itself says so. Proximity
in the merged list is not evidence of causation.

Chat senders appear in `stats.chat_senders`, kept separate from
`stats.speakers` (who actually talked). Both are valid sources for an owner
name; neither licenses inventing one. Someone who only ever typed is still a
real, named participant.

## Deciding vs. discussing

A line only counts as a **decision** if the transcript shows actual
agreement, not just a proposal on the table. Use these as heuristics, not a
rigid parser:

- **Decision language:** "we've decided," "agreed," "let's go with," "final
  call is," "confirmed," or a proposal that a subsequent line explicitly
  accepts ("sounds good," "yes let's do that," "+1").
- **Not a decision (task language):** "we should," "can you," "I'll handle,"
  "someone needs to" -- these are action-item candidates, not decisions.
- **Not a decision (open idea):** "what if," "I wonder," "maybe we could,"
  "thoughts on," "not sure yet" -- these belong in Open Questions, never in
  Decisions, even if they sound confident.

**Reversed or superseded decisions:** meetings backtrack. If something is
decided early and then changed later -- whether later in the same
transcript, in a later session, or on a later day of a multi-day event --
record only the final state as *the* decision. A short parenthetical --
"(revised from the earlier plan to ship Thursday)" -- is worth including
when the reversal itself is material; skip it when the earlier version was
clearly just thinking out loud. For a hierarchical recap (see [Multi-session
and multi-day meetings](#multi-session-and-multi-day-meetings) below), this
means checking later sessions/days before finalizing the top-level
Decisions list, not just scanning one session in isolation.

If a meeting made no real decisions (pure brainstorm, status update, etc.),
say so in one line rather than omitting the section or stretching a
discussion point to look like one.

## Action items that are actually actionable

Every action item needs three things. If the transcript doesn't supply one,
use the fallback -- never invent a plausible-sounding substitute:

| Field | Required form | Fallback when absent from the transcript |
|---|---|---|
| Task | One sentence, verb-first ("Send the updated deck," not "Deck stuff") | -- (if there's no clear task, it isn't an action item) |
| Owner | A name that actually appears in the transcript or speaker list | `UNASSIGNED` |
| Due date | A concrete date, or the relative phrase actually used ("by Friday") | `TBD` |

Do not add a status/progress field -- a single transcript is a snapshot and
cannot know whether something later got done; claiming otherwise is a
fabrication risk, not a feature.

A vague mention ("we should really follow up on the vendor thing at some
point") that never gets a task, owner, or timeframe attached is **not** an
action item -- put it in Open Questions/Parking Lot instead of dressing it up
with a table row it doesn't earn.

## Topic clustering

- If the meeting had a stated agenda, use it as the organizing backbone
  instead of inventing new topic buckets.
- Otherwise, merge related sub-threads into a small number of meaningfully
  distinct clusters -- the count in `stats.recommended_tier` (typically 3-5
  for a Standard meeting; see the sliding ruler in
  [output-template.md](output-template.md)), not one bucket per utterance.
  Working memory for a scanned list holds a handful of grouped items well;
  a longer flat list stops being skimmable regardless of how short each
  line is.
- Each topic gets 1-3 bullets, not a paragraph. If a topic genuinely needs
  more than that to make sense, it's a candidate for the expanded notes
  offered in Step 4 of SKILL.md, not the default recap.
- Skip pure noise: greetings, "can everyone hear me," recording/logistics
  chatter, and scheduling-only exchanges -- unless scheduling *was* the
  point of the meeting.

## Redundancy check

Before presenting, scan the TL;DR against Decisions/Action Items, and scan
Decision bullets against each other, for a fact or qualifier stated more
than once (a format rule, a contrast, a named tool). Keep it in whichever
spot is most load-bearing and cut it elsewhere. Don't let this turn into
dropping the fact entirely -- precision on names/tools/numbers should
survive even when the surrounding restatement gets cut.

## Open questions / parking lot

Keep this structurally separate from Decisions so nothing undecided can be
mistaken for something settled. Each entry: what's unresolved, and who (if
anyone) is expected to answer it. An item can appear here even if it also
has a related action item elsewhere (e.g. "Bob will investigate pricing" as
an action item, "what pricing tier makes sense" as the still-open question
it's meant to resolve).

## Confidence and uncertainty signaling

Never silently guess. Concretely:

- **Names:** only use names that appear in the transcript, the `speakers` or
  `chat_senders` lists from `normalize_transcript.py`, or the meeting's
  attendee roster if Step 0 retrieved one. Never invent an owner because the
  table looks incomplete without one. An attendee who never spoke or typed
  is a name you can *resolve* a partial mention against — not a candidate to
  assign work to.
- **Ambiguous attribution:** when speaker labels are missing or a comment's
  origin is genuinely unclear, say so in first person ("I couldn't tell from
  the transcript who said this") rather than picking a plausible name, and
  rather than a generic passive hedge ("it is unclear").
- **Ambiguous decisions:** if something reads as *probably* decided but the
  wording is genuinely ambiguous, mark it inline with `(unclear from
  transcript)` instead of asserting it flatly either way.
- Don't manufacture a confidence caveat for content that's actually clear --
  reserve these markers for genuine ambiguity, or they stop meaning anything.

## Meeting-type adaptivity

Start from `stats.recommended_tier` (see the sliding ruler in
[output-template.md](output-template.md)) for the base structure, then
adjust for the meeting's actual type and content -- duration is a strong
prior, not an override of what's actually in the transcript:

- **Very short / single-topic (standup, quick sync):** the TL;DR plus
  whichever of Decisions/Action Items/Open Questions actually have content
  is enough -- don't force a "Topics Discussed" section that would just
  repeat the TL;DR.
- **1:1 or single-subject call:** the whole meeting may *be* one topic; skip
  topic clustering and go straight to what was decided/actioned, even if its
  duration would otherwise suggest more structure.
- **Standard sync, planning, or review:** the flat template applies as-is.
- **Brainstorm / exploratory session:** it's normal and expected to have few
  or zero decisions and many open questions -- say that plainly rather than
  padding the Decisions section to look substantive.
- **Half-day and beyond, or real session/day breaks detected:** switch to
  the hierarchical rollup-plus-detail structure -- see [Multi-session and
  multi-day meetings](#multi-session-and-multi-day-meetings) below and the
  hierarchical template in [output-template.md](output-template.md). A
  tightly-focused 2.5-hour workshop with no real break can stay flat; a
  45-minute meeting that jumps across 8 unrelated topics might need early
  structure despite its short duration -- detected breaks and actual
  content can override the duration default in either direction.

A section that legitimately has nothing in it still gets a one-line "None"
rather than silently disappearing -- an omitted section reads as "forgot to
check," a stated "None" reads as "checked, nothing here."

## Long transcripts

The normalized `stats.word_count`/`duration_sec` tells you how much material
you're working with -- but a longer input should almost never mean a
proportionally longer *output*. Work through a long transcript in sequence
(by natural agenda or session breaks if there are any, or in large
sequential passes otherwise) and keep a running list of candidate
decisions/action items/topics as you go, so nothing from later in the
meeting gets dropped just because it's furthest from the end of your
context. Then compress that running list to fit whichever tier actually
applies:

- **Extended tier and below:** compress into the same flat, short recap as
  any shorter meeting -- length of input and length of the (single) output
  are separate variables, and only the output is under a hard budget (see
  [output-template.md](output-template.md)).
- **Half-day tier and above:** some growth in *total* document size is
  expected and fine, but only as more short, independently-skippable chunks
  (sessions or days) -- never as longer prose in the part everyone reads
  first. See below.

## Multi-session and multi-day meetings

Once `stats.recommended_tier.structure` is `hierarchical` -- or the content
itself won't compress into the flat cluster cap even after merging, even at
a shorter duration -- switch to the rollup-plus-detail template in
[output-template.md](output-template.md) instead of a longer flat document.
What that means for extraction specifically:

- **Each session/day gets its own extraction pass**, following exactly the
  rules above (deciding vs. discussing, action-item fields, topic
  clustering) as if it were a standalone meeting of that session's own
  length -- a 40-minute session inside a full-day workshop gets
  Standard-tier treatment for its own detail section.
- **The top-level Decisions and Action Items are a consolidated rollup**
  across every session/day, not a duplicate of each session's own list --
  and reversed decisions get resolved across the *whole* rollup (see
  "Reversed or superseded decisions" above), not just within one session.
- **Boundaries come from evidence, not just duration.** Use
  `stats.possible_session_breaks` and `stats.day_marker_hints` as signals,
  corroborated by the transcript's own language (a stated agenda, "let's
  move to the next session," a new day's greeting) -- see "Finding the
  session/day boundaries" in [output-template.md](output-template.md) for
  the full guidance, including multi-day-specific deduplication and
  cross-day reversal handling.

## Worked example: a trickier case

Real meetings backtrack and leave things half-assigned. From a ~4-minute,
3-speaker sync (trimmed):

```
0:30 Dev: I think we should go with option B, the three-column layout.
0:45 Marcus: I like option C better, the toggle felt cleaner.
0:55 Priya: What if we did a hybrid -- three columns with the toggle on top?
1:05 Dev: Let's go with that then.
1:15 Priya: Agreed, that's the plan. Dev can you own building that by Wednesday?
...
1:32 Marcus: Actually, on second thought, let's just go with option B as-is, no
             toggle -- the survey data says the toggle didn't move the needle.
1:50 Priya: Good catch. Final answer: plain three-column layout, no toggle.
             Dev, same timeline, Wednesday.
...
2:45 Priya: Someone needs to review the legal language in email two before we send.
3:05 Priya: Let's leave that one open, not sure whose job that actually is.
```

This resolves to:

```markdown
## Decisions
- **Pricing page will use the plain three-column layout, no toggle** --
  agreed by Priya and Dev based on survey data. (Revised from an earlier
  in-meeting decision to add a toggle on top of the three columns.)

## Action Items
| Owner | Action | Due |
|---|---|---|
| Dev | Build the pricing page redesign (three columns, no toggle) | Wednesday |
| UNASSIGNED | Review the legal language in email two before sending | TBD |
```

Two things to notice: the hybrid-with-toggle plan never appears as if it
were also a decision (it was superseded before the meeting ended, so only
the final state is recorded, with a one-line note since the reversal was
material to the outcome); and the legal-review item still qualifies as an
action item -- the *task* is clear and necessary even though the transcript
explicitly leaves the *owner* unresolved, which is exactly what `UNASSIGNED`
is for.

## Before presenting: one quick self-check

Re-read your drafted Decisions and Action Items once against the source
transcript before showing them to the user. You're checking specifically
for: a name that doesn't actually appear in the transcript, a decision that
was actually just a suggestion, and a due date you inferred rather than one
that was stated. This is a cheap single pass, not a second full analysis --
its only job is to catch the specific fabrication risks above before they
reach the user.

When sources beyond the transcript were pulled, this pass has two more jobs,
and they're the ones most likely to catch a real error:

- **Point every item at its source.** For each decision, action item, and
  open question, name where it came from. Anything you can only trace to a
  file or an email thread is context that leaked into the recap as fact —
  remove it, or demote it to a clarification on an item that *is* grounded
  in the meeting.
- **Check that every non-transcript item is tagged.** An untagged item reads
  as "someone said this out loud," and for a chat message or a
  file-sourced clarification that's simply not true.
