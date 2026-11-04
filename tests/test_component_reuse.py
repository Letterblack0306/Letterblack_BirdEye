import hashlib
import json
from pathlib import Path

from component_reuse import check_request, inventory


def fixtures(tmp_path: Path):
    folder = tmp_path / "0.3.5"
    folder.mkdir()
    body = json.dumps({"component": "birdeye-browser-relay", "version": "0.3.5",
                       "files": {"server.py": "a" * 64}}).encode()
    (folder / "release-manifest.json").write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def test_inventory_derives_existing_component_from_release(tmp_path):
    digest = fixtures(tmp_path)
    found = inventory(tmp_path)
    assert found["complete"]
    assert found["components"]["birdeye-browser-relay"][0]["manifest_sha256"] == digest


def test_known_duplicate_is_denied(tmp_path):
    fixtures(tmp_path)
    req = {"argv": ["powershell", "-Command", "New-Item browser_relay_v2"],
           "context_evidence": {"component_change": {
               "component": "birdeye-browser-relay", "operation": "create"}}}
    assert check_request(req, tmp_path)["error"] == "DUPLICATE_COMPONENT"


def test_missing_preflight_is_denied_for_mutation(tmp_path):
    fixtures(tmp_path)
    req = {"argv": ["powershell", "-Command", "New-Item -ItemType Directory browser_relay_v2"]}
    assert check_request(req, tmp_path)["error"] == "COMPONENT_PREFLIGHT_REQUIRED"


def test_existing_update_with_current_manifest_allowed(tmp_path):
    digest = fixtures(tmp_path)
    req = {"argv": ["powershell", "-Command", "Set-Content browser_relay/server.py updated"],
           "context_evidence": {"component_change": {
               "component": "birdeye-browser-relay", "operation": "modify_existing",
               "manifest_sha256": digest}}}
    assert check_request(req, tmp_path) is None


def test_stale_release_manifest_denied(tmp_path):
    fixtures(tmp_path)
    req = {"argv": ["powershell", "-Command", "Set-Content browser_relay/server.py updated"],
           "context_evidence": {"component_change": {
               "component": "birdeye-browser-relay", "operation": "modify_existing",
               "manifest_sha256": "0" * 64}}}
    assert check_request(req, tmp_path)["error"] == "COMPONENT_MANIFEST_MISMATCH"


def test_later_sequence_step_cannot_bypass(tmp_path):
    fixtures(tmp_path)
    req = {"commands": [
        {"argv": ["cmd", "/c", "echo", "safe"]},
        {"argv": ["powershell", "-Command", "New-Item browser_relay_copy"]}]}
    assert check_request(req, tmp_path)["error"] == "COMPONENT_PREFLIGHT_REQUIRED"


def test_missing_inventory_fails_closed(tmp_path):
    req = {"argv": ["powershell", "-Command", "New-Item browser_relay_v2"],
           "context_evidence": {"component_change": {
               "component": "birdeye-browser-relay", "operation": "create"}}}
    assert check_request(req, tmp_path / "missing")["error"] == "COMPONENT_INVENTORY_INCOMPLETE"


def test_unique_component_not_assumed_safe(tmp_path):
    fixtures(tmp_path)
    req = {"argv": ["cmd", "/c", "echo", "new"],
           "context_evidence": {"component_change": {
               "component": "unlisted-service", "operation": "create"}}}
    assert check_request(req, tmp_path)["error"] == "NEW_COMPONENT_AUTHORITY_UNVERIFIED"


def test_unrelated_and_read_only_commands_remain_compatible(tmp_path):
    fixtures(tmp_path)
    assert check_request({"argv": ["cmd", "/c", "echo", "hello"]}, tmp_path) is None
    assert check_request({"argv": ["Get-Content", "browser_relay/README.md"]}, tmp_path) is None


def test_real_dispatch_denies_before_command(tmp_path, monkeypatch):
    import mcp_server
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "CONFIG_PATH", cfg)
    monkeypatch.setattr(mcp_server, "run_command",
                        lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")))
    result = mcp_server.invoke("workspace_run", {
        "workspace": str(tmp_path),
        "argv": ["cmd", "/c", "echo", "SAFE-NO-MUTATION"],
        "intent": "create another browser relay"})
    assert result["error"] == "COMPONENT_PREFLIGHT_REQUIRED"


def test_real_dispatch_denies_known_duplicate(tmp_path, monkeypatch):
    import component_reuse
    import mcp_server
    digest = fixtures(tmp_path)
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "CONFIG_PATH", cfg)
    monkeypatch.setattr(component_reuse, "inventory", lambda _: {
        "complete": True, "errors": [], "components": {
            "birdeye-browser-relay": [{"version": "0.3.5",
                                     "manifest_sha256": digest}]}})
    monkeypatch.setattr(mcp_server, "run_command",
                        lambda *_: (_ for _ in ()).throw(AssertionError("must not execute")))
    result = mcp_server.invoke("workspace_run", {
        "workspace": str(tmp_path),
        "argv": ["cmd", "/c", "echo", "SAFE"],
        "context_evidence": {"component_change": {
            "component": "birdeye-browser-relay", "operation": "create"}}})
    assert result["error"] == "DUPLICATE_COMPONENT"


def test_declared_update_cannot_hide_obvious_new_component(tmp_path):
    digest = fixtures(tmp_path)
    req = {"argv": ["powershell", "-Command", "New-Item -ItemType Directory browser_relay_v2"],
           "context_evidence": {"component_change": {
               "component": "birdeye-browser-relay", "operation": "modify_existing",
               "manifest_sha256": digest}}}
    assert check_request(req, tmp_path)["error"] == "COMPONENT_OPERATION_MISMATCH"


def test_real_dispatch_allows_unrelated_command(tmp_path, monkeypatch):
    import mcp_server
    cfg = tmp_path / "config.json"
    cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(mcp_server, "CONFIG_PATH", cfg)
    monkeypatch.setattr(mcp_server, "run_command",
                        lambda *_: {"ok": True, "stdout": "SAFE-READ"})
    result = mcp_server.invoke("workspace_run", {
        "workspace": str(tmp_path),
        "argv": ["cmd", "/c", "echo", "SAFE-READ"]})
    assert result["ok"] is True and result["stdout"] == "SAFE-READ"
