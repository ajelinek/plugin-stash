"""The deterministic half: script -> audio -> chapter timestamps -> .m4b.

No creative decisions happen here. Mode selection, chapter boundaries, story
rewriting, voice assignment and prosody are all the calling model's job (see
`skills/audiobook-creator/SKILL.md`); by the time anything in this module
runs, every one of those choices is already fixed in the validated script.

Carried over essentially unchanged from the original bundled
`build_audiobook.py`, with two additions: a `progress` callback so a
long-running job can report where it is without this module knowing anything
about jobs, and streaming writes so memory stays flat on a book-length run.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable
from pathlib import Path

import numpy as np
import soundfile as sf

from .setup_check import MODEL_FILE, VOICES_FILE

SAMPLE_RATE = 24000
LINE_GAP_MS = 250  # silence between lines within a chapter (voice/beat changes)
CHAPTER_GAP_MS = 600  # silence appended at the end of each chapter

ProgressFn = Callable[[int, int, str], None]


class SynthesisError(RuntimeError):
    """Raised when synthesis or encoding fails."""


def slugify(title: str) -> str:
    normalized = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "audiobook"


def escape_ffmetadata(value: str) -> str:
    # Per https://ffmpeg.org/ffmpeg-formats.html#Metadata-1 — '=', ';', '#',
    # '\' and a leading whitespace need a backslash escape inside FFMETADATA1.
    escaped = re.sub(r"([=;#\\])", r"\\\1", value).replace("\n", " ")
    # Chapter titles are stripped during validation, so this is belt-and-braces
    # — but folding a newline above can itself expose a leading space.
    if escaped[:1].isspace():
        escaped = "\\" + escaped
    return escaped


def write_ffmetadata(chapters: list[dict], dest: Path) -> None:
    lines = [";FFMETADATA1"]
    for c in chapters:
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={c['start_ms']}")
        lines.append(f"END={c['end_ms']}")
        lines.append(f"title={escape_ffmetadata(c['title'])}")
    dest.write_text("\n".join(lines) + "\n")


def synthesize_to_wav(
    script: dict,
    speed: float,
    progress: ProgressFn,
    wav_path: Path,
) -> tuple[list[dict], int]:
    """Render every line straight into `wav_path`.

    Returns the chapter marks and the total frame count.

    Each line is written to disk as soon as it is generated rather than
    accumulated in a list and concatenated at the end. The old shape held the
    whole book in memory twice — an 8-hour audiobook is ~2.8GB of float32
    samples, and `np.concatenate` briefly doubled that. Streaming keeps
    memory flat at one line regardless of book length.
    """
    from kokoro_onnx import Kokoro

    chapters = script["chapters"]
    total = sum(len(c["lines"]) for c in chapters)

    # Report the real line total from the very first callback, so a client
    # polling during the (multi-second) model load still gets a percentage
    # instead of a null.
    progress(0, total, "loading TTS model")
    kokoro = Kokoro(str(MODEL_FILE), str(VOICES_FILE))
    lang = script.get("lang", "en-us")

    chapter_marks: list[dict] = []
    cursor_ms = 0
    total_frames = 0
    line_gap = np.zeros(int(SAMPLE_RATE * LINE_GAP_MS / 1000), dtype=np.float32)
    chapter_gap = np.zeros(int(SAMPLE_RATE * CHAPTER_GAP_MS / 1000), dtype=np.float32)

    done = 0
    with sf.SoundFile(
        wav_path, mode="w", samplerate=SAMPLE_RATE, channels=1, subtype="PCM_16"
    ) as wav:

        def emit(samples: np.ndarray) -> None:
            nonlocal total_frames
            wav.write(samples)
            total_frames += len(samples)

        for ci, chapter in enumerate(chapters):
            start_ms = cursor_ms
            for li, line in enumerate(chapter["lines"]):
                done += 1
                line_speed = line.get("speed", speed)
                progress(
                    done,
                    total,
                    f"chapter {ci + 1}/{len(chapters)} ({chapter['title']!r}) "
                    f"voice={line['voice']} speed={line_speed}",
                )
                samples, sr = kokoro.create(
                    line["text"], voice=line["voice"], speed=line_speed, lang=lang
                )
                if sr != SAMPLE_RATE:
                    raise SynthesisError(
                        f"Kokoro returned {sr}Hz audio, expected {SAMPLE_RATE}Hz — "
                        "chapter timestamps would be wrong."
                    )
                emit(samples)
                cursor_ms += int(len(samples) / SAMPLE_RATE * 1000)
                if li < len(chapter["lines"]) - 1:
                    emit(line_gap)
                    cursor_ms += LINE_GAP_MS
            emit(chapter_gap)
            cursor_ms += CHAPTER_GAP_MS
            chapter_marks.append(
                {"title": chapter["title"], "start_ms": start_ms, "end_ms": cursor_ms}
            )

    return chapter_marks, total_frames


def encode_m4b(wav_path: Path, chapters_path: Path, out_path: Path, script: dict) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-i", str(wav_path),
        "-i", str(chapters_path),
        "-map_metadata", "1",
        "-c:a", "aac", "-b:a", "128k",
        "-metadata", f"title={script['title']}",
        # The tag that makes Apple Books/Podcasts/Overcast treat this as an
        # audiobook — resume position, speed controls, real chapter
        # navigation — instead of one flat unnavigable track.
        "-metadata", "genre=Audiobook",
    ]
    if script.get("source"):
        cmd += ["-metadata", f"comment=Source: {script['source']}"]
    cmd += ["-f", "mp4", str(out_path), "-loglevel", "error"]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SynthesisError(f"ffmpeg encode failed:\n{result.stderr}")


def build_audiobook(script: dict, speed: float, progress: ProgressFn) -> dict:
    """Full pipeline for an already-validated script. Returns a result summary."""
    output_dir = Path(script["output_dir"]).expanduser()
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise SynthesisError(f"can't create output directory {output_dir}: {e}") from e

    total = sum(len(c["lines"]) for c in script["chapters"])

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        wav_path = tmp_path / "combined.wav"
        chapters_path = tmp_path / "chapters.txt"

        chapter_marks, total_frames = synthesize_to_wav(script, speed, progress, wav_path)
        write_ffmetadata(chapter_marks, chapters_path)

        out_path = output_dir / f"{slugify(script['title'])}.m4b"
        # Still `total`/`total` — the line counters must stay line counters, or
        # a client narrating lines_done/lines_total reports the chapter count
        # as its final word on a run.
        progress(total, total, f"encoding {out_path.name}")
        encode_m4b(wav_path, chapters_path, out_path, script)

    return {
        "file": str(out_path),
        "total_duration_seconds": round(total_frames / SAMPLE_RATE, 1),
        "chapter_count": len(chapter_marks),
        "chapters": chapter_marks,
    }
