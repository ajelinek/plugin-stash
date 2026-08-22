# Story mode: rewriting for engagement

Story mode exists for exactly one reason: dense, dry, fact-heavy source
material is unpleasant to listen to read flat for twenty minutes, in a way
it isn't unpleasant to *read* flat for twenty minutes. A wall of text on a
screen lets the eye skim ahead, re-read a sentence, skip a paragraph that's
obviously boilerplate. Audio has none of that -- it's linear and it's paced
by someone else. Narrative shape (a hook, a throughline, pacing, maybe a
recurring voice explaining things) is what makes that linear, un-skippable
format tolerable for dry content. Plain mode is the right tool when fidelity
to the exact text matters more than that; story mode is the right tool when
holding attention matters more.

## What triggers it

The user supplies a **theme** -- a freeform description of style/genre
("noir detective story," "kids' bedtime adventure," "sports-commentary
energy," "true-crime podcast tone"). There's no fixed menu. If they're not
sure what to ask for, offer a handful of examples spanning different moods
(one atmospheric/narrative, one comedic, one high-energy, one calm/soothing)
-- but don't constrain the actual input to a preset list.

## The rewrite: reframe, don't fabricate

The underlying information from the source document must survive the
rewrite. This is a reframing of real content into a new *shape*, not a
license to invent facts, numbers, or claims that aren't in the source. If
the source is a quarterly report, the noir version still needs to convey the
actual figures and decisions -- it just delivers them as a detective's case
notes instead of a bulleted deck.

Concretely, this usually means:

- **A hook.** Open with something that earns attention before the source
  material's own opening line would.
- **A throughline.** A framing device (an investigation, a journey, a
  countdown) or a recurring character who reacts to / explains the material,
  rather than a flat list of sections.
- **Pacing.** Vary sentence rhythm and paragraph length the way a written
  story would, instead of mirroring the source's own (often uniform,
  report-style) structure.

What it does *not* mean: padding with fluff that isn't tied to the source,
changing what actually happened/was decided/was measured, or a rewrite so
loose that someone who knows the source material wouldn't recognize the
content underneath the theme.

## Deciding on voices

After the rewrite, decide whether multiple voices genuinely help. This is a
per-document judgment call, not a checkbox -- some themes are better served
by one committed narrator voice performing the whole thing than by splitting
it across two. Reach for a second voice when the rewrite naturally produces:

- A narrator plus one recurring character who explains, reacts to, or
  argues about the material (e.g. an analyst voice interjecting on a case
  narrator's findings).
- Actual dialogue exchanges, if the theme calls for a scene rather than
  continuous narration.

Pick voices from [voice-roster.md](voice-roster.md) -- typically an
opposite-gender pair for maximum contrast, or same-gender-different-accent
if the theme calls for two characters who should still read as similar in
kind (e.g. two colleagues) but distinguishable by ear. Assign every line
in the chapter's `lines` list to one of the two (or more) chosen voices; see
[script-schema.md](script-schema.md).

**Don't reach for multi-voice by default.** If the rewritten narrative
doesn't naturally produce a second recurring speaker, one voice performing
it well beats two voices used for their own sake.

## No manual voice-role override (yet)

There's no user-facing step where they approve or adjust your voice
assignments before synthesis -- you decide, you commit, the audiobook gets
built. If the automatic assignment misses (wrong line gets the character
voice, a one-off aside gets promoted to its own voice when it shouldn't),
that's expected occasionally and not a bug to work around mid-pipeline --
just mention it in your summary back to the user as something to note for
next time, and let them ask for a specific line's voice to change on a
rerun if it bothered them.

## Worked example

**Source:** a dry internal report: "Q3 revenue grew 4%, driven primarily by
the enterprise segment. Churn in the SMB segment increased to 6.2%,
attributed to a pricing change rolled out in July."

**Theme:** "noir detective story"

**Rewrite (narrator voice, `af_heart`):**
> The numbers came in on a Tuesday, and they weren't clean. Revenue was up --
> four percent, enterprise money mostly, the kind that pays on time and
> doesn't ask questions. But something was bleeding out the other side.

**Character voice (`am_michael`), the analyst who found it:**
> Churn. SMB segment. Six point two percent, up from where it should've
> been. I traced it back to July -- a pricing change nobody flagged as the
> reason people were walking.

Notice the actual figures (4%, 6.2%, July pricing change, enterprise vs. SMB)
are all still there -- only the delivery changed.
