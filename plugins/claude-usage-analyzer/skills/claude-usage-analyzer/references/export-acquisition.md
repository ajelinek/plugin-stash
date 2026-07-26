# Requesting an account export via browser automation

This only applies in Step 2 of the main skill file, when the user doesn't
already have an unzipped export folder and agrees to try automating the
*request*. It is deliberately light on exact click-paths -- the claude.ai
UI changes, and hard-coded selectors go stale. Drive it by reading the live
page, not by recalling a fixed layout from training data.

## This plugin has no browser of its own

Nothing here bundles browser-automation code. The skill instead:

1. Searches (`ToolSearch` or equivalent) for a browser-automation /
   computer-use tool already available and enabled in this environment.
2. If none is found, says so and stops -- fall back to asking the user to
   request/download the export by hand (Settings > Account > Export Data
   in their own browser) and hand you the unzipped folder's path once it
   arrives.
3. If one is found, asks the user explicitly whether they want it used for
   this. A prior approval from an earlier session doesn't carry over --
   ask again each time.

## Before starting (if the user agrees)

- Confirm the user is actually logged into Claude in the browser the tool
  controls. If a login/auth screen shows up instead of the account
  settings, **stop and tell the user** rather than trying to work around
  it -- this skill never touches credentials, 2FA, or session cookies
  itself.
- State in short form what's about to happen: navigate to Settings >
  Account, find the data export control, request an export. Nothing is
  downloaded automatically past that point unless the recheck flow below
  is also set up.

## The request itself

Navigate to the account settings area and find the export-data control by
reading the actual page (visible labels/accessibility tree), not a
hard-coded path -- Settings has moved before and will again. Click it.

While there, also check (by reading the page, not assuming) whether it
shows any export history or a pending/ready state for past requests --
that's what the recheck flow below revisits. If the UI has nothing like
that, say so and skip straight to "If no recheck is possible or wanted."

## The export is not instant -- offer to close the loop

Requesting an export does not hand back a file immediately. claude.ai
processes the request and (historically) emails a download link some time
later -- this can be minutes to well over a day, and there's no reliable
way to make that faster or to synchronously wait for it in this session.
Don't imply the export is already in hand just because the request
succeeded -- "requested" and "downloaded" are different states, and
conflating them will send Step 3 (`parse_export`) looking for a folder
that doesn't exist yet.

Right after requesting, search (`ToolSearch` or equivalent) for a
scheduling capability in this environment -- a scheduled-task feature, a
Routine/trigger creator, anything that can deliver a message or resume
this work later without the user having to remember to come back. What
it's called and how it's invoked varies by environment; don't assume a
specific tool name, just look for the capability.

### If a scheduling capability is found

Ask the user explicitly (opt-in, every time -- no carried-over approval):
something like *"I can check back in about an hour and grab the download
automatically if it's ready by then -- want me to?"* If yes:

1. Schedule **one** recheck, roughly an hour out. Don't schedule a tight
   polling loop -- exports don't arrive that fast, and repeated
   close-together checks just burn the browser tool for nothing.
2. When it fires, re-confirm the browser tool (and the user's login) are
   still available, then revisit the settings page's export
   history/pending state -- read the live page again rather than assuming
   it looks the same as before.
3. **If it shows ready/downloadable:** trigger the download, then call
   `locate_export_download` (defaults to this OS's Downloads folder) to
   find the newly-downloaded zip and unpack it. It matches by the zip
   actually containing `conversations.json`, not by filename, and returns
   `ambiguous: true` with every candidate if more than one zip looks like
   an export -- ask the user to pick rather than guessing which is
   current. Once resolved, tell the user it's done and offer to move
   straight on to Step 3 (`parse_export`) with the returned `export_dir`.
4. **If it's still not ready:** reschedule one more recheck at the same
   interval -- but cap this at around 6-8 attempts total (roughly a
   workday's worth). Past that, stop rescheduling automatically and ask
   the user whether to keep trying or just wait for the email and hand you
   the path themselves. Don't set up an open-ended/indefinite recheck --
   it should end in either a completed download or an explicit
   handoff back to the user, never just quietly keep running forever.
5. If the settings page turns out not to show any ready/pending state at
   all (some export UIs are request-only, with status only ever
   communicated by email), say so plainly and fall back to the manual
   flow below instead of continuing to reschedule blind rechecks against
   a page that can't tell you anything new.

### If no recheck is possible or wanted

No scheduling capability found, the user declines it, or the settings page
has no ready-state to check: tell the user plainly that the download
arrives by email, and ask them to either paste the unzipped folder's path
directly once they have it, or come back and ask you to re-drive the
browser to the settings page later. `locate_export_download` still works
standalone at that point too -- once the user has actually downloaded the
zip (however they got there), it can find and unpack it without needing
the scheduled flow at all.

## Guardrails

- Never take an action beyond requesting the export and (if set up)
  completing the recheck/download -- no navigating elsewhere in account
  settings, no changing anything else, per the main skill file's
  non-goals.
- The recheck flow never touches email -- it only ever revisits the
  settings page claude.ai itself shows, and only with the same
  browser-automation tool already approved for the request. It doesn't
  add a new permission surface beyond what was already granted.
- If mid-flow the user wants to stop or change their mind, stop
  immediately -- including canceling a scheduled recheck if one was set
  up, don't let it fire anyway.
- If a step fails (control not where expected, page doesn't respond, an
  action is refused, the scheduling capability itself errors), stop and
  surface the failure -- don't guess at an alternate path silently.
