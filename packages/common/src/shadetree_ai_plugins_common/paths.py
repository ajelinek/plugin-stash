"""Per-solution state directories, shared across every shadetree-ai-plugins plugin."""

from __future__ import annotations

from pathlib import Path


def state_dir(name: str) -> Path:
    """Return (creating if needed) `~/.shadetree-ai-plugins/<name>/`."""
    d = Path.home() / ".shadetree-ai-plugins" / name
    d.mkdir(parents=True, exist_ok=True)
    return d
