"""In-memory job registry for synthesis runs.

Synthesis runs at roughly half the audio's own duration on CPU, so a
one-hour audiobook is ~30 minutes of work — far longer than any single MCP
tool call survives. `start_synthesis` therefore returns immediately with a
job id and the work continues on a background thread; `synthesis_status`
polls it.

**State is in memory and nothing else, on purpose.** If this process is
restarted — Desktop restart, plugin update, a crash inside onnxruntime — any
running job is lost and the user simply reruns it. There are no on-disk
progress snapshots, no resume-from-partial-audio, and no cancellation. Those
were all considered and deliberately declined: the failure is rare, the
recovery is "run it again", and every one of them adds state to keep correct.
The one thing that IS durable is the only thing that matters — the finished
.m4b, written to the user's own `output_dir`.

A thread (rather than a child process) is enough here because onnxruntime
releases the GIL during inference, so a long synthesis doesn't stall the
server's event loop.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any

from .script_schema import total_lines
from .synthesis import build_audiobook

# Job lifecycle: running -> done | failed.
RUNNING = "running"
DONE = "done"
FAILED = "failed"


class JobRegistry:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def start(self, script: dict, speed: float) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = {
                "job_id": job_id,
                "state": RUNNING,
                "title": script["title"],
                "started_at": time.time(),
                "lines_done": 0,
                "lines_total": total_lines(script),
                "detail": "queued",
                "result": None,
                "error": None,
            }

        thread = threading.Thread(
            target=self._run,
            args=(job_id, script, speed),
            name=f"audiobook-{job_id}",
            daemon=True,
        )
        thread.start()
        return job_id

    def _update(self, job_id: str, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None:
                job.update(fields)

    def _run(self, job_id: str, script: dict, speed: float) -> None:
        def progress(done: int, total: int, detail: str) -> None:
            # `total` is the real line count from the first callback onward,
            # including the one emitted before the model loads — no `or None`
            # fallback, which used to blank out percent during that window.
            self._update(job_id, lines_done=done, lines_total=total, detail=detail)

        try:
            result = build_audiobook(script, speed, progress)
        except Exception as e:  # noqa: BLE001 — the job records it; the thread must not die silently
            self._update(
                job_id,
                state=FAILED,
                error=f"{type(e).__name__}: {e}",
                finished_at=time.time(),
                detail="failed",
            )
            return
        self._update(
            job_id,
            state=DONE,
            result=result,
            finished_at=time.time(),
            detail="complete",
        )

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            snapshot = dict(job)

        started = snapshot.pop("started_at")
        finished = snapshot.pop("finished_at", None)
        snapshot["elapsed_seconds"] = round((finished or time.time()) - started, 1)

        done, total = snapshot["lines_done"], snapshot["lines_total"]
        snapshot["percent"] = round(done * 100 / total, 1) if total else None
        return snapshot

    def active_count(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j["state"] == RUNNING)
