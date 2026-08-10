# Output Template

The default recap is short by construction, not by luck -- every rule below
exists to stop one specific way these things balloon. If the user
explicitly asks for full minutes, a longer document, or a specific other
format, give them that instead; this is the default when they just want to
know what happened.

## The sliding ruler

Recap length and structure scale with the meeting's actual duration --
`stats.recommended_tier` from `normalize_transcript.py` tells you which row
applies, computed from the transcript's own first recorded start time
through its last recorded end time (see SKILL.md Step 1), not guessed from
how the request is phrased.

| Tier | Duration | Structure | Clusters | Entry-point word budget |
|---|---|---|---|---|
| Micro | ≤15 min | Flat | 0 topics (omit) | 40-80 |
| Short | 15-30 min | Flat | 0-2 topics | 80-150 |
| Standard | 30-60 min | Flat | 3-5 topics | 150-350 |
| Extended | 1-3 hr | Flat | 4-6 topics | 250-450 |
| Half-day | 3-5 hr | Hierarchical | 3-6 sessions | 200-350 |
| Full-day | 5-8 hr | Hierarchical | 4-8 sessions | 250-400 |
| Multi-day | >8 hr / spans days | Hierarchical | 1 chunk/day + rollup | 300-500 |

This table mirrors `LENGTH_TIERS` in `scripts/normalize_transcript.py` --
that's the tunable source of truth; if you retune the numbers, retune them
there and update this table to match, don't let them drift apart.

Notice the entry-point budget barely grows from Standard to Extended (a
meeting three times longer gets a recap barely bigger), and actually
*shrinks back down* at Half-day. That's deliberate, not a rounding
artifact: working-memory research puts comfortable chunk capacity at
roughly 3-5 meaningfully grouped items regardless of input size, so past a
certain point the right response to "there's more content" is to push
detail into independently-skippable sub-sections (sessions, days), not to
keep inflating the one summary everyone reads first. Decisions and action
items are the exception -- see "Never cap what actually happened" below.

Chat never moves the tier. `duration_sec` is computed from transcript
segments alone, so a meeting where fifty links got pasted is not thereby a
longer meeting — the parser enforces this, so the tier can be trusted even
on a heavily-merged transcript.

`stats.recommended_tier.basis` tells you whether the tier came from real
recorded timestamps (`recorded_end_times` or `start_times_only`) or a
word-count estimate (`estimated_from_word_count`, only used when the
transcript has no timestamps at all). If it's an estimate, treat the tier
as a starting point, not a precise cutoff -- a transcript that estimates to
2h50m and one that estimates to 3h10m should probably get the same
treatment even though they land in different tiers.

## Flat recaps (Micro / Short / Standard / Extended)

```markdown
# <Meeting title or topic> -- <date, if known>

**TL;DR:** <1-3 sentences. A reader who stops here should still know what
happened and whether anything is owed to them.>

## Decisions
- **<what was decided>** -- <who/how, in a few words>
(If none: "No decisions were made -- this was a `<discussion/status/brainstorm>` session.")

## Action Items
| Owner | Action | Due |
|---|---|---|
| <name or UNASSIGNED> | <verb-first task> | <date or TBD> |
(If none: "No action items came out of this meeting.")

## Topics Discussed
- **<topic>** -- <1-3 bullets, not a paragraph>
(Omit entirely for a single-topic/short meeting where the TL;DR already covers it.)

## Open Questions
- <what's unresolved> -- <who's expected to weigh in, if known>
(If none: "No open questions.")
```

### Hard rules, and the reasoning behind each

- **TL;DR is mandatory and comes first.** Someone who reads one line should
  still walk away knowing the gist -- everything below is for whoever needs
  more than that.
- **Every bullet is one or two sentences, never a paragraph.** If a point
  needs more room than that to make sense, it belongs in expanded notes
  (Step 4 of SKILL.md), offered on request -- not stretched into the default
  recap.
- **A section with nothing in it still gets a one-line "None," not
  silence.** Test this by asking: could the user tell the difference
  between "I checked and there's nothing here" and "I forgot to check"? If
  not, say it explicitly.
- **Topic cluster count comes from the ruler above, not a paragraph per
  utterance.** A flat list of fifteen bullet points is not skimmable no
  matter how short each bullet is.
- **Bullets and tables, not prose paragraphs.** Busy readers scan; a recap's
  job is to let them skip the full meeting, not to reproduce it in miniature.
- **Whole default recap should fit the entry-point word budget for its
  tier.** If you're well past it, you're including detail that belongs in
  expanded notes, not the recap.
- **Don't restate a decision's mechanism in the TL;DR if it's spelled out
  in a Decision bullet below** -- name the outcome, the reader gets detail
  one paragraph down.

### Source tags

When the recap draws on anything beyond the transcript, say so inline. The
rules for *what may support what* are in
[extraction-rules.md](extraction-rules.md); this is just the notation.

- **Chat-sourced item** — append `[chat]`:
  ```markdown
  - **Rollout moves to the 14th** -- confirmed by Priya. [chat]
  ```
  ```markdown
  | Marcus | Own the rollout comms [chat] | Friday |
  ```
- **Clarification from a file or email** — name the source in the tag, since
  "a document said so" is only useful if the reader knows which:
  ```markdown
  - **Vendor shortlist narrowed to two** -- Acme and Northwind.
    [names from the Q3 vendor deck]
  ```
- **Transcript-sourced content is never tagged.** It's the default, and
  tagging everything would make the tags meaningless.

If a meeting's chat contributed nothing worth including, no tags appear and
nothing needs to be said about it. But if chat was *unavailable* — the match
wasn't confident, or the meeting had none — state that in one line under the
TL;DR, because "no chat items" and "chat wasn't checked" are very different
claims:

```markdown
> Recap is from the transcript only — I couldn't confidently identify this
> meeting's Teams chat.
```

Apply the same one-liner when `stats.chat_alignment` is `unanchored`: chat is
included, but nothing about its position in the timeline is meaningful.

### A worked example

Input (labeled transcript, ~4 minutes, 3 speakers -- `duration_sec` from
`normalize_transcript.py` is 60s, so tier = **Micro**):

```
0:00 Alice: Hey team, quick sync on the launch date.
0:15 Bob: I think we should push to next Friday given QA is behind.
0:30 Alice: Agreed, let's go with next Friday. Bob can you own the QA checklist?
0:40 Bob: Yep on it, will have it done by Wednesday.
0:50 Carol: What about the marketing email, is that still going out Thursday?
1:00 Alice: Good question, let's leave that open for now.
```

Output:

```markdown
# Launch Date Sync -- (date not stated in transcript)

**TL;DR:** Launch pushed to next Friday because QA is behind; Bob owns the
QA checklist by Wednesday. Marketing email timing is still unresolved.

## Decisions
- **Launch date moved to next Friday** -- agreed by Alice and Bob due to QA being behind schedule.

## Action Items
| Owner | Action | Due |
|---|---|---|
| Bob | Complete the QA checklist | Wednesday |

## Open Questions
- Is the marketing email still going out Thursday? -- raised by Carol, left open by Alice.
```

Notice what's *not* here: no "Topics Discussed" section (the whole minute
was one topic, already covered by the TL;DR and the sections above), no
invented meeting date (the transcript never stated one, so it says so
rather than guessing), and no participants list padded out with roles
nobody mentioned.

Now the same meeting with its chat merged in, where Carol's open question
got answered by someone who never unmuted:

```
[chat 1:02 Marcus] marketing email is already scheduled for Thursday, no change
```

Only two lines move — and the tag is the whole point:

```markdown
**TL;DR:** Launch pushed to next Friday because QA is behind; Bob owns the
QA checklist by Wednesday. Marketing email is unchanged, still going
Thursday.

## Decisions
- **Launch date moved to next Friday** -- agreed by Alice and Bob due to QA being behind schedule.
- **Marketing email stays on Thursday** -- confirmed by Marcus. [chat]
```

The marketing email moves out of Open Questions because it genuinely got
resolved — a commitment typed in chat is a real commitment, which is why
chat may source an item at all. But it stays tagged, because a reader
scanning this needs to know Marcus typed it rather than said it, and that
nobody in the room necessarily heard it. What the recap must *not* say is
that Marcus was replying to Carol: his message lands near hers in the merged
timeline, and that proximity proves nothing.

## Hierarchical recaps (Half-day / Full-day / Multi-day)

Once a meeting has real internal structure -- distinct sessions separated
by breaks, or it literally spans multiple days -- a longer *flat* document
stops being skimmable no matter how disciplined the bullets are. The fix
isn't a bigger version of the same template; it's a short top-level rollup
that stands on its own, with per-session (or per-day) detail underneath it
that most readers never have to open.

```markdown
# <Meeting/event title> -- <date or date range>

*<N> sessions · about <X> min to read this rollup*

**TL;DR:** <3-6 sentences covering the headline outcomes across the whole
event. A reader who never opens a single session below should still know
what got decided and what's owed to them.>

## Sessions
1. **<Session 1 name>** -- <one-line takeaway>
2. **<Session 2 name>** -- <one-line takeaway>
3. **<Session 3 name>** -- <one-line takeaway>

## Decisions
- **<what was decided>** -- <who/how> _(Session 2)_
(A decision revisited in a later session follows the same reversal rule as
a single transcript -- record only the final state, noting the reversal
when it's material. Now it's scanning across the whole rollup, not just one
transcript, so check later sessions/days before finalizing this list.)

## Action Items
| Owner | Action | Due | From |
|---|---|---|---|
| <name or UNASSIGNED> | <verb-first task> | <date or TBD> | <session/day> |
(See "Grouping large tables" below once this passes ~15 rows.)

## Open Questions
- <what's unresolved> -- <who's expected to weigh in>

---

## Session detail

### Session 1: <name>
<A normal flat recap scoped to just this session -- sized per the Standard
or Extended row of the ruler, whichever fits this session's own length.>

### Session 2: <name>
...
```

### Grouping large tables

Decisions and action items are never capped by tier -- suppressing a real
commitment to hit a length target is worse than a longer table. But past
roughly 15 rows a flat table stops being scannable regardless of how short
each row is. At that point, group the Action Items table by **Owner**
instead of leaving it in chronological/session order -- each person can
then scan straight to their own commitments instead of reading everyone
else's:

```markdown
## Action Items

**Priya**
| Action | Due | From |
|---|---|---|
| Finalize the vendor shortlist | Friday | Session 2 |

**Dev**
| Action | Due | From |
|---|---|---|
| Ship the pricing page redesign | Wednesday | Session 1 |
| Review the API rate limits | TBD | Session 3 |
```

### Finding the session/day boundaries

Use `stats.possible_session_breaks` (large recorded gaps between segments)
and `stats.day_marker_hints` (textual mentions like "Day 2") as evidence,
not verdicts -- a hint can false-positive (someone saying "let's revisit in
a day or two" isn't a day boundary). Corroborate with the transcript's own
language: an explicit agenda, "let's move to the next session," "welcome
back from lunch," or a new day's greeting are all stronger signals than a
gap or keyword match alone. If the user handed over multiple files for one
event, treat each file as its own day by default.

### Multi-day specifics

The same hierarchical shape applies with "day" in place of "session": the
top-level rollup becomes a **cross-day executive summary**, each "Session"
in the Sessions list becomes a day, and Decisions/Action Items are
consolidated across *all* days. Two things need extra care at this scale:

- **Deduplicate across days.** If the same commitment is mentioned again on
  day 2 with no real change, that's the same action item, not a new row --
  update its Due date only if the transcript actually changed it.
- **Cross-day reversals matter more, not less.** A decision made on day 1
  and revisited on day 3 of a multi-day offsite is exactly the kind of thing
  a napkin summary would miss and a reader would be upset to find out about
  later -- extend the single-meeting reversal rule across the whole event,
  not just within one day's transcript.

## When the default isn't enough

If the user wants more after seeing the recap -- the full blow-by-blow, a
specific topic or session expanded, verbatim quotes for a contested point
-- go back to the source transcript and pull exactly that, rather than
having generated it up front on the chance it might be wanted. See SKILL.md
Step 4.
