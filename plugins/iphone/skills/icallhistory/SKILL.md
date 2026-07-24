---
name: icallhistory
description: >
  Query local Mac/iPhone call history — cellular phone calls, FaceTime Audio, and
  FaceTime Video — by date range, contact, or direction, and resolve numbers to
  contact names via the local AddressBook. Uses the iphone plugin's bundled
  calls_* MCP tools. Trigger on requests like "who called me", "what numbers
  have I called", "show my missed calls", "did I call [person]", "call history
  with [person]", or "correlate my calls with my texts". Read-only and
  local-only: no calls are placed, no data is written back, no analysis is
  performed on its own — tools return structured data for the caller to reason
  about. Only works for Claude running locally on macOS with Full Disk Access
  granted (not from a cloud/remote session) — this is the Mac/iPhone Continuity
  call log, not a generic telephony API.
---

# icallhistory

Read-only access to the Mac's local call history via the `iphone` plugin's bundled
`shadetree-ai-plugins-iphone` MCP server. Tools open
`~/Library/Application Support/CallHistoryDB/CallHistory.storedata` read-only,
query, close, and return structured data — no persistent process, no writes,
ever. This is a **separate database from iMessage** (`chat.db`); see
"Correlating with iMessage" below for how to use both together.

## Non-goals (read before using)

- **No placing calls.** There is no AppleScript/Automation equivalent here to the
  `imessage` skill's `imessage_send` — Apple doesn't expose a reliable way to
  originate a phone or FaceTime call by script, and a Mac can't place a cellular
  call at all without an iPhone relaying it. These tools are read-only, full stop.
- **No summarization or analysis.** Tools return data; deciding who to call back or
  what a pattern of calls means is the caller's job.
- **No writes to CallHistory.storedata.** Every tool opens the database read-only.
- **No scheduling/daemon.** On-demand only; there is no background watcher, no
  polling for inbound calls.
- **No voicemail content.** CallHistory.storedata records call metadata only (who,
  when, how long, answered or not) — it has no voicemail audio or transcripts.

## Calling the tools

Call this plugin's `calls_*` MCP tools directly (no Bash, no subprocess) — each
returns structured data, not a JSON string to parse.

### First time in a session: call `calls_doctor`

Reports whether CallHistory.storedata and the AddressBook are readable, the total
call count, the local date range, and how many of those calls were real cellular
phone calls (as opposed to FaceTime). If `call_history_db_readable` is false, tell
the user to grant **Full Disk Access** to the app running Claude (Claude Desktop,
or the terminal/IDE running Claude Code), then try again — this is the same grant
the `imessage` skill needs, not a separate one; see
[references/platform-issues.md](references/platform-issues.md). If `warnings`
flags zero phone calls or a shallow date range, see "Continuity Calling" below
before assuming the data doesn't exist — this is the single most common surprise
with this skill.

### Everyday tools

| Tool | Purpose |
|---|---|
| `calls_list(since=, until=, handle=, direction=, call_type=, missed_only=False, limit=100, offset=0)` | List calls newest-first, with `contact_name` resolved via the local AddressBook. Defaults to all local history if `since` is omitted. `direction` is `incoming`/`outgoing`; `call_type` is one of `phone`/`facetime_video`/`facetime_audio`/`third_party_app`. |
| `contacts(query=, handle=)` | Resolve name -> handle or handle -> name via the local AddressBook (shared with the `imessage` skill's tools). |

`since`/`until` accept ISO 8601 (`2026-06-30`, `2026-06-30T14:00:00`),
`today`/`yesterday`, or relative phrases like `7 days ago` — identical parsing to
the `imessage` skill, so the two compose cleanly when scoping both to the same
window.

`handle` accepts a phone number or an Apple ID email, in any of the formats the
AddressBook or CallHistory itself stores them in (loose digit-matching handles
punctuation differences like `(555) 123-4567` vs `+15551234567`).

Each call record:

```json
{
  "date_iso": "2026-07-10T14:32:05-04:00",
  "direction": "outgoing",
  "answered": true,
  "missed": false,
  "duration_seconds": 184,
  "call_type": "phone",
  "handle": "+15551234567",
  "contact_name": "Jane Doe",
  "service_provider": null
}
```

`missed` is `true` only for unanswered **incoming** calls (the same definition the
Phone/FaceTime apps' "Missed" list uses) — an unanswered outgoing call just has
`answered: false`, `direction: "outgoing"`.

## Correlating with iMessage

The `calls_*` tools and the `imessage` skill's tools live in the same MCP server
and share the same `contacts` tool and AddressBook, so to answer "everything with
Jane, calls and texts together," call both and merge on `handle`/`sender_handle`:

```
calls_list(handle="+15551234567", since="30 days ago")
imessage_search(handle="+15551234567", since="30 days ago")
```

Merge the two results yourself (sort by `date_iso`) — the two databases aren't
joined for you. If you only have a name, resolve it once with `contacts(query=NAME)`
to get the canonical handle to pass to both.

## Continuity Calling — read this before concluding "no calls found"

`CallHistory.storedata` only has **real cellular phone calls** if this Mac has
Continuity Calling ("Calls From iPhone") turned on — FaceTime -> Settings/Preferences
-> Calls From iPhone, Mac and iPhone on the same Apple ID and Wi-Fi network. Without
it, this database only has FaceTime Audio/Video calls placed from the Mac itself:
`calls_doctor`'s `phone_call_count` will be `0` and it'll warn. Even when on, it's not
an iCloud-backed continuous sync like iMessage — calls only relay while both devices
were actually near each other on the same network — so treat results as useful but
possibly incomplete, not an authoritative log of every call the phone made. See
[references/platform-issues.md](references/platform-issues.md) for detail.

## Known limitations

- `service_provider` is surfaced as a raw passthrough string — Apple doesn't publicly
  document its exact contents (carrier name vs. an `"iPhone"` literal for a
  Continuity-relayed call vs. a third-party app identifier all appear to be possible),
  so don't over-interpret it.
- Unrecognized `ZCALLTYPE` values (e.g. from newer macOS releases) surface as
  `other_<code>` rather than a guessed label — `call_type` can only filter on the four
  known names (`phone`, `facetime_video`, `facetime_audio`, `third_party_app`).
- Group FaceTime calls aren't specifically handled or tested in this phase — this
  skill targets 1:1 call records.
- Contact resolution reflects the AddressBook **right now**; a call from a number
  since removed from Contacts resolves to a raw number, same graceful-degradation
  behavior as the `imessage` skill.
- Pre-macOS-13 (Ventura) systems encrypted CallHistory.storedata with a key pulled
  from Keychain; this plugin assumes the unencrypted Ventura+ format and does not
  attempt decryption.

See [references/schema.md](references/schema.md) for the `ZCALLRECORD` table layout
and the Core Data timestamp conversion, and
[references/platform-issues.md](references/platform-issues.md) for TCC permissions
and the Continuity Calling caveat in full.
