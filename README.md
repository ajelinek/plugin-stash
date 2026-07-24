# shadetree-ai-plugins

A growing collection of Claude plugins ("solutions") for consulting
clients, each bundling its own local MCP server(s) (and, over time,
Skills/hooks) as a single one-command install. This repo is itself a
plugin marketplace.

## What this is

The consultant behind this repo builds automation tools and ships them
to consulting clients. Early tools were Python CLI scripts invoked
through Claude's Bash tool, which sometimes can't reach a client's real
machine because Bash-tool commands execute wherever that session's
code-execution sandbox happens to be hosted (for a default Cowork
session, that's a fully isolated, ephemeral VM on Anthropic's servers
with no path to the local machine or home network). MCP servers fix
this: an MCP tool call is dispatched to wherever the MCP server process
actually lives. Once a server is installed and spawned by **Claude
Desktop**, it runs as a real local subprocess on the user's own
machine — and both a plain chat session and a Cowork session in that
same Desktop app can reach that already-running local server, since its
lifecycle is owned by Desktop, not by whatever sandbox a particular
session's own reasoning/code-execution happens to use.

Each solution here is a **Claude plugin**: it bundles its own MCP
server (built with [FastMCP](https://gofastmcp.com)) plus a Skill that
tells Claude how to use it. This repo is the **marketplace** that lists
every solution, so a client can add it once and install whichever
solutions they need.

## Repo layout

```
shadetree-ai-plugins/
  CLAUDE.md                        # how this repo works — read this to add a new plugin
  .claude-plugin/marketplace.json  # the marketplace manifest
  packages/common/                 # shared helpers, vendored (not installed) into plugins
  plugins/imessages/                # placeholder/test solution #1
  plugins/iphone-history/           # placeholder/test solution #2
```

## Installing a plugin (for clients)

In Claude Desktop: **Customize → Plugins → (+) → Add marketplace**,
enter `ajelinek/shadetree-ai-plugins`, then install a plugin from the list (for
now: `imessages` or `iphone-history`).

The equivalent commands also work the same way in a Desktop or Cowork
chat window, since both reach the same locally-running MCP server once
it's registered through Desktop:

```
/plugin marketplace add ajelinek/shadetree-ai-plugins
/plugin install imessages@shadetree-ai-plugins
```

To update or remove a plugin later:

```
/plugin update imessages@shadetree-ai-plugins
/plugin marketplace update shadetree-ai-plugins
/plugin uninstall imessages@shadetree-ai-plugins
```

**Prerequisite:** the machine running Claude Desktop needs
[`uv`](https://docs.astral.sh/uv/getting-started/installation/)
installed and on `PATH`. `uv` fetches the right Python version itself,
so no separate Python install is required.

Each plugin's own README (e.g. `plugins/imessages/README.md`) has
plugin-specific usage notes; this section covers the marketplace
mechanics, which are identical for every plugin here.

## Adding a new solution (for you)

There's no scaffolding script — this repo is meant to be worked on
with Claude's help. **`CLAUDE.md` documents the full pattern** (file
layout, naming, the `packages/common` vendoring trick, registration,
versioning); just ask Claude to add a new plugin (e.g. "add a plugin
called `my-solution` that does X") and it will generate the files
directly from that doc and `plugins/imessages`, which is a complete,
tested, working reference implementation to copy the shape of.

## Local development

```
uv sync --all-packages --locked   # one-time / after pulling changes
uv run ruff check .
uv run pytest
claude plugin validate .           # maintainer-side only — see CLAUDE.md
```

Run a single plugin's tests with `uv run pytest plugins/<name>/tests`.

## Versioning and releases

Each plugin is versioned independently — bump `version` in
`plugins/<name>/.claude-plugin/plugin.json`, add a dated entry to
`plugins/<name>/CHANGELOG.md`, then `claude plugin tag plugins/<name>`
(`--dry-run` to preview, `--push` to push) and push the commit. Clients
only receive the update when they explicitly update (via the Desktop
Plugins panel, or `/plugin update <name>@shadetree-ai-plugins`) — nothing changes
on a client's machine on its own. Explicit semver (rather than letting
every commit auto-count as a new version) is deliberate here: these are
client deliverables that need controlled releases, not an
internal/rapidly-iterating tool.

## Roadmap

Next up: a real Microsoft 365/Graph-related solution, and turning
`imessages`/`iphone-history` into actual data-reading plugins once the
plumbing proven here (packaging, marketplace install, stdio connection,
local filesystem access) is trusted. Nothing in this structure blocks a
future plugin from also mixing in a third-party/remote MCP server
(e.g. an OAuth-authenticated Microsoft Graph endpoint) alongside its
own local FastMCP server, in the same `.mcp.json`.
