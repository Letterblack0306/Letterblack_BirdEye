"""Live receipt metadata binding must never imply acceptance."""
import json
import hashlib
import sys
from pathlib import Path

import pytest

from workspace_bridge import BridgeError, RunRequest, RunSequenceRequest, run_command, run_sequence


def _setup(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"knowledge_roots": [{"name": "receipt-test", "path": str(root), "root_class": "workspace"}], "state_dir": str(tmp_path / "state")}), encoding="utf8")
    return root, cfg


def test_single_execution_persists_task_and_requirement_without_acceptance(tmp_path):
    root, cfg = _setup(tmp_path)
    request = RunRequest.from_mapping({"workspace": str(root), "argv": [sys.executable, "-c", "print('receipt-binding')"], "task_id": "task-1", "requirement_id": "focused-test"})
    result = run_command(request, cfg)
    assert result["exit_code"] == 0
    receipt = json.loads(Path(result["execution_history"]["receipt_path"]).read_text(encoding="utf8"))
    assert receipt["task_id"] == "task-1"
    assert receipt["requirement_id"] == "focused-test"
    assert "completion_verdict" not in receipt
    digest = receipt.pop("receipt_sha256")
    assert hashlib.sha256(json.dumps(receipt, sort_keys=True, ensure_ascii=False).encode("utf8")).hexdigest() == digest
    assert receipt["execution_id"] == result["execution_history"]["execution_id"]
    assert receipt["execution_evidence_sha256"] == result["execution_evidence"]["execution_evidence_sha256"]


def test_sequence_execution_persists_binding_and_failure(tmp_path):
    root, cfg = _setup(tmp_path)
    request = RunSequenceRequest.from_mapping({"workspace": str(root), "commands": [{"argv": [sys.executable, "-c", "raise SystemExit(9)"]}], "task_id": "task-2", "requirement_id": "build"})
    result = run_sequence(request, cfg)
    receipt = json.loads(Path(result["execution_history"]["receipt_path"]).read_text(encoding="utf8"))
    assert receipt["task_id"] == "task-2"
    assert receipt["requirement_id"] == "build"
    assert receipt["exit_code"] == 9
    assert "completion_verdict" not in receipt


def test_invalid_requirement_rejected(tmp_path):
    root, cfg = _setup(tmp_path)
    with pytest.raises(BridgeError):
        RunRequest.from_mapping({"workspace": str(root), "argv": ["python", "--version"], "requirement_id": " "})
