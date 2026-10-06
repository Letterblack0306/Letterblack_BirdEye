from __future__ import annotations

import json
from pathlib import Path

import pytest

from capability_gateway import capability_discover, capability_invoke
from workspace_bridge import BridgeError


def _config(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "knowledge_roots": [
            {"name": "demo", "path": str(workspace), "root_class": "workspace"}
        ],
        "state_dir": str(tmp_path / "state"),
    }), encoding="utf-8")
    return config


def test_discovery_reports_existing_owners():
    result = capability_discover()
    names = {item["name"] for item in result["capabilities"]}
    assert "workspace.command" in names
    assert "workspace.sequence" in names
    assert "github.request-bridge" in names
    assert "browser.relay" in names
    assert result["authority"] == "birdeye-capability-gateway"


def test_transport_only_capability_is_not_fake_invokable(tmp_path):
    with pytest.raises(BridgeError, match="CAPABILITY_NOT_INVOKABLE"):
        capability_invoke("github.request-bridge", {}, _config(tmp_path))


def test_request_cannot_supply_identity(tmp_path):
    with pytest.raises(BridgeError, match="CALLER_IDENTITY_NOT_ACCEPTED"):
        capability_invoke(
            "workspace.command",
            {"workspace": "demo", "argv": ["git", "status"], "actor": "agent"},
            _config(tmp_path),
        )


def test_workspace_command_routes_to_existing_owner(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured = {}

    def fake_run(request, config_path):
        captured["workspace"] = request.workspace
        captured["argv"] = request.argv
        captured["config_path"] = config_path
        return {"ok": True, "receipt": {"execution_evidence_sha256": "abc"}}

    monkeypatch.setattr("capability_gateway.run_command", fake_run)
    result = capability_invoke(
        "workspace.command",
        {"workspace": "demo", "argv": ["git", "status", "--short"]},
        config,
    )
    assert captured["workspace"] == "demo"
    assert captured["argv"] == ("git", "status", "--short")
    assert captured["config_path"] == config
    assert result["authority"] == "birdeye-capability-gateway"
    assert result["owner"] == "workspace_bridge.run_command"
    assert result["result"]["receipt"]["execution_evidence_sha256"] == "abc"


def test_mcp_surface_exposes_gateway_tools():
    import mcp_server

    names = {item["name"] for item in mcp_server._TOOL_DEFINITIONS}
    assert "capability_discover" in names
    assert "capability_invoke" in names
    assert "capability_discover" in mcp_server._TOOL_REGISTRY
    assert "capability_invoke" in mcp_server._TOOL_REGISTRY
