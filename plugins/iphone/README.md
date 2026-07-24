# iphone

Read (and send) local iMessage history, read local Mac/iPhone call history
(cellular + FaceTime), and resolve Contacts — all via one bundled local FastMCP
server. Everything stays on this Mac: reads open the relevant database
read-only, query, close, and return structured data; the only thing that ever
leaves the machine is a sent iMessage, delivered through Messages.app via
AppleScript (never over the network directly from this plugin).

macOS only. Needs Full Disk Access (for reading `chat.db`,
`CallHistory.storedata`, and the local AddressBook) and, for sending, a
separate Automation permission for Messages.app — see
`skills/imessage/references/platform-issues.md` and
`skills/icallhistory/references/platform-issues.md`.

## Prerequisites

The machine needs `uv` installed and on `PATH`
(https://docs.astral.sh/uv/getting-started/installation/).

## Install

In Claude Desktop: **Customize → Plugins → (+) → Add marketplace**,
enter `ajelinek/shadetree-ai-plugins`, then install `iphone` from the list.
(Equivalent commands also work in a Desktop or Cowork chat window:
`/plugin marketplace add ajelinek/shadetree-ai-plugins` then
`/plugin install iphone@shadetree-ai-plugins`.)

Ask Claude to "check iMessage doctor" or "check my call history" to exercise
the tools once installed.

## What's inside

- `.claude-plugin/plugin.json` / `.mcp.json` — plugin + MCP server manifest
  (one server, `shadetree_ai_plugins_iphone`).
- `fastmcp.json` — local dev only (`fastmcp run fastmcp.json`), not used by the
  installed plugin.
- `skills/imessage/SKILL.md` — usage guidance for the `imessage_*` tools
  (read/send iMessage, trusted contacts).
- `skills/icallhistory/SKILL.md` — usage guidance for the `calls_*` tools
  (read-only call history).
- `src/shadetree_ai_plugins_iphone/server.py` — the FastMCP server: `imessage_*` tools,
  `calls_*` tools, and a shared `contacts` tool.
- `src/shadetree_ai_plugins_iphone/contacts.py` / `dates.py` — shared AddressBook
  resolution and date-argument parsing used by both tool families.
- `src/shadetree_ai_plugins_common` — symlink to the repo's shared helpers (logging,
  `~/.shadetree-ai-plugins/iphone/` state dir for trusted contacts).
- `tests/test_server.py` — in-memory tests against synthetic chat.db /
  CallHistory.storedata / AddressBook fixtures (`uv run pytest` from repo
  root — no real macOS databases required).

## Tools

| Server | Tools |
|---|---|
| `imessage_*` | `imessage_doctor`, `imessage_chats`, `imessage_recent`, `imessage_messages`, `imessage_search`, `imessage_resolve_chat`, `imessage_send` (preview-then-confirm), `imessage_get_attachment`, `imessage_trusted_list`/`_add`/`_remove`/`_suggest` |
| `calls_*` | `calls_doctor`, `calls_list` |
| shared | `contacts` |

See the two SKILL.md files for full usage guidance, safety rules around
`imessage_send`, and known platform caveats.
