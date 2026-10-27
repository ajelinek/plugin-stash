"""FastMCP server bundled with the shadetree-ai-plugins 'claude-usage-analyzer'
plugin.

Scope is Claude Desktop / Cowork -- this plugin is for business users of
the Desktop app. Claude Code CLI data is deliberately out of scope and is
never read; the CLI ships its own `/doctor`, `/usage`, and `/context`
commands for that surface, and duplicating them here would give a business
user a report about a product they don't use.

Nine tools, each thin over one of this package's modules:

- `check_data_access` -- read-only access check across the Desktop app-data
  and user-visible output locations (see local_data.py), plus which of the
  account-level data files are present this run. Call this first in a
  session.
- `list_local_workspace` -- redacted inventory of local Desktop/Cowork
  usage: Spaces, cached cloud Projects, Chat/Cowork sessions, and the
  chat->Project/Space membership. Optional filter/projection params
  (session_ids/project_uuid/folder_path/fields) scope this down instead of
  always paying for the full dump.
- `get_project_membership` -- just the project/Space join key for local
  sessions (no titles/transcripts/model data), meant to chain after the
  harness's own `session_info.list_sessions` and before `read_transcript`.
- `get_instructions_inventory` -- all four layers of standing context
  (global custom instructions, Space `instructions`, cloud Project
  `prompt_template`, and Claude's own local memory files) in one place,
  with character counts, session reach, and line-level duplication. This
  is also **the** place to get memory: it is on disk, a local-only run can
  read it, and there is no second memory tool. Backs the workspace checkup
  in references/workspace-checkup.md.
- `get_plan_usage_history` -- what the plan was *actually* consuming, from
  Claude Desktop's own rolling record of the five-hour and seven-day usage
  windows. The Desktop counterpart of the Claude Code CLI's `/usage`, and
  the only ground truth this plugin has about consumption; everything else
  in the usage lens is a proxy.
- `get_desktop_settings` -- account-level settings in one place: standing
  folder grants (each remote-session grant resolved to the Space it belongs
  to, not left as a raw session id), app-wide permission toggles, scheduled
  tasks, and the authoritative list of installed extensions. Backs the
  access-scope, capability-inventory, and settings-hygiene areas of the
  checkup, none of which per-session data can answer on its own.
- `locate_export_download` -- find an already-downloaded claude.ai export
  zip (verified by content, not filename) and unpack it, for the
  scheduled-recheck flow in references/export-acquisition.md.
- `parse_export` -- parse a claude.ai account data export
  (conversations.json etc.) into compact paged files instead of a single
  huge in-context blob.
- `render_dashboard` -- render a reorganization plan (already reasoned
  about by the caller -- this tool does no clustering/analysis of its own)
  into a self-contained, re-renderable HTML dashboard, with a local
  history log so re-runs show progress over time.

None of these tools move, rename, or delete a chat or Project -- this
plugin is read-only analysis. See skills/claude-usage-analyzer/SKILL.md
for the full workflow these are meant to be called in.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from shadetree_ai_plugins_common import get_logger, state_dir

from . import dashboard, export_data, local_data

logger = get_logger("shadetree-ai-plugins-claude-usage-analyzer")

mcp = FastMCP(
    name="shadetree-ai-plugins-claude-usage-analyzer",
    instructions=(
        "Read-only analysis of local Claude Desktop/Cowork usage plus an optional "
        "claude.ai account data export, rendered as a self-updating HTML dashboard. "
        "Claude Code CLI data is out of scope and never read. Never moves, renames, "
        "or deletes a chat or Project -- it only reads and proposes. Call "
        "check_data_access first each session."
    ),
    mask_error_details=False,
)

_READ_ONLY = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_WRITE_LOCAL = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}


def _default_state_dir() -> str:
    override = os.environ.get("SHADETREE_AI_PLUGINS_CLAUDE_USAGE_ANALYZER_STATE_DIR")
    if override:
        return override
    return str(state_dir("claude-usage-analyzer"))


@mcp.tool(annotations=_READ_ONLY)
def check_data_access() -> dict:
    """Preflight check: which local Claude Desktop/Cowork data locations
    (the Desktop app-data directory, the user-visible ~/Claude output
    folder) are visible to this session, per-platform. Call this first --
    if nothing is visible, this is almost always a Cowork Space/Project
    that hasn't had those folders added to its scope yet (see the warnings
    field and references/data-sources.md), not proof the data doesn't
    exist.

    Not to be confused with Claude Code's own `/doctor`, which checks a CLI
    installation. This checks only whether this plugin can *see* the data;
    the equivalent config-health review for Desktop is the workspace
    checkup in references/workspace-checkup.md.

    Each probed path carries its last-modified time in readable form --
    `modified_local_iso`, `modified_local_date`, `modified_local_weekday`
    (this machine's zone, named in `timezone`) and `modified_iso` (UTC) --
    beside the raw `modified` epoch-seconds float. Quote a local one; the
    raw float is a number, not a date, and reading it as milliseconds is
    how a real file's date comes out as 1970."""
    result = local_data.check_data_access()
    result["state_dir"] = _default_state_dir()
    return result


@mcp.tool(annotations=_READ_ONLY)
def list_local_workspace(
    session_ids: list[str] | None = None,
    project_uuid: str | None = None,
    folder_path: str | None = None,
    fields: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Redacted inventory of local Claude Desktop/Cowork usage: Desktop
    Spaces (folder-bound Projects), cached cloud Project metadata, and
    Chat/Cowork session metadata -- each with a `default_model`/`effort`
    (the session's configured default), `memory_enabled`/`skills_enabled`/
    `plugins_enabled`, and `custom_instructions` (the account's global
    custom instructions text, when set) -- plus, when a nested per-session
    transcript exists, a `models_used` tally of model id -> message count
    joined in for per-message accuracy (a session isn't necessarily one
    model throughout) -- and the chat->Project/Space membership.

    Space membership is the app's own record: each session carries the
    Space id Desktop filed it under, resolved through `spaces.json`. Only a
    chat the app recorded no Space for falls back to matching its folder
    against Space folders, and that fallback is a guess -- every session
    says which it is via `space_source` (`app_record` /
    `inferred_from_folder` / `none`) and `space_from_app_record`. Never
    present an inferred attribution as the user's own filing; say it was
    matched from the folder. `space_label` is always something a person can
    read -- the Space's real name when it resolves, and a plain-English
    explanation when it doesn't, never a bare uuid.

    Claude Code CLI sessions are deliberately not included. Secret-shaped
    keys and heavy MCP tool-schema blobs are stripped before this ever
    returns. This is a cache, not authoritative -- say so before
    presenting counts as exact.

    All params are optional and default to the full unfiltered inventory.
    Pass `session_ids`/`project_uuid`/`folder_path` to scope `local_sessions`
    down instead of pulling everything (avoids the write-to-file-and-grep
    workaround a large, unfiltered dump forces) -- or, if all that's needed
    is the project/Space join key for a known set of session ids, call
    `get_project_membership` instead, it's cheaper. `fields` projects every
    `local_sessions` entry down to just the named keys (the join key, `id`,
    is always kept).

    `since`/`until` scope this run to a time window. Both accept a relative
    age ("90d", "6m"), an ISO date ("2026-01-01"), or epoch milliseconds;
    omit both for the full history. Prefer the relative form when today's
    date isn't certain. The response's `time_window` reports what the
    bounds resolved to, how many records were excluded, and any bad-bound
    errors -- repeat that alongside any count taken from this response,
    since a windowed count presented as an account total is simply wrong.

    A bound written as a bare date is anchored at midnight **UTC**, while
    every date and weekday this plugin shows a person is local -- so on a
    machine that isn't on UTC the window edge lands part-way through a
    local day. `time_window.boundary_notes` (repeated in `notes`) says so
    and gives the exact bound to pass instead
    ("2026-01-01T00:00:00-05:00"); `since_local`/`until_local` show where
    the bounds actually fell on the reader's clock. Quote a window bound as
    UTC, or quote its local form -- never as a bare date with no zone.

    Sessions are matched on *overlap* with the window, not creation date --
    a chat started in January and still worked in March is in a March
    window. The window is applied before the Project/Space membership join,
    so every grouping in the response describes the same period.

    Each session carries both renderings of both timestamps beside the raw
    epoch-millisecond fields: `created_at_iso`/`last_activity_at_iso` in
    UTC, for comparison and joining, and
    `created_at_local_iso`/`created_at_local_date`/`created_at_local_weekday`
    (same for `last_activity_at`) on this machine's clock. **Present the
    local ones.** The response's `timezone` block names the zone they are
    in: "you started this chat on Thursday" is a wrong answer, not a
    caveated one, if Thursday was worked out in a zone the reader doesn't
    live in. The `time_window` bounds are the exception -- they stay UTC,
    are labelled UTC, and carry `since_local`/`until_local` beside them."""
    return local_data.build_inventory(
        session_ids, project_uuid, folder_path, fields, since, until
    )


@mcp.tool(annotations=_READ_ONLY)
def get_project_membership(
    session_ids: list[str] | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """The project/Space join key for local Desktop/Cowork sessions, and
    nothing else -- no titles, no transcripts, no model data (call
    `list_local_workspace` for the full inventory, or the harness's own
    `session_info.read_transcript` for content). Chain
    `session_info.list_sessions()` -> this tool -> `read_transcript` for only
    the sessions that actually matter to the question being asked, instead of
    reading every transcript blind; `session_ids` matches `session_info`'s own
    `local_<uuid>` id format directly, no translation needed.

    Returns `{"rows": [...], "uncached_project_uuids": [...],
    "unresolved_space_ids": [...], "space_source_notes": {...}}`. Each row:
    `session_id`, `cloud_project_uuids`/`cloud_project_names` (parallel lists
    -- a session can declare more than one cloud Project; a `None` name means
    that uuid has no local `.project-cache` entry yet), `space_id`/
    `space_name`, `local_folder_paths` (always populated), `is_archived`.
    Omit `session_ids` to get every local session's membership row.

    Space membership comes from the app's own record -- the Space id Desktop
    wrote on the session, resolved through `spaces.json`. A chat the app
    recorded no Space for falls back to matching its folder against Space
    folders, which is a guess. Every row says which it is:

    - `space_source` -- `app_record`, `inferred_from_folder`, or `none`;
      `space_source_note` is the same thing in a sentence you can show a
      user, and `space_from_app_record` is the boolean shortcut.
    - `space_matched_folder` -- the Space folder a guess matched, or `None`
      for a recorded attribution.
    - `space_label` -- always readable: the Space's real name when it
      resolves, otherwise a plain-English explanation ("a Space Claude no
      longer lists..."), never a bare uuid. `space_resolved` /
      `space_resolution` say why.

    Report inferred attributions as guesses, separately from recorded ones.
    A reorganization suggestion built on a guess and presented as fact tells
    the user something false about their own workspace.

    Rows here match `list_local_workspace`'s `local_sessions` field for
    field -- both read the same join.

    `since`/`until` scope this run to a time window. Both accept a relative
    age ("90d", "6m"), an ISO date ("2026-01-01"), or epoch milliseconds;
    omit both for the full history. Prefer the relative form when today's
    date isn't certain. The response's `time_window` reports what the
    bounds resolved to, how many records were excluded, and any bad-bound
    errors -- repeat that alongside any count taken from this response,
    since a windowed count presented as an account total is simply wrong.

    A bound written as a bare date is anchored at midnight **UTC**, while
    every date and weekday this plugin shows a person is local -- so on a
    machine that isn't on UTC the window edge lands part-way through a
    local day. `time_window.boundary_notes` (repeated in `notes`) says so
    and gives the exact bound to pass instead
    ("2026-01-01T00:00:00-05:00"); `since_local`/`until_local` show where
    the bounds actually fell on the reader's clock. Quote a window bound as
    UTC, or quote its local form -- never as a bare date with no zone."""
    return local_data.build_project_membership(session_ids, since, until)


@mcp.tool(annotations=_READ_ONLY)
def get_instructions_inventory(
    max_chars: int = 4000,
    since: str | None = None,
    until: str | None = None,
    max_memory_chars: int = 2000,
) -> dict:
    """Every layer of standing context on this machine in one place -- the
    account's global custom instructions, each Space's `instructions`, each
    cached cloud Project's `prompt_template`, **and Claude's own local
    memory files** -- with the character counts, session reach, and
    line-level duplication needed to audit them for bloat. This is the
    Desktop/Cowork analogue of what Claude Code's `/doctor` does to
    `CLAUDE.md`; see references/workspace-checkup.md for how to turn this
    into findings.

    **This is the single place to get memory.** "Review my Claude memory"
    is answered here, on a plain local run, with no account export needed.
    Memory lives on this tool rather than one of its own because it is the
    fourth layer of the same thing every other layer here measures -- text
    carried into a session that the user didn't type this turn -- keyed by
    the same Space ids and Project uuids, so it shares this tool's name
    join and session-reach counts, and so the strongest finding it produces
    (memory restating what the instructions already say) can be computed at
    all. Don't look for a second memory tool; there isn't one.

    Returns:
    - `global_instructions` -- `{"variants": [...], "variant_count": int}`.
      Global instructions are stored per-session rather than in one account
      file, so distinct texts are grouped into variants sorted by most
      recent session activity. `variants[0]` is the best guess at what's in
      force now; more than one variant means the text was *edited over
      time*, not that two are competing -- don't report it as a conflict.
    - `spaces` / `cloud_projects` -- one entry each with `char_count`,
      `line_count`, `session_count` (how many local sessions this text
      actually reached), and `duplicates_global` (lines shared verbatim
      with the current global text -- paid for twice on every turn).
    - `repeated_across_projects` -- lines appearing verbatim in two or more
      Spaces/Projects; the inverse signal, standing context pasted into
      Project after Project that probably belongs in global instructions or
      a Skill.
    - `memory` -- every memory file on this device, attributed to its
      owner: `account` (the account-level `memory/` folder), `spaces` (one
      entry per Space memory folder, named through `spaces.json`), and
      `cloud_projects` (one per cached Project's `memory.md`). Each owner
      carries `label`, `session_count`, `char_count`, its files (with
      `text`, last-modified in both UTC and local form, and
      `days_since_modified`), and `duplicates_global` -- the same overlap
      check the instruction layers get, because memory restating the
      standing instructions is paid for twice. `repeated_across_memory` is
      the cross-topic-drift signal: lines remembered under two or more
      different Spaces/Projects.

      Where an id can't be resolved to a name, `label` says what the thing
      is ("a Space that is no longer listed in spaces.json") instead of
      printing a raw uuid at a user who has never seen one. That includes
      an owner Claude lists with **no name at all**, which is reported as
      exactly that and never as a deletion -- a Space sitting unnamed in
      the sidebar has not been deleted, and `label` is never empty, so
      nothing downstream falls back to the id.

      **Local memory and export memory both exist.** These files are the
      copy *this device* holds, written by the app as it runs and only as
      current as the last time it ran here. An account data export also
      contains memory and comes from the account itself, so where the two
      disagree **the export wins**. The local files are what a default,
      export-free run can see and are enough for a same-day review -- say
      which source a finding came from. No memory files present is not
      evidence the account has none.

      Memory is deliberately excluded from `totals`: it loads on demand
      rather than on every turn, so folding it in would overstate what a
      session actually pays for.
    - `totals` -- `always_loaded_char_count` (what every session pays for)
      and `worst_case_char_count` (global plus the largest single
      Project/Space text).
    - `notes` -- data-quality caveats to repeat before presenting findings.

    `max_chars` caps each returned instruction text; `max_memory_chars`
    separately caps each memory file's `text` and defaults lower, because
    memory is bulkier and there are more files of it. Both are clamped to
    zero or more -- a negative cap used to return essentially the whole
    text while still reporting itself as truncated. Every `char_count` is
    the true uncapped length regardless. This tool reports counts, content
    and overlap -- deciding what should be trimmed is the caller's
    judgment, not this tool's.

    Memory-file dates come back in both forms (`modified_at_local_iso`,
    `modified_at_local_date`, `modified_at_local_weekday` on this machine's
    clock; `modified_at_iso` in UTC), with the response's `timezone` block
    naming the zone. Quote the local ones -- staleness is judged against a
    calendar the user actually reads.

    `since`/`until` scope this run to a time window. Both accept a relative
    age ("90d", "6m"), an ISO date ("2026-01-01"), or epoch milliseconds;
    omit both for the full history. Prefer the relative form when today's
    date isn't certain. The response's `time_window` reports what the
    bounds resolved to, how many records were excluded, and any bad-bound
    errors -- repeat that alongside any count taken from this response,
    since a windowed count presented as an account total is simply wrong.

    A bound written as a bare date is anchored at midnight **UTC**, while
    every date and weekday this plugin shows a person is local -- so on a
    machine that isn't on UTC the window edge lands part-way through a
    local day. `time_window.boundary_notes` (repeated in `notes`) says so
    and gives the exact bound to pass instead
    ("2026-01-01T00:00:00-05:00"); `since_local`/`until_local` show where
    the bounds actually fell on the reader's clock. Quote a window bound as
    UTC, or quote its local form -- never as a bare date with no zone.

    Because the global text is stored per-session, a window here does more
    than trim: it answers "what were my standing instructions during that
    period, and how many sessions did each reach" -- a question no view in
    the app can answer.

    `since`/`until` do **not** scope the `memory` block. A memory file is
    current state with one last-modified date, not a record of what was
    remembered during a period; only the `session_count` on each memory
    owner is windowed. Say which is which when reporting a windowed run."""
    return local_data.build_instructions_inventory(
        max_chars, since, until, max_memory_chars
    )


@mcp.tool(annotations=_READ_ONLY)
def get_plan_usage_history(
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """What the plan was **actually** consuming, from Claude Desktop's own
    rolling record of usage-window fullness. This is the Desktop
    counterpart of the Claude Code CLI's `/usage` -- and the only ground
    truth this plugin has about real consumption. Every other usage signal
    it can produce (message counts, session counts, model tallies) is a
    proxy; when they disagree with this, this wins.

    **Percentages are shares of the user's own plan limits** -- how full
    the five-hour window and the seven-day window were. They are not
    dollars, not tokens, and not a bill. A business user reading "100%"
    must understand it as "you'd run out here", never as an amount of
    money. Never convert these to cost.

    **Two limits, reported equally.** The five-hour window and the
    seven-day window are separate caps and both are analysed here in the
    same shape. For a business user the seven-day one usually matters
    more: running out of the five-hour window costs the rest of an
    afternoon, running out of the weekly one can cost the rest of the
    week. Never report one without the other.

    **Every timestamp comes back twice.** `*_at_iso` is UTC, for
    comparison and joining; `*_local_iso`, `*_local_date`, `weekday` and
    the `timezone` block are **this machine's own timezone**, and are the
    ones any sentence shown to a person must be built from. A weekday
    computed in UTC is a wrong answer for someone reading their own
    calendar, not a caveat on a right one.

    Returns:
    - `current` -- the most recent reading, with `age_hours`. If that age
      is large the app simply hasn't been open since; it is a last-known
      value, not a live one.
    - `peaks` -- highest `five_hour`/`seven_day` reading in the window,
      each with the UTC and local timestamp it happened and the local
      date and weekday it fell on.
    - `limit_events` -- `five_hour` and `seven_day` sub-blocks, identical
      in shape, each holding the runs where that window sat at or near its
      limit (>= 90%) collapsed into ONE event apiece, with start/end
      timestamps (UTC and local), local weekdays, duration, and peak.
      `hit_ceiling` distinguishes "you ran out" (100) from "you got
      close". Sampling is every ~15 minutes, so one busy afternoon would
      otherwise look like a dozen separate limit hits -- it doesn't here.
      The two windows use different run-break thresholds (`run_break_hours`
      says which) because five hours of quiet empties the five-hour window
      while the seven-day window barely moves overnight.
    - `daily_high_water` -- the highest each window reached per **local**
      calendar day, with the weekday name and the day's `utc_offset`.
      This is what a weekly-rhythm claim ("you run hot on Mondays") should
      be built from.
    - `extra_usage` -- whether purchased extra usage was ever consumed and
      when. The field is absent from most samples rather than zero, so
      `samples_missing_field` is reported alongside: "never above zero on
      the samples that recorded it" is the only provable claim.
    - `coverage` / `cadence` -- how much history is on this machine, how
      much of it this response covers, how often it sampled, and the
      largest gaps. **A gap means the app was closed or the machine
      asleep, NOT that usage was zero.** Reading a gap as idleness turns a
      holiday into "you barely use Claude"; this is the single easiest way
      to misread this data. `coverage` also carries `unusable_sample_count`
      and `unusable_reading_count` -- entries and individual readings
      dropped as unreadable, so a partly-corrupt file shows as one rather
      than as quietly lower numbers. Note what `coverage` does *not* say:
      why the record starts where it does. That could be this machine's
      install date, the date the app started keeping the file, or an
      internal trimming rule -- nothing on disk distinguishes them, so
      report the earliest date and attribute no cause to it.
    - `organizations` -- per-organization figures when more than one
      appears. Labelled opaquely ("organization 1"): the file identifies
      them only by account uuid, which this tool does not surface.

    There is no raw sample series and no `max_samples`. Both were removed:
    nothing consumed the series, it made up most of the response, and its
    even-spacing downsampler dropped extrema, so a chart drawn from it
    could omit every limit hit and contradict `peaks` in the same
    response. Use the aggregates above.

    `since`/`until` scope this run to a time window. Both accept a
    relative age ("90d", "6m"), an ISO date ("2026-01-01"), or epoch
    milliseconds; omit both for everything on disk. Prefer the relative
    form when today's date isn't certain. The response's `time_window`
    reports what the bounds resolved to, how many samples were excluded,
    and any bad-bound errors -- repeat that alongside any figure taken
    from this response.

    Unlike every other tool here, samples are matched on the sample
    timestamp falling **inside** the window, not on interval overlap. A
    usage sample is a point in time, not a span, so there is nothing to
    overlap with -- which also means a window narrower than the sampling
    gap can legitimately come back empty.

    This is a local file the app maintains for itself, covering **this
    machine only** and only while Desktop was running: usage from the
    phone, the web app, or a second computer is not in it, and it is not
    account history. `available: false` means the file isn't there (fresh
    install, or Claude is rarely opened here) -- that is not evidence of
    low usage, and no proxy count should be substituted for it."""
    return local_data.build_plan_usage_history(since, until)


@mcp.tool(annotations=_READ_ONLY)
def get_desktop_settings() -> dict:
    """Account-level Claude Desktop settings in one consolidated, redacted
    view: standing folder grants, app-wide permission toggles, installed
    extensions, and first-launch/locale/theme. Backs the access-scope,
    capability-inventory, and settings-hygiene areas of the checkup in
    references/workspace-checkup.md -- areas that until now had only
    per-session data, which cannot see any of this.

    Why each block can't be derived from session data:

    - `access_scope` -- `localAgentModeTrustedFolders`,
      `remoteFolderConsentMemory` and `remoteSessionFolderGrants` are
      **standing** grants: approved once, and in force for future sessions
      until removed in Settings. A folder trusted months ago and never
      opened since appears in no session's `user_selected_folders` at all,
      which makes this a far stronger scope-creep signal than the
      per-session list. Paths come back in full (they're the actionable
      part), each with `looks_broad` and a plain-language
      `breadth_reasons` when the grant covers a whole category of files --
      a home folder, a full cloud-storage account root, a whole code
      directory -- rather than one piece of work. Flags only: there is no
      score here, because how much a broad grant matters depends entirely
      on what's inside it, and judging that is the skill's job.
      `access_scope.broad_grants` is the figure to quote: distinct paths,
      deduplicated across settings, with standing grants and
      session-scoped grants counted separately. Do not sum the
      per-setting `broad_count`s yourself -- that counts a folder listed
      in two settings twice, and it sweeps session-scoped grants (which
      do not carry forward on their own) in under the word "standing".
      Each entry under `remote_session_folder_grants.by_session` now
      carries the **Space** its `session_01...` id belongs to, resolved
      through `remote-session-spaces.json` and then `spaces.json`. Quote
      `space_label` -- it is the Space's real name when the id resolves,
      and a plain-language explanation of why it didn't when it doesn't
      ("a Space that is no longer listed in spaces.json", or "a Space that
      spaces.json lists without a name", which is a different situation and
      must not be reported as a deletion). Never print the
      raw `session_id` at the user: it names nothing they have seen, and a
      report that shows it has not answered "which Space is this".
    - `scheduled_tasks` -- read from `scheduled-tasks.json`, a file
      earlier research recorded as not existing because scheduled tasks
      run server-side. It does exist. What comes back is the task count,
      the `recordedSkips` identifiers, the two boundary-stamp flags, and
      any top-level field this plugin doesn't recognize.
      **`task_entry_shape_verified` is `False` and means it**: the array
      has never been observed populated on a real install, so entries are
      returned verbatim (minus redaction, and minus anything over the
      per-entry size cap -- a task carries the prompt text it runs, so one
      entry can be arbitrarily large; `oversized_task_count` says how many
      came back as a measurement of themselves instead) and no field inside
      one is named or promised -- read them, don't parse them. An **empty**
      list means
      exactly "this device records no scheduled tasks": not that the
      feature is absent, and not that the account has none, since the work
      runs server-side. Cross it with the `coworkScheduledTasksEnabled` /
      `ccdScheduledTasksEnabled` toggles in `settings` before saying
      anything about whether the user schedules work.
    - `extensions` -- **authoritative for "installed"**, which
      `enabled_mcp_server_labels` in `list_local_workspace` is not. An
      extension installed and never enabled in any session appears nowhere
      in session data, so "installed months ago, never used once" -- the
      most actionable capability finding available -- is invisible without
      this. Cross this list against `enabled_mcp_server_labels` (switched
      on) and `mcp_servers_invoked` (actually called). Per extension: id,
      version, source, signature status, install date (epoch and ISO), and
      the manifest's *size*; the manifest itself is measured and dropped,
      never returned. **`signature_status: unsigned` is normal and is not
      a security finding on its own** -- Anthropic's own first-party
      chrome-control extension reports it, and it is the only value ever
      observed on a real install. This tool cannot verify any extension's
      provenance, so never tell a user an extension is unsafe on that
      basis; the field is a drift signal for maintainers.
    - `settings` -- the app-wide toggles that no session file carries:
      `allowAllBrowserActions`, `dispatchCodeTasksPermissionMode`,
      `bypassPermissionsGateByAccount`, the scheduled-task and web-search
      switches, and so on. Keyed by their on-disk names so a finding can
      name the exact setting the user has to change. Per-account maps have
      their account uuids replaced with opaque labels.
    - `unrecognized_settings` -- every key this plugin doesn't know by
      name, with values summarized rather than dumped. It covers **both**
      namespaces in `claude_desktop_config.json`: unknown keys inside
      `preferences`, and unknown keys at the file's own top level -- so an
      `mcpServers` block, which some installs have and some don't, really
      does show up here rather than being dropped. Kept deliberately:
      Claude Desktop ships new settings regularly, and silently dropping
      unknown keys is how a reader like this goes stale without anyone
      noticing. Don't turn an unrecognized key into a finding on its own --
      it's a drift signal for maintainers.
    - `app_config` -- first launch (the denominator for any per-week rate;
      forty chats means something different in a week-old install than in
      a year-old one), locale, and theme. Read through a strict three-key
      allowlist, because that same file also holds live OAuth token
      caches; nothing else in it is ever touched, and every field is
      re-checked against the secret-key filter on the way out.
      `account_tenure_days` is derived **only** from a first-launch value
      that renders as a real date; when it doesn't,
      `first_launch_at_usable` is `False`, the tenure is null, and a note
      says the rates in this run have no verified start date behind them.
      Don't substitute one.

    **Dates come back twice.** `*_local_iso`, `*_local_date` and
    `*_local_weekday` are on this machine's clock and are the ones to quote
    (the top-level `timezone` block names the zone); the `*_iso` value
    beside each is the same instant in UTC, for comparison and joining
    only. This tool used to emit UTC alone, which on a machine west of UTC
    reported a first-launch date wrong by a calendar day and a weekday --
    in the one figure this docstring tells you to quote.

    `unusable_number_count` is how many values were dropped because the
    file held `NaN`/`Infinity` where a number belonged. Dropped, not
    clamped -- there is no honest figure to substitute for a corrupt one --
    so they read as "not recorded", and a nonzero count comes with a note
    saying a settings file is at least partly corrupt.

    **No `since`/`until`.** These are current-state settings, not a time
    series -- there's no record of when a toggle was flipped or a folder
    trusted, so a window would have nothing to filter and would imply a
    precision this data doesn't have. A run scoped elsewhere is not scoped
    here; say so if the rest of the report is windowed.

    Every section degrades to `available: false` with a note when its file
    is missing -- a fresh install, a non-macOS layout, or a session without
    access to the app-data folder is normal, not an error. Check
    `check_data_access`'s `data_files` first if this comes back empty."""
    return local_data.build_desktop_settings()


@mcp.tool(annotations=_WRITE_LOCAL)
def locate_export_download(
    search_dirs: list[str] | None = None, out_dir_base: str | None = None
) -> dict:
    """Look for an already-downloaded claude.ai export zip and unpack it --
    used by the scheduled-recheck flow in
    references/export-acquisition.md after a browser tool has (re)visited
    the export settings page and triggered a download. Matches by content
    (a `conversations.json` member inside the zip), never by filename --
    that naming isn't a documented convention. Defaults `search_dirs` to
    this OS's Downloads folder; unpacks into its own subdirectory (named
    after the zip) under out_dir_base, defaulting to a plugin state
    subdirectory.

    Returns one of:
    - `{"found": false}` -- no candidate yet; not an error, the download
      may simply not have landed. The caller should try again later
      rather than treat this as failure.
    - `{"found": true, "ambiguous": true, "candidates": [...]}` -- more
      than one zip looks like an export; ask the user which one rather
      than guessing.
    - `{"found": true, "ambiguous": false, "export_dir": ..., "zip_path":
      ...}` -- unpacked and ready to hand straight to `parse_export`."""
    resolved_search_dirs = search_dirs or [str(Path.home() / "Downloads")]
    resolved_out_dir_base = out_dir_base or str(Path(_default_state_dir()) / "export-downloads")
    return export_data.locate_export_download(resolved_search_dirs, resolved_out_dir_base)


@mcp.tool(annotations=_WRITE_LOCAL)
def parse_export(
    export_dir: str,
    out_dir: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Parse a claude.ai account data export (Settings > Account > Export
    Data, unzipped -- must contain conversations.json) into
    conversations.jsonl / projects_index.json / memory_context.md /
    stats.json under out_dir (defaults to a plugin state subdirectory),
    instead of holding the raw export in context. Each conversation record
    in conversations.jsonl includes a `models_used` tally (model id ->
    message count) when the export schema happens to carry one. Check the
    returned stats.notes for data-quality caveats (thin corpus, missing
    project links, no per-message model field, etc.) before analyzing
    further.

    `since`/`until` scope this run to a time window. Both accept a relative
    age ("90d", "6m"), an ISO date ("2026-01-01"), or epoch milliseconds;
    omit both for the full history. Prefer the relative form when today's
    date isn't certain. The response's `time_window` reports what the
    bounds resolved to, how many records were excluded, and any bad-bound
    errors -- repeat that alongside any count taken from this response,
    since a windowed count presented as an account total is simply wrong.

    A bound written as a bare date is anchored at midnight **UTC**, while
    every date and weekday this plugin shows a person is local -- so on a
    machine that isn't on UTC the window edge lands part-way through a
    local day. `time_window.boundary_notes` (repeated in `notes`) says so
    and gives the exact bound to pass instead
    ("2026-01-01T00:00:00-05:00"); `since_local`/`until_local` show where
    the bounds actually fell on the reader's clock. Quote a window bound as
    UTC, or quote its local form -- never as a bare date with no zone.

    The window is applied before anything is written, so the paged files on
    disk and every number in `stats` describe the same period. Conversations
    are matched on overlap of `created_at`..`updated_at`, so a chat begun
    before the window but continued inside it still counts."""
    resolved_out_dir = out_dir or str(Path(_default_state_dir()) / "export-parsed")
    return export_data.parse_export(export_dir, resolved_out_dir, since, until)


@mcp.tool(annotations=_WRITE_LOCAL)
def render_dashboard(plan: dict[str, Any], out_path: str | None = None) -> dict:
    """Render an already-reasoned-about reorganization plan into a
    self-contained HTML dashboard -- this tool does no clustering or
    analysis itself, it only presents structured input the caller
    produced. `plan` shape:

    {
      "title": str, "subtitle": str (optional),
      "data_sources": [str, ...] (e.g. ["Local Desktop/Cowork",
        "Account export (2026-07-20)"]),
      "time_window": str (optional but strongly encouraged whenever the
        run was scoped -- a human-readable rendering of the window the
        data tools resolved, e.g. "2026-05-01 to 2026-07-29 (last 90
        days)". Rendered prominently under the header; omitting it on a
        scoped run leaves every count below looking like an account
        total. Omit only when the run really did cover all history),
      "stat_tiles": [{"label": str, "value": str, "status": "good"|
        "warning"|"critical" (optional)}],
      "start_here": [{"title": str, "action": str, "where": str
        (optional, the exact place to make the change -- e.g. "Global
        custom instructions" or a Project name), "impact": str
        (optional, one short phrase on why it's worth doing)}]
        (optional but strongly encouraged: the ranked shortlist of the
        highest-impact fixes this run found, most important first,
        rendered at the top of the dashboard before every detailed
        section. Aim for about five. Every other section stays complete
        -- this one exists so a reader meets the shortlist before the
        full inventory, not so findings get dropped. Each entry should
        point at something detailed further down rather than introducing
        a finding that appears nowhere else. Omit entirely if the run
        genuinely found nothing worth prioritizing),
      "projects": [{"name": str, "description": str,
        "instructions": str (optional, the Project's custom
        instructions), "chats": [str, ...], "files": [str, ...],
        "status": str (optional, e.g. "new"/"existing"/"rename")}],
      "model_usage": [{"model": str, "count": int}] (optional, sorted
        descending by count -- from the usage & best-practices lens),
      "findings": [{"title": str, "severity": str (optional, "info"/
        "warning"/"critical"), "evidence": [str, ...] (optional),
        "recommendation": str}] (optional, one entry per usage/
        best-practices pattern actually found -- model right-sizing,
        automation candidates, prompting/context patterns; see
        references/usage-efficiency.md),
      "recommendations": [{"title": str, "kind": "existing"|"custom",
        "item_type": "skill"|"plugin"|"connector", "source": str
        (optional, only for "existing" -- which marketplace/registry it
        came from), "evidence": [str, ...] (optional), "rationale": str}]
        (optional, one entry per skill/plugin/connector recommendation --
        "existing" means install/connect something that already exists
        publicly, "custom" means it's worth building from scratch; see
        references/usage-efficiency.md's "Recommending skills, plugins &
        connectors" section),
      "leftovers": [{"name": str, "note": str}],
      "notes": [str, ...] (data-quality caveats),
      "generated_at": str (optional ISO timestamp; defaults to now)
    }

    Writes to out_path (defaults to a plugin state subdirectory) and
    appends a stat_tiles snapshot to a local history log every call, so
    re-running this later shows progress over time in the dashboard
    itself. Returns {path, html, history_runs} -- if an Artifact-
    publishing capability is available in this session, hand it `html`
    directly to publish/update a live version; otherwise tell the user
    the dashboard was saved at `path` and they can open it in a browser."""
    resolved_out_path = out_path or str(Path(_default_state_dir()) / "dashboard.html")
    history_path = str(Path(_default_state_dir()) / "history.jsonl")
    return dashboard.render_dashboard(plan, resolved_out_path, history_path)
