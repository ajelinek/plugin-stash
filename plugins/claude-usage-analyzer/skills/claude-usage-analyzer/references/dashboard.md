# Rendering and publishing the dashboard

`render_dashboard(plan, out_path=None)` does the actual HTML generation --
see the tool's own docstring for the exact `plan` shape (also documented in
references/reorganization.md's "The plan" section). This file covers what
to do with what it returns: `{path, html, history_runs}`.

## Try to publish it live, fall back to the local file

"Live artifact" means the dashboard should be something the user can keep
checking back on as this analysis re-runs over time, not a one-off file
they have to remember where they saved.

1. Search (`ToolSearch` or equivalent) for an Artifact-publishing
   capability in this session. Whether one is available depends on the
   environment this plugin ended up installed into (a Desktop/Cowork
   session may or may not expose it) -- don't assume either way, check.
2. **If available:** publish/update it using the returned `html` directly.
   On every later re-run, redeploy to the *same* artifact rather than
   minting a new one each time, so the user has one stable link to keep
   checking -- that's what makes it "live" rather than a fresh snapshot
   every time.
3. **If not available:** tell the user the dashboard was saved locally at
   `path` and that they can open it directly in a browser. This is not a
   lesser result -- the file itself is self-contained and already carries
   the history/progress-over-time section from `history_runs`.

Either way, mention `history_runs` in what you tell the user (e.g. "this is
run #4 -- the dashboard's progress section now covers all four").

## Re-running later

Nothing extra to do -- call `render_dashboard` again with a fresh `plan`
from a new analysis pass. The history log lives outside `out_path` (a
separate file in this plugin's state directory), so it accumulates
automatically across calls without the caller having to pass anything
history-related in.

## What the dashboard is not

It's a proposal, not a change log. Nothing about generating or re-generating
it moves, renames, or deletes a chat or Project -- see the main skill
file's "What this is" and Non-goals sections. Don't let a polished-looking
dashboard imply otherwise to the user.
