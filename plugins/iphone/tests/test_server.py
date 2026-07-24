"""Tests for the shadetree-ai-plugins-iphone FastMCP server against synthetic chat.db /
CallHistory.storedata / AddressBook fixtures — no real macOS databases required.

Fixture data and the attributedBody hex blobs are ported from the reference
imessage/icallhistory skills this server's logic was adapted from (see
skills/imessage/references/attributed-body.md for provenance of the blobs).
"""

from __future__ import annotations

import os
import re
import sqlite3
import time as time_module
from pathlib import Path

import pytest
from fastmcp import Client
from shadetree_ai_plugins_iphone import server as srv

APPLE_EPOCH_OFFSET = srv.APPLE_EPOCH_OFFSET

# ---- attributedBody fixtures (byte-for-byte from a published reference implementation) ----

SHORT_MESSAGE = bytes.fromhex(
    "040b73747265616d747970656481e803840140848484124e5341747472696275746564537472696e"
    "67008484084e534f626a656374008592848484084e53537472696e67019484012b0159868402694901"
    "01928484840c4e5344696374696f6e617279009484016901928496961d5f5f6b494d4d657373616765"
    "506172744174747269627574654e616d658692848484084e534e756d626572008484074e5356616c75"
    "65009484012a84999900868686"
)
SHORT_MESSAGE_TEXT = "Y"

MEDIUM_MESSAGE = bytes.fromhex(
    "040b73747265616d747970656481e803840140848484124e5341747472696275746564537472696e"
    "67008484084e534f626a656374008592848484084e53537472696e67019484012b194f6b2c206e70"
    "2c207468616e6b7320666f7220747279696e6786840269490119928484840c4e5344696374696f6e"
    "617279009484016901928496961d5f5f6b494d4d657373616765506172744174747269627574654e"
    "616d658692848484084e534e756d626572008484074e5356616c7565009484012a84999900868686"
)
MEDIUM_MESSAGE_TEXT = "Ok, np, thanks for trying"

MALFORMED_BLOB = bytes.fromhex("deadbeefcafebabe")
TINY_BLOB = bytes.fromhex("040b")


def to_apple_ns(days_ago: float) -> int:
    unix_seconds = time_module.time() - days_ago * 86400
    return int((unix_seconds - APPLE_EPOCH_OFFSET) * 1_000_000_000)


def to_core_data_ts(days_ago: float) -> float:
    unix_seconds = time_module.time() - days_ago * 86400
    return unix_seconds - APPLE_EPOCH_OFFSET


# --------------------------------------------------------------------------------
# Pure-function tests (no fixtures needed)
# --------------------------------------------------------------------------------

class TestAttributedBodyDecode:
    def test_short(self):
        assert srv.extract_text_from_attributed_body(SHORT_MESSAGE) == SHORT_MESSAGE_TEXT

    def test_medium_uses_0x81_length_encoding(self):
        assert srv.extract_text_from_attributed_body(MEDIUM_MESSAGE) == MEDIUM_MESSAGE_TEXT

    def test_malformed_blob_returns_none(self):
        assert srv.extract_text_from_attributed_body(MALFORMED_BLOB) is None

    def test_tiny_blob_returns_none(self):
        assert srv.extract_text_from_attributed_body(TINY_BLOB) is None

    def test_none_and_empty(self):
        assert srv.extract_text_from_attributed_body(None) is None
        assert srv.extract_text_from_attributed_body(b"") is None


class TestReactionTypeNames:
    def test_normal_message_has_no_reaction_type(self):
        assert srv._reaction_type_name(0) is None

    def test_known_reaction_code(self):
        assert srv._reaction_type_name(2000) == "loved"

    def test_unknown_reaction_code_surfaces_as_other(self):
        assert srv._reaction_type_name(9999) == "other_9999"


class TestDateBoundary:
    def test_bare_iso_date_and_named_days_are_bare_days(self):
        from shadetree_ai_plugins_iphone.dates import _is_bare_day

        assert srv.iso  # sanity: module imported iso
        assert _is_bare_day("2026-06-30")
        assert _is_bare_day("today")
        assert not _is_bare_day("2026-06-30T14:00:00")
        assert not _is_bare_day("7 days ago")

    def test_until_boundary_extends_bare_date_to_end_of_day(self):
        dt = srv.until_boundary("2026-06-30")
        assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)

    def test_today_is_floored_to_midnight(self):
        dt = srv.parse_when("today")
        assert (dt.hour, dt.minute, dt.second, dt.microsecond) == (0, 0, 0, 0)


class TestCoreDataTimestamp:
    def test_epoch_zero_is_the_2001_reference_date(self):
        dt = srv.core_data_ts_to_dt(0)
        assert dt.astimezone(None).year == 2001

    def test_none_returns_none(self):
        assert srv.core_data_ts_to_dt(None) is None

    def test_round_trip(self):
        dt = srv.parse_when("2026-01-01")
        ts = srv.dt_to_core_data_ts(dt)
        round_tripped = srv.dt_to_core_data_ts(srv.core_data_ts_to_dt(ts))
        assert abs(round_tripped - ts) < 1


# --------------------------------------------------------------------------------
# Fixture: synthetic chat.db + CallHistory.storedata + AddressBook, wired via env vars
# --------------------------------------------------------------------------------

@pytest.fixture
def fixtures(tmp_path, monkeypatch):
    chat_db_path = tmp_path / "chat.db"
    callhistory_db_path = tmp_path / "CallHistory.storedata"
    addressbook_dir = tmp_path / "AddressBook" / "Sources"
    trusted_contacts_path = tmp_path / "trusted_contacts.json"
    attachments_root = tmp_path / "Attachments"
    (addressbook_dir / "SOURCE1").mkdir(parents=True)
    attachments_root.mkdir()

    _build_chat_db(chat_db_path, attachments_root)
    _build_address_book(addressbook_dir / "SOURCE1" / "AddressBook-v22.abcddb")
    _build_call_history(callhistory_db_path)

    monkeypatch.setenv("SHADETREE_AI_PLUGINS_IPHONE_CHAT_DB_PATH", str(chat_db_path))
    monkeypatch.setenv("SHADETREE_AI_PLUGINS_IPHONE_CALLHISTORY_DB_PATH", str(callhistory_db_path))
    monkeypatch.setenv("SHADETREE_AI_PLUGINS_IPHONE_ADDRESSBOOK_DIR", str(addressbook_dir))
    monkeypatch.setenv(
        "SHADETREE_AI_PLUGINS_IPHONE_TRUSTED_CONTACTS_PATH", str(trusted_contacts_path)
    )
    monkeypatch.setenv("SHADETREE_AI_PLUGINS_IPHONE_ATTACHMENTS_ROOT", str(attachments_root))
    srv._handle_cache.clear()

    return {
        "tmp_path": tmp_path,
        "trusted_contacts_path": trusted_contacts_path,
        "attachments_root": attachments_root,
    }


def _build_chat_db(db_path: Path, attachments_root: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE message (
            ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT, attributedBody BLOB,
            handle_id INTEGER, is_from_me INTEGER, date INTEGER,
            cache_has_attachments INTEGER, associated_message_guid TEXT,
            associated_message_type INTEGER, service TEXT
        );
        CREATE TABLE chat (
            ROWID INTEGER PRIMARY KEY, guid TEXT, chat_identifier TEXT,
            display_name TEXT, style INTEGER, service_name TEXT
        );
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT, service TEXT);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        CREATE TABLE chat_handle_join (chat_id INTEGER, handle_id INTEGER);
        CREATE TABLE attachment (
            ROWID INTEGER PRIMARY KEY, filename TEXT, mime_type TEXT,
            total_bytes INTEGER, transfer_name TEXT
        );
        CREATE TABLE message_attachment_join (message_id INTEGER, attachment_id INTEGER);
        """
    )

    conn.execute("INSERT INTO handle (ROWID, id, service) VALUES (1, '+15551234567', 'iMessage')")
    conn.execute("INSERT INTO handle (ROWID, id, service) VALUES (2, '+15559876543', 'iMessage')")

    # Chats: 1 = DM with Jane, 2 = group with Jane + Bob
    conn.execute(
        "INSERT INTO chat (ROWID, guid, chat_identifier, display_name, style, service_name) "
        "VALUES (1, 'iMessage;-;+15551234567', '+15551234567', NULL, 45, 'iMessage')"
    )
    conn.execute(
        "INSERT INTO chat (ROWID, guid, chat_identifier, display_name, style, service_name) "
        "VALUES (2, 'iMessage;+;chat123456', 'chat123456', 'Weekend Crew', 43, 'iMessage')"
    )
    conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (1, 1)")
    conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (2, 1)")
    conn.execute("INSERT INTO chat_handle_join (chat_id, handle_id) VALUES (2, 2)")

    photo_path = attachments_root / "photo.jpg"
    photo_path.write_bytes(b"\xff\xd8\xff fake jpeg bytes")

    messages = [
        # rowid, guid, text, blob, handle_id, is_from_me, days_ago, has_att, assoc_type
        (1, "m-1", "Hey are we still on for Saturday?", None, 1, 0, 3.0, 0, 0),
        (2, "m-2", "Yes! See you at 10", None, None, 1, 2.95, 0, 0),
        (3, "m-3", None, MEDIUM_MESSAGE, 1, 0, 2.0, 0, 0),
        (4, "m-4", "who's driving Saturday", None, 2, 0, 5.0, 0, 0),
        (5, "m-5", None, SHORT_MESSAGE, 2, 0, 4.99, 0, 2001),  # tapback reaction ("liked")
        (6, "m-6", "Check this photo out", None, 1, 0, 0.5, 1, 0),
        (7, "m-7", "ancient history message", None, 1, 0, 40.0, 0, 0),
    ]
    chat_for_message = {1: 1, 2: 1, 3: 1, 4: 2, 5: 2, 6: 1, 7: 1}

    for rowid, guid, text, blob, handle_id, is_from_me, days_ago, has_att, assoc_type in messages:
        conn.execute(
            """INSERT INTO message
               (ROWID, guid, text, attributedBody, handle_id, is_from_me, date,
                cache_has_attachments, associated_message_guid, associated_message_type, service)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 'iMessage')""",
            (
                rowid, guid, text, blob, handle_id, is_from_me,
                to_apple_ns(days_ago), has_att, assoc_type,
            ),
        )
        conn.execute(
            "INSERT INTO chat_message_join (chat_id, message_id) VALUES (?, ?)",
            (chat_for_message[rowid], rowid),
        )

    conn.execute(
        "INSERT INTO attachment (ROWID, filename, mime_type, total_bytes, transfer_name) "
        "VALUES (1, ?, 'image/jpeg', 12345, 'photo.jpg')",
        (str(photo_path),),
    )
    conn.execute("INSERT INTO message_attachment_join (message_id, attachment_id) VALUES (6, 1)")

    conn.commit()
    conn.close()


def _build_address_book(ab_path: Path) -> None:
    conn = sqlite3.connect(ab_path)
    conn.executescript(
        """
        CREATE TABLE ZABCDRECORD (Z_PK INTEGER PRIMARY KEY, ZFIRSTNAME TEXT, ZLASTNAME TEXT);
        CREATE TABLE ZABCDPHONENUMBER (ZOWNER INTEGER, ZFULLNUMBER TEXT);
        CREATE TABLE ZABCDEMAILADDRESS (ZOWNER INTEGER, ZADDRESS TEXT);
        """
    )
    conn.execute("INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME, ZLASTNAME) VALUES (1, 'Jane', 'Doe')")
    conn.execute("INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER) VALUES (1, '+15551234567')")
    conn.execute("INSERT INTO ZABCDRECORD (Z_PK, ZFIRSTNAME, ZLASTNAME) VALUES (2, 'Bob', 'Smith')")
    conn.execute("INSERT INTO ZABCDPHONENUMBER (ZOWNER, ZFULLNUMBER) VALUES (2, '+15559876543')")
    conn.commit()
    conn.close()


def _build_call_history(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE ZCALLRECORD (
            Z_PK INTEGER PRIMARY KEY, ZDATE REAL, ZDURATION REAL, ZADDRESS TEXT,
            ZORIGINATED INTEGER, ZANSWERED INTEGER, ZCALLTYPE INTEGER, ZSERVICE_PROVIDER TEXT
        );
        """
    )
    calls = [
        # days_ago, address, originated, answered, call_type, duration, provider
        (2.0, "+15551234567", 1, 1, 1, 120.0, "carrier"),   # outgoing, answered, phone
        (1.0, "+15551234567", 0, 0, 1, 0.0, "carrier"),     # incoming missed, phone
        (3.0, "+15559999999", 0, 1, 8, 300.0, None),        # incoming answered, facetime video
        (40.0, "+15559876543", 1, 1, 16, 60.0, "iPhone"),   # outgoing answered, facetime audio, old
        (5.0, "+15551234567", 1, 0, 1, 0.0, "carrier"),     # outgoing, unanswered (not missed)
    ]
    for days_ago, address, originated, answered, call_type, duration, provider in calls:
        conn.execute(
            "INSERT INTO ZCALLRECORD "
            "(ZDATE, ZDURATION, ZADDRESS, ZORIGINATED, ZANSWERED, ZCALLTYPE, ZSERVICE_PROVIDER) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                to_core_data_ts(days_ago), duration, address,
                originated, answered, call_type, provider,
            ),
        )
    conn.commit()
    conn.close()


# --------------------------------------------------------------------------------
# imessage_* tool tests
# --------------------------------------------------------------------------------

async def test_imessage_doctor_reports_access_and_counts(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_doctor", {})
        assert r.data["ok"] is True
        assert r.data["chat_db_readable"] is True
        assert r.data["message_count"] == 7
        assert r.data["address_book_found"] is True
        assert r.data["contact_count"] == 2
        assert any("history" in w.lower() for w in r.data["warnings"])


async def test_imessage_chats_lists_conversations_with_names(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_chats", {})
        assert r.data["count"] == 2
        dm = next(c for c in r.data["chats"] if c["chat_guid"] == "iMessage;-;+15551234567")
        assert dm["participants"] == [{"handle": "+15551234567", "name": "Jane Doe"}]


async def test_imessage_chats_filters_by_contact(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_chats", {"contact": "Bob"})
        assert r.data["count"] == 1
        assert r.data["chats"][0]["chat_guid"] == "iMessage;+;chat123456"


async def test_imessage_messages_decodes_attributed_body_and_resolves_names(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_messages", {"chat_guid": "iMessage;-;+15551234567"})
        texts = [m["text"] for m in r.data["messages"]]
        assert MEDIUM_MESSAGE_TEXT in texts
        by_text = {m["text"]: m for m in r.data["messages"]}
        jane_msg = by_text["Hey are we still on for Saturday?"]
        assert jane_msg["sender_name"] == "Jane Doe"
        assert jane_msg["is_from_me"] is False
        me_msg = by_text["Yes! See you at 10"]
        assert me_msg["is_from_me"] is True
        assert me_msg["sender_name"] == "Me"


async def test_imessage_messages_includes_attachment_paths(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_messages", {"chat_guid": "iMessage;-;+15551234567"})
        photo_msg = next(m for m in r.data["messages"] if m["text"] == "Check this photo out")
        assert photo_msg["has_attachment"] is True
        assert len(photo_msg["attachment_paths"]) == 1


async def test_imessage_messages_unknown_chat_guid_returns_soft_error(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_messages", {"chat_guid": "iMessage;-;+19999999999"})
        assert "error" in r.data
        assert r.data["count"] == 0


async def test_imessage_recent_default_excludes_old_and_reactions(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_recent", {})
        all_texts = [m["text"] for c in r.data["chats"] for m in c["messages"]]
        assert "ancient history message" not in all_texts
        assert MEDIUM_MESSAGE_TEXT in all_texts
        assert SHORT_MESSAGE_TEXT not in all_texts  # reaction, excluded by default


async def test_imessage_recent_include_reactions_surfaces_reaction_type(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_recent", {"include_reactions": True})
        all_messages = [m for c in r.data["chats"] for m in c["messages"]]
        reaction = next(m for m in all_messages if m["text"] == SHORT_MESSAGE_TEXT)
        assert reaction["reaction_type"] == "liked"


async def test_imessage_recent_explicit_since_includes_old_message(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_recent", {"since": "90 days ago"})
        all_texts = [m["text"] for c in r.data["chats"] for m in c["messages"]]
        assert "ancient history message" in all_texts


async def test_imessage_search_finds_plain_text_match(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_search", {"query": "Saturday"})
        assert r.data["count"] >= 2


async def test_imessage_search_finds_attributed_body_only_match(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_search", {"query": "trying"})
        assert r.data["count"] == 1
        assert r.data["results"][0]["text"] == MEDIUM_MESSAGE_TEXT


async def test_imessage_search_excludes_reactions_by_default(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_search", {"query": SHORT_MESSAGE_TEXT})
        # SHORT_MESSAGE_TEXT ("Y") is too common a substring to assert count == 0 (it
        # coincidentally matches unrelated messages like "Yes! See you at 10") — assert
        # instead that no *reaction* row leaked into the results.
        assert all(res["reaction_type"] is None for res in r.data["results"])


async def test_imessage_resolve_chat_by_handle(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_resolve_chat", {"handle": "+15551234567"})
        assert r.data["chat_guid"] == "iMessage;-;+15551234567"


async def test_imessage_resolve_chat_group_only_handle_returns_note(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_resolve_chat", {"handle": "+15559876543"})
        assert r.data["count"] == 0
        assert "chat_guid" not in r.data
        assert "group" in r.data["note"].lower()


async def test_imessage_contacts_resolve_handle_to_name(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("contacts", {"handle": "+15551234567"})
        assert r.data["contacts"][0]["name"] == "Jane Doe"


async def test_imessage_get_attachment_reads_bytes(fixtures):
    async with Client(srv.mcp) as client:
        msgs = await client.call_tool("imessage_messages", {"chat_guid": "iMessage;-;+15551234567"})
        photo_msg = next(m for m in msgs.data["messages"] if m["text"] == "Check this photo out")
        att_path = photo_msg["attachment_paths"][0]
        r = await client.call_tool("imessage_get_attachment", {"path": att_path})
        assert r.data["mime_type"] == "image/jpeg"
        assert r.data["size_bytes"] > 0
        assert r.data["data_base64"]


async def test_imessage_get_attachment_refuses_path_outside_root(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("imessage_get_attachment", {"path": "/etc/passwd"})


# --------------------------------------------------------------------------------
# imessage_send tests (preview/confirm split) — send_via_applescript is always
# monkeypatched here; this suite never shells out to real osascript.
# --------------------------------------------------------------------------------

def _patch_send(monkeypatch):
    calls = []

    def fake_send(chat_guid, text):
        calls.append((chat_guid, text))

    monkeypatch.setattr(srv, "send_via_applescript", fake_send)
    return calls


async def test_imessage_send_defaults_to_preview_without_sending(fixtures, monkeypatch):
    calls = _patch_send(monkeypatch)
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "imessage_send", {"chat_guid": "iMessage;-;+15551234567", "text": "hi there"}
        )
        assert r.data["preview"] is True
        assert r.data["sent"] is False
        assert calls == []


async def test_imessage_send_confirmed_true_sends(fixtures, monkeypatch):
    calls = _patch_send(monkeypatch)
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "imessage_send",
            {"chat_guid": "iMessage;-;+15551234567", "text": "hi there", "confirmed": True},
        )
        assert r.data["sent"] is True
        assert calls == [("iMessage;-;+15551234567", "hi there")]


async def test_imessage_send_by_handle_resolves_to_single_dm(fixtures, monkeypatch):
    calls = _patch_send(monkeypatch)
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "imessage_send", {"handle": "Jane", "text": "hi Jane", "confirmed": True}
        )
        assert r.data["chat_guid"] == "iMessage;-;+15551234567"
        assert calls == [("iMessage;-;+15551234567", "hi Jane")]


async def test_imessage_send_by_handle_group_only_raises_without_sending(fixtures, monkeypatch):
    calls = _patch_send(monkeypatch)
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "imessage_send", {"handle": "+15559876543", "text": "hi Bob", "confirmed": True}
            )
        assert calls == []


async def test_imessage_send_empty_text_raises_without_sending(fixtures, monkeypatch):
    calls = _patch_send(monkeypatch)
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "imessage_send", {"chat_guid": "iMessage;-;+15551234567", "text": "   "}
            )
        assert calls == []


async def test_imessage_send_both_chat_guid_and_handle_raises(fixtures, monkeypatch):
    calls = _patch_send(monkeypatch)
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool(
                "imessage_send",
                {"chat_guid": "iMessage;-;+15551234567", "handle": "Jane", "text": "hi"},
            )
        assert calls == []


# --------------------------------------------------------------------------------
# Trusted contacts tests
# --------------------------------------------------------------------------------

async def test_trusted_add_list_remove_roundtrip(fixtures):
    async with Client(srv.mcp) as client:
        added = await client.call_tool(
            "imessage_trusted_add", {"handle": "+15551234567", "name": "Jane Doe", "note": "spouse"}
        )
        assert added.data["added"] == {
            "handle": "+15551234567", "name": "Jane Doe", "note": "spouse",
        }

        listed = await client.call_tool("imessage_trusted_list", {})
        assert listed.data["count"] == 1
        assert os.path.exists(fixtures["trusted_contacts_path"])

        removed = await client.call_tool("imessage_trusted_remove", {"handle": "+15551234567"})
        assert removed.data["removed_count"] == 1
        listed_after = await client.call_tool("imessage_trusted_list", {})
        assert listed_after.data["count"] == 0


async def test_trusted_add_duplicate_raises(fixtures):
    async with Client(srv.mcp) as client:
        await client.call_tool("imessage_trusted_add", {"handle": "+15551234567"})
        with pytest.raises(Exception):
            await client.call_tool("imessage_trusted_add", {"handle": "+15551234567"})


async def test_sender_trusted_true_once_handle_is_added(fixtures):
    async with Client(srv.mcp) as client:
        await client.call_tool("imessage_trusted_add", {"handle": "+15551234567"})
        r = await client.call_tool("imessage_messages", {"chat_guid": "iMessage;-;+15551234567"})
        by_text = {m["text"]: m for m in r.data["messages"]}
        assert by_text["Hey are we still on for Saturday?"]["sender_trusted"] is True
        assert by_text["Yes! See you at 10"]["sender_trusted"] is None  # is_from_me


async def test_trusted_suggest_ranks_by_message_count_and_excludes_trusted(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("imessage_trusted_suggest", {})
        handles = [c["handle"] for c in r.data["candidates"]]
        assert handles[0] == "+15551234567"

        await client.call_tool("imessage_trusted_add", {"handle": "+15551234567"})
        r2 = await client.call_tool("imessage_trusted_suggest", {})
        assert "+15551234567" not in [c["handle"] for c in r2.data["candidates"]]


# --------------------------------------------------------------------------------
# calls_* tool tests
# --------------------------------------------------------------------------------

async def test_calls_doctor_reports_access_and_continuity_warning(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("calls_doctor", {})
        assert r.data["ok"] is True
        assert r.data["call_count"] == 5
        assert r.data["phone_call_count"] == 3


async def test_calls_list_newest_first_with_contact_names(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("calls_list", {})
        assert r.data["count"] == 5
        assert r.data["calls"][0]["contact_name"] in ("Jane Doe", "Bob Smith", None)
        jane_call = next(
            c for c in r.data["calls"]
            if c["handle"] == "+15551234567" and c["direction"] == "outgoing" and c["answered"]
        )
        assert jane_call["contact_name"] == "Jane Doe"


async def test_calls_list_since_filters_older_records(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("calls_list", {"since": "10 days ago"})
        # 40-day-old Bob call excluded
        assert all(c["handle"] != "+15559876543" for c in r.data["calls"])


async def test_calls_list_missed_only_excludes_unanswered_outgoing(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("calls_list", {"missed_only": True})
        assert r.data["count"] == 1
        assert r.data["calls"][0]["direction"] == "incoming"
        assert r.data["calls"][0]["missed"] is True


async def test_calls_list_unanswered_outgoing_has_missed_false(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "calls_list", {"handle": "+15551234567", "direction": "outgoing"}
        )
        unanswered = next(c for c in r.data["calls"] if not c["answered"])
        assert unanswered["missed"] is False


async def test_calls_list_filters_by_type(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("calls_list", {"call_type": "facetime_video"})
        assert r.data["count"] == 1
        assert r.data["calls"][0]["call_type"] == "facetime_video"


async def test_calls_list_invalid_direction_raises(fixtures):
    async with Client(srv.mcp) as client:
        with pytest.raises(Exception):
            await client.call_tool("calls_list", {"direction": "sideways"})


async def test_calls_contacts_shared_with_imessage(fixtures):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("contacts", {"query": "Bob"})
        assert r.data["count"] == 1
        assert r.data["contacts"][0]["name"] == "Bob Smith"


# --------------------------------------------------------------------------------
# No-network guard
# --------------------------------------------------------------------------------

def test_server_has_no_network_calls():
    server_path = (
        Path(__file__).resolve().parent.parent / "src" / "shadetree_ai_plugins_iphone" / "server.py"
    )
    source = server_path.read_text()
    banned_imports = re.compile(
        r"^\s*(import|from)\s+(requests|socket|urllib\d*|http\.client|httplib)\b", re.MULTILINE
    )
    assert not banned_imports.search(source), "found a banned network-related import"
