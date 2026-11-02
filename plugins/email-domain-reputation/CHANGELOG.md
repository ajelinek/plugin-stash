# Changelog

## 0.1.0 — 2026-10-30

- Migrated the reputation and mail-DNS skill to a local FastMCP plugin.
- Added one-address investigations, keyless domain audits, optional known DKIM
  selectors, and cross-platform DNS without a system `dig` requirement.
- Added reusable hidden-prompt OS-keychain credential setup in `packages/common`.
- Switched to POST/Bearer authentication, normalized-only output, explicit API
  failure handling, and evidence-led assessments instead of Trust/Block claims.
- Corrected duplicate-SPF handling, null MX, unknown DNS errors, invalid DMARC
  percentages/tags, and per-selector DKIM error reporting.
