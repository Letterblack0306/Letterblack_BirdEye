
import json
import hashlib
import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient
import control_center_api as api

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "ROOT", tmp_path)
    monkeypatch.setattr(api, "STATE", tmp_path / "state")
    (tmp_path / "state").mkdir()
    (tmp_path / "workspace").mkdir()
    payload = {"unrelated": {"keep": True}, "roots": [{"id": "example", "path": str(tmp_path / "workspace"),
               "enabled": True, "index": True, "git": False, "exclusions": [], "source_class": "preserve-me"}]}
    (tmp_path / "config.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path

def revision(store):
    return hashlib.sha256((store / "config.json").read_bytes()).hexdigest()

def test_root_update_readback_and_audit(store):
    result = api.update_root({"id": "example", "patch": {"exclusions": ["cache"], "index": False}, "revision": revision(store)})
    assert result["ok"] and result["changed"] and result["audit_saved"]
    saved = json.loads((store / "config.json").read_text())
    assert saved["roots"][0]["exclusions"] == ["cache"]
    assert saved["roots"][0]["source_class"] == "preserve-me"
    assert saved["unrelated"] == {"keep": True}
    event = json.loads((store / "state/config_changes.jsonl").read_text())
    assert event["effect_verified"] and event["sha256_after"] == revision(store)

def test_stale_revision_cannot_overwrite(store):
    before = (store / "config.json").read_bytes()
    result = api.update_root({"id": "example", "patch": {"enabled": False}, "revision": "old"})
    assert not result["ok"] and result["conflict"]
    assert (store / "config.json").read_bytes() == before

@pytest.mark.parametrize("patch", [{"enabled": "false"}, {"path": "relative"}, {"path": "Z:/missing/target"},
                                  {"exclusions": "cache"}, {"authority": "override"}, {"exclusions": [""]}])
def test_invalid_patch_is_nonmutating(store, patch):
    before = (store / "config.json").read_bytes()
    assert not api.update_root({"id": "example", "patch": patch, "revision": revision(store)})["ok"]
    assert (store / "config.json").read_bytes() == before

def test_unknown_root_is_nonmutating(store):
    before = revision(store)
    assert not api.update_root({"id": "missing", "patch": {"enabled": False}, "revision": before})["ok"]
    assert revision(store) == before

def test_cross_origin_and_malformed_request_denied(store, monkeypatch):
    monkeypatch.setattr(api, "allowed", lambda request: True)
    app = Starlette(routes=[Route("/config", api.config, methods=["GET", "POST"])])
    client = TestClient(app)
    payload = {"id": "example", "patch": {"enabled": False}, "revision": revision(store)}
    assert client.post("/config", json=payload, headers={"Origin": "https://unrelated.example"}).status_code == 403
    assert client.post("/config", content="{", headers={"Origin": "http://127.0.0.1:8766"}).status_code == 400
    data = client.get("/config").json()
    assert data["revision"] == revision(store) and data["roots"][0]["exclusions"] == []

def test_registry_add_remove_roots_sources_and_connections(store):
    (store / "other").mkdir()
    (store / "asset.txt").write_text("asset", encoding="utf-8")
    first = api.update_registry({"action": "add_root", "id": "data-source",
                                 "class": "data", "path": str(store / "other"),
                                 "revision": revision(store)})
    assert first["ok"] and first["audit_saved"]
    source = api.update_registry({"action": "add_source", "id": "asset-one",
                                  "root_id": "data-source", "path": str(store / "asset.txt"),
                                  "revision": revision(store)})
    assert source["ok"]
    missing_hostname = api.update_registry({"action": "add_connection", "id": "qa-machine",
                                             "host": "192.168.1.240", "user": "Letterblack",
                                             "identity_file": str(store / "asset.txt"),
                                             "workspace": r"C:\\BirdEye", "revision": revision(store)})
    assert not missing_hostname["ok"]
    connection = api.update_registry({"action": "add_connection", "id": "qa-machine",
                                      "host": "192.168.1.240", "expected_hostname": "LETTERBLACK",
                                      "user": "Letterblack", "port": 22,
                                      "identity_file": str(store / "asset.txt"),
                                      "workspace": r"C:\\BirdEye", "revision": revision(store)})
    assert connection["ok"]
    assert json.loads((store / "config.json").read_text())["ssh_targets"][0]["expected_hostname"] == "LETTERBLACK"
    for payload in ({"action": "remove_connection", "id": "qa-machine"},
                    {"action": "remove_source", "id": "asset-one", "root_id": "data-source"},
                    {"action": "remove_root", "id": "data-source"}):
        assert api.update_registry({**payload, "revision": revision(store)})["ok"]
    assert json.loads((store / "config.json").read_text())["unrelated"] == {"keep": True}
    assert (store / "asset.txt").exists()


def test_uploaded_file_is_exclusive_and_registers_data_root(store):
    blob = b"BirdEye upload acceptance"
    result = api.save_uploaded_file("uploaded-qa.txt", blob, revision(store))
    assert result["ok"] and result["audit_saved"] and result["file"]["size"] == len(blob)
    assert (store / "user_data" / "uploaded-qa.txt").read_bytes() == blob
    assert any(root["id"] == "user-data" for root in json.loads((store / "config.json").read_text())["roots"])
    assert not api.save_uploaded_file("uploaded-qa.txt", b"overwrite", revision(store))["ok"]
    assert not api.save_uploaded_file("../escape.txt", blob, revision(store))["ok"]
    assert not (store.parent / "escape.txt").exists()
