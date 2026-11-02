"""Cross-platform keychain wrapper (macOS Keychain, Windows Credential
Locker, Linux Secret Service) via `keyring`, namespaced per solution.
"""

from __future__ import annotations

import keyring
import keyring.errors
from keyring.backend import KeyringBackend

_SERVICE_PREFIX = "shadetree-ai-plugins"


class SecretStoreError(RuntimeError):
    """The OS credential store is unavailable; no plaintext fallback is allowed."""


def _secure_backend() -> KeyringBackend:
    """Select only OS-backed stores, including when keyring uses a chainer."""
    backend = keyring.get_keyring()
    candidates = backend.backends if type(backend).__module__ == "keyring.backends.chainer" else [backend]
    approved = {
        ("keyring.backends.macOS", "Keyring"),
        ("keyring.backends.Windows", "WinVaultKeyring"),
        ("keyring.backends.SecretService", "Keyring"),
        ("keyring.backends.kwallet", "DBusKeyring"),
        ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
    }
    for candidate in candidates:
        if (type(candidate).__module__, type(candidate).__name__) in approved:
            return candidate
    raise SecretStoreError("No supported OS keychain is available. Unlock or configure your OS store.")


def _service(namespace: str) -> str:
    return f"{_SERVICE_PREFIX}:{namespace}"


def get_secret(namespace: str, key: str) -> str | None:
    try:
        return _secure_backend().get_password(_service(namespace), key)
    except Exception:
        # Backends may include credentials in exception text. Never relay it.
        raise SecretStoreError("Cannot read the OS keychain. Check that it is available and unlocked.") from None


def set_secret(namespace: str, key: str, value: str) -> None:
    try:
        _secure_backend().set_password(_service(namespace), key, value)
    except Exception:
        raise SecretStoreError("Cannot save to the OS keychain. No plaintext file was written.") from None


def delete_secret(namespace: str, key: str) -> None:
    try:
        _secure_backend().delete_password(_service(namespace), key)
    except keyring.errors.PasswordDeleteError:
        pass
    except Exception:
        raise SecretStoreError("Cannot remove the credential from the OS keychain.") from None
