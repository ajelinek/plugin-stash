---
description: Review how you've actually been using Claude and build a dashboard of what to reorganize, fix, and stop paying for.
argument-hint: "[time window, e.g. 90d or 'all']"
---

Run the **claude-usage-analyzer** skill's workflow start to finish, exactly as
written in its `SKILL.md`. That file is the single source of truth for the steps
-- follow it there rather than working from anything restated here.

Time window requested for this run: **$ARGUMENTS**

Use it to fill the `since`/`until` pair the skill's data tools take:

- **Empty** -- do the skill's Step 2a as written: ask the user whether they want
  the full flow (all available history) or a time window, *before* pulling any
  data. Do not quietly default to all history; that choice changes what every
  number in the dashboard means.
- **"all"** (or an equivalent) -- full-history run: leave `since` and `until`
  unset.
- **Anything else** -- treat it as the window, preferring the relative form
  (`90d`, `6m`), and pass the same values to every data tool in the run, per the
  skill's Step 3.
