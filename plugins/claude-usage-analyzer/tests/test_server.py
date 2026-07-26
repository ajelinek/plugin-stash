"""Tests for the shadetree-ai-plugins-claude-usage-analyzer FastMCP server,
all against synthetic fixture directories -- no real Claude Desktop/CLI
install required.
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest
from fastmcp import Client
from shadetree_ai_plugins_claude_usage_analyzer import export_data, local_data
from shadetree_ai_plugins_claude_usage_analyzer import server as srv


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
# usage_doctor
# --------------------------------------------------------------------------------


@pytest.fixture
def no_data_paths(tmp_path, monkeypatch):
    missing_cli = tmp_path / "nowhere" / ".claude"
    missing_desktop = tmp_path / "nowhere" / "Claude"
    missing_output = tmp_path / "nowhere" / "Output"
    monkeypatch.setattr(local_data, "cli_dir", lambda: missing_cli)
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [missing_desktop])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [missing_output])
    return tmp_path


async def test_usage_doctor_warns_when_nothing_visible(no_data_paths):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("usage_doctor", {})
        assert r.data["ok"] is False
        assert any("No known Claude data location" in w for w in r.data["warnings"])


async def test_usage_doctor_warns_cli_only_no_desktop(tmp_path, monkeypatch):
    cli = tmp_path / ".claude"
    cli.mkdir()
    monkeypatch.setattr(local_data, "cli_dir", lambda: cli)
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [tmp_path / "NoDesktop"])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])
    async with Client(srv.mcp) as client:
        r = await client.call_tool("usage_doctor", {})
        assert r.data["ok"] is True
        assert any("no Claude Desktop app-data directory" in w for w in r.data["warnings"])


async def test_usage_doctor_no_warnings_when_both_present(tmp_path, monkeypatch):
    cli = tmp_path / ".claude"
    cli.mkdir()
    desktop = tmp_path / "Claude"
    desktop.mkdir()
    monkeypatch.setattr(local_data, "cli_dir", lambda: cli)
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])
    async with Client(srv.mcp) as client:
        r = await client.call_tool("usage_doctor", {})
        assert r.data["ok"] is True
        assert r.data["warnings"] == []


# --------------------------------------------------------------------------------
# list_local_workspace -- full fixture tree (CLI + Desktop)
# --------------------------------------------------------------------------------


@pytest.fixture
def workspace_fixture(tmp_path, monkeypatch):
    cli_root = tmp_path / "cli" / ".claude"
    desktop_root = tmp_path / "desktop" / "Claude"

    # CLI session
    project_dir = cli_root / "projects" / "-Users-x-proj1"
    session_uuid = "11111111-1111-1111-1111-111111111111"
    jsonl_path = project_dir / f"{session_uuid}.jsonl"
    jsonl_path.parent.mkdir(parents=True)
    with jsonl_path.open("w", encoding="utf-8") as f:
        f.write(json.dumps({
            "cwd": "/Users/x/proj1", "timestamp": "2026-01-01T00:00:00Z",
            "entrypoint": "cli", "gitBranch": "main", "type": "user",
        }) + "\n")
        f.write(json.dumps({"type": "assistant"}) + "\n")
        f.write(json.dumps({"type": "user"}) + "\n")
    _write_json(project_dir / "bridge-pointer.json", {"sessionId": session_uuid})
    (cli_root / "tasks" / session_uuid).mkdir(parents=True)
    (cli_root / "tasks" / session_uuid / "1.json").write_text("{}")
    (cli_root / "tasks" / session_uuid / "2.json").write_text("{}")

    # Desktop: spaces.json, project-cache, and three local_*.json sessions
    org_dir = desktop_root / "local-agent-mode-sessions" / "ACCOUNT" / "ORG"
    _write_json(org_dir / "spaces.json", [
        {
            "id": "space1", "name": "Space One", "description": "d", "instructions": "i",
            "folders": [{"path": "/Users/x/proj1"}, "/Users/x/proj2"],
        }
    ])
    _write_json(org_dir / ".project-cache" / "PROJ-UUID" / "metadata.json", {
        "uuid": "PROJ-UUID", "name": "Cloud Proj", "description": "cd", "synced_at": "2026-01-01",
    })
    _write_json(org_dir / "local_sess1.json", {
        "title": "Chat in Space", "createdAt": "2026-01-01T00:00:00Z",
        "userSelectedFolders": ["/Users/x/proj1/sub"], "userSelectedProjectUuids": [],
        "enabledMcpTools": {"huge": "blob"}, "authToken": "shhh",
    })
    _write_json(org_dir / "local_sess2.json", {
        "title": "Chat in Cloud Project", "createdAt": "2026-01-02T00:00:00Z",
        "userSelectedFolders": [], "userSelectedProjectUuids": ["PROJ-UUID"],
    })
    _write_json(org_dir / "local_sess3.json", {
        "title": "Unfiled chat", "createdAt": "2026-01-03T00:00:00Z",
        "userSelectedFolders": ["/Users/x/other"], "userSelectedProjectUuids": [],
    })
    # Code-tab session -- must NOT be picked up by read_local_sessions
    _write_json(desktop_root / "claude-code-sessions" / "ACCOUNT" / "ORG" / "local_codetab.json", {
        "title": "should be excluded",
    })

    monkeypatch.setattr(local_data, "cli_dir", lambda: cli_root)
    monkeypatch.setattr(local_data, "desktop_dir_candidates", lambda: [desktop_root])
    monkeypatch.setattr(local_data, "output_dir_candidates", lambda: [tmp_path / "NoOutput"])
    return {"cli_root": cli_root, "desktop_root": desktop_root, "session_uuid": session_uuid}


async def test_list_local_workspace_cli_sessions(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        cli_sessions = r.data["cli_sessions"]
        assert len(cli_sessions) == 1
        s = cli_sessions[0]
        assert s["cwd"] == "/Users/x/proj1"
        assert s["entrypoint"] == "cli"
        assert s["git_branch"] == "main"
        assert s["event_count"] == 3
        assert s["bridged_to_desktop"] is True
        assert s["task_item_count"] == 2


async def test_list_local_workspace_excludes_code_tab_sessions(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        titles = [s["title"] for s in r.data["local_sessions"]]
        assert "should be excluded" not in titles
        assert len(r.data["local_sessions"]) == 3


async def test_list_local_workspace_membership_join(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        membership = r.data["membership"]
        assert membership["by_space_id"]["space1"] == ["local_sess1"]
        assert membership["by_project_uuid"]["PROJ-UUID"] == ["local_sess2"]
        assert membership["unfiled_session_ids"] == ["local_sess3"]


async def test_list_local_workspace_spaces_and_project_cache(workspace_fixture):
    async with Client(srv.mcp) as client:
        r = await client.call_tool("list_local_workspace", {})
        assert r.data["spaces"][0]["name"] == "Space One"
        assert r.data["spaces"][0]["folders"] == ["/Users/x/proj1", "/Users/x/proj2"]
        assert r.data["project_cache"][0]["uuid"] == "PROJ-UUID"


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
    _write_json(export_dir / "memories.json", {
        "memory": "Global narrative about the user.",
        "project_memories": [
            {
                "project_uuid": "p1", "project_name": "Japan Trip",
                "memory": "User is planning a Japan trip.",
            }
        ],
    })
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
        assert "Japan Trip" in memory_md


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


# --------------------------------------------------------------------------------
# No-network guard
# --------------------------------------------------------------------------------


def test_server_has_no_network_calls():
    package_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "shadetree_ai_plugins_claude_usage_analyzer"
    )
    banned_imports = re.compile(
        r"^\s*(import|from)\s+(requests|socket|urllib\d*|http\.client|httplib)\b", re.MULTILINE
    )
    for py_file in package_dir.glob("*.py"):
        source = py_file.read_text()
        assert not banned_imports.search(source), (
            f"found a banned network-related import in {py_file}"
        )
