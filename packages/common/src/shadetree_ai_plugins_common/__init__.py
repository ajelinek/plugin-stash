"""Shared helpers vendored into shadetree-ai-plugins plugins.

This package is never installed as its own distribution (see
pyproject.toml: `package = false`). Consuming plugins vendor it by
symlinking this directory into their own `src/` tree and listing
`shadetree_ai_plugins_common` in their `[tool.uv.build-backend] module-name` list —
see plugins/imessages and plugins/iphone-history for reference.
"""

from .fsutil import check_path
from .logging import get_logger
from .notify import notify
from .paths import state_dir
from .secrets import delete_secret, get_secret, set_secret

__all__ = [
    "check_path",
    "delete_secret",
    "get_logger",
    "get_secret",
    "notify",
    "set_secret",
    "state_dir",
]
