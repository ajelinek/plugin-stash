# claude-usage-analyzer 0.5.0 — Setup & Effectiveness Checkup

**Status:** implemented (see §11 for what shipped and §13 for what the
implementation changed about this design)
**Date:** 2026-07-30, implemented 2026-08-01
**Current version:** 0.4.0 → target **0.5.0**
**Origin:** grilling session + deep research (sources restricted to 2026-04-29 onward)

This document is the agreed output of a design interview. It is the
implementation brief — everything below was explicitly decided, and the
decisions that overrode a recommendation are called out in
[§10](#10-decision-log-and-overrides).

Kept outside `plugins/` deliberately: Claude Desktop copies the plugin
directory into its cache, and design docs should not ship to clients.

---

## 1. Goal

Not usage telemetry. **Effectiveness of utilization, and whether the setup
is correct.** Concretely, the user asked for:

- Global instructions, Space instructions, Project instructions — all layers
- **Memory usage and management**
- Each Project having a good **name, description, and instructions**
- Understanding **what files are being accessed**
- Understanding **which connectors are being used**
- Understanding **which skills are being used**
- Identifying **skills and schedules worth building**
- All of it **without overwhelming the user**

---

## 2. Structural decisions

| Decision | Outcome |
|---|---|
| Scope of work | Docs **and** tool/server changes where a practice is otherwise unreachable |
| Lens count | Stays at **three**. No fourth lens. |
| Where new checks live | `workspace-checkup` expands from "standing instructions" into a full **setup & effectiveness checkup** |
| Reflect / 4D AI Fluency | **Abandoned entirely** (see §9) |
| Presentation | Analyze everything; lead with a **short ranked fix list**, full detail below |

The `reorganization` and `usage-efficiency` lenses keep their current shape.
The cross-lens rule already in the docs ("report a finding in one lens or the
other, not both") still applies and gets more load-bearing as checkup grows.

---

## 3. Confirmed bugs to fix

All three verified directly against code and against 67 real session files
on the maintainer's machine.

### 3.1 `effort` is a dead field — **0/67**

`local_data.py::read_local_sessions` reads `data.get("effort")`. That key
does not exist in any session file. The real key is **`effortOverride`**,
present in **40/67**.

Impact: `effort` has been `None` in every `list_local_workspace` response
ever returned, while `SKILL.md` Step 5 and `usage-efficiency.md` both
instruct Claude to right-size against `default_model`/`effort`.

Fix: read `effortOverride`. Keep the response field name `effort`, or rename
and update both docs — either is fine, but the docs and code must agree.
Absent on 27/67 sessions, so it stays legitimately optional.

### 3.2 Docs promise local fields that do not exist

`SKILL.md` Step 5 and `usage-efficiency.md` both state the lens works from
`first_human_message`, `keywords`, `tool_names`, `message_count` across
**both** sources. Those four fields exist **only** in `export_data.py`.

Local sessions today carry only: `id`, `title`, `created_at`,
`last_activity_at`, `is_archived`, `user_selected_folders`,
`user_selected_project_uuids`, `default_model`, `effort` (broken),
`memory_enabled`, `skills_enabled`, `plugins_enabled`,
`custom_instructions`, plus `models_used`/`transcript_event_count` joined
from transcripts.

Fix: §4 and §5 below make most of these real. Any that remain unavailable
must be described accurately in the docs rather than promised.

### 3.3 `memories.json` parser is written against the retired memory model

`export_data.py::render_memory_context` uses
`_GLOBAL_MEMORY_KEYS = ("memory", "global_memory", "account_memory", "narrative")`
and its comment describes *"Claude's own synthesized narrative, verbatim."*

Memory was **re-architected on 2026-07-10** into individual categorized
entries that Claude reads and updates during conversations — replacing
synthesized summaries. The parser is very likely reading a shape that no
longer exists.

Fix: update for the categorized-entry schema, keeping a defensive fallback
(it already dumps raw JSON when it cannot match — preserve that). Verify
against a real current export before finalizing.

---

## 4. New session fields to read

All verified present across 67 real sessions. **No transcript walking
required** — this is session metadata already on disk.

| Field | Present | Purpose |
|---|---|---|
| `permissionMode` | 57/67 | Approval-mode cost lever — Auto costs **more** than Manual or Skip |
| `effortOverride` | 40/67 | Fixes §3.1 |
| `enabledMcpTools` | 67/67 | Which connectors/tools were **enabled** |
| `remoteMcpServersConfig` | 67/67 | Which remote connectors are configured |
| `pluginInstallPaths` | 58/67 | Which plugins are installed |
| `slashCommands` | 67/67 | Commands available (~55 per session) |
| `initialMessage` | 67/67 | The first human message — makes `first_human_message` real locally |
| `egressAllowedDomains` | 67/67 | Network scope |
| `webFetchAllowedUrls` | 38/67 | Fetch scope |
| `fsDetectedFiles` | 32/67 | Files detected |
| `systemPrompt` | 67/67 | **Character count only** (~49KB) |
| `memoryGuidelinesTemplate` | 67/67 | **Character count only** (~13.5KB) |

**Critical distinction:** `enabledMcpTools` is what was *enabled*, not what
was *invoked*. Actual usage still requires §5.

**Sensitivity:** `systemPrompt` and `memoryGuidelinesTemplate` are read for
**size only**, never content. Their content is largely Anthropic scaffolding
the user cannot edit, so findings there would not be actionable. Existing
`redact()` handling must cover the new fields — several
(`egressAllowedDomains`, `webFetchAllowedUrls`, `fsDetectedFiles`, `cwd`)
can carry identifying paths and hosts.

**Caveat:** field presence verified on **one machine only**. Fields at 32/67
and 38/67 are not yet fully understood; treat absence as normal and never
let a missing field produce a false finding.

---

## 5. Transcript pass extension

`read_cowork_session_transcripts` already walks every nested JSONL and is
already called by `list_local_workspace`. Extend that same pass — marginal
I/O cost is near zero — to also extract:

- **Tool names actually invoked** — the real usage signal for §6.4
- **Message count**
- **File paths touched** — required for the scope-creep check (§6.3)

`initialMessage` (§4) covers first-human-message, so it does not need
extraction here.

**Line-cap problem:** `_MODEL_TALLY_MAX_LINES` currently bounds the scan.
Taking the first N lines biases any distribution toward the start of long
sessions — exactly the long agentic sessions Cowork is built for. Either
raise the cap for these signals or sample across the file. Do not silently
truncate.

---

## 6. The expanded checkup — new checks

Every check keeps the lens's existing evidence bar: **a real recurring
pattern, never a one-off, never a decorative finding to fill a section.** An
honest "this looks fine" remains a good result.

### 6.1 Memory (new)

Full content audit. Memory content is **export-only** — local data has the
on/off flag but no entries.

- Stale or contradicted entries
- Entries duplicating what standing instructions already say (paid for twice)
- Cross-topic pollution inside a Project's memory pool
- Per-project memory isolation — memory never crosses project boundaries
- Settings: memory on/off per surface; Cowork memory is **not** connected to
  chat memory
- Two documented traps to surface as advice:
  - **Deleting a conversation does not delete the memory it produced**
  - **Incognito chats are still included in Team/Enterprise data exports**
- Hygiene actions: review/edit at Settings → Capabilities; **pause** keeps
  existing entries and stops new ones; **reset** permanently deletes
  everything including project memories and is irreversible

Memory entries are the most sensitive data in the corpus — Claude's
synthesized profile of the user. The existing "Handling sensitive content"
rules apply in full: evidence is a short quoted line, never a block dump.

### 6.2 Project and Space quality (new)

Data is fully available today: Spaces carry `name`/`description`/
`instructions`/`folders`; cloud Projects carry `name`/`description`/
`prompt_template`.

**Grade on concrete defects only, and write the replacement text where a
defect is found.** Stay silent on Projects that are already fine.

Defects to catch:
- No description
- No instructions
- A name that does not describe what its chats are actually about
- A description that contradicts the actual content (drift)

Do **not** propose rewrites for every Project regardless of state — that
produces twenty proposals on a twenty-Project account, which is the
overwhelm this design is avoiding.

### 6.3 Access scope (new)

Grounded in [Use Claude Cowork safely](https://support.claude.com/en/articles/13364135-use-claude-cowork-safely)
(help center shows a relative stamp only — "over 2 weeks ago", so roughly
mid-July 2026). Quoted guidance:

> "Consider creating a dedicated working folder for Claude rather than granting broad access"
> "Be cautious about granting access to sensitive information like financial documents, credentials, or personal records"
> "Review what access you've granted, and consider whether that level of access is appropriate"
> "Watch for unexpected patterns: Is Claude accessing files or websites you didn't mention?"

All four checks are in scope:

1. **Broad-grant detection** — a Space or session bound to a home directory
   or drive root rather than a dedicated working folder
2. **Review-your-access** — present every folder each Space and session can
   reach, in plain language. This is presentation, not judgment.
3. **Sensitive-folder name heuristic** — flag granted folders whose names
   suggest credentials, financial documents, tax records. **Accepted
   limitation:** this is pattern-matching on names; it will occasionally
   alarm on something harmless and occasionally miss something real. Word
   findings accordingly — never assert, always "worth a look."
4. **Scope creep** — compare file paths actually touched (§5) against what
   was granted and asked for. The most expensive check here and the reason
   §5 extracts file paths.

`egressAllowedDomains` and `webFetchAllowedUrls` extend the same idea to
network reach.

### 6.4 Capability inventory — installed vs. actually used (new)

Cross what is installed/enabled (§4: `enabledMcpTools`,
`remoteMcpServersConfig`, `pluginInstallPaths`, `slashCommands`) against
what was actually invoked (§5: tool names). Where the harness exposes
`list_skills` / `list_plugins` / `list_connectors`, use them too — the
existing guidance in `usage-efficiency.md` to prefer native harness tools
over duplicated plugin logic still stands.

Three findings fall out:

- **Installed but never invoked** — clutter; the common case on a mature
  account
- **Invoked constantly** — worth promoting to Project-level setup
- **Repeatedly needed but never installed** — the capability gap

This feeds, and must stay consistent with, `usage-efficiency.md`'s existing
*already available* vs. *worth building custom* labelling. That section's
confirmed boundary also stands: **there is no callable tool for raw MCP
connector auth status** — do not infer it from `connected: true/false`.

### 6.5 Cost levers (new)

- **`permissionMode`** — Auto consumes **more** usage than Manual or Skip,
  because of the extra safety-checking pass. Counterintuitive and
  essentially uncovered elsewhere. Anthropic also advises staying actively
  engaged rather than relying on Auto for financial or sensitive-
  communication work.
- **Effort right-sizing** — effort is a five-level user-facing dial
  (low / medium / high / xhigh / max), high being the default. This is the
  single largest un-analyzed cost/quality lever. Depends on §3.1.
- **Surface choice** — Cowork "consumes significantly more tokens than chat
  due to computational intensity." Simple questions belong in chat. The
  documented remedy when hitting limits is batching related work into a
  single session.

---

## 7. Documentation freshness corrections

Research was restricted to sources dated 2026-04-29 or later. These
correct or add to existing doc content.

**Corrections — existing guidance is now wrong or stale:**

| Item | Correction | Date |
|---|---|---|
| Memory model | Real-time categorized entries, not ~24h synthesized summaries. Any "wait a day for memory to settle" advice is wrong. | 2026-07-10 |
| Scheduled tasks | Run **remotely**; the machine no longer needs to be awake. Exception: tasks needing local files/apps still run locally. | 2026-07-07 |
| Auto-compaction | Long conversations auto-compact, it **does not consume usage tokens**, and it requires code execution enabled. Softens the blanket "start a fresh chat" advice in `usage-efficiency.md`'s context-hygiene section. | in window |
| Context windows | Opus 5 / Sonnet 5 = 1M on paid plans; 4.6–4.8 generation = 500K. Any 200K assumption is wrong. | in window |
| Directory | Skills, connectors and plugins consolidated into **one** directory under Customize. Guidance pointing at three separate surfaces is out of date. | 2026-05-29 |
| Model lineup | Opus 5 (Jul 24), Sonnet 5 (Jun 30), Fable 5 (Jun 9), Opus 4.8 (May 28); **Sonnet 4 and Opus 4 retired Jun 15**. | various |

**Model lineup note:** `usage-efficiency.md` already instructs Claude to
look up the current lineup each session rather than hardcoding names. That
guidance is correct and survives the turnover — **keep it**. Verify no doc
hardcodes a specific model name or price anywhere.

**Additions — new guidance worth encoding:**

- **Project knowledge caching is the highest-leverage documented efficiency
  move.** Uploaded project documents are cached; only new or uncached
  portions count against limits. This gives `reorganization.md`'s existing
  per-project `files` field a real cost rationale it currently lacks.
- **Context is NOT shared across chats in the same project** unless it is in
  the knowledge base. A common and expensive user misconception — flag when
  behaviour shows the user assuming otherwise.
- **Plugin hooks and sub-agents are greyed out in the Chat tab** and only
  run in Cowork. A plugin evaluated on the wrong surface looks broken.
- **Cowork Projects cannot be shared**, even on Team/Enterprise. Classic
  Project sharing is Team/Enterprise only, and connectors are only available
  in private projects — so sharing a Project strips live connector data.
- **RAG auto-activates** as project knowledge nears the context limit on
  paid plans, expanding capacity rather than truncating. Aggressive pruning
  for context reasons is no longer necessary on paid plans.

**Dating caveat:** Anthropic's help center is Intercom-hosted and shows
relative stamps ("this week", "over 2 weeks ago") rather than absolute
dates. Only the Release notes article carries absolute dates. Items above
sourced from Release notes are high confidence; the rest were verified to
display a stamp inside the window.

---

## 8. Presentation

**Analyze everything; present with restraint.**

- Dashboard opens with a **"start here" section of roughly five
  highest-impact fixes** — each one line, its evidence, and one action.
- Full detail follows below for anyone who wants it.
- The existing history log means a re-run can show whether the top five
  actually got fixed. Preserve that.

This separates completeness of analysis from restraint in presentation,
which was the stated tension. Nothing analysed is discarded; it is ranked.

---

## 9. Explicitly out of scope

**Reflect recreation and 4D AI Fluency scoring — abandoned.**
Anthropic shipped Reflect on 2026-07-09 (Settings → Reflect) with the 4D
rubric (Delegation, Description, Discernment, Diligence). This was explored
and rejected: 4D scores the *human's practice*, not the *workspace's
configuration*, and the goal here is setup and effectiveness. Also dropped
with it: activity telemetry (peak hour, most active day, daily activity
chart) and proportional theme breakdown — telemetry, not diagnosis.

**Auditing existing scheduled tasks — dropped, confirmed boundary.**
Verified empirically: no schedule-shaped files exist anywhere under the
Claude app-data directory. The only Cowork caches on disk are feature flags
(`cowork-gb-cache.json`), policy limits, and an empty `artifacts.json`.
Consistent with the 2026-07-07 change making scheduled tasks execute
server-side. Document this as a confirmed boundary in `data-sources.md`, the
same way other confirmed boundaries are handled.

**Note:** this drops *auditing existing* schedules only. **Recommending new
scheduled tasks** as automation candidates stays in `usage-efficiency.md`.

**Unchanged non-goals** (already in `SKILL.md`): plan execution in the
claude.ai UI, full automation mining, Claude Code CLI analysis, and any
write/apply step. This lens proposes; it never changes anything.

---

## 10. Decision log and overrides

| # | Decision | Outcome |
|---|---|---|
| 1 | Scope of work | Docs **+ tools where needed** — as recommended |
| 2 | Relationship to Reflect | Initially "recreate the rubric over Cowork data"; **later reversed to abandon entirely** |
| 3 | Recap structure | New fourth lens — **moot**, recap abandoned |
| 4 | Recap data scope | All local **+ export** — *override of recommendation*; **moot**, recap abandoned |
| 5 | 4D expression | Three bands + evidence — **moot**, abandoned |
| 6 | Activity stats | Question superseded by the course correction; **abandoned** |
| 7 | Lens shape | Expand `workspace-checkup` — as recommended |
| 8 | Memory depth | Full content audit + fix stale parser — as recommended |
| 9 | Usage signal | Extend transcript pass, audit installed-vs-used — as recommended |
| 10 | Access hygiene | **All four checks** — *broader than recommended*; includes the false-positive-prone name heuristic and the expensive scope-creep check |
| 11 | Scheduled tasks | **Drop and document the boundary** — *override*; recommendation was to extend the browser flow |
| 12 | Project quality | Defect-based grading with rewrites — as recommended |
| 13 | Output discipline | Analyze all, lead with ~5 ranked fixes — as recommended |

**Course correction, recorded:** the session initially went down a
Reflect-recreation path. It was cut on the grounds that the goal is
effectiveness and setup correctness, not usage telemetry. Decisions 3–6 are
superseded and retained here only for traceability.

---

## 11. Implementation checklist

**Server / tools**
- [ ] Fix `effort` → `effortOverride` in `read_local_sessions`
- [ ] Add the §4 fields to `read_local_sessions`; sizes only for
      `systemPrompt` / `memoryGuidelinesTemplate`
- [ ] Extend `redact()` coverage to new path- and host-bearing fields
- [ ] Extend `read_cowork_session_transcripts` for tool names, message
      count, file paths (§5)
- [ ] Resolve the `_MODEL_TALLY_MAX_LINES` bias (raise or sample)
- [ ] Update `render_memory_context` for the categorized-entry schema (§3.3)
- [ ] Verify `parse_export` `stats.notes` still reports memory availability
      accurately after the schema change
- [ ] Extend `render_dashboard` for the "start here" ranked-fix section (§8)

**Skill docs**
- [ ] `workspace-checkup.md` — add §6.1–6.4 checks
- [ ] `usage-efficiency.md` — add §6.5 cost levers; correct the
      context-hygiene section for auto-compaction; verify no hardcoded model
      names
- [ ] `reorganization.md` — add the knowledge-base caching rationale to
      `files`
- [ ] `data-sources.md` — document the scheduled-task boundary; correct the
      claimed-but-absent local fields (§3.2); document the new §4 fields
- [ ] `SKILL.md` — update Step 5/6 to match reality; bump the skill version
      line to 0.5.0

**Release** (per repo `CLAUDE.md`)
- [ ] Bump `.claude-plugin/plugin.json` to `0.5.0`
- [ ] Dated `CHANGELOG.md` entry
- [ ] Regenerate `plugins/claude-usage-analyzer/uv.lock` **only if
      dependencies changed** — via the isolated `/tmp/lock-sim` procedure
- [ ] `uv run ruff check .` / `uv run pytest plugins/claude-usage-analyzer/tests`
- [ ] `claude plugin validate ./plugins/claude-usage-analyzer` and
      `claude plugin validate .`

---

## 13. What implementation changed about this design

Five things were wrong or unknown in the design above and were corrected by
checking real data rather than reasoning about it. Recorded because each one
is a case where the design would have shipped a bug.

**The memory bug was worse than described.** §3.3 said the parser was
written against a retired schema. In fact `memories.json` is a
single-element **list**, so the parser's `.get` call never ran at all and
every real export fell through to a 2,000-character raw-JSON dump. The
schema was recovered from two live exports rather than left as an open
item: `[{conversations_memory, project_memories: {uuid: str}, account_uuid}]`.
Per-project memory carries **no project name**, only the uuid — the old
code looked for `project_name`, which does not exist. Memory *is*
categorized post-July, but serialized as markdown with `**Bold**` headers,
so categories are recoverable by parsing headers.

**Three capability-field shapes were assumed wrong.** `enabledMcpTools` is
a dict keyed `local:<Server display name>:<tool>` with a **bool** value, so
a present key does not mean enabled, and the server segment is a display
label that does **not** join to the `mcp__<server>__<tool>` slug used in
transcripts. `pluginInstallPaths` are hashed temp directories whose
basenames are useless as names — readable plugin names come from
`slashCommands`' `<plugin>:<command>` prefix instead. `slashCommands` is a
list of plain strings, not objects.

**The line-cap fix chose sampling over raising.** §5 offered either. Raising
the cap only moves the bias; the scan now continues past the cap at a
stride, so tallies describe the whole file, with `transcript_sampled`
flagging it. On 81 real sessions nothing exceeded the cap, so this is
insurance rather than a live fix.

**`redact()` needed no new rules.** §11 listed extending it to new
path-bearing fields. The right place for that minimization turned out to be
extraction, not redaction: only parent directories and URL hosts are ever
collected, so there is nothing sensitive downstream to strip — and adding
redact rules would have removed exactly the signal the access-scope check
needs. Verified that all new fields survive `redact()` end-to-end.

**The network-import guard needed narrowing, not weakening.** `urllib.parse`
tripped a test banning all `urllib`. Rather than relax the guard, it now
exempts `urllib.parse` specifically (a pure string parser that cannot open
a connection) and a second test asserts the guard still bans
`urllib.request`, bare `urllib`, `urllib3`, `requests`, `socket`, and
`http.client`.

**A join bug slipped past the unit tests and was caught end-to-end.** The
transcript scanner produced the new signals correctly and its own tests
passed, but `list_local_workspace` copied only `models_used` and
`transcript_event_count` onto each session by name — so every new tool-use
and reach field was silently dropped before any caller saw it. Unit tests
on both sides were green; only running the real pipeline surfaced it
(`sessions with invoked-tool data: 0`). The join now copies whatever the
scan produces, and a regression test asserts the scan's key set is a subset
of the joined session's.

**`uv.lock` was not regenerated** — dependencies did not change, and the
repo's rule is to regenerate only when they do.

## 12. Open items

- ~~Memory export schema is unverified.~~ **Closed** — recovered from two
  live exports and pinned by tests. See §13.
- **Field presence verified on one machine only** (67 sessions at design
  time, 81 by implementation). Every consumer treats an absent field as
  normal and the docs say so, but the 32/67 and 38/67 fields are still not
  fully understood.
- **The enabled-vs-invoked join is by eye, not by key.** Display labels
  (`Control Chrome`) and transcript slugs (`claude-in-chrome`) share no
  identifier. The docs instruct against manufacturing a precise-looking
  join; a real mapping would be better if one can be found.
- **Should `SKILL.md` mention Reflect at all** as orientation for users who
  have seen it — noting it omits Cowork and Claude Code and is unavailable
  on Team/Enterprise? Still undecided; nothing was written either way.
- **Sensitive-folder heuristic wording** needs care to stay useful without
  becoming alarmist. Currently worded as "worth a look", with an explicit
  statement that folder *contents* were never examined.
- **Windows path handling is untested.** `touched_dirs` uses the
  platform-native `PurePath` flavour, correct in principle for reading a
  machine's own data, but exercised only on macOS.
