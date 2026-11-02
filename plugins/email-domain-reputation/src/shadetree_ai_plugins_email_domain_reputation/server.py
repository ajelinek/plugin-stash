"""Local MCP workflow boundary. Judgment stays in the bundled skill."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastmcp import FastMCP
from shadetree_ai_plugins_common.secrets import SecretStoreError, get_secret

from .dns_auth import audit_dns, domain_from_arg, normalize_email, validate_selectors
from .reputation import check_reputation

NAMESPACE = "email-domain-reputation"
CREDENTIAL_KEY = "abstractapi-api-key"
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False,
             "idempotentHint": False, "openWorldHint": True}

mcp = FastMCP(
    "shadetree-ai-plugins-email-domain-reputation",
    instructions=(
        "Investigate one email address with investigate_email; it performs both AbstractAPI "
        "and DNS checks. Stop on setup/API failures; do not substitute DNS-only findings. "
        "audit_domain is for explicit keyless domain audits. Apply the bundled skill's "
        "evidence-led rubric; results cannot authenticate a particular message. "
        "Never ask for or accept API keys in chat. check_setup returns a local terminal command."
    ),
)


def _setup_command() -> str:
    root = Path(__file__).resolve().parents[2]
    # JSON/text command is shown to the user; it is never executed by this server.
    return (f'uv run --project "{root}" --locked python '
            "-m shadetree_ai_plugins_email_domain_reputation credentials set")


@mcp.tool(annotations={**READ_ONLY, "idempotentHint": True, "openWorldHint": False})
def check_setup() -> dict:
    """Report keychain readiness without a network request or exposing the key.

    A stored key is not proof of API access or remaining credits. Show the local
    setup_command to the user; never execute an interactive key prompt in a tool.
    """
    try:
        configured = bool(get_secret(NAMESPACE, CREDENTIAL_KEY))
        error = None if configured else "API key not configured."
    except SecretStoreError as exc:
        configured, error = False, str(exc)
    return {"ready": configured, "configured": configured, "error": error,
            "setup_command": _setup_command(), "api_access_verified": False}


@mcp.tool(annotations=READ_ONLY)
def investigate_email(email: str, selectors: list[str] | None = None) -> dict:
    """Investigate one address: AbstractAPI reputation then SPF/DMARC/MX/DKIM.

    Sends the address to AbstractAPI and the domain to the machine's DNS resolver.
    Uses one metered API request, no automatic retry, history or bulk mode. A key
    and working API access are required; failure returns no DNS-only assessment.
    Optional selectors add known DKIM names to the nine common defaults.
    """
    try:
        email = normalize_email(email)
        validate_selectors(selectors)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_input", "detail": str(exc)}
    try:
        key = get_secret(NAMESPACE, CREDENTIAL_KEY)
    except SecretStoreError:
        key = None
    if not key:
        return {"ok": False, "error": "setup_required", "setup_command": _setup_command()}
    reputation = check_reputation(email, key)
    if not reputation["source"]["ok"]:
        return {"ok": False, "error": "reputation_unavailable", **reputation}
    return {"ok": True, "email": email, "domain": domain_from_arg(email),
            "checked_at": datetime.now(UTC).isoformat(), **reputation,
            "dns": audit_dns(email, selectors=selectors)}


@mcp.tool(annotations=READ_ONLY)
def audit_domain(target: str, selectors: list[str] | None = None) -> dict:
    """Inspect public mail DNS for a domain (or an address's domain), without an API key.

    Use only for explicit DNS-only audits, not as a fallback for a failed email
    investigation. No DNS modifications. Does not evaluate message authentication,
    recursive SPF includes, organizational DMARC inheritance or DKIM signatures.
    """
    try:
        report = audit_dns(target, selectors=selectors)
    except ValueError as exc:
        return {"ok": False, "error": "invalid_input", "detail": str(exc)}
    return {"ok": True, "checked_at": datetime.now(UTC).isoformat(), "dns": report}
