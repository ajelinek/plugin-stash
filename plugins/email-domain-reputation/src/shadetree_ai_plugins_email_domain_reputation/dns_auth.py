"""Live DNS record inspection, adapted from check_dns_auth.py.

Uses dnspython with the machine's resolver, bounded concurrent queries, and
explicit unknowns. This is not an SPF evaluator or a message-authentication test.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor

import dns.exception
import dns.resolver

COMMON_DKIM_SELECTORS = ("google", "selector1", "selector2", "default", "dkim",
                         "k1", "s1", "s2", "mail")
LABEL_RE = re.compile(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?")
LOCAL_RE = re.compile(r"[a-zA-Z0-9!#$%&'*+/=?^_`{|}~.-]+")
QUERY_TIMEOUT_SECONDS = 3


def normalize_domain(value: str) -> str:
    if not value or any(char.isspace() or ord(char) < 32 for char in value):
        raise ValueError("Provide a domain name without spaces, URLs, or paths.")
    try:
        domain = value.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise ValueError("Invalid internationalized domain name.") from None
    if len(domain) > 253 or "." not in domain or not all(
        LABEL_RE.fullmatch(label) for label in domain.split(".")
    ):
        raise ValueError("Provide a valid fully qualified domain name, not a URL or IP address.")
    if all(label.isdigit() for label in domain.split(".")):
        raise ValueError("Provide a domain name, not an IP address.")
    return domain


def normalize_email(value: str) -> str:
    if value.count("@") != 1:
        raise ValueError("Provide one email address, without a display name or message text.")
    local, domain = value.split("@")
    if not LOCAL_RE.fullmatch(local) or len(local) > 64 or local.startswith(".") or (
        local.endswith(".") or ".." in local
    ):
        raise ValueError("Unsupported email format; provide one ordinary, unquoted email address.")
    email = f"{local}@{normalize_domain(domain)}"
    if len(email) > 254:
        raise ValueError("Email address is too long.")
    return email


def domain_from_arg(value: str) -> str:
    return normalize_email(value).rsplit("@", 1)[1] if "@" in value else normalize_domain(value)


def validate_selectors(selectors: list[str] | None) -> list[str]:
    if selectors is not None and (len(selectors) > 20 or any(
        not selector or len(selector) > 128 or not all(
            LABEL_RE.fullmatch(label) for label in selector.split(".")
        ) for selector in selectors
    )):
        raise ValueError("Supply at most 20 DKIM selector names (DNS labels, at most 128 characters).")
    return list(dict.fromkeys([*COMMON_DKIM_SELECTORS, *(selectors or [])]))


def _query(name: str, rtype: str) -> tuple[list, str | None]:
    try:
        return list(dns.resolver.resolve(
            name + ".", rtype, search=False, lifetime=QUERY_TIMEOUT_SECONDS
        )), None
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return [], None
    except dns.exception.Timeout:
        return [], "DNS query timed out; result unknown."
    except (dns.exception.DNSException, OSError):
        return [], "DNS resolver failed; result unknown."


def _txt(records: list) -> list[str]:
    return [b"".join(record.strings).decode("utf-8", errors="replace") for record in records]


def _parse_tag_list(record: str) -> tuple[dict[str, str], bool]:
    tags = {}
    valid = True
    for part in record.split(";"):
        if not part.strip():
            continue
        key, equals, value = part.strip().partition("=")
        key = key.strip().lower()
        if not equals or key in tags:
            valid = False
        tags[key] = value.strip()
    return tags, valid


def parse_spf(records: list[str]) -> dict:
    match = re.search(r"(?:^|\s)([-~?+]?)all(?:\s|$)", records[0], re.I) if records else None
    qualifier = ((match.group(1) or "+") + "all") if match else None
    return {
        "records": records, "found": bool(records), "multiple_records": len(records) > 1,
        "all_mechanism": qualifier, "strict": len(records) == 1 and qualifier == "-all",
        "evaluation_performed": False,
    }


def parse_dmarc(records: list[str]) -> dict:
    record = records[0] if records else None
    tags, valid = _parse_tag_list(record or "")
    pct_text = tags.get("pct", "100")
    pct = int(pct_text) if pct_text.isascii() and pct_text.isdigit() and len(pct_text) <= 3 else None
    policy = tags.get("p")
    valid = bool(records) and len(records) == 1 and valid and tags.get("v") == "DMARC1" and (
        policy in {"none", "quarantine", "reject"}
    ) and pct is not None and 0 <= pct <= 100 and tags.get("sp", policy) in {
        "none", "quarantine", "reject"
    }
    return {
        "found": bool(records), "record": record, "multiple_records": len(records) > 1,
        "policy": policy, "subdomain_policy": tags.get("sp", policy), "pct": pct if records else None,
        "rua": [part.strip() for part in tags.get("rua", "").split(",") if part.strip()],
        "record_valid": valid, "enforced": valid and policy in {"quarantine", "reject"} and pct == 100,
        "inheritance_checked": False,
    }


def audit_dns(target: str, *, selectors: list[str] | None = None) -> dict:
    domain = domain_from_arg(target)
    checked = validate_selectors(selectors)
    queries = {"mx": (domain, "MX"), "spf": (domain, "TXT"),
               "dmarc": (f"_dmarc.{domain}", "TXT")}
    queries.update({f"dkim:{selector}": (f"{selector}._domainkey.{domain}", "TXT")
                    for selector in checked})
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {key: pool.submit(_query, *query) for key, query in queries.items()}
        results = {key: future.result() for key, future in futures.items()}
    errors = {key: error for key, (_, error) in results.items() if error and not key.startswith("dkim:")}
    mx_records = sorted(
        [{"preference": record.preference, "exchange": record.exchange.to_text().rstrip(".") or "."}
         for record in results["mx"][0]], key=lambda record: record["preference"]
    )
    null_mx = any(record["exchange"] == "." for record in mx_records)
    spf = parse_spf([record for record in _txt(results["spf"][0])
                     if re.match(r"^v=spf1(?:\s|$)", record, re.I)])
    dmarc = parse_dmarc([record for record in _txt(results["dmarc"][0])
                         if record.startswith("v=DMARC1;") or record == "v=DMARC1"])
    if "spf" in errors:
        spf.update(found=None, strict=None)
    if "dmarc" in errors:
        dmarc.update(found=None, enforced=None, record_valid=None)
    found = {}
    dkim_errors = {}
    for selector in checked:
        records, error = results[f"dkim:{selector}"]
        if error:
            dkim_errors[selector] = error
        candidates = []
        for record in _txt(records):
            tags, valid = _parse_tag_list(record)
            if "p" in tags and tags.get("v", "DKIM1") == "DKIM1":
                candidates.append((tags, valid))
        if candidates:
            tags, valid = candidates[0]
            found[selector] = {
                "has_public_key": len(candidates) == 1 and valid and bool(tags["p"]),
                "revoked": tags["p"] == "", "multiple_records": len(candidates) > 1,
            }
    if dkim_errors:
        errors["dkim"] = "Some DKIM selectors could not be checked; see dkim.errors."
    return {
        "domain": domain,
        "mx": {"records": mx_records, "found": None if "mx" in errors else bool(mx_records),
               "null_mx": None if "mx" in errors else null_mx,
               "accepts_mail": None if "mx" in errors or not mx_records else not null_mx},
        "spf": spf, "dmarc": dmarc,
        "dkim": {"selectors_checked": checked, "selectors_found": found, "errors": dkim_errors,
                 "found": True if found else (None if dkim_errors else False)},
        "errors": errors,
    }
