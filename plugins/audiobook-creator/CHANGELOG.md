# Changelog

## 0.1.1 -- 2026-08-19

Review pass over the 0.1.0 port. No API changes; every tool takes and returns
what it did before.

**The prosody guide was teaching a syntax this engine doesn't have.** Its
"Emphasis & Stress Markup" section documented Misaki's `[word](+2)` notation,
which belongs to the PyTorch `kokoro` package. This plugin runs `kokoro-onnx`,
which phonemizes through espeak-ng instead, so `[best](+2)` was going into the
audio as *"best plus two"* -- verified against the real phonemizer. The section
is gone, replaced with what actually controls emphasis here (structure,
punctuation, `speed`, voice) and a worked example of what markup sounds like
when it leaks. The "Capitalization for Clarity" section was wrong the same way
-- espeak normalizes case away, and `GPU` and `gpu` phonemize identically -- so
it now says so, and documents the one thing that *does* need normalizing:
numbers, dates, and currency, which espeak reads mechanically ("$1,234.50"
becomes "dollar one, two hundred thirty four. fifty").

- **Memory is now flat regardless of book length.** `synthesize` accumulated
  every line in a list and `np.concatenate`d at the end, holding the finished
  audiobook in RAM twice -- ~2.8GB for an 8-hour book, ~5.5GB at the
  concatenate. `synthesize_to_wav` streams each line into the wav as it's
  produced instead. Book-length input is the stated target, so this was the
  wrong shape for the job it advertises.
- **Progress counters stay line counters.** The encode step called
  `progress(chapter_count, chapter_count, ...)`, so a 3-line/2-chapter run
  finished reporting `lines_done: 2, lines_total: 2` -- and `SKILL.md` tells
  Claude to narrate those fields, making the last number the user hears a
  wrong one. Relatedly, `lines_total` was blanked to `None` during the
  multi-second model load, so `percent` was null exactly when a first poll
  was most likely.
- **`lang` is validated at the cheap gate** like every other field: `en-us` or
  `en-gb`, matching kokoro-onnx's own tokenizer. It was the one field that
  could fail deep inside a run that had already cost minutes.
- **`concurrent_jobs` is reported by `start_synthesis`.** Nothing serializes
  runs and each loads its own Kokoro session, so two at once crawl; the count
  is surfaced rather than left to look like a mysteriously slow run.
- `expected_output` is built with `Path` rather than string concatenation, and
  `escape_ffmetadata` handles the leading whitespace its own comment described.
- Tests cover the synthesis pipeline for the first time -- chapter-timestamp
  arithmetic, gap accounting, streaming writes, per-line speed override, and
  the progress-counter regression -- by stubbing `kokoro_onnx.Kokoro` rather
  than the whole of `build_audiobook`. 30 tests to 40.
- A stale `script.json` reference in `story-mode-guide.md` is gone; the script
  has been a passed structure since 0.1.0.

## 0.1.0 -- 2026-08-18

Initial release. Ported from the `skills-stash` skill of the same name,
reshaped from a pair of `uv run` scripts into a server-backed plugin.

All the judgment stays where it was — `SKILL.md` and the four reference
guides (script schema, voice roster, story mode, Kokoro prosody) carry mode
selection, chapter boundaries, story rewriting, voice assignment, and prosody
verbatim. Plain and story modes, the six-voice roster, per-line speed, and
every non-goal ship unchanged.

What changed is how the mechanical half runs:

- **A local FastMCP server replaces the bundled scripts.** `ffmpeg`, the
  model cache, and the user's `output_dir` all exist only on the client's own
  machine, and a stdio MCP process is what guarantees the code runs there.
  Three tools: `check_setup`, `start_synthesis`, `synthesis_status`.
- **Synthesis is a polled background job.** It takes roughly half the
  finished audio's own duration, so an hour-long audiobook is ~30 minutes —
  far past what one tool call survives. `start_synthesis` returns a `job_id`
  immediately. Job state is in memory only: a restart loses the run and it is
  simply started again. No resume, no cancel, on purpose.
- **The script is passed as a structure, not a file path.** Nothing is
  written to disk on the way in, so no assumption is made that Claude's
  execution context and this process share a filesystem, and no stray
  `script.json` is left behind.
- **Model weights are fetched once, behind `check_setup`.** ~120MB into
  `~/.cache/audiobook-creator/models/` — outside `~/.claude/plugins/cache/`,
  which the client replaces on every update. They are not bundled in the
  repo: the marketplace is cloned whole to install any single plugin, so
  bundling would charge every client of every other plugin 120MB. Calling it
  up front means a missing prerequisite costs seconds rather than surfacing
  partway through a long run; `start_synthesis` refuses to trigger the
  download implicitly.
- **Text extraction is gone.** `extract_text.py` and `markitdown` are
  dropped: getting text out of a PDF or Word file is upstream of this plugin.
  The skill's input is markdown, and the description no longer advertises
  formats it doesn't handle itself.
- **The workspace is now capped at Python <3.14**, inherited from
  `kokoro-onnx`. Recorded explicitly in the root `pyproject.toml`; lift it
  when that dependency supports 3.14.
