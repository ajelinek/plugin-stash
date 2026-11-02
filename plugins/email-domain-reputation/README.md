# Email/domain reputation

Give Claude one email address. The plugin checks AbstractAPI reputation and
deliverability signals, then inspects live SPF, DMARC, MX, and common DKIM
selectors. Claude explains the evidence, unknowns, and next steps without
claiming the sender or a particular message is verified safe.

## Install and setup

Install `email-domain-reputation` from `ajelinek/shadetree-ai-plugins` in
Claude Desktop's Plugins panel. The machine needs `uv` on PATH; Python and
locked dependencies are managed by uv. No `dig` installation is needed.

Ask Claude to run `check_setup` and show its setup command. Run that command
yourself in a local terminal. It prompts for your Email Reputation API key
twice without echoing, then saves and verifies it in the OS keychain.
Get a product-specific key at https://www.abstractapi.com/api/email-reputation-api.

From this checkout, the equivalent command is:

```sh
uv run --project plugins/email-domain-reputation --locked python -m shadetree_ai_plugins_email_domain_reputation credentials set
```

For installed clients, use the absolute project path returned by `check_setup`,
not a checkout path or `${CLAUDE_PLUGIN_ROOT}`. Replace `set` with `status`,
`replace`, or `delete` to inspect, rotate, or remove the key. Values are never
command arguments. Never paste keys into chat, `.env` files, or MCP calls.

Supported credential stores: macOS Keychain, Windows Credential Locker,
Linux Secret Service/KWallet. An unavailable/locked store is a setup error;
there is no plaintext fallback. The service is
`shadetree-ai-plugins:email-domain-reputation`, credential `abstractapi-api-key`.
Key storage survives plugin updates. The shared setup implementation lives in
`packages/common`, so other plugins can use the same flow with their own namespace.

## Use

- “Check person@example.com.” Full reputation + DNS investigation.
- “Audit my domain's SPF and DMARC.” Keyless domain DNS audit.
- “Check DKIM for example.com using selector custom2026.” Adds the known selector
  to the defaults, rather than requiring it for every investigation.

Tools: `check_setup`, `investigate_email(email, selectors?)`, and
`audit_domain(target, selectors?)`. Setup status checks only local key presence;
actual API validity/access is checked on an investigation. Address investigations
require a working reputation lookup and stop on missing keys, API errors, or
exhausted quota. They do not silently downgrade to a DNS-only report or retry.
DNS failures within a successful investigation are reported as missing evidence.

## Privacy and limitations

The MCP process runs locally. Address checks send the address to **AbstractAPI**;
DNS checks query the machine's configured resolver. One address costs one metered
API request; plan-dependent fields may be missing. No persistent lookup history,
raw provider-response logging, or breach-list output. Chat results are still
subject to the client's retention policy.

An address cannot authenticate a message or person. DNS configuration isn't a
message SPF/DKIM/DMARC result. DKIM discovery is best-effort, and keys are not
cryptographically validated. This release does not evaluate recursive SPF
includes/lookup limits or organizational-domain DMARC inheritance. DNS lookup
errors are unknown, not missing records; absent MX is not proof of no mail service.
No bulk screening, message/header analysis, DNS modifications, or automatic blocking.

Implemented for macOS, Windows, and Linux. Automated checks and local keychain
validation were run on macOS; real Windows/Linux and installed Desktop smoke
tests remain necessary before promising those environments are verified.

## Development

```sh
uv run pytest plugins/email-domain-reputation/tests packages/common/tests
uv run ruff check .
claude plugin validate plugins/email-domain-reputation
```

Tests replace network/keychain boundaries; they do not spend real API credits.
Also test a dereferenced standalone copy with its own `uv.lock`, as described in
`docs/plugin-authoring.md`. No live reputation lookup is part of the test suite.
