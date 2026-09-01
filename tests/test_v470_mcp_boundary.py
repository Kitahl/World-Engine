from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_optional_mcp_is_declared_and_enforced_as_trusted_local_only():
    source = (ROOT / "mcp_server.py").read_text(encoding="utf-8")
    assert "For a public hostname" not in source
    assert "MCP_ALLOWED_HOSTS" not in source
    assert "MCP_ALLOWED_ORIGINS" not in source
    assert "World Engine v4.7 trusted-local operator" in source
    assert "ip_address(address).is_loopback" in source
    assert "MCP_HOST must be a loopback address" in source
    assert "enable_dns_rebinding_protection=True" in source


def test_public_gpt_schema_does_not_expose_mcp_operator_tools():
    import json

    schema = json.loads((ROOT / "openapi_actions.json").read_text(encoding="utf-8"))
    operation_ids = {
        operation["operationId"]
        for methods in schema["paths"].values()
        for operation in methods.values()
        if isinstance(operation, dict) and operation.get("operationId")
    }
    assert "getInternalStateBlock" not in operation_ids
    assert "authorWorldContent" not in operation_ids
    assert "runRulesKernel" not in operation_ids
