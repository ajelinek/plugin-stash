"""FastMCP server bundled with the shadetree-ai-plugins 'iphone' plugin.

One process, two tool families, sharing Contacts/date-parsing code:

- `imessage_*` — read (and send) the local iMessage database
  (~/Library/Messages/chat.db). Reads open it read-only, query, close, and
  return structured data — no persistent watcher, no writes to chat.db ever.
  `imessage_send` never touches chat.db either: it hands text and a
  chat_guid to `osascript`, which tells Messages.app to deliver it via
  AppleScript.
- `calls_*` — read-only access to the Mac's local call history
  (~/Library/Application Support/CallHistoryDB/CallHistory.storedata):
  cellular phone calls (only present with Continuity Calling enabled) and
  FaceTime Audio/Video calls placed from this Mac. No way to place calls —
  Apple doesn't expose that via script.
- `contacts` — shared macOS AddressBook (Contacts) name<->handle resolution,
  used by both families above.

See skills/imessage/references/ and skills/icallhistory/references/ for the
chat.db/CallHistory.storedata schemas, the attributedBody NULL-text decode,
and known platform caveats (TCC permissions, iCloud sync gaps, Continuity
Calling limits) this module works around.
"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from shadetree_ai_plugins_common import get_logger, state_dir

from .contacts import (
    address_book_dbs,
    address_book_sources_dir,
    find_handles_by_name,
    load_address_book,
    normalize_digits,
    resolve_handle_to_name,
)
from .dates import APPLE_EPOCH_OFFSET, iso, parse_when, until_boundary

logger = get_logger("shadetree-ai-plugins-iphone")

mcp = FastMCP(
    name="shadetree-ai-plugins-iphone",
    instructions=(
        "Local, read-only (and for imessage_send, send-capable) access to this Mac's "
        "iMessage history, Mac/iPhone call history, and Contacts. All data stays local: "
        "no network calls except imessage_send, which delivers through Messages.app via "
        "AppleScript, never over the network directly. imessage_send is a real, "
        "irreversible action — call it with confirmed=false first to preview the "
        "resolved recipient and exact text, get the user's explicit go-ahead, then call "
        "again with confirmed=true to actually deliver."
    ),
    mask_error_details=False,
)


# --------------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------------

def chat_db_path() -> str:
    return os.environ.get("SHADETREE_AI_PLUGINS_IPHONE_CHAT_DB_PATH") or str(
        Path.home() / "Library/Messages/chat.db"
    )


def callhistory_db_path() -> str:
    return os.environ.get("SHADETREE_AI_PLUGINS_IPHONE_CALLHISTORY_DB_PATH") or str(
        Path.home() / "Library/Application Support/CallHistoryDB/CallHistory.storedata"
    )


def trusted_contacts_path() -> str:
    override = os.environ.get("SHADETREE_AI_PLUGINS_IPHONE_TRUSTED_CONTACTS_PATH")
    if override:
        return override
    return str(state_dir("iphone") / "trusted_contacts.json")


def attachments_root() -> Path:
    override = os.environ.get("SHADETREE_AI_PLUGINS_IPHONE_ATTACHMENTS_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / "Library/Messages/Attachments").resolve()


# --------------------------------------------------------------------------------
# Database access (read-only, with a short retry for transient "database is
# locked" errors — chat.db is under WAL and can be mid-checkpoint while
# Messages.app is running; this is not a permissions problem and clears in
# well under a second in practice)
# --------------------------------------------------------------------------------

class DbUnavailable(Exception):
    pass


_LOCK_RETRY_DELAYS = (0.15, 0.3)


def _open_readonly_db(db_path: str, probe_sql: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise DbUnavailable(f"{os.path.basename(db_path)} not found at {db_path}.")
    last_exc: Exception | None = None
    for delay in (0.0, *_LOCK_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            conn.execute(probe_sql)
            return conn
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                break
    raise DbUnavailable(
        f"Cannot read {db_path}: {last_exc}. Grant Full Disk Access to the app running this "
        f"MCP server (Claude Desktop, or the terminal/IDE running Claude Code) -- System "
        f"Settings -> Privacy & Security -> Full Disk Access. If Full Disk Access was already "
        f"granted and this just started failing after a Claude update, macOS may have silently "
        f"revoked it: Claude Code's embedded CLI binary path changes with each version, so "
        f"re-add the exact current binary under Full Disk Access after updating."
    ) from last_exc


def open_chat_db() -> sqlite3.Connection:
    return _open_readonly_db(chat_db_path(), "SELECT 1 FROM message LIMIT 1")


def open_calls_db() -> sqlite3.Connection:
    return _open_readonly_db(callhistory_db_path(), "SELECT 1 FROM ZCALLRECORD LIMIT 1")


# --------------------------------------------------------------------------------
# Apple epoch conversions — chat.db stores nanoseconds since 2001-01-01,
# CallHistory.storedata (a Core Data store) stores plain seconds. Different
# units, same epoch; keep the two conversions separate rather than one
# "clever" shared function that has to branch on which database called it.
# --------------------------------------------------------------------------------

def apple_ns_to_dt(ns: int | None):
    if not ns:
        return None
    from datetime import datetime

    return datetime.fromtimestamp(ns / 1_000_000_000 + APPLE_EPOCH_OFFSET).astimezone()


def dt_to_apple_ns(dt) -> int:
    return int((dt.timestamp() - APPLE_EPOCH_OFFSET) * 1_000_000_000)


def core_data_ts_to_dt(seconds: float | None):
    if seconds is None:
        return None
    from datetime import datetime

    return datetime.fromtimestamp(seconds + APPLE_EPOCH_OFFSET).astimezone()


def dt_to_core_data_ts(dt) -> float:
    return dt.timestamp() - APPLE_EPOCH_OFFSET


# --------------------------------------------------------------------------------
# attributedBody (NSArchiver typedstream) decoding
#
# macOS 14+ sometimes leaves message.text NULL and stores the text only inside the
# attributedBody blob, an NSKeyedArchiver/NSArchiver-encoded NSAttributedString.
# This marker-scan heuristic (locate the 0x01 0x2B type marker, or fall back to
# scanning past a literal "NSString" class reference) was chosen after comparing
# two independent published implementations byte-for-byte against five real
# reference blobs — see skills/imessage/references/attributed-body.md for the
# writeup. This is a best-effort heuristic parser for the common single-string
# case, not a full typedstream reader.
# --------------------------------------------------------------------------------

def _read_length(blob: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(blob):
        return -1, offset
    first = blob[offset]
    if first < 0x80:
        return first, offset + 1
    if first == 0x81:
        if offset + 3 > len(blob):
            return -1, offset
        return int.from_bytes(blob[offset + 1 : offset + 3], "little"), offset + 3
    if first == 0x82:
        if offset + 5 > len(blob):
            return -1, offset
        return int.from_bytes(blob[offset + 1 : offset + 5], "little"), offset + 5
    return -1, offset


def _find_text_marker(blob: bytes) -> int:
    for i in range(len(blob) - 1):
        if blob[i] == 0x01 and blob[i + 1] == 0x2B:
            return i + 1

    idx = blob.find(b"NSString")
    if idx >= 0:
        for i in range(idx + 8, min(idx + 50, len(blob) - 1)):
            length, data_offset = _read_length(blob, i)
            if 0 < length < 100_000 and data_offset + length <= len(blob):
                candidate = blob[data_offset : data_offset + length]
                try:
                    decoded = candidate.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                has_control_chars = re.search(r"[\x00-\x08]", decoded)
                if any(ch.isprintable() for ch in decoded) and not has_control_chars:
                    return i - 1
    return -1


def extract_text_from_attributed_body(blob: bytes | None) -> str | None:
    """Best-effort extraction of plain text from an attributedBody NSArchiver blob.
    Returns None if the blob is missing, malformed, or doesn't match the expected
    shape — callers fall back to the `text` column in that case."""
    if not blob or len(blob) < 20:
        return None
    if blob[0] != 0x04 or blob[1] != 0x0B:  # typedstream magic
        return None

    marker = _find_text_marker(blob)
    if marker < 0:
        return None

    length_offset = marker + 1
    if length_offset >= len(blob):
        return None

    length, data_offset = _read_length(blob, length_offset)
    if length <= 0 or data_offset + length > len(blob):
        return None

    try:
        text = blob[data_offset : data_offset + length].decode("utf-8")
    except UnicodeDecodeError:
        return None
    return text or None


def resolve_message_text(text: str | None, attributed_body: bytes | None) -> str | None:
    if text:
        return text
    if attributed_body:
        return extract_text_from_attributed_body(attributed_body)
    return None


# Tapback reaction codes (associated_message_type). Documented from public
# chat.db research; not guessed — anything else surfaces as "other_<code>".
REACTION_TYPE_NAMES = {
    2000: "loved", 2001: "liked", 2002: "disliked",
    2003: "laughed", 2004: "emphasized", 2005: "questioned",
    3000: "removed_loved", 3001: "removed_liked", 3002: "removed_disliked",
    3003: "removed_laughed", 3004: "removed_emphasized", 3005: "removed_questioned",
}


def _reaction_type_name(associated_message_type: int) -> str | None:
    if not associated_message_type:
        return None
    return REACTION_TYPE_NAMES.get(associated_message_type, f"other_{associated_message_type}")


# Reactions (tapbacks) and edit/unsend placeholders show up as their own "message"
# rows with associated_message_type != 0. They aren't real conversation content, so
# read tools exclude them by default (include_reactions=True opts back in).
REACTION_FILTER_SQL = "m.associated_message_type = 0"

MESSAGE_COLUMNS = """
    m.ROWID as rowid, m.guid, m.text, m.attributedBody, m.handle_id, m.is_from_me,
    m.date, m.cache_has_attachments, m.associated_message_type
"""


# --------------------------------------------------------------------------------
# chat.db row -> dict helpers
# --------------------------------------------------------------------------------

_handle_cache: dict[int, str | None] = {}


def _handle_id_to_address(conn: sqlite3.Connection, handle_id: int) -> str | None:
    if handle_id in _handle_cache:
        return _handle_cache[handle_id]
    row = conn.execute("SELECT id FROM handle WHERE ROWID = ?", (handle_id,)).fetchone()
    value = row["id"] if row else None
    _handle_cache[handle_id] = value
    return value


def _attachments_for_messages(
    conn: sqlite3.Connection, message_rowids: list[int]
) -> dict[int, list[str]]:
    if not message_rowids:
        return {}
    placeholders = ",".join("?" for _ in message_rowids)
    rows = conn.execute(
        f"""
        SELECT maj.message_id as message_id, a.filename as filename
        FROM message_attachment_join maj
        JOIN attachment a ON a.ROWID = maj.attachment_id
        WHERE maj.message_id IN ({placeholders})
        """,
        message_rowids,
    ).fetchall()
    out: dict[int, list[str]] = {}
    for row in rows:
        if row["filename"]:
            out.setdefault(row["message_id"], []).append(os.path.expanduser(row["filename"]))
    return out


def _row_message_dict(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    chat_guid: str,
    address_book: dict[str, str],
    attachments_by_message: dict[int, list[str]],
    trusted_contacts: list[dict[str, Any]],
) -> dict[str, Any]:
    handle = None
    if not row["is_from_me"] and row["handle_id"]:
        handle = _handle_id_to_address(conn, row["handle_id"])

    text = resolve_message_text(row["text"], row["attributedBody"])
    sender_name = (
        "Me" if row["is_from_me"] else (resolve_handle_to_name(address_book, handle) or handle)
    )
    rowid = row["rowid"]

    return {
        "message_guid": row["guid"],
        "chat_guid": chat_guid,
        "date_iso": iso(apple_ns_to_dt(row["date"])),
        "sender_handle": handle,
        "sender_name": sender_name,
        # None for your own messages — trust is a property of an external sender.
        # Data signal only: this server's own behavior does not change based on it.
        "sender_trusted": (
            None if row["is_from_me"] else is_trusted_handle(handle, trusted_contacts)
        ),
        "is_from_me": bool(row["is_from_me"]),
        "text": text,
        "has_attachment": bool(row["cache_has_attachments"]),
        "attachment_paths": attachments_by_message.get(rowid, []),
        "reaction_type": _reaction_type_name(row["associated_message_type"]),
    }


def _chat_participants(
    conn: sqlite3.Connection, chat_rowid: int, address_book: dict[str, str]
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT h.id as id
        FROM handle h
        JOIN chat_handle_join chj ON chj.handle_id = h.ROWID
        WHERE chj.chat_id = ?
        ORDER BY h.id
        """,
        (chat_rowid,),
    ).fetchall()
    return [
        {"handle": r["id"], "name": resolve_handle_to_name(address_book, r["id"]) or r["id"]}
        for r in rows
    ]


def _chat_stats(conn: sqlite3.Connection, chat_rowid: int) -> tuple[int | None, int]:
    row = conn.execute(
        f"""
        SELECT MAX(m.date) as last_date, COUNT(*) as c
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        WHERE cmj.chat_id = ? AND {REACTION_FILTER_SQL}
        """,
        (chat_rowid,),
    ).fetchone()
    return row["last_date"], row["c"]


def _chat_dict(
    conn: sqlite3.Connection, row: sqlite3.Row, address_book: dict[str, str]
) -> dict[str, Any]:
    row_keys = row.keys()
    if "last_message_date" in row_keys and "message_count" in row_keys:
        last_message_date, message_count = row["last_message_date"], row["message_count"]
    else:
        last_message_date, message_count = _chat_stats(conn, row["rowid"])
    return {
        "chat_guid": row["guid"],
        "display_name": row["display_name"] or None,
        "participants": _chat_participants(conn, row["rowid"], address_book),
        "last_message_date": iso(apple_ns_to_dt(last_message_date)),
        "message_count": message_count,
    }


# --------------------------------------------------------------------------------
# Trusted contacts (imessage-only) — an explicit, user-maintained allow-list of
# handles. This server only *exposes* the sender_trusted signal on message reads;
# it does not change imessage_send's own behavior based on it. See
# skills/imessage/references/trusted-contacts.md.
# --------------------------------------------------------------------------------

def load_trusted_contacts() -> list[dict[str, Any]]:
    import json

    path = trusted_contacts_path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []
    contacts = data.get("trusted_contacts") if isinstance(data, dict) else None
    return contacts if isinstance(contacts, list) else []


def save_trusted_contacts(contacts: list[dict[str, Any]]) -> None:
    import json

    path = trusted_contacts_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"trusted_contacts": contacts}, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _trusted_handle_keys(handle: str) -> set[str]:
    keys = {handle.strip().lower()}
    digits = normalize_digits(handle)
    if len(digits) >= 10:
        keys.add(digits[-10:])
    return keys


def is_trusted_handle(handle: str | None, trusted_contacts: list[dict[str, Any]]) -> bool:
    if not handle:
        return False
    handle_keys = _trusted_handle_keys(handle)
    for entry in trusted_contacts:
        entry_handle = entry.get("handle") or ""
        if entry_handle and handle_keys & _trusted_handle_keys(entry_handle):
            return True
    return False


_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_WRITE_LOCAL = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
_SEND_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}


# --------------------------------------------------------------------------------
# imessage_* tools
# --------------------------------------------------------------------------------

@mcp.tool(annotations=_READ_ONLY)
def imessage_doctor() -> dict:
    """Preflight check for iMessage: chat.db/AddressBook access, message count,
    local date range, and trusted-contact count. Call this first in a session
    before any other imessage_* tool."""
    db_path = chat_db_path()
    result: dict[str, Any] = {
        "chat_db_path": db_path,
        "chat_db_exists": os.path.exists(db_path),
        "chat_db_readable": False,
        "message_count": None,
        "earliest_message_date": None,
        "latest_message_date": None,
        "address_book_dir": address_book_sources_dir(),
        "address_book_found": False,
        "contact_count": 0,
        "trusted_contact_count": 0,
        "warnings": [],
        "errors": [],
        "ok": False,
    }

    try:
        conn = open_chat_db()
        result["chat_db_readable"] = True
        row = conn.execute(
            "SELECT COUNT(*) as c, MIN(date) as min_d, MAX(date) as max_d FROM message"
        ).fetchone()
        result["message_count"] = row["c"]
        earliest = apple_ns_to_dt(row["min_d"])
        latest = apple_ns_to_dt(row["max_d"])
        result["earliest_message_date"] = iso(earliest)
        result["latest_message_date"] = iso(latest)
        conn.close()

        if earliest is not None:
            from datetime import datetime

            history_days = (datetime.now().astimezone() - earliest).days
            if history_days < 90:
                result["warnings"].append(
                    f"Local history only spans {history_days} day(s). If you expected more, "
                    f"this Mac may not have 'Messages in iCloud' enabled/fully synced — "
                    f"chat.db only contains what synced to this device. Initial iCloud sync "
                    f"of a large history can take 1-2 days. Note also that what's visible in "
                    f"Messages.app is not guaranteed to match chat.db exactly: older history "
                    f"can be offloaded to iCloud-only storage."
                )
        if result["message_count"] == 0:
            result["warnings"].append("chat.db is readable but contains zero messages.")
    except DbUnavailable as exc:
        result["errors"].append(str(exc))

    sources = address_book_dbs()
    result["address_book_found"] = bool(sources)
    if sources:
        try:
            book = load_address_book()
            result["contact_count"] = len(set(book.values()))
            if not book:
                result["warnings"].append(
                    "AddressBook database(s) found but no contacts resolved from them."
                )
        except Exception as exc:  # pragma: no cover - defensive
            result["errors"].append(f"Failed to read AddressBook: {exc}")
    else:
        result["warnings"].append(
            "No AddressBook database found — contact names won't resolve, only raw handles. "
            "This is optional; grant Full Disk Access if you want name resolution."
        )

    trusted_contacts = load_trusted_contacts()
    result["trusted_contact_count"] = len(trusted_contacts)
    if result["chat_db_readable"] and not trusted_contacts:
        result["warnings"].append(
            "No trusted contacts configured yet. This is optional and doesn't change anything "
            "this server does on its own — it's a signal (sender_trusted on messages) that "
            "higher-level tooling can use. Consider calling imessage_trusted_suggest and "
            "asking the user if they'd like to add anyone it surfaces."
        )

    result["ok"] = result["chat_db_readable"] and result["message_count"] not in (None, 0)
    return result


@mcp.tool(annotations=_READ_ONLY)
def imessage_chats(since: str | None = None, contact: str | None = None, limit: int = 20) -> dict:
    """List iMessage conversations: guid, display name/participants, last activity,
    message count. `since` filters to chats with activity on/after that date."""
    conn = open_chat_db()
    address_book = load_address_book()

    where = ["message_count > 0"]
    params: list[Any] = []
    if since:
        where.append("last_message_date >= ?")
        params.append(dt_to_apple_ns(parse_when(since)))
    where_sql = " AND ".join(where)

    query = f"""
        SELECT * FROM (
            SELECT
              c.ROWID as rowid, c.guid as guid, c.display_name as display_name,
              (SELECT MAX(m.date) FROM message m
                 JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                 WHERE cmj.chat_id = c.ROWID AND {REACTION_FILTER_SQL}) as last_message_date,
              (SELECT COUNT(*) FROM message m
                 JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
                 WHERE cmj.chat_id = c.ROWID AND {REACTION_FILTER_SQL}) as message_count
            FROM chat c
        ) t
        WHERE {where_sql}
    """
    filtered = conn.execute(query, params).fetchall()
    chats = [_chat_dict(conn, r, address_book) for r in filtered]

    if contact:
        q = contact.lower()
        chats = [
            c for c in chats
            if (c["display_name"] and q in c["display_name"].lower())
            or any(
                q in (p["name"] or "").lower() or q in p["handle"].lower()
                for p in c["participants"]
            )
        ]

    chats.sort(key=lambda c: c["last_message_date"] or "", reverse=True)
    chats = chats[:limit]
    conn.close()
    return {"chats": chats, "count": len(chats)}


def _messages_for_chat(
    conn: sqlite3.Connection,
    chat_row: sqlite3.Row,
    address_book: dict[str, str],
    trusted_contacts: list[dict[str, Any]],
    since,
    until,
    limit: int,
    offset: int,
    include_reactions: bool,
) -> tuple[list[dict[str, Any]], int]:
    where = ["cmj.chat_id = ?"]
    params: list[Any] = [chat_row["rowid"]]
    if not include_reactions:
        where.append(REACTION_FILTER_SQL)
    if since:
        where.append("m.date >= ?")
        params.append(dt_to_apple_ns(since))
    if until:
        where.append("m.date <= ?")
        params.append(dt_to_apple_ns(until))
    where_sql = " AND ".join(where)

    total = conn.execute(
        f"""
        SELECT COUNT(*) as c FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        WHERE {where_sql}
        """,
        params,
    ).fetchone()["c"]

    rows = conn.execute(
        f"""
        SELECT {MESSAGE_COLUMNS}
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        WHERE {where_sql}
        ORDER BY m.date ASC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    attachments = _attachments_for_messages(conn, [r["rowid"] for r in rows])
    messages = [
        _row_message_dict(conn, r, chat_row["guid"], address_book, attachments, trusted_contacts)
        for r in rows
    ]
    return messages, total


@mcp.tool(annotations=_READ_ONLY)
def imessage_messages(
    chat_guid: str,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    offset: int = 0,
    include_reactions: bool = False,
) -> dict:
    """Paginated messages for one already-known chat_guid."""
    conn = open_chat_db()
    address_book = load_address_book()
    trusted_contacts = load_trusted_contacts()

    chat_row = conn.execute(
        "SELECT ROWID as rowid, guid, display_name FROM chat WHERE guid = ?", (chat_guid,)
    ).fetchone()
    if not chat_row:
        conn.close()
        return {"error": f"No chat found with guid {chat_guid!r}", "messages": [], "count": 0}

    since_dt = parse_when(since) if since else None
    until_dt = until_boundary(until) if until else None
    messages, total = _messages_for_chat(
        conn, chat_row, address_book, trusted_contacts,
        since_dt, until_dt, limit, offset, include_reactions,
    )
    conn.close()
    return {
        "chat_guid": chat_guid,
        "messages": messages,
        "count": len(messages),
        "total_matching": total,
        "has_more": offset + len(messages) < total,
    }


@mcp.tool(annotations=_READ_ONLY)
def imessage_recent(
    since: str | None = None,
    until: str | None = None,
    limit: int = 500,
    include_reactions: bool = False,
) -> dict:
    """All messages across all chats in a date range, grouped by chat. Start here
    for 'what have people been saying' requests — no contact lookup needed first.
    Defaults to the last 7 days if `since` is omitted."""
    conn = open_chat_db()
    address_book = load_address_book()
    trusted_contacts = load_trusted_contacts()

    since_dt = parse_when(since) if since else parse_when("7 days ago")
    until_dt = until_boundary(until) if until else None

    where = [REACTION_FILTER_SQL if not include_reactions else "1=1", "m.date >= ?"]
    params: list[Any] = [dt_to_apple_ns(since_dt)]
    if until_dt:
        where.append("m.date <= ?")
        params.append(dt_to_apple_ns(until_dt))
    where_sql = " AND ".join(where)

    rows = conn.execute(
        f"""
        SELECT {MESSAGE_COLUMNS}, cmj.chat_id as chat_id
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        WHERE {where_sql}
        ORDER BY m.date DESC
        LIMIT ?
        """,
        [*params, limit],
    ).fetchall()

    chat_rowids = sorted({r["chat_id"] for r in rows})
    chat_rows = {}
    if chat_rowids:
        placeholders = ",".join("?" for _ in chat_rowids)
        chat_query = (
            f"SELECT ROWID as rowid, guid, display_name FROM chat WHERE ROWID IN ({placeholders})"
        )
        for r in conn.execute(chat_query, chat_rowids):
            chat_rows[r["rowid"]] = r

    attachments = _attachments_for_messages(conn, [r["rowid"] for r in rows])

    grouped: dict[int, dict[str, Any]] = {}
    for r in rows:
        chat_row = chat_rows.get(r["chat_id"])
        if not chat_row:
            continue
        bucket = grouped.get(r["chat_id"])
        if bucket is None:
            bucket = {
                "chat_guid": chat_row["guid"],
                "display_name": chat_row["display_name"] or None,
                "participants": _chat_participants(conn, r["chat_id"], address_book),
                "messages": [],
            }
            grouped[r["chat_id"]] = bucket
        bucket["messages"].append(
            _row_message_dict(
                conn, r, chat_row["guid"], address_book, attachments, trusted_contacts
            )
        )

    for bucket in grouped.values():
        bucket["messages"].reverse()

    def _last_date(c):
        return c["messages"][-1]["date_iso"] if c["messages"] else ""

    chats = sorted(grouped.values(), key=_last_date, reverse=True)
    conn.close()
    return {
        "since": iso(since_dt),
        "until": iso(until_dt),
        "chats": chats,
        "chat_count": len(chats),
        "message_count": len(rows),
    }


@mcp.tool(annotations=_READ_ONLY)
def imessage_search(
    query: str,
    handle: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
    include_reactions: bool = False,
) -> dict:
    """Full-text search, optionally scoped to a handle and/or date range. Finds
    matches in both the plain `text` column and macOS 14+'s attributedBody-only
    messages — don't roll your own SQL against this DB, it'll miss the latter."""
    conn = open_chat_db()
    address_book = load_address_book()
    trusted_contacts = load_trusted_contacts()

    base_where = [REACTION_FILTER_SQL if not include_reactions else "1=1"]
    params: list[Any] = []
    if handle:
        base_where.append("h.id = ?")
        params.append(handle)
    if since:
        base_where.append("m.date >= ?")
        params.append(dt_to_apple_ns(parse_when(since)))
    if until:
        base_where.append("m.date <= ?")
        params.append(dt_to_apple_ns(until_boundary(until)))
    base_where_sql = " AND ".join(base_where)

    like_query = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    text_rows = conn.execute(
        f"""
        SELECT {MESSAGE_COLUMNS}, cmj.chat_id as chat_id
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE {base_where_sql} AND m.text LIKE ? ESCAPE '\\'
        ORDER BY m.date DESC
        LIMIT ?
        """,
        [*params, like_query, limit],
    ).fetchall()

    blob_rows = conn.execute(
        f"""
        SELECT {MESSAGE_COLUMNS}, cmj.chat_id as chat_id
        FROM message m
        JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
        LEFT JOIN handle h ON h.ROWID = m.handle_id
        WHERE {base_where_sql} AND m.text IS NULL AND m.attributedBody IS NOT NULL
        ORDER BY m.date DESC
        LIMIT ?
        """,
        [*params, max(limit * 25, 2000)],
    ).fetchall()

    query_lower = query.lower()
    matched_blob_rows = []
    for r in blob_rows:
        decoded = extract_text_from_attributed_body(r["attributedBody"])
        if decoded and query_lower in decoded.lower():
            matched_blob_rows.append(r)

    combined = {r["rowid"]: r for r in (*text_rows, *matched_blob_rows)}
    rows = sorted(combined.values(), key=lambda r: r["date"], reverse=True)[:limit]

    chat_rowids = sorted({r["chat_id"] for r in rows})
    chat_guids: dict[int, str] = {}
    if chat_rowids:
        placeholders = ",".join("?" for _ in chat_rowids)
        guid_query = f"SELECT ROWID as rowid, guid FROM chat WHERE ROWID IN ({placeholders})"
        for r in conn.execute(guid_query, chat_rowids):
            chat_guids[r["rowid"]] = r["guid"]

    attachments = _attachments_for_messages(conn, [r["rowid"] for r in rows])
    results = [
        _row_message_dict(
            conn, r, chat_guids.get(r["chat_id"], ""), address_book, attachments, trusted_contacts
        )
        for r in rows
    ]
    conn.close()
    return {"query": query, "handle": handle, "results": results, "count": len(results)}


def _resolve_chat(
    conn: sqlite3.Connection, address_book: dict[str, str], query: str
) -> dict[str, Any]:
    query = query.strip()

    if query.startswith(("iMessage;", "SMS;")) or "@" in query:
        handles_to_try = [query]
    else:
        handles_to_try = find_handles_by_name(address_book, query) or [query]

    candidates: list[dict[str, Any]] = []
    seen_guids: set[str] = set()
    group_only = False

    for h in handles_to_try:
        if h.startswith(("iMessage;", "SMS;")):
            rows = conn.execute(
                "SELECT ROWID as rowid, guid, display_name FROM chat WHERE guid = ?", (h,)
            ).fetchall()
            for r in rows:
                if r["guid"] in seen_guids:
                    continue
                seen_guids.add(r["guid"])
                candidates.append(_chat_dict(conn, r, address_book))
            continue

        digits = normalize_digits(h)
        rows = conn.execute(
            """
            SELECT c.ROWID as rowid, c.guid as guid, c.display_name as display_name,
              (SELECT COUNT(*) FROM chat_handle_join chj2 WHERE chj2.chat_id = c.ROWID) as chat_size
            FROM chat c
            JOIN chat_handle_join chj ON chj.chat_id = c.ROWID
            JOIN handle ha ON ha.ROWID = chj.handle_id
            WHERE (ha.id = ? OR (
                length(?) >= 10
                AND replace(replace(replace(ha.id,'-',''),' ',''),'(','') LIKE '%' || ?
            ))
            """,
            (h, digits, digits[-10:] if len(digits) >= 10 else h),
        ).fetchall()
        for r in rows:
            if r["chat_size"] != 1:
                group_only = True
                continue
            if r["guid"] in seen_guids:
                continue
            seen_guids.add(r["guid"])
            candidates.append(_chat_dict(conn, r, address_book))

    result: dict[str, Any] = {"query": query, "candidates": candidates, "count": len(candidates)}
    if len(candidates) == 1:
        result["chat_guid"] = candidates[0]["chat_guid"]
    elif not candidates and group_only:
        result["note"] = (
            "This handle/name only matches group chats, not a 1:1 DM. "
            "Use imessage_chats(contact=...) to find the group instead."
        )
    return result


def _resolve_single_chat_guid(
    conn: sqlite3.Connection, address_book: dict[str, str], query: str
) -> str:
    result = _resolve_chat(conn, address_book, query)
    if result["count"] == 1:
        return result["chat_guid"]
    if result["count"] == 0:
        note = result.get("note")
        raise ValueError(f"No chat found for {query!r}.{(' ' + note) if note else ''}")
    raise ValueError(
        f"{query!r} matches {result['count']} chats — ambiguous, refusing to guess. Call "
        f"imessage_resolve_chat(handle={query!r}) to see candidates, then call imessage_send "
        f"again with an exact chat_guid."
    )


@mcp.tool(annotations=_READ_ONLY)
def imessage_resolve_chat(handle: str) -> dict:
    """Resolve a phone/email/contact-name to a chat_guid. Resolves to the 1:1 DM
    specifically — `chat_guid` is only present when count == 1."""
    conn = open_chat_db()
    try:
        return _resolve_chat(conn, load_address_book(), handle)
    finally:
        conn.close()


class SendFailed(Exception):
    pass


# 10,000 chars matches the official Anthropic imessage plugin's own auto-split
# threshold; here it's a hard cap rather than a silent auto-split, since a failed
# send should surface to the caller instead of quietly turning one message into
# several.
MAX_SEND_TEXT_LENGTH = 10_000

# Text and chat_guid arrive as argv entries below the script itself, not
# interpolated into the script source — no shell, no quoting/escaping footgun.
_SEND_APPLESCRIPT = """
on run argv
    set targetChatGuid to item 1 of argv
    set messageText to item 2 of argv
    tell application "Messages"
        set targetChat to a reference to chat id targetChatGuid
        send messageText to targetChat
    end tell
end run
"""


def send_via_applescript(chat_guid: str, text: str) -> None:
    try:
        proc = subprocess.run(
            ["osascript", "-e", _SEND_APPLESCRIPT, chat_guid, text],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError as exc:
        raise SendFailed(
            "osascript not found — sending only works on macOS with Messages.app."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise SendFailed("osascript timed out after 15s waiting on Messages.app.") from exc
    if proc.returncode != 0:
        raise SendFailed(
            f"Messages.app rejected the send (chat_guid={chat_guid!r}): "
            f"{proc.stderr.strip() or 'unknown osascript error'}. If this is the first send this "
            f"session, macOS may be waiting on an Automation permission prompt for Messages.app — "
            f"check System Settings -> Privacy & Security -> Automation."
        )


@mcp.tool(annotations=_SEND_ANNOTATIONS)
def imessage_send(
    text: str,
    chat_guid: str | None = None,
    handle: str | None = None,
    confirmed: bool = False,
) -> dict:
    """Send a plain-text iMessage via Messages.app — irreversibly delivers once
    confirmed=true. There is no undo, edit, or unsend from this tool.

    Call with confirmed=false (the default) first: this resolves the recipient
    and returns a preview (resolved chat_guid, recipient names, exact text)
    WITHOUT sending anything. Show that preview to the user and get their
    explicit go-ahead, then call again with confirmed=true (same arguments) to
    actually deliver it. Skip the preview only if the user's own message already
    dictated the exact text and recipient themselves.

    Pass exactly one of chat_guid (exact, e.g. from imessage_resolve_chat or
    imessage_chats) or handle (phone/email/contact name — must resolve to
    exactly one chat, or this raises instead of guessing)."""
    if not text or not text.strip():
        raise ValueError("text must not be empty.")
    if len(text) > MAX_SEND_TEXT_LENGTH:
        raise ValueError(
            f"text is {len(text)} characters, over this tool's {MAX_SEND_TEXT_LENGTH}-character "
            f"send limit. Split it into multiple imessage_send calls."
        )
    if bool(chat_guid) == bool(handle):
        raise ValueError("Pass exactly one of chat_guid or handle.")

    conn = open_chat_db()
    try:
        address_book = load_address_book()
        target = chat_guid or handle
        resolved = _resolve_chat(conn, address_book, target)
        resolved_guid = (
            chat_guid if chat_guid else _resolve_single_chat_guid(conn, address_book, handle)
        )
    finally:
        conn.close()

    if not confirmed:
        return {
            "preview": True,
            "sent": False,
            "chat_guid": resolved_guid,
            "recipients": resolved.get("candidates") or [{"chat_guid": resolved_guid}],
            "text": text,
            "next_step": (
                "Show this exact text and recipient to the user. If they approve, call "
                "imessage_send again with confirmed=true and the same chat_guid/handle and text."
            ),
        }

    send_via_applescript(resolved_guid, text)
    logger.info("imessage_send delivered chat_guid=%s chars=%d", resolved_guid, len(text))
    return {"preview": False, "sent": True, "chat_guid": resolved_guid, "text": text}


@mcp.tool(annotations=_READ_ONLY)
def imessage_trusted_list() -> dict:
    """List handles on the trusted-contacts allow-list."""
    contacts = load_trusted_contacts()
    return {"trusted_contacts": contacts, "count": len(contacts), "path": trusted_contacts_path()}


@mcp.tool(annotations=_WRITE_LOCAL)
def imessage_trusted_add(handle: str, name: str | None = None, note: str | None = None) -> dict:
    """Add a handle to the trusted-contacts allow-list (stored locally, never
    sent anywhere). `sender_trusted` is a data signal only — it does not change
    what imessage_send requires."""
    handle = handle.strip()
    if not handle:
        raise ValueError("handle must not be empty.")
    contacts = load_trusted_contacts()
    if is_trusted_handle(handle, contacts):
        raise ValueError(f"{handle!r} is already in the trusted contacts list.")
    entry = {"handle": handle, "name": name, "note": note}
    contacts.append(entry)
    save_trusted_contacts(contacts)
    return {"added": entry, "trusted_contacts": contacts, "count": len(contacts)}


@mcp.tool(annotations=_WRITE_LOCAL)
def imessage_trusted_remove(handle: str) -> dict:
    """Remove a handle from the trusted-contacts allow-list."""
    handle = handle.strip()
    contacts = load_trusted_contacts()
    handle_keys = _trusted_handle_keys(handle)
    remaining = [
        c for c in contacts if not (handle_keys & _trusted_handle_keys(c.get("handle") or ""))
    ]
    removed = len(contacts) - len(remaining)
    if removed == 0:
        raise ValueError(f"{handle!r} was not found in the trusted contacts list.")
    save_trusted_contacts(remaining)
    return {"removed_count": removed, "trusted_contacts": remaining, "count": len(remaining)}


@mcp.tool(annotations=_READ_ONLY)
def imessage_trusted_suggest(since: str | None = None, limit: int = 10) -> dict:
    """Rank people you've actually exchanged messages with by message count,
    excluding anyone already trusted — for onboarding. Never adds anyone itself."""
    conn = open_chat_db()
    address_book = load_address_book()
    trusted_contacts = load_trusted_contacts()

    where = ["m.is_from_me = 0", "h.id IS NOT NULL", REACTION_FILTER_SQL]
    params: list[Any] = []
    if since:
        where.append("m.date >= ?")
        params.append(dt_to_apple_ns(parse_when(since)))
    where_sql = " AND ".join(where)

    rows = conn.execute(
        f"""
        SELECT h.id as handle, COUNT(*) as message_count, MAX(m.date) as last_date
        FROM message m
        JOIN handle h ON h.ROWID = m.handle_id
        WHERE {where_sql}
        GROUP BY h.id
        ORDER BY message_count DESC
        """,
        params,
    ).fetchall()
    conn.close()

    candidates = []
    for r in rows:
        if is_trusted_handle(r["handle"], trusted_contacts):
            continue
        candidates.append({
            "handle": r["handle"],
            "name": resolve_handle_to_name(address_book, r["handle"]),
            "message_count": r["message_count"],
            "last_message_date": iso(apple_ns_to_dt(r["last_date"])),
        })
        if len(candidates) >= limit:
            break

    return {"candidates": candidates, "count": len(candidates), "since": since}


_EXTRA_MIME_TYPES = {".heic": "image/heic", ".heif": "image/heif", ".caf": "audio/x-caf"}
MAX_ATTACHMENT_BYTES = 5_000_000


def _guess_mime_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in _EXTRA_MIME_TYPES:
        return _EXTRA_MIME_TYPES[ext]
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


@mcp.tool(annotations=_READ_ONLY)
def imessage_get_attachment(path: str) -> dict:
    """Read one message attachment's raw bytes (base64-encoded) from disk. Only
    accepts a `path` exactly as returned in an `attachment_paths` list from
    imessage_messages/imessage_recent/imessage_search — refuses any path outside
    ~/Library/Messages/Attachments/. Capped at 5,000,000 bytes; raises rather
    than returning partial/truncated data for anything larger. HEIC/HEIF images
    are returned as-is (no format conversion) and may need external conversion
    to render in some clients."""
    root = attachments_root()
    candidate = Path(path).expanduser().resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"Refusing to read {path!r} — only attachments under {root} can be fetched."
        ) from exc
    if not candidate.is_file():
        raise ValueError(f"Attachment not found at {path!r}.")
    size = candidate.stat().st_size
    if size > MAX_ATTACHMENT_BYTES:
        raise ValueError(
            f"Attachment is {size} bytes, over this tool's {MAX_ATTACHMENT_BYTES}-byte cap — "
            f"too large to return inline."
        )
    data = candidate.read_bytes()
    return {
        "path": str(candidate),
        "mime_type": _guess_mime_type(candidate),
        "size_bytes": size,
        "data_base64": base64.b64encode(data).decode("ascii"),
    }


# --------------------------------------------------------------------------------
# calls_* tools
# --------------------------------------------------------------------------------

CALL_TYPE_NAMES = {0: "third_party_app", 1: "phone", 8: "facetime_video", 16: "facetime_audio"}
CALL_TYPE_CODES = {v: k for k, v in CALL_TYPE_NAMES.items()}


def _call_type_name(code: int | None) -> str:
    if code is None:
        return "unknown"
    return CALL_TYPE_NAMES.get(code, f"other_{code}")


def _call_dict(row: sqlite3.Row, address_book: dict[str, str]) -> dict[str, Any]:
    address = row["address"]
    direction = "outgoing" if row["originated"] else "incoming"
    answered = bool(row["answered"])
    duration = row["duration"]
    return {
        "date_iso": iso(core_data_ts_to_dt(row["date"])),
        "direction": direction,
        "answered": answered,
        "missed": direction == "incoming" and not answered,
        "duration_seconds": int(round(duration)) if duration is not None else 0,
        "call_type": _call_type_name(row["call_type"]),
        "handle": address,
        "contact_name": resolve_handle_to_name(address_book, address),
        # Best-effort passthrough — Apple doesn't document this column's exact
        # contents, so it's surfaced raw, not interpreted.
        "service_provider": row["service_provider"],
    }


@mcp.tool(annotations=_READ_ONLY)
def calls_doctor() -> dict:
    """Preflight check for call history: CallHistory.storedata/AddressBook access,
    call count, local date range, and whether any real cellular calls are present.
    Call this first before calls_list — a zero phone_call_count with call_count > 0
    almost always means Continuity Calling ('Calls From iPhone') is off, not a bug."""
    db_path = callhistory_db_path()
    result: dict[str, Any] = {
        "call_history_db_path": db_path,
        "call_history_db_exists": os.path.exists(db_path),
        "call_history_db_readable": False,
        "call_count": None,
        "phone_call_count": 0,
        "earliest_call_date": None,
        "latest_call_date": None,
        "address_book_dir": address_book_sources_dir(),
        "address_book_found": False,
        "contact_count": 0,
        "warnings": [],
        "errors": [],
        "ok": False,
    }

    try:
        conn = open_calls_db()
        result["call_history_db_readable"] = True
        row = conn.execute(
            "SELECT COUNT(*) as c, MIN(ZDATE) as min_d, MAX(ZDATE) as max_d FROM ZCALLRECORD"
        ).fetchone()
        result["call_count"] = row["c"]
        earliest = core_data_ts_to_dt(row["min_d"])
        latest = core_data_ts_to_dt(row["max_d"])
        result["earliest_call_date"] = iso(earliest)
        result["latest_call_date"] = iso(latest)

        phone_row = conn.execute(
            f"SELECT COUNT(*) as c FROM ZCALLRECORD WHERE ZCALLTYPE = {CALL_TYPE_CODES['phone']}"
        ).fetchone()
        result["phone_call_count"] = phone_row["c"]
        conn.close()

        if result["call_count"]:
            if result["phone_call_count"] == 0:
                result["warnings"].append(
                    "No cellular phone calls found here — only FaceTime/other call types, if "
                    "any. If you expected real phone calls to show up, turn on Continuity "
                    "Calling: on this Mac, FaceTime -> Settings -> 'Calls From iPhone', with "
                    "both devices signed into the same Apple ID and on the same Wi-Fi network."
                )
            if earliest is not None:
                from datetime import datetime

                history_days = (datetime.now().astimezone() - earliest).days
                if history_days < 90:
                    result["warnings"].append(
                        f"Local call history only spans {history_days} day(s). Continuity "
                        f"Calling syncs opportunistically — both devices need to have been "
                        f"near each other on the same network — so this Mac may not have your "
                        f"full history even with the feature on."
                    )
        else:
            result["warnings"].append(
                "CallHistory.storedata is readable but contains zero call records."
            )
    except DbUnavailable as exc:
        result["errors"].append(str(exc))

    sources = address_book_dbs()
    result["address_book_found"] = bool(sources)
    if sources:
        try:
            book = load_address_book()
            result["contact_count"] = len(set(book.values()))
            if not book:
                result["warnings"].append(
                    "AddressBook database(s) found but no contacts resolved from them."
                )
        except Exception as exc:  # pragma: no cover - defensive
            result["errors"].append(f"Failed to read AddressBook: {exc}")
    else:
        result["warnings"].append(
            "No AddressBook database found — contact names won't resolve, only raw numbers/emails. "
            "This is optional; grant Full Disk Access if you want name resolution."
        )

    result["ok"] = result["call_history_db_readable"] and result["call_count"] not in (None, 0)
    return result


@mcp.tool(annotations=_READ_ONLY)
def calls_list(
    since: str | None = None,
    until: str | None = None,
    handle: str | None = None,
    direction: str | None = None,
    call_type: str | None = None,
    missed_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """List calls (phone + FaceTime), newest first, with contact names resolved.
    `direction` is 'incoming'/'outgoing'; `call_type` is one of phone/
    facetime_video/facetime_audio/third_party_app. Defaults to all local history
    if `since` is omitted."""
    if direction is not None and direction not in ("incoming", "outgoing"):
        raise ValueError("direction must be 'incoming' or 'outgoing'.")
    if call_type is not None and call_type not in CALL_TYPE_CODES:
        raise ValueError(f"call_type must be one of {sorted(CALL_TYPE_CODES.keys())}.")

    conn = open_calls_db()
    address_book = load_address_book()

    where: list[str] = []
    params: list[Any] = []

    since_dt = parse_when(since) if since else None
    until_dt = until_boundary(until) if until else None
    if since_dt:
        where.append("ZDATE >= ?")
        params.append(dt_to_core_data_ts(since_dt))
    if until_dt:
        where.append("ZDATE <= ?")
        params.append(dt_to_core_data_ts(until_dt))

    if handle:
        digits = normalize_digits(handle)
        if len(digits) >= 10:
            where.append("(ZADDRESS = ? OR ZADDRESS LIKE ?)")
            params.extend([handle, "%" + digits[-10:]])
        else:
            where.append("ZADDRESS = ?")
            params.append(handle)

    if direction:
        where.append("ZORIGINATED = ?")
        params.append(1 if direction == "outgoing" else 0)

    if call_type:
        where.append("ZCALLTYPE = ?")
        params.append(CALL_TYPE_CODES[call_type])

    if missed_only:
        where.append("ZORIGINATED = 0 AND ZANSWERED = 0")

    where_sql = " AND ".join(where) if where else "1=1"

    total = conn.execute(
        f"SELECT COUNT(*) as c FROM ZCALLRECORD WHERE {where_sql}", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"""
        SELECT ZDATE as date, ZDURATION as duration, ZADDRESS as address,
               ZORIGINATED as originated, ZANSWERED as answered, ZCALLTYPE as call_type,
               ZSERVICE_PROVIDER as service_provider
        FROM ZCALLRECORD
        WHERE {where_sql}
        ORDER BY ZDATE DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    calls = [_call_dict(r, address_book) for r in rows]
    conn.close()
    return {
        "since": iso(since_dt),
        "until": iso(until_dt),
        "calls": calls,
        "count": len(calls),
        "total_matching": total,
        "has_more": offset + len(calls) < total,
    }


# --------------------------------------------------------------------------------
# Shared contacts tool
# --------------------------------------------------------------------------------

@mcp.tool(annotations=_READ_ONLY)
def contacts(query: str | None = None, handle: str | None = None) -> dict:
    """Resolve name<->handle via the local macOS AddressBook (Contacts). Used by
    both imessage_* and calls_* tools for name resolution; call this directly to
    look someone up. `handle` resolves one phone/email to a name; `query` finds
    handles by (partial, case-insensitive) name match; passing neither lists every
    resolvable contact."""
    address_book = load_address_book()
    if handle:
        name = resolve_handle_to_name(address_book, handle)
        return {"contacts": [{"handle": handle, "name": name}], "count": 1 if name else 0}
    if query:
        keys = find_handles_by_name(address_book, query)
        result = [{"handle": k, "name": address_book[k]} for k in keys]
        return {"contacts": result, "count": len(result)}
    result = [
        {"handle": k, "name": v} for k, v in sorted(address_book.items(), key=lambda kv: kv[1])
    ]
    return {"contacts": result, "count": len(result)}
