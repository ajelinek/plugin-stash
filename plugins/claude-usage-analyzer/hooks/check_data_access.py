#!/usr/bin/env python3
"""SessionStart hook: warn if this session can't see any local Claude data.

Plain stdlib only, same reasoning as check_uv.py: invoked directly with a
bare system Python before this plugin's own deps are installed, so it can't
import fastmcp/shadetree_ai_plugins_claude_usage_analyzer. It deliberately
duplicates (a subset of) the path-candidate logic that
shadetree_ai_plugins_claude_usage_analyzer.local_data computes for real once
the MCP server is up -- this hook only ever does existence checks, never
reads file contents.

Why this hook exists at all: this plugin's tools read Claude Desktop's own
app-data directory (and the user-visible Claude output folder). Installed as
a Desktop/Cowork plugin, the session may be running inside a folder-scoped
Space/Project that never attached those directories -- in which case every
path below reads as simply missing, not "permission denied", and the
plugin's tools will look like they found nothing rather than erroring
loudly. This hook exists to catch that silently-empty case at session start
and say so explicitly.

Claude Code's `~/.claude` is deliberately not checked here: this plugin
does not read CLI data at all (see local_data.py's module docstring).
"""

from __future__ import annotations

import json
import os
import platform
import sys
from pathlib import Path


def candidate_paths() -> dict[str, list[str]]:
    home = Path.home()
    system = platform.system()

    desktop: list[str] = []
    if system == "Darwin":
        desktop = [
            str(home / "Library/Application Support/Claude"),
            str(home / "Library/Application Support/Claude-3p"),
        ]
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", str(home / "AppData/Roaming"))
        localappdata = os.environ.get("LOCALAPPDATA", str(home / "AppData/Local"))
        desktop = [str(Path(appdata) / "Claude"), str(Path(appdata) / "Claude-3p")]
        packages_dir = Path(localappdata) / "Packages"
        if packages_dir.is_dir():
            for entry in packages_dir.iterdir():
                if entry.name.startswith("Claude_"):
                    desktop.append(str(entry / "LocalCache/Roaming/Claude"))
    else:
        # Desktop is not officially supported on Linux; check the XDG-conventional
        # spot defensively rather than assuming it can never exist.
        desktop = [str(home / ".config/Claude")]

    output_folders = [str(home / "Claude"), str(home / "Documents/Claude")]

    return {"desktop": desktop, "output": output_folders}


def main() -> None:
    paths = candidate_paths()
    all_paths = [*paths["desktop"], *paths["output"]]
    visible = [p for p in all_paths if os.path.exists(p)]

    if visible:
        return  # at least one known location is visible -- nothing to warn about

    desktop_list = "\n".join(f"  - {p}" for p in paths["desktop"])
    output_list = "\n".join(f"  - {p}" for p in paths["output"])
    message = (
        "claude-usage-analyzer can't see any of the local Claude data locations it "
        "expects on this machine:\n\n"
        f"Claude Desktop app data:\n{desktop_list}\n\n"
        f"Claude output folder:\n{output_list}\n\n"
        "If Claude Desktop is actually installed and used on this machine, this "
        "almost always means this session is running inside a folder-scoped "
        "Cowork Space or Project that hasn't been given access to those folders "
        "yet -- a plugin's MCP server only sees what the session itself can see, "
        "it doesn't get broader filesystem access on its own. Add the relevant "
        "folder(s) above to this Space/Project's file access scope (and, "
        "separately, wherever you plan to save/unzip a claude.ai account data "
        "export -- Settings > Account > Export Data), then restart the session."
    )
    print(json.dumps({"systemMessage": message}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Best-effort only -- never let this hook itself surface as a session error.
        sys.exit(0)
