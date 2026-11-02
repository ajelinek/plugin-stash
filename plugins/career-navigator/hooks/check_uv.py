#!/usr/bin/env python3
"""SessionStart hook: warn (never silently auto-install) if `uv` is missing.

Plain stdlib only, deliberately. This runs before any plugin's own
dependencies are installed (that's the whole point — it exists to catch
the case where `uv run` in .mcp.json is about to fail), so it can't
import fastmcp, shadetree_ai_plugins_common, or anything else non-stdlib. It's
invoked directly with a bare system Python interpreter (see hooks.json),
not through `uv run`.
"""

from __future__ import annotations

import json
import platform
import shutil
import sys


def install_command() -> str:
    if platform.system() == "Windows":
        return 'powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"'
    return "curl -LsSf https://astral.sh/uv/install.sh | sh"


def main() -> None:
    if shutil.which("uv") is not None:
        return  # present — exit 0 with no output, nothing to report

    message = (
        "This shadetree-ai-plugins plugin needs `uv` installed to run, and it wasn't found "
        f"on PATH. Install it with:\n\n  {install_command()}\n\n"
        "Then restart Claude Desktop (or start a new session) for this plugin's "
        "tools to work. See "
        "https://docs.astral.sh/uv/getting-started/installation/ for other "
        "install options."
    )
    print(json.dumps({"systemMessage": message}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Best-effort only: never let this hook itself surface as a session
        # error. Worst case, the plugin's own `uv run` command fails later
        # with its own native error, same as before this hook existed.
        sys.exit(0)
