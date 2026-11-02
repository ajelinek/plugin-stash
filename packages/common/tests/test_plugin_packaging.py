"""Exercise marketplace plugins after a cache-copy that preserves symlinks."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
MARKETPLACE = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
SERVER_PLUGINS = [
    ROOT / plugin["source"]
    for plugin in MARKETPLACE["plugins"]
    if (ROOT / plugin["source"] / ".mcp.json").is_file()
]


@pytest.mark.parametrize("plugin", SERVER_PLUGINS, ids=lambda plugin: plugin.name)
def test_marketplace_plugin_runs_without_the_rest_of_the_repo(plugin: Path, tmp_path: Path):
    installed = tmp_path / plugin.name
    shutil.copytree(
        plugin,
        installed,
        symlinks=True,
        ignore=shutil.ignore_patterns(".venv", "__pycache__", ".pytest_cache"),
    )

    # A checkout-relative link is broken when only this plugin gets installed.
    assert not (installed / "src/shadetree_ai_plugins_common").is_symlink()
    assert not (installed / "hooks/check_uv.py").is_symlink()
    assert (installed / "hooks/check_uv.py").is_file()

    # -I ignores PYTHONPATH and user site packages. Check module origins too:
    # workspace editable installs must not conceal a missing bundled module.
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            """
import asyncio
import importlib
import sys
import tomllib
from pathlib import Path

from fastmcp import Client

installed = Path(sys.argv[1])
source = installed / "src"
sys.path.insert(0, str(source))
project = tomllib.loads((installed / "pyproject.toml").read_text())
modules = project["tool"]["uv"]["build-backend"]["module-name"]

async def check_tools(server):
    async with Client(server) as client:
        assert await client.list_tools(), "MCP server exposes no tools"

for name in modules:
    module = importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(source), module.__file__
    if name != "shadetree_ai_plugins_common":
        asyncio.run(check_tools(module.mcp))
""",
            str(installed),
        ],
        cwd=installed,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
