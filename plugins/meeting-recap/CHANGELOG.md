# Changelog

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
