"""Tests for the shadetree-ai-plugins-claude-usage-analyzer FastMCP server,
all against synthetic fixture directories -- no real Claude Desktop install
required.

Claude Code CLI data is out of scope for this plugin (see local_data.py's
module docstring), so there are no CLI fixtures here and
`test_no_cli_surface_remains` guards against one creeping back in.
"""

from __future__ import annotations

import ast
import json
import os
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastmcp import Client
from shadetree_ai_plugins_claude_usage_analyzer import dashboard, export_data, local_data
from shadetree_ai_plugins_claude_usage_analyzer import server as srv
from shadetree_ai_plugins_claude_usage_analyzer import time_window as tw


def _ms(year: int, month: int, day: int) -> int:
    """Epoch milliseconds -- the shape Claude Desktop actually writes for
    `createdAt`/`lastActivityAt`, so fixtures exercise the real format
    rather than a convenient stand-in."""
    return int(datetime(year, month, day, tzinfo=UTC).timestamp() * 1000)


def _write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# --------------------------------------------------------------------------------
# Pure-function tests: redaction, folder normalization
# --------------------------------------------------------------------------------


class TestRedact:
    def test_strips_secret_shaped_keys_case_insensitively(self):
        raw = {"title": "ok", "AuthToken": "abc", "cookie_jar": "x", "nested": {"apiKey": "y"}}
        redacted = local_data.redact(raw)
        assert "AuthToken" not in redacted
        assert "cookie_jar" not in redacted
        assert "apiKey" not in redacted["nested"]
        assert redacted["title"] == "ok"

    def test_strips_heavy_tool_schema_keys(self):
        raw = {"title": "ok", "enabledMcpTools": {"big": "blob"}, "remoteMcpServersConfig": {}}
        redacted = local_data.redact(raw)
        assert "enabledMcpTools" not in redacted
        assert "remoteMcpServersConfig" not in redacted

    def test_passes_through_lists(self):
        raw = [{"secretKey": "x", "name": "ok"}]
        redacted = local_data.redact(raw)
        assert redacted == [{"name": "ok"}]


class TestModelOfTranscriptRecord:
    def test_message_model_field(self):
        record = {"message": {"model": "claude-x"}}
        assert local_data._model_of_transcript_record(record) == "claude-x"

    def test_top_level_model_field(self):
        assert local_data._model_of_transcript_record({"model": "claude-y"}) == "claude-y"

    def test_missing_returns_none(self):
        assert local_data._model_of_transcript_record({"type": "user"}) is None

    def test_non_string_model_ignored(self):
        assert local_data._model_of_transcript_record({"message": {"model": 5}}) is None


class TestActivitySortKey:
    def test_epoch_millis_int(self):
        """The real on-disk shape -- `lastActivityAt` is epoch milliseconds
        as an integer, not an ISO string."""
        assert local_data._activity_sort_key(1783349183415) == 1783349183415.0

    def test_iso_string_still_parses(self):
        assert local_data._activity_sort_key("2026-01-01T00:00:00Z") > 0

    def test_unparseable_and_missing_sort_last(self):
        assert local_data._activity_sort_key(None) == 0.0
        assert local_data._activity_sort_key("not a date") == 0.0
        assert local_data._activity_sort_key(True) == 0.0


class TestNormalizeFolders:
    def test_bare_strings(self):
        assert local_data._normalize_folders(["/a", "/b"]) == ["/a", "/b"]

    def test_dict_entries(self):
        assert local_data._normalize_folders([{"path": "/a"}, {"path": "/b"}]) == ["/a", "/b"]

    def test_mixed_and_malformed_entries_are_skipped(self):
        assert local_data._normalize_folders(["/a", {"path": "/b"}, {"nope": 1}, 5]) == ["/a", "/b"]

    def test_non_list_returns_empty(self):
        assert local_data._normalize_folders(None) == []
        assert local_data._normalize_folders("/a") == []


# --------------------------------------------------------------------------------
# check_data_access
# --------------------------------------------------------------------------------


@pytest.fixture
def no_data_paths(tmp_path, monkeypatch):
    missing_desktop = tmp_path / "nowhere" / "Claude"
    missing_output = tmp_path / "nowhere" / "Output"
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [missing_desktop])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [missing_output])
    return tmp_path


async def test_check_data_access_warns_when_nothing_visible(no_data_paths):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        assert r.data["ok"] is False
        assert any("No Claude Desktop app-data directory" in w for w in r.data["warnings"])


async def test_check_data_access_never_probes_the_cli(no_data_paths):
    """The CLI is out of scope -- `~/.claude` must not appear anywhere in
    the result, and its absence must never be reported as a problem."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        assert "cli" not in r.data
        assert ".claude" not in json.dumps(r.data)


async def test_check_data_access_warns_when_only_output_folder_missing(tmp_path, monkeypatch):
    desktop = tmp_path / "Claude"
    desktop.mkdir()
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])
    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        assert r.data["ok"] is True
        assert any("output folder isn't visible" in w for w in r.data["warnings"])


async def test_check_data_access_no_warnings_when_both_present(tmp_path, monkeypatch):
    desktop = tmp_path / "Claude"
    desktop.mkdir()
    output = tmp_path / "ClaudeOutput"
    output.mkdir()
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [output])
    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        assert r.data["ok"] is True
        assert r.data["warnings"] == []


# --------------------------------------------------------------------------------
# list_local_workspace -- full Desktop fixture tree
# --------------------------------------------------------------------------------

# Standing-instruction texts shared between the fixture and the
# get_instructions_inventory tests. Lines are deliberately over
# _DUPLICATE_LINE_MIN_CHARS so overlap detection has something real to find.
GLOBAL_CURRENT = "Always answer in plain language.\nNever use bullet points unless asked."
GLOBAL_OLDER = "Be extremely terse in every response."
SHARED_PROJECT_LINE = "Focus on the proj1 codebase."
SPACE_ONE_INSTRUCTIONS = f"Always answer in plain language.\n{SHARED_PROJECT_LINE}"
SPACE_TWO_INSTRUCTIONS = f"{SHARED_PROJECT_LINE}\nSomething else entirely here."

# --- local memory -------------------------------------------------------------
#
# Memory is NOT export-only: three separate stores sit on disk, and the
# per-Space one (several .md files per Space) is the largest of the three.
# A local-only run has to be able to see all of it -- that is the whole
# point of the change, so these fixtures live in the local-only workspace
# tree with no export anywhere near them.

# The house template every memory file is written to: a YAML front-matter
# block and section headings written with bold rather than `#`. Repeated
# verbatim across files by construction, so it must NOT register as "the
# same thing remembered under two different Spaces" -- that is a finding
# about the template, not about the user.
MEMORY_TEMPLATE_PREAMBLE = "---\nname: memory\ntype: project\n---\n\n**Purpose & Context**\n"
# Remembered content that genuinely did spread across two owners.
MEMORY_DRIFT_LINE = "The user prefers invoices sent on the first of the month."
# A remembered line that merely restates the standing global instructions --
# context paid for twice on every turn.
MEMORY_ECHOES_GLOBAL = "Always answer in plain language."

ACCOUNT_MEMORY = f"{MEMORY_ECHOES_GLOBAL}\nThe user runs a two-person consultancy."
SPACE_ONE_MEMORY_INDEX = f"{MEMORY_TEMPLATE_PREAMBLE}{MEMORY_DRIFT_LINE}\n"
SPACE_ONE_MEMORY_TOPIC = "Proj1 ships on the last Friday of each quarter.\n"
ORPHAN_SPACE_ID = "space-deleted-in-the-app"
ORPHAN_SPACE_MEMORY = "The Acme rebrand wrapped up in March 2026.\n"
CLOUD_PROJECT_MEMORY = f"{MEMORY_TEMPLATE_PREAMBLE}{MEMORY_DRIFT_LINE}\n"
UNCACHED_PROJECT_UUID = "UNCACHED-MEMORY-UUID"
UNCACHED_PROJECT_MEMORY = "Notes for a Project whose metadata never cached here.\n"

# Session timestamps, chosen so the fixture exercises interval overlap rather
# than just creation date: sess1 is created before sess2 but stays active
# after it, so a late-February window must catch sess1 and miss sess2.
SESS1_CREATED, SESS1_ACTIVE = _ms(2026, 1, 1), _ms(2026, 3, 1)
SESS2_CREATED, SESS2_ACTIVE = _ms(2026, 2, 1), _ms(2026, 2, 15)
SESS3_CREATED, SESS3_ACTIVE = _ms(2025, 6, 1), _ms(2025, 6, 2)
SESS4_CREATED, SESS4_ACTIVE = _ms(2026, 3, 1), _ms(2026, 4, 1)


@pytest.fixture
def workspace_fixture(tmp_path, monkeypatch):
    desktop_root = tmp_path / "desktop" / "Claude"

    # Desktop: spaces.json, project-cache, and four local_*.json sessions.
    # spaces.json's real on-disk shape is {"spaces": [...]} -- a dict wrapper,
    # not a bare list (confirmed directly against a real Claude Desktop
    # install; a bare list used to be what both the code and this fixture
    # wrongly assumed, which silently made Spaces always empty).
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(org_dir / "spaces.json", {"spaces": [
        {
            "id": "space1", "name": "Space One", "description": "d",
            "instructions": SPACE_ONE_INSTRUCTIONS,
            "folders": [{"path": "/Users/x/proj1"}, "/Users/x/proj2"],
        },
        {
            "id": "space2", "name": "Space Two", "description": "d2",
            "instructions": SPACE_TWO_INSTRUCTIONS,
            "folders": ["/Users/x/nomatch"],
        },
    ]})
    _write_json(org_dir / ".project-cache" / "PROJ-UUID" / "metadata.json", {
        "uuid": "PROJ-UUID", "name": "Cloud Proj", "description": "cd", "synced_at": "2026-01-01",
        "prompt_template": "Be a meticulous cloud project assistant.",
    })
    # `lastActivityAt` is epoch milliseconds on a real install, not an ISO
    # string -- the instructions-variant ordering depends on that.
    _write_json(org_dir / "local_sess1.json", {
        "title": "Chat in Space",
        "createdAt": SESS1_CREATED, "lastActivityAt": SESS1_ACTIVE,
        "userSelectedFolders": ["/Users/x/proj1/sub"], "userSelectedProjectUuids": [],
        "enabledMcpTools": {"huge": "blob"}, "authToken": "shhh",
        "model": "claude-sonnet-5", "effort": "high",
        "memoryEnabled": True, "skillsEnabled": True, "pluginsEnabled": False,
        "systemPromptRendererAppends": [
            f"<user_preferences>\n{GLOBAL_CURRENT}\n</user_preferences>",
            "<project_instructions>\nIgnored -- see spaces.json instead.\n</project_instructions>",
        ],
    })
    _write_json(org_dir / "local_sess2.json", {
        "title": "Chat in Cloud Project",
        "createdAt": SESS2_CREATED, "lastActivityAt": SESS2_ACTIVE,
        "userSelectedFolders": [], "userSelectedProjectUuids": ["PROJ-UUID"],
    })
    _write_json(org_dir / "local_sess3.json", {
        "title": "Unfiled chat",
        "createdAt": SESS3_CREATED, "lastActivityAt": SESS3_ACTIVE,
        "userSelectedFolders": ["/Users/x/other"], "userSelectedProjectUuids": [],
        "systemPromptRendererAppends": [
            f"<user_preferences>\n{GLOBAL_OLDER}\n</user_preferences>",
        ],
    })
    _write_json(org_dir / "local_sess4.json", {
        "title": "Chat in uncached cloud project",
        "createdAt": SESS4_CREATED, "lastActivityAt": SESS4_ACTIVE,
        "userSelectedFolders": [], "userSelectedProjectUuids": ["UNCACHED-UUID"],
        "systemPromptRendererAppends": [
            f"<user_preferences>\n{GLOBAL_CURRENT}\n</user_preferences>",
        ],
    })
    # Code-tab session -- must NOT be picked up by read_local_sessions
    _write_json(desktop_root / "claude-code-sessions" / "ACCOUNT" / "ORG" / "local_codetab.json", {
        "title": "should be excluded",
    })

    # Local memory, in all three stores. `<space-id>` and `<project-uuid>`
    # in these paths are the same join keys spaces.json and .project-cache
    # already use, so the name join is the one the inventory already does.
    def _write_memory(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    _write_memory(org_dir / "memory" / "CLAUDE.md", ACCOUNT_MEMORY)
    _write_memory(
        org_dir / "spaces" / "space1" / "memory" / "MEMORY.md", SPACE_ONE_MEMORY_INDEX
    )
    _write_memory(
        org_dir / "spaces" / "space1" / "memory" / "invoicing.md", SPACE_ONE_MEMORY_TOPIC
    )
    # A Space deleted in the app whose memory was left behind on disk.
    _write_memory(
        org_dir / "spaces" / ORPHAN_SPACE_ID / "memory" / "MEMORY.md", ORPHAN_SPACE_MEMORY
    )
    # ...and one memory file that exists but cannot be read. Listed, not
    # dropped: a memory file that can't be read is itself worth knowing about.
    unreadable_memory = org_dir / "spaces" / "space2" / "memory" / "MEMORY.md"
    unreadable_memory.parent.mkdir(parents=True, exist_ok=True)
    unreadable_memory.write_bytes(b"remembered: \xff\xfe not utf-8")

    _write_memory(org_dir / ".project-cache" / "PROJ-UUID" / "memory.md", CLOUD_PROJECT_MEMORY)
    _write_memory(
        org_dir / ".project-cache" / UNCACHED_PROJECT_UUID / "memory.md",
        UNCACHED_PROJECT_MEMORY,
    )

    # Nested Cowork transcript for local_sess2 -- Cowork's own per-session
    # transcript, confirming a session isn't necessarily one model throughout
    # (a cheaper model ran mid-session here).
    cowork_transcript = (
        org_dir / "local_sess2" / ".claude" / "projects" / "proj" / "sub-session.jsonl"
    )
    cowork_transcript.parent.mkdir(parents=True)
    with cowork_transcript.open("w", encoding="utf-8") as f:
        f.write(json.dumps(
            {"type": "assistant", "message": {"model": "claude-sonnet-5", "role": "assistant"}}
        ) + "\n")
        f.write(json.dumps(
            {"type": "assistant",
             "message": {"model": "claude-haiku-4-5-20251001", "role": "assistant"}}
        ) + "\n")

    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop_root])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])
    return {"desktop_root": desktop_root}


async def test_list_local_workspace_has_no_cli_sessions_key(workspace_fixture):
    """CLI data is out of scope -- the inventory must not carry an empty
    `cli_sessions` list that a caller could mistake for "no CLI usage
    found" rather than "never looked."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        assert "cli_sessions" not in r.data


async def test_list_local_workspace_excludes_code_tab_sessions(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        titles = [s["title"] for s in r.data["local_sessions"]]
        assert "should be excluded" not in titles
        assert len(r.data["local_sessions"]) == 4


async def test_list_local_workspace_membership_join(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        membership = r.data["membership"]
        assert membership["by_space_id"]["space1"] == ["local_sess1"]
        assert membership["by_project_uuid"]["PROJ-UUID"] == ["local_sess2"]
        assert membership["unfiled_session_ids"] == ["local_sess3"]


async def test_list_local_workspace_membership_keeps_uncached_project_uuid(workspace_fixture):
    """A session declaring a real project uuid that has no local
    .project-cache entry yet must still be attributed to that project, not
    dropped into unfiled_session_ids -- the exact false positive the
    feedback doc's evidence described (a project that synced to the cloud
    after the session was created)."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        membership = r.data["membership"]
        assert membership["by_project_uuid"]["UNCACHED-UUID"] == ["local_sess4"]
        assert membership["uncached_project_uuids"] == ["UNCACHED-UUID"]
        assert "local_sess4" not in membership["unfiled_session_ids"]


async def test_list_local_workspace_spaces_and_project_cache(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        assert r.data["spaces"][0]["name"] == "Space One"
        assert r.data["spaces"][0]["folders"] == ["/Users/x/proj1", "/Users/x/proj2"]
        assert r.data["project_cache"][0]["uuid"] == "PROJ-UUID"
        assert (
            r.data["project_cache"][0]["prompt_template"]
            == "Be a meticulous cloud project assistant."
        )


async def test_list_local_workspace_environment_settings(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        by_id = {s["id"]: s for s in r.data["local_sessions"]}
        sess1 = by_id["local_sess1"]
        assert sess1["memory_enabled"] is True
        assert sess1["skills_enabled"] is True
        assert sess1["plugins_enabled"] is False
        assert sess1["custom_instructions"] == GLOBAL_CURRENT
        # local_sess2 never set these fields -- must read as None, not KeyError.
        assert by_id["local_sess2"]["memory_enabled"] is None
        assert by_id["local_sess2"]["custom_instructions"] is None
        # The Code-tab bridge field is gone along with the rest of the CLI surface.
        assert "cli_session_id" not in sess1


def test_read_spaces_accepts_bare_list(tmp_path):
    """Defensive backward-compat: a bare-list spaces.json (the shape this
    code originally assumed, and still what some other on-disk generation
    might use) must still parse, not just the confirmed real dict-wrapped
    shape."""
    desktop_root = tmp_path / "Claude"
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(org_dir / "spaces.json", [
        {"id": "space1", "name": "Bare List Space", "folders": ["/x"]},
    ])
    spaces = local_data.read_spaces(desktop_root)
    assert len(spaces) == 1
    assert spaces[0]["name"] == "Bare List Space"


class TestExtractCustomInstructions:
    def test_finds_user_preferences_block(self):
        appends = ["<user_preferences>\nBe terse.\n</user_preferences>"]
        assert local_data._extract_custom_instructions(appends) == "Be terse."

    def test_ignores_project_instructions_block(self):
        appends = ["<project_instructions>\nirrelevant\n</project_instructions>"]
        assert local_data._extract_custom_instructions(appends) is None

    def test_missing_returns_none(self):
        assert local_data._extract_custom_instructions([]) is None
        assert local_data._extract_custom_instructions(None) is None

    def test_returns_full_untruncated_text(self):
        """Extraction itself must not truncate -- `build_instructions_inventory`
        needs the real character count to report length findings. Truncation
        is the caller's, applied only to what gets returned."""
        long_text = "x" * 5000
        appends = [f"<user_preferences>{long_text}</user_preferences>"]
        assert local_data._extract_custom_instructions(appends) == long_text

    def test_caller_side_truncation_marks_the_cut(self):
        assert local_data._truncate("x" * 5000).endswith("...[truncated]")
        assert local_data._truncate("short") == "short"
        assert local_data._truncate(None) is None


# --------------------------------------------------------------------------------
# get_instructions_inventory -- the workspace-checkup data source
# --------------------------------------------------------------------------------


class TestNormalizeLines:
    def test_collapses_whitespace_and_lowercases(self):
        assert local_data._normalize_lines("  Always   Answer Plainly  ") == [
            "always answer plainly"
        ]

    def test_drops_lines_below_the_minimum_length(self):
        """Blank lines, bare bullets, and one-word headings must not count as
        meaningful duplication."""
        assert local_data._normalize_lines("\n- \nNotes\nA properly long line here") == [
            "a properly long line here"
        ]

    def test_none_returns_empty(self):
        assert local_data._normalize_lines(None) == []


async def test_get_instructions_inventory_groups_global_variants_by_recency(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        variants = r.data["global_instructions"]["variants"]

        assert r.data["global_instructions"]["variant_count"] == 2
        # sess4 (lastActivityAt 4000) carries GLOBAL_CURRENT, so it sorts first
        # even though the older text also exists on disk.
        assert variants[0]["text"] == GLOBAL_CURRENT
        assert variants[0]["session_count"] == 2
        assert variants[0]["char_count"] == len(GLOBAL_CURRENT)
        assert variants[0]["latest_activity_at"] == SESS4_ACTIVE
        assert variants[1]["text"] == GLOBAL_OLDER
        assert variants[1]["session_count"] == 1

        assert any("edited over time" in n for n in r.data["notes"])


async def test_get_instructions_inventory_flags_duplication_with_global(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        spaces = {s["name"]: s for s in r.data["spaces"]}

        # "Always answer in plain language." is in both the global text and
        # Space One's -- paid for twice on every turn in that Space.
        dup = spaces["Space One"]["duplicates_global"]
        assert dup["line_count"] == 1
        assert dup["sample_lines"] == ["always answer in plain language."]

        # Space Two shares nothing with the global text.
        assert spaces["Space Two"]["duplicates_global"]["line_count"] == 0


async def test_get_instructions_inventory_flags_lines_repeated_across_projects(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        repeated = r.data["repeated_across_projects"]
        assert len(repeated) == 1
        assert repeated[0]["line"] == SHARED_PROJECT_LINE.lower()
        assert sorted(repeated[0]["owners"]) == ["Space One", "Space Two"]
        assert repeated[0]["owner_count"] == 2


async def test_get_instructions_inventory_counts_session_reach_and_totals(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        spaces = {s["name"]: s for s in r.data["spaces"]}
        projects = {p["name"]: p for p in r.data["cloud_projects"]}

        # Only local_sess1 resolves into Space One; Space Two has no sessions,
        # which is itself the signal (instructions nobody is using).
        assert spaces["Space One"]["session_count"] == 1
        assert spaces["Space Two"]["session_count"] == 0
        assert projects["Cloud Proj"]["session_count"] == 1
        assert projects["Cloud Proj"]["char_count"] == len(
            "Be a meticulous cloud project assistant."
        )

        totals = r.data["totals"]
        assert totals["always_loaded_char_count"] == len(GLOBAL_CURRENT)
        largest = max(
            len(SPACE_ONE_INSTRUCTIONS),
            len(SPACE_TWO_INSTRUCTIONS),
            len("Be a meticulous cloud project assistant."),
        )
        assert totals["worst_case_char_count"] == len(GLOBAL_CURRENT) + largest


async def test_get_instructions_inventory_truncates_text_but_not_counts(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {"max_chars": 10})
        variant = r.data["global_instructions"]["variants"][0]
        assert variant["truncated"] is True
        assert variant["text"].endswith("...[truncated]")
        assert variant["char_count"] == len(GLOBAL_CURRENT)


async def test_get_instructions_inventory_without_desktop_dir(no_data_paths):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        assert r.data["global_instructions"]["variant_count"] == 0
        assert r.data["spaces"] == []
        assert r.data["totals"]["always_loaded_char_count"] == 0
        assert any("No Claude Desktop app-data directory" in n for n in r.data["notes"])


# --------------------------------------------------------------------------------
# G1 -- local memory, the fourth layer of standing context
# --------------------------------------------------------------------------------


async def test_get_instructions_inventory_reads_memory_on_a_local_only_run(
    workspace_fixture,
):
    """The point of the whole change: memory is NOT export-only.

    The docs said memory content could only come from an account data
    export, so the default local-only run -- the one a user gets by asking
    "review my Claude memory", a shipped trigger phrase -- had no memory
    lens at all. There are three stores on disk, and this fixture has all
    three with no export anywhere near it."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        memory = r.data["memory"]

        assert memory["available"] is True
        assert memory["source"] == "local_disk"
        assert memory["file_count"] == 7
        assert memory["files_found_count"] == 7
        assert memory["file_list_truncated"] is False

        # Account store.
        assert memory["account"]["file_count"] == 1
        assert memory["account"]["files"][0]["file_name"] == "CLAUDE.md"
        assert memory["account"]["files"][0]["text"] == ACCOUNT_MEMORY

        # Per-Space store -- several .md files under ONE Space, which is the
        # store a reader built to a single-file assumption would have missed.
        spaces = {entry["label"]: entry for entry in memory["spaces"]}
        assert spaces["Space One"]["file_count"] == 2
        assert {f["file_name"] for f in spaces["Space One"]["files"]} == {
            "MEMORY.md", "invoicing.md"
        }

        # Per-cloud-Project store.
        projects = {entry["label"]: entry for entry in memory["cloud_projects"]}
        assert projects["Cloud Proj"]["files"][0]["text"] == CLOUD_PROJECT_MEMORY

        assert memory["total_char_count"] == sum(
            len(text) for text in (
                ACCOUNT_MEMORY, SPACE_ONE_MEMORY_INDEX, SPACE_ONE_MEMORY_TOPIC,
                ORPHAN_SPACE_MEMORY, CLOUD_PROJECT_MEMORY, UNCACHED_PROJECT_MEMORY,
            )
        )
        assert any("local memory file(s) totalling" in n for n in r.data["notes"])
        # The export stays authoritative where the two disagree -- these
        # files are a per-device cache.
        assert any("the export is authoritative" in n for n in r.data["notes"])


async def test_get_instructions_inventory_joins_memory_to_space_and_project_names(
    workspace_fixture,
):
    """The join keys in a memory file's path are the same `<space-id>` and
    `<project-uuid>` the inventory already resolves to names, which is why
    memory belongs on this tool rather than in one of its own.

    Every owner comes back with a `label` a person can act on, and
    `session_count` says how much work the memory actually reached."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        memory = r.data["memory"]

        space_one = next(e for e in memory["spaces"] if e["space_id"] == "space1")
        assert space_one["name"] == "Space One"
        assert space_one["name_resolved"] is True
        assert space_one["label"] == "Space One"
        assert space_one["session_count"] == 1

        cloud = next(
            e for e in memory["cloud_projects"] if e["project_uuid"] == "PROJ-UUID"
        )
        assert cloud["name"] == "Cloud Proj"
        assert cloud["session_count"] == 1


async def test_get_instructions_inventory_never_names_an_owner_by_bare_id(
    workspace_fixture,
):
    """Memory outlives the Space or Project it belongs to. When the id can't
    be resolved the label says *why* rather than handing back a uuid, which
    names nothing the user has ever seen in the app."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        memory = r.data["memory"]

        orphan = next(e for e in memory["spaces"] if e["space_id"] == ORPHAN_SPACE_ID)
        assert orphan["name_resolved"] is False
        assert orphan["resolution"] == "space_id_not_in_spaces_json"
        assert ORPHAN_SPACE_ID not in orphan["label"]
        assert "no longer listed in spaces.json" in orphan["label"]
        # Its content is still here and still readable -- deleted in the app,
        # left behind on disk.
        assert orphan["files"][0]["text"] == ORPHAN_SPACE_MEMORY

        uncached = next(
            e for e in memory["cloud_projects"]
            if e["project_uuid"] == UNCACHED_PROJECT_UUID
        )
        assert uncached["resolution"] == "project_uuid_not_cached"
        assert UNCACHED_PROJECT_UUID not in uncached["label"]
        assert "isn't cached on this device" in uncached["label"]

        assert any("no longer in spaces.json" in n for n in r.data["notes"])


async def test_get_instructions_inventory_memory_duplication_signals(workspace_fixture):
    """The two findings memory is actually read for, and the filter that
    keeps the second one honest.

    `duplicates_global` catches memory that merely restates the standing
    instructions -- context paid for twice on every turn.
    `repeated_across_memory` catches memory that was meant to be scoped to
    one piece of work and has spread. Both fixture memory files carry the
    same house template (YAML front matter and a bold section heading), and
    reporting `type: project` as "remembered under two different Spaces"
    would be a finding about the template, not the user."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        memory = r.data["memory"]

        duplicates = memory["account"]["duplicates_global"]
        assert duplicates["line_count"] == 1
        assert duplicates["sample_lines"] == [MEMORY_ECHOES_GLOBAL.lower()]

        repeated = memory["repeated_across_memory"]
        assert [entry["line"] for entry in repeated] == [MEMORY_DRIFT_LINE.lower()]
        assert set(repeated[0]["owners"]) == {"Space One", "Cloud Proj"}
        assert repeated[0]["owner_count"] == 2

        # Template furniture, present verbatim in both files, reported by
        # neither.
        reported = {entry["line"] for entry in repeated}
        assert "type: project" not in reported
        assert "name: memory" not in reported
        assert "**purpose & context**" not in reported

        assert any("appear under two or more different" in n for n in r.data["notes"])


async def test_get_instructions_inventory_lists_an_unreadable_memory_file(
    workspace_fixture,
):
    """A memory file that exists and can't be read is itself worth knowing
    about, so it is listed with its problem rather than silently dropped --
    and its content is counted in no total."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        memory = r.data["memory"]

        assert memory["unreadable_file_count"] == 1
        broken = next(
            f for entry in memory["spaces"] for f in entry["files"] if not f["readable"]
        )
        assert broken["problem"] == "unreadable"
        assert broken["text"] is None
        assert broken["char_count"] == 0
        assert any("couldn't be read this run" in n for n in r.data["notes"])


async def test_get_instructions_inventory_caps_memory_text_but_not_counts(
    workspace_fixture,
):
    """`max_memory_chars` is separate from `max_chars` because memory is
    bulkier than an instruction block and there is more of it. The cap
    trims the returned text; `char_count` stays the true, uncapped
    length."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {"max_memory_chars": 20})
        account_file = r.data["memory"]["account"]["files"][0]
        assert account_file["truncated"] is True
        assert account_file["text"].endswith("...[truncated]")
        assert account_file["char_count"] == len(ACCOUNT_MEMORY)

        # The instruction cap is a different knob and is not affected by it.
        assert r.data["global_instructions"]["variants"][0]["truncated"] is False


# --------------------------------------------------------------------------------
# The memory reader's two safety properties: redaction-routed, and capped
# --------------------------------------------------------------------------------


class TestClampMaxChars:
    """A caller-supplied character cap, forced back into a range that caps
    something.

    A **negative** cap didn't bypass `_truncate` by skipping it -- it made
    `text[:max_chars]` slice from the *end*, returning all but the last few
    characters of the file while still reporting `truncated: True`. On the
    memory path that is the whole of the user's remembered business and
    personal context escaping a cap that looks like it is holding."""

    def test_an_unclamped_negative_cap_returns_the_file_it_claims_to_cap(self):
        """The defect itself, stated directly, so the clamp below is
        visibly fixing something rather than asserting a tautology."""
        assert local_data._truncate("abcdefghij", -1) == "abcdefghi ...[truncated]"

    def test_a_negative_cap_becomes_zero(self):
        assert local_data.clamp_max_chars(-1, 2000) == 0
        capped = local_data._truncate("abcdefghij", local_data.clamp_max_chars(-1, 2000))
        assert capped == " ...[truncated]"

    def test_a_non_integer_falls_back_to_the_default(self):
        # `True` is an `int` in Python and would otherwise clamp to 1 -- a
        # cap of one character, silently, from a caller that passed a flag.
        for value in (None, "2000", 1.5, True, False, [10]):
            assert local_data.clamp_max_chars(value, 2000) == 2000

    def test_a_usable_cap_is_left_alone(self):
        assert local_data.clamp_max_chars(0, 2000) == 0
        assert local_data.clamp_max_chars(50, 2000) == 50


async def test_get_instructions_inventory_clamps_a_negative_memory_cap(
    workspace_fixture,
):
    """The cap reached through the real MCP tool. Both knobs, because
    `max_chars` carries the identical defect one argument over."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "get_instructions_inventory", {"max_memory_chars": -1, "max_chars": -1}
        )
        account_file = r.data["memory"]["account"]["files"][0]
        assert account_file["text"] == " ...[truncated]"
        assert account_file["truncated"] is True
        # ...and the count is still the true, uncapped length.
        assert account_file["char_count"] == len(ACCOUNT_MEMORY)

        assert r.data["global_instructions"]["variants"][0]["text"] == " ...[truncated]"

        # Nothing of the remembered or standing text survived the cap.
        payload = json.dumps(r.data)
        assert "two-person consultancy" not in payload
        assert "Never use bullet points" not in payload


def test_read_memory_files_routes_every_return_path_through_redact(
    monkeypatch, tmp_path
):
    """D5. Memory is the most sensitive text this plugin ever reads --
    Claude's own remembered notes about the user's business, health, family
    and customers -- and it was the one reader that shipped outside the
    secret filter.

    Driven by replacing the reader rather than by planting a file, because
    the guarantee under test is about the *wrapper*: every return path,
    including any degraded one added later, is filtered. A fixture on disk
    could only ever check the paths that exist today."""
    seen: dict[str, int] = {}

    def fake_reader(base_dir, max_chars):
        seen["max_chars"] = max_chars
        return {
            "files": [
                {
                    "file_name": "CLAUDE.md",
                    "text": "The user runs a two-person consultancy.",
                    "authToken": "MEMORY-READER-SECRET-DO-NOT-LEAK",
                }
            ],
            "found_count": 1,
            "read_count": 1,
        }

    monkeypatch.setattr(local_data, "_read_memory_files", fake_reader)

    result = local_data.read_memory_files(tmp_path)
    assert "authToken" not in result["files"][0]
    assert "MEMORY-READER-SECRET-DO-NOT-LEAK" not in json.dumps(result)
    # Redacted, not gutted -- the content the tool exists to report survives.
    assert result["files"][0]["file_name"] == "CLAUDE.md"
    assert seen["max_chars"] == local_data._MEMORY_DEFAULT_MAX_CHARS

    # ...and the cap the reader is handed is the clamped one.
    local_data.read_memory_files(tmp_path, max_chars=-5)
    assert seen["max_chars"] == 0


# --------------------------------------------------------------------------------
# A memory owner that exists on disk and was never given a name
# --------------------------------------------------------------------------------
#
# `owner_id in space_names` used to be the whole resolution test, so a Space
# listed with a null name scored `matched`, produced a `label` of None, and
# the None fell through to a `str(owner_id)` fallback in
# `repeated_across_memory[].owners` -- a bare uuid in the one field a person
# reads as a list of the Spaces their memory has spread across.

NAMED_SPACE_OWNER_ID = "space-with-a-name"
UNNAMED_SPACE_OWNER_ID = "3f8c1e22-0000-4000-8000-unnamedspace"
UNNAMED_PROJECT_OWNER_ID = "9a2b7d04-0000-4000-8000-unnamedproj"
# Remembered content shared by all three owners, so it reaches
# `repeated_across_memory` and its `owners` list has to name each of them.
SPREAD_MEMORY_LINE = "Deliveries are scheduled for the first Tuesday."


@pytest.fixture
def unnamed_owner_fixture(tmp_path, monkeypatch):
    desktop_root = tmp_path / "desktop" / "Claude"
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"

    _write_json(org_dir / "spaces.json", {"spaces": [
        {"id": NAMED_SPACE_OWNER_ID, "name": "Client Work", "folders": []},
        # Listed by the app, carrying no `name` key at all. Not a deleted
        # Space: this one is sitting in the user's sidebar.
        {"id": UNNAMED_SPACE_OWNER_ID, "folders": []},
    ]})
    _write_json(
        org_dir / ".project-cache" / UNNAMED_PROJECT_OWNER_ID / "metadata.json",
        {"uuid": UNNAMED_PROJECT_OWNER_ID},
    )

    for owner in (NAMED_SPACE_OWNER_ID, UNNAMED_SPACE_OWNER_ID):
        path = org_dir / "spaces" / owner / "memory" / "MEMORY.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{SPREAD_MEMORY_LINE}\n", encoding="utf-8")
    project_memory = org_dir / ".project-cache" / UNNAMED_PROJECT_OWNER_ID / "memory.md"
    project_memory.write_text(f"{SPREAD_MEMORY_LINE}\n", encoding="utf-8")

    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop_root])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])
    return desktop_root


class TestOwnerResolution:
    """The resolution itself. "Listed without a name" and "not listed at
    all" are different situations and get different sentences: telling a
    user a Space was *deleted* when it is sitting unnamed in their sidebar
    is a wrong answer, not a vaguer one."""

    def test_a_listed_owner_with_no_usable_name_is_not_a_deleted_one(self):
        for name in (None, "", "   "):
            assert local_data._owner_resolution(
                "s1", {"s1": name}, "space_id_not_in_spaces_json"
            ) == "space_listed_without_a_name"
            assert local_data._owner_resolution(
                "p1", {"p1": name}, "project_uuid_not_cached"
            ) == "project_listed_without_a_name"

    def test_an_owner_that_is_not_listed_keeps_the_missing_sentence(self):
        assert local_data._owner_resolution(
            "s1", {}, "space_id_not_in_spaces_json"
        ) == "space_id_not_in_spaces_json"
        assert local_data._owner_resolution(
            "p1", {}, "project_uuid_not_cached"
        ) == "project_uuid_not_cached"

    def test_no_id_at_all_says_so(self):
        assert local_data._owner_resolution(
            None, {}, "space_id_not_in_spaces_json"
        ) == "no_owner_id"

    def test_a_named_owner_matches(self):
        assert local_data._owner_resolution(
            "s1", {"s1": "Client Work"}, "space_id_not_in_spaces_json"
        ) == "matched"


async def test_get_instructions_inventory_describes_an_unnamed_owner(
    unnamed_owner_fixture,
):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
    memory = r.data["memory"]

    unnamed = next(
        e for e in memory["spaces"] if e["space_id"] == UNNAMED_SPACE_OWNER_ID
    )
    assert unnamed["resolution"] == "space_listed_without_a_name"
    assert unnamed["name_resolved"] is False
    assert unnamed["name"] is None
    assert UNNAMED_SPACE_OWNER_ID not in unnamed["label"]
    assert "never given one" in unnamed["label"]
    # Not the deleted-Space sentence. This Space still exists.
    assert "deleted" not in unnamed["label"]

    project = next(
        e for e in memory["cloud_projects"]
        if e["project_uuid"] == UNNAMED_PROJECT_OWNER_ID
    )
    assert project["resolution"] == "project_listed_without_a_name"
    assert project["name"] is None
    assert project["name_resolved"] is False
    assert UNNAMED_PROJECT_OWNER_ID not in project["label"]

    # ...and a named owner is unaffected.
    named = next(e for e in memory["spaces"] if e["space_id"] == NAMED_SPACE_OWNER_ID)
    assert named["resolution"] == "matched"
    assert named["name_resolved"] is True
    assert named["label"] == "Client Work"

    assert any("listed by Claude with no name" in n for n in r.data["notes"])


async def test_no_bare_owner_id_escapes_through_repeated_across_memory(
    unnamed_owner_fixture,
):
    """THE regression for this defect. `repeated_across_memory[].owners` is
    read as "the Spaces your memory has spread across" -- a uuid there
    names nothing the user has ever seen in the app."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
    memory = r.data["memory"]

    entry = next(
        e for e in memory["repeated_across_memory"]
        if e["line"] == SPREAD_MEMORY_LINE.lower()
    )
    assert entry["owner_count"] == 3
    assert "Client Work" in entry["owners"]
    assert all(owner for owner in entry["owners"]), "an owner came back unlabelled"

    spread = json.dumps(memory["repeated_across_memory"])
    for owner_id in (UNNAMED_SPACE_OWNER_ID, UNNAMED_PROJECT_OWNER_ID):
        assert owner_id not in spread

    # The ids still appear where they belong -- the explicit id fields --
    # so this is a labelling guarantee, not a claim the id was discarded.
    assert UNNAMED_SPACE_OWNER_ID in json.dumps(memory["spaces"])


async def test_get_instructions_inventory_dates_memory_by_local_modification_time(
    workspace_fixture,
):
    """Memory files carry no timestamp of their own, so the filesystem mtime
    is the only staleness signal there is -- and it is labelled the same way
    every other instant in this plugin is (D1): UTC, local, and a local
    weekday, never a bare date the reader has to convert."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        account_file = r.data["memory"]["account"]["files"][0]
        modified = account_file["modified_at_ms"]

        assert account_file["modified_at_iso"].endswith("+00:00")
        assert account_file["modified_at_local_date"] == _local_date(modified)
        assert account_file["modified_at_local_weekday"] == _local_weekday(modified)
        assert account_file["modified_at_local_iso"].startswith(_local_date(modified))
        # Written moments ago by the fixture, so nothing is stale.
        assert account_file["days_since_modified"] < 1
        assert r.data["memory"]["stale_file_count"] == 0
        assert r.data["memory"]["stale_after_days"] == 90.0


async def test_get_instructions_inventory_memory_excluded_from_always_loaded_totals(
    workspace_fixture,
):
    """Memory loads on demand rather than on every turn, so folding it into
    `totals` would overstate what a session pays for. It is measured
    separately and deliberately."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        assert r.data["memory"]["total_char_count"] > 0
        assert r.data["totals"]["always_loaded_char_count"] == len(GLOBAL_CURRENT)


async def test_get_instructions_inventory_with_no_memory_files(tmp_path, monkeypatch):
    """No memory files is not proof of no memory. The app writes these as it
    runs, so a fresh install, a second machine, or memory turned off for
    every Space all look identical here -- and the block still comes back
    with the same keys rather than as a None a caller has to guard."""
    desktop_root = tmp_path / "desktop" / "Claude"
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(org_dir / "local_sess1.json", {"title": "Chat", "createdAt": _ms(2026, 1, 1)})
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop_root])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])

    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        memory = r.data["memory"]
        assert memory["available"] is False
        assert memory["file_count"] == 0
        assert memory["spaces"] == []
        assert memory["cloud_projects"] == []
        assert memory["repeated_across_memory"] == []
        assert set(memory) == set(local_data._empty_memory_block())
        assert any("not proof of absent memory" in n for n in r.data["notes"])


async def test_get_instructions_inventory_memory_survives_a_missing_desktop_dir(
    no_data_paths,
):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})
        assert r.data["memory"]["available"] is False
        assert r.data["memory"]["file_count"] == 0


# --------------------------------------------------------------------------------
# Local-time rendering, everywhere a timestamp reaches a user
# --------------------------------------------------------------------------------
#
# The convention: `<prefix>_iso` in UTC for comparison and joining, and
# `<prefix>_local_iso` / `_local_date` / `_local_weekday` / `_utc_offset` on
# this machine's clock for anything a person reads, with a `timezone` block
# naming the zone.
#
# It shipped applied to exactly one tool. `get_desktop_settings` emitted UTC
# only, which on a machine west of UTC reported a first-launch date wrong by
# a calendar day and a weekday -- in the one figure the docs tell an analyst
# to quote back. `check_data_access` emitted a raw epoch-**seconds** float
# with no rendering at all, and `get_instructions_inventory`'s variant
# `latest_activity_at` was raw epoch milliseconds.
#
# Every fixture below is anchored on an instant whose LOCAL calendar day is
# not its UTC calendar day (see `straddling_sample`). A fixture whose two
# dates agree would pass under UTC-only rendering just as happily and would
# prove nothing.


@pytest.fixture
def straddling_instant(straddling_sample):
    """`straddling_sample` under a name that doesn't imply a usage sample --
    it is just one instant, described in both zones."""
    return straddling_sample


@pytest.fixture
def straddling_desktop(tmp_path, monkeypatch, straddling_instant):
    """A Desktop tree whose every timestamp is that one straddling instant:
    a session's `createdAt`/`lastActivityAt`, a cloud Project's `synced_at`,
    and a memory file's modification time."""
    at = straddling_instant["at"]
    seconds = at / 1000
    desktop_root = tmp_path / "desktop" / "Claude"
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"

    _write_json(org_dir / "spaces.json", {"spaces": [
        {
            "id": "space1", "name": "Space One", "folders": [],
            "instructions": "Keep invoice answers short.",
        },
    ]})
    _write_json(org_dir / ".project-cache" / "PROJ-UUID" / "metadata.json", {
        "uuid": "PROJ-UUID", "name": "Cloud Proj",
        # Written as an ISO string on a real install -- which says nothing
        # about which zone the reader is in, so it still gets rendered.
        "synced_at": datetime.fromtimestamp(seconds, UTC).isoformat(),
        "prompt_template": "Cloud project instructions.",
    })
    _write_json(org_dir / "local_straddle.json", {
        "title": "One chat",
        "createdAt": at, "lastActivityAt": at,
        "spaceId": "space1", "spaceIdSetBy": "user",
        "userSelectedFolders": [], "userSelectedProjectUuids": [],
        "systemPromptRendererAppends": [
            "<user_preferences>\nAlways answer in plain language.\n</user_preferences>",
        ],
    })

    memory = org_dir / "spaces" / "space1" / "memory" / "MEMORY.md"
    memory.parent.mkdir(parents=True, exist_ok=True)
    memory.write_text("The user prefers invoices on the first.\n", encoding="utf-8")
    os.utime(memory, (seconds, seconds))

    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop_root])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])
    return desktop_root


def _missing_local_companions(payload) -> list[str]:
    """Every `<prefix>_iso` key in `payload` with no `<prefix>_local_iso`
    sibling, as dotted paths.

    A UTC instant is the one form of a date this plugin's audience cannot
    read. Checking the pairing structurally rather than field by field is
    what stops a new timestamp shipping with half the convention -- which is
    exactly how three of these got out."""
    gaps: list[str] = []

    def walk(node, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.endswith("_iso") and not key.endswith("_local_iso"):
                    if f"{key[: -len('_iso')]}_local_iso" not in node:
                        gaps.append(f"{path}.{key}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    walk(payload, "")
    return gaps


def test_the_local_companion_sweep_catches_a_utc_only_timestamp():
    """Guard the guard. The sweep is only worth running if a lone `*_iso`
    actually trips it."""
    assert _missing_local_companions({"a": {"first_launch_at_iso": "x"}}) == [
        ".a.first_launch_at_iso"
    ]
    assert _missing_local_companions([{"at_iso": "x"}]) == ["[0].at_iso"]
    assert _missing_local_companions(
        {"at_iso": "x", "at_local_iso": "y"}
    ) == []


async def test_list_local_workspace_renders_every_date_in_local_time(
    straddling_desktop, straddling_instant
):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})

    session = r.data["local_sessions"][0]
    for prefix in ("created_at", "last_activity_at"):
        assert session[f"{prefix}_iso"].startswith(straddling_instant["utc_date"])
        assert session[f"{prefix}_local_date"] == straddling_instant["local_date"]
        assert session[f"{prefix}_local_weekday"] == straddling_instant["local_weekday"]
        assert session[f"{prefix}_local_iso"].startswith(straddling_instant["local_date"])
        assert session[f"{prefix}_utc_offset"] == straddling_instant["utc_offset"]

    project = r.data["project_cache"][0]
    assert project["synced_at_iso"].startswith(straddling_instant["utc_date"])
    assert project["synced_at_local_date"] == straddling_instant["local_date"]

    assert r.data["timezone"]["utc_offset"].startswith("UTC")
    assert "machine's own timezone" in r.data["local_time_note"]
    assert _missing_local_companions(r.data) == []


async def test_get_instructions_inventory_renders_every_date_in_local_time(
    straddling_desktop, straddling_instant
):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {})

    variant = r.data["global_instructions"]["variants"][0]
    # This one carried no rendering at all -- raw epoch milliseconds, in a
    # field whose whole job is answering "which text is the current one".
    assert variant["latest_activity_at"] == straddling_instant["at"]
    assert variant["latest_activity_at_iso"].startswith(straddling_instant["utc_date"])
    assert variant["latest_activity_at_local_date"] == straddling_instant["local_date"]
    assert variant["latest_activity_at_local_weekday"] == (
        straddling_instant["local_weekday"]
    )

    memory = r.data["memory"]
    assert memory["newest_modified_at_iso"].startswith(straddling_instant["utc_date"])
    assert memory["newest_modified_at_local_date"] == straddling_instant["local_date"]
    space = memory["spaces"][0]
    assert space["newest_modified_at_local_date"] == straddling_instant["local_date"]
    assert space["oldest_modified_at_local_weekday"] == (
        straddling_instant["local_weekday"]
    )

    assert r.data["timezone"]["utc_offset"].startswith("UTC")
    assert any("machine's own timezone" in n for n in r.data["notes"])
    assert _missing_local_companions(r.data) == []


async def test_check_data_access_renders_every_probed_timestamp(
    tmp_path, monkeypatch, straddling_instant
):
    """`check_path` hands back `modified` as a raw epoch-**seconds** float:
    not a date anyone can read, and one unit conversion away from being read
    as 1970. All three probe blocks get the standard rendering, not just the
    `data_files` one added most recently."""
    seconds = straddling_instant["at"] / 1000
    desktop = tmp_path / "Claude"
    desktop.mkdir()
    output = tmp_path / "ClaudeOutput"
    output.mkdir()
    _write_json(desktop / local_data.APP_CONFIG_FILENAME, {"locale": "en-US"})
    for path in (desktop / local_data.APP_CONFIG_FILENAME, desktop, output):
        os.utime(path, (seconds, seconds))
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [output])

    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})

    probes = [
        *r.data["desktop_candidates"],
        *r.data["output_candidates"],
        *r.data["data_files"]["files"].values(),
    ]
    rendered = [p for p in probes if "modified" in p]
    assert len(rendered) == 3, "the fixture planted three readable paths to probe"
    for probe in rendered:
        assert probe["modified_local_date"] == straddling_instant["local_date"]
        assert probe["modified_local_weekday"] == straddling_instant["local_weekday"]
        assert probe["modified_iso"].startswith(straddling_instant["utc_date"])
        assert probe["modified_utc_offset"] == straddling_instant["utc_offset"]
        # The raw epoch-seconds float stays -- it is what arithmetic wants.
        assert probe["modified"] == pytest.approx(seconds)

    assert r.data["timezone"]["utc_offset"].startswith("UTC")
    assert _missing_local_companions(r.data) == []


# --------------------------------------------------------------------------------
# list_local_workspace -- filter/projection params
# --------------------------------------------------------------------------------


async def test_list_local_workspace_filters_by_session_ids(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "list_local_workspace", {"session_ids": ["local_sess1"]}
        )
        assert [s["id"] for s in r.data["local_sessions"]] == ["local_sess1"]


async def test_list_local_workspace_filters_by_project_uuid(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "list_local_workspace", {"project_uuid": "PROJ-UUID"}
        )
        assert [s["id"] for s in r.data["local_sessions"]] == ["local_sess2"]


async def test_list_local_workspace_filters_by_folder_path(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "list_local_workspace", {"folder_path": "/Users/x/proj1"}
        )
        ids = {s["id"] for s in r.data["local_sessions"]}
        # local_sess1's own folder is nested under /Users/x/proj1; unrelated
        # sessions (sess2, sess3, sess4) must not match.
        assert ids == {"local_sess1"}


async def test_list_local_workspace_projects_fields(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "list_local_workspace", {"fields": ["title"]}
        )
        sess1 = next(s for s in r.data["local_sessions"] if s["id"] == "local_sess1")
        # join key always kept even though not in `fields`, everything else dropped.
        assert set(sess1.keys()) == {"id", "title"}
        assert sess1["title"] == "Chat in Space"


# --------------------------------------------------------------------------------
# get_project_membership
# --------------------------------------------------------------------------------


async def test_get_project_membership_all_sessions(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_project_membership", {})
        rows_by_id = {row["session_id"]: row for row in r.data["rows"]}

        assert rows_by_id["local_sess1"]["space_id"] == "space1"
        assert rows_by_id["local_sess1"]["space_name"] == "Space One"
        assert rows_by_id["local_sess1"]["cloud_project_uuids"] == []
        assert rows_by_id["local_sess1"]["local_folder_paths"] == ["/Users/x/proj1/sub"]

        assert rows_by_id["local_sess2"]["cloud_project_uuids"] == ["PROJ-UUID"]
        assert rows_by_id["local_sess2"]["cloud_project_names"] == ["Cloud Proj"]
        assert rows_by_id["local_sess2"]["space_id"] is None

        assert rows_by_id["local_sess4"]["cloud_project_uuids"] == ["UNCACHED-UUID"]
        assert rows_by_id["local_sess4"]["cloud_project_names"] == [None]

        assert r.data["uncached_project_uuids"] == ["UNCACHED-UUID"]


async def test_get_project_membership_filters_by_session_ids(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "get_project_membership", {"session_ids": ["local_sess2"]}
        )
        assert [row["session_id"] for row in r.data["rows"]] == ["local_sess2"]


# --------------------------------------------------------------------------------
# The chat -> Space join
# --------------------------------------------------------------------------------
#
# The app writes a chat's Space down: a session file carries an explicit
# `spaceId` that resolves straight through spaces.json. An earlier version of
# this plugin believed no such field existed and reconstructed membership by
# matching each session's folders against every Space's folders by prefix,
# taking the FIRST match in file order. On a real install that disagreed with
# the app on every one of the 59 sessions that had a recorded Space: an
# "OneDrive-Personal" Space scoped to a whole cloud-storage root swallowed the
# 36 chats filed under a "Vacation - July 2026" Space nested several levels
# beneath it, and the reorganization lens -- this plugin's headline output --
# then told the user the nested Spaces were empty.
#
# Every fixture below is shaped so the OLD behavior fails it. A test both
# behaviors pass would prove nothing about the bug that shipped.

ONEDRIVE_ROOT = "/Users/x/CloudStorage/OneDrive-Personal"
VACATION_FOLDER = f"{ONEDRIVE_ROOT}/Vacations/July 2026"
SIBLING_FOLDER = "/Users/x/proj"
# `/Users/x/projector` starts with `/Users/x/proj` as a *string* and is an
# unrelated directory as a *path*.
BOUNDARY_FOLDER = "/Users/x/projector"
DELETED_SPACE_ID = "space-deleted-after-this-chat-was-filed"
UNNAMED_SPACE_ID = "space-listed-with-no-name"
JOIN_PROJECT_UUID = "JOIN-PROJ-UUID"

# Every attribution key `join_local_sessions` produces. `list_local_workspace`
# and `get_project_membership` must answer with all of them, identically --
# they silently disagreed in production, one reporting a name for a session the
# other reported as belonging to no Space at all.
ATTRIBUTION_KEYS = frozenset({
    "space_id", "space_name", "space_resolved", "space_resolution", "space_label",
    "space_source", "space_source_note", "space_from_app_record", "space_matched_folder",
})


@pytest.fixture
def space_join_fixture(tmp_path, monkeypatch):
    """The real-world shape that produced the shipped defect, in miniature.

    `spaces.json` deliberately lists the broad cloud-storage-root Space
    FIRST, so a first-match-in-file-order join picks it and a
    most-specific-match join does not."""
    desktop_root = tmp_path / "desktop" / "Claude"
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"

    _write_json(org_dir / "spaces.json", {"spaces": [
        {"id": "space-onedrive", "name": "OneDrive-Personal", "folders": [ONEDRIVE_ROOT]},
        {"id": "space-vacation", "name": "Vacation - July 2026",
         "folders": [VACATION_FOLDER]},
        {"id": "space-sibling", "name": "Sibling Project", "folders": [SIBLING_FOLDER]},
        # Listed, and carrying no name at all -- a different situation from
        # an id spaces.json doesn't carry, and a different sentence.
        {"id": UNNAMED_SPACE_ID, "folders": []},
    ]})
    _write_json(org_dir / ".project-cache" / JOIN_PROJECT_UUID / "metadata.json", {
        "uuid": JOIN_PROJECT_UUID, "name": "Cloud Project",
    })

    def _session(name: str, **fields) -> None:
        _write_json(org_dir / f"{name}.json", {
            "title": name,
            "createdAt": _ms(2026, 5, 1),
            "lastActivityAt": _ms(2026, 5, 2),
            "userSelectedFolders": [],
            "userSelectedProjectUuids": [],
            **fields,
        })

    # The exact real-world shape: a chat the app filed in the nested Space,
    # working in a folder that ALSO sits under the broad parent Space.
    _session(
        "local_recorded",
        spaceId="space-vacation", spaceIdSetBy="user",
        userSelectedFolders=[VACATION_FOLDER],
    )
    # Precedence in isolation: the recorded Space and the folder-matched Space
    # are unrelated, so only the recorded one can produce this answer.
    _session(
        "local_recorded_disagrees_with_folder",
        spaceId="space-sibling", spaceIdSetBy="user",
        userSelectedFolders=[ONEDRIVE_ROOT],
    )
    # Nothing recorded -- the folder fallback, and the nesting it has to get
    # right. The deepest folder wins, not the first one listed.
    _session("local_inferred", userSelectedFolders=[f"{VACATION_FOLDER}/photos"])
    # A sibling directory whose path merely shares a string prefix.
    _session("local_boundary", userSelectedFolders=[BOUNDARY_FOLDER])
    # No Space recorded, no folder, no cloud Project: genuinely in no Space.
    _session("local_bare")
    # A cloud Project member whose folder would otherwise be swallowed by the
    # broad Space.
    _session(
        "local_cloud_project",
        userSelectedProjectUuids=[JOIN_PROJECT_UUID],
        userSelectedFolders=[ONEDRIVE_ROOT],
    )
    # A Space the user deleted in the app after filing this chat in it.
    _session("local_deleted_space", spaceId=DELETED_SPACE_ID, spaceIdSetBy="user")
    # ...and one that still exists and was never given a name.
    _session("local_unnamed_space", spaceId=UNNAMED_SPACE_ID, spaceIdSetBy="user")

    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop_root])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])
    return desktop_root


async def _membership_rows() -> dict[str, dict]:
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_project_membership", {})
    return {row["session_id"]: row for row in r.data["rows"]}


async def _inventory_sessions() -> dict[str, dict]:
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
    return {s["id"]: s for s in r.data["local_sessions"]}


class TestBestFolderMatch:
    """The folder fallback's two rules, checked directly. Both were absent
    from the shipped join, and each one on its own is enough to file a chat
    under the wrong Space."""

    def test_most_specific_folder_wins_not_the_first_listed(self):
        """Spaces nest. Returning the first match in file order let a Space
        scoped to a cloud-storage root swallow every Space beneath it."""
        match = local_data._best_folder_match(
            [f"{VACATION_FOLDER}/photos"], [ONEDRIVE_ROOT, VACATION_FOLDER]
        )
        assert match == (len(VACATION_FOLDER), VACATION_FOLDER)
        # ...and the answer does not depend on which order they were listed in.
        assert local_data._best_folder_match(
            [f"{VACATION_FOLDER}/photos"], [VACATION_FOLDER, ONEDRIVE_ROOT]
        ) == (len(VACATION_FOLDER), VACATION_FOLDER)

    def test_matches_on_a_path_boundary_not_a_string_prefix(self):
        assert local_data._best_folder_match([BOUNDARY_FOLDER], [SIBLING_FOLDER]) is None
        assert local_data._folder_matches_space([BOUNDARY_FOLDER], [SIBLING_FOLDER]) is False

    def test_the_folder_itself_matches(self):
        assert local_data._best_folder_match([SIBLING_FOLDER], [SIBLING_FOLDER]) == (
            len(SIBLING_FOLDER), SIBLING_FOLDER
        )
        # A trailing slash is the same folder, not a different one.
        assert local_data._best_folder_match(
            [f"{SIBLING_FOLDER}/"], [SIBLING_FOLDER]
        ) is not None

    def test_a_space_scoped_to_the_root_swallows_nothing(self):
        """A Space whose folder is bare `/` contains every session on the
        machine. That is the swallowing failure in its purest form, so it is
        skipped rather than allowed to file everything under one Space."""
        assert local_data._best_folder_match([VACATION_FOLDER], ["/"]) is None
        assert local_data._best_folder_match([VACATION_FOLDER], ["", "/"]) is None

    def test_no_folders_on_either_side_matches_nothing(self):
        assert local_data._best_folder_match([], [ONEDRIVE_ROOT]) is None
        assert local_data._best_folder_match([VACATION_FOLDER], []) is None


async def test_join_prefers_the_app_record_over_a_broader_folder_match(
    space_join_fixture,
):
    """THE regression. A chat the app filed in "Vacation - July 2026", working
    in a folder that also sits under a Space scoped to the whole OneDrive
    root. The old join returned "OneDrive-Personal" here -- for 36 chats on a
    real install -- and reported the vacation Space as empty."""
    rows = await _membership_rows()
    row = rows["local_recorded"]

    assert row["space_id"] == "space-vacation"
    assert row["space_name"] == "Vacation - July 2026"
    assert row["space_source"] == "app_record"
    assert row["space_from_app_record"] is True
    assert row["space_resolved"] is True
    assert row["space_resolution"] == "matched"
    # A recorded attribution is not a folder guess, so nothing here may read
    # as evidence that it was one.
    assert row["space_matched_folder"] is None
    assert "not worked out afterwards" in row["space_source_note"]

    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
    by_space = r.data["membership"]["by_space_id"]
    assert "local_recorded" in by_space["space-vacation"]
    assert "local_recorded" not in by_space.get("space-onedrive", [])
    # Nothing was filed under the broad parent Space at all: every chat that
    # would have been swallowed by it belongs somewhere more specific.
    assert "space-onedrive" not in by_space


async def test_join_takes_the_recorded_space_even_when_a_folder_says_otherwise(
    space_join_fixture,
):
    """Precedence with the two signals pointing at unrelated Spaces, so only
    the recorded one can produce this answer. The session's folder IS the
    OneDrive Space's own root folder -- about as strong as a folder inference
    gets -- and the app's record still wins."""
    rows = await _membership_rows()
    row = rows["local_recorded_disagrees_with_folder"]

    assert row["space_id"] == "space-sibling"
    assert row["space_name"] == "Sibling Project"
    assert row["space_source"] == "app_record"
    assert row["space_matched_folder"] is None


async def test_join_infers_the_most_specific_space_when_nothing_was_recorded(
    space_join_fixture,
):
    """The fallback is kept -- it is right often enough to be worth having --
    but it picks the deepest matching Space, and it is labelled a guess."""
    rows = await _membership_rows()
    row = rows["local_inferred"]

    assert row["space_id"] == "space-vacation"
    assert row["space_name"] == "Vacation - July 2026"
    assert row["space_source"] == "inferred_from_folder"
    assert row["space_from_app_record"] is False
    assert row["space_matched_folder"] == VACATION_FOLDER
    assert "best guess" in row["space_source_note"]


async def test_join_does_not_match_a_folder_across_a_path_boundary(space_join_fixture):
    """`/Users/x/projector` is not inside `/Users/x/proj`. String-prefix
    matching says it is."""
    rows = await _membership_rows()
    row = rows["local_boundary"]

    assert row["space_id"] is None
    assert row["space_source"] == "none"
    assert row["space_resolution"] == "no_space"

    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
    assert "local_boundary" in r.data["membership"]["unfiled_session_ids"]


async def test_join_leaves_a_session_with_no_signal_in_no_space(space_join_fixture):
    """No recorded Space, no folder, no cloud Project. "No Space" is the
    answer; inventing one from whatever folder happens to be nearby is how
    43 chats ended up in a generic cloud-storage folder."""
    rows = await _membership_rows()
    row = rows["local_bare"]

    assert row["space_id"] is None
    assert row["space_name"] is None
    assert row["space_resolved"] is False
    assert row["space_resolution"] == "no_space"
    assert row["space_source"] == "none"
    assert row["space_label"] == "not in any Space"
    assert row["space_source_note"] == "this chat is not in any Space"


async def test_join_puts_a_cloud_project_member_ahead_of_a_folder_guess(
    space_join_fixture,
):
    rows = await _membership_rows()
    row = rows["local_cloud_project"]

    assert row["cloud_project_uuids"] == [JOIN_PROJECT_UUID]
    assert row["cloud_project_names"] == ["Cloud Project"]
    assert row["space_id"] is None
    assert row["space_source"] == "none"


async def test_join_reports_an_unresolvable_space_id_without_printing_it(
    space_join_fixture,
):
    """A recorded Space id spaces.json no longer carries. It is reported as
    unresolved and described in words -- never handed back as a bare uuid,
    which names nothing the user has ever seen in the app."""
    rows = await _membership_rows()
    row = rows["local_deleted_space"]

    assert row["space_resolved"] is False
    assert row["space_resolution"] == "space_id_not_in_spaces_json"
    assert row["space_name"] is None
    assert DELETED_SPACE_ID not in row["space_label"]
    assert "most likely deleted in the app" in row["space_label"]

    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_project_membership", {})
    assert DELETED_SPACE_ID in r.data["unresolved_space_ids"]


async def test_join_separates_an_unnamed_space_from_a_deleted_one(space_join_fixture):
    """A Space that exists and was never named is not a deleted Space.
    Telling the user it was deleted is a wrong answer, not a vaguer one."""
    rows = await _membership_rows()
    row = rows["local_unnamed_space"]

    assert row["space_resolved"] is False
    assert row["space_resolution"] == "space_listed_without_a_name"
    assert row["space_name"] is None
    assert UNNAMED_SPACE_ID not in row["space_label"]
    assert "never given one" in row["space_label"]
    assert "deleted" not in row["space_label"]


async def test_join_buckets_are_mutually_exclusive_and_sum(space_join_fixture):
    """Four buckets, one session each. If a session could land in two of
    them the counts a reorganization report is built from would double-count
    it, and `space_attribution_by_session_id` must cover every session so a
    missing key never has to be interpreted."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
    membership = r.data["membership"]
    session_ids = [s["id"] for s in r.data["local_sessions"]]

    filed_by_space = [sid for ids in membership["by_space_id"].values() for sid in ids]
    filed_by_project = [
        sid for ids in membership["by_project_uuid"].values() for sid in ids
    ]
    unfiled = membership["unfiled_session_ids"]
    placed = filed_by_space + filed_by_project + unfiled

    assert len(placed) == len(session_ids) == 8
    assert sorted(placed) == sorted(session_ids)
    assert set(membership["space_attribution_by_session_id"]) == set(session_ids)


async def test_inventory_and_membership_agree_on_every_session(space_join_fixture):
    """`list_local_workspace` used to return `space_id: null` for every
    session while `get_project_membership` named a Space for the same one --
    two tools, one fact, two answers. They read the same attribution map now,
    so assert the equality directly rather than spot-checking a field."""
    sessions = await _inventory_sessions()
    rows = await _membership_rows()

    assert set(sessions) == set(rows)
    for session_id, session in sessions.items():
        attribution = {k: session[k] for k in ATTRIBUTION_KEYS}
        assert attribution == {k: rows[session_id][k] for k in ATTRIBUTION_KEYS}, (
            f"the two tools disagree about {session_id}"
        )
        # ...and neither one is answering with an empty attribution.
        assert attribution["space_source"] in local_data.SPACE_SOURCE_NOTES


async def test_inventory_carries_the_space_name_onto_every_session_row(
    space_join_fixture,
):
    """The narrower half of the same defect: the inventory's session rows
    came straight off disk, so `space_name` was null for all 97 sessions --
    including the 59 the app had recorded a Space for."""
    sessions = await _inventory_sessions()

    assert sessions["local_recorded"]["space_name"] == "Vacation - July 2026"
    assert sessions["local_inferred"]["space_name"] == "Vacation - July 2026"
    assert sessions["local_bare"]["space_name"] is None
    assert [s["space_name"] for s in sessions.values()].count(None) == 5


async def test_join_keeps_the_apps_own_statement_of_provenance(space_join_fixture):
    """`spaceIdSetBy` is the app saying who filed the chat. It costs nothing
    to carry and is the difference between "the user filed this" and "some
    process did"."""
    sessions = await _inventory_sessions()
    assert sessions["local_recorded"]["space_id_set_by"] == "user"
    assert sessions["local_bare"]["space_id_set_by"] is None



async def test_list_local_workspace_model_signals(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        by_id = {s["id"]: s for s in r.data["local_sessions"]}

        # No nested transcript for local_sess1 -- default_model/effort from
        # its local_<uuid>.json metadata is the only signal available.
        assert by_id["local_sess1"]["default_model"] == "claude-sonnet-5"
        assert by_id["local_sess1"]["effort"] == "high"
        assert "models_used" not in by_id["local_sess1"]

        # local_sess2 has a nested transcript -- per-message models_used
        # should be joined in, showing the model switched mid-session.
        assert by_id["local_sess2"]["models_used"] == {
            "claude-sonnet-5": 1, "claude-haiku-4-5-20251001": 1,
        }
        assert by_id["local_sess2"]["transcript_event_count"] == 2


def test_read_cowork_session_transcripts(tmp_path):
    desktop_root = tmp_path / "Claude"
    transcript = (
        desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
        / "local_abc" / ".claude" / "projects" / "proj" / "sess.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    with transcript.open("w", encoding="utf-8") as f:
        f.write(json.dumps(
            {"type": "assistant", "message": {"model": "claude-opus-5", "role": "assistant"}}
        ) + "\n")
        f.write(json.dumps(
            {"type": "assistant", "message": {"model": "claude-opus-5", "role": "assistant"}}
        ) + "\n")

    result = local_data.read_cowork_session_transcripts(desktop_root)
    assert result == {
        "local_abc": {
            "models_used": {"claude-opus-5": 2},
            "transcript_event_count": 2,
            "message_count": 2,
            "tools_invoked": {},
            "mcp_servers_invoked": [],
            "touched_dirs": [],
            "url_hosts": [],
            "transcript_sampled": False,
        }
    }


async def test_list_local_workspace_joins_every_transcript_signal(workspace_fixture):
    """The join must carry *all* transcript signals onto the session, not a
    hand-listed subset -- enumerating them individually is how the tool-use
    and reach fields silently went missing once already.
    """
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        joined = {s["id"]: s for s in r.data["local_sessions"]}["local_sess2"]

    # source_file is <base>/local-agent-mode-sessions/<account>/<org>/local_sess2.json;
    # the transcript glob is rooted at <base>.
    produced = set(local_data.read_cowork_session_transcripts(
        Path(joined["source_file"]).parents[3]
    )["local_sess2"])
    assert produced <= set(joined), f"transcript signals dropped in join: {produced - set(joined)}"


def test_read_cowork_session_transcripts_extracts_tool_and_reach_signals(tmp_path):
    """What a session actually *did*: which tools it invoked, which MCP
    servers those came from, and which directories/hosts it reached."""
    desktop_root = tmp_path / "Claude"
    transcript = (
        desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
        / "local_abc" / ".claude" / "projects" / "proj" / "sess.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    with transcript.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "type": "assistant",
            "message": {"model": "claude-opus-5", "role": "assistant", "content": [
                {"type": "tool_use", "name": "Read",
                 "input": {"file_path": "/Users/x/work/notes.md"}},
                {"type": "tool_use", "name": "mcp__claude-in-chrome__navigate",
                 "input": {"url": "https://example.com/a/b?secret=1"}},
            ]},
        }) + "\n")
        f.write(json.dumps({"type": "user", "message": {"role": "user", "content": []}}) + "\n")

    result = local_data.read_cowork_session_transcripts(desktop_root)["local_abc"]
    assert result["tools_invoked"] == {"Read": 1, "mcp__claude-in-chrome__navigate": 1}
    assert result["mcp_servers_invoked"] == ["claude-in-chrome"]
    # Parent directory only, never the filename; host only, never the path
    # or query string.
    assert result["touched_dirs"] == ["/Users/x/work"]
    assert result["url_hosts"] == ["example.com"]
    assert result["message_count"] == 2


class TestStartHereSection:
    """The ranked shortlist that opens the dashboard. Analysis stays
    complete; this is the one section that prioritizes."""

    def test_absent_when_not_supplied(self):
        html = dashboard.render_dashboard_html({"title": "T"}, [])
        assert '<section class="start-here">' not in html

    def test_renders_above_the_detailed_sections(self):
        html = dashboard.render_dashboard_html({
            "title": "T",
            "start_here": [{"title": "Fix me", "action": "Do this", "where": "Global"}],
            "findings": [{"title": "f", "recommendation": "r"}],
        }, [])
        assert "Start here" in html
        assert html.index("Start here") < html.index("best-practices findings")

    def test_escapes_user_supplied_text(self):
        html = dashboard.render_dashboard_html({
            "title": "T",
            "start_here": [{"title": "<script>x</script>", "action": "a"}],
        }, [])
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;" in html

    def test_overflow_note_only_past_the_soft_limit(self):
        def build(n):
            return dashboard.render_dashboard_html({
                "title": "T",
                "start_here": [{"title": str(i), "action": "x"} for i in range(n)],
            }, [])

        assert "fixes listed" not in build(dashboard._START_HERE_SOFT_LIMIT)
        assert "fixes listed" in build(dashboard._START_HERE_SOFT_LIMIT + 2)


class TestMemoryParsing:
    """The real memories.json is a single-element **list**, not an object.
    Assuming a dict made every real export fall through to the raw-preview
    branch, so these tests pin the confirmed shape.
    """

    REAL_SHAPE = [{
        "conversations_memory": "**Work context**\n\nRuns a consultancy.\n\n**Top of mind**\n\nQ3.",
        "project_memories": {
            "uuid-a": "**Purpose & context**\n\nClient work.",
            "uuid-b": "  ",
        },
        "account_uuid": "acct-1",
    }]

    def _write(self, tmp_path, payload):
        (tmp_path / "memories.json").write_text(json.dumps(payload), encoding="utf-8")
        return tmp_path

    def test_list_wrapper_is_unwrapped(self, tmp_path):
        summary, _ = export_data.summarize_memory(self._write(tmp_path, self.REAL_SHAPE))
        assert summary["available"] is True
        assert summary["account"]["char_count"] > 0

    def test_categories_are_parsed_from_bold_headers(self, tmp_path):
        summary, _ = export_data.summarize_memory(self._write(tmp_path, self.REAL_SHAPE))
        assert summary["account"]["categories"] == ["Work context", "Top of mind"]

    def test_project_memory_is_keyed_by_uuid_and_skips_blanks(self, tmp_path):
        summary, _ = export_data.summarize_memory(self._write(tmp_path, self.REAL_SHAPE))
        assert [p["project_uuid"] for p in summary["projects"]] == ["uuid-a"]

    def test_bare_dict_still_accepted(self, tmp_path):
        summary, _ = export_data.summarize_memory(self._write(tmp_path, self.REAL_SHAPE[0]))
        assert summary["available"] is True

    def test_render_does_not_fall_back_to_raw_dump(self, tmp_path):
        md, notes = export_data.render_memory_context(self._write(tmp_path, self.REAL_SHAPE))
        assert "Raw preview" not in md
        assert "Runs a consultancy." in md
        assert "### uuid-a" in md
        assert notes == []

    def test_missing_file_is_reported_not_raised(self, tmp_path):
        summary, _ = export_data.summarize_memory(tmp_path)
        assert summary == {"available": False, "account": None, "projects": []}

    def test_empty_memory_is_flagged_as_possibly_off(self, tmp_path):
        payload = [{"conversations_memory": "", "project_memories": {}}]
        _, notes = export_data.summarize_memory(self._write(tmp_path, payload))
        assert any("turned off" in n for n in notes)


class TestMcpServerAttribution:
    """`mcp__<server>__<tool>` is what makes the invoked-tool tally double as
    a record of which connectors a session actually used."""

    def test_extracts_server(self):
        assert local_data._mcp_server_of("mcp__claude-in-chrome__navigate") == "claude-in-chrome"

    def test_server_name_may_contain_single_underscores(self):
        assert local_data._mcp_server_of("mcp__session_info__list") == "session_info"

    def test_builtin_tool_is_not_a_server(self):
        assert local_data._mcp_server_of("Read") is None

    def test_malformed_prefix_returns_none(self):
        assert local_data._mcp_server_of("mcp__nodelimiter") is None


class TestCapabilityFieldShapes:
    """Each of these fields has a different real on-disk shape; all four were
    confirmed by inspecting a live session file."""

    def test_enabled_tools_is_a_mapping_and_false_means_disabled(self):
        raw = {"local:Control Chrome:close_tab": True, "local:Control Chrome:off": False}
        assert local_data._names_of(raw) == ["local:Control Chrome:close_tab"]

    def test_enabled_tool_keys_yield_server_display_labels(self):
        raw = {
            "local:Control Chrome:close_tab": True,
            "local:Read and Send iMessages:send": True,
            "local:Control Chrome:disabled": False,
        }
        assert local_data._enabled_mcp_server_labels(raw) == [
            "Control Chrome", "Read and Send iMessages",
        ]

    def test_remote_servers_are_dicts_with_a_name(self):
        raw = [{"uuid": "u", "name": "Gmail", "url": "https://x"}, {"uuid": "v"}]
        assert local_data._names_of(raw) == ["Gmail"]

    def test_plugin_names_come_from_slash_commands_not_install_paths(self):
        # pluginInstallPaths basenames are opaque hashes, so slashCommands'
        # `<plugin>:<command>` prefix is the only readable source.
        raw = ["my-plugin:do-thing", "my-plugin:other", "second:cmd", "bare"]
        assert local_data._plugin_names_of(raw) == ["my-plugin", "second"]

    def test_length_of_distinguishes_absent_from_empty(self):
        assert local_data._length_of(None) is None
        assert local_data._length_of([]) == 0

    def test_hosts_only_never_full_urls(self):
        raw = ["https://a.example.com/path?token=secret", "not a url"]
        assert local_data._hosts_of(raw) == ["a.example.com"]


def test_effort_reads_effort_override_first(tmp_path):
    """The on-disk key is `effortOverride`; `effort` has never been observed
    and is a defensive fallback only."""
    org_dir = tmp_path / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(org_dir / "local_a.json", {"title": "a", "effortOverride": "max"})
    _write_json(org_dir / "local_b.json", {"title": "b", "effort": "low"})
    _write_json(org_dir / "local_c.json", {"title": "c"})

    by_id = {s["id"]: s for s in local_data.read_local_sessions(tmp_path)}
    assert by_id["local_a"]["effort"] == "max"
    assert by_id["local_b"]["effort"] == "low"
    assert by_id["local_c"]["effort"] is None


def test_large_always_loaded_blobs_are_measured_never_returned(tmp_path):
    """`systemPrompt` and `memoryGuidelinesTemplate` are ~49k and ~13.5k
    characters of Anthropic scaffolding the user cannot edit. Their size is
    the finding; their text would be unactionable and sensitive."""
    org_dir = tmp_path / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(org_dir / "local_a.json", {
        "title": "a",
        "systemPrompt": "x" * 49_488,
        "memoryGuidelinesTemplate": "y" * 13_526,
    })

    session = local_data.read_local_sessions(tmp_path)[0]
    assert session["system_prompt_char_count"] == 49_488
    assert session["memory_guidelines_char_count"] == 13_526
    assert "x" * 100 not in json.dumps(session)
    assert "y" * 100 not in json.dumps(session)


class TestMessageModel:
    def test_finds_model_key(self):
        assert export_data._message_model({"model": "claude-x"}) == "claude-x"

    def test_finds_model_slug_key(self):
        assert export_data._message_model({"model_slug": "claude-y"}) == "claude-y"

    def test_missing_returns_none(self):
        assert export_data._message_model({"text": "hi"}) is None


# --------------------------------------------------------------------------------
# parse_export
# --------------------------------------------------------------------------------


@pytest.fixture
def export_fixture(tmp_path):
    export_dir = tmp_path / "export"
    _write_json(export_dir / "conversations.json", [
        {
            "uuid": "c1", "name": "Trip planning", "project_uuid": "p1",
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
            "chat_messages": [
                {"sender": "human", "text": "Help me plan a trip to Japan"},
                {"sender": "assistant", "content": [{"type": "text", "text": "Sure!"}]},
            ],
        },
        {
            "uuid": "c2", "name": "Random one-off",
            "created_at": "2026-02-01T00:00:00Z",
            "chat_messages": [{"sender": "human", "text": "What's the capital of France?"}],
        },
    ])
    _write_json(export_dir / "projects.json", [
        {
            "uuid": "p1", "name": "Japan Trip", "description": "Planning",
            "prompt_template": "Be a travel agent",
        }
    ])
    # Real shape, confirmed against two live exports: a single-element list
    # wrapping `conversations_memory` plus a `project_memories` dict keyed by
    # project uuid. This fixture previously used an invented list-of-dicts
    # shape carrying `project_name`, which is why the parser was written to
    # match something no export actually produces.
    _write_json(export_dir / "memories.json", [{
        "conversations_memory": "Global narrative about the user.",
        "project_memories": {"p1": "User is planning a Japan trip."},
        "account_uuid": "acct-1",
    }])
    return export_dir


async def test_parse_export_writes_expected_files(tmp_path, export_fixture):
    out_dir = tmp_path / "out"
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "parse_export", {"export_dir": str(export_fixture), "out_dir": str(out_dir)}
        )
        stats = r.data["stats"]
        assert stats["conversation_count"] == 2
        assert stats["project_count"] == 1
        assert stats["conversations_with_project"] == 1
        assert stats["date_range"]["earliest"] == "2026-01-01T00:00:00Z"
        assert any("fewer than 10 conversations" in n.lower() for n in stats["notes"])

        conversations = (out_dir / "conversations.jsonl").read_text().splitlines()
        assert len(conversations) == 2
        first = json.loads(conversations[0])
        assert first["uuid"] == "c1"
        assert first["first_human_message"] == "Help me plan a trip to Japan"

        projects_index = json.loads((out_dir / "projects_index.json").read_text())
        assert projects_index["p1"]["conversation_count"] == 1
        assert projects_index["p1"]["name"] == "Japan Trip"

        memory_md = (out_dir / "memory_context.md").read_text()
        assert "Global narrative about the user." in memory_md
        # memories.json carries no project *name* -- only the uuid, which is
        # the join key back into projects_index.json above.
        assert "### p1" in memory_md
        assert "User is planning a Japan trip." in memory_md

        assert stats["memory"]["available"] is True
        assert [p["project_uuid"] for p in stats["memory"]["projects"]] == ["p1"]


async def test_parse_export_notes_missing_model_field(tmp_path, export_fixture):
    out_dir = tmp_path / "out_no_model"
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "parse_export", {"export_dir": str(export_fixture), "out_dir": str(out_dir)}
        )
        notes = r.data["stats"]["notes"]
        assert any("doesn't record which model generated a response" in n for n in notes)
        conversations = [
            json.loads(line)
            for line in (out_dir / "conversations.jsonl").read_text().splitlines()
        ]
        assert all(c["models_used"] == {} for c in conversations)


async def test_parse_export_captures_model_usage(tmp_path):
    export_dir = tmp_path / "export_with_model"
    _write_json(export_dir / "conversations.json", [
        {
            "uuid": "c1", "name": "With model", "created_at": "2026-01-01T00:00:00Z",
            "chat_messages": [
                {"sender": "human", "text": "Summarize this."},
                {"sender": "assistant", "text": "Sure.", "model": "claude-sonnet-5"},
                {"sender": "assistant", "text": "Anything else?", "model": "claude-sonnet-5"},
            ],
        },
    ])
    out_dir = tmp_path / "out_with_model"
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "parse_export", {"export_dir": str(export_dir), "out_dir": str(out_dir)}
        )
        notes = r.data["stats"]["notes"]
        assert not any("doesn't record which model" in n for n in notes)
        conversations = [
            json.loads(line)
            for line in (out_dir / "conversations.jsonl").read_text().splitlines()
        ]
        assert conversations[0]["models_used"] == {"claude-sonnet-5": 2}


async def test_parse_export_missing_conversations_json_notes_zero(tmp_path):
    empty_dir = tmp_path / "empty_export"
    empty_dir.mkdir()
    out_dir = tmp_path / "out2"
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "parse_export", {"export_dir": str(empty_dir), "out_dir": str(out_dir)}
        )
        assert r.data["stats"]["conversation_count"] == 0
        assert any("missing or not a json array" in n.lower() for n in r.data["stats"]["notes"])


# --------------------------------------------------------------------------------
# locate_export_download
# --------------------------------------------------------------------------------


def _write_zip(path: Path, members: dict[str, str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return path


class TestFindExportZipCandidates:
    def test_finds_zip_with_top_level_conversations_json(self, tmp_path):
        _write_zip(tmp_path / "export1.zip", {"conversations.json": "[]", "users.json": "{}"})
        candidates = export_data.find_export_zip_candidates([tmp_path])
        assert len(candidates) == 1
        assert candidates[0]["zip_path"] == str(tmp_path / "export1.zip")

    def test_finds_zip_with_nested_conversations_json(self, tmp_path):
        _write_zip(tmp_path / "export2.zip", {"export-data/conversations.json": "[]"})
        candidates = export_data.find_export_zip_candidates([tmp_path])
        assert len(candidates) == 1

    def test_ignores_zip_without_conversations_json(self, tmp_path):
        _write_zip(tmp_path / "not-an-export.zip", {"readme.txt": "hello"})
        candidates = export_data.find_export_zip_candidates([tmp_path])
        assert candidates == []

    def test_ignores_corrupt_zip(self, tmp_path):
        (tmp_path / "corrupt.zip").write_bytes(b"not actually a zip")
        candidates = export_data.find_export_zip_candidates([tmp_path])
        assert candidates == []

    def test_missing_directory_returns_empty(self, tmp_path):
        assert export_data.find_export_zip_candidates([tmp_path / "nope"]) == []

    def test_newest_first(self, tmp_path):
        import os

        older = _write_zip(tmp_path / "older.zip", {"conversations.json": "[]"})
        newer = _write_zip(tmp_path / "newer.zip", {"conversations.json": "[]"})
        os.utime(older, (1_000_000_000, 1_000_000_000))
        os.utime(newer, (2_000_000_000, 2_000_000_000))
        candidates = export_data.find_export_zip_candidates([tmp_path])
        assert len(candidates) == 2
        assert candidates[0]["zip_path"] == str(newer)


class TestUnpackExportZip:
    def test_top_level_conversations_json(self, tmp_path):
        zip_path = _write_zip(tmp_path / "export.zip", {"conversations.json": "[]"})
        export_dir = export_data.unpack_export_zip(str(zip_path), str(tmp_path / "out"))
        assert (Path(export_dir) / "conversations.json").is_file()

    def test_nested_conversations_json_returns_inner_dir(self, tmp_path):
        zip_path = _write_zip(
            tmp_path / "export.zip", {"wrapper-folder/conversations.json": "[]"}
        )
        export_dir = export_data.unpack_export_zip(str(zip_path), str(tmp_path / "out"))
        assert Path(export_dir).name == "wrapper-folder"
        assert (Path(export_dir) / "conversations.json").is_file()


class TestLocateExportDownload:
    def test_not_found_yet(self, tmp_path):
        result = export_data.locate_export_download([str(tmp_path)], str(tmp_path / "out"))
        assert result == {"found": False, "candidates": []}

    def test_single_candidate_unpacks(self, tmp_path):
        _write_zip(tmp_path / "my-export.zip", {"conversations.json": "[]"})
        result = export_data.locate_export_download([str(tmp_path)], str(tmp_path / "out"))
        assert result["found"] is True
        assert result["ambiguous"] is False
        assert (Path(result["export_dir"]) / "conversations.json").is_file()

    def test_multiple_candidates_are_ambiguous(self, tmp_path):
        _write_zip(tmp_path / "export-a.zip", {"conversations.json": "[]"})
        _write_zip(tmp_path / "export-b.zip", {"conversations.json": "[]"})
        result = export_data.locate_export_download([str(tmp_path)], str(tmp_path / "out"))
        assert result["found"] is True
        assert result["ambiguous"] is True
        assert result["export_dir"] is None
        assert len(result["candidates"]) == 2

    def test_repeat_calls_against_same_zip_are_idempotent(self, tmp_path):
        _write_zip(tmp_path / "my-export.zip", {"conversations.json": "[]"})
        first = export_data.locate_export_download([str(tmp_path)], str(tmp_path / "out"))
        second = export_data.locate_export_download([str(tmp_path)], str(tmp_path / "out"))
        assert first["export_dir"] == second["export_dir"]


async def test_locate_export_download_tool_defaults_and_explicit_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "SHADETREE_AI_PLUGINS_CLAUDE_USAGE_ANALYZER_STATE_DIR", str(tmp_path / "state")
    )
    downloads_dir = tmp_path / "Downloads"
    _write_zip(downloads_dir / "my-export.zip", {"conversations.json": "[]"})

    async with Client(srv.mcp) as client:
        r = await client.call_tool("locate_export_download", {"search_dirs": [str(downloads_dir)]})
        assert r.data["found"] is True
        assert r.data["ambiguous"] is False
        export_dir = Path(r.data["export_dir"])
        assert (export_dir / "conversations.json").is_file()
        # defaulted out_dir_base should land under the overridden state dir, not real $HOME
        assert str(tmp_path / "state") in str(export_dir)


# --------------------------------------------------------------------------------
# render_dashboard
# --------------------------------------------------------------------------------


def _sample_plan(unfiled_count: str) -> dict:
    return {
        "title": "Claude Usage Analysis",
        "subtitle": "example.account",
        "data_sources": ["Local Desktop/Cowork"],
        "stat_tiles": [
            {"label": "Chats analyzed", "value": "42"},
            {"label": "Unfiled chats", "value": unfiled_count, "status": "warning"},
        ],
        "projects": [
            {
                "name": "Japan Trip",
                "description": "Planning an upcoming Japan trip.",
                "instructions": "Act as a meticulous travel planner.",
                "chats": ["Trip planning"],
                "files": ["itinerary.md"],
                "status": "new",
            }
        ],
        "leftovers": [{"name": "Random one-off", "note": "Leave standalone."}],
        "notes": ["Thin export -- low confidence."],
    }


async def test_render_dashboard_writes_html_and_history(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "SHADETREE_AI_PLUGINS_CLAUDE_USAGE_ANALYZER_STATE_DIR", str(tmp_path / "state")
    )
    out_path = tmp_path / "dashboard.html"

    async with Client(srv.mcp) as client:
        r1 = await client.call_tool(
            "render_dashboard", {"plan": _sample_plan("5"), "out_path": str(out_path)}
        )
        assert r1.data["history_runs"] == 1
        assert out_path.exists()
        html = out_path.read_text()
        assert "Japan Trip" in html
        assert "Act as a meticulous travel planner." in html
        assert "Random one-off" in html

        r2 = await client.call_tool(
            "render_dashboard", {"plan": _sample_plan("2"), "out_path": str(out_path)}
        )
        assert r2.data["history_runs"] == 2
        html2 = out_path.read_text()
        assert "Progress over time" in html2

        history_path = tmp_path / "state" / "history.jsonl"
        lines = history_path.read_text().splitlines()
        assert len(lines) == 2


async def test_render_dashboard_model_usage_and_findings(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "SHADETREE_AI_PLUGINS_CLAUDE_USAGE_ANALYZER_STATE_DIR", str(tmp_path / "state2")
    )
    out_path = tmp_path / "dashboard2.html"
    plan = {
        "title": "Claude Usage Analysis",
        "stat_tiles": [{"label": "Chats analyzed", "value": "10"}],
        "model_usage": [
            {"model": "claude-opus-5", "count": 3},
            {"model": "claude-haiku-4-5", "count": 27},
        ],
        "findings": [
            {
                "title": "Frontier model used for simple lookups",
                "severity": "warning",
                "evidence": ["Capital of France?", "Convert 5 miles to km"],
                "recommendation": "Route short factual asks to a lighter model.",
            }
        ],
    }

    async with Client(srv.mcp) as client:
        await client.call_tool("render_dashboard", {"plan": plan, "out_path": str(out_path)})
        html = out_path.read_text()
        assert "Model usage" in html
        assert "claude-opus-5" in html
        assert "claude-haiku-4-5" in html
        assert "Usage &amp; best-practices findings" in html
        assert "Frontier model used for simple lookups" in html
        assert "Route short factual asks to a lighter model." in html
        assert "Convert 5 miles to km" in html


async def test_render_dashboard_recommendations(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "SHADETREE_AI_PLUGINS_CLAUDE_USAGE_ANALYZER_STATE_DIR", str(tmp_path / "state3")
    )
    out_path = tmp_path / "dashboard3.html"
    plan = {
        "title": "Claude Usage Analysis",
        "recommendations": [
            {
                "title": "Connect the Gmail connector",
                "kind": "existing",
                "item_type": "connector",
                "source": "claude.ai connectors",
                "evidence": ["Weekly status email", "Client follow-up"],
                "rationale": "Both chats had the user paste email text by hand.",
            },
            {
                "title": "Build a custom invoice-reconciliation skill",
                "kind": "custom",
                "item_type": "skill",
                "evidence": ["Invoice check Jan", "Invoice check Feb", "Invoice check Mar"],
                "rationale": "No existing skill/plugin covers this internal workflow.",
            },
        ],
    }

    async with Client(srv.mcp) as client:
        await client.call_tool("render_dashboard", {"plan": plan, "out_path": str(out_path)})
        html = out_path.read_text()
        assert "Recommended skills, plugins &amp; connectors" in html
        assert "Already available" in html
        assert "Worth building custom" in html
        assert "Connect the Gmail connector" in html
        assert "Build a custom invoice-reconciliation skill" in html
        assert "claude.ai connectors" in html
        assert "Weekly status email" in html


# --------------------------------------------------------------------------------
# No-network guard
# --------------------------------------------------------------------------------


# --------------------------------------------------------------------------------
# time_window -- shared scoping for every data-pulling tool
# --------------------------------------------------------------------------------

NOW = _ms(2026, 7, 29)


class TestToEpochMs:
    def test_epoch_millis_passes_through(self):
        assert tw.to_epoch_ms(1783349183415) == 1783349183415.0

    def test_epoch_seconds_are_promoted_to_millis(self):
        """Desktop writes millis, but a seconds-shaped value must not be
        read as 1970 -- that would look like real, very stale data."""
        assert tw.to_epoch_ms(1783349183) == 1783349183000.0

    def test_iso_string_from_the_export_format(self):
        assert tw.to_epoch_ms("2026-01-01T00:00:00Z") == _ms(2026, 1, 1)

    def test_naive_iso_is_treated_as_utc(self):
        assert tw.to_epoch_ms("2026-01-01T00:00:00") == _ms(2026, 1, 1)

    def test_unrecognized_returns_none_rather_than_raising(self):
        assert tw.to_epoch_ms("not a date") is None
        assert tw.to_epoch_ms("") is None
        assert tw.to_epoch_ms(None) is None
        assert tw.to_epoch_ms(True) is None


class TestParseBound:
    def test_relative_ages(self):
        assert tw.parse_bound("90d", now_ms=NOW)[0] == _ms(2026, 4, 30)
        assert tw.parse_bound("2w", now_ms=NOW)[0] == _ms(2026, 7, 15)

    def test_relative_is_case_and_space_insensitive(self):
        assert tw.parse_bound(" 90D ", now_ms=NOW)[0] == tw.parse_bound("90d", now_ms=NOW)[0]

    def test_iso_date(self):
        assert tw.parse_bound("2026-01-01")[0] == _ms(2026, 1, 1)

    def test_none_is_unbounded_not_an_error(self):
        assert tw.parse_bound(None) == (None, None)

    def test_garbage_reports_an_error_instead_of_raising(self):
        epoch_ms, error = tw.parse_bound("last tuesday")
        assert epoch_ms is None
        assert "Could not read" in error


class TestResolve:
    def test_unbounded_by_default(self):
        window = tw.resolve()
        assert window["bounded"] is False
        assert window["errors"] == []

    def test_backwards_window_degrades_to_unbounded_with_an_error(self):
        """A window that excludes everything must not silently return zero
        results -- that reads as "you have no data" rather than "your
        bounds are backwards."""
        window = tw.resolve("2026-07-01", "2026-01-01")
        assert window["bounded"] is False
        assert any("is after" in e for e in window["errors"])

    def test_bad_bound_widens_rather_than_empties(self):
        window = tw.resolve("nonsense")
        assert window["bounded"] is False
        assert len(window["errors"]) == 1


class TestOverlaps:
    def test_unbounded_window_keeps_everything(self):
        assert tw.overlaps(tw.resolve(), None, None) is True

    def test_item_spanning_into_the_window_is_kept(self):
        """Interval overlap, not creation date: a chat started in January
        and still worked in March belongs in a March window."""
        window = tw.resolve("2026-02-20", "2026-02-25")
        assert tw.overlaps(window, _ms(2026, 1, 1), _ms(2026, 3, 1)) is True

    def test_item_entirely_before_the_window_is_dropped(self):
        window = tw.resolve("2026-02-20")
        assert tw.overlaps(window, _ms(2026, 1, 1), _ms(2026, 2, 1)) is False

    def test_item_entirely_after_the_window_is_dropped(self):
        window = tw.resolve(None, "2026-02-20")
        assert tw.overlaps(window, _ms(2026, 3, 1), _ms(2026, 3, 2)) is False

    def test_one_missing_endpoint_stands_in_for_the_other(self):
        window = tw.resolve("2026-02-20", "2026-02-25")
        assert tw.overlaps(window, None, _ms(2026, 2, 22)) is True
        assert tw.overlaps(window, _ms(2026, 2, 22), None) is True

    def test_undated_items_are_kept_not_dropped(self):
        window = tw.resolve("2026-02-20")
        assert tw.overlaps(window, None, None) is True


class TestApply:
    def _items(self):
        return [
            {"id": "old", "created_at": _ms(2025, 1, 1), "updated_at": _ms(2025, 1, 2)},
            {"id": "new", "created_at": _ms(2026, 6, 1), "updated_at": _ms(2026, 6, 2)},
            {"id": "undated"},
        ]

    def test_summary_counts_are_reportable(self):
        kept, summary = tw.apply(
            tw.resolve("2026-01-01"), self._items(), "created_at", "updated_at"
        )
        assert [i["id"] for i in kept] == ["new", "undated"]
        assert summary["total_before"] == 3
        assert summary["kept"] == 2
        assert summary["excluded"] == 1
        assert summary["undated_kept"] == 1

    def test_unbounded_reports_no_exclusions(self):
        kept, summary = tw.apply(tw.resolve(), self._items(), "created_at", "updated_at")
        assert len(kept) == 3
        assert summary["bounded"] is False
        assert summary["excluded"] == 0


class TestSummaryNotes:
    def test_clean_unbounded_read_has_nothing_to_say(self):
        _, summary = tw.apply(tw.resolve(), [], "created_at", "updated_at")
        assert tw.summary_notes(summary, "chats") == []

    def test_windowed_read_warns_against_reporting_a_total(self):
        _, summary = tw.apply(
            tw.resolve("2026-01-01"),
            [{"created_at": _ms(2025, 1, 1)}, {"created_at": _ms(2026, 6, 1)}],
            "created_at",
            "updated_at",
        )
        notes = tw.summary_notes(summary, "chats")
        assert any("1 of 2 chats are in scope" in n for n in notes)
        assert any("account total" in n for n in notes)

    def test_undated_items_are_disclosed(self):
        _, summary = tw.apply(tw.resolve("2026-01-01"), [{}], "created_at", "updated_at")
        assert any("no usable timestamp" in n for n in tw.summary_notes(summary, "chats"))

    def test_the_window_span_is_marked_utc(self):
        """The note describing the window used to quote a bare
        UTC-derived date with no zone marker at all, sitting in a response
        whose every other date is local."""
        _, summary = tw.apply(
            tw.resolve("2026-01-01", "2026-02-01"),
            [{"created_at": _ms(2020, 1, 1)}],
            "created_at",
            "updated_at",
        )
        note = next(
            n for n in tw.summary_notes(summary, "chats")
            if n.startswith("Time-scoped to")
        )
        assert "2026-01-01 to 2026-02-01 UTC" in note
        assert "every date and weekday elsewhere in this response is a local one" in note


# --------------------------------------------------------------------------------
# A bare-date bound is anchored at midnight UTC, and says so
# --------------------------------------------------------------------------------
#
# The bound is deliberately left where it is: midnight UTC is the
# documented, tested contract, and moving it would silently re-scope every
# existing caller and every saved analysis. What was wrong is that the
# mismatch was INVISIBLE -- the window edge fell part-way through a local
# day while every day bucket and weekday the reader sees is local.


@pytest.fixture
def local_zone_is_not_utc():
    """Skip on a machine running in UTC, where a zone-less bound and a local
    one land on the same instant and there is nothing to disclose."""
    offset = datetime.now().astimezone().utcoffset()
    if offset is None or offset.total_seconds() == 0:
        pytest.skip(
            "this machine runs in UTC, so a zone-less bound and a local one "
            "coincide and the mismatch is untestable here"
        )
    return offset


class TestZonelessBounds:
    def test_a_bare_date_discloses_where_it_actually_landed(self, local_zone_is_not_utc):
        window = tw.resolve("2026-08-01")

        # The contract is unchanged -- this is a disclosure, not a move.
        assert window["since"] == "2026-08-01T00:00:00+00:00"
        assert window["since_local"] is not None
        assert window["since_local"] != window["since"]

        note = window["boundary_notes"][0]
        assert "`since`" in note
        assert "names no timezone" in note
        assert "midnight UTC" in note
        # The local instant it actually landed on, and the zone label.
        assert window["since_local"] in note
        assert window["timezone"]["utc_offset"] in note

    def test_it_recommends_a_bound_that_actually_lines_up(self, local_zone_is_not_utc):
        """A caveat the reader can't act on is only half a disclosure, so
        the note carries the exact bound to pass instead -- and that bound
        has to resolve to the reader's own midnight and raise no note of
        its own."""
        note = tw.resolve("2026-08-01")["boundary_notes"][0]
        suggested = re.search(r'Pass "([^"]+)" instead', note).group(1)

        replacement = tw.resolve(suggested)
        assert replacement["boundary_notes"] == []
        assert replacement["since_local"].startswith("2026-08-01T00:00:00")

    def test_a_bound_that_names_a_zone_has_nothing_to_disclose(
        self, local_zone_is_not_utc
    ):
        for bound in ("2026-08-01T00:00:00Z", "2026-08-01T00:00:00-05:00"):
            assert tw.resolve(bound)["boundary_notes"] == []

    def test_a_relative_age_or_an_epoch_is_not_a_zone_less_bound(
        self, local_zone_is_not_utc
    ):
        """Neither one names a calendar day, so neither can be off by one."""
        assert tw.resolve("90d")["boundary_notes"] == []
        assert tw.resolve(_ms(2026, 8, 1))["boundary_notes"] == []
        assert tw.resolve(str(_ms(2026, 8, 1)))["boundary_notes"] == []

    def test_both_ends_are_checked(self, local_zone_is_not_utc):
        window = tw.resolve("2026-08-01", "2026-08-31")
        assert len(window["boundary_notes"]) == 2
        assert "`since`" in window["boundary_notes"][0]
        assert "`until`" in window["boundary_notes"][1]

    def test_an_unbounded_window_says_nothing(self):
        assert tw.resolve()["boundary_notes"] == []
        assert tw.UNBOUNDED["boundary_notes"] == []

    def test_both_bounds_come_back_in_both_zones(self, local_zone_is_not_utc):
        window = tw.resolve("2026-08-01", "2026-08-31")
        assert window["since_local"] != window["since"]
        assert window["until_local"] != window["until"]
        assert window["timezone"]["utc_offset"].startswith("UTC")


async def test_get_instructions_inventory_discloses_a_zone_less_bound(
    workspace_fixture, local_zone_is_not_utc
):
    """Through a real tool: `summary_notes` repeats the caveat, so every
    tool that already reports notes gets it with no extra wiring."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_instructions_inventory", {"since": "2026-02-20"})

    assert any("names no timezone" in n for n in r.data["notes"])
    assert r.data["time_window"]["since_local"] is not None
    assert r.data["time_window"]["boundary_notes"]


async def test_list_local_workspace_carries_the_bound_caveat_in_its_window(
    workspace_fixture, local_zone_is_not_utc
):
    """`list_local_workspace` reports no `notes` of its own, so the caveat
    has to ride on the window summary a caller is already told to repeat."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {"since": "2026-02-20"})
    window = r.data["time_window"]

    assert window["boundary_notes"]
    assert "names no timezone" in window["boundary_notes"][0]
    assert window["since_local"] is not None
    assert window["timezone"]["utc_offset"].startswith("UTC")


async def test_get_plan_usage_history_discloses_a_zone_less_bound(
    plan_usage_fixture, local_zone_is_not_utc
):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {"since": "2026-08-08"})
    assert any("names no timezone" in n for n in r.data["notes"])


# --------------------------------------------------------------------------------
# since/until on the data-pulling tools
# --------------------------------------------------------------------------------


async def test_list_local_workspace_unbounded_by_default(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        assert len(r.data["local_sessions"]) == 4
        assert r.data["time_window"]["bounded"] is False
        assert r.data["time_window"]["excluded"] == 0


async def test_list_local_workspace_scopes_to_a_window(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {"since": "2026-03-15"})
        assert [s["id"] for s in r.data["local_sessions"]] == ["local_sess4"]
        window = r.data["time_window"]
        assert window["bounded"] is True
        assert window["kept"] == 1
        assert window["excluded"] == 3
        assert window["since"].startswith("2026-03-15")


async def test_list_local_workspace_window_matches_overlap_not_creation(workspace_fixture):
    """sess1 was created in January -- before sess2 -- but stayed active into
    March, so a late-February window must keep sess1 and drop sess2."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "list_local_workspace", {"since": "2026-02-20", "until": "2026-02-25"}
        )
        assert [s["id"] for s in r.data["local_sessions"]] == ["local_sess1"]


async def test_list_local_workspace_window_applies_before_membership_join(workspace_fixture):
    """Membership groupings have to describe the same window as the session
    list, or the dashboard mixes a windowed chat count with an all-time
    Project breakdown."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {"since": "2026-03-15"})
        membership = r.data["membership"]
        assert membership["by_project_uuid"] == {"UNCACHED-UUID": ["local_sess4"]}
        assert membership["by_space_id"] == {}
        assert membership["unfiled_session_ids"] == []


async def test_list_local_workspace_bad_bound_widens_and_complains(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {"since": "last tuesday"})
        assert len(r.data["local_sessions"]) == 4
        assert any("Could not read" in e for e in r.data["time_window"]["errors"])


async def test_list_local_workspace_exposes_readable_timestamps(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        sess1 = next(s for s in r.data["local_sessions"] if s["id"] == "local_sess1")
        assert sess1["created_at"] == SESS1_CREATED
        assert sess1["created_at_iso"].startswith("2026-01-01")
        assert sess1["last_activity_at_iso"].startswith("2026-03-01")


async def test_get_project_membership_scopes_to_a_window(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_project_membership", {"since": "2026-03-15"})
        assert [row["session_id"] for row in r.data["rows"]] == ["local_sess4"]
        assert r.data["time_window"]["excluded"] == 3


async def test_get_instructions_inventory_window_changes_which_text_was_in_force(
    workspace_fixture,
):
    """The global text is stored per-session, so a window doesn't just trim
    the result -- it answers what the instructions actually were during that
    period. Windowing past sess3 drops the older variant entirely."""
    async with Client(srv.mcp) as client:
        full = await client.call_tool("get_instructions_inventory", {})
        assert full.data["global_instructions"]["variant_count"] == 2

        scoped = await client.call_tool("get_instructions_inventory", {"since": "2026-01-01"})
        variants = scoped.data["global_instructions"]["variants"]
        assert [v["text"] for v in variants] == [GLOBAL_CURRENT]
        assert scoped.data["time_window"]["excluded"] == 1
        assert any("Time-scoped to" in n for n in scoped.data["notes"])


async def test_parse_export_scopes_conversations_to_a_window(tmp_path):
    export_dir = tmp_path / "export_window"
    _write_json(export_dir / "conversations.json", [
        {
            "uuid": "old", "name": "Last year", "created_at": "2025-01-01T00:00:00Z",
            "updated_at": "2025-01-02T00:00:00Z",
            "chat_messages": [{"sender": "human", "text": "old question"}],
        },
        {
            "uuid": "spanning", "name": "Long-running", "created_at": "2025-12-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z",
            "chat_messages": [{"sender": "human", "text": "still going"}],
        },
        {
            "uuid": "recent", "name": "This year", "created_at": "2026-05-01T00:00:00Z",
            "updated_at": "2026-05-02T00:00:00Z",
            "chat_messages": [{"sender": "human", "text": "new question"}],
        },
    ])
    out_dir = tmp_path / "out_window"

    async with Client(srv.mcp) as client:
        r = await client.call_tool(
            "parse_export",
            {"export_dir": str(export_dir), "out_dir": str(out_dir), "since": "2026-01-01"},
        )
        stats = r.data["stats"]
        # "spanning" started in 2025 but was still active in 2026 -- overlap keeps it.
        assert stats["conversation_count"] == 2
        assert stats["time_window"]["excluded"] == 1

        written = [
            json.loads(line)
            for line in (out_dir / "conversations.jsonl").read_text().splitlines()
        ]
        assert {c["uuid"] for c in written} == {"spanning", "recent"}
        assert any("Time-scoped to" in n for n in stats["notes"])


async def test_render_dashboard_shows_the_time_window(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "SHADETREE_AI_PLUGINS_CLAUDE_USAGE_ANALYZER_STATE_DIR", str(tmp_path / "state_tw")
    )
    out_path = tmp_path / "dashboard_tw.html"
    async with Client(srv.mcp) as client:
        await client.call_tool("render_dashboard", {
            "plan": {"title": "Scoped run", "time_window": "2026-05-01 to 2026-07-29 (90d)"},
            "out_path": str(out_path),
        })
        assert "2026-05-01 to 2026-07-29 (90d)" in out_path.read_text()


async def test_render_dashboard_says_so_when_unscoped(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "SHADETREE_AI_PLUGINS_CLAUDE_USAGE_ANALYZER_STATE_DIR", str(tmp_path / "state_tw2")
    )
    out_path = tmp_path / "dashboard_tw2.html"
    async with Client(srv.mcp) as client:
        await client.call_tool(
            "render_dashboard", {"plan": {"title": "Full run"}, "out_path": str(out_path)}
        )
        assert "Covering all available history." in out_path.read_text()


# --------------------------------------------------------------------------------
# get_plan_usage_history -- Claude Desktop's own record of plan consumption
# --------------------------------------------------------------------------------

# The org uuid is an account identifier: it is used for grouping inside the
# reader and must never reach a response.
USAGE_ORG_UUID = "11111111-2222-3333-4444-555555555555"

_ABSENT = object()


def _usage_ms(day: int, hour: int, minute: int = 0) -> int:
    """Epoch milliseconds in August 2026 -- `t` on a real install is epoch
    milliseconds, not seconds and not an ISO string."""
    return int(datetime(2026, 8, day, hour, minute, tzinfo=UTC).timestamp() * 1000)


def _local_date(epoch_ms: float) -> str:
    """The calendar day one instant fell on in **this machine's** timezone.

    Computed here from the stdlib rather than through `time_window`, so the
    day/weekday assertions check the answer instead of agreeing with the
    implementation that produced it."""
    return datetime.fromtimestamp(epoch_ms / 1000).astimezone().strftime("%Y-%m-%d")


def _local_weekday(epoch_ms: float) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000).astimezone().strftime("%A")


def _utc_offset_label(epoch_ms: float) -> str:
    """`UTC-05:00` for the offset in force **at that instant** -- which is
    not the offset in force today, across a daylight-saving boundary."""
    offset = datetime.fromtimestamp(epoch_ms / 1000).astimezone().utcoffset()
    seconds = int(offset.total_seconds())
    sign = "-" if seconds < 0 else "+"
    seconds = abs(seconds)
    return f"UTC{sign}{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}"


def _sample(at: int, fh, sd, xu=_ABSENT, org: str = USAGE_ORG_UUID) -> dict:
    """One `plan-usage-history.json` sample in its real on-disk shape.

    `xu` defaults to *absent* rather than zero because that is the common
    case on a real install (present on only 159 of 544 samples), and the
    two are different claims: "no extra usage" versus "this build didn't
    record the field."""
    usage: dict = {"fh": fh, "sd": sd}
    if xu is not _ABSENT:
        usage["xu"] = xu
    return {"t": at, "org": org, "u": usage}


# Twelve samples across four days. The 08-08 block is five *contiguous*
# at-or-above-ceiling samples, which must collapse into ONE limit event
# rather than five; the 08-09 pair is a near-limit run that never reaches
# 100, so it is an event but not a ceiling event. Nine of the twelve carry
# no `xu` key at all.
USAGE_SAMPLES = [
    _sample(_usage_ms(7, 10, 0), 10, 5),
    _sample(_usage_ms(7, 10, 15), 20, 6, xu=0),
    _sample(_usage_ms(8, 12, 0), 95, 40),
    _sample(_usage_ms(8, 12, 15), 100, 41),
    _sample(_usage_ms(8, 12, 30), 100, 42),
    _sample(_usage_ms(8, 12, 45), 100, 43),
    _sample(_usage_ms(8, 13, 0), 100, 44),
    _sample(_usage_ms(8, 13, 15), 30, 45),
    _sample(_usage_ms(9, 9, 0), 92, 50, xu=0),
    _sample(_usage_ms(9, 9, 15), 93, 51),
    _sample(_usage_ms(9, 9, 30), 40, 52),
    _sample(_usage_ms(20, 8, 0), 12, 19, xu=5),
]


@pytest.fixture
def usage_dir(tmp_path, monkeypatch):
    """An otherwise empty Desktop app-data directory. Tests write whatever
    `plan-usage-history.json` they need into it -- or none at all, which is
    itself a case the tool has to survive."""
    desktop_root = tmp_path / "desktop" / "Claude"
    desktop_root.mkdir(parents=True)
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop_root])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])
    return desktop_root


@pytest.fixture
def plan_usage_fixture(usage_dir):
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": USAGE_SAMPLES},
    )
    return usage_dir


async def test_get_plan_usage_history_parses_the_version_2_shape(plan_usage_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["available"] is True
        assert r.data["schema_version"] == 2
        assert r.data["sample_count"] == len(USAGE_SAMPLES)
        assert r.data["total_sample_count"] == len(USAGE_SAMPLES)
        # `coverage`, not `retention`: nothing in this file establishes that
        # the earliest sample is where a retention rule cut it, and the
        # field name was the most prominent place that claim was made.
        assert "retention" not in r.data
        assert r.data["coverage"]["first_sample_at_iso"].startswith("2026-08-07")
        assert r.data["coverage"]["last_sample_at_iso"].startswith("2026-08-20")
        assert r.data["peaks"]["five_hour"]["pct"] == 100
        assert r.data["peaks"]["five_hour"]["at_iso"].startswith("2026-08-08T12:15")
        assert r.data["peaks"]["seven_day"]["pct"] == 52
        # `current` is the newest sample, not the first one in the file.
        assert r.data["current"]["five_hour_pct"] == 12
        assert r.data["current"]["seven_day_pct"] == 19


async def test_get_plan_usage_history_never_surfaces_the_org_uuid(plan_usage_fixture):
    """The file identifies organizations only by account uuid. Grouping needs
    it; a caller does not, and an opaque label is all that comes back."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert USAGE_ORG_UUID not in json.dumps(r.data)
        assert [o["label"] for o in r.data["organizations"]] == ["organization 1"]


async def test_get_plan_usage_history_survives_samples_with_no_xu_key(plan_usage_fixture):
    """Most real samples have no `xu` key at all. Missing must stay None
    rather than collapsing to 0 -- "never above zero on the samples that
    recorded it" is the only claim this file supports."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        extra = r.data["extra_usage"]
        assert extra["samples_reporting_field"] == 3
        assert extra["samples_missing_field"] == 9
        assert extra["ever_used"] is True
        assert extra["peak_pct"] == 5
        assert extra["first_used_at_iso"].startswith("2026-08-20")


async def test_get_plan_usage_history_reports_never_used_extra_usage_as_such(usage_dir):
    """`xu: 0` on every sample that carries it is not "extra usage was
    purchased and left unused" -- and it must not produce a peak timestamp,
    which would read as an event that never happened."""
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": [
            _sample(_usage_ms(7, 10, 0), 10, 5),
            _sample(_usage_ms(7, 10, 15), 20, 6, xu=0),
        ]},
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        extra = r.data["extra_usage"]
        assert extra["ever_used"] is False
        assert extra["peak_at_iso"] is None
        assert extra["samples_missing_field"] == 1


async def test_get_plan_usage_history_collapses_a_run_of_ceiling_samples(plan_usage_fixture):
    """Five contiguous 100% samples are ONE afternoon of running out, not
    five separate limit hits. Sampling every ~15 minutes makes this the
    difference between "you hit your limit twice this month" and "twenty
    times"."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        limits = r.data["limit_events"]["five_hour"]
        assert r.data["limit_events"]["near_limit_threshold_pct"] == 90
        assert limits["event_count"] == 2
        assert limits["ceiling_event_count"] == 1

        ceiling, near = limits["events"]
        assert ceiling["sample_count"] == 5
        assert ceiling["hit_ceiling"] is True
        assert ceiling["peak_five_hour_pct"] == 100
        assert ceiling["start_at_iso"].startswith("2026-08-08T12:00")
        assert ceiling["end_at_iso"].startswith("2026-08-08T13:00")
        assert ceiling["duration_minutes"] == 60.0

        # 92 and 93 are near the limit but never reach it -- "you got close"
        # is a different finding from "you ran out".
        assert near["sample_count"] == 2
        assert near["hit_ceiling"] is False
        assert near["peak_five_hour_pct"] == 93


async def test_get_plan_usage_history_splits_a_run_broken_by_a_long_gap(usage_dir):
    """Contiguity is what collapses a run. Two at-limit samples a day apart
    are two events -- the app was closed in between and the five-hour window
    rolled over long before the second one."""
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": [
            _sample(_usage_ms(7, 10, 0), 100, 40),
            _sample(_usage_ms(8, 10, 0), 100, 41),
        ]},
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["limit_events"]["five_hour"]["event_count"] == 2
        assert r.data["limit_events"]["five_hour"]["ceiling_event_count"] == 2


async def test_get_plan_usage_history_daily_high_water(plan_usage_fixture):
    """Buckets are **local** calendar days, so the expectations here are
    derived from each sample's local date rather than written out as
    literals.

    The literals this test used to carry only happened to be right in the
    zone it was written in -- the fixture's UTC timestamps fall on a
    different local day east of about UTC+10. `test_..._buckets_days_in_the
    _local_timezone` below is the one that proves the bucketing is local
    rather than merely self-consistent."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        days = r.data["daily_high_water"]

        expected = sorted({_local_date(s["t"]) for s in USAGE_SAMPLES})
        assert [d["date"] for d in days] == expected

        # The 08-08 ceiling block: six samples, whichever local day they
        # land on together (they span 12:00-13:15 UTC, so they never split).
        busy_date = _local_date(_usage_ms(8, 12, 0))
        busiest = next(d for d in days if d["date"] == busy_date)
        assert busiest["peak_five_hour_pct"] == 100
        assert busiest["sample_count"] == 6
        assert busiest["weekday"] == _local_weekday(_usage_ms(8, 12, 0))
        # The zone is never left to inference -- see D1.
        assert busiest["utc_offset"] == _utc_offset_label(_usage_ms(8, 12, 0))
        assert busiest["first_sample_local_iso"].startswith(busy_date)


async def test_get_plan_usage_history_reports_cadence_and_gaps(plan_usage_fixture):
    """A gap is a closed app, not idle time -- so it has to be visible, and
    the note that says so has to come back with it."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        cadence = r.data["cadence"]
        assert cadence["median_interval_minutes"] == 15.0
        assert cadence["shortest_interval_minutes"] == 15.0
        # 2026-08-09T09:30 to 2026-08-20T08:00.
        assert cadence["longest_gap_hours"] == round(262.5, 2)
        assert cadence["notable_gap_count"] == 3
        assert any("machine was asleep" in n for n in r.data["notes"])


async def test_get_plan_usage_history_scopes_to_a_window(plan_usage_fixture):
    """The standard `time_window` block comes back, and samples outside the
    window are excluded *and* counted -- a caller must be able to say how
    much history the figures leave out."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {"since": "2026-08-15"})
        window = r.data["time_window"]
        assert window["bounded"] is True
        assert window["kept"] == 1
        assert window["excluded"] == 11
        assert window["since"].startswith("2026-08-15")

        assert r.data["sample_count"] == 1
        assert r.data["total_sample_count"] == 12
        # The window drops the ceiling run entirely -- peaks must follow.
        assert r.data["peaks"]["five_hour"]["pct"] == 12
        assert r.data["limit_events"]["five_hour"]["event_count"] == 0
        assert r.data["limit_events"]["seven_day"]["event_count"] == 0
        assert any("Time-scoped to" in n for n in r.data["notes"])


async def test_get_plan_usage_history_window_with_no_samples_is_not_no_usage(
    plan_usage_fixture,
):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {"since": "2027-01-01"})
        assert r.data["available"] is True
        assert r.data["sample_count"] == 0
        assert r.data["total_sample_count"] == 12
        assert r.data["coverage"]["in_window"] is False
        assert any("not as 'no usage'" in n for n in r.data["notes"])


async def test_get_plan_usage_history_bad_bound_widens_and_complains(plan_usage_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {"since": "last tuesday"})
        assert r.data["sample_count"] == 12
        assert any("Could not read" in e for e in r.data["time_window"]["errors"])


async def test_get_plan_usage_history_returns_no_raw_sample_series(usage_dir):
    """The `series` block and its `max_samples` knob are gone, and must stay
    gone.

    Nothing consumed them -- not the renderer, not the skill, not any
    reference doc -- while together they were roughly two thirds of the
    response a model had to read. Worse, the downsampler picked evenly
    spaced indices with no extrema preservation, so a chart drawn from the
    series could omit every limit hit and show a maximum that contradicted
    the `peaks` block sitting beside it in the same response. The
    aggregates are what the analysis actually uses.

    Five hundred samples is the size that made the old series dominate the
    payload; the point here is that the analysis over them is undiminished
    while the raw dump is absent."""
    start = _usage_ms(1, 0, 0)
    quarter_hour = 15 * 60 * 1000
    samples = [_sample(start + i * quarter_hour, i % 101, 20) for i in range(500)]
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": samples},
    )
    async with Client(srv.mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        properties = tools["get_plan_usage_history"].input_schema.get("properties", {})
        assert set(properties) == {"since", "until"}

        r = await client.call_tool("get_plan_usage_history", {})
        assert "series" not in r.data
        assert "max_samples" not in json.dumps(r.data)
        # Every sample is still analysed -- the series went, the analysis
        # did not.
        assert r.data["sample_count"] == 500
        assert r.data["peaks"]["five_hour"]["pct"] == 100
        assert r.data["limit_events"]["five_hour"]["event_count"] > 0


async def test_get_plan_usage_history_missing_file_degrades(usage_dir):
    """A fresh install, or a machine Claude is rarely opened on, simply has
    no such file. That is not evidence of low usage, and it must not raise."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["available"] is False
        assert r.data["sample_count"] == 0
        assert r.data["daily_high_water"] == []
        assert r.data["limit_events"] is None
        assert any("proxy" in n for n in r.data["notes"])


async def test_get_plan_usage_history_malformed_file_degrades(usage_dir):
    (usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME).write_text(
        '{"version": 2, "samples": [{"t": 178334', encoding="utf-8"
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["available"] is False
        assert r.data["sample_count"] == 0
        assert r.data["notes"]


async def test_get_plan_usage_history_empty_samples_list_degrades(usage_dir):
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME, {"version": 2, "samples": []}
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["available"] is False
        # The file was readable, so its schema version still comes back --
        # that is how a caller tells "empty" from "unreadable".
        assert r.data["schema_version"] == 2
        assert r.data["sample_count"] == 0


async def test_get_plan_usage_history_skips_unusable_samples(usage_dir):
    """One malformed sample must not take down the read -- same posture as
    every other reader here."""
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": [
            "not a sample",
            {"org": USAGE_ORG_UUID, "u": {"fh": 50, "sd": 10}},  # no `t`
            {"t": _usage_ms(7, 10, 0), "org": USAGE_ORG_UUID, "u": None},
            _sample(_usage_ms(7, 11, 0), 42, 11),
        ]},
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["sample_count"] == 2
        assert r.data["peaks"]["five_hour"]["pct"] == 42
        assert r.data["coverage"]["unusable_sample_count"] == 2
        # `u: null` yields a timestamped sample with no readings, not zeros:
        # its day is still bucketed and counted, and carries no percentage
        # at all rather than a fabricated 0.
        days = r.data["daily_high_water"]
        assert sum(d["sample_count"] for d in days) == 2
        peaks = [d["peak_five_hour_pct"] for d in days]
        assert 42 in peaks
        assert 0 not in peaks


async def test_get_plan_usage_history_without_desktop_dir(no_data_paths):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["available"] is False
        assert r.data["source_file"] is None
        assert any("check_data_access" in n for n in r.data["notes"])


# --------------------------------------------------------------------------------
# D1 -- days and weekdays are the reader's, not UTC's
# --------------------------------------------------------------------------------


@pytest.fixture
def straddling_sample():
    """One instant whose LOCAL calendar day is not its UTC calendar day.

    This is what makes the timezone tests below worth running: a fixture
    whose two dates agree would pass under UTC bucketing just as happily as
    under local bucketing, and would prove nothing. Late evening local time
    crosses into the next UTC day west of Greenwich; early morning local
    time falls back into the previous UTC day east of it. `.astimezone()`
    on a naive datetime reads it as local wall-clock time and attaches the
    offset actually in force then, so this is right on either side of a
    daylight-saving boundary.

    On a machine running in UTC no such instant exists, and the test skips
    rather than pretending to have checked something."""
    for hour, minute in ((23, 30), (0, 30)):
        moment = datetime(2026, 8, 20, hour, minute).astimezone()
        epoch_ms = int(moment.timestamp() * 1000)
        utc_date = datetime.fromtimestamp(epoch_ms / 1000, UTC).strftime("%Y-%m-%d")
        local_date = moment.strftime("%Y-%m-%d")
        if utc_date != local_date:
            return {
                "at": epoch_ms,
                "local_date": local_date,
                "local_weekday": moment.strftime("%A"),
                "utc_date": utc_date,
                "utc_offset": _utc_offset_label(epoch_ms),
            }
    pytest.skip(
        "this machine's timezone is UTC, so no instant has a local day that "
        "differs from its UTC day and local-vs-UTC bucketing is untestable here"
    )
    return None  # pragma: no cover -- pytest.skip raises


async def test_get_plan_usage_history_buckets_days_in_the_local_timezone(
    usage_dir, straddling_sample
):
    """The reported day, weekday, and peak date must be the user's own, not
    UTC's.

    This is the defect that made the plugin state something false: UTC
    bucketing moved the reported seven-day peak from Thursday to Friday on
    a real 545-sample file and changed 25 of 30 day labels. The audience
    reads dates off a calendar in the zone they live in, so a weekday
    computed in a zone they don't live in is a wrong answer, not a caveat
    on a right one.

    Every assertion here is chosen to FAIL under the old UTC bucketing: the
    fixture's single sample lands on one calendar day locally and a
    different one in UTC."""
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": [_sample(straddling_sample["at"], 100, 95)]},
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})

        days = r.data["daily_high_water"]
        assert [d["date"] for d in days] == [straddling_sample["local_date"]]
        # The UTC day is what the old bucketing would have reported.
        assert straddling_sample["utc_date"] not in [d["date"] for d in days]
        assert days[0]["weekday"] == straddling_sample["local_weekday"]
        assert days[0]["utc_offset"] == straddling_sample["utc_offset"]

        # A peak is the figure most likely to be repeated back as a
        # sentence ("you ran out on Thursday afternoon").
        for peak in (r.data["peaks"]["five_hour"], r.data["peaks"]["seven_day"]):
            assert peak["local_date"] == straddling_sample["local_date"]
            assert peak["local_weekday"] == straddling_sample["local_weekday"]
            assert peak["at_local_iso"].startswith(straddling_sample["local_date"])
            # ...with the UTC form still beside it, for joining.
            assert peak["at_iso"].startswith(straddling_sample["utc_date"])

        # Limit events carry the same pair, for both windows.
        for window in ("five_hour", "seven_day"):
            event = r.data["limit_events"][window]["events"][0]
            assert event["start_local_date"] == straddling_sample["local_date"]
            assert event["start_local_weekday"] == straddling_sample["local_weekday"]
            assert event["end_local_date"] == straddling_sample["local_date"]

        assert r.data["timezone"]["utc_offset"] == straddling_sample["utc_offset"]
        assert r.data["coverage"]["first_sample_local_date"] == (
            straddling_sample["local_date"]
        )
        assert any("machine's own timezone" in n for n in r.data["notes"])


async def test_get_plan_usage_history_notes_never_quote_a_bare_utc_date(
    usage_dir, straddling_sample
):
    """The notes used to build their dates by slicing `at_iso[:10]`, which
    handed the reader a UTC date in a sentence written for a person. A
    business user should never be given a date to convert."""
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": [_sample(straddling_sample["at"], 50, 40)]},
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        coverage_note = next(
            n for n in r.data["notes"] if "The samples on this machine run from" in n
        )
        assert straddling_sample["local_date"] in coverage_note
        assert straddling_sample["utc_date"] not in coverage_note


class TestOutOfRangeTimestampRendering:
    """D4(a). A numerically valid epoch value can still be outside the range
    a calendar can express -- `1e18` milliseconds is roughly the year
    31,000,000, and a garbled field really does produce values like that.
    `datetime.fromtimestamp` raises `ValueError`/`OverflowError`/`OSError`
    on those, depending on platform, and every one of the three timestamp
    renderings has to swallow it: an unreadable `t` on one sample must not
    take down a whole tool call."""

    UNRENDERABLE = (
        1e18,  # ~year 31,000,000
        -1e18,
        10**19,
        float("nan"),
        float("inf"),
        float("-inf"),
    )

    @pytest.mark.parametrize("value", UNRENDERABLE)
    def test_iso(self, value):
        assert tw.iso(value) is None

    @pytest.mark.parametrize("value", UNRENDERABLE)
    def test_local_iso(self, value):
        assert tw.local_iso(value) is None

    @pytest.mark.parametrize("value", UNRENDERABLE)
    def test_local_day(self, value):
        assert tw.local_day(value) is None

    @pytest.mark.parametrize("value", UNRENDERABLE)
    def test_local_timezone_label_falls_back_rather_than_raising(self, value):
        """The zone label is anchored to a supplied instant so it matches the
        most recent thing being reported. An unusable anchor must degrade to
        the current zone, not take the response down with it."""
        label = tw.local_timezone_label(value)
        assert label["utc_offset"].startswith("UTC")
        assert label["source"]


async def test_get_plan_usage_history_skips_unrenderable_timestamps(usage_dir):
    """D4(a), end to end. A sample whose `t` normalizes to a number but
    can't be rendered as a date is skipped whole and counted -- keeping it
    would put a None where every consumer expects a date string."""
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": [
            {"t": 1e18, "org": USAGE_ORG_UUID, "u": {"fh": 99, "sd": 99}},
            {"t": -1e18, "org": USAGE_ORG_UUID, "u": {"fh": 98, "sd": 98}},
            _sample(_usage_ms(7, 11, 0), 42, 11),
        ]},
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["available"] is True
        assert r.data["sample_count"] == 1
        # The unreadable ones contributed nothing -- not even their
        # percentages, which would otherwise have set a false peak.
        assert r.data["peaks"]["five_hour"]["pct"] == 42
        assert r.data["coverage"]["unusable_sample_count"] == 2
        assert any("no timestamp this plugin can read" in n for n in r.data["notes"])


async def test_get_plan_usage_history_invalid_utf8_file_degrades(usage_dir):
    """D4(b). Bytes that aren't UTF-8 raise `UnicodeDecodeError`, which is
    not a `JSONDecodeError` -- so a reader catching only `OSError` and
    `JSONDecodeError` went down on a single bad byte. A partially written
    or disk-corrupted file lands here, and the reader's own docstring
    promises it degrades."""
    (usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME).write_bytes(
        b'{"version": 2, "samples": [{"t": 1786000000000, "u": {"fh": \xff\xfe}}]}'
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["available"] is False
        assert r.data["sample_count"] == 0
        assert r.data["notes"]


async def test_get_plan_usage_history_drops_nan_and_infinity_readings(usage_dir):
    """D4(c). Python's `json` parses the non-standard `NaN`/`Infinity`
    literals happily, so a corrupt file really does deliver one.

    They are **dropped, not clamped**, and counted. Clamping `1e+308` to
    100 would manufacture "you hit your limit" out of file corruption, and
    there is no honest number to substitute -- so the reading reads as "not
    recorded" and the drop is said out loud."""
    (usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME).write_text(
        json.dumps({"version": 2, "samples": [
            {"t": _usage_ms(7, 10, 0), "org": USAGE_ORG_UUID,
             "u": {"fh": float("nan"), "sd": float("inf"), "xu": 1e308}},
            {"t": _usage_ms(7, 11, 0), "org": USAGE_ORG_UUID,
             "u": {"fh": 42, "sd": 11}},
        ]}),
        encoding="utf-8",
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        # The sample is timestamped, so it survives; only its readings go.
        assert r.data["sample_count"] == 2
        assert r.data["coverage"]["unusable_sample_count"] == 0
        assert r.data["coverage"]["unusable_reading_count"] == 3
        assert r.data["peaks"]["five_hour"]["pct"] == 42
        assert r.data["peaks"]["seven_day"]["pct"] == 11
        assert any("dropped rather than clamped" in n for n in r.data["notes"])


def test_build_plan_usage_history_is_strict_json(usage_dir):
    """The reason dropping matters mechanically, not just editorially: a
    `NaN` or `Infinity` token re-emitted into the response makes it
    non-standard JSON that a strict parser on the other end of the tool
    call rejects outright. `allow_nan=False` is that parser."""
    (usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME).write_text(
        '{"version": 2, "samples": ['
        '{"t": 1786000000000, "u": {"fh": NaN, "sd": Infinity, "xu": -Infinity}},'
        '{"t": 1786003600000, "u": {"fh": 42, "sd": 11}}]}',
        encoding="utf-8",
    )
    response = local_data.build_plan_usage_history()
    assert response["sample_count"] == 2
    json.dumps(response, allow_nan=False)


def test_build_plan_usage_history_routes_every_return_path_through_redact(
    usage_dir, monkeypatch
):
    """D5. Every reader in this plugin routes its output through `redact`
    before it leaves, and this one did not.

    Nothing in the response is secret-shaped today -- the point is that the
    guarantee holds without anyone re-checking after the next field is
    added. Swapping `redact` for a sentinel proves it on *every* return
    path, including the three degraded ones, which four separate inline
    calls would have been free to miss."""
    monkeypatch.setattr(local_data, "redact", lambda value: {"went_through_redact": True})

    # Populated.
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": USAGE_SAMPLES},
    )
    assert local_data.build_plan_usage_history() == {"went_through_redact": True}
    # Window that excludes every sample.
    assert local_data.build_plan_usage_history(since="2027-01-01") == {
        "went_through_redact": True
    }
    # File present but unusable.
    (usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME).write_text("{", encoding="utf-8")
    assert local_data.build_plan_usage_history() == {"went_through_redact": True}
    # No Desktop directory at all.
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [])
    assert local_data.build_plan_usage_history() == {"went_through_redact": True}


# --------------------------------------------------------------------------------
# D3 -- the seven-day limit, reported as visibly as the five-hour one
# --------------------------------------------------------------------------------

# A week of running out, sampled the way Desktop actually samples it: an
# evening at the cap, overnight silence, the next day still at the cap, and
# a ceiling hit on the third day. That is ONE week of being nearly out of
# the weekly allowance, not three events -- the seven-day window drains at
# roughly 14% a day, so it does not refill overnight the way the five-hour
# one does. `fh` is held low throughout so the five-hour analysis has
# nothing to find and cannot be what these assertions are reading.
SEVEN_DAY_SAMPLES = [
    _sample(_usage_ms(10, 20, 0), 20, 95),
    _sample(_usage_ms(11, 9, 0), 15, 96),   # 13h later -- overnight, same week
    _sample(_usage_ms(11, 21, 0), 25, 97),
    _sample(_usage_ms(12, 10, 0), 30, 100),  # ran out
    # ...and a separate week, twelve days on, well past any run-break.
    _sample(_usage_ms(24, 9, 0), 20, 98),
    _sample(_usage_ms(24, 10, 0), 20, 99),
]


@pytest.fixture
def seven_day_fixture(usage_dir):
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": SEVEN_DAY_SAMPLES},
    )
    return usage_dir


async def test_get_plan_usage_history_detects_seven_day_limit_events(seven_day_fixture):
    """The weekly cap is the one that strands a business user for days
    rather than hours, and it used to be invisible: limit events were
    computed for `fh` only, so nothing detected `sd` exhaustion -- no
    event, no framing, no guidance."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        weekly = r.data["limit_events"]["seven_day"]

        assert weekly["window"] == "seven-day"
        assert weekly["event_count"] == 2
        assert weekly["ceiling_event_count"] == 1

        ran_out, got_close = weekly["events"]
        # Four samples across three calendar days, broken only by overnight
        # gaps -- one week of being nearly out, not three events.
        assert ran_out["sample_count"] == 4
        assert ran_out["hit_ceiling"] is True
        assert ran_out["peak_seven_day_pct"] == 100
        assert ran_out["start_at_iso"].startswith("2026-08-10T20:00")
        assert ran_out["end_at_iso"].startswith("2026-08-12T10:00")
        assert ran_out["duration_hours"] == 38.0
        # "You ran out of your week and your five-hour window was at 30%"
        # is a materially different situation from the same event at 95%.
        assert ran_out["five_hour_pct_at_end"] == 30

        assert got_close["sample_count"] == 2
        assert got_close["hit_ceiling"] is False
        assert got_close["peak_seven_day_pct"] == 99

        # Nothing here is a five-hour finding -- `fh` never nears the cap.
        assert r.data["limit_events"]["five_hour"]["event_count"] == 0


async def test_get_plan_usage_history_reports_both_windows_in_the_same_shape(
    seven_day_fixture,
):
    """"Equally visible" is a shape claim, not a wording one. Five-hour is
    no longer the unlabelled default with seven-day bolted on underneath:
    a caller reading one block cannot miss the other, because they are the
    same block twice."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        limits = r.data["limit_events"]
        assert set(limits) == {"near_limit_threshold_pct", "five_hour", "seven_day"}
        assert set(limits["five_hour"]) == set(limits["seven_day"])

        # ...and the note names both counts side by side, so a summary built
        # from the notes alone still mentions the weekly cap.
        note = next(n for n in r.data["notes"] if "two separate limits" in n)
        assert "five-hour window reached or neared its limit in 0 run(s)" in note
        assert "seven-day window in 2 run(s)" in note
        assert "rest of the week" in note


async def test_get_plan_usage_history_windows_use_different_run_breaks(
    seven_day_fixture, usage_dir
):
    """The one deliberate asymmetry between the two windows, pinned so it
    can't be "tidied" into a single constant.

    Five hours of quiet empties the five-hour window, so a 13-hour hole
    genuinely separates two five-hour events. The seven-day window drains
    at ~14% a day, so the same hole is the middle of one continuous week of
    being nearly out. Same timestamps, same gaps, opposite groupings --
    which is exactly what a shared run-break would get wrong for one of
    them."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        assert r.data["limit_events"]["five_hour"]["run_break_hours"] == 2.0
        assert r.data["limit_events"]["seven_day"]["run_break_hours"] == 36.0
        # Four samples, three overnight-sized gaps, ONE weekly event.
        assert r.data["limit_events"]["seven_day"]["events"][0]["sample_count"] == 4

    # The identical timestamps, read as five-hour exhaustion instead.
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": [
            _sample(s["t"], s["u"]["sd"], 20) for s in SEVEN_DAY_SAMPLES[:4]
        ]},
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        five_hour = r.data["limit_events"]["five_hour"]
        assert five_hour["event_count"] == 4
        assert all(e["sample_count"] == 1 for e in five_hour["events"])
        assert r.data["limit_events"]["seven_day"]["event_count"] == 0


async def test_get_plan_usage_history_collapses_a_contiguous_seven_day_run(usage_dir):
    """The five-hour collapse test's exact counterpart: sampling every ~15
    minutes means one week at the cap produces dozens of samples, and
    reporting those as dozens of "weekly limit hits" would badly overstate
    how often the user actually ran out."""
    quarter_hour = 15 * 60 * 1000
    start = _usage_ms(10, 8, 0)
    _write_json(
        usage_dir / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": [
            _sample(start + i * quarter_hour, 10, 100) for i in range(12)
        ]},
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_plan_usage_history", {})
        weekly = r.data["limit_events"]["seven_day"]
        assert weekly["event_count"] == 1
        assert weekly["ceiling_event_count"] == 1
        assert weekly["events"][0]["sample_count"] == 12
        assert weekly["events"][0]["duration_minutes"] == 165.0


# --------------------------------------------------------------------------------
# get_desktop_settings -- standing grants, app-wide toggles, installed extensions
# --------------------------------------------------------------------------------

# Grant paths are built from the real home directory so breadth detection is
# exercised the way it runs on a real machine, without hardcoding a username.
BROAD_HOME_GRANT = str(Path.home() / "Documents")
BROAD_CLOUD_GRANT = str(Path.home() / "Library" / "CloudStorage" / "ShadeTree")
NARROW_GRANT = "/Users/x/proj1/sub"

# Planted secrets. None of these may appear anywhere in a response.
OAUTH_TOKEN_MARKER = "SUPER-SECRET-OAUTH-TOKEN-DO-NOT-LEAK"
OAUTH_TOKEN_V2_MARKER = "SECOND-SECRET-OAUTH-TOKEN-DO-NOT-LEAK"
NON_ALLOWLISTED_MARKER = "NOT-ALLOWLISTED-CONFIG-VALUE"
PREFERENCE_SECRET_MARKER = "PREFERENCE-SHAPED-SECRET-DO-NOT-LEAK"
MANIFEST_MARKER = "MANIFEST-BLOB-SHOULD-NEVER-BE-RETURNED"
# An MCP server block's `env` is where an API key lives. `mcpServers` has to
# be *surfaced* (D6) without its values being dumped.
MCP_SERVER_ENV_MARKER = "MCP-SERVER-ENV-SECRET-DO-NOT-LEAK"
TOP_LEVEL_SECRET_MARKER = "TOP-LEVEL-SECRET-DO-NOT-LEAK"
# Planted in the Claude Code CLI's identically-named scheduled-tasks.json.
# That store is out of scope for this plugin entirely, so this string must
# never appear in any response.
CLI_SCHEDULED_TASK_MARKER = "CLAUDE-CODE-CLI-TASK-MUST-NOT-BE-READ"

CONFIGURED_USER_FILES_DIRNAME = "ClaudeConfiguredOutput"

# The Space one of the fixture's remote sessions was started in. Grants are
# keyed by opaque `session_01...`-style ids; this is the name a user has
# actually seen in the app, which is the only thing worth reporting.
SESSION_SPACE_ID = "space-vacation"
SESSION_SPACE_NAME = "Vacation - July 2026"

DESKTOP_CONFIG_FIXTURE = {
    "coworkUserFilesPath": None,  # filled in per-test with a real tmp path
    # Two keys at the file's own top level, outside `preferences`. Both used
    # to be dropped without a trace: `mcpServers` must surface as an
    # unrecognized setting (summarized, never dumped), and the
    # credential-shaped one must not surface at all.
    "mcpServers": {
        "weather": {
            "command": "uvx",
            "args": ["weather-mcp"],
            "env": {"WEATHER_API_KEY": MCP_SERVER_ENV_MARKER},
        }
    },
    "globalShortcutToken": TOP_LEVEL_SECRET_MARKER,
    "preferences": {
        "localAgentModeTrustedFolders": [BROAD_HOME_GRANT, BROAD_CLOUD_GRANT, NARROW_GRANT],
        "remoteFolderConsentMemory": [{"path": "/Users/x/proj2"}],
        "remoteSessionFolderGrants": {
            "sessA": ["/Users/x/proj1"],
            "sessB": ["/Users/x/proj1", "/Users/x/proj2/deep"],
        },
        "allowAllBrowserActions": True,
        "coworkScheduledTasksEnabled": False,
        "sidebarMode": "compact",
        "bypassPermissionsGateByAccount": {"ACCOUNT-UUID-A": True, "ACCOUNT-UUID-B": False},
        # Two keys Claude Desktop hasn't shipped yet, as far as this plugin
        # knows -- they must survive into `unrecognized_settings`.
        "someBrandNewToggle": True,
        "epitaxyPrefs": {"a": 1, "b": 2, "c": 3},
        # ...and one whose name looks credential-shaped, which must not.
        "sessionAuthToken": PREFERENCE_SECRET_MARKER,
    },
}

EXTENSIONS_FIXTURE = {
    "extensions": {
        "ant.chrome-control": {
            "id": "ant.chrome-control",
            "version": "0.1.6",
            "hash": "deadbeef",
            "installedAt": "2026-06-29T12:00:00Z",
            "source": "registry",
            "signatureInfo": {"status": "unsigned"},
            "manifest": {"name": MANIFEST_MARKER, "tools": [{"n": i} for i in range(40)]},
        },
        "third.party.helper": {
            "id": "third.party.helper",
            "version": "2.0.0",
            "installedAt": 1780000000000,
            "source": "sideload",
            "signatureInfo": {"status": "valid"},
            "manifest": {"name": "small"},
        },
    }
}

APP_CONFIG_FIXTURE = {
    "first_launch_at": 1750000000000,
    "locale": "en-US",
    "userThemeMode": "system",
    # config.json really does hold live credentials next to the three
    # harmless keys this plugin reads. That is why the reader is an
    # allowlist rather than a redaction pass.
    "oauth:tokenCache": OAUTH_TOKEN_MARKER,
    "oauth:tokenCacheV2": {"accessToken": OAUTH_TOKEN_V2_MARKER, "refresh": "x"},
    "someOtherPrivateThing": NON_ALLOWLISTED_MARKER,
}


@pytest.fixture
def desktop_settings_dir(tmp_path, monkeypatch):
    """Factory for a Desktop app-data directory carrying the three
    account-level settings files. `omit` drops one of them, so each file's
    absence can be checked independently -- a fresh install has none of
    them, and any one can be missing on its own."""

    def _make(omit: tuple[str, ...] = ()) -> Path:
        desktop_root = tmp_path / "desktop" / "Claude"
        desktop_root.mkdir(parents=True, exist_ok=True)
        configured_output = tmp_path / CONFIGURED_USER_FILES_DIRNAME
        configured_output.mkdir(exist_ok=True)
        org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"

        if "desktop_config" not in omit:
            config = json.loads(json.dumps(DESKTOP_CONFIG_FIXTURE))
            config["coworkUserFilesPath"] = str(configured_output)
            _write_json(desktop_root / local_data.DESKTOP_CONFIG_FILENAME, config)
        if "extensions" not in omit:
            _write_json(desktop_root / local_data.EXTENSIONS_FILENAME, EXTENSIONS_FIXTURE)
        if "app_config" not in omit:
            _write_json(desktop_root / local_data.APP_CONFIG_FILENAME, APP_CONFIG_FIXTURE)
        if "session_spaces" not in omit:
            # The two-step lookup that turns a `session_01...` grant key into
            # a Space name: remote-session-spaces.json for session -> Space,
            # spaces.json for Space -> name. sessA resolves; sessB is
            # deliberately absent from the lookup table.
            _write_json(org_dir / "spaces.json", {"spaces": [
                {"id": SESSION_SPACE_ID, "name": SESSION_SPACE_NAME, "folders": []},
            ]})
            _write_json(org_dir / local_data.REMOTE_SESSION_SPACES_FILENAME, {"entries": [
                {
                    "sessionId": "sessA",
                    "spaceId": SESSION_SPACE_ID,
                    "folders": ["/Users/x/proj1"],
                    "memoryEnabled": True,
                },
            ]})
        if "scheduled_tasks" not in omit:
            # The only shape ever observed on a real install: the file and
            # its schema exist, and the list is empty.
            _write_json(org_dir / local_data.SCHEDULED_TASKS_FILENAME, {
                "scheduledTasks": [],
                "recordedSkips": {},
                "sundayAliasBoundaryStamped": True,
                "dayFieldsOrBoundaryStamped": True,
            })
        # An identically-named file lives under the Claude Code CLI's own
        # session store. That surface is out of scope for this plugin, and
        # the reader's glob is anchored to `local-agent-mode-sessions/` to
        # keep it that way -- see the regression test below.
        _write_json(
            desktop_root / "claude-code-sessions" / "ACCOUNT" / "ORG"
            / local_data.SCHEDULED_TASKS_FILENAME,
            {"scheduledTasks": [{"name": CLI_SCHEDULED_TASK_MARKER}]},
        )

        monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop_root])
        monkeypatch.setattr(
            local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"]
        )
        return desktop_root

    return _make


@pytest.fixture
def desktop_settings_fixture(desktop_settings_dir):
    return desktop_settings_dir()


async def test_get_desktop_settings_reports_standing_folder_grants(
    desktop_settings_fixture,
):
    """Standing grants are the point of this reader: they were approved once
    and stay in force, so they appear in no session's folder list."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        scope = r.data["access_scope"]

        trusted = scope["trusted_folders"]
        assert trusted["setting"] == "localAgentModeTrustedFolders"
        assert trusted["count"] == 3
        assert trusted["broad_count"] == 2
        assert [g["path"] for g in trusted["grants"]] == [
            BROAD_HOME_GRANT, BROAD_CLOUD_GRANT, NARROW_GRANT
        ]

        home_grant, cloud_grant, narrow = trusted["grants"]
        assert home_grant["looks_broad"] is True
        assert any("Documents" in reason for reason in home_grant["breadth_reasons"])
        assert cloud_grant["looks_broad"] is True
        assert any(
            "cloud-storage" in reason for reason in cloud_grant["breadth_reasons"]
        )
        # Flagged, never scored -- one project folder is not a finding.
        assert narrow["looks_broad"] is False
        assert narrow["breadth_reasons"] == []

        assert scope["remote_folder_consent"]["count"] == 1
        assert scope["remote_folder_consent"]["broad_count"] == 0

        session_grants = scope["remote_session_folder_grants"]
        assert session_grants["session_count"] == 2
        assert session_grants["unique_path_count"] == 2
        assert session_grants["broad_path_count"] == 0


async def test_get_desktop_settings_reports_app_wide_toggles(desktop_settings_fixture):
    """These are account-wide switches that no session file carries, keyed
    by their on-disk names so a finding can name the setting to change."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        settings = r.data["settings"]
        assert settings["allowAllBrowserActions"] is True
        assert settings["coworkScheduledTasksEnabled"] is False
        assert settings["sidebarMode"] == "compact"

        # A `{account uuid: value}` map, with the uuids replaced.
        by_account = settings["bypassPermissionsGateByAccount"]
        assert by_account["account_count"] == 2
        assert by_account["any_enabled"] is True
        assert set(by_account["values_by_account"]) == {"account 1", "account 2"}
        assert "ACCOUNT-UUID-A" not in json.dumps(r.data)


async def test_get_desktop_settings_keeps_unrecognized_preference_keys(
    desktop_settings_fixture,
):
    """Claude Desktop ships new settings regularly. Silently dropping keys
    this plugin doesn't know is how a reader like this goes stale without
    anyone noticing, so they are surfaced -- summarized, not dumped."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        unrecognized = r.data["unrecognized_settings"]
        assert unrecognized["someBrandNewToggle"] is True
        assert unrecognized["epitaxyPrefs"] == {
            "type": "object", "field_count": 3, "size_chars": len('{"a": 1, "b": 2, "c": 3}')
        }
        # Grants and named toggles are reported properly elsewhere, so they
        # must not be duplicated in here.
        assert "localAgentModeTrustedFolders" not in unrecognized
        assert "allowAllBrowserActions" not in unrecognized
        assert any("unrecognized_settings" in n for n in r.data["notes"])


async def test_get_desktop_settings_surfaces_unknown_top_level_config_keys(
    desktop_settings_fixture,
):
    """D6. `unrecognized_settings` spans BOTH namespaces in
    `claude_desktop_config.json` -- unknown keys inside `preferences`, and
    unknown keys at the file's own top level.

    The top-level ones used to be dropped silently, which made a documented
    promise ("an unrecognized `mcpServers` key would show up in
    `unrecognized_settings`") untrue. Silently discarding unknown keys is
    exactly how a reverse-engineered reader goes stale without anyone
    noticing, so the fix belongs in the code rather than the doc."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        unrecognized = r.data["unrecognized_settings"]

        assert unrecognized["mcpServers"] == {
            "type": "object",
            "field_count": 1,
            "size_chars": len(json.dumps(DESKTOP_CONFIG_FIXTURE["mcpServers"])),
        }
        # Surfaced as a shape, never dumped: an MCP server block's `env`
        # holds API keys.
        assert MCP_SERVER_ENV_MARKER not in json.dumps(r.data)
        assert "WEATHER_API_KEY" not in json.dumps(r.data)

        # A top-level key whose name looks credential-shaped is withheld the
        # same way a preference one is -- the new namespace is not a new gap.
        assert "globalShortcutToken" not in unrecognized
        assert TOP_LEVEL_SECRET_MARKER not in json.dumps(r.data)

        # `coworkUserFilesPath` and `preferences` are consumed properly
        # elsewhere, so they are not "unrecognized".
        assert "coworkUserFilesPath" not in unrecognized
        assert "preferences" not in unrecognized


async def test_get_desktop_settings_labels_a_top_level_preference_name_collision(
    desktop_settings_dir,
):
    """The two namespaces are different settings that can, in principle,
    share a name. Label rather than clobber -- losing one silently is the
    same defect in miniature."""
    desktop_root = desktop_settings_dir()
    _write_json(desktop_root / local_data.DESKTOP_CONFIG_FILENAME, {
        "sameName": "the top-level one",
        "preferences": {"sameName": "the preference one"},
    })
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        unrecognized = r.data["unrecognized_settings"]
        assert unrecognized["sameName"] == "the preference one"
        assert unrecognized["sameName (top level)"] == "the top-level one"


async def test_get_desktop_settings_counts_each_broad_path_once(desktop_settings_dir):
    """D8. `broad_grants` is deduplicated across settings and split by how
    long the grant lasts.

    Two things were wrong in one sentence. Summing the per-setting broad
    counts counted a folder listed in two settings twice -- one folder, one
    exposure -- and folding the remote-session grants into that total called
    them "standing", contradicting the note immediately above it. A session
    grant applies to one remote session and does not carry forward."""
    desktop_root = desktop_settings_dir()
    session_only_broad = str(Path.home() / "Downloads")
    _write_json(desktop_root / local_data.DESKTOP_CONFIG_FILENAME, {
        "preferences": {
            # The same broad folder, granted three different ways.
            "localAgentModeTrustedFolders": [BROAD_HOME_GRANT, NARROW_GRANT],
            "remoteFolderConsentMemory": [BROAD_HOME_GRANT],
            "remoteSessionFolderGrants": {
                "sessA": [BROAD_HOME_GRANT, session_only_broad],
            },
        },
    })
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        scope = r.data["access_scope"]
        broad = scope["broad_grants"]

        # The naive sum of the per-setting counts is 4 (1 + 1 + 2). There
        # are two distinct broad folders, and only one of them is standing.
        naive_sum = (
            scope["trusted_folders"]["broad_count"]
            + scope["remote_folder_consent"]["broad_count"]
            + scope["remote_session_folder_grants"]["broad_path_count"]
        )
        assert naive_sum == 4
        assert broad["standing_path_count"] == 1
        assert broad["standing_paths"] == [BROAD_HOME_GRANT]

        # The session-scoped one is reported separately, and the folder that
        # is *also* standing is not double-counted into it.
        assert broad["session_scoped_path_count"] == 1
        assert broad["session_scoped_paths"] == [session_only_broad]
        assert BROAD_HOME_GRANT not in broad["session_scoped_paths"]

        standing_note = next(n for n in r.data["notes"] if "standing grant(s) cover" in n)
        assert standing_note.startswith("1 distinct standing grant(s)")
        session_note = next(n for n in r.data["notes"] if "broad folder(s) were granted" in n)
        assert "A further 1 broad folder(s)" in session_note
        # ...and it does not contradict the note above it.
        assert "NOT standing grants" in session_note


async def test_get_desktop_settings_resolves_a_session_grant_to_its_space(
    desktop_settings_fixture,
):
    """G2. `remoteSessionFolderGrants` is keyed by raw `session_01...` ids,
    which name nothing a user has ever seen in the app.
    `remote-session-spaces.json` maps each to a Space and `spaces.json`
    names it."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        block = r.data["access_scope"]["remote_session_folder_grants"]
        by_session = {entry["session_id"]: entry for entry in block["by_session"]}

        resolved = by_session["sessA"]
        assert resolved["space_resolved"] is True
        assert resolved["space_resolution"] == "matched"
        assert resolved["space_id"] == SESSION_SPACE_ID
        assert resolved["space_name"] == SESSION_SPACE_NAME
        assert resolved["space_label"] == SESSION_SPACE_NAME
        # From the lookup table -- what the session was actually started
        # with, not what the grant list says was approved.
        assert resolved["space_folders"] == ["/Users/x/proj1"]
        assert resolved["space_memory_enabled"] is True

        assert block["space_lookup_available"] is True
        assert block["resolved_space_count"] == 1
        assert block["unresolved_space_count"] == 1
        assert any("never the `session_id`" in n for n in r.data["notes"])


async def test_get_desktop_settings_never_reports_a_bare_session_id_as_a_space(
    desktop_settings_fixture,
):
    """The unresolved case, which is the one that matters: a raw id printed
    in a report reads as an answer while being none. Each outcome says what
    happened instead."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        block = r.data["access_scope"]["remote_session_folder_grants"]
        unresolved = next(e for e in block["by_session"] if e["session_id"] == "sessB")

        assert unresolved["space_resolved"] is False
        assert unresolved["space_resolution"] == "session_not_listed"
        assert unresolved["space_id"] is None
        assert unresolved["space_name"] is None
        assert "sessB" not in unresolved["space_label"]
        assert "unidentified Space" in unresolved["space_label"]


async def test_get_desktop_settings_names_every_unresolvable_space_situation(
    desktop_settings_dir,
):
    """The other two ways resolution fails, each with its own explanation:
    a Space deleted in the app after the grant was made, and a session
    listed with no Space id at all."""
    desktop_root = desktop_settings_dir()
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(desktop_root / local_data.DESKTOP_CONFIG_FILENAME, {
        "preferences": {
            "remoteSessionFolderGrants": {
                "sessDeleted": ["/Users/x/proj1"],
                "sessNoSpace": ["/Users/x/proj2"],
            },
        },
    })
    _write_json(org_dir / local_data.REMOTE_SESSION_SPACES_FILENAME, {"entries": [
        {"sessionId": "sessDeleted", "spaceId": "space-gone", "folders": []},
        {"sessionId": "sessNoSpace", "folders": []},
    ]})
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        block = r.data["access_scope"]["remote_session_folder_grants"]
        by_session = {entry["session_id"]: entry for entry in block["by_session"]}

        deleted = by_session["sessDeleted"]
        assert deleted["space_resolution"] == "space_id_not_in_spaces_json"
        assert "no longer listed in spaces.json" in deleted["space_label"]
        # Not even the Space id leaks into the label -- it names nothing
        # either.
        assert "space-gone" not in deleted["space_label"]

        no_space = by_session["sessNoSpace"]
        assert no_space["space_resolution"] == "no_space_id_recorded"
        assert "carries no Space id" in no_space["space_label"]

        assert block["resolved_space_count"] == 0
        assert block["unresolved_space_count"] == 2


async def test_get_desktop_settings_reports_an_empty_scheduled_task_list_as_such(
    desktop_settings_fixture,
):
    """G3. The only shape ever observed on a real install: the file and its
    schema exist, and the list is empty.

    Reading that emptiness as "the feature doesn't exist" is what put a
    false "confirmed hard boundary" into the docs -- that nothing
    schedule-shaped is stored locally. It is stored locally. What an empty
    list does *not* establish is that the account has no scheduled tasks:
    they run server-side, and this device may simply not be where they are
    recorded."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        tasks = r.data["scheduled_tasks"]

        assert tasks["available"] is True
        assert tasks["file_count"] == 1
        assert tasks["scheduled_task_count"] == 0
        assert tasks["tasks"] == []
        assert tasks["recorded_skip_count"] == 0
        assert tasks["recorded_skip_ids"] == []
        assert tasks["boundary_flags"] == {
            "sundayAliasBoundaryStamped": True,
            "dayFieldsOrBoundaryStamped": True,
        }
        assert tasks["unrecognized_top_level_fields"] == []
        assert tasks["task_entry_shape_verified"] is False

        note = next(n for n in r.data["notes"] if "scheduled-tasks.json exists" in n)
        assert "does NOT mean the feature is absent" in note
        assert "coworkScheduledTasksEnabled" in note
        # The reading only means something next to the toggle.
        assert r.data["settings"]["coworkScheduledTasksEnabled"] is False


async def test_get_desktop_settings_handles_a_populated_task_list_defensively(
    desktop_settings_dir,
):
    """A populated `scheduledTasks` has never been observed, so the shape of
    an entry is unverified and stays that way. Entries come through
    verbatim-minus-redaction: no field of one is named, parsed, or
    promised, and `task_entry_shape_verified` does not flip just because
    something turned up."""
    desktop_root = desktop_settings_dir()
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(org_dir / local_data.SCHEDULED_TASKS_FILENAME, {
        "scheduledTasks": [
            {"whateverFieldThisTurnsOutToBe": "Weekly summary", "cron": "0 9 * * 1"},
            {"nested": {"shape": ["unknown"]}, "apiToken": PREFERENCE_SECRET_MARKER},
        ],
        "recordedSkips": {"skip-1": {"at": 1}, "skip-2": {"at": 2}},
        "sundayAliasBoundaryStamped": False,
        "dayFieldsOrBoundaryStamped": True,
        "someFieldShippedLater": 7,
    })
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        tasks = r.data["scheduled_tasks"]

        assert tasks["scheduled_task_count"] == 2
        assert tasks["task_entry_shape_verified"] is False
        assert tasks["tasks"][0] == {
            "whateverFieldThisTurnsOutToBe": "Weekly summary", "cron": "0 9 * * 1"
        }
        # Verbatim *minus redaction* -- an unverified shape is not a hole in
        # the secret filter.
        assert "apiToken" not in tasks["tasks"][1]
        assert PREFERENCE_SECRET_MARKER not in json.dumps(r.data)

        assert tasks["recorded_skip_count"] == 2
        assert tasks["recorded_skip_ids"] == ["skip-1", "skip-2"]
        assert tasks["unrecognized_top_level_fields"] == ["someFieldShippedLater"]

        note = next(n for n in r.data["notes"] if "scheduled task(s) are" in n)
        assert "UNVERIFIED" in note


async def test_get_desktop_settings_ignores_the_cli_copy_of_scheduled_tasks(
    desktop_settings_fixture,
):
    """Regression guard. An identically-named `scheduled-tasks.json` exists
    under `claude-code-sessions/` -- the Claude Code CLI's own store, out of
    scope for this plugin entirely.

    The reader's glob is anchored to `local-agent-mode-sessions/`, and the
    fixture plants a populated CLI file so that widening the glob by one
    path segment fails here rather than silently pulling CLI data into a
    Desktop report."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        serialized = json.dumps(r.data)
        assert CLI_SCHEDULED_TASK_MARKER not in serialized
        assert "claude-code-sessions" not in serialized

        tasks = r.data["scheduled_tasks"]
        assert tasks["file_count"] == 1
        assert tasks["scheduled_task_count"] == 0
        assert all(
            "local-agent-mode-sessions" in f["source_file"] for f in tasks["files"]
        )


async def test_get_desktop_settings_without_scheduled_tasks_file(desktop_settings_dir):
    """No file is not "no scheduled tasks" -- it is "nothing can be said
    either way", which is a different sentence."""
    desktop_settings_dir(omit=("scheduled_tasks",))
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        assert r.data["scheduled_tasks"]["available"] is False
        assert r.data["scheduled_tasks"]["scheduled_task_count"] == 0
        assert any("nothing can be said about scheduled tasks" in n for n in r.data["notes"])


async def test_get_desktop_settings_invalid_utf8_files_degrade(desktop_settings_dir):
    """D4(b) on this tool's own inputs. `UnicodeDecodeError` is not a
    `JSONDecodeError`, so one bad byte in any of these files used to take
    down the whole call."""
    desktop_root = desktop_settings_dir()
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    for path in (
        desktop_root / local_data.DESKTOP_CONFIG_FILENAME,
        desktop_root / local_data.EXTENSIONS_FILENAME,
        desktop_root / local_data.APP_CONFIG_FILENAME,
        org_dir / local_data.SCHEDULED_TASKS_FILENAME,
        org_dir / local_data.REMOTE_SESSION_SPACES_FILENAME,
    ):
        path.write_bytes(b'{"preferences": \xff\xfe}')

    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        assert r.data["available"] is True
        assert r.data["settings"] == {}
        assert r.data["extensions"]["available"] is False
        assert r.data["app_config"]["available"] is False
        assert r.data["scheduled_tasks"]["available"] is False
        assert r.data["notes"]


async def test_get_desktop_settings_survives_out_of_range_timestamps(
    desktop_settings_dir,
):
    """D4(a) on this tool's own two timestamp fields.

    `installedAt` and `first_launch_at` go through the same rendering as a
    usage sample's `t`, and the same garbled values reach them -- an epoch
    value that is a perfectly good number and still outside anything a
    calendar can express. Each has to come back as "no usable timestamp"
    rather than taking the tool call down."""
    desktop_root = desktop_settings_dir()
    _write_json(desktop_root / local_data.EXTENSIONS_FILENAME, {"extensions": {
        "ant.chrome-control": {"id": "ant.chrome-control", "installedAt": 1e18},
        "second.one": {"id": "second.one", "installedAt": float("inf")},
    }})
    _write_json(desktop_root / local_data.APP_CONFIG_FILENAME, {
        "first_launch_at": -1e18, "locale": "en-US",
    })
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})

        installed = r.data["extensions"]["installed"]
        assert r.data["extensions"]["available"] is True
        assert len(installed) == 2
        assert all(e["installed_at_iso"] is None for e in installed)

        app_config = r.data["app_config"]
        assert app_config["available"] is True
        assert app_config["first_launch_at_iso"] is None
        assert app_config["locale"] == "en-US"


# --------------------------------------------------------------------------------
# A rejected timestamp must not become a figure
# --------------------------------------------------------------------------------


async def test_get_desktop_settings_refuses_a_tenure_from_an_unreadable_date(
    desktop_settings_dir,
):
    """`account_tenure_days` used to be computed from `first_launch_at` even
    when the same function had already judged that value un-renderable,
    producing figures around 11.5 billion days sitting beside a `null` date
    -- in the field a report is most likely to quote as a plain fact, and
    the denominator behind every per-week rate the run computes."""
    desktop_root = desktop_settings_dir()
    _write_json(desktop_root / local_data.APP_CONFIG_FILENAME, {
        "first_launch_at": 1e18, "locale": "en-US",
    })
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
    app_config = r.data["app_config"]

    assert app_config["available"] is True
    assert app_config["first_launch_at_iso"] is None
    assert app_config["account_tenure_days"] is None
    assert app_config["first_launch_at_usable"] is False
    # The rejected value is still carried, so this is a refusal to derive
    # from it rather than a silent deletion of what the file said.
    assert app_config["first_launch_at"] == 1e18
    # ...and everything else the file did say still comes back.
    assert app_config["locale"] == "en-US"

    note = next(n for n in r.data["notes"] if "account_tenure_days" in n)
    assert "no verified start date" in note


async def test_get_desktop_settings_refuses_a_tenure_from_a_future_date(
    desktop_settings_dir,
):
    """The other half of the same field. A first launch in the future
    renders perfectly well as a date and still has no honest tenure behind
    it -- a negative one is not a smaller number, it is a wrong one."""
    desktop_root = desktop_settings_dir()
    future_ms = int((datetime.now(UTC).timestamp() + 365 * 86400) * 1000)
    _write_json(desktop_root / local_data.APP_CONFIG_FILENAME, {
        "first_launch_at": future_ms, "locale": "en-US",
    })
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
    app_config = r.data["app_config"]

    assert app_config["first_launch_at_iso"] is not None
    assert app_config["account_tenure_days"] is None
    assert app_config["first_launch_at_usable"] is False
    assert any("no verified start date" in n for n in r.data["notes"])


async def test_get_desktop_settings_reports_a_tenure_when_the_date_is_real(
    desktop_settings_fixture,
):
    """The positive control. A refusal that fires on everything would be
    just as useless as the figure it replaced."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
    app_config = r.data["app_config"]

    assert app_config["first_launch_at_usable"] is True
    assert app_config["account_tenure_days"] > 0
    assert not any("no verified start date" in n for n in r.data["notes"])


async def test_get_desktop_settings_clears_the_epoch_beside_a_rejected_date(
    desktop_settings_dir,
):
    """The same shape one field over. An `installed_at_ms` kept beside an
    `installed_at_iso` of `None` is a number that renders as no date at all
    and still reads as a fact."""
    desktop_root = desktop_settings_dir()
    _write_json(desktop_root / local_data.EXTENSIONS_FILENAME, {"extensions": {
        "one": {"id": "one", "installedAt": 1e18},
    }})
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
    extension = r.data["extensions"]["installed"][0]

    assert extension["installed_at_iso"] is None
    assert extension["installed_at_ms"] is None
    assert extension["installed_at_local_date"] is None
    assert extension["installed_at_local_weekday"] is None


async def test_get_desktop_settings_renders_every_date_in_local_time(
    desktop_settings_dir, straddling_instant
):
    """This tool emitted UTC only. On a machine west of UTC that reported a
    first-launch date wrong by a calendar day and a weekday -- and the docs
    instruct the analyst to quote that date back to the user."""
    desktop_root = desktop_settings_dir()
    _write_json(desktop_root / local_data.APP_CONFIG_FILENAME, {
        "first_launch_at": straddling_instant["at"], "locale": "en-US",
    })
    _write_json(desktop_root / local_data.EXTENSIONS_FILENAME, {"extensions": {
        "one": {
            "id": "one",
            "installedAt": straddling_instant["at"],
            "signatureInfo": {"status": "unsigned"},
        },
    }})
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})

    app_config = r.data["app_config"]
    assert app_config["first_launch_at_iso"].startswith(straddling_instant["utc_date"])
    assert app_config["first_launch_at_local_date"] == straddling_instant["local_date"]
    assert app_config["first_launch_at_local_weekday"] == (
        straddling_instant["local_weekday"]
    )
    assert app_config["first_launch_at_local_iso"].startswith(
        straddling_instant["local_date"]
    )
    assert app_config["first_launch_at_utc_offset"] == straddling_instant["utc_offset"]

    extension = r.data["extensions"]["installed"][0]
    assert extension["installed_at_iso"].startswith(straddling_instant["utc_date"])
    assert extension["installed_at_local_date"] == straddling_instant["local_date"]

    # The `timezone` block is anchored on the first-launch instant, not on
    # "now" -- an offset read across a daylight-saving boundary is the wrong
    # label for the date beside it.
    assert r.data["timezone"]["utc_offset"] == straddling_instant["utc_offset"]
    assert any("machine's own timezone" in n for n in r.data["notes"])
    assert _missing_local_companions(r.data) == []


# --------------------------------------------------------------------------------
# Non-finite numbers: dropped, counted, never clamped
# --------------------------------------------------------------------------------
#
# Python's json module parses the non-standard `NaN`/`Infinity` literals
# happily, so a corrupt settings file really can put one into a response --
# and `json.dumps(..., allow_nan=False)`, which is what a strict client
# parser on the other end amounts to, refuses the result outright. Almost
# everything this tool returns is a pass-through, and every one of those
# paths could carry one.


def _corrupt_settings_dir(desktop_root: Path) -> None:
    """Plant a `NaN`/`Infinity` down each of the five pass-through paths."""
    config = json.loads(json.dumps(DESKTOP_CONFIG_FIXTURE))
    # 1. A known preference, copied through by name.
    config["preferences"]["allowAllBrowserActions"] = float("nan")
    # 2. An unrecognized preference scalar.
    config["preferences"]["someBrandNewToggle"] = float("inf")
    # 3. A per-account map's value -- beside a genuine `False`, so a summary
    #    that counts the corrupt entry as "enabled" is visible as a wrong
    #    answer rather than an unlucky one.
    config["preferences"]["bypassPermissionsGateByAccount"] = {
        "ACCOUNT-UUID-A": float("nan"), "ACCOUNT-UUID-B": False,
    }
    # 4. An unrecognized key at the config file's own top level.
    config["someTopLevelCounter"] = float("-inf")
    _write_json(desktop_root / local_data.DESKTOP_CONFIG_FILENAME, config)
    # 5. An extension's `installedAt`, copied through verbatim.
    _write_json(desktop_root / local_data.EXTENSIONS_FILENAME, {"extensions": {
        "one": {"id": "one", "installedAt": float("inf")},
    }})


class TestDropNonFinite:
    def test_replaces_each_non_finite_and_counts_it(self):
        value, dropped = local_data._drop_non_finite(
            {"a": float("nan"), "b": [1.0, float("inf"), float("-inf")], "c": 3}
        )
        assert value == {"a": None, "b": [1.0, None, None], "c": 3}
        assert dropped == 3

    def test_list_length_is_preserved(self):
        """A silently shorter list is a second, quieter lie about the same
        data -- the dropped item stays in place as a None."""
        value, _ = local_data._drop_non_finite([float("nan"), 1, 2])
        assert len(value) == 3

    def test_booleans_and_usable_numbers_are_untouched(self):
        value, dropped = local_data._drop_non_finite(
            {"t": True, "f": False, "n": 0, "z": 0.0}
        )
        assert value == {"t": True, "f": False, "n": 0, "z": 0.0}
        assert dropped == 0


async def test_get_desktop_settings_drops_every_non_finite_pass_through(
    desktop_settings_dir,
):
    desktop_root = desktop_settings_dir()
    _corrupt_settings_dir(desktop_root)
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})

    assert r.data["unusable_number_count"] == 5

    by_account = r.data["settings"]["bypassPermissionsGateByAccount"]
    # Dropped, never clamped: each one reads as "not recorded" rather than
    # as a 0 or a float-max manufactured out of file corruption.
    assert [
        r.data["settings"]["allowAllBrowserActions"],
        r.data["unrecognized_settings"]["someBrandNewToggle"],
        r.data["unrecognized_settings"]["someTopLevelCounter"],
        by_account["values_by_account"]["account 1"],
        r.data["extensions"]["installed"][0]["installed_at"],
    ] == [None] * 5
    assert by_account["unusable_value_count"] == 1
    # `bool(float("nan"))` is True, so the claim *derived* from the corrupt
    # value used to survive the drop that removed the value itself --
    # asserting "yes, this is switched on somewhere" out of file corruption.
    assert by_account["any_enabled"] is False

    assert any("not numbers a response can carry" in n for n in r.data["notes"])


def test_build_desktop_settings_is_strict_json(desktop_settings_dir):
    """`json.dumps(..., allow_nan=False)` is what a strict client parser on
    the other end of a tool call amounts to. The default `allow_nan=True`
    would emit bare `NaN`/`Infinity` tokens that are not JSON at all."""
    _corrupt_settings_dir(desktop_settings_dir())
    result = local_data.build_desktop_settings()
    json.dumps(result, allow_nan=False)
    assert result["unusable_number_count"] == 5


def test_the_settings_fixture_would_have_emitted_non_finite_numbers(
    desktop_settings_dir,
):
    """Guard the guard: prove the planted values really are non-finite on
    disk and really do reach the reader, so the test above is checking a
    scrub rather than an empty file."""
    desktop_root = desktop_settings_dir()
    _corrupt_settings_dir(desktop_root)
    raw = json.loads(
        (desktop_root / local_data.DESKTOP_CONFIG_FILENAME).read_text()
    )
    assert raw["preferences"]["allowAllBrowserActions"] != (
        raw["preferences"]["allowAllBrowserActions"]
    )  # NaN is the only value not equal to itself
    with pytest.raises(ValueError):
        json.dumps(raw, allow_nan=False)


# --------------------------------------------------------------------------------
# Scheduled tasks: a cap on entry SIZE, not just entry count
# --------------------------------------------------------------------------------


async def test_get_desktop_settings_caps_the_size_of_one_task_entry(
    desktop_settings_dir,
):
    """Capping the entry *count* bounded nothing that matters. A scheduled
    task carries the prompt text it runs, so a single populated entry can be
    arbitrarily large -- and entries come through verbatim precisely because
    their shape is unverified."""
    desktop_root = desktop_settings_dir()
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(org_dir / local_data.SCHEDULED_TASKS_FILENAME, {
        "scheduledTasks": [
            {"cron": "0 9 * * 1"},
            {"prompt": "P" * 9000, "apiToken": PREFERENCE_SECRET_MARKER},
        ],
        "recordedSkips": {},
    })
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
    tasks = r.data["scheduled_tasks"]

    assert tasks["scheduled_task_count"] == 2
    assert tasks["oversized_task_count"] == 1
    assert tasks["max_task_entry_chars"] == 2000
    # The small entry is untouched: the cap is on size, not on passing
    # entries through.
    assert tasks["tasks"][0] == {"cron": "0 9 * * 1"}

    replaced = tasks["tasks"][1]
    assert replaced["omitted"] is True
    assert replaced["size_chars"] > 2000
    # Measured after redaction, so the count describes what would actually
    # have been returned.
    assert replaced["top_level_field_count"] == 1
    # A measurement of the entry, not a truncation of it -- a truncation
    # would be a partial verbatim dump under another name, and naming a
    # field of the entry would be inventing a schema no one has seen.
    assert "prompt" not in replaced
    assert "P" * 200 not in json.dumps(r.data)
    assert PREFERENCE_SECRET_MARKER not in json.dumps(r.data)

    note = next(n for n in r.data["notes"] if "per-entry cap" in n)
    assert "too large to include" in note


async def test_a_secret_shaped_key_cannot_change_whether_a_task_fits(
    desktop_settings_dir,
):
    """Redaction runs before measurement. Otherwise a 3,000-character
    credential-shaped value would decide whether a small entry was reported
    at all -- and the entry would be measured at a size it never gets
    returned at."""
    desktop_root = desktop_settings_dir()
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(org_dir / local_data.SCHEDULED_TASKS_FILENAME, {
        "scheduledTasks": [{"cron": "0 9 * * 1", "apiToken": "T" * 3000}],
        "recordedSkips": {},
    })
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
    tasks = r.data["scheduled_tasks"]

    assert tasks["oversized_task_count"] == 0
    assert tasks["tasks"][0] == {"cron": "0 9 * * 1"}
    assert "T" * 100 not in json.dumps(r.data)
    assert not any("per-entry cap" in n for n in r.data["notes"])


async def test_get_desktop_settings_says_unsigned_is_not_a_security_finding(
    desktop_settings_fixture,
):
    """D10. `signature_status` is kept as a drift signal, but on its own it
    invites an alarm this plugin cannot substantiate.

    The only value observed on a real install is `unsigned`, and it is on
    Anthropic's own first-party chrome-control extension. Telling a
    non-technical user their extension is unsafe on that basis would be
    wrong, so the guidance travels with the field rather than being left to
    the reader."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        note = next(n for n in r.data["notes"] if "signature_status" in n)
        assert "normal" in note
        assert "not a security finding" in note
        assert "first-party chrome-control extension" in note
        assert "Never tell a user an extension is unsafe" in note


async def test_get_desktop_settings_withholds_secret_shaped_preference_keys(
    desktop_settings_fixture,
):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        assert "sessionAuthToken" not in r.data["unrecognized_settings"]
        assert PREFERENCE_SECRET_MARKER not in json.dumps(r.data)
        assert any("secret-shaped" in n for n in r.data["notes"])


async def test_the_withheld_key_note_names_the_right_namespace(
    desktop_settings_fixture,
):
    """`globalShortcutToken` sits at the config file's TOP LEVEL;
    `sessionAuthToken` sits inside `preferences`. One counter fed a note
    that called both "preference key(s)", which sends anyone looking for the
    top-level one to the wrong place in Settings."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
    note = next(n for n in r.data["notes"] if "secret-shaped" in n)

    assert note.startswith("2 setting key(s) were withheld")
    assert "1 inside the `preferences` block" in note
    assert "1 at the config file's top level" in note
    assert TOP_LEVEL_SECRET_MARKER not in json.dumps(r.data)
    assert PREFERENCE_SECRET_MARKER not in json.dumps(r.data)


async def test_a_top_level_secret_alone_is_never_called_a_preference(
    desktop_settings_dir,
):
    """The failing case in isolation: a config whose only withheld key is a
    top-level one. Under a single counter this note said "preference"."""
    desktop_root = desktop_settings_dir()
    config = json.loads(json.dumps(DESKTOP_CONFIG_FIXTURE))
    config["preferences"].pop("sessionAuthToken")
    _write_json(desktop_root / local_data.DESKTOP_CONFIG_FILENAME, config)

    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
    note = next(n for n in r.data["notes"] if "secret-shaped" in n)

    assert note.startswith("1 setting key(s) were withheld")
    assert "1 at the config file's top level" in note
    assert "inside the `preferences` block" not in note


async def test_a_preference_secret_alone_is_not_reported_as_top_level(
    desktop_settings_dir,
):
    """...and the mirror image, so the note is reading its two counters
    rather than always printing both halves."""
    desktop_root = desktop_settings_dir()
    config = json.loads(json.dumps(DESKTOP_CONFIG_FIXTURE))
    config.pop("globalShortcutToken")
    _write_json(desktop_root / local_data.DESKTOP_CONFIG_FILENAME, config)

    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
    note = next(n for n in r.data["notes"] if "secret-shaped" in n)

    assert note.startswith("1 setting key(s) were withheld")
    assert "1 inside the `preferences` block" in note
    assert "at the config file's top level" not in note


async def test_get_desktop_settings_lists_installed_extensions(desktop_settings_fixture):
    """`extensions-installations.json` is the only source for what is
    *installed*; per-session `enabled_mcp_server_labels` only shows what a
    session switched on."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        extensions = r.data["extensions"]
        assert extensions["available"] is True
        assert extensions["count"] == 2

        chrome, helper = extensions["installed"]
        assert chrome["id"] == "ant.chrome-control"
        assert chrome["version"] == "0.1.6"
        assert chrome["source"] == "registry"
        assert chrome["signature_status"] == "unsigned"
        assert chrome["installed_at_iso"].startswith("2026-06-29")
        # The epoch companion is normalized even when the file wrote ISO.
        assert chrome["installed_at_ms"] == int(
            datetime(2026, 6, 29, 12, tzinfo=UTC).timestamp() * 1000
        )
        assert helper["signature_status"] == "valid"
        assert helper["installed_at_iso"].startswith("2026-05")
        assert any("INSTALLED" in n for n in r.data["notes"])


async def test_get_desktop_settings_measures_but_never_returns_the_manifest(
    desktop_settings_fixture,
):
    """A manifest is a large nested blob that produces no finding its size
    can't. It is measured and dropped."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        chrome = r.data["extensions"]["installed"][0]
        assert chrome["manifest_size_chars"] > 100
        assert chrome["manifest_top_level_field_count"] == 2
        assert "manifest" not in chrome
        assert MANIFEST_MARKER not in json.dumps(r.data)


async def test_get_desktop_settings_never_leaks_the_oauth_token_cache(
    desktop_settings_fixture,
):
    """The single most important assertion here. `config.json` holds live
    OAuth token caches next to the three harmless keys this plugin wants, so
    the whole response is serialized and checked for every planted secret --
    not just the fields anyone remembered to look at."""
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        serialized = json.dumps(r.data)
        for secret in (
            OAUTH_TOKEN_MARKER,
            OAUTH_TOKEN_V2_MARKER,
            NON_ALLOWLISTED_MARKER,
            PREFERENCE_SECRET_MARKER,
            # Surfacing unknown top-level keys (D6) must not become a way
            # for an MCP server block's env to walk out.
            MCP_SERVER_ENV_MARKER,
            TOP_LEVEL_SECRET_MARKER,
        ):
            assert secret not in serialized
        # Not even the key names -- their presence would mean the file was
        # loaded wholesale and filtered afterwards.
        assert "tokenCache" not in serialized
        assert "oauth" not in serialized.lower()

        # ...and the three allowlisted keys really did come through, so this
        # test can't pass by reading nothing at all.
        app_config = r.data["app_config"]
        assert app_config["available"] is True
        assert app_config["locale"] == "en-US"
        assert app_config["user_theme_mode"] == "system"
        assert app_config["first_launch_at_iso"].startswith("2025-06-15")
        assert app_config["account_tenure_days"] > 0


class TestAppConfigAllowlist:
    """The allowlist is the real guarantee behind the test above, and it is
    directly testable on its own."""

    def test_returns_only_allowlisted_keys(self, tmp_path):
        path = tmp_path / local_data.APP_CONFIG_FILENAME
        _write_json(path, APP_CONFIG_FIXTURE)
        allowlisted = local_data._read_app_config_allowlisted(path)
        assert set(allowlisted) == set(local_data._APP_CONFIG_ALLOWLIST)
        assert OAUTH_TOKEN_MARKER not in json.dumps(allowlisted)

    def test_a_secret_shaped_key_would_not_survive_even_if_allowlisted(self, tmp_path):
        """Two independent gates, because one typo in the allowlist would
        otherwise be enough to leak a token."""
        path = tmp_path / local_data.APP_CONFIG_FILENAME
        _write_json(path, {"authToken": OAUTH_TOKEN_MARKER, "locale": "en-US"})
        monkey_allowlist = ("authToken", "locale")
        original = local_data._APP_CONFIG_ALLOWLIST
        try:
            local_data._APP_CONFIG_ALLOWLIST = monkey_allowlist
            allowlisted = local_data._read_app_config_allowlisted(path)
        finally:
            local_data._APP_CONFIG_ALLOWLIST = original
        assert allowlisted == {"locale": "en-US"}

    def test_missing_or_malformed_file_returns_empty(self, tmp_path):
        assert local_data._read_app_config_allowlisted(tmp_path / "absent.json") == {}
        broken = tmp_path / "broken.json"
        broken.write_text("{not json", encoding="utf-8")
        assert local_data._read_app_config_allowlisted(broken) == {}


async def test_get_desktop_settings_reports_the_configured_output_folder(
    tmp_path, desktop_settings_fixture
):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        user_files = r.data["user_files_path"]
        assert user_files["path"] == str(tmp_path / CONFIGURED_USER_FILES_DIRNAME)
        assert user_files["source"] == local_data.DESKTOP_CONFIG_FILENAME
        assert user_files["setting"] == "coworkUserFilesPath"


async def test_get_desktop_settings_without_desktop_config(desktop_settings_dir):
    """The other two files still report, and the missing one says so rather
    than leaving a caller to infer "no standing grants"."""
    desktop_settings_dir(omit=("desktop_config",))
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        assert r.data["available"] is True
        assert r.data["settings"] == {}
        assert r.data["unrecognized_settings"] == {}
        assert r.data["access_scope"]["trusted_folders"]["count"] == 0
        assert r.data["user_files_path"]["path"] is None
        assert r.data["extensions"]["count"] == 2
        assert r.data["app_config"]["available"] is True
        assert any("claude_desktop_config.json is missing" in n for n in r.data["notes"])


async def test_get_desktop_settings_without_extensions_file(desktop_settings_dir):
    desktop_settings_dir(omit=("extensions",))
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        assert r.data["available"] is True
        assert r.data["extensions"]["available"] is False
        assert r.data["extensions"]["installed"] == []
        assert r.data["settings"]["allowAllBrowserActions"] is True
        assert any(
            "No extensions-installations.json" in n for n in r.data["notes"]
        )


async def test_get_desktop_settings_without_app_config(desktop_settings_dir):
    desktop_settings_dir(omit=("app_config",))
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        assert r.data["available"] is True
        assert r.data["app_config"]["available"] is False
        assert r.data["extensions"]["count"] == 2
        assert any("config.json is missing" in n for n in r.data["notes"])


async def test_get_desktop_settings_malformed_files_degrade(desktop_settings_dir):
    desktop_root = desktop_settings_dir()
    for filename in (
        local_data.DESKTOP_CONFIG_FILENAME,
        local_data.EXTENSIONS_FILENAME,
        local_data.APP_CONFIG_FILENAME,
    ):
        (desktop_root / filename).write_text('{"extensions": ', encoding="utf-8")
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        assert r.data["available"] is True
        assert r.data["settings"] == {}
        assert r.data["extensions"]["available"] is False
        assert r.data["app_config"]["available"] is False


async def test_get_desktop_settings_wrong_shaped_extensions_block_degrades(
    desktop_settings_dir,
):
    desktop_root = desktop_settings_dir()
    _write_json(desktop_root / local_data.EXTENSIONS_FILENAME, {"extensions": "nope"})
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        assert r.data["extensions"]["available"] is False
        assert r.data["extensions"]["count"] == 0


async def test_get_desktop_settings_without_desktop_dir(no_data_paths):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("get_desktop_settings", {})
        assert r.data["available"] is False
        assert r.data["settings"] == {}
        assert r.data["extensions"]["available"] is False
        assert any("check_data_access" in n for n in r.data["notes"])


async def test_get_desktop_settings_takes_no_time_window():
    """These are current-state settings with no history of when a toggle was
    flipped, so a window would imply a precision the data doesn't have."""
    async with Client(srv.mcp) as client:
        tools = {t.name: t for t in await client.list_tools()}
        properties = tools["get_desktop_settings"].input_schema.get("properties", {})
        assert "since" not in properties
        assert "until" not in properties


# --------------------------------------------------------------------------------
# check_data_access -- configured output folder, and the account-level files
# --------------------------------------------------------------------------------


async def test_check_data_access_prefers_the_configured_output_folder(
    tmp_path, monkeypatch
):
    """Claude's own `coworkUserFilesPath` beats the hardcoded candidate list,
    and the response says which was used -- "Claude is configured to write
    here" and "it's probably here" are different things to tell a user who
    is looking for a missing scheduled-task result."""
    desktop = tmp_path / "Claude"
    desktop.mkdir()
    configured = tmp_path / "ConfiguredOutput"
    configured.mkdir()
    guessed = tmp_path / "GuessedOutput"
    guessed.mkdir()
    _write_json(
        desktop / local_data.DESKTOP_CONFIG_FILENAME,
        {"coworkUserFilesPath": str(configured), "preferences": {}},
    )
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [guessed])

    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        assert r.data["output_dir"] == str(configured)
        assert r.data["output_dir_source"] == "claude_desktop_config.json"
        # The configured path is probed first, and the guess stays behind it
        # as a fallback rather than being discarded.
        assert r.data["output_candidates"][0]["path"] == str(configured)
        assert str(guessed) in [c["path"] for c in r.data["output_candidates"]]
        assert r.data["warnings"] == []


async def test_check_data_access_falls_back_when_no_configured_path(tmp_path, monkeypatch):
    desktop = tmp_path / "Claude"
    desktop.mkdir()
    guessed = tmp_path / "GuessedOutput"
    guessed.mkdir()
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [guessed])

    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        assert r.data["output_dir"] == str(guessed)
        assert r.data["output_dir_source"] == "candidate_probe"
        assert r.data["warnings"] == []


async def test_check_data_access_says_a_configured_folder_is_unreachable(
    tmp_path, monkeypatch
):
    """A configured path this session can't reach is a file-access problem,
    not evidence the folder doesn't exist -- and the warning has to say so."""
    desktop = tmp_path / "Claude"
    desktop.mkdir()
    unreachable = tmp_path / "Unreachable"
    _write_json(
        desktop / local_data.DESKTOP_CONFIG_FILENAME,
        {"coworkUserFilesPath": str(unreachable)},
    )
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])

    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        assert r.data["output_dir"] == str(unreachable)
        assert r.data["output_dir_source"] == "claude_desktop_config.json"
        warning = next(w for w in r.data["warnings"] if "output folder isn't visible" in w)
        assert "not a guess" in warning


async def test_check_data_access_reports_which_account_files_are_present(
    desktop_settings_fixture,
):
    """A caller learns here that (say) plan usage history is unavailable,
    instead of finding out from an empty `get_plan_usage_history` later."""
    _write_json(
        desktop_settings_fixture / local_data.PLAN_USAGE_HISTORY_FILENAME,
        {"version": 2, "samples": USAGE_SAMPLES},
    )
    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        data_files = r.data["data_files"]
        assert data_files["plan_usage_history_available"] is True
        assert data_files["desktop_settings_available"] is True
        assert all(f["exists"] for f in data_files["files"].values())
        assert data_files["notes"] == []


async def test_check_data_access_reports_missing_account_files(desktop_settings_dir):
    """The settings files are there but the usage history is not -- one lens
    can run this session and the other can't."""
    desktop_settings_dir()
    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        data_files = r.data["data_files"]
        assert data_files["plan_usage_history_available"] is False
        assert data_files["desktop_settings_available"] is True
        assert data_files["files"]["plan_usage_history"]["exists"] is False
        assert data_files["files"]["desktop_config"]["exists"] is True
        assert any("not evidence the account has low usage" in n for n in data_files["notes"])
        # Presence caveats belong in `data_files.notes`, not in the
        # top-level warnings -- a missing usage file is not an access fault.
        assert not any("plan usage history" in w for w in r.data["warnings"])


async def test_check_data_access_reports_no_account_files_at_all(no_data_paths):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("check_data_access", {})
        data_files = r.data["data_files"]
        assert data_files["plan_usage_history_available"] is False
        assert data_files["desktop_settings_available"] is False
        assert all(f["exists"] is False for f in data_files["files"].values())
        assert data_files["notes"]


# --------------------------------------------------------------------------------
# SKILL.md frontmatter budget -- the field that decides whether the skill runs
# --------------------------------------------------------------------------------
#
# A skill's frontmatter `description` + `when_to_use` is what the model
# matches a user's phrasing against. It is capped, and going over it is
# silent: the text is truncated with no error, no warning, and a SKILL.md
# on disk that still reads perfectly.
#
# That is not hypothetical here. Through 0.5.x this plugin shipped a
# 2,193-character `description` against the 1,536 cap. The trigger list
# lived at the end of the field, so the truncation cut it: two of fourteen
# trigger phrases survived, and the cut landed mid-word inside the third
# ("audit my Claude usag"). The skill under-activated for four releases and
# read to users as "the plugin doesn't do that" rather than as a bug.
# Forty-one tests were added in the release that fixed it and not one
# pinned the budget.

_SKILL_FRONTMATTER_CHAR_BUDGET = 1536
# The fields that make up the budget, in the order they are reported.
_SKILL_BUDGETED_FIELDS = ("description", "when_to_use")
_FRONTMATTER_FENCE = "---"
_FRONTMATTER_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


def _parse_frontmatter(text: str) -> dict[str, str]:
    """The YAML frontmatter of a SKILL.md, as `{key: value}`.

    Deliberately stdlib-only and deliberately small. This test must not be
    the thing that adds a dependency to a plugin whose whole install story
    is a locked `uv run --locked`, and it must not be skippable: a budget
    check that quietly disappears when an optional import is missing is the
    same failure mode as the truncation it exists to catch.

    Handles the two forms this frontmatter uses -- a plain `key: value` and
    a folded block scalar (`key: >-`), whose continuation lines YAML joins
    with single spaces. `test_frontmatter_parser_agrees_with_pyyaml` checks
    that reading against a real YAML parser wherever one is installed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        raise AssertionError("SKILL.md does not start with a '---' frontmatter fence")
    try:
        end = next(
            i for i in range(1, len(lines)) if lines[i].strip() == _FRONTMATTER_FENCE
        )
    except StopIteration:
        raise AssertionError("SKILL.md frontmatter is never closed by a '---'") from None

    fields: dict[str, str] = {}
    index = 1
    while index < end:
        match = _FRONTMATTER_KEY_RE.match(lines[index])
        index += 1
        if not match:
            continue
        key, inline = match.group(1), match.group(2).strip()
        if inline.startswith((">", "|")):
            folded = inline.startswith(">")
            block: list[str] = []
            while index < end and (not lines[index].strip() or lines[index][:1].isspace()):
                block.append(lines[index].strip())
                index += 1
            if not folded:
                fields[key] = "\n".join(block).strip()
                continue
            # Folded: consecutive non-empty lines join with a single space,
            # a blank line folds to a newline. That is what decides the
            # character count, so it has to match YAML rather than merely
            # look close.
            out = ""
            for chunk in block:
                if not chunk:
                    out += "\n"
                elif out and not out.endswith("\n"):
                    out += " " + chunk
                else:
                    out += chunk
            fields[key] = out.strip()
        else:
            fields[key] = inline.strip("\"'")
    return fields


def _skill_files() -> list[Path]:
    skills_dir = Path(__file__).resolve().parent.parent / "skills"
    found = sorted(skills_dir.glob("*/SKILL.md"))
    assert found, f"no SKILL.md found under {skills_dir}"
    return found


@pytest.mark.parametrize("skill_path", _skill_files(), ids=lambda p: p.parent.name)
def test_skill_frontmatter_stays_within_the_activation_char_budget(skill_path):
    """The single most important test in this file.

    `description` + `when_to_use` is the text the model matches a user's
    phrasing against, and anything past the cap is cut silently. Ordinary
    editing breaches it -- the fix for the last breach left 104 characters
    of headroom, and routine edits since have already spent some of it."""
    frontmatter = _parse_frontmatter(skill_path.read_text(encoding="utf-8"))
    missing = [f for f in _SKILL_BUDGETED_FIELDS if f not in frontmatter]
    assert not missing, (
        f"{skill_path.parent.name}/SKILL.md frontmatter is missing {missing}. "
        "This test cannot pass by finding nothing to measure -- if a field was "
        "renamed, update _SKILL_BUDGETED_FIELDS to match."
    )

    sizes = {field: len(frontmatter[field]) for field in _SKILL_BUDGETED_FIELDS}
    total = sum(sizes.values())
    assert total <= _SKILL_FRONTMATTER_CHAR_BUDGET, (
        f"{skill_path.parent.name}/SKILL.md frontmatter is {total} characters "
        f"({' + '.join(f'{k} {v}' for k, v in sizes.items())}), over the "
        f"{_SKILL_FRONTMATTER_CHAR_BUDGET}-character cap by "
        f"{total - _SKILL_FRONTMATTER_CHAR_BUDGET}.\n"
        "Everything past the cap is SILENTLY TRUNCATED -- no error, no warning, "
        "and the file on disk still reads correctly. The trigger phrases live at "
        "the end of these fields, so what gets cut is exactly what makes the "
        "skill activate: it stops firing on the phrasings past the cut and reads "
        "to users as 'the plugin doesn't do that'. This shipped once already and "
        "went unnoticed for four releases. Cut words, don't raise the cap."
    )


def test_frontmatter_parser_agrees_with_pyyaml():
    """Guard the guard.

    The budget above is only as good as the character counts behind it, and
    those come from a hand-rolled parser. Wherever a real YAML parser
    happens to be installed, check the two agree exactly -- if they ever
    diverge, the budget is being measured against the wrong string. Skipped
    when PyYAML isn't present; the budget test itself never is."""
    yaml = pytest.importorskip("yaml")
    for skill_path in _skill_files():
        text = skill_path.read_text(encoding="utf-8")
        body = text.split(f"{_FRONTMATTER_FENCE}\n", 1)[1].split(
            f"\n{_FRONTMATTER_FENCE}", 1
        )[0]
        reference = yaml.safe_load(body)
        parsed = _parse_frontmatter(text)
        for field in _SKILL_BUDGETED_FIELDS:
            assert parsed[field] == reference[field], (
                f"frontmatter parser disagrees with PyYAML on {field} "
                f"in {skill_path.parent.name}/SKILL.md"
            )


# The Claude Code CLI keeps its own session store under
# `claude-code-sessions/`, and -- since 0.6.0 -- an identically named
# `scheduled-tasks.json` inside it. That is the shape a reader would take to
# get there, and it is a shape no identifier ban can see: `base_dir.glob(
# "claude-code-sessions/*/*/scheduled-tasks.json")` mentions neither
# `cli_dir(` nor `read_cli_sessions` nor `Path.home()`.
#
# So the guard reads string *constants* out of the AST rather than matching
# source text. Two consequences, both wanted:
#
#   - Prose is free. Both modules deliberately explain the exclusion in their
#     docstrings and comments, naming the directory they don't read. Comments
#     never reach the AST and docstrings are skipped explicitly, so explaining
#     the boundary can never trip the guard that enforces it.
#   - Indirection doesn't help. A constant assigned to a name, interpolated
#     into an f-string, or tucked inside a tuple is still a string constant in
#     the tree, and is still caught.

_CLI_SESSION_DIR = "claude-code-sessions"
# Every path this package reads is anchored here. That is the actual
# invariant -- narrower and more durable than a list of forbidden spellings.
_ANCHORED_PREFIX = "local-agent-mode-sessions/"
# Substrings that mean a string is being used as a path or a glob. `"/*"`/
# `"*/"` rather than a bare `"*"`: `_CUSTOM_INSTRUCTIONS_TAG_RE`'s pattern
# `<user_preferences>(.*?)</user_preferences>` contains both `*` and `/` and
# is not a path.
_PATHLIKE_MARKERS = (".claude", "/*", "*/")


def _non_docstring_string_constants(source: str) -> list[tuple[int, str]]:
    """Every string literal in `source` that is not a docstring, as
    `(lineno, value)`. f-string pieces count: they are how a forbidden path
    would be assembled without any one literal spelling it out."""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _cli_surface_violations(source: str) -> list[str]:
    """Which string constants in `source` reach outside this plugin's scope."""
    violations: list[str] = []
    for lineno, text in _non_docstring_string_constants(source):
        if _CLI_SESSION_DIR in text:
            violations.append(f"line {lineno}: CLI-DIR {text!r}")
        if any(marker in text for marker in _PATHLIKE_MARKERS) and not text.startswith(
            _ANCHORED_PREFIX
        ):
            violations.append(f"line {lineno}: UNANCHORED {text!r}")
    return violations


def test_no_cli_surface_remains():
    """Claude Code CLI data is deliberately out of scope (see local_data.py's
    module docstring). Guard against a reader creeping back in.

    Two rules over the package's string constants:

    1. None may name `claude-code-sessions` -- there is no in-scope use of
       that directory, ever, and since 0.6.0 it holds a `scheduled-tasks.json`
       identical in name to one this plugin does read.
    2. Any constant that looks like a path or a glob (`.claude`, `/*`, `*/`)
       must be anchored at `local-agent-mode-sessions/`, which is where every
       path this package reads begins. That includes `_COWORK_TRANSCRIPT_GLOB`,
       whose nested `.claude/projects/` segment is Cowork's own transcript
       directory and is legitimately in scope.

    ...plus the two CLI reader symbols, which are function names no string
    rule would see. Prose mentioning the CLI is fine -- explaining the
    exclusion is the point -- which is why this reads the AST rather than
    the source text."""
    package_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "shadetree_ai_plugins_claude_usage_analyzer"
    )
    banned_symbols = re.compile(r"cli_dir\(|read_cli_sessions")
    checked = 0
    for py_file in package_dir.glob("*.py"):
        source = py_file.read_text()
        checked += 1
        assert not banned_symbols.search(source), (
            f"{py_file.name} reintroduces a Claude Code CLI data reader"
        )
        assert _cli_surface_violations(source) == [], (
            f"{py_file.name} reaches outside this plugin's data scope: "
            f"{_cli_surface_violations(source)}"
        )
    assert checked, "the guard found no package sources to check"


def test_cli_surface_guard_catches_the_shapes_it_has_to():
    """Guard the guard. The version this replaced banned three identifiers,
    and a `claude-code-sessions/...` glob tripped none of them -- so the rules
    are checked against the shapes they exist to catch, and against the
    legitimate constructs they must leave alone."""
    caught = (
        # The plain form, and the same thing hidden three ways.
        'base_dir.glob("claude-code-sessions/*/*/scheduled-tasks.json")',
        '_CLI = "claude-code-sessions/*/*/*.jsonl"\nd.glob(_CLI)',
        'd.glob(f"claude-code-sessions/*/*/{NAME}")',
        '_MEMORY_SOURCES = (("cli", "claude-code-sessions/*/*/memory/**/*.md"),)',
        # The `Path.home() / ".claude"` shape, split across two statements --
        # which the old source-text regex walked straight past.
        'home = Path.home()\np = home / ".claude" / "projects"',
    )
    for source in caught:
        assert _cli_surface_violations(source), f"guard no longer catches: {source!r}"

    allowed = (
        # Cowork's own nested transcript: `.claude/` in the path, correctly
        # anchored, and squarely in scope.
        '_G = "local-agent-mode-sessions/*/*/local_*/.claude/projects/*/*.jsonl"',
        # A regex that merely contains `*` and `/` and is not a path at all.
        're.compile(r"<user_preferences>(.*?)</user_preferences>")',
        # Prose naming the excluded directory -- as a docstring and as a
        # comment. Explaining the boundary must never trip the boundary.
        '"""Deliberately excludes claude-code-sessions/ -- out of scope."""',
        '# claude-code-sessions/ is the CLI store and is never read here\nx = 1',
    )
    for source in allowed:
        assert _cli_surface_violations(source) == [], f"guard wrongly flags: {source!r}"

def test_server_has_no_network_calls():
    # `urllib.parse` is deliberately exempt: it is a pure string parser with
    # no capacity to open a connection, and `local_data` uses it to reduce a
    # URL to its host. Everything else under `urllib` -- `.request`,
    # `.error`, and bare `import urllib` -- stays banned, as do urllib2/3.
    package_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "shadetree_ai_plugins_claude_usage_analyzer"
    )
    banned_imports = re.compile(
        r"^\s*(import|from)\s+"
        r"(requests|socket|urllib\d*(?!\.parse\b)|http\.client|httplib)\b",
        re.MULTILINE,
    )
    for py_file in package_dir.glob("*.py"):
        source = py_file.read_text()
        assert not banned_imports.search(source), (
            f"found a banned network-related import in {py_file}"
        )


def test_network_guard_still_bans_real_network_modules():
    """The exemption above is narrow on purpose -- guard the guard."""
    banned_imports = re.compile(
        r"^\s*(import|from)\s+"
        r"(requests|socket|urllib\d*(?!\.parse\b)|http\.client|httplib)\b",
        re.MULTILINE,
    )
    for forbidden in (
        "import urllib",
        "from urllib.request import urlopen",
        "import urllib.error",
        "import urllib3",
        "import requests",
        "import socket",
        "from http.client import HTTPConnection",
    ):
        assert banned_imports.search(forbidden), f"guard no longer bans: {forbidden}"

    for allowed in ("from urllib.parse import urlparse", "import json"):
        assert not banned_imports.search(allowed), f"guard wrongly bans: {allowed}"
