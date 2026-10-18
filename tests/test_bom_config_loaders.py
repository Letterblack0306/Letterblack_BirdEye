from pathlib import Path
import json

import agent
import birdeye_projection as projection
import remote_transport
import workspace_bridge
from bridge import birdeye_request_bridge
from loop_registry import LoopRegistry


def bom_json(path, payload):
    raw=b"\xef\xbb\xbf"+json.dumps(payload).encode("utf-8")
    path.write_bytes(raw)
    return raw


def test_root_registry_loads_bom_without_mutation(tmp_path):
    path=tmp_path/"config.json"
    raw=bom_json(path,{"roots":[]})
    assert agent.load_json(path)=={"roots":[]}
    assert path.read_bytes()==raw


def test_projection_registry_loads_bom(tmp_path):
    path=tmp_path/"projection.json"
    raw=bom_json(path,{"schemaVersion":1,"projects":[]})
    assert projection.Registry(path).list_projects()==[] if hasattr(projection.Registry(path),"list_projects") else projection.Registry(path)._projects==[]
    assert path.read_bytes()==raw


def test_workspace_bridge_loads_bom(tmp_path):
    path=tmp_path/"config.json"
    raw=bom_json(path,{"knowledge_roots":[{"name":"demo","path":str(tmp_path),"root_class":"workspace"}]})
    assert workspace_bridge.load_workspaces(path)[0].name=="demo"
    assert path.read_bytes()==raw


def test_request_bridge_loads_bom(tmp_path):
    path=tmp_path/"request-config.json"
    raw=bom_json(path,{"workspaces":[]})
    assert birdeye_request_bridge.load_json(path)=={"workspaces":[]}
    assert path.read_bytes()==raw


def test_loop_registry_loads_bom_without_dropping_loops(tmp_path):
    path=tmp_path/"loops.json"
    raw=bom_json(path,{"version":1,"loops":{"session-x":{"status":"waiting"}}})
    assert "session-x" in LoopRegistry(path)._read()["loops"]
    assert path.read_bytes()==raw


def test_remote_transport_loads_bom_lease(tmp_path,monkeypatch):
    path=tmp_path/"access_tunnel.json"
    raw=bom_json(path,{"open":True,"last_activity":1234})
    monkeypatch.setattr(remote_transport,"STATE_FILE",path)
    lease=remote_transport._load_lease()
    assert lease["open"] is True and lease["last_activity"]==1234.0
    assert path.read_bytes()==raw
