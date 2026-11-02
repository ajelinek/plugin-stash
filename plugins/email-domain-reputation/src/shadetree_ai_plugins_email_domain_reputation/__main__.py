"""The stdio server and a thin entry point to shared, local key setup."""

import sys

from shadetree_ai_plugins_common.secret_setup import main as credentials_main

from .server import CREDENTIAL_KEY, NAMESPACE, mcp


def main() -> None:
    if sys.argv[1:2] == ["credentials"]:
        if len(sys.argv) != 3 or sys.argv[2] not in {"set", "status", "replace", "delete"}:
            raise SystemExit("Usage: python -m shadetree_ai_plugins_email_domain_reputation "
                             "credentials {set,status,replace,delete}")
        raise SystemExit(credentials_main([sys.argv[2], NAMESPACE, CREDENTIAL_KEY]))
    if len(sys.argv) != 1:
        raise SystemExit("Use no arguments for the MCP server, or credentials <action> for setup.")
    mcp.run()


if __name__ == "__main__":
    main()
