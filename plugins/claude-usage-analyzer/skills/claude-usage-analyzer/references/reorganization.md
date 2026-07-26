# Reorganization analysis

Given whatever combination of `list_local_workspace` and `parse_export`
output Step 2/3 landed on, answer: how should the user's *existing* chats
and Projects/Spaces be restructured?

## Reconstruct membership first if the direct link is missing

- **Local data:** use `membership` from `list_local_workspace` --
  `by_project_uuid`, `by_space_id`, and `unfiled_session_ids`. This is
  already reconstructed per references/data-sources.md section 3; don't
  redo it by hand.
- **Export data:** check `stats.conversations_with_project` from
  `parse_export` before anything else. Many export schemas simply don't
  carry a per-conversation project reference -- `project_uuid` reads as
  null on every record even when Projects exist and most chats genuinely
  belong to one. If `stats.notes` flags this (or `conversations_with_project`
  is 0 while `project_count` isn't), **do not report those chats as
  unaffiliated.** Instead infer real membership per conversation from:
  - `memory_context.md`'s per-project sections -- Claude's own synthesized
    summary of what each project's conversations are about.
  - Topic/keyword/name similarity between a conversation and each
    project's name/description in `projects_index.json`.

Only treat a chat as genuinely project-less once it's been checked against
both signals and found no match -- not because the raw link field was
empty.

## What to look for

- **Topic clusters** -- chats that are really the same kind of work spread
  across multiple threads (a sign they belong in one Project together).
- **Existing structure vs. reality** -- where a current Project/Space no
  longer matches what the chats inside it are actually about, using the
  reconstructed membership above.
- **Stale vs. active** -- old one-off threads (check `updated_at`/
  `last_activity_at`/`last_message_date`) that don't need a home vs. live
  work that does. This is the `status: "good"/"warning"/"critical"` signal
  the dashboard's stat tiles and project tags use.

If a recurring *workflow* pattern turns up (the same multi-step process
being manually re-explained chat after chat), that's automation-mining
territory, not this lens -- note it in one line and move on; this version
doesn't build that report (see the main skill file's non-goals).

Keep the reasoning visible to the user in summary form before jumping
straight to the polished dashboard -- a one-paragraph "here's what I found
and why" before Step 5 gives them a checkpoint.

## The plan (what feeds `render_dashboard`)

Build a `plan` dict per `render_dashboard`'s documented shape:

1. **`stat_tiles`** -- a handful of headline numbers: total chats analyzed,
   existing Project/Space count, proposed Project count, unfiled count,
   stale-vs-active split. Tag each with a `status` only when it's a
   genuine signal (e.g. a large unfiled count as `"warning"`), not
   decoratively.
2. **`projects`** -- one entry per proposed Project: `name`, `description`,
   `instructions` (the Project-level custom instructions text -- write
   real, usable instructions, not a placeholder), which existing `chats`
   move in (by name, not verbatim content), and what `files` (reference
   docs, knowledge files) it needs and why.
3. **`leftovers`** -- chats that don't fit any recommended Project. Say
   what you propose for them (archive, leave standalone, revisit later)
   rather than silently dropping them from the plan.
4. **`notes`** -- data-quality caveats carried over from Step 1-3 (thin
   export, missing project links, local-only vs. combined, etc.).

Then return to the main skill file's Step 5 (render) and Step 6 (review).
