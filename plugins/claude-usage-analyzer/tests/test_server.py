"""Tests for the shadetree-ai-plugins-claude-usage-analyzer FastMCP server,
all against synthetic fixture directories -- no real Claude Desktop install
required.

Claude Code CLI data is out of scope for this plugin (see local_data.py's
module docstring), so there are no CLI fixtures here and
`test_no_cli_surface_remains` guards against one creeping back in.
"""

from __future__ import annotations

import json
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


def test_no_cli_surface_remains():
    """Claude Code CLI data is deliberately out of scope (see local_data.py's
    module docstring). Guard against a reader creeping back in: no code here
    should reference `~/.claude` or the CLI's `projects/<encoded-cwd>` store.
    Prose mentioning the CLI is fine -- explaining the exclusion is the
    point -- so this only checks for the paths/symbols that would do the
    reading."""
    package_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "shadetree_ai_plugins_claude_usage_analyzer"
    )
    banned = re.compile(r"""cli_dir\(|read_cli_sessions|Path\.home\(\)\s*/\s*["']\.claude["']""")
    for py_file in package_dir.glob("*.py"):
        assert not banned.search(py_file.read_text()), (
            f"{py_file.name} reintroduces a Claude Code CLI data reader"
        )


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
