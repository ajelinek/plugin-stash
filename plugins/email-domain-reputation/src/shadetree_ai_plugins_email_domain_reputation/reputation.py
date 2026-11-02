"""AbstractAPI fetch/normalization, migrated from check_reputation.py.

Only normalized evidence leaves this module. No verdict, persistence, raw
response, breach-domain list, retries, or query-string credentials.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request

ABSTRACTAPI_URL = "https://emailreputation.abstractapi.com/v1/"
REQUEST_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 256 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _number(value: object, *, integer: bool = False) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = float(value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0 or (integer and not number.is_integer()):
        return None
    return int(number) if integer else number


def normalize_reputation(raw: dict) -> dict:
    """Flatten the original signal namespaces, preserving unknowns as null."""
    groups = {
        name: raw[name] if isinstance(raw.get(name), dict) else {}
        for name in ("email_deliverability", "email_sender", "email_domain",
                     "email_quality", "email_risk", "email_breaches")
    }
    delivery, sender, domain, quality, risk, breaches = groups.values()

    def boolean(group, key):
        value = group.get(key)
        return value if isinstance(value, bool) else None

    def text(group, key):
        value = group.get(key)
        return value[:256] if isinstance(value, str) else None

    def risk_level(key):
        value = risk.get(key)
        return value if isinstance(value, str) and value in {"low", "medium", "high"} else None

    count = _number(breaches.get("total_breaches"), integer=True)
    score = _number(quality.get("score"))
    status = text(delivery, "status")
    return {
        "suggested_correction": text(raw, "suggested_correction"),
        "deliverability_status": status,
        "deliverability_detail": text(delivery, "status_detail"),
        "deliverable": {"deliverable": True, "undeliverable": False}.get(status),
        "is_valid_format": boolean(delivery, "is_format_valid"),
        "smtp_valid": boolean(delivery, "is_smtp_valid"),
        "valid_mx": boolean(delivery, "is_mx_valid"),
        "email_provider_name": text(sender, "email_provider_name"),
        "organization_name": text(sender, "organization_name"),
        "domain_age_days": _number(domain.get("domain_age"), integer=True),
        "is_risky_tld": boolean(domain, "is_risky_tld"),
        "is_live_site": boolean(domain, "is_live_site"),
        "quality_score": score if score is not None and score <= 1 else None,
        "is_free_email": boolean(quality, "is_free_email"),
        "is_username_suspicious": boolean(quality, "is_username_suspicious"),
        "is_disposable": boolean(quality, "is_disposable"),
        "is_catchall": boolean(quality, "is_catchall"),
        "is_role": boolean(quality, "is_role"),
        "spf_strict": boolean(quality, "is_spf_strict"),
        "dmarc_enforced": boolean(quality, "is_dmarc_enforced"),
        "address_risk": risk_level("address_risk_status"),
        "domain_risk": risk_level("domain_risk_status"),
        "total_breaches": count,
        "data_breach": count > 0 if count is not None else None,
    }


def _failure(error: str, detail: str) -> dict:
    return {"signals": {}, "source": {"ok": False, "error": error, "detail": detail}}


def check_reputation(email: str, api_key: str) -> dict:
    request = urllib.request.Request(
        ABSTRACTAPI_URL,
        data=json.dumps({"email": email}).encode(),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "Accept": "application/json"},
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(NoRedirect())
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            status = response.status
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        body = b""
    except (TimeoutError, urllib.error.URLError, OSError, ValueError):
        return _failure("network_error", "AbstractAPI was unreachable or timed out; no retry was made.")
    if status == 401:
        return _failure("invalid_api_key", "AbstractAPI rejected the key. Use credentials replace.")
    if status in {403, 422}:
        return _failure("quota_exhausted", "AbstractAPI credits or access are exhausted; check your plan.")
    if status == 429:
        return _failure("rate_limited", "AbstractAPI per-second rate limit hit; wait before trying again.")
    if status != 200:
        return _failure("http_error", f"AbstractAPI returned HTTP {status}; no retry was made.")
    try:
        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("oversized response")
        raw = json.loads(body)
        if not isinstance(raw, dict):
            raise ValueError("expected object")
        delivery = raw.get("email_deliverability")
        if not isinstance(delivery, dict) or delivery.get("status") not in (
            "deliverable", "undeliverable", "unknown"
        ):
            raise ValueError("missing deliverability")
        signals = normalize_reputation(raw)
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return _failure("invalid_response", "AbstractAPI returned an unusable response; no assessment made.")
    return {"signals": signals, "source": {"ok": True, "error": None, "detail": None}}
