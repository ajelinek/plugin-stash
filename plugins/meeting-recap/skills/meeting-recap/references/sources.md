# Getting the meeting's material

This covers the case where the user *doesn't* hand over a transcript — they
name a meeting ("recap this morning's architecture sync") and expect the
material to be found for them.

The front door is unchanged and comes first: a pasted transcript, a file
path, or an export file is always the simplest path, and nothing here is
needed for it. Everything below applies only when there's no transcript in
hand and a Microsoft 365 connector is available.

If there's no transcript and no connector, say so plainly and ask for a file
or a paste. Don't reconstruct a meeting from calendar metadata and email —
an invitation and a follow-up thread are not a record of what was said.

## Check this first: transcript retrieval is often blocked

**Verified against a live tenant on 2026-08-09.** Calendar, chat, files, and
email all work. **Graph transcript retrieval frequently does not**, for
reasons outside this skill's control. Know the three failure modes before
promising a user a recap:

| What you'll see | Cause | What to do |
|---|---|---|
| `GraphAccessToTranscriptsDisabled` | The tenant admin has not enabled Graph API access to Teams transcripts. Tenant-wide; nothing per-meeting will fix it. | Ask for the `.vtt` file. Mention their M365 admin can enable it if they want this to work. |
| `3003: User does not have access to lookup meeting` | The signed-in user didn't organize the meeting — commonly an invite from another tenant. | Ask for the file, or ask the organizer to export it. |
| `MIME type 'text/vtt' is not allowed` | Reading a `.vtt` straight out of SharePoint/OneDrive. `text/vtt` isn't in the connector's allowed list. | The file must be converted to `.txt`/`.md`, or downloaded and handed over locally. |

That last one matters more than it looks: it means **even a transcript
sitting in the user's own OneDrive can't be read as a `.vtt`**. The
SharePoint fallback only works if the file has a permitted extension.

So when transcript retrieval fails, don't keep trying variations. Say which
of the three it was, and ask for the file. The file/paste front door works
every time and needs none of this.

## The chain, in order

Each step feeds the next. Don't skip ahead: the transcript URI is only
obtainable from a calendar event read, and the chat id is derived from that
same read.

### 1. Find the meeting

`outlook_calendar_search` with the user's words as `query` and a date window
from whatever they said ("this morning", "last Tuesday") via
`afterDateTime`/`beforeDateTime`. If they named no time at all, search the
last 7 days and prefer the most recent match.

- **Exactly one match** — proceed.
- **Several matches** — show them (subject, start time, organizer) and let
  the user pick before fetching anything. This is also the moment they can
  catch a wrong-meeting guess, which is much cheaper than catching it in a
  finished recap.
- **No matches** — say so and ask for a file. Don't widen the search
  repeatedly hoping something turns up.

Note the returned `start`/`end` are `{dateTime, timeZone}` pairs, where
`dateTime` is wall-clock in the named zone. Don't reinterpret it as UTC.

### 2. Read the event

`read_resource` on `calendar:///events/{eventId}`. This gives:

- **`meetingTranscriptUrl`** — pass it to step 3 **verbatim**. It contains an
  opaque base64url token encoding the join URL; it is not something to
  construct, decode, or tidy up. For a recurring meeting it arrives with
  `?start=…&end=…` already appended, scoping it to this occurrence — another
  reason not to edit it. (If you ever build one by hand for a series and
  omit those, you get the most recent transcripts of the *series*, capped,
  with nothing announcing the mix-up.)
- **`onlineMeeting.joinUrl`** — keep it. Step 4 derives the chat id from it.
- **The attendee roster** — full names, addresses, and `responseStatus`.
  It's how a partial first name in the transcript resolves to a real person,
  and it tells you who was invited but never spoke.
- **Organizer, subject, scheduled start/end** — the recap's title and date
  line, and the anchor for step 5.

If there's no `meetingTranscriptUrl`, the meeting wasn't transcribed. Say
that and stop — don't substitute chat or email and present the result as a
recap of the meeting.

### 3. Read the transcript

`read_resource` on the `meeting-transcript:///events/{joinUrlToken}` URI from
step 2, exactly as it was given to you.

Expect this to fail more often than it succeeds — see the three failure
modes at the top of this file, check which one you got, report it in those
terms, and ask for the file. Don't retry with a reconstructed URI.

On success, write the result to a file in a temp directory (see "Files on
disk" below) so the parser can read it and Step 4 follow-ups can go back to
the source without re-fetching.

### 4. Find the meeting chat

**The chat id is derivable from the event — this is exact, not a guess.**
A Teams meeting's chat id is the thread id embedded in
`onlineMeeting.joinUrl`: take the segment between `/meetup-join/` and the
following `/0?`, and URL-decode it.

```
joinUrl:  .../meetup-join/19%3ameeting_YzZkNGEzNTEt…%40thread.v2/0?context=…
chat id:  19:meeting_YzZkNGEzNTEt…@thread.v2
```

Verified against a live tenant: the derived id matches the one
`teams_list_chats` reports, exactly. Use this first — it's deterministic,
costs no extra call, and can't select the wrong meeting's chat.

Fall back to matching only when there's no `joinUrl` (a non-Teams event, or
an event whose online meeting was removed). Then `teams_list_chats` gives
meeting chats with a `topic` and member list; match on topic ≈ event
subject, **and** member overlap with the attendee roster, **and** time
proximity. When that match isn't confident and singular, don't guess:
produce the recap from the transcript alone and say plainly that chat wasn't
included and why. A recap silently built on the wrong meeting's chat is far
worse than one that admits a missing source.

`chat_message_search` with a date window is a last resort — it scans at most
50 chats and 50 messages each and flags partial results only via a prefixed
note, so in a busy tenant it's quietly incomplete.

#### Reading the messages

`read_resource` on **`teams:///chats/{chatId}/messages`** — no message id —
lists the chat's messages. (The tool's own description documents only the
single-message form; the list form works.)

Two things to handle, both seen in real chats:

- **Filter out system events.** Meeting chats are full of "X joined", "call
  ended", "recording started" entries, carrying
  `messageType: "unknownFutureValue"` and an `eventDetail`. The parser drops
  these, but don't waste reads on them either.
- **`bodyPreview` in the listing is truncated** with an ellipsis. For any
  message with real content, follow up with `read_resource` on that
  message's own URI to get the full `body.content`. The parser accepts
  either shape — flat `from` plus `bodyPreview` from a listing, or
  `from.displayName` plus `body.content` from a full read — but a truncated
  message can silently lose the commitment you were pulling chat for.

### 5. Normalize, with chat merged in

Write the chat messages to a JSON file — the parser reads Microsoft Graph's
own shape (`{"messages": [...]}` with `createdDateTime`, `from.user.displayName`,
and an HTML `body.content`), so they can go in roughly as they came out.

```bash
python3 scripts/normalize_transcript.py <transcript-file> \
    --chat <chat-file> \
    --chat-anchor <ISO 8601 datetime of transcript second 0>
```

**The anchor is what makes interleaving possible.** Teams transcript cue
times are relative to when *recording* started, while chat messages carry
absolute wall-clock times; the anchor is what puts them on one clock.

Use the event's scheduled start as the anchor, and understand what you're
accepting: recording usually starts a minute or two after the scheduled
time, so every chat message is offset by that difference. The parser emits a
warning saying exactly this. Treat adjacency as approximate — a chat message
appearing next to a remark is *not* proof it was responding to it, and the
recap must not claim otherwise.

Without an anchor (or against a transcript with no timestamps) chat is
appended after the transcript rather than interleaved, `chat_alignment` comes
back `unanchored`, and the ordering carries no meaning at all.

### 6. Files and email

Pull both, then read the provenance rules in
[extraction-rules.md](extraction-rules.md) before letting either touch the
recap. They are **context-only**: they resolve a name, a link, an acronym, or
what "the deck" refers to. Neither can be the sole basis for a decision, an
action item, or an open question, no matter how clearly it reads.

- **Files** — `sharepoint_search` for documents referenced in the meeting,
  then `read_resource` on `file:///{driveId}/{itemId}`. Word, PowerPoint,
  Excel, PDF, and plain text/markdown all read fine; `.vtt` does not (see
  the table at the top).
- **Email** — `outlook_email_search` around the meeting window and its
  attendees.

The failure mode to watch for: a thread that merely happened near the
meeting in time gets absorbed, and the recap starts describing a wider world
than the meeting covered. If something appears only in email or a file and
was never mentioned in the meeting, it does not belong in the recap.

## Files on disk

Write fetched transcript and chat to a session temp directory (`$TMPDIR` on
macOS, `/tmp` otherwise) with a name identifying the meeting and date, e.g.
`meeting-recap-20260807-architecture-sync.vtt`.

These are transit files, not artifacts: they can vanish on reboot. That's
fine for the session — the parser needs a path, and Step 4 follow-ups need
the source to go back to.

After presenting the recap, **offer** to save the raw transcript somewhere
durable. Don't do it unasked, and don't write meeting transcripts into the
user's working directory, where they're one `git add -A` away from being
committed.

## What this doesn't cover

**Google Meet is not reachable.** Meet transcripts are written to Google
Drive, and there's no Drive connector here — a Google Calendar event will
tell you the meeting happened but gives no path to what was said. If the
meeting was on Meet, ask for the transcript file; the user can export it from
Drive themselves.

The same logic applies to any other platform: if a connector can't produce
the actual transcript, the answer is to ask for a file, not to assemble a
substitute from whatever metadata is reachable.
