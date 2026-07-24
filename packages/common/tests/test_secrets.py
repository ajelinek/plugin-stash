import keyring
import pytest
from shadetree_ai_plugins_common import secrets


@pytest.fixture(autouse=True)
def in_memory_keyring():
    ring: dict[tuple[str, str], str] = {}

    class MemoryKeyring(keyring.backend.KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def get_password(self, service, username):
            return ring.get((service, username))

        def set_password(self, service, username, password):
            ring[(service, username)] = password

        def delete_password(self, service, username):
            ring.pop((service, username), None)

    keyring.set_keyring(MemoryKeyring())
    yield


def test_set_get_delete_roundtrip():
    secrets.set_secret("demo", "api-key", "s3cr3t")
    assert secrets.get_secret("demo", "api-key") == "s3cr3t"
    secrets.delete_secret("demo", "api-key")
    assert secrets.get_secret("demo", "api-key") is None
