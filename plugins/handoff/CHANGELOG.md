# Changelog

## 0.1.0 -- 2026-08-09

Initial creation as a plugin, migrated from the `skills-stash` skill of the
same name. Skills-only: no MCP server, no bundled scripts, no dependencies.

The `/handoff create`, `/handoff list` and `/handoff resume` commands the
original documented are now real — the skill dispatches on its first argument
instead of describing commands that were never registered. Handoffs are named
`handoff-YYYYMMDD-HHMMSS-<slug>.md` (previously `handoff-<name>.md`, which
overwrote on a same-name rerun), and `resume` continues the work rather than
displaying the file.

Based on Matt Pocock's `handoff` skill.
