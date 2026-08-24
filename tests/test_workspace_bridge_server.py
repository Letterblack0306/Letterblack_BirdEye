from __future__ import annotations

import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from workspace_bridge_server import TransportError, make_handler, serve


def _config(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "knowledge_roots": [
            {"name": "test", "path": str(workspace), "root_class": "workspace"}
        ]
    }), encoding="utf-8")
    return config


def _request(handler, path: str, *, method: str = "GET", token: str | None = None, body=None):
    from http.server import ThreadingHTTPServer

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        encoded = None
        if body is not None:
            encoded = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(encoded))
        connection.request(method, path, body=encoded, headers=headers)
        response = connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_health_requires_bearer_token(tmp_path):
    handler = make_handler(config_path=_config(tmp_path), token="secret")
    status, payload = _request(handler, "/health")
    assert status == 401
    assert payload["error"] == "UNAUTHORIZED"


def test_health_reports_bounded_operations(tmp_path):
    handler = make_handler(config_path=_config(tmp_path), token="secret")
    status, payload = _request(handler, "/health", token="secret")
    assert status == 200
    assert payload["transport"] == "loopback-only"
    assert "git.status" in payload["operations"]
    assert "pytest" in payload["operations"]


def test_diagnostic_rejects_unknown_operation(tmp_path):
    handler = make_handler(config_path=_config(tmp_path), token="secret")
    status, payload = _request(
        handler,
        "/diagnostic",
        method="POST",
        token="secret",
        body={"workspace": "test", "operation": "shell", "args": ["whoami"]},
    )
    assert status == 400
    assert payload["error"] == "BridgeError"


def test_diagnostic_executes_fixed_workspace_operation(tmp_path, monkeypatch):
    config = _config(tmp_path)
    observed = {}

    def fake_execute(request, config_path):
        observed["workspace"] = request.workspace
        observed["operation"] = request.operation
        observed["config_path"] = config_path
        return {
            "request_id": "bd-test",
            "workspace": request.workspace,
            "operation": request.operation,
            "exit_code": 0,
            "stdout": "ok",
            "stderr": "",
        }

    monkeypatch.setattr("workspace_bridge_server.execute", fake_execute)
    handler = make_handler(config_path=config, token="secret")
    status, payload = _request(
        handler,
        "/diagnostic",
        method="POST",
        token="secret",
        body={"workspace": "test", "operation": "git.status"},
    )
    assert status == 200
    assert payload["exit_code"] == 0
    assert observed == {
        "workspace": "test",
        "operation": "git.status",
        "config_path": config,
    }


def test_server_rejects_non_loopback_bind(tmp_path):
    with pytest.raises(TransportError, match="loopback-only"):
        serve(
            host="0.0.0.0",
            port=0,
            config_path=_config(tmp_path),
            token="secret",
        )
