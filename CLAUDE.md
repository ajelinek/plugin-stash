# shadetree-ai-plugins — instructions for Claude

This repo is both a **uv workspace** (for building/testing Python code)
and a **Claude plugin marketplace** (`.claude-plugin/marketplace.json`
at the repo root) that consulting clients install from directly in
Claude Desktop: Customize → Plugins → (+) → Add marketplace →
`ajelinek/shadetree-ai-plugins`. Each installable unit is a "solution" — a
self-contained Claude plugin bundling one or more local FastMCP
servers, built so it still runs correctly after Claude Desktop copies
it out of this repo into its own isolated plugin cache.

There is no scaffolding script and no template directory — when asked
to add a new plugin, generate the files directly from the pattern
below (copy the shape of `plugins/imessages`, which is a complete,
tested, working reference implementation).

## Repo layout

```
shadetree-ai-plugins/
  .claude-plugin/marketplace.json   # lists every installable plugin
  packages/common/                  # shared helpers, vendored (not installed) into plugins
  plugins/<name>/                   # one directory per solution
```

## Anatomy of one plugin (`plugins/<name>/`)

```
plugins/<name>/
  .claude-plugin/plugin.json   # name (kebab-case), version, description, author, etc.
  .mcp.json                    # declares the bundled MCP server(s) — see below
  fastmcp.json                 # local dev only (`fastmcp run fastmcp.json`); NOT used by the installed plugin
  hooks/hooks.json              # SessionStart hook warning if `uv` is missing — see below
  hooks/check_uv.py             # symlink -> packages/common/hooks/check_uv.py
  skills/<name>/SKILL.md        # tells Claude when/how to use this plugin's tools
  pyproject.toml
  uv.lock                      # standalone lock, NOT the workspace root's — see "Per-plugin lockfile" below
  src/shadetree_ai_plugins_<name>/
    __init__.py                # `from .server import mcp`
    __main__.py                # `from shadetree_ai_plugins_<name>.server import mcp` + `mcp.run()` under `if __name__ == "__main__"`
    server.py                  # FastMCP instance + @mcp.tool functions
  tests/test_server.py          # in-memory fastmcp.Client tests
  README.md
  CHANGELOG.md
```

Naming: directory and marketplace `name` are kebab-case
(`iphone-history`); the Python package is the same string with
underscores (`shadetree_ai_plugins_iphone_history`).

### `.claude-plugin/plugin.json`

Required: `name` only. Always also set `version` (semver — see
Versioning below), `description`, `author`, `homepage`/`repository`
(this repo's URL), `license`. Do **not** add an `mcpServers` field here
— `.mcp.json` at the plugin root is already the default discovery
location, and declaring both risks the two sources being merged
ambiguously.

### `.mcp.json`

```json
{
  "mcpServers": {
    "<name>": {
      "command": "uv",
      "args": ["run", "--project", "${CLAUDE_PLUGIN_ROOT}", "--locked", "python", "-m", "shadetree_ai_plugins_<name>"]
    }
  }
}
```

Invoke the module directly (`uv run --project ... python -m <pkg>`),
not `fastmcp run <fastmcp.json>` — once installed, the plugin directory
is a fully self-sufficient uv project, and going through FastMCP's own
CLI would add a second, redundant environment-management layer with its
own path assumptions. `--locked` requires the plugin's own `uv.lock`
(see next section) and makes the client's install fail loudly if that
lock is ever out of sync with `pyproject.toml`, instead of silently
resolving different dependency versions than what was tested.

A single plugin can bundle **multiple** MCP servers: add more entries
to this same `mcpServers` object (each its own `command`/`args`,
typically one more `src/shadetree_ai_plugins_<name>/<thing>_server.py` module and
one more `__main__`-style entry point). Use this when a solution is one
coherent product with several tool surfaces (e.g. Outlook + Teams +
SharePoint under one client-facing install), rather than splitting into
several separately-installed plugins.

### `fastmcp.json` (dev-only)

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

### `pyproject.toml`

```toml
[project]
name = "shadetree-ai-plugins-<name>"
version = "0.1.0"
description = "..."
requires-python = ">=3.12"
dependencies = ["fastmcp>=3.4"]

[build-system]
requires = ["uv_build>=0.8.17,<0.9"]
build-backend = "uv_build"
```

If this plugin vendors `packages/common` (see next section), add
`"keyring>=25.5.0"` to `dependencies` (a transitive import of
`shadetree_ai_plugins_common`) and the `[tool.uv.build-backend]` table shown below.

## Per-plugin lockfile (`uv.lock`)

Every plugin ships its **own** `uv.lock`, committed at
`plugins/<name>/uv.lock`, pinning the exact resolved versions of
`fastmcp`/`keyring`/every transitive dependency for that plugin. This
is what `--locked` in `.mcp.json` (above) enforces at launch. Without
it, two clients installing the same plugin version weeks apart could
silently resolve different transitive dependency versions if something
new lands on PyPI in between — the opposite of "these are client
deliverables that need controlled releases."

**Why you can't just run `uv lock` inside the plugin directory**: this
repo is a uv workspace, and every `plugins/<name>/` is a workspace
member. Any `uv` command run with a cwd inside one walks up, finds the
workspace root, and operates on the *shared* root `uv.lock` instead —
it will not create a standalone one. A standalone lock only makes sense
for how the plugin actually runs post-install: alone, with no workspace
around it (exactly what Desktop's plugin cache looks like). So the lock
has to be generated in that same isolated shape:

```bash
rm -rf /tmp/lock-sim && mkdir -p /tmp/lock-sim
cp -RL plugins/<name> /tmp/lock-sim/<name>   # -L dereferences the shadetree_ai_plugins_common symlink, matching Desktop's cache-copy
cd /tmp/lock-sim/<name>
uv lock                                       # no enclosing workspace here -> produces a real standalone uv.lock
cp uv.lock <repo>/plugins/<name>/uv.lock
```

Re-run this any time that plugin's `pyproject.toml` dependencies
change, as part of the release steps below — a stale lock is exactly
what `--locked` is there to catch, so regenerating it isn't optional.
A per-plugin `uv.lock` sitting inside a workspace member does **not**
interfere with root-level workspace commands (`uv sync --all-packages`,
`uv run pytest`, CI) — verified: the workspace only ever reads/writes
the root lock; a member's own lock is inert until that member is run
standalone.

## Sharing code via `packages/common`

Claude Desktop copies an installed plugin's directory into an isolated
cache and does **not** follow references outside that directory — a
plain `../../../packages/common` import, or a `{workspace = true}`
uv-source dependency, breaks the moment a client installs the plugin,
even though it works fine when testing in-place from a git checkout.

The fix, already proven in `plugins/imessages` and
`plugins/iphone-history`: a **same-repo symlink** dereferenced by Claude
Desktop at install time into a real copy, packaged into the plugin's
own distribution via `uv_build`'s multi-directory `module-name`:

```bash
ln -s ../../../packages/common/src/shadetree_ai_plugins_common plugins/<name>/src/shadetree_ai_plugins_common
```

```toml
[tool.uv.build-backend]
module-name = ["shadetree_ai_plugins_<name>", "shadetree_ai_plugins_common"]
```

`packages/common` itself is a **virtual** workspace member
(`package = false`, no `[build-system]`) — it is never installed as its
own distribution, only ever consumed this way. Only vendor it if the
new plugin actually needs its helpers (logging, `~/.shadetree-ai-plugins/<name>/`
state paths, keychain secrets, desktop notifications, read-only
filesystem checks); it's fine for a plugin to skip this entirely.

## `uv`-missing hook (every plugin should have this)

Every plugin needs `uv` on the client's `PATH` to run at all (see
`.mcp.json` above). If it's missing, the client would otherwise just
see a silent "MCP server failed to connect" with no indication why.
Every plugin should include a `SessionStart` hook that checks for `uv`
and, if it's missing, shows a clear, actionable message — **never**
silently auto-installs anything on a client's machine; that's a
deliberate choice, not an oversight, since this can run unattended on
a client's computer.

Vendor the same way as `packages/common` — a symlink, since this is a
plain script (not part of the installable package, so no
`module-name`/build-backend involvement needed):

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
Python installs aren't consistent about which alias is on `PATH` —
whichever name doesn't exist on a given machine just fails to spawn
that one entry; the other one covers it. This has only been verified
by reasoning through the documented hook contract and by simulating
the cache-copy locally (`packages/common/hooks/check_uv.py` is plain
stdlib Python, dereferences and runs standalone the same way
`shadetree_ai_plugins_common` does) — it has **not** been exercised against a
real Windows machine or a live Claude Desktop session. Smoke-test it
for real once there's Windows access, particularly whether a hook
entry whose `command` can't be spawned at all surfaces as visible
error clutter or fails silently.

## Skills

`skills/<name>/SKILL.md` needs YAML frontmatter with `name` and
`description` (the description drives when Claude decides to use it —
be specific about trigger conditions), then guidance on which tools to
call and how to interpret their results.

## Registering a new plugin

Add an entry to `.claude-plugin/marketplace.json`'s `plugins` array
once it's ready for clients to install:

```json
{ "name": "<name>", "source": "./plugins/<name>", "description": "..." }
```

(Use a full `./plugins/<name>` path, not a bare `<name>` shorthand —
the latter isn't accepted by the currently-installed `claude` CLI even
with `metadata.pluginRoot` set.)

## Verifying a new plugin

```bash
uv sync --all-packages --locked
uv run ruff check .
uv run pytest plugins/<name>/tests
claude plugin validate ./plugins/<name>
claude plugin validate .            # whole marketplace, run before every push
```

Don't forget to generate `plugins/<name>/uv.lock` (see previous
section) before considering a new plugin done — without it, `--locked`
in `.mcp.json` will fail for every client since there's no lock to
enforce.

`claude plugin validate` and `claude` generally are maintainer-side dev
tools only — clients never run them; they use Claude Desktop's GUI.

For a stronger check that the vendored dependency truly survives
Desktop's cache-copy (not just the in-memory `fastmcp.Client` tests),
spawn the plugin's actual `.mcp.json` command against a copy with the
symlink dereferenced (`cp -RL plugins/<name> /tmp/check`) and confirm
`uv run --project /tmp/check python -m shadetree_ai_plugins_<name>` still resolves
and runs with no sibling `packages/` directory present.

## Versioning and releases

Each plugin versions independently. To release a change: update
`plugins/<name>/pyproject.toml` dependencies if needed, **regenerate
`plugins/<name>/uv.lock`** (see "Per-plugin lockfile" above — a stale
lock is the one thing `--locked` will not silently tolerate), bump
`version` in `plugins/<name>/.claude-plugin/plugin.json`, add a dated
entry to `plugins/<name>/CHANGELOG.md`, then
`claude plugin tag plugins/<name> --push` (or omit `--push` and push
manually), then push the commit. Clients only receive it when they
explicitly update (Desktop's Plugins panel, or
`/plugin update <name>@shadetree-ai-plugins`) — never automatically. Use explicit
semver here, not commit-SHA auto-versioning — these are client
deliverables that need controlled releases.

## CI

`.github/workflows/ci.yml` runs `uv sync --all-packages --locked` +
`ruff check` + `pytest`, and separately `claude plugin validate . --strict`
(installing `@anthropic-ai/claude-code` via npm — confirmed to need no
auth for this check). Keep both green before merging.
