"""Validation for the `script` structure the model hands to `start_synthesis`.

The script arrives as a dict argument rather than a path to a file on disk.
That removes any assumption that the model's execution context and this
process share a filesystem view, and leaves no stray `script.json` behind
after a run.

Everything here runs *before* the model is loaded or a single sample is
synthesized, so a malformed script costs milliseconds instead of the full
runtime. See `skills/audiobook-creator/references/script-schema.md` for the
authored-facing version of this contract.
"""

from __future__ import annotations

from typing import Any

from .voices import DEFAULT_LANG, MAX_SPEED, MIN_SPEED, SUPPORTED_LANGS, VOICE_ROSTER


class ScriptError(ValueError):
    """Raised when the submitted script can't be synthesized as given."""


def _validate_lang(lang: Any) -> str:
    """Check `lang` at the cheap gate, like every other field.

    Every voice on the roster is en-US or en-GB, and kokoro-onnx's own
    tokenizer documents those as its two options — so an unrecognized value is
    a script error, not something to forward and discover deep inside a run
    that has already cost minutes.
    """
    if lang is None:
        return DEFAULT_LANG
    if not isinstance(lang, str) or lang.strip().lower() not in SUPPORTED_LANGS:
        raise ScriptError(
            f"script 'lang' is {lang!r}. Valid values: "
            f"{', '.join(sorted(SUPPORTED_LANGS))}. Every voice on the roster is "
            "English; lang picks the pronunciation variant, not the voice."
        )
    return lang.strip().lower()


def validate_script(script: Any) -> dict:
    """Return a normalized copy of `script`, or raise ScriptError.

    Normalization is limited to stripping surrounding whitespace from each
    line's text — nothing here rewrites, reflows, or reinterprets content.
    """
    if not isinstance(script, dict):
        raise ScriptError(f"script must be an object, got {type(script).__name__}")

    for field in ("title", "output_dir", "chapters"):
        if field not in script:
            raise ScriptError(f"script is missing required field: {field!r}")

    if not isinstance(script["title"], str) or not script["title"].strip():
        raise ScriptError("script 'title' must be a non-empty string")
    if not isinstance(script["output_dir"], str) or not script["output_dir"].strip():
        raise ScriptError(
            "script 'output_dir' must be a non-empty string. It has no default on "
            "purpose — ask the user where the .m4b should land before synthesizing, "
            "rather than building it somewhere and moving it afterwards."
        )

    chapters = script["chapters"]
    if not isinstance(chapters, list) or not chapters:
        raise ScriptError("script 'chapters' must be a non-empty list")

    clean_chapters = []
    for i, chapter in enumerate(chapters):
        if not isinstance(chapter, dict):
            raise ScriptError(f"chapter {i} must be an object")
        title = chapter.get("title")
        if not isinstance(title, str) or not title.strip():
            raise ScriptError(f"chapter {i} is missing a title")

        lines = chapter.get("lines")
        if not isinstance(lines, list) or not lines:
            raise ScriptError(f"chapter {i} ({title!r}) has no lines")

        clean_lines = []
        for j, line in enumerate(lines):
            if not isinstance(line, dict):
                raise ScriptError(f"chapter {i} line {j} must be an object")

            voice = line.get("voice")
            if voice not in VOICE_ROSTER:
                raise ScriptError(
                    f"chapter {i} line {j} uses unknown voice {voice!r}. "
                    f"Valid voices: {', '.join(VOICE_ROSTER)}"
                )

            text = line.get("text")
            if not isinstance(text, str) or not text.strip():
                raise ScriptError(f"chapter {i} line {j} has empty text")

            clean_line = {"voice": voice, "text": text.strip()}

            speed = line.get("speed")
            if speed is not None:
                if isinstance(speed, bool) or not isinstance(speed, (int, float)):
                    raise ScriptError(
                        f"chapter {i} line {j} has invalid speed {speed!r}. "
                        f"Must be a number between {MIN_SPEED} and {MAX_SPEED}."
                    )
                if not MIN_SPEED <= speed <= MAX_SPEED:
                    raise ScriptError(
                        f"chapter {i} line {j} has invalid speed {speed!r}. "
                        f"Must be between {MIN_SPEED} and {MAX_SPEED}."
                    )
                clean_line["speed"] = float(speed)

            clean_lines.append(clean_line)

        clean_chapters.append({"title": title.strip(), "lines": clean_lines})

    clean: dict[str, Any] = {
        "title": script["title"].strip(),
        "output_dir": script["output_dir"].strip(),
        "chapters": clean_chapters,
    }
    if script.get("source"):
        clean["source"] = str(script["source"])
    clean["lang"] = _validate_lang(script.get("lang"))
    return clean


def total_lines(script: dict) -> int:
    return sum(len(c["lines"]) for c in script["chapters"])
