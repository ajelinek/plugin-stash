"""Tests for the shadetree-ai-plugins-audiobook-creator FastMCP server.

Nothing here loads Kokoro's weights, downloads the model, or shells out to
ffmpeg. The tool-surface and job tests stub at the `build_audiobook`
boundary; the pipeline tests stub `kokoro_onnx.Kokoro` itself with a fake
returning fixed-length silence, which makes the chapter-timestamp arithmetic
and the streaming wav writes checkable without a 92MB model or a real encode.

What's worth testing is everything guarding the expensive path — that a
malformed script is rejected before any of that machinery starts, that
chapter marks are contiguous and account for both gap lengths, that progress
counters stay line counters end to end, that the job registry reports
progress and terminal states correctly, and that a missing model produces an
instruction rather than a silent 120MB download.
"""

from __future__ import annotations

import json
import time

import numpy as np
import pytest
import soundfile as sf
from fastmcp import Client
from shadetree_ai_plugins_audiobook_creator import server as srv
from shadetree_ai_plugins_audiobook_creator.jobs import DONE, FAILED, JobRegistry
from shadetree_ai_plugins_audiobook_creator.script_schema import (
    ScriptError,
    total_lines,
    validate_script,
)
from shadetree_ai_plugins_audiobook_creator.synthesis import (
    CHAPTER_GAP_MS,
    LINE_GAP_MS,
    SAMPLE_RATE,
    escape_ffmetadata,
    slugify,
    write_ffmetadata,
)


def make_script(**overrides):
    script = {
        "title": "A Test Document",
        "output_dir": "/tmp/audiobook-test",
        "chapters": [
            {
                "title": "Introduction",
                "lines": [
                    {"voice": "af_heart", "text": "First line."},
                    {"voice": "af_heart", "text": "Second line.", "speed": 0.9},
                ],
            },
            {
                "title": "Conclusion",
                "lines": [{"voice": "am_michael", "text": "Last line."}],
            },
        ],
    }
    script.update(overrides)
    return script


# --------------------------------------------------------------------------------
# Script validation — the cheap guard that protects an expensive pipeline
# --------------------------------------------------------------------------------


def test_valid_script_normalizes():
    clean = validate_script(make_script())
    assert clean["title"] == "A Test Document"
    assert clean["lang"] == "en-us"
    assert total_lines(clean) == 3
    assert clean["chapters"][0]["lines"][1]["speed"] == 0.9


def test_whitespace_is_stripped_from_text():
    script = make_script()
    script["chapters"][0]["lines"][0]["text"] = "  padded  "
    assert validate_script(script)["chapters"][0]["lines"][0]["text"] == "padded"


def test_source_is_preserved_and_optional():
    assert "source" not in validate_script(make_script())
    assert validate_script(make_script(source="notes.md"))["source"] == "notes.md"


@pytest.mark.parametrize("missing", ["title", "output_dir", "chapters"])
def test_missing_required_field(missing):
    script = make_script()
    del script[missing]
    with pytest.raises(ScriptError, match=missing):
        validate_script(script)


def test_output_dir_has_no_default():
    # Guards the rule that output_dir always comes from the user. An empty
    # string must fail rather than quietly becoming the working directory.
    with pytest.raises(ScriptError, match="output_dir"):
        validate_script(make_script(output_dir="   "))


def test_unknown_voice_names_the_roster():
    script = make_script()
    script["chapters"][0]["lines"][0]["voice"] = "af_nonexistent"
    with pytest.raises(ScriptError) as exc:
        validate_script(script)
    assert "af_nonexistent" in str(exc.value)
    assert "af_heart" in str(exc.value)  # the error lists what IS valid


def test_empty_chapters_rejected():
    with pytest.raises(ScriptError, match="non-empty list"):
        validate_script(make_script(chapters=[]))


def test_chapter_without_lines_rejected():
    script = make_script()
    script["chapters"][1]["lines"] = []
    with pytest.raises(ScriptError, match="no lines"):
        validate_script(script)


def test_whitespace_only_text_rejected():
    script = make_script()
    script["chapters"][0]["lines"][0]["text"] = "   "
    with pytest.raises(ScriptError, match="empty text"):
        validate_script(script)


@pytest.mark.parametrize("bad_speed", [0.4, 2.1, "fast", True])
def test_out_of_range_speed_rejected(bad_speed):
    script = make_script()
    script["chapters"][0]["lines"][0]["speed"] = bad_speed
    with pytest.raises(ScriptError, match="speed"):
        validate_script(script)


@pytest.mark.parametrize("bad_lang", ["fr-fr", "en_us", "", 5])
def test_unsupported_lang_rejected(bad_lang):
    with pytest.raises(ScriptError, match="lang"):
        validate_script(make_script(lang=bad_lang))


def test_supported_lang_is_normalized():
    assert validate_script(make_script(lang="EN-GB"))["lang"] == "en-gb"


def test_script_must_be_an_object():
    with pytest.raises(ScriptError, match="must be an object"):
        validate_script(["not", "a", "dict"])


# --------------------------------------------------------------------------------
# Encoding helpers
# --------------------------------------------------------------------------------


def test_slugify():
    assert slugify("Hello, World!") == "hello-world"
    assert slugify("Café Naïve") == "cafe-naive"
    assert slugify("!!!") == "audiobook"  # never returns an empty filename


def test_escape_ffmetadata_escapes_the_documented_specials():
    assert escape_ffmetadata("a=b;c#d\\e") == "a\\=b\\;c\\#d\\\\e"
    assert "\n" not in escape_ffmetadata("two\nlines")


def test_escape_ffmetadata_escapes_leading_whitespace():
    # A folded newline can expose a leading space that FFMETADATA1 would
    # otherwise strip.
    assert escape_ffmetadata("\ntitle") == "\\ title"


def test_write_ffmetadata_roundtrip(tmp_path):
    dest = tmp_path / "chapters.txt"
    write_ffmetadata(
        [{"title": "One; two", "start_ms": 0, "end_ms": 1000}],
        dest,
    )
    content = dest.read_text()
    assert content.startswith(";FFMETADATA1")
    assert "TIMEBASE=1/1000" in content
    assert "START=0" in content and "END=1000" in content
    assert "title=One\\; two" in content


# --------------------------------------------------------------------------------
# Job registry — in-memory, no durability by design
# --------------------------------------------------------------------------------


def wait_for(registry: JobRegistry, job_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = registry.status(job_id)
        if status["state"] in (DONE, FAILED):
            return status
        time.sleep(0.01)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_job_runs_to_completion(monkeypatch):
    registry = JobRegistry()
    seen = []

    def fake_build(script, speed, progress):
        progress(1, 3, "chapter 1/2")
        seen.append(speed)
        return {"file": "/tmp/out.m4b", "total_duration_seconds": 1.0,
                "chapter_count": 2, "chapters": []}

    monkeypatch.setattr(
        "shadetree_ai_plugins_audiobook_creator.jobs.build_audiobook", fake_build
    )

    job_id = registry.start(validate_script(make_script()), 1.25)
    status = wait_for(registry, job_id)

    assert status["state"] == DONE
    assert status["result"]["file"] == "/tmp/out.m4b"
    assert status["elapsed_seconds"] >= 0
    assert seen == [1.25]  # the global speed reaches the pipeline


def test_job_records_failure_instead_of_dying_silently(monkeypatch):
    registry = JobRegistry()

    def boom(script, speed, progress):
        raise RuntimeError("ffmpeg exploded")

    monkeypatch.setattr(
        "shadetree_ai_plugins_audiobook_creator.jobs.build_audiobook", boom
    )

    status = wait_for(registry, registry.start(validate_script(make_script()), 1.0))
    assert status["state"] == FAILED
    assert "ffmpeg exploded" in status["error"]
    assert status["result"] is None


def test_unknown_job_id_returns_none():
    assert JobRegistry().status("nope") is None


def test_progress_percent_is_reported(monkeypatch):
    registry = JobRegistry()
    release = {"go": False}

    def slow_build(script, speed, progress):
        progress(1, 4, "working")
        while not release["go"]:
            time.sleep(0.01)
        return {"file": "/tmp/x.m4b", "total_duration_seconds": 1.0,
                "chapter_count": 1, "chapters": []}

    monkeypatch.setattr(
        "shadetree_ai_plugins_audiobook_creator.jobs.build_audiobook", slow_build
    )

    job_id = registry.start(validate_script(make_script()), 1.0)
    deadline = time.time() + 5
    while registry.status(job_id)["lines_done"] == 0 and time.time() < deadline:
        time.sleep(0.01)

    mid = registry.status(job_id)
    assert mid["state"] == "running"
    assert mid["percent"] == 25.0
    assert registry.active_count() == 1

    release["go"] = True
    wait_for(registry, job_id)


# --------------------------------------------------------------------------------
# Tool surface, over an in-memory client
# --------------------------------------------------------------------------------


async def call(tool: str, **kwargs) -> dict:
    async with Client(srv.mcp) as client:
        result = await client.call_tool(tool, kwargs)
    return json.loads(result.content[0].text)


async def test_exactly_three_tools_are_exposed():
    async with Client(srv.mcp) as client:
        names = {t.name for t in await client.list_tools()}
    assert names == {"check_setup", "start_synthesis", "synthesis_status"}


async def test_check_setup_does_not_download_when_asked_not_to():
    report = await call("check_setup", download=False)
    assert set(report) == {"ready", "ffmpeg", "model", "blockers"}
    assert report["model"]["downloaded_just_now"] is False
    assert report["model"]["cache_dir"].endswith("audiobook-creator/models")


async def test_start_synthesis_rejects_a_bad_script_before_touching_the_model():
    out = await call("start_synthesis", script={"title": "x"})
    assert "output_dir" in out["error"]


async def test_start_synthesis_rejects_out_of_range_global_speed():
    out = await call("start_synthesis", script=make_script(), speed=3.0)
    assert "speed" in out["error"]


async def test_start_synthesis_refuses_to_trigger_a_silent_download(monkeypatch):
    monkeypatch.setattr(srv, "models_present", lambda: False)
    out = await call("start_synthesis", script=make_script())
    assert "check_setup" in out["error"]


async def test_start_synthesis_returns_a_job_id(monkeypatch):
    monkeypatch.setattr(srv, "models_present", lambda: True)
    monkeypatch.setattr(srv._jobs, "start", lambda script, speed: "abc123")

    out = await call("start_synthesis", script=make_script())
    assert out["job_id"] == "abc123"
    assert out["state"] == "running"
    assert out["chapter_count"] == 2
    assert out["line_count"] == 3
    assert out["expected_output"] == "/tmp/audiobook-test/a-test-document.m4b"


async def test_synthesis_status_explains_an_unknown_id():
    out = await call("synthesis_status", job_id="does-not-exist")
    assert "restarted" in out["error"]


# --------------------------------------------------------------------------------
# The synthesis pipeline — Kokoro stubbed, so no model load and no ffmpeg
# --------------------------------------------------------------------------------


class FakeKokoro:
    """Returns a fixed 1s of silence per line, so frame math is predictable."""

    def __init__(self, model_path, voices_path):
        self.calls = []

    def create(self, text, voice, speed, lang):
        self.calls.append({"text": text, "voice": voice, "speed": speed, "lang": lang})
        return np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE


def test_chapter_timestamps_account_for_the_gaps(tmp_path, monkeypatch):
    import kokoro_onnx
    from shadetree_ai_plugins_audiobook_creator import synthesis

    monkeypatch.setattr(kokoro_onnx, "Kokoro", FakeKokoro)
    wav_path = tmp_path / "out.wav"

    marks, frames = synthesis.synthesize_to_wav(
        validate_script(make_script()), 1.0, lambda *a: None, wav_path
    )

    # Chapter 1: two 1000ms lines with one 250ms gap between them, then the
    # 600ms chapter gap. Chapter 2: one line, then its own chapter gap.
    assert marks[0] == {
        "title": "Introduction",
        "start_ms": 0,
        "end_ms": 2 * 1000 + LINE_GAP_MS + CHAPTER_GAP_MS,
    }
    # Chapters are contiguous — chapter N ends exactly where N+1 begins.
    assert marks[1]["start_ms"] == marks[0]["end_ms"]
    assert marks[1]["end_ms"] == marks[0]["end_ms"] + 1000 + CHAPTER_GAP_MS

    # The frame count and the file on disk agree with those timestamps.
    expected_ms = marks[-1]["end_ms"]
    assert frames == expected_ms * SAMPLE_RATE // 1000
    assert sf.info(wav_path).frames == frames


def test_synthesis_streams_rather_than_buffering(tmp_path, monkeypatch):
    """Audio must reach the file as it's produced, not in one final write.

    The whole point of streaming is that a book-length run never holds the
    finished audiobook in memory, so the wav has to be growing mid-run.
    """
    import kokoro_onnx
    from shadetree_ai_plugins_audiobook_creator import synthesis

    monkeypatch.setattr(kokoro_onnx, "Kokoro", FakeKokoro)
    wav_path = tmp_path / "out.wav"
    sizes = []

    def watch(done, total, detail):
        sizes.append(wav_path.stat().st_size if wav_path.exists() else 0)

    synthesis.synthesize_to_wav(validate_script(make_script()), 1.0, watch, wav_path)

    assert sizes[-1] > sizes[1] > 0, f"wav did not grow during the run: {sizes}"


def test_per_line_speed_overrides_the_global_one(tmp_path, monkeypatch):
    import kokoro_onnx
    from shadetree_ai_plugins_audiobook_creator import synthesis

    captured = []
    monkeypatch.setattr(
        kokoro_onnx,
        "Kokoro",
        lambda *a: type(
            "K",
            (),
            {
                "create": lambda self, text, voice, speed, lang: (
                    captured.append((voice, speed, lang))
                    or (np.zeros(SAMPLE_RATE, dtype=np.float32), SAMPLE_RATE)
                )
            },
        )(),
    )

    synthesis.synthesize_to_wav(
        validate_script(make_script(lang="en-gb")), 1.5, lambda *a: None, tmp_path / "o.wav"
    )
    # Line 2 carries speed=0.9; the others inherit the global 1.5.
    assert captured == [
        ("af_heart", 1.5, "en-gb"),
        ("af_heart", 0.9, "en-gb"),
        ("am_michael", 1.5, "en-gb"),
    ]


def test_progress_counters_stay_line_counters(tmp_path, monkeypatch):
    """Regression: the encode step used to report chapter counts in the line
    fields, so a finished 3-line/2-chapter run ended at 'lines_done: 2 of 2'."""
    import kokoro_onnx
    from shadetree_ai_plugins_audiobook_creator import synthesis

    monkeypatch.setattr(kokoro_onnx, "Kokoro", FakeKokoro)
    monkeypatch.setattr(synthesis, "encode_m4b", lambda *a: None)
    seen = []

    synthesis.build_audiobook(
        validate_script(make_script(output_dir=str(tmp_path))),
        1.0,
        lambda done, total, detail: seen.append((done, total, detail)),
    )

    # Every callback, including the pre-model-load one and the final encode,
    # reports the same line total — never a chapter count, never a zero.
    assert {total for _, total, _ in seen} == {3}
    assert seen[0][0] == 0 and "loading" in seen[0][2]
    assert seen[-1] == (3, 3, "encoding a-test-document.m4b")
