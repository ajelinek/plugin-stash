"""Preflight: `ffmpeg` on PATH, and the Kokoro weights on disk.

The weights (~120MB across two files) are deliberately NOT bundled in this
repo. The marketplace's `source` entries are `./plugins/<name>`, so Claude
Desktop clones the whole marketplace to install any one plugin — bundling a
TTS model here would make every client installing `grilling` or
`meeting-recap` pay 120MB they will never use, taking the repo from ~600KB
to roughly 200x that. So they're fetched once, on demand, into a cache
outside the plugin directory.

That cache location matters: `~/.claude/plugins/cache/` is owned by the
client and replaced wholesale on every plugin update, so weights written
"inside the plugin" would be silently re-downloaded on each version bump.
`~/.cache/audiobook-creator/models/` survives updates and uninstalls, and is
somewhere a user would think to look — or delete to reclaim the space.

A failed or interrupted download is not recoverable in place: the partial
file is removed and the whole fetch is retried from scratch on the next
call. That's a deliberate simplification — this is a once-per-machine cost.
"""

from __future__ import annotations

import shutil
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

MODEL_DIR = Path.home() / ".cache" / "audiobook-creator" / "models"
MODEL_FILE = MODEL_DIR / "kokoro-v1.0.int8.onnx"
VOICES_FILE = MODEL_DIR / "voices-v1.0.bin"
MODEL_BASE_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
)

FFMPEG_HELP = (
    "ffmpeg is not on PATH. Install it (`brew install ffmpeg` on macOS, "
    "`apt install ffmpeg` on Debian/Ubuntu, `winget install ffmpeg` on Windows) "
    "and try again — it is a system dependency, not something uv or pip can "
    "install into this plugin's environment."
)


class SetupError(RuntimeError):
    """Raised when the environment can't support synthesis."""


def ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def models_present() -> bool:
    return MODEL_FILE.exists() and VOICES_FILE.exists()


def _size_mb(path: Path) -> float | None:
    if not path.exists():
        return None
    return round(path.stat().st_size / 1_000_000, 1)


def _download(url: str, dest: Path, log: Callable[[str], None]) -> None:
    partial = dest.with_name(dest.name + ".partial")
    log(f"downloading {dest.name} from {url}")
    last_pct = -1

    def report(block_num: int, block_size: int, total_size: int) -> None:
        nonlocal last_pct
        if total_size <= 0:
            return
        done = min(block_num * block_size, total_size)
        pct = done * 100 // total_size
        # Coarse steps only. This goes to a log, not a terminal, so a
        # \r-driven bar would emit thousands of lines instead of redrawing.
        if pct >= last_pct + 10 or done >= total_size:
            log(f"  {dest.name}: {pct}% ({done // 1_000_000}MB/{total_size // 1_000_000}MB)")
            last_pct = pct

    try:
        urllib.request.urlretrieve(url, partial, reporthook=report)  # noqa: S310
    except (urllib.error.URLError, OSError) as e:
        partial.unlink(missing_ok=True)
        raise SetupError(
            f"failed to download {url}: {e}. Check the network connection and call "
            "check_setup again — the partial file has been cleaned up, so a retry "
            "starts fresh."
        ) from e
    partial.replace(dest)


def ensure_models(log: Callable[[str], None]) -> None:
    """Download whichever of the two model files are missing."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if not MODEL_FILE.exists():
        _download(f"{MODEL_BASE_URL}/{MODEL_FILE.name}", MODEL_FILE, log)
    if not VOICES_FILE.exists():
        _download(f"{MODEL_BASE_URL}/{VOICES_FILE.name}", VOICES_FILE, log)


def setup_report(*, download: bool, log: Callable[[str], None]) -> dict:
    """Check (and optionally satisfy) both prerequisites, and describe the result."""
    ffmpeg = ffmpeg_path()

    downloaded = False
    model_error: str | None = None
    if download and not models_present():
        try:
            ensure_models(log)
            downloaded = True
        except SetupError as e:
            model_error = str(e)

    present = models_present()
    blockers = []
    if ffmpeg is None:
        blockers.append(FFMPEG_HELP)
    if not present:
        blockers.append(
            model_error
            or (
                "The Kokoro model files are not cached yet. Call check_setup with "
                "download=true to fetch them (~120MB, one time)."
            )
        )

    return {
        "ready": not blockers,
        "ffmpeg": {"found": ffmpeg is not None, "path": ffmpeg},
        "model": {
            "present": present,
            "downloaded_just_now": downloaded,
            "cache_dir": str(MODEL_DIR),
            "files": {
                MODEL_FILE.name: _size_mb(MODEL_FILE),
                VOICES_FILE.name: _size_mb(VOICES_FILE),
            },
        },
        "blockers": blockers,
    }
