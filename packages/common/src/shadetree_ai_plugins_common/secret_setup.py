"""Shared local credential setup. Secret values are never CLI arguments.

Usage: python -m shadetree_ai_plugins_common.secret_setup set <namespace> <key>
Other actions: status, replace, delete. Run in the user's own terminal.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import sys
import warnings

from .secrets import SecretStoreError, delete_secret, get_secret, set_secret


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["set", "status", "replace", "delete"])
    parser.add_argument("namespace", help="Plugin name, not a credential value")
    parser.add_argument("key", help="Credential identifier, not its value")
    args = parser.parse_args(argv)
    if not all(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", part)
               for part in (args.namespace, args.key)):
        print("Invalid plugin or credential identifier.", file=sys.stderr)
        return 1
    try:
        existing = get_secret(args.namespace, args.key)
        if args.action == "status":
            print(json.dumps({"configured": bool(existing)}))
            return 0
        if args.action == "set" and existing:
            print("Already configured. Use replace to change this credential.", file=sys.stderr)
            return 1
        if args.action == "replace" and not existing:
            print("Not configured. Use set first.", file=sys.stderr)
            return 1
        if not sys.stdin.isatty():
            print("Run this command yourself in an interactive terminal.", file=sys.stderr)
            return 1
        if args.action == "delete":
            if input("Remove this credential? Type yes: ") != "yes":
                print("Cancelled.", file=sys.stderr)
                return 1
            delete_secret(args.namespace, args.key)
            if get_secret(args.namespace, args.key):
                raise SecretStoreError("Credential removal could not be verified.")
            print("Credential removed.")
            return 0
        # getpass otherwise falls back to echoing on stdin: fail closed instead.
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            value = getpass.getpass("API key (hidden): ")
            confirm = getpass.getpass("Confirm API key (hidden): ")
        if not value or value != confirm or not value.isascii() or any(
            ord(char) <= 32 or ord(char) == 127 for char in value
        ):
            print("Keys must match and contain no whitespace or control characters.", file=sys.stderr)
            return 1
        set_secret(args.namespace, args.key, value)
        if get_secret(args.namespace, args.key) != value:
            raise SecretStoreError("Credential storage could not be verified.")
        print("Credential saved and verified in the OS keychain.")
        return 0
    except (KeyboardInterrupt, EOFError):
        print("Cancelled; setup did not complete.", file=sys.stderr)
        return 1
    except Exception:
        # Includes getpass fallback warnings and backend errors; never echo raw exceptions.
        print("Credential operation failed. Check your terminal and unlocked OS keychain.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
