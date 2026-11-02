"""Bundle committed common helpers so plugins survive a standalone Git install.

Run after committing shared changes: python3 scripts/vendor_common.py <plugin> ...
Uncommitted shared-code changes and untracked files are deliberately excluded.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMON = "packages/common/src/shadetree_ai_plugins_common"
HOOK = "packages/common/hooks/check_uv.py"


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args], cwd=ROOT)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plugins", nargs="+", help="Plugin directory names to update")
    args = parser.parse_args()
    plugins = []
    for name in args.plugins:
        plugin = ROOT / "plugins" / name
        if plugin.resolve().parent != ROOT / "plugins" or not (plugin / "pyproject.toml").is_file():
            parser.error(f"Not a server-backed plugin: {name!r}")
        plugins.append(plugin)

    # Read tracked blobs, not the working tree: no caches, credentials, or
    # unfinished shared-code changes should accidentally enter a release.
    sources = git("ls-tree", "-r", "--name-only", "HEAD", "--", COMMON, HOOK).decode().splitlines()
    files = {path: git("show", f"HEAD:{path}") for path in sources}
    for plugin in plugins:
        for link in (plugin / "src/shadetree_ai_plugins_common", plugin / "hooks/check_uv.py"):
            if link.is_symlink():
                link.unlink()
        for source, content in files.items():
            relative = (
                Path("hooks/check_uv.py")
                if source == HOOK
                else Path("src/shadetree_ai_plugins_common") / Path(source).relative_to(COMMON)
            )
            destination = plugin / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
        print(f"Bundled common helpers into {plugin.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
