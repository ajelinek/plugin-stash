"""External HTTP is replaced; parsing and normalization run for real."""

import importlib
import importlib.util
import json
import urllib.error
from io import BytesIO

import pytest

PACKAGE = "shadetree_ai_plugins_email_domain_reputation"


def reputation_module():
    assert importlib.util.find_spec(PACKAGE), "The reputation plugin package must exist"
    return importlib.import_module(f"{PACKAGE}.reputation")


def payload(**overrides):
    return {
        "email_address": "person@example.com",
        "email_deliverability": {"status": "deliverable", "is_smtp_valid": True},
        "email_risk": {"address_risk_status": "low", "domain_risk_status": "low"},
        "email_quality": {"score": "0.80", "is_disposable": False},
        "email_domain": {"domain_age": 100, "is_risky_tld": False},
        "email_breaches": {"total_breaches": 0, "breached_domains": ["private.example"]},
        **overrides,
    }


def test_fetch_uses_post_bearer_and_returns_only_normalized_signals(monkeypatch):
    module = reputation_module()
    requests = []

    class Response(BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            assert 0 < timeout <= 15
            return Response(json.dumps(payload()).encode())

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: Opener())
    result = module.check_reputation("person@example.com", "fixture-secret")
    request = requests[0]
    assert request.method == "POST"
    assert "fixture-secret" not in request.full_url
    assert "person" not in request.full_url
    assert request.get_header("Authorization") == "Bearer fixture-secret"
    assert json.loads(request.data) == {"email": "person@example.com"}
    assert result["source"]["ok"] is True
    assert result["signals"]["quality_score"] == 0.8
    assert result["signals"]["total_breaches"] == 0
    assert result["signals"]["data_breach"] is False
    assert "private.example" not in json.dumps(result)
    assert "fixture-secret" not in json.dumps(result)


@pytest.mark.parametrize("status,error", [
    (401, "invalid_api_key"), (403, "quota_exhausted"), (422, "quota_exhausted"),
    (429, "rate_limited"), (503, "http_error"), (302, "http_error"),
])
def test_http_failures_are_sanitized_and_not_retried(monkeypatch, status, error):
    module = reputation_module()
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            raise urllib.error.HTTPError(
                "https://example.com/fixture-secret", status, "fixture-secret", {},
                BytesIO(b"fixture-secret"),
            )

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: Opener())
    result = module.check_reputation("person@example.com", "fixture-secret")
    assert len(calls) == 1
    assert result["source"]["error"] == error
    assert result["signals"] == {}
    assert "fixture-secret" not in json.dumps(result)


@pytest.mark.parametrize("body", [b"not json", b"[]", b"null", b"{}",
    b'{"email_deliverability": []}', b'{"email_deliverability":{"status":"nonsense"}}'])
def test_invalid_response_is_not_a_clean_assessment(monkeypatch, body):
    module = reputation_module()

    class Response(BytesIO):
        status = 200

    class Opener:
        def open(self, request, timeout):
            return Response(body)

    monkeypatch.setattr(module.urllib.request, "build_opener", lambda *args: Opener())
    result = module.check_reputation("person@example.com", "fixture-secret")
    assert result["source"]["error"] == "invalid_response"


def test_redirect_handler_refuses_to_forward_credentials():
    module = reputation_module()
    handler = module.NoRedirect()
    request = module.urllib.request.Request("https://emailreputation.abstractapi.com/v1/")
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://other.test") is None


def test_missing_and_wrongly_typed_signals_stay_unknown():
    module = reputation_module()
    signals = module.normalize_reputation(payload(
        email_quality={"score": "NaN", "is_disposable": "false"},
        email_domain={"domain_age": True}, email_breaches={"total_breaches": False},
        email_risk={"address_risk_status": "banana"},
    ))
    assert signals["quality_score"] is None
    assert signals["is_disposable"] is None
    assert signals["domain_age_days"] is None
    assert signals["total_breaches"] is None
    assert signals["address_risk"] is None
