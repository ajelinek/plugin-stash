# Reorganization analysis

Given whatever combination of `list_local_workspace` and `parse_export`
output Step 2/3 landed on, answer: how should the user's *existing* chats
and Projects/Spaces be restructured?

## Start from the app's own record of where each chat lives

**Claude Desktop writes down which Space a chat is in, and this lens reads
it.** Every session file carries a `spaceId` the moment the user files the
chat, and it resolves straight through `spaces.json`. That is the user's
own filing, not an inference, and it is where this analysis starts.

- **Local data:** use `membership` from `list_local_workspace` --
  `by_project_uuid`, `by_space_id`, `unfiled_session_ids` -- and the
  per-session attribution spliced onto every `local_sessions` row (and
  onto every `get_project_membership` row; they read the same map, so the
  two tools cannot disagree). Full mechanics in
  [data-sources.md section 2](data-sources.md#2-which-space-or-cloud-project-a-chat-is-in----the-join-join_local_sessions-does).
  Don't redo the join by hand.
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

  This *is* a reconstruction, and it should be presented the way an
  inferred local attribution is (below) -- as this analysis's guess, never
  as the account's own filing. Only treat a chat as genuinely project-less
  once it's been checked against both signals and found no match -- not
  because the raw link field was empty.

## Say which attributions are recorded and which are guessed

Every local session comes back with its provenance attached. This is the
single most important thing this lens has to get right, because it decides
how much confidence a proposal deserves:

| Field | Read it as |
|---|---|
| `space_source: "app_record"` | The user filed this chat here themselves. A fact about their workspace. |
| `space_source: "inferred_from_folder"` | **A guess.** Claude recorded no Space, so this plugin matched the chat's working folder to the Space that owns it. |
| `space_source: "none"` | Not in any Space. |
| `space_source_note` | The same thing as a sentence you can put in front of the user. `space_source_notes` on the response carries all three. |
| `space_from_app_record` | The boolean shortcut. |
| `space_matched_folder` | Which Space folder a guess matched. Populated **only** for a guess, so it never reads as evidence for a recorded fact. |
| `space_label` | Always readable prose -- the Space's real name, or a sentence explaining why there isn't one. **Never a bare uuid. Quote this**, not `space_id`. |
| `space_resolved` / `space_resolution` | Whether the id turned into a name, and which of four outcomes. |

**Write the difference into the output, not just into your reasoning.**
"Claude has this chat filed under *Vacation - July 2026*" and "this chat
was working in a folder that belongs to *Vacation - July 2026*, though
Claude didn't record a Space for it" are different claims. A proposal that
moves twenty chats out of a Space is a much bigger ask if the plugin only
guessed they were there. Where a recommendation rests on inferred rows,
say so in the plan's `notes` and, if it is load-bearing, in the finding
itself.

Two more fields on the response worth reading before you conclude anything:

- **`unresolved_space_ids`** -- Space ids the app recorded that
  `spaces.json` no longer names, most likely Spaces deleted after chats
  were filed in them. Chats pointing at one are not unfiled; they are
  filed somewhere that no longer exists, which is its own finding.
- **`uncached_project_uuids`** -- cloud Projects a session declares that
  this device has no cached metadata for. The membership is real; only the
  *name* is missing. Never demote these to unfiled.

### Why this section exists: the bug it replaced

Until 0.6.2 this plugin believed the app recorded no Space at all, and
rebuilt membership by matching each chat's folder against Space folders by
string prefix, taking the first Space in file order. On the machine it was
corrected against, that disagreed with the app's own record on **all 59**
sessions that had one. A "OneDrive-Personal" Space scoped to a whole
cloud-storage root contains, by prefix, every Space nested beneath it -- so
36 chats the user had filed under "Vacation - July 2026" were reported in
the generic cloud-storage Space instead, and "Vacation - July 2026" and
"ACTs 2027" were reported as **empty Spaces**.

Nothing about that output looked wrong. The Spaces were real, the folder
was real, the counts added up, and the lens confidently proposed
reorganizing a workspace the user did not have. Two rules now keep the
fallback honest: the **most specific** matching Space folder wins rather
than the first, and matching happens on a **path boundary** so `/a/b`
cannot claim `/a/bc`. Both exist because of that failure -- see
data-sources.md section 2.

The practical instruction: an empty Space is a *finding*, and findings
like that are the ones to double-check against `space_source` before you
report them.

## What to look for

- **Topic clusters** -- chats that are really the same kind of work spread
  across multiple threads (a sign they belong in one Project together).
- **Existing structure vs. reality** -- where a current Project/Space no
  longer matches what the chats inside it are actually about, using the
  membership above.
- **Stale vs. active** -- old one-off threads that don't need a home vs.
  live work that does. Read `last_activity_at_local_date` on local
  sessions and `updated_at` on export conversations; every local timestamp
  also comes back as `*_local_iso` / `*_local_weekday` in the reader's own
  timezone, and those are the ones to quote (see
  [data-sources.md section 8](data-sources.md#8-time-scoping-every-read)).
  This is the `status: "good"/"warning"/"critical"` signal the dashboard's
  stat tiles and project tags use.

If a recurring *workflow* pattern turns up (the same multi-step process
being manually re-explained chat after chat), that's the usage &
best-practices lens's territory, not this one -- note it in one line here
and let [usage-efficiency.md](usage-efficiency.md)'s automation-candidate
flag (Step 5) pick it up with evidence; don't build a full automation
recommendation out in this lens (see the main skill file's Non-goals).

Keep the reasoning visible to the user in summary form before jumping
straight to the polished dashboard -- a one-paragraph "here's what I found
and why" before Step 7 gives them a checkpoint.

## The plan (what feeds `render_dashboard`)

Build a `plan` dict per `render_dashboard`'s documented shape:

1. **`stat_tiles`** -- a handful of headline numbers: total chats analyzed,
   existing Project/Space count, proposed Project count, unfiled count,
   stale-vs-active split. Tag each with a `status` only when it's a
   genuine signal (e.g. a large unfiled count as `"warning"`), not
   decoratively. If any of those counts leans on inferred attributions,
   the tile is not the place to explain it -- put it in `notes`.
2. **`projects`** -- one entry per proposed Project: `name`, `description`,
   `instructions` (the Project-level custom instructions text -- write
   real, usable instructions, not a placeholder), which existing `chats`
   move in (by name, not verbatim content), and what `files` (reference
   docs, knowledge files) it needs and why.

   `files` is not filing for its own sake, and it's worth telling the user
   the reason. **Documents uploaded to project knowledge are cached, and
   only new or uncached portions count against usage limits on reuse** --
   which makes moving a repeatedly-pasted document into project knowledge
   the single highest-leverage efficiency change available in the whole
   analysis. It also fixes a related trap: **context is not shared between
   chats in a Project unless it is in the knowledge base**, so anything the
   user expects sibling chats to "already know" has to live there.

   Two more things worth knowing before recommending a large knowledge
   base. On paid plans, retrieval kicks in automatically as project
   knowledge approaches the context limit, expanding capacity rather than
   truncating -- so pruning purely to save context is unnecessary.
   Descriptive filenames and related content grouped together are the
   documented ways to make that retrieval work well.
3. **`leftovers`** -- chats that don't fit any recommended Project. Say
   what you propose for them (archive, leave standalone, revisit later)
   rather than silently dropping them from the plan.
4. **`notes`** -- data-quality caveats carried over from Step 1-3 (thin
   export, missing project links, local-only vs. combined, the time
   window, and how many attributions were inferred rather than recorded).

Then continue to the main skill file's Step 5 (usage & best-practices
lens) before Step 7 (render) and Step 8 (review).
