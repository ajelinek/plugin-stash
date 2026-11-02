"""Cross-platform keychain wrapper (macOS Keychain, Windows Credential
Locker, Linux Secret Service) via `keyring`, namespaced per solution.
"""

from __future__ import annotations

import keyring
import keyring.errors

_SERVICE_PREFIX = "shadetree-ai-plugins"


def _service(namespace: str) -> str:
    return f"{_SERVICE_PREFIX}:{namespace}"


def get_secret(namespace: str, key: str) -> str | None:
    return keyring.get_password(_service(namespace), key)


def set_secret(namespace: str, key: str, value: str) -> None:
    keyring.set_password(_service(namespace), key, value)


def delete_secret(namespace: str, key: str) -> None:
    try:
        keyring.delete_password(_service(namespace), key)
    except keyring.errors.PasswordDeleteError:
        pass
