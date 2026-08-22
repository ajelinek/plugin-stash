---
name: meeting-recap
description: >
  Turns a meeting/call transcript (pasted text, file, or VTT/SRT/Otter/Zoom/
  Teams export) into a short, skimmable recap: decisions, action items
  (owner + due date), topics, and open questions -- readable in under a
  minute. Trigger on "summarize this meeting/call/transcript", "write
  meeting notes/minutes", "what did we decide", "give me the action
  items", "recap this standup/sync/1:1/interview", or when handed a
  transcript. Also triggers when the user names a meeting instead of
  supplying one ("recap this morning's architecture sync", "what did we
  decide in the Teams call with the vendor") -- with a Microsoft 365
  connector available it finds the meeting on the calendar and pulls the
  Teams transcript, meeting chat, shared files, and related email itself.
  Collects the links and images shared during the call -- including those
  pasted in the meeting chat -- into their own lookup tables, opening the
  images it can reach and describing what they actually show.
  Distinguishes real decisions from mere discussion, never fabricates
  names/owners/deadlines (flags UNASSIGNED/TBD/"unclear" instead of
  guessing), never describes an image it couldn't open, tags anything
  sourced outside the transcript, and scales
  structure to meeting length via a sliding ruler -- same rigor from a
  10-minute standup to a multi-day offsite, no rigid template. Not for
  behavioral coaching feedback (filler words, speaking-time balance) --
  strictly what happened, what was decided, and what's owed.
---

# Meeting Recap

## What this is

There is no shortage of "summarize this meeting" skills already. What almost
none of them do: enforce a real length limit (most rely on "be concise,"
which isn't a rule a model can actually be held to), or signal uncertainty
about what they extracted (a guessed owner name and a quoted one end up
looking equally confident). This skill's whole edge is discipline -- a short
recap by default, hard fallback tokens instead of invented names or dates,
and explicit uncertainty markers on anything inferred rather than stated.
It is not the tool for a coaching-style breakdown of how someone communicated
(see Non-goals) -- it answers one question: what happened, what got
decided, and what's owed to whom.

## Step 0: If no transcript was supplied, go get one

Skip this entirely when the user pastes a transcript or points at a file —
that's the normal case and Step 1 handles it.

When the user instead *names* a meeting ("recap this morning's architecture
sync"), read [references/sources.md](references/sources.md) and follow it.
In brief: with a Microsoft 365 connector available, find the meeting via
calendar search (confirming which one when there's more than one candidate),
read the event for its `meetingTranscriptUrl` and attendee roster, read the
transcript, best-effort match the Teams meeting chat, and pull referenced
files and email. Everything lands in a temp directory for Step 1 to read.

With no transcript and no connector, ask for a file or a paste. Do not
assemble a stand-in recap from calendar metadata and email threads — an
invitation and a follow-up are not a record of what was said.

## Step 1: Normalize the transcript

Accepts pasted text in the conversation, a file path, or a known export
format (WebVTT `.vtt`, SubRip `.srt`, or a plain "Speaker: text" paste from
Zoom/Teams/Otter/Granola/etc.). Run the bundled parser first:

```bash
python3 scripts/normalize_transcript.py <path-or-'-'-for-stdin>
```

Paths are relative to this SKILL.md's own directory. Add
`--chat <path> --chat-anchor <ISO 8601>` when Step 0 also retrieved a
meeting chat — see [references/sources.md](references/sources.md) for what
the anchor is and why an approximate one is still worth using.

It prints one JSON object: `format_detected`, a flat `segments` list
(`source`, `speaker`, `timestamp_sec`, `end_sec`, `text`), and `stats`.

Every segment carries a **`source`** of `transcript` or `chat`. Chat is
merged into the same chronological list so a message lands beside what was
being said at the time — but it stays labeled, because a typed commitment
and a spoken one are not the same evidence. Carry that distinction through
extraction; see the provenance rules in
[references/extraction-rules.md](references/extraction-rules.md).

Check `stats` before doing anything else:

- **`recommended_tier`** -- the sliding-ruler tier (Micro through Multi-day)
  that sets this recap's structure and length budget; see the ruler in
  [references/output-template.md](references/output-template.md). Its
  `basis` field says whether `duration_sec` came from real recorded
  timestamps (`recorded_end_times`/`start_times_only`) or a word-count
  estimate (`estimated_from_word_count`) -- treat an estimated tier as a
  starting point, not a precise cutoff.
- **Meeting length is computed from the transcript's own first recorded
  start time through its last recorded end time** (`start_sec`/`end_sec`/
  `duration_sec`) -- not word count, whenever the format actually records
  cue end times (WebVTT/SRT do). Word count is only used to *estimate*
  duration as a last resort, when there are no timestamps at all.
- **`possible_session_breaks`** / **`day_marker_hints`** -- evidence of
  natural session or day boundaries (a large recorded gap, an explicit "Day
  2" mention). Hints, not verdicts -- corroborate with the transcript's own
  language before treating a meeting as multi-session/multi-day. See
  "Finding the session/day boundaries" in
  [references/output-template.md](references/output-template.md).
- **`warnings`** -- surface these to the user plainly (very short
  transcript, no speaker labels detected, no structural segmentation found,
  duration estimated rather than recorded, chat alignment approximate or
  unknown) rather than silently producing a confident-looking recap anyway.
- **`chat_alignment`** -- `anchored` means chat is interleaved on the
  transcript's clock but offset by however long recording lagged the
  scheduled start, so adjacency is approximate; `unanchored` means chat
  couldn't be placed at all and was appended, so its position carries **no**
  information. In neither case may the recap assert that a chat message was
  responding to a particular remark. `chat_senders` lists who typed, kept
  separate from `speakers` (who talked).
- **`shared_links`** -- every URL that made it into the transcript or chat as
  text, with who shared it, when, its `source` (`transcript`/`chat`), and the
  words said around it. See "Links shared in the meeting" in
  [references/extraction-rules.md](references/extraction-rules.md) for which
  ones earn a row and the rules on opening them (short version: don't, by
  default).
- **`shared_images`** -- every image reference (an image URL, markdown/HTML
  image syntax, or an attachment line naming a file), with the same
  provenance fields plus a `retrieval` field saying whether it can actually
  be opened. **`image_mentions_without_reference`** separately catches "I
  dropped a screenshot in the chat" with no file to go with it. These drive
  Step 2.
- **Every statistic above is computed from transcript segments only** --
  chat can't stretch `duration_sec`, inflate `word_count`, invent a session
  break, or bump the tier. That's enforced in the parser, so trust it: a
  meeting where fifty links got pasted is not thereby a longer meeting.
  Shared links and images are the deliberate exception: they're collected
  from chat too, because that's where people actually paste them. They're
  artifacts to look up, not measurements, so they can't move a tier.
- If `format_detected` looks wrong given what you can actually see in the
  raw text, the parser is a heuristic and can miss unusual export formats --
  fall back to reading the raw transcript directly rather than trusting a
  bad parse.

## Step 2: Look at the images that were shared

Unlike links, images are opened by default -- a filename like
`checkout-v3.png` tells the reader nothing, and a design review or bug
report is often *about* the picture. Work through `stats.shared_images` by
its `retrieval` field:

- **`local_path`** -- read the file and describe what you see.
- **`remote_url`** -- open it, unless the URL looks like it belongs to the
  user's organization rather than the open web. This is the common case
  here: a Teams-shared image lives on a tenant file host
  (`*.sharepoint.com`, `*.slack-files.com`), and its URL can carry a
  workspace or customer name. Ask before opening those.
- **`name_only`** / **`inline_data`** -- you cannot open these. Describe them
  from the transcript or chat alone and mark them as unavailable. Never infer
  content from a filename.

If an image won't open, say so and keep the description drawn from what was
said -- a failed fetch is not license to guess. An image shared in chat
carries `source: "chat"` and is tagged `[chat]` in the output like any other
chat-sourced content. The full rules, including what earns a row and how to
handle an image that contradicts what was said about it, are in "Images
shared in the meeting" in
[references/extraction-rules.md](references/extraction-rules.md).

Skip this step entirely when `shared_image_count` is 0 and
`image_mentions_without_reference` is empty.

## Step 3: Extract

Read [references/extraction-rules.md](references/extraction-rules.md) and
apply it. In brief, it covers:

- Telling an actual decision apart from mere discussion or a floated idea
  (and handling a decision that gets reversed later in the same meeting)
- What makes an action item genuinely actionable, and the hard
  `UNASSIGNED`/`TBD` fallbacks used instead of ever guessing a name or date
- Clustering topics into a handful of meaningful groups instead of one
  bucket per utterance
- Signaling confidence explicitly on anything inferred rather than stated,
  instead of presenting a guess with the same certainty as a quote
- Which shared links and images earn a row, describing a link from what was
  said rather than from its URL slug, describing an image from what you
  actually saw, and the hard line against describing one you couldn't open
- Which sources may support which claims (transcript and chat can source an
  item; files and email are context-only), and tagging anything that didn't
  come from the transcript
- Adapting which sections apply to the meeting's actual type and size,
  rather than forcing one rigid template on everything
- Working through a very long transcript without letting the *output* grow
  just because the *input* did -- and for a half-day-plus meeting, treating
  each session/day as its own extraction pass feeding a consolidated
  top-level rollup, per "Multi-session and multi-day meetings"
- A cheap final self-check against the source transcript before presenting

## Step 4: Write the recap

Use the exact template(s) and the hard rules in
[references/output-template.md](references/output-template.md) -- read it
before writing the first recap. The short version: look up the tier from
`stats.recommended_tier` (Micro through Multi-day), then follow that tier's
row on the sliding ruler. Every tier starts with a mandatory TL;DR, uses
bullets and tables (never paragraphs), and stays within its entry-point
word budget; Half-day and above add a short session/day index above a
per-session detail section instead of one long flat document. The point
that holds at every tier: whoever stops reading after the TL;DR should
still know what happened and what's owed to them.

## Step 5: Offer next steps -- don't do them unasked

Stop after presenting the recap. If the user wants more, two things are
reasonable to *offer*, not to do automatically:

- **Expand a section, or give the full blow-by-blow.** Go back to the
  source transcript for exactly what's requested rather than having
  pre-generated it on the chance it might be wanted.
- **Open the shared links and summarize what's behind them.** The recap
  lists links with what was said about them; actually fetching them is a
  separate, slower job the user should get to choose (and one that sends
  their URLs to whatever's on the other end). See the fetching rules in
  [references/extraction-rules.md](references/extraction-rules.md) for the
  narrow case where a link is load-bearing enough to open unasked.
- **Go deeper on a shared image.** The recap gives each image one phrase.
  Transcribing the text in a screenshot, walking through a mockup, or
  reading numbers off a chart is a bigger job the user should ask for --
  and worth offering explicitly when an image was clearly load-bearing.
  If images were referenced but couldn't be opened, this is the moment to
  ask the user to share the files.
- **Draft a follow-up email or message.** Draft it, show the user the exact
  text, and get explicit confirmation before sending it anywhere -- this
  skill never sends anything on its own behalf.

## Non-goals

- **Not a behavioral/communication coaching tool.** Filler words, conflict
  avoidance, talk-time balance, and leadership feedback are a different job
  from "what happened and what's owed" -- and answering both at once tends
  to produce a long, quote-heavy report when the user asked for a short one.
- **Not a transcription tool.** This skill expects a transcript (or raw
  notes) as input, whether supplied directly or retrieved in Step 0. If the
  user only has an audio/video file, get it transcribed first, then hand the
  transcript here. A meeting that was never transcribed doesn't get a recap
  reconstructed from its invitation and follow-up email.
- **Not an image-analysis report.** Images shared in the meeting *are*
  collected and opened (Steps 1-2), but each one earns one short phrase in a
  lookup table -- what it shows and why it came up. A full walkthrough of a
  mockup, OCR of a slide deck, or a design critique is a different job the
  user can ask for afterwards (Step 5). Non-image attachments -- a PDF, a
  spreadsheet, a deck -- are handled as context under the source-provenance
  rules, not opened as pictures.
- **Can only see images the transcript or chat actually references.** A
  screenshot that lives inside the meeting platform and left no URL, path, or
  filename behind is unreachable. The recap says one was shared and that it
  isn't captured, rather than hunting for it or describing it from a filename.
- **Not a research tool.** Files and email are pulled to resolve what the
  meeting referred to, not to widen its subject matter. If something appears
  only in a document or a thread and nobody raised it in the meeting, it
  doesn't belong in the recap -- the user asked what happened in a room, not
  for everything adjacent to it.
- **Not a bulk/batch tool.** One meeting, one recap. Processing a whole
  folder of historical transcripts at once is a different, corpus-scale
  problem than this skill is built for.
- **Not a PM-tool integration.** No auto-posting to Jira/Linear/Asana/
  Notion -- output is plain markdown the user can paste anywhere themselves.
- **Doesn't track action-item status after the meeting.** A single
  transcript is a snapshot; whether something later got done isn't
  something this skill can know, so it doesn't claim to.

## Known limitations

- Attribution and structure quality depend on the transcript itself -- one
  with no speaker labels will produce meaningfully less reliable ownership,
  which `normalize_transcript.py`'s `warnings` flags up front rather than
  hiding.
- The format parser is a heuristic covering the common export shapes
  (WebVTT, SRT, labeled paste), not a universal parser for every transcript
  tool in existence -- if `format_detected` looks wrong, read the raw text
  directly instead of trusting it blindly.
- Duration is only as good as the transcript's own timestamps. WebVTT/SRT
  record a real end time per cue, so their duration is exact; a plain
  timestamp-prefixed paste only records when each turn *started*, so
  duration there is a start-to-start span (a slight underestimate); with no
  timestamps at all, duration is a rough word-count estimate --
  `duration_basis` always says which case applies.
- Session/day boundary detection (`possible_session_breaks`,
  `day_marker_hints`) is heuristic evidence, not a guarantee -- corroborate
  with the transcript's own language before restructuring around it.
- Link and image collection only sees what survived into the transcript or
  chat *as text*. A URL read aloud is transcribed as words ("acme dot com
  slash pricing") and isn't detected; `likely_private_host` only flags
  network-private hosts, not the more common auth-walled page on a public
  domain. For images specifically: an image URL with no file extension is
  only caught when it arrives as markdown or an `<img>` tag, and an
  undelimited filename must not contain spaces, so a bare
  `Screenshot 2025-03-04 at 11.02.13.png` is only found when the export
  wraps it in brackets, parentheses, or quotes.
  `image_mentions_without_reference` is the backstop that at least flags
  *that* an image was shared when the reference itself is missing.
- No cross-meeting comparison or trend-tracking; a multi-day event is
  handled as one connected rollup, not a history across separate,
  unrelated meetings over time.
- **Transcript retrieval is frequently blocked, and not by anything this
  skill can fix.** Three verified failure modes: the tenant admin hasn't
  enabled Graph access to Teams transcripts (tenant-wide), the user didn't
  organize the meeting (common for cross-tenant invites), or the file is a
  `.vtt` in SharePoint, whose MIME type the connector refuses. In all three
  the answer is to ask for the file. See the table at the top of
  [references/sources.md](references/sources.md) -- report which one it was
  rather than retrying.
- **Chat linking is exact when a join URL exists**, since the chat id is
  embedded in it. Only when there's no join URL does the match fall back to
  topic/attendee/timing heuristics -- and when that isn't confident, the
  recap is built from the transcript alone and says chat was excluded.
- **Chat interleaving is approximate.** Transcript times are relative to
  when recording started; chat times are absolute. The anchor bridging them
  is the scheduled start, which usually precedes recording by a minute or
  two, so a chat message sitting next to a remark is never proof it was
  responding to it.
- **Google Meet transcripts aren't reachable.** They live in Google Drive
  and there's no Drive connector -- a Google Calendar event confirms the
  meeting happened but offers no path to what was said. Ask for the file.
- Connector coverage is Microsoft 365 only. Every other platform goes
  through the same file/paste front door, which is unchanged and fully
  supported.

## Supporting files

| File | Purpose |
| --- | --- |
| `references/sources.md` | Step 0 in full: the Microsoft 365 chain (calendar search → event read → transcript URI → best-effort chat match → files/email), the chat anchor, temp-file handling, and what isn't reachable |
| `references/extraction-rules.md` | Decision-vs-discussion heuristics, action-item rules, source provenance tiers, topic clustering, shared-link and shared-image selection and the fetching policy, confidence signaling, meeting-type adaptivity, long-transcript and multi-session/multi-day handling, the pre-presentation self-check |
| `references/output-template.md` | The sliding ruler, the flat and hierarchical recap templates, source tagging, the reasoning behind each rule, and worked examples |
| `scripts/normalize_transcript.py` | Stdlib-only transcript format detection, normalization, optional chat merge with source tagging, real start/end-time duration, session/day-break detection, shared-link and shared-image extraction, and tier recommendation (VTT/SRT/labeled/plain) |
