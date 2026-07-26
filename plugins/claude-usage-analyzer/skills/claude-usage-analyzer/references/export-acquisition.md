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
  downloaded automatically past that point (see below).

## The request itself

Navigate to the account settings area and find the export-data control by
reading the actual page (visible labels/accessibility tree), not a
hard-coded path -- Settings has moved before and will again. Click it.

## The export is not instant -- say so

Requesting an export does not hand back a file immediately. claude.ai
processes the request and (historically) emails a download link some time
later -- this can be minutes to well over a day, and there's no reliable
way to make that faster or to synchronously wait for it in this session.
After requesting:

- Tell the user plainly that the request was submitted, that the download
  arrives by email (or, if the settings page shows a pending/ready export
  history, check whether it also shows a "ready" state to revisit later),
  and that this session can't just sit and wait for it.
- Ask them to come back once they have the download, and either paste the
  unzipped folder's path directly, or ask you to check back and re-drive
  the browser to the settings page's export history to see if it's ready
  (only if a browser tool is still available then).
- Don't imply the export is already in hand just because the request
  succeeded -- "requested" and "downloaded" are different states, and
  conflating them will send Step 3 (parse_export) looking for a folder
  that doesn't exist yet.

## Guardrails

- Never take an action beyond requesting the export -- no navigating
  elsewhere in account settings, no changing anything else, per the main
  skill file's non-goals.
- If mid-flow the user wants to stop or change their mind, stop
  immediately -- don't push through to "just finish requesting it anyway."
- If a step fails (control not where expected, page doesn't respond, an
  action is refused), stop and surface the failure -- don't guess at an
  alternate path silently.
