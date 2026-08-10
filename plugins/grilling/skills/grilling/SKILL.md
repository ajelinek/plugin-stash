---
name: grilling
description: >
  Grill the user relentlessly about a plan, decision, or idea. Use when the
  user wants to stress-test their thinking, or uses any 'grill' trigger
  phrases.
---

# Grilling

Interview the user relentlessly about every aspect of the plan, decision, or
idea until you reach a shared understanding. Walk down each branch of the
decision tree, resolving dependencies between decisions one by one. For each
question, provide your recommended answer.

## Rules

1. **Ask every question through the question tool.** Use the structured
   question tool (`AskUserQuestion`) for every question in the interview —
   never ask as plain prose text, even for open-ended ones. It always offers
   an "Other" choice, so free-text answers stay available.
2. **One question at a time.** Each `AskUserQuestion` call carries exactly
   one question. Asking multiple at once (whether as multiple questions in
   one call, or as separate prose questions) is bewildering — wait for the
   user's answer before asking the next.
3. **Recommend, don't just ask.** Put your recommended answer as the first
   option, with "(Recommended)" appended to its label, and use that option's
   `description` to state why. Cover the realistic branches with the
   remaining options — the user can always fall back to "Other" for anything
   not listed.
4. **Look things up, don't ask.** If a fact can be found by exploring the
   environment (filesystem, tools, code, docs, etc.), look it up yourself
   rather than asking the user. Reserve questions for things that are
   genuinely the user's decision to make.
5. **Follow the dependency chain.** Walk down each branch of the decision
   tree in order — resolve a decision before moving to the ones that depend
   on it. If an answer changes what's downstream, re-derive the affected
   branch before continuing rather than asking questions that are now moot.
6. **Don't act until confirmed.** Do not implement, write, or execute
   anything based on this interview until the user explicitly confirms that
   shared understanding has been reached.

## Ending the interview

Once every branch has been resolved, summarize the shared understanding in
plain language — the plan or decision as agreed, including every point where
the user overrode your recommendation — then ask for final confirmation
using the question tool (e.g. an "Looks right, proceed" vs. "No, revisit
something" choice) before doing anything else.
