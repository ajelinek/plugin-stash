import importlib
import importlib.util
import json

import keyring
from fastmcp import Client

PACKAGE = "shadetree_ai_plugins_email_domain_reputation"


def server_module():
    assert importlib.util.find_spec(PACKAGE), "The reputation plugin package must exist"
    return importlib.import_module(f"{PACKAGE}.server")


async def test_mcp_tools_never_accept_or_return_api_keys(monkeypatch):
    module = server_module()
    monkeypatch.setattr(keyring, "get_keyring", lambda: keyring.backends.fail.Keyring())
    async with Client(module.mcp) as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools} == {"check_setup", "investigate_email", "audit_domain"}
        for tool in tools:
            assert "api_key" not in json.dumps(tool.inputSchema)
            assert tool.annotations.readOnlyHint is True
        report = (await client.call_tool("check_setup", {})).data
    assert report["ready"] is False
    assert "credentials set" in report["setup_command"]


async def test_missing_key_prevents_address_network_calls(monkeypatch):
    module = server_module()
    monkeypatch.setattr(keyring, "get_keyring", lambda: keyring.backends.fail.Keyring())
    async with Client(module.mcp) as client:
        report = (await client.call_tool("investigate_email", {"email": "person@example.com"})).data
    assert report["ok"] is False
    assert report["error"] == "setup_required"
    assert "dns" not in report


async def test_keyless_domain_audit_uses_real_dns_aggregation(monkeypatch):
    module = server_module()
    dns_module = importlib.import_module(f"{PACKAGE}.dns_auth")
    monkeypatch.setattr(dns_module.dns.resolver, "resolve", lambda *args, **kwargs: [])
    async with Client(module.mcp) as client:
        report = (await client.call_tool("audit_domain", {"target": "example.com"})).data
    assert report["ok"] is True
    assert report["dns"]["spf"]["found"] is False
    assert "source" not in report
