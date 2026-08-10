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
cp -RL plugins/<name> /tmp/lock-sim/<name>   # -L dereferences the common symlink, matching Desktop's cache-copy
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

Claude Desktop copies an installed plugin's directory into an isolated
cache and does **not** follow references outside it. A plain
`../../../packages/common` import, or a `{workspace = true}` uv-source
dependency, breaks the moment a client installs the plugin — even
though both work fine when testing in place from a git checkout.

The fix, proven in `plugins/iphone` and `plugins/career-navigator`: a
**same-repo symlink**, which Desktop dereferences into a real copy at
install time, packaged into the plugin's own distribution via
`uv_build`'s multi-directory `module-name`:

```bash
ln -s ../../../packages/common/src/shadetree_ai_plugins_common plugins/<name>/src/shadetree_ai_plugins_common
```

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

Vendor by symlink. It's a plain script, not part of the installable
package, so no `module-name`/build-backend involvement is needed:

```bash
mkdir -p plugins/<name>/hooks
ln -s ../../../packages/common/hooks/check_uv.py plugins/<name>/hooks/check_uv.py
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
(`packages/common/hooks/check_uv.py` is plain stdlib Python and
dereferences the same way `shadetree_ai_plugins_common` does). It has
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

Stronger than the in-memory `fastmcp.Client` tests: spawn the plugin's
actual `.mcp.json` command against a copy with the symlink
dereferenced, and confirm it resolves with no sibling `packages/`
directory present.

```bash
cp -RL plugins/<name> /tmp/check
uv run --project /tmp/check python -m shadetree_ai_plugins_<name>
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
| `grilling` | skills-only |
| `handoff` | skills-only + `commands/` |
| `meeting-recap` | skills-only + bundled script |
| `email-domain-reputation` | server-backed (API key → keychain) |
| `audiobook-creator` | server-backed (long-running synthesis) |
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
