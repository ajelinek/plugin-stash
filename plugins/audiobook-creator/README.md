# audiobook-creator

Turns a document's text into a real, chaptered `.m4b` audiobook using local,
offline neural TTS. Nothing is uploaded, nothing is metered, and the finished
file lands in a folder you name — typically one your phone already syncs.

## What you get

One `.m4b` with chapter markers embedded via `FFMETADATA1` and
`genre=Audiobook` set — the tag that makes Apple Books, Podcasts, Overcast
and friends treat it as an audiobook: real chapter navigation, remembered
resume position, and speed controls. Not a folder of MP3s, not a playlist.

Two modes:

- **Plain narration** — verbatim reading, one voice, maximum fidelity.
- **Story mode** — rewrites dry or dense source material into a themed
  narrative (noir detective, true-crime podcast, kids' bedtime story,
  sports-commentary energy — whatever theme you name), with narrator and
  character lines automatically split across voices.

## Requirements

- **`ffmpeg` on PATH.** A system dependency; `uv` and `pip` can't supply it.
  `brew install ffmpeg` on macOS, `apt install ffmpeg` on Debian/Ubuntu,
  `winget install ffmpeg` on Windows.
- **Network access, once.** The Kokoro-82M model (~120MB across two files)
  downloads on first use into `~/.cache/audiobook-creator/models/`. Every run
  after that is fully offline. The weights are deliberately not bundled: the
  marketplace is cloned whole to install any one plugin, so shipping a TTS
  model here would charge every client of every other plugin 120MB they'd
  never use.
- **Python 3.12 or 3.13.** `kokoro-onnx` has no 3.14 support yet.

## How to use it

Just ask — "turn this into an audiobook", "make this something I can listen
to on my commute", "read me this report as a noir detective story". The skill
handles the rest.

**Its input is markdown.** Getting text out of a PDF, a Word file, or a web
page happens before this plugin is involved; convert to markdown first.

You'll be asked where to put the file. That's required and has no default —
the point is that it lands somewhere useful, not in whatever directory
happened to be current.

## How it's built

The split is deliberate. Claude does everything requiring comprehension:
reading the source, choosing the mode, deciding chapter boundaries, rewriting
for a theme, assigning voices, and setting prosody. The bundled MCP server
does only what's mechanical once those decisions exist: validate, synthesize,
timestamp chapters, encode.

Three tools:

| Tool | Purpose |
|---|---|
| `check_setup` | Verify `ffmpeg` and the model cache; download the model if needed. Called first, so a missing prerequisite costs seconds instead of surfacing mid-run. |
| `start_synthesis` | Validate a finished script and start the job. Returns a `job_id` immediately. |
| `synthesis_status` | Poll a job for progress, then its result. |

Synthesis takes roughly half the finished audio's own duration on CPU, so an
hour-long audiobook is around thirty minutes of work — well past what any
single tool call survives. Hence the job-and-poll shape.

**Jobs are held in memory only.** If the server process restarts mid-run —
Desktop restart, plugin update, a crash — the job is lost and you rerun it.
There is no resume and no cancel, deliberately: the failure is rare, the
recovery is one command, and the alternative is durable state that has to
stay correct for no real benefit. The finished `.m4b` is the only thing that
needs to survive, and it's written straight to your output directory.

## Non-goals

- No text extraction — markdown in.
- No voice cloning or custom voices; the roster is a fixed six.
- No manual voice-to-role override in story mode.
- No background music, sound effects, or mixing.
- Not a cloud-upload integration — it writes to a local folder and stops.
- Not streaming or real-time narration; it produces a finished file.
- Not a batch tool — one document, one `.m4b`, one deliberate run.

## Development

```bash
uv run python -m pytest plugins/audiobook-creator/tests
uv run ruff check plugins/audiobook-creator
claude plugin validate ./plugins/audiobook-creator
```

(`python -m pytest` rather than bare `uv run pytest`: if a system-wide
`pytest` is earlier on `PATH`, `uv run` will pick that one up and collection
fails against the wrong interpreter.)

Tests never load the model weights, download anything, or shell out to
`ffmpeg`. The tool-surface and job tests stub at the `build_audiobook`
boundary; the pipeline tests stub `kokoro_onnx.Kokoro` with a fake returning
fixed-length silence, which makes chapter-timestamp arithmetic and the
streaming wav writes checkable without a 92MB model or a real encode. Between
them they cover script validation, gap accounting, progress counters, the job
registry's terminal states, `FFMETADATA1` escaping, and the tool surface.

Regenerating `uv.lock` after a dependency change follows the standalone
procedure in `docs/plugin-authoring.md` — `uv lock` run inside the plugin
directory operates on the workspace root's lock instead.

## Credits

Speech synthesis by [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M)
via [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx).
