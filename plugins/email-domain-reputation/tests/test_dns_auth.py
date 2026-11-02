import importlib
import importlib.util

import pytest

PACKAGE = "shadetree_ai_plugins_email_domain_reputation"


def dns_module():
    assert importlib.util.find_spec(PACKAGE), "The reputation plugin package must exist"
    return importlib.import_module(f"{PACKAGE}.dns_auth")


@pytest.mark.parametrize("record,strict", [
    ("v=spf1 include:_spf.google.com -all", True),
    ("v=spf1 ~all", False), ("v=spf1 +all", False),
    ("v=spf1 redirect=example.com", False),
])
def test_spf_preserves_terminal_qualifier(record, strict):
    assert dns_module().parse_spf([record])["strict"] is strict


def test_multiple_spf_records_cannot_be_strict():
    result = dns_module().parse_spf(["v=spf1 -all", "v=spf1 -all"])
    assert result["multiple_records"] is True
    assert result["strict"] is False


@pytest.mark.parametrize("records,enforced", [
    (["v=DMARC1; p=reject"], True),
    (["v=DMARC1; p=quarantine; pct=100"], True),
    (["v=DMARC1; p=none"], False),
    (["v=DMARC1; p=reject; pct=50"], False),
    (["v=DMARC1; p=reject; pct=bogus"], False),
    (["v=DMARC1; p=reject; pct=101"], False),
    (["v=DMARC1; p=reject; p=none"], False),
    (["v=DMARC1; p=reject", "v=DMARC1; p=reject"], False),
    ([], False),
])
def test_dmarc_does_not_turn_invalid_or_partial_policy_into_enforcement(records, enforced):
    assert dns_module().parse_dmarc(records)["enforced"] is enforced


def test_dns_audit_decodes_segmented_txt_and_detects_null_mx(monkeypatch):
    module = dns_module()
    import dns.rdata

    def resolve(name, rdtype, **kwargs):
        assert name.endswith(".")
        assert kwargs["search"] is False
        assert kwargs["lifetime"] <= 5
        values = {
            ("example.com.", "MX"): ["0 ."],
            ("example.com.", "TXT"): ['"v=spf1 " "-all"'],
            ("_dmarc.example.com.", "TXT"): ['"v=DMARC1; p=reject"'],
            ("custom._domainkey.example.com.", "TXT"): ['"v=DKIM1; " "p=YWJj"'],
        }
        return [dns.rdata.from_text("IN", rdtype, text) for text in values.get((name, rdtype), [])]

    monkeypatch.setattr(module.dns.resolver, "resolve", resolve)
    result = module.audit_dns("example.com", selectors=["custom"])
    assert result["mx"]["null_mx"] is True
    assert result["mx"]["accepts_mail"] is False
    assert result["spf"]["strict"] is True
    assert result["dmarc"]["enforced"] is True
    assert result["dkim"]["selectors_found"]["custom"]["has_public_key"] is True
    assert "google" in result["dkim"]["selectors_checked"]
    assert result["errors"] == {}


def test_dns_failures_are_unknown_not_missing_and_dkim_errors_survive(monkeypatch):
    module = dns_module()
    import dns.exception
    import dns.resolver

    def resolve(name, rdtype, **kwargs):
        if rdtype == "MX" or name.startswith("google._domainkey."):
            raise dns.exception.Timeout()
        raise dns.resolver.NoAnswer()

    monkeypatch.setattr(module.dns.resolver, "resolve", resolve)
    result = module.audit_dns("example.com")
    assert result["mx"]["found"] is None
    assert result["mx"]["accepts_mail"] is None
    assert result["spf"]["found"] is False
    assert result["dkim"]["found"] is None
    assert "mx" in result["errors"]
    assert "google" in result["dkim"]["errors"]


def test_absent_mx_is_not_proof_that_mail_cannot_be_received(monkeypatch):
    module = dns_module()
    import dns.resolver

    def resolve(*args, **kwargs):
        raise dns.resolver.NoAnswer()

    monkeypatch.setattr(module.dns.resolver, "resolve", resolve)
    result = module.audit_dns("example.com")
    assert result["mx"]["found"] is False
    assert result["mx"]["accepts_mail"] is None
    assert result["dmarc"]["inheritance_checked"] is False


@pytest.mark.parametrize("value", ["a@@example.com", "x@-example.com", "x@y..com", "x@localhost",
    "https://example.com", "x@example.com\nInjected", ".x@example.com", "x..y@example.com"])
def test_invalid_input_rejected(value):
    module = dns_module()
    with pytest.raises(ValueError):
        module.normalize_email(value)


def test_address_normalization_preserves_local_part():
    module = dns_module()
    assert module.normalize_email("Person+tag@EXAMPLE.COM") == "Person+tag@example.com"
    assert module.domain_from_arg("Person@example.com") == "example.com"


@pytest.mark.parametrize("selectors", [["bad;selector"], ["x."], ["a" * 64], ["x"] * 21])
def test_invalid_or_excessive_selectors_rejected_before_dns(selectors):
    with pytest.raises(ValueError):
        dns_module().audit_dns("example.com", selectors=selectors)
