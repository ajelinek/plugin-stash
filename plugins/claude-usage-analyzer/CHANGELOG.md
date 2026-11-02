# Changelog

## 0.6.3 -- 2026-11-01

- Bundle shared helpers and the startup hook as real files rather than
  checkout-relative symlinks. Git marketplace installs that copy only this
  plugin no longer lose `shadetree_ai_plugins_common` or `check_uv.py`.

## 0.6.2 -- 2026-09-06

**This release fixes a bug that made the primary lens describe the wrong
workspace.** For every release up to and including 0.6.1, this plugin told
the user which Space each of their chats was in, and on the machine it was
finally checked against it was wrong about **every chat that had an
answer** -- 59 of 97. It reported 43 chats sitting loose in a generic
cloud-storage folder when Claude's own record put 36 of them in
"Vacation - July 2026", and it reported two Spaces the user actively works
in -- "Vacation - July 2026" and "ACTs 2027" -- as **empty**.

Nothing about that output looked broken. The Spaces were real, the folder
was real, the counts added up, and the reorganization lens confidently
proposed restructuring a workspace that did not exist. It survived three
releases of documentation passes because there was no error to notice.

### The join was rebuilt from a field the docs said didn't exist

`data-sources.md` stated, in bold, that **"there is no single index"** of
which Space a chat belongs to, and `join_local_sessions` reconstructed one:
match a session's `userSelectedFolders` against each Space's folders by
string prefix, take the first Space in file order.

The index exists. Every session file carries an explicit **`spaceId`** the
app writes when the user files the chat, with a **`spaceIdSetBy`** beside
it naming who set it (`"user"` on all 59 here), and it resolves straight
through `spaces.json`. `read_local_sessions` had never read either key.

The reconstruction failed the way a prefix match fails when directories
nest. The "OneDrive-Personal" Space is scoped to a whole cloud-storage
root, so *by prefix it contains every Space beneath it* -- and being early
in file order, it won every time. One worked example, session
`local_0a3a3b90`:

| | Value |
|---|---|
| Its one folder | `.../CloudStorage/OneDrive-Personal/Vacations/July 2026` |
| The app's own `spaceId` | **Vacation - July 2026** |
| What the plugin reported | OneDrive-Personal |

What is read now, in precedence order -- a session lands in exactly one
bucket, so the four still sum:

1. **The recorded `space_id`**, taken as-is. `space_source: "app_record"`.
2. **Cloud Project uuids** (`userSelectedProjectUuids`), trusted even when
   a uuid has no `.project-cache` entry -- the uuid is the signal, the
   cache only supplies a name.
3. **Folder matching**, and only for a chat the app recorded no Space for.
   This is a guess and is labelled one: `space_source:
   "inferred_from_folder"`, with `space_matched_folder` naming what it
   matched.
4. Otherwise unfiled.

Two rules now keep the fallback honest, and both exist because of the
failure above. **Most specific wins**: among the Space folders containing a
session's folder, the *longest* is chosen, not the first found, so a nested
Space is no longer swallowed by a broad parent. **Match on a path
boundary**: `/a/b` contains `/a/b/c` but must never claim `/a/bc`, an
unrelated sibling. A Space scoped to bare `/` is skipped entirely rather
than collecting every session on the machine.

On the verification machine, all 59 recorded attributions now match the
app exactly (36 Vacation - July 2026, 8 CUST - Pico Construction, 5 ACTs
2027, 3 BetterBelly, 2 OneDrive-Personal, 2 skills-stash, 1 each Claude
Training / LinkedIn / CUST - Nick Becker), two more are matched from their
folders and tagged as guesses, and 36 chats are in no Space. One Space
(`CRM`) is genuinely empty; before the fix, three looked it.

### Provenance now travels with every attribution

The distinction between "the user filed this here" and "we matched this
from a folder" changes how much confidence a reorganization proposal
deserves, so it is now carried on every row rather than left to the
caller's memory. Each session gets `space_source` (`app_record` /
`inferred_from_folder` / `none`), `space_source_note` (the same thing as a
sentence a non-technical reader can act on; the full map comes back as
`space_source_notes`), `space_from_app_record`, and `space_matched_folder`
-- populated **only** for a guess, so it never reads as evidence for a
recorded fact. `space_label` is always readable prose, never a bare uuid.

The join also returns `space_attribution_by_session_id`, with an entry for
**every** session including the ones in no Space, so a missing key never
has to be interpreted, and `unresolved_space_ids` -- recorded ids
`spaces.json` no longer names, most likely Spaces deleted after chats were
filed in them (empty here).

### The two tools disagreed with each other about the same fact

`list_local_workspace` returned `space_id: null` and `space_name: null` for
**all 97 sessions** while `get_project_membership` named a Space for the
same chats. They were not one join with a lossy path: `build_inventory`
never did the Space half at all -- its rows came straight off disk, where
there was no `space_id` key, and nothing merged the sibling `membership`
object back onto them. `build_project_membership` did that merge itself, in
its own loop.

Both now splice the same `space_attribution_by_session_id` map onto their
rows. One map, two consumers; they cannot drift again.

### Bugs fixed

**`account_tenure_days` was computed from a timestamp the same function had
already rejected.** A `first_launch_at` of `1e18` produced a tenure of
roughly **11.5 billion days** beside a `first_launch_at_iso` of `None`.
Tenure is now computed only when the value both normalizes *and* renders,
and a first launch in the future gets the same treatment -- there is no
honest tenure either way. `first_launch_at_usable` says which happened, and
a note says the run's per-week rates have no verified start date behind
them. `installed_at_ms` had the identical shape one field over (an epoch
number kept beside an `installed_at_iso` of `None`) and now clears with it.

**0.6.1's timezone fix reached one tool out of six.** `get_plan_usage_history`
got local rendering; nothing else did. A single `_instant_fields` helper
now produces `_iso` / `_local_iso` / `_local_date` / `_local_weekday` /
`_utc_offset` for every instant this plugin returns, and it was applied to:
`get_desktop_settings`' `first_launch_at`/`installed_at`; every probed
path's `modified` in all three `check_data_access` blocks (a raw epoch-
**seconds** float before, rendered nowhere -- one unit conversion away from
reading as 1970); `list_local_workspace`'s per-session
`created_at`/`last_activity_at` and cloud-Project `synced_at`; memory
owners' `newest`/`oldest_modified_at`; and instruction variants'
`latest_activity_at`, which was raw epoch milliseconds with no rendering at
all. The last three were found by auditing rather than reported. On the
verification machine the account's first launch is Sunday 2026-06-28 in UTC
and **Saturday 2026-06-27** locally -- exactly the off-by-a-day this
convention exists to prevent. `check_data_access`, `list_local_workspace`,
`get_instructions_inventory` and `get_desktop_settings` each now carry a
`timezone` block.

Not fixed, and disclosed rather than hidden: `parse_export`'s
`stats.date_range` and per-conversation `created_at` are the export's own
strings and get no local companion.

**`get_desktop_settings` could emit JSON no strict parser accepts.**
Python's JSON reader accepts `NaN`/`Infinity`/`-Infinity`; strict JSON does
not, and five separate paths passed a config number through verbatim (a
known preference value, an unrecognized preference scalar, an unrecognized
top-level scalar, `values_by_account`, and an extension's `installed_at`).
Non-finite numbers are now replaced with `None` and counted in
`unusable_number_count`; list length is preserved so a dropped item doesn't
become a second, quieter lie, and nothing is clamped into a plausible
value. A sixth defect the scrub alone would have missed: `any_enabled` was
`any(bool(v) ...)`, and `bool(float("nan"))` is `True` -- the corrupt value
was dropped on the way out while the boolean *derived* from it survived to
assert "yes, this is switched on somewhere". It now ignores non-finite
values and reports `unusable_value_count`. (The suite's own fixture plants
`installedAt: float("inf")`, so this tool was emitting `Infinity` in
testing today.)

**`read_memory_files` returned outside `redact()`.** Split into a reader
plus a thin `return redact(...)` wrapper, so every return path -- including
any degraded one added later -- is covered. Same pattern
`build_plan_usage_history` already used.

**A negative `max_memory_chars` returned nearly the whole file.** It did
not skip truncation; it made `text[:max_chars]` slice from the *end*,
returning everything but the last N characters while still reporting
`truncated: True`. Both `max_chars` and `max_memory_chars` are now clamped
through `clamp_max_chars`, in `read_memory_files` and
`build_instructions_inventory` alike.

**A scheduled-task entry had no size bound.** The entry shape is
deliberately unverified, which also means unbounded -- a task carries
whatever prompt text it runs. An entry over 2,000 characters is now
replaced by a measurement of itself (`omitted`, `reason`, `size_chars`,
`top_level_field_count`) rather than truncated, since a truncation is still
a partial verbatim dump of unverified content under another name, and it
names no field of the entry. Redaction runs **before** the measurement, so
a secret-shaped key cannot buy an entry past the check.
`oversized_task_count` and `max_task_entry_chars` come back with a note.

**A bare uuid escaped onto the memory path.** `owner_id in space_names` was
the entire resolution test, so a Space listed with a **null name** scored
`matched`, produced a `label` of `None`, and the `or str(...)` fallback
emitted the raw uuid into `repeated_across_memory[].owners` -- the one case
that most needed a sentence was the one that printed an id. Two new
resolutions, `space_listed_without_a_name` and
`project_listed_without_a_name`, now carry readable labels; `label` is
guaranteed non-empty and the `str()` fallback is **deleted**, not merely
unreachable, and `name` is nulled unless the resolution is `matched` so
`name`/`name_resolved`/`label` cannot disagree. The same wrong sentence
existed on two sibling paths (session->Space attribution and remote-session
grants) and was fixed there too -- telling a user a Space was *deleted*
when it is sitting unnamed in their sidebar is a wrong answer, not a vaguer
one, so the two cases get different sentences.

**The withheld-secret note attributed keys to the wrong namespace.** One
counter covered both the `preferences` block and the config file's top
level, so the note could send someone looking for a top-level key into
`preferences`. Split into `withheld_preference_keys` and
`withheld_top_level_keys`, and the note names both.

**A bare-date window bound lands at midnight UTC, and nothing said so.**
The UTC anchoring is a deliberate contract -- every existing caller, every
saved analysis and the plugin's own tests depend on it -- so it has **not**
been changed. What was actually wrong is that the mismatch was invisible:
off UTC, the window edge falls part-way through a local day while every
date in the same response is local. `resolve()` now returns
`since_local` / `until_local` / `timezone` / `boundary_notes`, `apply()`
carries them into the summary, and `summary_notes()` emits them, so every
tool that already reports notes gets the caveat -- naming the local instant
the bound actually landed on, its offset, and the exact bound to pass
instead (`"2026-08-01T00:00:00-05:00"`). Suppressed on a UTC machine, where
the two coincide. The "Time-scoped to" note now marks its span **UTC** and
says the dates elsewhere are local.

*If local-midnight bounds are wanted instead, that is a deliberate
decision, not a side effect of this pass: three lines in `parse_bound` plus
three tests.*

### Docs

Six statements in the reference docs were false or unearned, and are
corrected rather than softened:

- **`data-sources.md` section 2** claimed there is no chat->Space index and
  documented the folder-prefix reconstruction. Rewritten around what the
  code does: the app's record first, folder matching as a marked-inferred
  fallback, with the swallowing failure written out concretely enough to
  explain why specificity and boundary matching matter.
- **`data-sources.md` contradicted itself within one editing pass.** The
  section 1 file table said the `.project-cache/<uuid>/memory.md` siblings
  "aren't read"; section 1c, added seven lines below in the same pass,
  documented reading them.
- **A measurement attributed to "the verification machine" was a synthetic
  fixture's number.** The grant-breadth deduplication was justified with a
  naive sum of 5 against a true 2 -- "a 2.5x overstatement" -- inside the
  passage arguing against overstated figures. The real machine produces a
  naive **3** (2 + 0 + 1) against 2 standing and 0 session-scoped. The
  recomputed figures are in, along with a note saying where the 5 came from.
- **`check_data_access`'s `data_files` probe never learned about the three
  sources 0.6.1 added.** It probes four files at the top of the app-data
  directory and nothing else, so `desktop_settings_available: true` says
  the settings tool can run and says nothing about memory, scheduled tasks
  or the session->Space lookup -- the exact blocks the docs told the reader
  to check `data_files` before reporting empty. Widening the probe is a
  code change and hasn't been made; the docs now say what it actually
  probes and which flag answers each empty block instead.
- **The `agent/` directory was cataloged as app scratch space.** It holds a
  real user session: `sessionType: "agent"`, a 2,875-character user prompt,
  a model, timestamps, its own nested transcript, its own `audit.jsonl`,
  and an `outputs/` folder with three real deliverables. It sits one level
  deeper than the session glob reaches, so it is invisible to every lens.
  It is documented as what it is -- a gap, not a policy -- and deliberately
  still not read, because one observation cannot answer whether it is a
  sub-agent of another session (counting it would double that work) or
  whether there is ever more than one.
- **Three stale cross-references in `reorganization.md`**, one of them to
  `last_message_date`, a field that exists nowhere in this plugin; the
  others pointed at the wrong data-sources section and the wrong step
  numbers. Also corrected: a `cowork-gb-cache.json` entry count of 442
  presented as "confirmed directly" (it is 514 now -- the churn is the
  point), the `agent/` file's key count, and the claim that Auto approval
  mode costs more usage than Manual or Skip, which is received guidance
  nothing local measures and now may not carry a finding on its own.

A permanent note sits at the top of `data-sources.md`, where the next
person will hit it before writing anything down: **a claim about what the
app does *not* store has to be verified on a machine where the feature is
actually in use.** An empty array, a null field and an unpopulated file are
indistinguishable from an absent feature, and that single mistake has now
cost three consecutive releases -- `memories.json` in 0.5.0, scheduled
tasks and memory in 0.6.1, and this. It carries the key-union command that
would have caught all three.

### The safeguards against this class of bug did not run

0.6.1 added a re-verification procedure to `data-sources.md` so a future
maintainer could catch a false-boundary claim before shipping it. Neither
half of it worked, which was found by running it rather than reading it.

**The key-union check crashed.** Documented as "the one command that would
have caught the `spaceId` miss", it called `Counter.update()` on a parsed
session dict -- which counts a mapping's *values*, not its keys -- and died
on `TypeError: can only concatenate list (not "int") to list` against real
data. Fixed to `.update(json.load(open(p)).keys())`, which now reports
`spaceId 59/97` in one line, exactly as intended.

**The memory re-verification loop reported zero.** It globbed
`memory/**/*.md` with `ls`, and macOS ships bash 3.2, which has no
`globstar` -- so `**` matched nothing and the two largest memory stores
read as empty. It reproduced, precisely, the "memory is export-only" false
negative that 0.6.1 retracted. Rewritten with `find`: 12 account-level,
11 per-Space, 3 per-Project, against the 0 and 0 it used to print.

A procedure that is never executed is documentation of an intention, not a
safeguard. Both are now run against a real machine before being written
down.

### Two more absence claims corrected

- **`artifacts/` exists.** It was cataloged by one survey as an app working
  directory and recorded by a later re-check as not existing at all. Both
  were wrong: it exists (created 2026-07-08) and is empty. Empty is not
  absent -- the distinction that has now cost this plugin four separate
  false claims.
- **`outputs/` is not scratch space.** It was dismissed as "that session's
  own scratch space" and skipped. It holds **1,244 real files** -- the
  deliverables those sessions produced, and the most legible evidence of
  what a Space was actually for. Reclassified as a known gap rather than a
  boundary; it is the strongest remaining candidate for the reorganization
  lens.

## 0.6.1 -- 2026-09-05

A correctness release, and everything in it was found by evaluating 0.6.0
rather than by using it. Four independent adversarial reviews went over
that release; the security posture held up under live testing (a real
`config.json` containing OAuth token caches leaked nothing), and what they
found instead was a set of statements this plugin was making that were not
true.

**Two of them were documented as confirmed hard boundaries, in bold, with
instructions to state them plainly to the user.** Both were false. A third
was a fresh untrue claim added by 0.6.0's own docs pass. The pattern is
worth naming up front, because it is the third time it has happened here:
each was an empty or unexpectedly shaped container on disk, read as an
absent feature, and written down as settled fact rather than as an
observation.

### The false boundaries

**"Scheduled tasks are not readable -- confirmed boundary."**
`data-sources.md` stated, in a bolded section of its own, that there are no
schedule-shaped files anywhere under the Desktop app-data directory, and
told the skill to say so plainly. There are.
`local-agent-mode-sessions/<account>/<org>/scheduled-tasks.json` exists,
with a schema: `{scheduledTasks, recordedSkips, sundayAliasBoundaryStamped,
dayFieldsOrBoundaryStamped}`. It is **empty** on the verification machine,
which is almost certainly how the wrong conclusion was reached -- an empty
array read as an absent feature.

`get_desktop_settings` now reports it, and reports it carefully. An empty
list means "this device records no scheduled tasks": not that the feature
is absent, and not that the account has none, since the tasks run
server-side. **`task_entry_shape_verified` is `False`** -- a populated
array has never been observed, so entries come back verbatim-minus-redaction
and no field inside one is named, parsed, or promised. Over-correcting into
a documented task schema would be the same mistake facing the other way.
(An identically named file under `claude-code-sessions/` is Claude Code and
stays out of scope; the glob is anchored and `test_no_cli_surface_remains`
guards it.)

**"Memory content is export-only."** `workspace-checkup.md` said memory
could only be audited from an account export. Memory is on disk, in
**three** stores, and the largest is the one that had never been globbed:

| Store | Files | Bytes |
|---|---|---|
| Per-Space (`spaces/<space-id>/memory/**/*.md`) | 11 | 16,622 |
| Cloud Project (`.project-cache/<uuid>/memory.md`) | 3 | 11,789 |
| Account-level (`memory/**/*.md`) | 1 | 20 |

**15 files, 28,431 bytes.** Per-Space memory is several `.md` files per
Space -- a `MEMORY.md` index plus topic files -- not one file each, and
seven of the twelve Space memory directories are empty, so a spot check
could easily land on nothing. A reader built to the two paths previously
catalogued would have found 41% of the bytes and missed the tier closest to
the user's actual work.

The practical cost of the wrong claim: a default local run, with no export
in hand, skipped a whole checkup area and said nothing. Memory now comes
back from **`get_instructions_inventory`** -- the fourth layer of the same
thing that tool already measures, keyed by the same Space ids and Project
uuids, which is what makes its strongest finding (memory restating what the
standing instructions already say) computable at all.

**The lesson, recorded where the next maintainer will hit it.** An empty
array is a reading, not an absence. A file you didn't find is not a file
that isn't there. "Confirmed boundary" belongs to something established
positively -- a documented architectural fact, or a file whose contents you
read -- not to "I looked and didn't see it." 0.5.0's `memories.json`
list-versus-object bug was the first instance; these are the second and
third. `data-sources.md` now carries all three and the rules that follow
from them.

And the process failure behind it: **0.6.0's docs pass grew
`data-sources.md` by nearly 300 lines (465 -> 754) and left the false
scheduled-tasks boundary sitting in the same file, untouched** -- in the release that started reading three new
files out of the same directory tree. Adding to a document is not the same
as re-verifying it.

### Bugs fixed

**Days were bucketed in UTC and presented as a bare weekday.**
`daily_high_water` grouped samples by UTC calendar day and emitted a plain
`weekday` name with no zone marker. On the verification machine (US
Central) that moved the reported seven-day peak from **Thursday 20 August
to Friday 21 August** and changed the weekday label on **22 of ~30 days**.
The audience for this plugin reads dates on their own calendar, so this was
not a caveat on a right answer -- it was a wrong one.

Bucketing is now local, and every instant comes back twice: `*_at_iso`
(UTC, for joining and comparison) and `*_at_local_iso` (local, real offset
attached), with `*_local_date` / `weekday` / `utc_offset` wherever a
calendar day is implied and a top-level `timezone` block naming the zone.
Offsets are resolved per instant from the OS timezone database, so a span
crossing a daylight-saving boundary is right on both sides of it. The docs
now say to build every user-facing sentence from the local values.

**The seven-day limit was invisible.** Limit events were computed only for
the five-hour window. Nothing detected seven-day exhaustion -- no event, no
peak framing, no guidance -- which meant the plugin was silent about the
cap that can strand a business user for the rest of the week rather than
the rest of an afternoon. `limit_events` is now
`{near_limit_threshold_pct, five_hour: {...}, seven_day: {...}}` with
identical key sets on both sub-blocks, and the docs say to report both and
to lead with the weekly one when it is the higher count.

The one deliberate asymmetry is the run break, and each block reports its
own `run_break_hours` rather than hiding it: **2 hours** for the five-hour
window, **36** for the seven-day one. Five hours of quiet empties the
five-hour window, so a two-hour hole in the samples really does separate
two events; the seven-day window drains at about 14% a day, so 95% on
Monday night and 95% on Tuesday morning is one continuous week of being
nearly out.

**An unrecognized `mcpServers` key would not have appeared in
`unrecognized_settings`, though 0.6.0's docs promised twice that it
would.** That block was built only from unknown keys *inside*
`preferences`; unknown keys at the config file's own top level -- exactly
where `mcpServers` sits -- were read past and discarded. The one key named
as the example was the one case that would have been silently dropped.

Fixed in the code rather than by softening the claim, because silently
dropping unknown keys is precisely how a reverse-engineered reader goes
stale without anyone noticing. `unrecognized_settings` now spans both
namespaces, with the same value summarization (so an MCP block's
`env`/`args` never come through verbatim), the same secret-shaped-key
withholding, and a `"<key> (top level)"` label on the theoretical
name collision.

**"Retention limit" was asserted, never established.** The docs and a tool
note called the start of `plan-usage-history.json` a retention limit. The
data shows no such thing: the earliest sample could be this machine's
install date, the date the app started keeping the file, or where an
internal trimming rule cut it -- nothing on disk distinguishes them. The
response block is renamed `retention` -> **`coverage`**, since the field
name was itself the unsubstantiated claim in the most prominent place
available, and both the tool note and the docs now report the observed
date and attribute no cause.

**Two uncaught exceptions, both contradicting their own docstrings.** An
out-of-range epoch value in any of the three new timestamp fields raised an
uncaught `ValueError` and failed the whole tool call; `_load_json` caught
only `OSError` and `JSONDecodeError`, so a file with one invalid UTF-8 byte
raised `UnicodeDecodeError` and took down both new tools. Both readers
promised graceful degradation. They now deliver it.

Alongside that, `NaN`/`Infinity` in a usage sample used to propagate
straight into the response -- producing non-standard JSON that a strict
parser rejects, and figures like "1e+308%". Such readings are now
**dropped, not clamped**: clamping `1e+308` to 100 would manufacture "you
hit your limit" out of file corruption, and there is no honest substitute.
What was dropped is counted in `coverage.unusable_sample_count` /
`unusable_reading_count` with a note, so a partly corrupt file shows up as
corruption rather than as quietly lower numbers.

**Only one of the two new readers was redacted.** `build_desktop_settings`
ended in `redact()`; `build_plan_usage_history` did not, against a stated
hard constraint that every reader routes its output through it. Now a thin
wrapper covers **every** return path, including the three degraded ones. It
is a genuine no-op on today's data, which is the point: the guarantee holds
without anyone re-checking after the next field lands.

**The grant-breadth note double-counted, and called a session grant
"standing".** The "N standing grant(s) cover a whole category of files"
note summed the per-setting counts, so a folder listed in two settings was
one exposure counted twice, and it swept session-scoped grants -- which do
not carry forward on their own -- in under the word "standing",
contradicting the note immediately above it. `access_scope.broad_grants` is
now the figure to quote: distinct paths, deduplicated across settings,
standing and session-scoped counted separately. On the verification machine
the old sum said **5**; the true figure is **2 standing, 0 session-scoped**.
Overstating a security-shaped number by 2.5x is worse than not reporting it.

**Stale time-window statements in `SKILL.md`.** One enumeration of the
windowed tools omitted `get_plan_usage_history`, and the frontmatter
claimed every data tool takes a window when `get_desktop_settings`
deliberately takes none. Both corrected; the frontmatter is at **1,472
characters against the 1,536 cap** (0.6.0's headline bug was overrunning
that cap and silently truncating the trigger phrases, so the count is
measured and reported every release now).

**A `workspace-checkup.md` table row described a field's shape wrongly.**
It promised counts inside `by_session` that are actually its siblings.

### Removed: the raw sample series

`get_plan_usage_history` no longer returns `series`, and the `max_samples`
parameter is gone with it. Two independent problems, and neither was worth
fixing:

- **Nothing consumed it.** Not the renderer, not the skill, not any
  reference doc -- while it made up roughly two thirds of the default
  response a model had to read.
- **The downsampler picked evenly spaced indices with no extrema
  preservation**, so a chart built from it could omit every limit hit and
  show a maximum contradicting the `peaks` block in the same response.

This is a response-shape change, not a deprecation: callers use the
aggregates.

### New: local memory, session->Space names, scheduled tasks

Beyond correcting the two boundaries above, three capabilities land:

- **Local memory**, in `get_instructions_inventory`'s `memory` block
  (`max_memory_chars`, default 2000). Per owner: a `label` fit to say out
  loud, `session_count`, `char_count`, `stale_file_count`,
  last-modified dates, and `duplicates_global` -- the same overlap check
  the instruction layers get, because memory restating the standing
  instructions is paid for twice. Block-level: `repeated_across_memory`,
  the cross-topic-drift signal.

  Two judgment calls worth recording. **Staleness comes from the filesystem
  mtime**, because memory files carry no timestamp of their own -- it is a
  prompt to check whether the content is still true, not evidence that it
  isn't. And **the drift signal needed a structural filter**: the first run
  reported `type: project` as "remembered under four different Spaces" and
  `**Purpose & context**` under three, which are YAML front matter and
  headings from the shared template -- a finding about the template, not
  the user. With front matter and heading-shaped lines stripped,
  `repeated_across_memory` went 5 -> **0**, which is the honest answer
  there. The instruction layers keep the plain comparison: they are
  hand-written prose with no template behind them.

  Memory is deliberately excluded from `totals` (it loads on demand, not on
  every turn) and `since`/`until` don't scope it (current state, not a
  series) -- only each owner's `session_count` is windowed.

- **Every remote-session folder grant now names its Space.**
  `remoteSessionFolderGrants` is keyed by raw `session_01...` ids, which
  0.6.0 returned as-is. `remote-session-spaces.json` is the lookup table
  that turns one into a Space, and `spaces.json` turns that into a name.
  Six of six resolved on the verification machine. Four outcomes are named
  and **none returns a bare id** -- an unresolvable one gets a
  `space_label` describing the situation ("a Space that is no longer listed
  in spaces.json -- most likely deleted in the app after this grant was
  made"). A raw uuid in a report reads as an answer while being none.

- **Scheduled tasks**, as described above, beside the
  `coworkScheduledTasksEnabled` / `ccdScheduledTasksEnabled` toggles --
  the only place the reading makes sense, since the toggle says the feature
  is on and the file says whether anything is scheduled here.

### Docs

- **Every app-wide setting a finding can raise now has a plain-language
  name.** `workspace-checkup.md` told the analyst to name settings by their
  raw on-disk key for 12 of the 13 it documents. A user who has never
  opened a terminal cannot find `keepAwakeEnabled` anywhere in the app: it
  is a string from a JSON file, not a label on a screen. Each setting now
  carries wording to say out loud ("Keep this computer awake so Claude can
  keep working"), with the key kept alongside for maintainers.

  On *where* to change them: **no per-setting UI location is established,
  and the docs say so rather than guessing.** Nothing on disk records where
  a setting appears in the app, and no published doc enumerates them. A
  confidently wrong menu path is worse than none -- it sends the user
  hunting and costs the report its credibility. The one illustrative menu
  path in the access-scope example was softened for the same reason.

- **`signature_status` kept, with the guidance it was missing.** It was
  surfaced to the analyst with no interpretation, and the only value ever
  observed is `unsigned` -- on Anthropic's own first-party chrome-control
  extension. As shipped it invited telling a non-technical user their
  extension was unsafe, which this plugin cannot substantiate. Dropping the
  field would lose the only signal that would show it ever carrying real
  values, so it stays with explicit guidance instead: unsigned is normal,
  it is not a security finding on its own, and it must never be shown to
  the user. `build_desktop_settings` now emits a note saying so whenever
  any extension reports it.

- `data-sources.md` gains full entries for the three new files -- verified
  shapes, response fields, and what each one does *not* establish -- under
  the same standing warning as the rest of section 1c, with the
  re-verification commands extended to cover them. Plus an instruction that
  is new in kind: **check the empty containers especially**, since those
  are what produced both false boundaries.

- `usage-efficiency.md`'s instruction to treat `daily_high_water` as UTC
  and "convert deliberately" is reversed -- it was asking a business user
  to do offset arithmetic on their own calendar -- and it now leads the
  limit-event section with the two windows rather than one.

- The README's scheduled-task boundary and retention wording are corrected,
  and the `get_plan_usage_history` row no longer advertises a chart series
  that no longer exists.

## 0.6.0 -- 2026-09-05

Gives the usage lens something it never had -- a real measurement -- and
un-blinds three checks in the setup checkup that had only ever been able to
see sessions. Also fixes a triggering bug that meant the skill often didn't
run at all.

### Bugs fixed

**The skill's description was 657 characters past the cap, so most of its
trigger phrases never shipped.** `SKILL.md`'s frontmatter carried a single
2,193-character `description` against a 1,536-character limit. Everything
past that was silently cut -- no error, no warning, and the file on disk
still reads correctly, which is why it survived four releases.

What got cut was the end of the field, and the trigger list lived there.
**Two of fourteen trigger phrases survived**; the truncation landed
mid-word inside the third ("audit my Claude usag"). So a user asking "am I
using the right models", "review my custom instructions", "is my Claude set
up correctly", "what folders can Claude see", or nine other documented
phrasings was matching against text the model never saw. The user-visible
symptom was the skill under-activating: it worked when invoked by name or
by one of the two surviving phrases, and otherwise quietly didn't fire,
which reads as "the plugin doesn't do that" rather than as a bug.

Fixed by splitting the frontmatter into `description` (782 chars -- what
this is) and `when_to_use` (650 -- when to fire it), for 1,432 total with
104 characters of headroom. All fourteen original phrases are back, plus
four new ones for the usage-limit questions this release can now answer
("am I hitting my usage limits", "why do I keep running out of usage",
"how much of my plan am I using", "what does Claude have access to"). The
old field also contained a mangled join -- `"...export folder. Analysis
Every data tool..."` -- from an earlier edit; that's gone.

The lesson worth keeping: this cost nothing to detect and nothing to fix,
and it silently degraded the whole plugin for four releases. **Count the
frontmatter.**

**`pyproject.toml` advertised Claude Code CLI support the design
explicitly forbids.** Its description said the server inventories "local
Claude Desktop/Cowork/CLI usage." CLI data has been out of scope since
0.3.0, the reader was deleted then, and `test_no_cli_surface_remains`
fails the build if one comes back -- so the package metadata contradicted
both the design and its own regression test. Corrected, and it now states
the constraint outright ("Claude Code CLI data is out of scope") rather
than merely omitting the claim.

**`data-sources.md` described `claude_desktop_config.json` as something it
isn't.** It was cataloged, in the list of files this plugin does *not*
read, as "MCP server definitions the user configured manually." On a
current build that is wrong twice over: the file holds
`coworkUserFilesPath` plus a 27-key `preferences` block covering standing
folder grants and app-wide toggles, and it may carry **no `mcpServers` key
at all** -- there was none on the machine this was verified against. This
plugin now reads that file, so the entry is not just stale but pointed at
the wrong contents. Rewritten as part of the new section 1c, with the
correction called out in place so a returning reader notices it changed.

**The README's test command doesn't work on a fresh clone.** It said tests
run with `uv run pytest` from the repo root. This is a uv workspace, and a
workspace does not install its members by default, so on a clean checkout
that command fails to import the package -- exactly the wrong first
impression for someone evaluating the repo. The working command is
`uv sync --all-packages && uv run pytest`, and the README now says why the
sync isn't optional rather than just listing it.

### New: what the plan actually consumed (`get_plan_usage_history`)

**The usage lens had no ground truth at all.** Every cost-shaped claim it
could make -- model tier, the effort dial, approval mode, Cowork versus
chat -- is *configuration*, describing how a session was set up. None of it
observed a single token. The lens was inferring cost from settings and
presenting the inference in the place a measurement belongs.

`plan-usage-history.json` sits at the root of the Desktop app-data
directory and is the Desktop counterpart of the Claude Code CLI's `/usage`:
Desktop's own rolling record of how full the five-hour and seven-day usage
windows were, sampled roughly every 15 minutes while the app runs. On the
verification machine: **545 samples across 29.3 days**, median sampling
interval **15.0 minutes**, five-hour window peaking at **100%** and the
seven-day window at **80%**.

The tool returns the current reading with its age, peaks with timestamps,
at-or-near-limit runs, per-UTC-day high-water marks with weekday names,
extra-usage status, retention and cadence, and a downsampled chart series
(first and last samples always preserved). The design decisions that
matter, because each is a way this could have misled someone:

- **Limit runs collapse into events.** Sampling every 15 minutes means one
  busy afternoon at the ceiling would otherwise read as a dozen separate
  limit hits. Contiguous samples at or above 90% become one event with a
  start, end, duration and peak; `hit_ceiling` separates "you ran out" from
  "you got close." On the verification machine that turned raw samples into
  **5 events, 4 of them at the ceiling** -- the first collapsing 21
  contiguous samples into a single 100-minute event. A run breaks after two
  hours of silence, because by then the app was closed and the five-hour
  window has rolled on anyway.
- **A gap means the app was closed, not that usage was zero.** Desktop
  writes this file only while running; the largest gap observed was **33.2
  hours**, a laptop asleep overnight. Reading gaps as idleness turns a
  holiday into "you barely use Claude." The tool says this in its own notes
  and the docs repeat it in three places, deliberately.
- **Missing is not zero.** The extra-usage field `xu` was present on
  **159 of 545 samples** and simply absent from the other 386. It comes
  back as `None`, never `0`, with `samples_missing_field` alongside, so the
  only claim made is "never above zero on the samples that recorded it" --
  not "this user has never bought extra usage," which the file cannot
  support.
- **Percentages are never money.** These are shares of the user's own plan
  limits. There is no price, no token count and no plan tier anywhere in
  this data, so any dollar figure would be invented. The docs forbid
  converting them, summing them across days, or even calling them "usage"
  rather than "how full a limit got."
- **Samples match on falling inside the window**, not on interval overlap
  like every other reader here, because a sample is a point in time rather
  than a span. That means a narrow window can legitimately return nothing,
  and the response distinguishes "no data recorded for that period" from
  "no usage."
- The organization uuid the file carries is used for grouping and **never
  surfaced** -- organizations come back as `organization 1`.

`usage-efficiency.md` now leads with this and reframes the four old signals
explicitly as proxies: measure first, then use the proxies to explain the
peak and name the lever, and when the history is unavailable say so and
label the fallback rather than letting a proxy-only finding read as if it
were measured. It also documents how to turn a limit event into something
actionable -- when it happened, what was running then, which single lever
to pull, and how often it actually costs the user -- and points the
headline numbers at the plan's `stat_tiles`, which the dashboard already
logs across runs, so "times at the five-hour limit" becomes a trend on the
next run.

This file covers **this machine only**, and only as far back as it retains.
Phone, web, and second-computer usage aren't in it. That is now a stated
limitation in the README rather than an unstated assumption.

### New: account-level settings (`get_desktop_settings`)

**Three of the checkup's six areas were structurally blind.** Access scope,
capability inventory and settings hygiene all ran on per-session data
alone, which can only describe what some session happened to use. Anything
standing at the account level left no trace:

- A folder granted once and trusted ever after appears in no session's
  `user_selected_folders` unless a session recently opened it -- so the
  strongest possible scope signal was invisible, and the check was
  reporting on the weaker one.
- An extension installed and never enabled in any session appears in no
  session file at all. The installed-but-never-used check -- the most
  actionable capability finding there is, and the common case on a mature
  account -- could therefore only ever find things that had been switched
  on at least once. It could not find the thing it was named for. **If an
  earlier run reported no unused capabilities, this is why.**
- App-wide toggles aren't per-session fields, so settings hygiene could
  only audit the three per-session ones.

One tool now reads three files and fills all three gaps:

- **`claude_desktop_config.json`** -- `coworkUserFilesPath` (the output
  folder as Claude itself has it configured, replacing a guess) plus the
  27-key `preferences` block: the three standing-grant settings
  (`localAgentModeTrustedFolders`, `remoteFolderConsentMemory`,
  `remoteSessionFolderGrants`), 13 named toggles, and 11 keys this plugin
  doesn't recognize. Each grant path carries `looks_broad` and a
  plain-language reason ("your entire home folder", "a whole cloud-storage
  account root") -- **a flag, never a score**, since how much a broad grant
  matters depends on what's inside it and nothing here looks. Verification
  machine: 10 trusted folders with 2 flagged broad, 4 remembered consents,
  and 6 sessions holding 5 distinct granted paths.
- **`extensions-installations.json`** -- authoritative for *installed*.
  Manifests are measured and dropped rather than returned. The checkup now
  crosses three levels rather than two: installed, enabled, invoked.
- **`config.json`** -- three allowlisted keys only (`first_launch_at`,
  `locale`, `userThemeMode`) out of 19 on disk, because that same file
  holds `oauth:tokenCache` and `oauth:tokenCacheV2`. The guarantee is two
  independent gates: the allowlist, **and** a second `_is_secret_key` check
  on every survivor, so one typo in the allowlist isn't enough to leak a
  token. A test plants both token caches in a fixture and asserts nothing
  credential-shaped -- not the values, not the string `oauth` in any
  casing -- survives into the response, with positive assertions alongside
  so it can't pass by reading nothing. `first_launch_at` is worth having on
  its own: it's the denominator for any rate, and "forty chats" means
  something different in a week-old install than in a year-old one.

Unrecognized preferences are kept, with values summarized rather than
dumped, because silently dropping unknown keys is precisely how a reader
like this goes stale without anyone noticing. They are a drift signal for
maintainers and explicitly **not** a finding about the user.

No `since`/`until` on this tool: settings are current state, nothing on
disk records when a toggle was flipped, and a window would imply a
precision the data doesn't have. On a scoped run, that section isn't
scoped -- and the docs say to tell the user so.

`workspace-checkup.md` wires all of this in: how to spot a stale standing
grant (present in the grant list, touched by nothing for the whole period)
or an over-broad one (granted a whole category, used for one subfolder),
and how to write either for someone who has never seen a file path in
`snake_case`. It also applies 0.5.0's scheduled-tasks correction to a
specific setting: **`keepAwakeEnabled` is now likely obsolete**, since
scheduled tasks have run on Anthropic's servers since 2026-07-07 whether or
not the machine is awake. It's worded as a question rather than a
correction, because the local-files exception survives and this plugin
cannot enumerate existing scheduled tasks to check.

One thing a reader needs and won't guess: **hooks are a Cowork feature and
are greyed out in the Chat tab**, so this plugin's SessionStart
access-scope warning never fires for a Chat-tab user no matter how badly
scoped their session is. Its silence proves nothing; `check_data_access` is
the check that always runs. That's now stated in both the README and the
checkup reference.

### `check_data_access` says up front what can run

It now reports a `data_files` block -- which of the four account-level
files are present, and therefore whether the usage-history and
settings lenses can run at all this session -- so a caller learns that here
instead of from an empty response two tools later. The caveats live in
`data_files.notes` rather than in top-level `warnings`, since a missing
usage-history file on a fresh install is normal, not a problem.

It also prefers Claude's own configured output folder over the candidate
list, and reports `output_dir_source` as `"claude_desktop_config.json"` or
`"candidate_probe"`. "Claude is configured to write here" and "it's
probably here" are different statements, and the difference matters before
anyone goes looking for a missing scheduled-task result.

### `/analyze-usage`

A slash command that runs the skill's workflow start to finish. It
delegates rather than restating the steps -- `SKILL.md` stays the single
source of truth, so the workflow can't fork. It takes a time window
(`/analyze-usage 90d`, `/analyze-usage 6m`, `/analyze-usage all`); with no
argument it defers to the skill's Step 2a and *asks* rather than quietly
defaulting to all history, because that choice changes what every number in
the dashboard means.

### FastMCP 4 -- upgraded, not deferred

`fastmcp>=3.4` → `>=4.0`, resolving to 4.0.3. Kept, after checking rather
than assuming: FastMCP 4 removes server-initiated sampling and roots, and
this server uses neither (the one grep hit was a prose comment about
sampling a transcript tail). The transitive churn was larger than the
version bump suggests -- `mcp` 1.28.1 → 2.1.1, `httpx`/`httpcore` replaced
by `httpx2`/`httpcore2` -- so the workspace suite alone wasn't treated as
sufficient: the plugin was also rebuilt standalone in the shape Desktop's
plugin cache uses, and all nine tools confirmed to register there under an
in-memory client.

Full workspace suite before and after: identical, 391 passed with one
failure in an unrelated plugin that fails the same way without the upgrade
(a machine-timezone assertion in `plugins/iphone`). Not caused by this
change and not fixed here, but worth recording so the next person doesn't
re-diagnose it.

Both lockfiles were regenerated. That matters more than it sounds: this
repo commits **two** locks per server-backed plugin, and `.mcp.json`
enforces the plugin's own standalone lock with `--locked` at every client
launch. Bumping the floor without regenerating that second lock would leave
CI green while **every client install failed to launch** on a
lock/pyproject mismatch.

### Manifest, docs and version alignment

- `plugin.json` gains `$schema` and `displayName`, keywords `usage-limits`
  and `plan-usage`, and a description that names both new capabilities in
  business-user terms ("see when you actually hit your five-hour and weekly
  limits instead of guessing") while keeping all four framing commitments:
  Desktop business users, CLI deliberately excluded, all-history-or-window,
  read-only.
- **Versions realigned at 0.6.0** across `plugin.json`, `pyproject.toml`
  (which had drifted to 0.1.2, three releases behind, while the plugin
  manifest read 0.5.0) and `SKILL.md`'s version line. `SKILL.md` also gains
  `compatibility` and `license`.
- `data-sources.md` gains section 1c: full field semantics for all four
  account-level files, and a standing warning that these are undocumented
  app internals verified against **one macOS install in September 2026**,
  with the commands to re-verify them and what to check. That warning is
  earned rather than boilerplate -- 0.5.0 was largely bug fixes from
  exactly this class of drift, and each of those bugs looked from the
  outside like a field that was simply empty.
- Tests: 133 → **174**. The new ones cover the missing-versus-zero
  distinction, event collapsing (including a guard against
  over-collapsing across a day-long gap), downsampling that preserves
  first and last, the org uuid never appearing in a serialized response,
  manifests never being returned, grant-breadth flagging, the
  `config.json` allowlist at both gates, and each account-level file being
  independently absent, truncated, or the wrong shape.

## 0.5.0 -- 2026-08-01

Widens the third lens from "audit the standing instructions" into a full
**setup & effectiveness checkup**, and fixes three bugs found while doing
it -- two of which meant the plugin had been silently returning nothing
where it claimed to return data.

### Bugs fixed

**`effort` was read from a key that does not exist.** The reader asked for
`effort`; the on-disk key is `effortOverride`. Across 67 real session files
`effort` appeared 0 times and `effortOverride` 40 times, so the field was
`None` in every response the plugin has ever returned -- while the skill
docs instructed the caller to right-size against it. Now reads
`effortOverride`, with the old key kept as a defensive fallback.

**`memories.json` was parsed against a shape no export produces.** The
payload is a single-element **list** wrapping
`{conversations_memory, project_memories, account_uuid}`; the parser
assumed a bare object and called `.get` on it. Every real export therefore
fell through to a raw-JSON-preview branch and emitted a 2,000-character
truncated dump instead of memory. Verified against two live exports; the
same fix now parses ~19,000 characters of real memory where it previously
produced a dump. Per-project memory is keyed by project uuid (which joins
to `projects_index.json`) and carries no project name -- the old code
looked for a `project_name` field that does not exist.

**The docs promised local fields that were never returned.** `SKILL.md` and
`usage-efficiency.md` both said the analysis works from
`first_human_message`/`keywords`/`tool_names`/`message_count` across both
sources. Those existed only on the export side. The two sources now have an
explicit field-mapping table, and the local side actually supplies the
equivalents.

### New session data

`list_local_workspace` read 10 of a session file's ~34 keys. It now also
returns: `permission_mode` (Manual/Auto/Skip -- a cost signal, since Auto
consumes more usage than either of the others), `initial_message`,
`enabled_mcp_tool_names` / `enabled_mcp_server_labels`,
`remote_mcp_server_names`, `plugin_names`, `slash_command_names`,
`plugin_install_count`, `egress_allowed_domains`,
`web_fetch_allowed_url_hosts`, `fs_detected_file_count`, `cwd`, and
character counts for `system_prompt` and `memory_guidelines`.

The large always-loaded blobs are measured, never returned -- roughly
49,000 and 13,500 characters of Anthropic scaffolding the user cannot edit,
so the text would be unactionable and sensitive while the size is useful
context for judging the user's own instruction length.

### What a session actually did

The nested-transcript scan already ran on every call to tally models; it
now also extracts `tools_invoked`, `mcp_servers_invoked` (parsed from the
`mcp__<server>__<tool>` convention, so the tool tally doubles as a record
of which connectors were really used), `message_count`, `touched_dirs`
(parent directories only) and `url_hosts` (hosts only). No message text,
no file contents, no full URLs.

Crossing this against the configuration fields above -- what a session
*could* do versus what it *did* -- is where most of the new findings come
from.

The scan's line cap no longer truncates. Past the cap it continues at a
sampling stride instead of stopping, because taking a flat prefix biased
every distribution toward the opening of a session -- exactly wrong for the
long agentic sessions this is meant to measure. `transcript_sampled` flags
when sampling occurred.

### New checks

- **Memory** -- stale or contradicted entries, cross-topic pollution in a
  project's pool, duplication against standing instructions, and whether
  memory is on at all. Memory is now individual categorized entries rather
  than a daily synthesized summary; guidance to "wait a day for memory to
  settle" has been removed as stale.
- **Project and Space quality** -- existing Projects graded on whether
  their name, description and instructions match what they are for, with
  replacement text written for each defect found and silence on the ones
  already fine.
- **Access scope** -- what each Space and session can reach, broad grants
  versus dedicated working folders, and scope creep measured as directories
  and hosts actually touched versus what was granted. Quotes Anthropic's
  published guidance rather than inventing a standard.
- **Capability inventory** -- installed and enabled versus actually
  invoked, yielding installed-but-never-used, used-constantly, and
  needed-but-missing.
- **Effort, approval mode and surface choice** as explicit cost levers.

### Dashboard

New optional `start_here` field on the plan: a ranked shortlist of the
highest-impact fixes, rendered above every detailed section. Analysis stays
complete -- this only decides what the reader meets first.

### Corrected guidance

- **Scheduled tasks run remotely** and no longer need the machine awake
  with Desktop open, except when a task needs local files or apps.
  Auditing *existing* scheduled tasks is documented as a confirmed hard
  boundary: nothing schedule-shaped is stored on disk, because they execute
  server-side. Recommending *new* ones is unaffected.
- **Don't advise starting a fresh chat to avoid a context limit.** Long
  conversations auto-compact and that compaction does not consume usage
  tokens. Topic separation is the real reason to start fresh.
- **Project knowledge is cached**, so only new portions count against
  limits on reuse -- the highest-leverage efficiency change available, and
  now the stated rationale behind each proposed Project's file list.
- **Context is not shared between chats in a Project** unless it is in the
  knowledge base -- a common and costly misconception.

## 0.4.0 -- 2026-07-29

Makes time scoping a first-class part of every run. Previously the only
thing that read a timestamp was the instruction-variant ordering; there was
no way to ask "how did I use Claude last quarter" short of analyzing
everything and mentally discarding the rest.

**Every data-pulling tool now takes `since`/`until`.** That's
`list_local_workspace`, `get_project_membership`,
`get_instructions_inventory`, and `parse_export`. Omit both for the full
history -- the previous behavior, still the default. Both bounds accept a
relative age (`"90d"`, `"12w"`, `"6m"`, `"2y"`), an ISO date or datetime,
or epoch milliseconds. The relative form is preferred: it resolves against
the real clock server-side, so it can't be thrown off by an uncertain sense
of today's date.

The shared implementation is the new `time_window.py`. Design decisions
worth knowing, because each one is a way this could have failed quietly:

- **Matching is interval overlap, not creation date.** A chat started in
  January and still worked in March belongs in a March window. Filtering on
  creation date would silently drop exactly the long-running work most
  worth analyzing.
- **The two sources record time differently** -- local sessions in epoch
  milliseconds, export conversations in ISO 8601 strings -- so
  normalization lives in one place. Reading the local values as seconds
  yields 1970 dates that look like real, very stale data rather than an
  error.
- **Bad bounds widen the read, never empty it.** An unparseable bound, or
  a `since` after `until`, is reported in `time_window.errors` and the read
  proceeds unbounded. Returning zero rows is indistinguishable from "this
  account has no data," which is the worst available failure mode.
- **Undated records are kept, not dropped**, and counted as `undated_kept`
  so they can be disclosed. Excluding data that merely failed to prove it
  belongs would understate the result.
- **In `list_local_workspace` the window is applied before the
  Project/Space membership join**, so the session list and every grouping
  derived from it describe the same period rather than pairing a windowed
  chat count with an all-time Project breakdown.

**Every response now carries a `time_window` block** -- resolved bounds,
`total_before`, `kept`, `excluded`, `undated_kept`, and `errors` --
alongside plain-language notes. A windowed count presented as an account
total is simply wrong, and nothing in a rendered dashboard makes the
difference obvious on its own, so `render_dashboard`'s plan gained a
first-class `time_window` field that renders under the header. A run with
no window says "Covering all available history" instead, meaning omitting
the field on a scoped run doesn't just lose context -- it asserts something
false.

`get_instructions_inventory` is the case where a window does more than
filter. Because global custom instructions are stored per-session, scoping
to a period reports the instructions that were actually in force *then*,
and the session reach for that period -- which is how to ask "what were my
standing instructions last quarter," a question no view in the app answers.

Also in this release:

- `list_local_workspace` sessions now carry `created_at_iso` and
  `last_activity_at_iso` alongside the raw epoch-millisecond fields, so
  nothing downstream has to re-derive the conversion to present a date.
- SKILL.md gains Step 2a: ask the user for the full flow or a window
  *before* pulling anything, and pass the same window to every tool in the
  run.
- `_activity_sort_key` now delegates to `time_window.to_epoch_ms` rather
  than carrying its own parsing.

## 0.3.0 -- 2026-07-29

Narrows the plugin to its actual audience -- business users of the Claude
Desktop app -- and adds the one check from Claude Code's `/doctor` that
genuinely translates to that audience.

**Breaking: Claude Code CLI data is no longer read, anywhere.** Two
reasons. Terminal sessions, working directories, and git branches are
noise to a non-developer, and they inflated every count in the analysis
with activity from a different product. And Claude Code already covers
that surface better than this plugin could: `/doctor` (alias `/checkup`)
for installation and configuration health, `/usage` (alias `/cost`) for
billing-period cost, `/context` for context-window pressure. What went:

- `cli_dir()`, `read_cli_sessions()`, and the `cli_sessions` key in
  `list_local_workspace`'s response (removed outright rather than left
  empty -- an empty list reads as "no CLI usage found" rather than "never
  looked").
- The `cli` key in the access check, and its CLI-related warnings.
- `cli_session_id` on each session record -- a bridge to Desktop's Code
  tab, which is Claude Code embedded in Desktop and also out of scope.
- The `~/.claude/tasks/` join. Cowork's own scheduled tasks were never in
  local data at all; that's now stated plainly rather than left implied.

`test_no_cli_surface_remains` fails the build if a reader creeps back in.
Cowork's *own* nested per-session transcripts stay -- they share Claude
Code's JSONL layout but are Cowork's data, and they remain the only
per-message-accurate model signal Desktop has.

**Breaking: `usage_doctor` is renamed `check_data_access`.** The old name
invited confusion with Claude Code's `/doctor`, which does something
substantially different. This tool only reports whether the current
session can *see* the data files.

**New: `get_instructions_inventory` and a workspace-checkup lens.** The
most valuable thing `/doctor` does is audit `CLAUDE.md` -- deduplicating
it, trimming content Claude could derive for itself, and moving
always-loaded guidance into things that load on demand. The Desktop
equivalent of `CLAUDE.md` is standing instructions, which live in three
separate places that no single view brings together: global custom
instructions, each Space's `instructions`, each cloud Project's
`prompt_template`. The new tool joins all three and reports:

- Character counts, line counts, and how many sessions each blob actually
  reached.
- `duplicates_global` -- lines a Project repeats verbatim from the global
  text, paid for twice on every turn in that Project.
- `repeated_across_projects` -- the inverse: the same text pasted into
  several Projects, which usually belongs one level up.
- `totals.always_loaded_char_count` / `worst_case_char_count`.

Global custom instructions are stored per-session rather than in one
account file, so distinct texts on disk mean the user *edited them over
time*, not that two are competing. They're grouped into `variants` sorted
by recency, with `variants[0]` the best guess at what's live, and a note
saying so -- reporting it as a conflict would be wrong.

The judgment half lives in the new
`references/workspace-checkup.md`: which instruction content is derivable
and should be cut (a list of the Project's own files, background already in
the description) versus what must be kept (standing constraints, tone,
internal jargon, anything contradicting a default). Unlike `/doctor`, there
is no apply step and won't be one -- the text is the user's own writing
about their own business.

- **Bug fix:** `createdAt`/`lastActivityAt` are epoch **milliseconds as
  integers** on a real install, not ISO strings. Anything sorting sessions
  by recency has to handle that; `_activity_sort_key` does, and still
  accepts an ISO string defensively since this format is unversioned.
- `_extract_custom_instructions` no longer truncates -- it returns the full
  text and callers truncate what they return, so a length-based finding
  reports the real character count rather than a capped one.
- `check_data_access` now warns separately when the user-visible `~/Claude`
  output folder isn't in scope, since that's where Cowork writes
  scheduled-task output.

## 0.2.0 -- 2026-07-29

Chains existing native tools instead of duplicating them, fixes a real bug
found by direct inspection of a real Claude Desktop install, and fills the
one confirmed gap from an external chaining-spec review.

- **Bug fix:** `read_spaces()` expected `spaces.json` to be a bare JSON
  array; the real on-disk shape is `{"spaces": [...]}` (confirmed directly).
  This silently made `spaces`/`by_space_id` empty on every real install --
  fixed to accept both shapes, defensively (this format is
  reverse-engineered and unversioned). The existing test fixture made the
  same wrong assumption and has been corrected to the real shape, plus a
  new test covers the bare-list case for backward compatibility.
- **Bug fix:** `join_local_sessions` silently dropped a session's declared
  cloud-Project uuid if that uuid had no local `.project-cache` entry yet
  (never opened on this device, or synced after the cache snapshot), which
  misclassified real project members as `unfiled_session_ids`. Fixed to
  keep all declared uuids as membership regardless of cache presence, with
  the cache only used to resolve a *name*. `membership` gains
  `uncached_project_uuids` so a caller can tell "filed under an uncached
  project" apart from a normal hit.
- New environment-settings fields, sourced from data `read_local_sessions`
  already opens (no new file reads): `memory_enabled`/`skills_enabled`/
  `plugins_enabled` and `custom_instructions` (the account's global custom
  instructions text, extracted from `systemPromptRendererAppends`'s
  `<user_preferences>` block) per session in `list_local_workspace`, and
  `prompt_template` (a cloud Project's custom instructions) in
  `project_cache`.
- New `get_project_membership` tool: just the project/Space join key for
  local sessions (cloud Project uuid(s)/name(s), Space id/name, local
  folder paths), meant to chain after the harness's own
  `session_info.list_sessions()` (same `local_<uuid>` id format, no
  translation needed) and before `read_transcript` -- read only the
  sessions that actually matter instead of every transcript blind.
- `list_local_workspace` gains optional `session_ids`/`project_uuid`/
  `folder_path`/`fields` params to scope the response instead of always
  paying for the full inventory dump (all default to current unfiltered
  behavior -- additive, not breaking).
- `references/data-sources.md`: corrected the `spaces.json` shape and
  removed a stale, nonexistent-on-a-real-install `cowork_account_settings.json`
  entry; new sections document the environment-settings fields above, a
  confirmed hard boundary ("chat search enabled" and generic account
  feature toggles aren't answerable -- the only on-disk candidate is an
  unstable, undocumented 442-key internal GrowthBook cache, deliberately
  not read), and the chaining recipe for capability inventory
  (`list_skills`/`list_plugins`/`list_connectors`) and session/project
  correlation via the new tool -- all confirmed by direct inspection of a
  real Claude Desktop install rather than guessed at.
- `references/usage-efficiency.md`: names the concrete native tools for
  the "check for an existing skill/plugin/connector first" step, notes the
  confirmed gap (no raw MCP connector auth-status tool), and adds
  environment-settings hygiene as a finding category alongside model
  right-sizing.
- `SKILL.md`: points Step 3 at the new tool/params for scoping data
  gathering on a large account; Step 5 folds in environment-settings
  findings.

## 0.1.2 -- 2026-07-27

- `references/data-sources.md` expanded into a full file-reference guide
  for local Claude Code CLI/Desktop/Cowork data: every path this plugin
  actually reads keeps its existing tie to the reading code, plus a new
  catalog of adjacent files seen on disk but deliberately not read
  (`bridge-state.json`, `claude_desktop_config.json`, feature-flag/audit
  caches, global/per-Space memory files, `IndexedDB`, `Cookies`,
  `buddy-tokens.json`, etc.), Windows MSIX path-redirect provenance, and a
  `Sources` section citing the official docs and community GitHub issues
  this was reverse-engineered against.

## 0.1.1 -- 2026-07-27

- `SKILL.md` now shows its own version number right under the title,
  kept in lockstep with `plugin.json`'s `version` -- an easy way to
  confirm an installed/cached copy actually picked up the latest skill
  content instead of a stale one.

## 0.1.0 -- 2026-07-25

Initial release.

- `usage_doctor`, `list_local_workspace`, `locate_export_download`,
  `parse_export`, `render_dashboard` MCP tools.
- Read-only, cross-platform probing of local Claude Desktop/Cowork/CLI data
  (`~/.claude`, Desktop app-data incl. Windows MSIX/3P variants, the
  user-visible `~/Claude` output folder), with redaction of secret-shaped
  keys and heavy MCP tool-schema blobs before anything is ever returned.
- claude.ai account data export parsing (`conversations.json`/
  `memories.json`/`projects.json`) into paged files, with defensive
  handling of a missing per-conversation project link.
- Self-contained HTML dashboard renderer with a local history log so
  re-running analysis later shows progress over time.
- `skills/claude-usage-analyzer/SKILL.md` + reference docs covering data
  sourcing, opt-in browser-assisted export requesting, the reorganization
  analysis method, and dashboard rendering/publishing.
- Closing the export-request loop: claude.ai only ever announces a ready
  export by email (the settings page itself has no ready/pending status),
  so the acquisition flow discovers the account's own email address and
  checks whether an already-connected mailbox (Gmail, Outlook/Microsoft
  365, etc.) is that same account. If so, it offers (opt-in, every time)
  to search for and act on the export-ready message automatically, with an
  optional scheduled recheck when a scheduling capability is also
  available. Otherwise it pauses and asks the user directly for the
  download link or the unzipped folder path.
- SessionStart hooks: shared `uv`-on-PATH check, plus a new
  `check_data_access.py` explaining Desktop Space/Project folder-scope
  requirements when no local data is visible.
- A second, independent "usage & best-practices" analysis lens alongside
  the reorganization proposal: project/chat counts, a model-usage
  breakdown, chats that used a more expensive/complex model than the task
  needed, context/prompting patterns worth a look, and evidence-based
  (not exhaustive) automation candidates. `list_local_workspace`'s CLI
  sessions now carry a per-message `models_used` tally (model id ->
  message count); `parse_export` carries the same field defensively, but
  it's confirmed empty in practice -- the claude.ai web export format
  doesn't record which model generated a response at all (checked
  directly: every conversation/message key, plus a raw-text regex for any
  "model"-containing key, found zero hits), and `stats.notes` says so.
  New `skills/claude-usage-analyzer/references/usage-efficiency.md`
  covers the method, including looking up the current model lineup each
  session (a "claude-api"-style skill or Anthropic's own docs) rather than
  hardcoding model names that would go stale. `render_dashboard`'s plan
  shape gains `model_usage` and `findings` sections for this.
- Model-usage data confirmed for Cowork/Chat local sessions too, not just
  CLI: each keeps its own nested per-session CLI-format transcript
  (`local_<uuid>/.claude/projects/.../*.jsonl`, same `message.model`
  format as the top-level CLI store) -- a session isn't necessarily one
  model throughout, since sub-agents/background steps can run a cheaper
  model mid-session. `list_local_workspace` now joins this in as
  `models_used`, falling back to each session's `default_model`/`effort`
  (its configured default, from `local_<uuid>.json`) when no nested
  transcript matched. New `skills/claude-usage-analyzer/references/
  data-sources.md` section 5 summarizes where model data does and doesn't
  live across every source.
- The usage & best-practices lens now also recommends skills, plugins,
  and connectors -- explicitly split into *already available* (an
  existing public skill/plugin/connector the user should just
  install/connect, found via whatever discovery capability the
  environment offers) versus *worth building custom* (nothing existing
  fits, so a bespoke Skill/plugin is the recommendation instead).
  `render_dashboard`'s plan shape gains a `recommendations` section for
  this.
- Analysis and dashboard generation only -- plan execution and full
  automation mining are deliberately out of scope for this version.
- The export-acquisition flow now treats an already-connected mail
  connector (Gmail, Outlook/Microsoft 365, etc.) as the expected case
  rather than a long-shot, since most client accounts already have one
  enabled, while still verifying it's the same mailbox as the claude.ai
  account before using it. Adds an explicit plain-language narration
  requirement throughout that flow -- since the person on the other end
  is typically a non-technical business user, every "want me to check?"
  now states what's being checked in the same breath, and email access
  (available, unavailable, or mismatched) is always stated outright
  rather than left implicit.
