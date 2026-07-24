"""Safe, read-only local filesystem access checks.

Deliberately metadata-only: these helpers report whether a path exists
and basic stat info (size, modified time, readable), and never read file
contents. This is the first thing every shadetree-ai-plugins plugin that needs
local file access should prove works before building real data-reading
logic on top.
"""

from __future__ import annotations

import os
from pathlib import Path


def check_path(path: str) -> dict:
    """Return existence + basic metadata for `path`. Never reads content."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"path": str(p), "exists": False}
    stat = p.stat()
    return {
        "path": str(p),
        "exists": True,
        "is_file": p.is_file(),
        "is_dir": p.is_dir(),
        "size_bytes": stat.st_size,
        "modified": stat.st_mtime,
        "readable": os.access(p, os.R_OK),
    }
