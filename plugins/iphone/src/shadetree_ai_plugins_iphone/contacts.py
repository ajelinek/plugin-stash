"""Shared macOS AddressBook (Contacts) resolution for the imessage and
icallhistory servers.

Reads `~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb`
read-only. Both servers resolve handles (phone numbers/emails) to contact names
against this same local database, so the lookup lives here once instead of
duplicated per server.
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path


def address_book_sources_dir() -> str:
    return os.environ.get("SHADETREE_AI_PLUGINS_IPHONE_ADDRESSBOOK_DIR") or str(
        Path.home() / "Library/Application Support/AddressBook/Sources"
    )


def address_book_dbs() -> list[str]:
    sources_dir = address_book_sources_dir()
    if not os.path.isdir(sources_dir):
        return []
    try:
        entries = sorted(os.listdir(sources_dir))
    except PermissionError:
        # TCC can let isdir() see the directory while still denying listdir() on it
        # (observed on real macOS: Full Disk Access not yet granted). Treat like "no
        # sources" so callers fall back to raw handles instead of crashing outright.
        return []
    found = []
    for entry in entries:
        candidate = os.path.join(sources_dir, entry, "AddressBook-v22.abcddb")
        if os.path.exists(candidate):
            found.append(candidate)
    return found


def normalize_digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def load_address_book() -> dict[str, str]:
    """Map normalized handle (last-10-digits phone, or lowercase email) -> full name."""
    cache: dict[str, str] = {}
    for db_path in address_book_dbs():
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except sqlite3.OperationalError:
            continue
        try:
            for row in conn.execute(
                """
                SELECT r.ZFIRSTNAME as first, r.ZLASTNAME as last, p.ZFULLNUMBER as number
                FROM ZABCDRECORD r
                JOIN ZABCDPHONENUMBER p ON r.Z_PK = p.ZOWNER
                WHERE p.ZFULLNUMBER IS NOT NULL
                """
            ):
                name = " ".join(x for x in (row["first"], row["last"]) if x).strip()
                if not name or not row["number"]:
                    continue
                digits = normalize_digits(row["number"])
                if len(digits) >= 10:
                    cache[digits[-10:]] = name
                if row["number"].startswith("+"):
                    # Normalize to bare "+<digits>" rather than keying on the raw string —
                    # AddressBook can store the same number with different punctuation
                    # across sources, but chat.db/CallHistory's handle/address columns are
                    # always the clean form.
                    cache["+" + digits] = name

            for row in conn.execute(
                """
                SELECT r.ZFIRSTNAME as first, r.ZLASTNAME as last, e.ZADDRESS as address
                FROM ZABCDRECORD r
                JOIN ZABCDEMAILADDRESS e ON r.Z_PK = e.ZOWNER
                WHERE e.ZADDRESS IS NOT NULL
                """
            ):
                name = " ".join(x for x in (row["first"], row["last"]) if x).strip()
                if not name or not row["address"]:
                    continue
                cache[row["address"].lower()] = name
        except sqlite3.OperationalError:
            pass
        finally:
            conn.close()
    return cache


def resolve_handle_to_name(address_book: dict[str, str], handle: str | None) -> str | None:
    if not handle:
        return None
    lower = handle.lower()
    if lower in address_book:
        return address_book[lower]
    if handle in address_book:
        return address_book[handle]
    digits = normalize_digits(handle)
    if len(digits) >= 10 and digits[-10:] in address_book:
        return address_book[digits[-10:]]
    return None


def find_handles_by_name(address_book: dict[str, str], query: str) -> list[str]:
    # load_address_book() indexes each phone number under two keys: a canonical
    # "+"-prefixed form and a bare last-10-digits form (a fuzzy-match aid for handles
    # that don't carry the "+"). Both map to the same contact, so a name search must
    # only surface the canonical form (or the email) — otherwise one contact with a
    # phone number shows up twice.
    q = query.lower()
    return [
        key for key, name in address_book.items()
        if q in name.lower() and (key.startswith("+") or "@" in key)
    ]
