"""FastMCP server bundled with the shadetree-ai-plugins 'audiobook-creator' plugin.

A local stdio process — no hosting, no accounts, and no text leaves the
machine: Kokoro-82M runs on this CPU and the finished .m4b is written
straight to a folder the user names.

The division of labour matters and is deliberate. Claude does everything
that needs comprehension — reading the source, choosing plain vs story mode,
deciding chapter boundaries, rewriting for a theme, assigning voices, and
setting prosody (see `skills/audiobook-creator/SKILL.md` and its
`references/`). This server does only what is pure mechanism once those
decisions exist: validate the script, synthesize, timestamp chapters, and
encode.

Three tools, in the order they're used:

- `check_setup` — ffmpeg on PATH and the ~120MB Kokoro model cached,
  fetching the model if it isn't. Call this before building a script, so a
  missing prerequisite costs seconds instead of being discovered partway
  through a long run.
- `start_synthesis` — hand over a finished script; returns a job id at once.
- `synthesis_status` — poll that job id until it reports done or failed.

There is deliberately no text-extraction tool: getting text out of a PDF or
a Word file is upstream of this plugin, not part of it. The skill's input is
markdown.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from shadetree_ai_plugins_common import get_logger

from .jobs import JobRegistry
from .script_schema import ScriptError, total_lines, validate_script
from .setup_check import models_present, setup_report
from .synthesis import slugify
from .voices import MAX_SPEED, MIN_SPEED

logger = get_logger("shadetree-ai-plugins-audiobook-creator")

mcp = FastMCP(
    name="shadetree-ai-plugins-audiobook-creator",
    instructions=(
        "Local, offline text-to-audiobook synthesis. Claude writes the script "
        "(chapters, per-line voice and speed); these tools turn it into a "
        "chaptered .m4b. Call check_setup first, then start_synthesis, then poll "
        "synthesis_status. Synthesis takes roughly half the finished audio's own "
        "duration, so a long document is minutes of real time — never wait on it "
        "in a single call."
    ),
)

_jobs = JobRegistry()

# Rough CPU-bound rate observed locally: synthesis takes about half the
# duration of the audio it produces. Only used to set expectations in the
# start_synthesis response, never to decide anything.
_SECONDS_OF_AUDIO_PER_LINE = 12.0
_SYNTHESIS_SPEED_FACTOR = 0.5


@mcp.tool
def check_setup(download: bool = True) -> dict[str, Any]:
    """Verify ffmpeg and the Kokoro model, downloading the model if missing.

    Call this before writing a script. The model is ~120MB across two files
    and is fetched once per machine into ~/.cache/audiobook-creator/models/,
    which lives outside the plugin directory so it survives plugin updates.
    Every run after the first is fully offline.

    With download=true (the default) this call blocks until the fetch
    finishes, which on a slow connection can take a while. If it dies partway,
    the partial file is cleaned up — just call it again.

    Args:
        download: Fetch the model if it isn't cached. Set false to report
            status only, without touching the network.
    """
    report = setup_report(download=download, log=logger.info)
    if report["ready"]:
        logger.info("setup ok (ffmpeg=%s)", report["ffmpeg"]["path"])
    else:
        logger.warning("setup incomplete: %s", "; ".join(report["blockers"]))
    return report


@mcp.tool
def start_synthesis(script: dict, speed: float = 1.0) -> dict[str, Any]:
    """Start building an .m4b from a finished script. Returns a job id at once.

    The script is passed as a structure, not a path — nothing is written to
    disk on the way in, and this works regardless of whether Claude's
    execution context shares a filesystem with this process.

    The whole script is validated before any audio is synthesized, so a
    malformed one fails in milliseconds rather than partway through a long
    run. See references/script-schema.md for the full contract; in short:

        {
          "title": "...",              # required; also names the output file
          "output_dir": "...",         # required, no default — ask the user
          "source": "...",             # optional, embedded as a comment tag
          "lang": "en-us",             # optional
          "chapters": [
            {"title": "...", "lines": [{"voice": "af_heart", "text": "...",
                                        "speed": 1.0}]}
          ]
        }

    Args:
        script: The chapter/line structure described above.
        speed: Global speed multiplier applied to any line without its own
            `speed`. Between 0.5 and 2.0.
    """
    if not MIN_SPEED <= speed <= MAX_SPEED:
        return {
            "error": f"speed must be between {MIN_SPEED} and {MAX_SPEED}, got {speed}"
        }

    try:
        clean = validate_script(script)
    except ScriptError as e:
        return {"error": str(e)}

    if not models_present():
        return {
            "error": (
                "The Kokoro model is not cached yet. Call check_setup(download=true) "
                "first — synthesis will not silently trigger a 120MB download."
            )
        }

    lines = total_lines(clean)
    active = _jobs.active_count()
    job_id = _jobs.start(clean, speed)
    logger.info("job %s started: %r, %d lines", job_id, clean["title"], lines)

    estimate = lines * _SECONDS_OF_AUDIO_PER_LINE * _SYNTHESIS_SPEED_FACTOR
    return {
        "job_id": job_id,
        "state": "running",
        "title": clean["title"],
        "chapter_count": len(clean["chapters"]),
        "line_count": lines,
        "expected_output": str(
            Path(clean["output_dir"]).expanduser() / f"{slugify(clean['title'])}.m4b"
        ),
        "rough_estimate_seconds": round(estimate),
        # Nothing serializes these runs, and each one loads its own Kokoro
        # session — so say plainly that two at once will crawl rather than
        # letting the user wonder why a short book is taking so long.
        "concurrent_jobs": active,
        "next": (
            "Poll synthesis_status with this job_id. Nothing is durable until the "
            "job reports done — if this server restarts mid-run the job is lost and "
            "must be started again."
            + (
                f" NOTE: {active} other synthesis job(s) already running. They share "
                "one CPU, so both will take substantially longer than the estimate — "
                "worth telling the user."
                if active
                else ""
            )
        ),
    }


@mcp.tool
def synthesis_status(job_id: str) -> dict[str, Any]:
    """Report progress, or the final result, for a synthesis job.

    States are `running`, `done`, or `failed`. On `done` the `result` holds
    the output path, total duration, and the chapter list with timestamps.

    An unknown job id means either a typo or that this server restarted since
    the job began — job state is held in memory only, so a restart loses it.

    Args:
        job_id: The id returned by start_synthesis.
    """
    status = _jobs.status(job_id)
    if status is None:
        return {
            "error": (
                f"no job {job_id!r}. Either the id is wrong, or this server restarted "
                "since it started — job state is in memory only. Start the synthesis "
                "again."
            )
        }
    return status
