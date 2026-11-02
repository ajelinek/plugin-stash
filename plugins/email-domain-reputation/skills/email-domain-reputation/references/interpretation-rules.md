# Evidence-led interpretation

Adapted from the original skill's fetch/interpret split. The MCP tools collect
evidence; the skill makes the assessment. Thresholds below are heuristics, not
a validated fraud model. Attribute provider risk labels to AbstractAPI.

## 1. Completeness first

- `ok: false`: stop. Explain the setup/provider failure without a verdict or
  DNS-only fallback. A stored key alone does not establish working API access.
- `source.ok: true` is a successful provider lookup, not proof of full coverage.
  Read the actual fields. Missing risk/quality/domain-age signals remain unknown.
- A nonempty `dns.errors` section makes that section unavailable. Ignore its
  derived negative fields. DKIM can have successful discoveries and failed
  selectors together; report both. An error is never "no record found".
- If the evidence doesn't support either an affirmative concern or a useful
  clean finding, use **Insufficient evidence** and name what is missing. Don't
  turn deliverability, a zero breach count, or an MX record alone into confidence.

## 2. High risk

Use **High risk** if AbstractAPI reports `address_risk: high`, naming the source.
The original combination of a disposable address and a domain less than 90 days
old can also warrant High risk, but explain that this is a heuristic, not proof
of fraud. Include every relevant driver, not just the first one found.

A risky TLD alone is not enough for High risk. A domain or mailbox that was in a
breach is not thereby controlled by an attacker. Don't infer either identity
or malicious intent from deliverability, free-email use, or a role address.

## 3. Caution

If not High risk, use **Caution** for specific supported concerns:

- provider `address_risk: medium` or `domain_risk: medium/high`;
- disposable provider, a domain under 90 days old, risky TLD, or suspicious
  username (weak heuristics; state their limitations);
- undeliverable mailbox (use `deliverability_detail`; full mailboxes and
  unreachable servers are delivery problems, not proof of fraud);
- catch-all mailboxes (the provider cannot verify a particular mailbox);
- breach exposure (security hygiene concern, not evidence the sender is hostile);
- explicit null MX (`dns.mx.null_mx: true`) advertises no incoming mail service.

Missing SPF/DMARC is a configuration finding, not by itself a reason to accuse a
sender. Missing MX is inconclusive: SMTP may fall back to A/AAAA records, which
this audit does not test. If valid MX records exist, that's published routing,
not a successful SMTP delivery test. Resolve contradictory evidence explicitly.

## 4. No red flags found

Use **No red flags found** when no concern above applies and there is meaningful
positive evidence: both provider address/domain risk are low and the address is
deliverable. A quality score over 0.7 may corroborate that, but it is an email
quality score, not a probability that a sender is safe. DNS policy can corroborate
published anti-spoofing intent but cannot establish message authenticity.

If data is mostly null or only delivery/DNS data is available, use
**Insufficient evidence**, even if nothing adverse was returned. Always state
that a particular message, person's identity, or payment request remains unverified.

## 5. Domain audit and next steps

Report the published records and their limitations. For a third-party address,
prioritize what the requester can do: verify an unexpected request through a
known independent channel; don't rely on a good address score to approve a
payment, attachment, or credential request. Don't recommend blocking automatically.

For the owner's requested configuration audit:

- **SPF:** report the record and `all_mechanism`. Multiple records are invalid;
  consolidate with the mail administrator. `strict` describes one published
  record's `-all`, not full SPF validity, authorized-sender completeness, or the
  result for an actual sending IP. `redirect=` can be legitimate without `all`.
  Inventory legitimate senders before tightening policy; don't prescribe an
  invented `include:` or change `~all` blindly.
- **DMARC:** report the exact-name record, `policy`, `pct`, validity checks,
  and `rua`. Duplicate tags/records and invalid percentages must not be presented
  as enforcement. These are limited record checks, not full RFC validation.
  `inheritance_checked: false` means an absent record here may inherit an
  organizational-domain policy. Check that before recommending a new record.
  For rollout, inventory SPF/DKIM alignment, monitor aggregate reports, then
  move deliberately toward quarantine/reject after reviewing legitimate senders.
- **DKIM:** name selectors checked/found, empty/revoked keys, duplicate key records,
  and selector errors. A nonempty public-key string isn't cryptographic key
  validation. If no key is discovered, check the platform admin settings and a
  real message's selector rather than declaring DKIM disabled. Optional known
  selectors can be passed to the tool on a follow-up audit.
- **MX:** distinguish null MX, records found, no records found, and unknown due
  to failure. Null MX can be intentional for a non-mail domain. No MX alone is
  not proof that mail cannot be received.

Don't apply records or invent provider-specific fixes. Identify the precise
finding and the information the administrator needs before making a change.
