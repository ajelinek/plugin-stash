# Requesting an account export via browser automation

This only applies in Step 2 of the main skill file, when the user doesn't
already have an unzipped export folder and agrees to try automating the
*request*. It is deliberately light on exact click-paths -- the claude.ai
UI changes, and hard-coded selectors go stale. Drive it by reading the live
page, not by recalling a fixed layout from training data.

The account settings page does **not** expose an export ready/pending
status -- claude.ai communicates that only by email once the export is
ready. Don't look for or poll an in-page status; the sections below cover
the two ways to close the loop instead (an email check, or a manual
handoff), not a web recheck.

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
  Account, find the data export control, request an export, and note the
  account's own email address while there (needed below). Nothing is
  downloaded automatically past that point unless the email-check flow
  below is also set up.

## The request itself

Navigate to the account settings area and find the export-data control by
reading the actual page (visible labels/accessibility tree), not a
hard-coded path -- Settings has moved before and will again. Click it.

While there, also read (from the live page, not assumed) the account's own
email address shown in the profile/account section. Keep it -- the email
check below needs it to confirm a connected mailbox is actually the same
account before searching it.

## The export is not instant -- offer to close the loop

Requesting an export does not hand back a file immediately. claude.ai
processes the request and emails a download link some time later -- this
can be minutes to well over a day, and there's no reliable way to make
that faster or to synchronously wait for it in this session, and (as
above) no in-page status to poll either. Email is the only channel
claude.ai ships a status through. Don't imply the export is already in
hand just because the request succeeded -- "requested" and "downloaded"
are different states, and conflating them will send Step 3 (`parse_export`)
looking for a folder that doesn't exist yet.

## Checking whether an automated email check is possible

Right after requesting:

1. Search (`ToolSearch` or equivalent) for an email-reading connector
   already available and enabled in this environment -- Gmail,
   Outlook/Microsoft 365, or any other configured mail integration. If
   none exists, there's no automated path here: skip straight to "If no
   automated email check is possible or wanted" below.
2. If one exists, confirm it's actually the **same** mailbox as the
   claude.ai account before using it for anything -- never assume a
   connected mail tool happens to belong to the right account. Compare
   against the email address read from the claude.ai settings page above:
   - Some connectors expose their own authenticated identity directly
     (e.g. a `get_me`/profile-style tool) -- use that if available.
   - If a connector has no such tool, don't guess: confirm some other way
     (e.g. ask the user to confirm the connected mailbox's address
     matches) rather than searching a mailbox that might belong to
     someone else.
3. Only once the mailbox is confirmed to match, treat an automated email
   check as possible and move to the opt-in flow below. Otherwise, treat
   it exactly like "no connector found" -- fall through to the manual
   handoff.

## If an automated email check is possible

Ask the user explicitly (opt-in, every time -- no carried-over approval):
something like *"I found [connector] connected to the same email as your
claude.ai account -- want me to check it for the export-ready message
instead of you having to watch for it?"* If yes:

1. Search that mailbox narrowly: messages from claude.ai/Anthropic (sender
   containing `anthropic.com` or `claude.ai`) received after the export was
   requested, about the data export being ready. This is the one message
   this flow needs -- don't run an open-ended inbox search or read
   unrelated mail while you're in there.
2. **If found:** the message's download link is tied to the user's
   authenticated claude.ai session, so a plain HTTP fetch won't carry it --
   open the link with the same browser-automation tool already approved
   for the request instead, which should trigger the download. Then call
   `locate_export_download` (defaults to this OS's Downloads folder) to
   find the newly-downloaded zip and unpack it. It matches by the zip
   actually containing `conversations.json`, not by filename, and returns
   `ambiguous: true` with every candidate if more than one zip looks like
   an export -- ask the user to pick rather than guessing which is
   current. Once resolved, tell the user it's done and offer to move
   straight on to Step 3 (`parse_export`) with the returned `export_dir`.
3. **If not found yet:** search (`ToolSearch` or equivalent) for a
   scheduling capability in this environment -- a scheduled-task feature, a
   Routine/trigger creator, anything that can deliver a message or resume
   this work later without the user having to remember to come back.
   - If found, ask the user whether to schedule one recheck roughly an
     hour out (same opt-in-every-time rule). Don't schedule a tight
     polling loop -- exports don't arrive that fast, and repeated
     close-together checks just burn the connector for nothing. When it
     fires, re-confirm the mailbox connector is still available, then
     re-run the narrow search above. Cap this at around 6-8 attempts total
     (roughly a workday's worth); past that, stop rescheduling
     automatically and ask the user whether to keep trying or just wait
     for the email and hand you the link or folder path themselves. Don't
     set up an open-ended/indefinite recheck.
   - If no scheduling capability is found, or the user doesn't want one,
     tell them plainly the export hasn't arrived yet, and either check
     back later yourself if asked or wait for the user to notice it and
     come back.
4. If mid-flow the mailbox connector itself is revoked, or a search comes
   back with an access/auth error, stop and say so -- don't keep retrying a
   broken connector or fall silently back to guessing.

## If no automated email check is possible or wanted

No mail connector found, no mailbox match confirmed, or the user declines:
**pause here and ask the user directly for the download link from the
export-ready email.** If they've already followed that link and downloaded
(and unzipped) the file themselves, the folder path works just as well --
`locate_export_download` still works standalone once they hand you a
downloaded zip, without needing anything above.

If the user gives you the link instead of a folder: open it with the
browser-automation tool if one is available and already approved (same
reasoning as above -- it needs their authenticated session), then run
`locate_export_download`. Otherwise ask them to open it themselves and hand
you the resulting unzipped folder path.

## Guardrails

- Never take an action beyond requesting the export and (if set up)
  completing the email check/download -- no navigating elsewhere in
  account settings, no changing anything else, per the main skill file's
  non-goals.
- Reading email is exactly as opt-in as browser automation: ask every
  time, no carried-over approval, and only after the mailbox is confirmed
  to match the claude.ai account -- never search a mailbox you haven't
  confirmed belongs to the user's claude.ai account.
- Keep any mailbox search scoped to the one export-ready message -- don't
  browse, label, reply to, or otherwise act on unrelated mail while you're
  in there.
- If mid-flow the user wants to stop or change their mind, stop
  immediately -- including canceling a scheduled recheck if one was set
  up, don't let it fire anyway.
- If a step fails (control not where expected, page doesn't respond, an
  action is refused, the scheduling or mail-connector capability itself
  errors), stop and surface the failure -- don't guess at an alternate
  path silently.
