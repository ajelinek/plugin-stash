---
name: audiobook-creator
description: >
  Converts a document's text into a real, chaptered .m4b audiobook using
  local, offline neural TTS (Kokoro-82M) -- no cloud API, no per-use cost, no
  text ever leaving the machine. Supports two modes: Plain narration
  (exact-text reading, one voice, maximum fidelity) and Story mode (rewrites
  dry/dense source material into an engaging themed narrative -- noir
  detective, true-crime podcast, kids' bedtime story, sports-commentary
  energy, whatever theme the user names -- with automatic narrator/character
  multi-voice assignment). Output is one .m4b file with chapter markers
  embedded via FFMETADATA1 (not raw MP3s or an .m3u playlist), so it gets
  real chapter navigation, resume position, and speed controls in Apple
  Books, Podcasts, Overcast, and other audiobook apps. Trigger this whenever
  the user wants to listen to a document instead of reading it -- "turn this
  into an audiobook," "make this something I can listen to on my
  commute/drive/walk," "read this to me," "narrate this doc," "convert this
  report into a story," mentions of .m4b / audiobook / TTS / text-to-speech /
  narration applied to a document, or dry/technical source material they want
  turned into something more engaging to listen to. Also trigger when they
  want the result delivered to a specific folder (e.g. one synced to their
  phone via OneDrive) rather than left in the working directory.
---

# Audiobook Creator

## What this is

Turns written documents into real audiobook files you can listen to away
from a screen. Two mechanically different halves, and it matters which is
which: **you** do the parts that need actual comprehension -- reading the
text, deciding chapter boundaries, and for story mode, rewriting dry content
into an engaging narrative and deciding which lines get which voice. The
**plugin's tools** do the parts that are pure mechanism once your decisions
exist -- speech synthesis, audio concatenation, chapter-timestamp math, and
encoding. Don't try to do the tools' job by hand, and don't try to make the
tools do your job -- see
[references/script-schema.md](references/script-schema.md) for the exact
handoff between the two halves.

Everything runs locally on this machine. Nothing is uploaded, and the model
is the same one every run, so there's no per-use cost and no rate limit.

## Step 0: Check setup

Call **`check_setup`** before anything else. It verifies `ffmpeg` is on PATH
and that the Kokoro model (~120MB, two files) is cached, downloading it if
this is the first run on this machine. Cached at
`~/.cache/audiobook-creator/models/`, so every run after the first is fully
offline.

Do this **first**, not after building a script. A missing `ffmpeg` or a
failed download should cost seconds, not be discovered after you've written a
full narrative. If `ready` is false, show the user the `blockers` text --
it names the exact install command -- and stop.

The download is one blocking call. If it dies partway, the partial file is
cleaned up; just call `check_setup` again.

## Step 1: Get the source text

**The skill's input is markdown.** Getting text out of a PDF, a Word file, a
web page, or anything else is upstream of this skill and not part of it -- if
what you have isn't markdown, convert it to markdown first, then continue
here. Use whatever heading structure the result has.

**Pasted text** needs no conversion. If it's unstructured prose with no
headings, ask the user for a title and chapter breakdown rather than
guessing one.

## Step 2: Pick a mode

- **No theme given → Plain mode.** Verbatim reading, one voice.
- **User names a theme** ("noir detective story," "kids' bedtime
  adventure," etc.) **→ Story mode.** If they want story mode but aren't
  sure what theme to ask for, offer a few varied examples -- don't force a
  fixed dropdown; theme stays freeform.

## Step 3: Build the script

Both modes produce the same structure -- see
[references/script-schema.md](references/script-schema.md) for the full
schema, required fields, and validation rules. The short version: a list of
chapters, each with a title and a list of `{voice, text}` lines. You pass it
directly as the `script` argument; there is no file to write.

**Every character in `text` is spoken.** There is no emphasis syntax, no SSML,
and no stress markup -- `[word](+2)` comes out as "word plus two" and
`<emphasis>` comes out as "emphasis". Emphasis is achieved with sentence
structure, punctuation, per-line `speed`, and voice choice; see
[references/kokoro-prosody-guide.md](references/kokoro-prosody-guide.md).

**Plain mode:**
- Pick one voice from [references/voice-roster.md](references/voice-roster.md)
  -- default `af_heart`, or `am_michael` if the user wants male, or whatever
  they name explicitly. Every line in the whole document uses that one voice.
- H1 headings become chapter boundaries, in order, titled from the heading
  text. No H1 headings at all → the whole document is a single chapter.
- Strip markdown syntax (`**bold**`, `[text](url)`, etc.) when writing each
  line's `text` -- it's read aloud exactly as written, so anything left in
  that shouldn't be spoken gets spoken. Split each chapter into one line per
  paragraph; this is what gives the audio natural pause points, and costs
  nothing since it's still one voice throughout.
- No embellishment, no rewriting, no interpretation -- if in doubt, prefer
  the more literal reading.
- **Prosody control:** Use punctuation naturally (commas, semicolons, em-dashes)
  to guide intonation. Add optional per-line `speed` overrides for pacing
  variation (e.g., 0.9 for serious/reflective passages, 1.05 for energetic
  ones). See [references/kokoro-prosody-guide.md](references/kokoro-prosody-guide.md)
  for detailed techniques to prevent monotone reading.

**Story mode:**
- Read [references/story-mode-guide.md](references/story-mode-guide.md)
  before writing anything -- it covers how to rewrite for the requested
  theme while preserving the source's actual information, and how to decide
  whether/how to split lines across a narrator voice and a second voice.
- The chapter structure doesn't have to mirror the source's own headings --
  build whatever chapter breaks serve the narrative you're writing.
- **Prosody control:** Use voice assignment, punctuation, and optional per-line
  `speed` to reinforce character personalities and emotional beats. A warm
  narrator voice paired with fast speech (1.1) creates energy; a deeper voice
  at 0.9 adds gravitas. See [references/kokoro-prosody-guide.md](references/kokoro-prosody-guide.md)
  for techniques and examples.

## Step 4: Start the job and poll it

Call **`start_synthesis`** with the script (and optionally a global `speed`,
0.5-2.0, default 1.0). It validates everything up front -- missing fields,
empty chapters, an unknown voice ID, an out-of-range speed -- and returns an
`error` before synthesizing any audio, so a mistake costs milliseconds
instead of the full runtime.

On success it returns a **`job_id` immediately**. Synthesis runs in the
background because it takes roughly half the finished audio's own duration --
a one-hour audiobook is around 30 minutes of CPU. Poll **`synthesis_status`**
with the job id until `state` is `done` or `failed`.

While polling, tell the user what's happening rather than going quiet:
`percent`, `lines_done`/`lines_total`, and `detail` all say where the run is.
Space the polls sensibly -- every few seconds on a short doc, far less often
on a long one. Don't poll in a tight loop.

If `start_synthesis` reports a non-zero `concurrent_jobs`, another synthesis is
already running on the same CPU and both will take considerably longer than
their estimates. Say so rather than letting the user watch a short book crawl.

**If the job is lost, rerun it.** Job state is held in memory only, so if the
plugin's process restarts mid-run (Desktop restart, plugin update, a crash)
`synthesis_status` will report an unknown job id. There's no resume; just
start it again. Nothing is durable until the job reports `done` and the
`.m4b` exists at the output path.

## Step 5: Report back

Tell the user the file path, total duration, and chapter count/titles --
never just "done."

## Output directory: always required, never assumed

`output_dir` must come from the user, explicitly, every time -- never
hardcode it, never default to the working directory or a "reasonable-looking"
guess. **Ask for it before running anything**: it's a required field with no
default, and it's wasted synthesis time to build the file somewhere the user
didn't ask for and then move it.

In practice this is usually a local folder that's synced to OneDrive (or
similar), so the finished file shows up on their phone with zero extra steps
-- but this skill only writes to a local folder; whatever sync client is
watching it does the rest. If the file isn't showing up on the phone, that's
a sync-client problem, not something to debug here.

## Non-goals

- **No text extraction.** Markdown in. Converting a PDF/Word/HTML source to
  markdown happens before this skill is involved.
- **No voice cloning or custom voices.** The roster is fixed at the 6
  curated IDs in [references/voice-roster.md](references/voice-roster.md).
- **No manual voice-to-role override in story mode.** Assignment is
  automatic, per document. If it's off on a given line, that's worth
  mentioning in your summary to the user, not something to work around
  mid-pipeline.
- **No background music, sound effects, or audio mixing** beyond narration
  and chapter markers.
- **Not a OneDrive/cloud-upload integration.** Writes to a local folder,
  full stop.
- **Not real-time/streaming narration.** Produces a finished file, not a
  live read-aloud.
- **Not a bulk/batch tool.** One document, one `.m4b`, one deliberate run.

## Known limitations

- **`ffmpeg` is a hard system dependency**, not pip/uv-installable.
  `check_setup` says so plainly rather than failing deep inside a subprocess.
- **No resume and no cancel.** A job that dies is rerun from the start. This
  is deliberate: the failure is rare and the recovery is cheap, so the
  alternative was state that has to stay correct for no real gain.
- **Story mode has no deterministic quality guarantee.** The rewrite's tone,
  length, and pacing depend on your judgment each run -- there's no fixed
  template enforcing consistency between two runs of the same source.
- **Automatic voice-role assignment is heuristic, not rule-based.** It can
  occasionally put a line on the wrong voice. No manual override exists yet.
- **CPU-only synthesis.** Kokoro-82M runs faster than real-time on a modern
  Mac (roughly half the audio's own duration, per local testing), but a
  long document still takes real wall-clock time -- set expectations with
  the user for anything book-length rather than a short doc.

## Supporting files

| File | Purpose |
|---|---|
| `references/script-schema.md` | The full script structure, field-by-field, including optional per-line `speed`, the validation rules, and what `synthesis_status` returns |
| `references/voice-roster.md` | The 6 curated voice IDs, their roles/notes, and how to choose between them in each mode |
| `references/story-mode-guide.md` | How to rewrite source material into a themed narrative without fabricating content, and how to decide on narrator/character voice splits |
| `references/kokoro-prosody-guide.md` | Detailed techniques for using Kokoro's punctuation, speed control, stress markup (`[word](±N)`), voice selection, and capitalization to create expressive narration and prevent flat/monotone reading |
