# The script structure

This is the one interface between the judgment half of the pipeline (you,
deciding chapters, rewriting for story mode, assigning voices) and the
mechanical half (the plugin's MCP tools, which just synthesize, concatenate,
chapter, and encode whatever you hand them). Build one of these per document
and pass it to `start_synthesis` as the `script` argument.

It is passed as a **structure, not a file**: don't write a `script.json` to
disk and hand over a path. Passing it directly removes any assumption that
your filesystem and the plugin's are the same one, and leaves nothing behind
to clean up.

## Shape

```json
{
  "title": "The Document Title",
  "output_dir": "/absolute/path/to/where/the/user/wants/the/file",
  "source": "original-file-name-or-'pasted text'",
  "lang": "en-us",
  "chapters": [
    {
      "title": "Introduction",
      "lines": [
        {"voice": "af_heart", "text": "Plain prose, no markdown syntax -- this is read verbatim by the TTS engine."}
      ]
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `title` | yes | Becomes the `.m4b` filename (slugified) and the embedded `title` tag. |
| `output_dir` | yes | Absolute path. Created if it doesn't exist. Always comes from the user -- never invent or default this. |
| `source` | no | Original filename or `"pasted text"`. Written into the file's `comment` metadata tag for provenance. |
| `lang` | no | `en-us` (default) or `en-gb` — anything else is a hard error. Picks the pronunciation variant, not the voice; every roster voice is already English. |
| `chapters` | yes | Non-empty list. Each chapter needs a non-empty `title` and a non-empty `lines` list. |
| `lines[].voice` | yes | Must be one of the 6 roster IDs in [voice-roster.md](voice-roster.md). Anything else is a hard error -- the script validates this before synthesizing anything. |
| `lines[].text` | yes | Plain prose. Strip markdown syntax (`**bold**`, `[text](url)`, etc.) before writing it here -- whatever's in `text` gets read aloud exactly as-is. For prosody techniques (emphasis, pausing, stress), see [kokoro-prosody-guide.md](kokoro-prosody-guide.md). |
| `lines[].speed` | no | Optional per-line speed override, 0.5–2.0. If omitted, uses the global `speed` argument to `start_synthesis` (default 1.0). Use for dramatic pacing changes (slower for serious moments, faster for energetic passages). |

## Why "lines" and not just one big block of text per chapter

Two reasons:

1. **Story mode needs per-line voice assignment.** A chapter alternating between a narrator and a character is naturally a list of `{voice, text}` pairs -- one per beat/turn.
2. **Line granularity gives you natural pause points.** The synthesizer inserts a short silence between lines within a chapter and a longer one between chapters, so switching voices doesn't sound like an abrupt jump-cut. In plain mode, splitting a chapter into one line per paragraph gets you this pacing for free even with a single voice throughout.

You don't need to chunk for the TTS engine's sake -- Kokoro handles arbitrarily long text per line internally (it splits at the phoneme level, preferring punctuation boundaries, and stitches the result back together). Chunk at the "line" level for narrative/pacing reasons, not to work around any length limit.

## Validation performed before synthesizing anything

`start_synthesis` validates the whole structure and returns an `error`
immediately -- before the model loads or a single sample is generated -- if:

- required top-level fields are missing
- any chapter has no title or no lines
- any line uses a voice outside the 6-voice roster
- any line's text is empty after stripping whitespace
- any line's `speed` is outside 0.5-2.0
- `lang` is anything other than `en-us` or `en-gb`

`ffmpeg` and the model cache are checked separately by `check_setup`, which is
why you call that first -- a missing prerequisite should cost seconds, not be
discovered partway through a long run.

A validation failure returns `{"error": "..."}` naming the exact chapter and
line. There's no silent partial output.

## What comes back

`start_synthesis` returns a `job_id` immediately. Poll `synthesis_status` with
it; on completion the `result` holds:

```json
{
  "file": "/absolute/path/to/output/my-title.m4b",
  "total_duration_seconds": 613.4,
  "chapter_count": 3,
  "chapters": [{"title": "Introduction", "start_ms": 0, "end_ms": 45000}, ...]
}
```

Report this back to the user directly -- file path, runtime, chapter count/titles -- not just "done."
