"""Focused public-route regression tests for the installed Control Center gateway."""
from __future__ import annotations

import asyncio
import json

import httpx

import control_center_api
import http_mcp_server


async def fetch_public_and_private_routes():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_mcp_server.build_app()),
        base_url="http://127.0.0.1:8766",
    ) as client:
        urls = [
            "/", "/apps/birdeye-control/", "/control.css", "/control.js",
            "/control/api/snapshot", "/control/api/workspace?root=__birdeye__",
            "/control/api/storage", "/control/api/config", "/apps/status",
            "/control/api/history?root=__birdeye__",
        ]
        return {url: await client.get(url) for url in urls}


def test_control_center_routes_restore_original_letterblack_launcher():
    result = asyncio.run(fetch_public_and_private_routes())
    assert all(reply.status_code == 200 for reply in result.values())
    home = result["/"].text
    assert 'LB Effects Editor' in home
    assert 'BirdEye Control Center' in home
    assert home.index('LB Effects Editor') < home.index('BirdEye Control Center')
    app = result["/apps/birdeye-control/"].text
    assert 'Control Center' in app
    assert '/control.js' in app and '/control.css' in app
    assert result["/control.css"].headers["content-type"].startswith("text/css")
    data = result["/control/api/snapshot"].json()
    assert data["ok"]
    assert data["tools"]
    assert len(data["tools"]) == http_mcp_server._tool_definitions().__len__()
    assert data["host"]


def test_workspace_provenance_and_real_receipt_endpoints():
    result = asyncio.run(fetch_public_and_private_routes())
    workspace = result["/control/api/workspace?root=__birdeye__"].json()
    assert workspace["ok"] and workspace["exists"]
    assert workspace["git"]["is_repository"] is True
    assert workspace["evidence_source"].startswith("live filesystem")
    history = result["/control/api/history?root=__birdeye__"].json()
    assert history["ok"] and history["source"].endswith("receipt.json")
    assert "records" in history
    data = result["/control/api/storage"].json()
    assert data["ok"] and any(entry["name"] == "Execution receipts" for entry in data["stores"])


async def invalid_cases():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_mcp_server.build_app()),
        base_url="http://127.0.0.1:8766",
    ) as client:
        bad_root = await client.get("/control/api/workspace?root=does-not-exist")
        bad_receipt = await client.get("/control/api/receipt?id=../../windows/system.ini")
        invalid_write = await client.post(
            "/control/api/config", json={"id": "unknown-root", "enabled": False},
            headers={"origin": "http://127.0.0.1:8766"},
        )
        foreign_origin = await client.post(
            "/control/api/config", json={"id": "brew", "enabled": False},
            headers={"origin": "https://malicious.example"},
        )
        return bad_root, bad_receipt, invalid_write, foreign_origin


def test_invalid_targets_and_foreign_origin_are_rejected_without_config_changes():
    before = (control_center_api.ROOT / "config.json").read_bytes()
    bad_root, bad_receipt, invalid_write, foreign_origin = asyncio.run(invalid_cases())
    assert bad_root.status_code == 404
    assert bad_receipt.status_code == 400
    assert invalid_write.status_code == 409
    assert foreign_origin.status_code == 403
    assert (control_center_api.ROOT / "config.json").read_bytes() == before


def test_freellmapi_ping_contract_is_status_ok(monkeypatch):
    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, max_bytes): return b'{"status":"ok"}'
    monkeypatch.setattr(control_center_api, "urlopen", lambda url, timeout=1.4: FakeResponse())
    assert control_center_api.service_probe("http://127.0.0.1:8767/api/ping")["ok"]


def test_native_control_center_aliases_do_not_fall_into_generic_app_proxy():
    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=http_mcp_server.build_app()),
            base_url="http://127.0.0.1:8766",
            follow_redirects=True,
        ) as client:
            good = ["/apps/birdeye-control/index.html", "/apps/birdeye-control//"]
            for url in good:
                reply = await client.get(url)
                assert reply.status_code == 200, (url, reply.status_code, reply.text)
                assert "Control Center" in reply.text, url
                assert "unknown app" not in reply.text
            bad = await client.get("/apps/birdeye-control/does-not-exist")
            assert bad.status_code == 404
            other = await client.get("/apps/nonexistent/")
            assert other.status_code == 404
            assert other.json() == {"error": "unknown app"}
    asyncio.run(run())

def test_service_probe_reports_actionable_failure(monkeypatch):
    def refused(*args, **kwargs):
        raise ConnectionRefusedError("simulated local endpoint refused")
    monkeypatch.setattr(control_center_api, "urlopen", refused)
    result = control_center_api.service_probe("http://127.0.0.1:7726/health")
    assert result["ok"] is False
    assert result["reason"] == "ConnectionRefusedError"
    assert "existing service" in result["next_action"]


def test_browser_service_up_but_extension_missing_is_not_connected(monkeypatch):
    class FakeResponse:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, max_bytes):
            return json.dumps({"ok": True, "service": "birdeye-browser-relay",
                               "browser_client": {"seen": 100.0, "version": "0.3.2"}}).encode()
    monkeypatch.setattr(control_center_api, "urlopen", lambda *args, **kwargs: FakeResponse())
    result = control_center_api.service_probe("http://127.0.0.1:7726/health")
    assert result["ok"] is True
    assert result["client_connected"] is False
    assert "30 seconds" in result["client_reason"]


def test_index_and_connection_diagnostics_hooks_exist():
    from pathlib import Path
    js = (Path(__file__).parent.parent / "control_center.js").read_text(encoding="utf8")
    for marker in ("function diagnostic(", "function serviceStatus(", "root-failure-reason",
                   "Chrome extension connection not verified", "Projection is behind by",
                   "Configured storage location is not present", "Recorded execution evidence failed"):
        assert marker in js
