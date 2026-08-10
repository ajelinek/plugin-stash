# shadetree-ai-plugins — instructions for Claude

This repo is both a **uv workspace** and a **Claude plugin marketplace**
(`.claude-plugin/marketplace.json` at the root) that consulting clients
install from in Claude Desktop: Customize → Plugins → (+) → Add
marketplace → `ajelinek/shadetree-ai-plugins`. Each installable unit is
a self-contained plugin bundling skills and, where earned, local FastMCP
servers — and must still run after Desktop copies it out of this repo
into its isolated plugin cache.

There's no scaffolding script or template directory: generate new
plugins from the patterns below, copying an existing one
(`plugins/iphone` or `plugins/career-navigator` for server-backed).

**`docs/plugin-authoring.md`** holds the step-by-step procedures —
lockfile generation, `packages/common` vendoring, the `uv`-missing hook,
`fastmcp.json`, and the `skills-stash` migration plan. Read the relevant
section there when doing that task; this file carries the rules that
apply every session.

## Repo layout

```
shadetree-ai-plugins/
  .claude-plugin/marketplace.json   # lists every installable plugin
  packages/common/                  # shared helpers, vendored (not installed) into plugins
  plugins/<name>/                   # one directory per solution
  docs/plugin-authoring.md          # detailed authoring procedures
```

## Two plugin shapes — MCP only when earned

A plugin is either **server-backed** (skills + FastMCP server(s)) or
**skills-only** (skills, optionally `commands/` and bundled scripts).

A server isn't free to ship: it adds a per-plugin lockfile resolved
under `--locked`, a hard `uv`-on-`PATH` requirement, the `uv`-missing
hook explaining it, and a subprocess Desktop must keep alive. Pay that
only for:

- **persistent state** across sessions (stored profile, job history);
- **secret storage** — keys go in the OS keychain via
  `shadetree_ai_plugins_common.secrets`, never a `.env` beside a skill
  (a gitignored `.env` doesn't survive install; committing one leaks a
  real key);
- a **long-running job** a single blocking tool call can't survive.

Otherwise ship skills-only, with a bundled script for deterministic
logic. Prose plus a stdlib-only script is a complete plugin — don't add
a server to make one look substantial. When in doubt start skills-only:
adding a server later is a version bump, removing one is a migration.

## Anatomy: server-backed plugin (`plugins/<name>/`)

```
plugins/<name>/
  .claude-plugin/plugin.json   # name (kebab-case), version, description, author
  .mcp.json                    # declares the bundled MCP server(s)
  fastmcp.json                 # dev only; unused once installed
  hooks/hooks.json             # SessionStart hook warning if `uv` is missing
  hooks/check_uv.py            # symlink -> packages/common/hooks/check_uv.py
  skills/<skill>/SKILL.md      # when/how to use this plugin's tools (one or more skills)
  commands/<command>.md        # optional slash commands
  pyproject.toml
  uv.lock                      # standalone lock, NOT the workspace root's
  src/shadetree_ai_plugins_<name>/
    __init__.py                # `from .server import mcp`
    __main__.py                # imports `mcp` from .server + `mcp.run()` under `if __name__ == "__main__"`
    server.py                  # FastMCP instance + @mcp.tool functions
  tests/test_server.py         # in-memory fastmcp.Client tests
  README.md
  CHANGELOG.md
```

Naming: directory and marketplace `name` are kebab-case
(`career-navigator`); the Python package is the same with underscores
(`shadetree_ai_plugins_career_navigator`). Skill names are independent —
`plugins/iphone` ships `imessage` and `icallhistory`.

For `hooks/`, `fastmcp.json`, the standalone `uv.lock`, and vendoring
`packages/common` into `src/`, see `docs/plugin-authoring.md`.

### `.claude-plugin/plugin.json`

Only `name` is required; always also set `version` (semver),
`description`, `author`, `homepage`/`repository` (this repo), `license`.
Do **not** add `mcpServers` here — `.mcp.json` at the plugin root is the
default discovery location, and declaring both risks ambiguous merging.

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

Invoke the module directly, not `fastmcp run <fastmcp.json>` — the
installed plugin directory is already a self-sufficient uv project, so
FastMCP's CLI would add a redundant environment layer with its own path
assumptions. `--locked` requires the plugin's own standalone `uv.lock`
and fails the client's launch loudly if that lock drifts from
`pyproject.toml`, instead of silently resolving untested versions.

One plugin can bundle **multiple** servers: add entries to `mcpServers`,
each with its own `command`/`args` and typically another
`src/shadetree_ai_plugins_<name>/<thing>_server.py` plus entry point.
Use this when one coherent product has several tool surfaces (Outlook +
Teams + SharePoint in a single install) rather than splitting plugins.

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

Vendoring `packages/common` adds a dependency and a
`[tool.uv.build-backend]` table — see `docs/plugin-authoring.md`.

## Anatomy: skills-only plugin (`plugins/<name>/`)

```
plugins/<name>/
  .claude-plugin/plugin.json   # same fields as above — the only required file
  skills/<skill>/SKILL.md      # the substance of the plugin
  skills/<skill>/references/   # optional progressive-disclosure detail
  scripts/<thing>.py           # optional bundled script — stdlib only
  commands/<command>.md        # optional slash commands
  tests/test_<thing>.py        # only if there's a script to test
  README.md
  CHANGELOG.md
```

No `.mcp.json`, `fastmcp.json`, `pyproject.toml`, `uv.lock`, `src/`, or
**`hooks/`** — the `uv`-missing hook explains a dependency this shape
doesn't have. Bundled scripts must be stdlib-only for the same reason:
with no lockfile and no `uv run`, whatever `python3` the client has is
what runs them.

**Every skills-only plugin must be listed in the root `pyproject.toml`
`exclude`.** A `plugins/` directory with no `pyproject.toml` breaks
every root-level `uv` command in the repo — the root
`members = ["packages/*", "plugins/*"]` glob matches it and `uv`
hard-errors with *"Workspace member ... is missing a `pyproject.toml`"*.

```toml
[tool.uv.workspace]
members = ["packages/*", "plugins/*"]
exclude = ["plugins/<name>"]        # every skills-only plugin, one line each
```

Exclusion costs nothing — linting and test collection still work; see
`docs/plugin-authoring.md` for how tests reach a bundled script.

## Skills

`skills/<skill>/SKILL.md` needs YAML frontmatter with `name` and
`description` (the description decides when Claude uses it — be specific
about triggers), then guidance on which tools to call and how to read
their results. Push long detail into `skills/<skill>/references/*.md`
and link to it, keeping the body cheap to load every time.

**Referring to bundled files:** use paths relative to the skill's own
directory (`scripts/recap.py`, `references/schema.md`) — Claude reads
`SKILL.md` from a real absolute path in the cache and resolves siblings
from there. Do **not** use `${CLAUDE_PLUGIN_ROOT}` in skill or command
markdown: it's expanded only inside `.mcp.json` and `hooks/hooks.json`,
and isn't exported into Bash-tool environments (confirmed absent from a
running session's env; no official plugin uses it outside config files).

## Commands (`commands/<command>.md`)

One markdown file per command; `commands/<command>.md` becomes
`/<command>`. Frontmatter takes `description` (shown in the command
list) and optionally `allowed-tools`; the body is the prompt that runs.
If a skill's docs promise a `/something`, it needs a file here — prose
describing a slash command doesn't create one.

## Registering a new plugin

Add to `.claude-plugin/marketplace.json`'s `plugins` array once it's
ready for clients:

```json
{ "name": "<name>", "source": "./plugins/<name>", "description": "..." }
```

Use the full `./plugins/<name>` path — the bare `<name>` shorthand isn't
accepted by the installed `claude` CLI, even with `metadata.pluginRoot`.

## Verifying a new plugin

```bash
uv sync --all-packages --locked     # server-backed only; also proves the exclude list is right
uv run ruff check .
uv run pytest plugins/<name>/tests  # skip if the plugin has no tests
claude plugin validate ./plugins/<name>
claude plugin validate .            # whole marketplace, run before every push
```

The easy-to-miss step per shape: **server-backed** needs its
`plugins/<name>/uv.lock` generated, or `--locked` fails for every client;
**skills-only** needs the root `exclude` entry, and forgetting it breaks
`uv` for the entire repo rather than just that plugin.

`claude` is a maintainer-side tool only — clients use Desktop's GUI.

For a stronger check that a vendored dependency survives Desktop's
cache-copy, see `docs/plugin-authoring.md`.

## Versioning and releases

Each plugin versions independently; both shapes release the same way:

1. Server-backed only: update `pyproject.toml` deps if needed and
   **regenerate `uv.lock`** (procedure in `docs/plugin-authoring.md`).
2. Bump `version` in `plugins/<name>/.claude-plugin/plugin.json`.
3. Add a dated entry to `plugins/<name>/CHANGELOG.md`.
4. `claude plugin tag plugins/<name> --push` (or tag and push manually),
   then push the commit.

Use explicit semver, not commit-SHA auto-versioning — these are client
deliverables needing controlled releases. Clients get updates only when
they explicitly update (Desktop's Plugins panel or
`/plugin update <name>@shadetree-ai-plugins`), never automatically.

## CI

`.github/workflows/ci.yml` runs `uv sync --all-packages --locked` +
`ruff check` + `pytest`, and separately `claude plugin validate . --strict`
(installing `@anthropic-ai/claude-code` via npm — confirmed to need no
auth). Keep both green before merging.

## Incoming migration from `skills-stash`

Skills from the sibling `skills-stash` repo are moving here to install as
versioned deliverables. One plugin per skill, one at a time, each its own
commit in both repos. Planned order, per-skill shapes, and the two
follow-through steps (audit for secrets; clean up the `skills-stash`
side) are in `docs/plugin-authoring.md`.
