# career-navigator

A local, conversational career-exploration companion for one high school
student. Claude runs the actual conversation — a natural, ~8-15 question
RIASEC (Holland Code) interview, then academics/activities — and this
plugin's bundled FastMCP server validates and persists what's inferred, then
searches/ranks a local O*NET 30.3-derived occupation dataset (923
occupations). Everything the plugin itself stores and runs stays on this
machine: two local JSON files under `~/.shadetree-ai-plugins/career-navigator/`, plus
the bundled read-only dataset. No accounts, no hosting, no network calls
from the plugin's own tools/data. Claude may still use its own web search
mid-conversation for time-sensitive facts (current wages, program/licensing
specifics) the static dataset can't have — see the skill's "Filling gaps
with web search" section.

This plugin is primarily its skill (`skills/career-navigator/SKILL.md`) —
the conversational RIASEC-interview technique and career-presentation flow
live there; the MCP tools are deliberately thin (validate + persist +
rank/search).

Includes data from O*NET OnLine (USDOL/ETA), used under CC BY 4.0 — see
`skills/career-navigator/references/onet-data.md` for the full attribution
text and dataset provenance.

## Prerequisites

The machine needs `uv` installed and on `PATH`
(https://docs.astral.sh/uv/getting-started/installation/).

## Install

In Claude Desktop: **Customize → Plugins → (+) → Add marketplace**,
enter `ajelinek/shadetree-ai-plugins`, then install `career-navigator` from the list.
(Equivalent commands also work in a Desktop or Cowork chat window:
`/plugin marketplace add ajelinek/shadetree-ai-plugins` then
`/plugin install career-navigator@shadetree-ai-plugins`.)

Ask Claude something like "help me figure out what career might fit me" to
start the conversation once installed.

## What's inside

- `.claude-plugin/plugin.json` / `.mcp.json` — plugin + MCP server manifest
  (one server, `shadetree_ai_plugins_career_navigator`).
- `fastmcp.json` — local dev only (`fastmcp run fastmcp.json`), not used by
  the installed plugin.
- `skills/career-navigator/SKILL.md` — the conversational RIASEC-interview
  technique, career-presentation flow, and tool usage guidance.
- `skills/career-navigator/references/riasec-interview.md` — the full
  question bank (tagged by RIASEC dimension) and tone examples.
- `skills/career-navigator/references/onet-data.md` — dataset provenance,
  build process, field schema, and required attribution text.
- `src/shadetree_ai_plugins_career_navigator/server.py` — the FastMCP server: the six
  `career_*` tools.
- `src/shadetree_ai_plugins_career_navigator/onet_data.py` / `matching.py` — dataset
  loading and RIASEC-overlap/keyword ranking logic.
- `src/shadetree_ai_plugins_career_navigator/profile_store.py` /
  `preferences_store.py` — read/write for the two local state files.
- `src/shadetree_ai_plugins_career_navigator/data/onet_occupations.json` — the bundled,
  pre-processed O*NET 30.3 extract (923 occupations, ~1.3MB).
- `scripts/enrich_onet_dataset.py` — re-runnable script that joins O*NET's
  `task_ratings`/`work_styles` tables onto the dataset above (the `tasks`/
  `top_work_styles` fields); see onet-data.md for how to re-run it.
- `src/shadetree_ai_plugins_common` — symlink to the repo's shared helpers (logging,
  `~/.shadetree-ai-plugins/career-navigator/` state dir).
- `tests/test_server.py` — in-memory tests against a small synthetic
  occupation dataset (`uv run pytest` from repo root — no dependency on the
  real bundled dataset's exact contents).

## Tools

| Tool | Purpose |
|---|---|
| `career_status` | Session-start preflight: profile + completeness + next-step hint. |
| `career_update_profile` | Merge new RIASEC/academic/activity signal onto the stored profile. |
| `career_update_preferences` | Append a freeform note. |
| `career_search` | Ad-hoc O*NET search by RIASEC codes and/or free text. |
| `career_record_feedback` | Log a reaction to one presented career. |
| `career_rank_matches` | The primary shortlist — combines stored profile + prior reactions. |

See `skills/career-navigator/SKILL.md` for the full usage guidance, the
RIASEC interview technique, and known limitations.
