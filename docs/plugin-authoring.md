# Plugin authoring reference

Deep detail for procedures referenced from `CLAUDE.md`. Read the
relevant section when you're actually doing that task; the root
`CLAUDE.md` carries the rules that apply to every session.

## `fastmcp.json` (dev-only)

Local development only (`fastmcp run fastmcp.json`) — the installed
plugin never uses it, since `.mcp.json` invokes the module directly.

```json
{
  "source": {
    "type": "filesystem",
    "path": "src/shadetree_ai_plugins_<name>/server.py",
    "entrypoint": "mcp"
  },
  "environment": { "type": "uv", "python": ">=3.12", "project": "." },
  "deployment": { "transport": "stdio", "log_level": "INFO" }
}
```

## Per-plugin lockfile (`uv.lock`, server-backed only)

Every server-backed plugin commits its **own** `plugins/<name>/uv.lock`
pinning `fastmcp`/`keyring`/all transitive versions. That's what
`--locked` in `.mcp.json` enforces at launch. Without it, two clients
installing the same plugin version weeks apart can resolve different
transitive versions — the opposite of controlled client deliverables.

**`uv lock` inside the plugin directory won't produce one.** Each
`plugins/<name>/` is a workspace member, so `uv` walks up, finds the
workspace root, and operates on the shared root lock instead. A
standalone lock only makes sense in the shape the plugin actually runs
in post-install: alone, with no workspace around it — exactly what
Desktop's plugin cache looks like. So generate it in that shape:

```bash
rm -rf /tmp/lock-sim && mkdir -p /tmp/lock-sim
cp -R plugins/<name> /tmp/lock-sim/<name>    # plugin already contains its shared helpers
cd /tmp/lock-sim/<name>
uv lock                                      # no enclosing workspace -> real standalone uv.lock
cp uv.lock <repo>/plugins/<name>/uv.lock
```

Re-run whenever that plugin's `pyproject.toml` dependencies change, as
part of the release steps — a stale lock is exactly what `--locked` is
there to catch, so regenerating isn't optional.

A per-plugin `uv.lock` inside a workspace member does **not** interfere
with root-level commands (`uv sync --all-packages`, `uv run pytest`,
CI) — verified: the workspace only ever reads and writes the root lock,
and a member's own lock is inert until that member runs standalone.

## Sharing code via `packages/common`

Marketplace installers can copy only the selected plugin directory into an
isolated cache. Imports from `../../../packages/common`, workspace-only
dependencies, and symlinks pointing outside the plugin all break in that
shape. Do not rely on Claude Desktop or another host dereferencing links.

**Commit real copies inside each plugin.** `packages/common` remains the
source of truth; refresh selected plugins with the maintainer script:

```bash
python3 scripts/vendor_common.py <name> [<other-name> ...]
```

The script copies the shared package and `check_uv.py` hook from **committed
`HEAD`**, replacing the old symlinks if present. Commit shared changes before
running it. It deliberately excludes untracked files, local caches, and
uncommitted work so unfinished changes aren't swept into client releases.
Commit the resulting plugin files and bump each affected plugin's version.
Existing plugin snapshots need not change until that plugin is released.

Package the bundled helpers using `uv_build`'s multi-directory `module-name`:

```toml
[tool.uv.build-backend]
module-name = ["shadetree_ai_plugins_<name>", "shadetree_ai_plugins_common"]
```

Also add `"keyring>=25.5.0"` to `[project] dependencies` — a transitive
import of `shadetree_ai_plugins_common`.

`packages/common` is itself a **virtual** workspace member
(`package = false`, no `[build-system]`) — never installed as its own
distribution, only ever consumed this way. Vendor it only if the plugin
actually needs its helpers (logging, `~/.shadetree-ai-plugins/<name>/`
state paths, keychain secrets, desktop notifications, read-only
filesystem checks); skipping it entirely is fine.

## `uv`-missing hook (server-backed plugins)

Every server-backed plugin needs `uv` on the client's `PATH` to run at
all. If it's missing, the client otherwise sees only a silent "MCP
server failed to connect" with no indication why. Ship a `SessionStart`
hook that checks for `uv` and prints a clear, actionable message.

**Never** silently auto-install anything on a client's machine. That's a
deliberate choice, not an oversight — this can run unattended on a
client's computer.

Bundle the hook as a real file using the same vendoring command above.
It's a plain script, not part of the installable package, so no
`module-name`/build-backend involvement is needed:

```bash
python3 scripts/vendor_common.py <name>
```

`plugins/<name>/hooks/hooks.json`:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          { "type": "command", "command": "python3", "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/check_uv.py"] },
          { "type": "command", "command": "py", "args": ["${CLAUDE_PLUGIN_ROOT}/hooks/check_uv.py"] }
        ]
      }
    ]
  }
}
```

Two entries (`python3` for macOS/Linux, `py` for the Windows launcher)
because exec-form hooks need one literal interpreter name and Windows
Python installs aren't consistent about which alias is on `PATH`.
Whichever name doesn't exist on a given machine just fails to spawn that
entry; the other covers it.

**Caveat:** verified only by reasoning through the documented hook
contract and simulating the cache-copy locally
(`packages/common/hooks/check_uv.py` is plain stdlib Python and is
bundled as a regular file). It has
**not** been exercised against a real Windows machine or a live Claude
Desktop session. Smoke-test for real once there's Windows access,
particularly whether a hook entry whose `command` can't be spawned at
all surfaces as visible error clutter or fails silently.

## Skills-only plugins and the workspace `exclude` list

The rule is in `CLAUDE.md`: every skills-only plugin needs an entry in
the root `[tool.uv.workspace] exclude`, or it breaks every root-level
`uv` command in the repo.

Excluding it costs nothing that matters. Root `ruff check .` still lints
its scripts, and root `pytest` still collects its `tests/` — `testpaths`
includes `plugins`, which is independent of workspace membership. A test
just has to reach its script by path, since there's no installed package
to import:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
```

Both behaviors were verified end-to-end on a scratch workspace, not
assumed.

`claude plugin validate` accepts a plugin with no `.mcp.json` — verified
against a minimal fixture (plugin manifest + `skills/` + `commands/`,
nothing else). It passes plugin-level and marketplace-level validation,
and `--strict` too.

## Verifying the vendored dependency survives the cache-copy

`packages/common/tests/test_plugin_packaging.py` copies every server-backed
marketplace plugin without dereferencing symlinks, imports its bundled
modules in a fresh process, and lists its MCP tools. It checks module origins
so the workspace's editable installs cannot hide missing bundled code.

For an additional launch check, spawn the plugin's actual `.mcp.json`
command against a standalone copy with no sibling `packages/` directory.
Do **not** use `cp -L`: dereferencing links would hide this packaging bug.

```bash
cp -R plugins/<name> /tmp/check
uv run --project /tmp/check --locked python -m shadetree_ai_plugins_<name>
```

## Incoming migration from `skills-stash`

Several skills from the sibling `skills-stash` repo (an `npx skills add`
collection) are moving here so they install as versioned client
deliverables instead of copies. One plugin per skill, one skill at a
time, each its own commit in both repos. The plan lives in
`MIGRATION-skills-to-plugins.md` in the parent directory of both
checkouts.

| Skill | Shape |
|---|---|
| ~~`grilling`~~ | skills-only — **done**, `plugins/grilling` |
| ~~`handoff`~~ | skills-only — **done**, `plugins/handoff` |
| ~~`meeting-recap`~~ | skills-only + bundled script — **done**, `plugins/meeting-recap` |
| ~~`audiobook-creator`~~ | server-backed (long-running synthesis) — **done**, `plugins/audiobook-creator` |
| `email-domain-reputation` | server-backed (API key → keychain) |
| `professional-writing` (from `jelinek-writing`) | server-backed (profile state) |
| `website-health-analytics` | deferred — may not move |

`imessage`, `icallhistory`, and `cc-data-analyzer` already made the trip,
as `plugins/iphone` and `plugins/claude-usage-analyzer`.

Two things to watch when migrating one:

1. **Audit for secrets** before the first commit —
   `email-domain-reputation` currently keeps a real API key in a
   gitignored `.env` beside its `SKILL.md`.
2. After the plugin lands, finish the other half in `skills-stash` —
   delete the skill directory, drop its README row and `npx skills add`
   line, rebuild `dist/`, and check for dangling symlinks.
