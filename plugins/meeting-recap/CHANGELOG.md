# Changelog

## 0.2.0 -- 2026-08-10

Shared links and images are now collected, and images are actually looked at.
Ported from the `skills-stash` skill of the same name and adapted to this
plugin's chat merging and source provenance.

- **Shared links** (`stats.shared_links`) — every URL that reached the
  transcript or chat as text, with who shared it, what was said around it,
  `mention_count`, and a `likely_private_host` flag. Listed in a Shared Links
  table; **not** fetched by default, since opening every URL is slow, sends
  the user's links to third parties, and mostly returns login walls.
- **Shared images** (`stats.shared_images`) — image URLs, markdown/HTML image
  syntax, and attachment lines naming a file. Unlike links, these *are*
  opened by default and described by what they show: `checkout-v3.png` tells
  a reader nothing, and a design review is usually about the picture.
- **A `retrieval` field decides what's possible per image** — `local_path`
  (read it), `remote_url` (open it unless it looks org-internal), or
  `name_only` (a filename with no path or URL, which cannot be opened). An
  image that can't be opened still gets a row, marked unavailable and
  described only from what was said. `final-approved-layout.png` is not
  evidence that anything was approved — the same discipline as
  `UNASSIGNED`/`TBD`, applied to pixels.
- **Both are collected from chat as well as the transcript**, because that's
  where links and screenshots actually get pasted, and each entry carries the
  `source` of its segment so a chat-shared artifact stays `[chat]`-tagged.
  This does not breach the stats-purity rule: links and images are artifacts
  to look up, not measurements, so they still can't stretch a duration or
  bump a recap tier. Covered by tests both ways.
- **`image_mentions_without_reference`** — "I just dropped a screenshot in
  the chat" with no file attached. Weak evidence kept separate from real
  references, so the recap can say something visual was shared and isn't
  captured without ever describing an image nobody can see.

Fixed while porting, and the reason images work in Teams at all:

- **`_flatten_chat_text` was destroying the artifacts before extraction.**
  Teams chat bodies are HTML, and the tag strip ran over the whole message —
  so `<img src=...>` (the normal way an image reaches a meeting chat) and
  `<a href=...>` (a hyperlinked URL) were deleted outright. The loss was
  invisible: the message still read fine with the artifact gone. Images and
  anchors are now rewritten to their plain-text equivalents *before* the
  generic strip, with an autolinked bare URL (`<a href="U">U</a>`) collapsing
  to one mention rather than "U (U)".

## 0.1.0 -- 2026-08-09

Initial creation as a plugin, migrated from the `skills-stash` skill of the
same name. Skills-only: no MCP server, no dependencies, one stdlib-only
bundled script.

The summarization logic is carried over unchanged — the sliding ruler, the
decision-vs-discussion heuristics, the `UNASSIGNED`/`TBD` fallbacks, and the
recap templates all behave as they did.

Added in the same release: source acquisition, so the skill can be pointed at
a meeting rather than a transcript.

- **Microsoft 365 retrieval** (`references/sources.md`) — calendar search to
  identify the meeting (confirming when ambiguous), event read for the
  transcript URI and attendee roster, transcript read, best-effort Teams chat
  match, plus referenced files and email.
- **Chat merging** — `normalize_transcript.py` takes an optional `--chat` and
  `--chat-anchor`, merging chat into one chronological `segments` list where
  every segment carries a `source` of `transcript` or `chat`.
- **Stats purity** — every statistic (`duration_sec`, `word_count`,
  `speakers`, session breaks, day markers, and the recommended tier) is
  computed from transcript segments alone, so chat volume cannot push a
  meeting into a longer recap tier. Enforced in the parser and covered by
  tests rather than left as a documented rule.
- **Source provenance** — transcript and chat may source a decision or action
  item; files and email are context-only and can never be the sole basis for
  one. Anything not from the transcript is tagged in the output.
- **New stats fields** — `chat_message_count`, `chat_senders`,
  `chat_word_count`, and `chat_alignment` (`anchored` / `unanchored` /
  `none`), with warnings covering both the approximate-anchor and
  cannot-place cases.

Verified against a live Microsoft 365 tenant on 2026-08-09, which changed
several things from what the connector's documentation implied:

- **The chat id is derivable from the calendar event**, not heuristic — it's
  the thread id embedded in `onlineMeeting.joinUrl`, and the derived value
  matches `teams_list_chats` exactly. Topic/attendee/time matching is now
  only the fallback for events with no join URL.
- **`teams:///chats/{chatId}/messages`** (no message id) lists a chat's
  messages. The tool description documents only the single-message form.
- **Teams chat listings need handling the parser didn't have:** system-event
  messages (`messageType: "unknownFutureValue"` with an `eventDetail`) are
  now filtered out, and `bodyPreview` is accepted as a last-resort text
  field — flagged in the docs as truncated, so substantive messages should
  be read in full.
- **Transcript retrieval failed in three distinct ways**, all documented
  with the exact error text and the right response: Graph transcript access
  disabled tenant-wide, the user not being the meeting organizer, and
  `text/vtt` being rejected when reading a transcript out of SharePoint.
  That last one means even a transcript in the user's own OneDrive can't be
  read as a `.vtt`.

The practical consequence: on a tenant where Graph transcript access is
off, the connector still supplies calendar, chat, files, and email, but the
transcript itself has to come from a file or a paste. The skill says which
failure occurred instead of retrying.

Other known gaps: chat interleaving is offset by however long recording
lagged the scheduled start, and Google Meet transcripts are unreachable
without a Drive connector.
