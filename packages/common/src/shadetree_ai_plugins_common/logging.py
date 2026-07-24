"""Stdio-safe logging for every shadetree-ai-plugins server.

MCP stdio transport uses stdout exclusively for the JSON-RPC stream.
This logger only ever writes to stderr and/or a file under the
solution's state directory — never stdout.
"""

from __future__ import annotations

import logging
import sys

from .paths import state_dir


def get_logger(name: str, *, to_file: bool = True, level: int = logging.INFO) -> logging.Logger:
    """Return a configured logger for `name` (idempotent across calls)."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(level)
    logger.propagate = False
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(fmt)
    logger.addHandler(stderr_handler)

    if to_file:
        log_path = state_dir(name) / f"{name}.log"
        file_handler = logging.FileHandler(log_path)
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger
