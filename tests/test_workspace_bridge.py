from __future__ import annotations

import json
from pathlib import Path

import pytest

from workspace_bridge import (
    BridgeError,
    DiagnosticRequest,
    build_command,
    load_request_from_comment,
    resolve_workspace,
)


def _config(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({
            "knowledge_roots": [
                {"name": "demo", "path": str(workspace)},
            ]
        }),
        encoding="utf-8",
    )
    return config


def test_comment_request_is_typed():
    request = load_request_from_comment(
        '/birdeye {"workspace":"demo","operation":"git.status"}'
    )
    assert request.workspace == "demo"
    assert request.operation == "git.status"
    assert request.args == ()


def test_unknown_request_field_is_rejected():
    with pytest.raises(BridgeError, match="Unsupported request fields"):
        load_request_from_comment(
            '/birdeye {"workspace":"demo","operation":"git.status","command":"whoami"}'
        )


def test_workspace_must_be_registered(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(BridgeError, match="Unknown workspace"):
        resolve_workspace(config, "other")


def test_registered_workspace_resolves(tmp_path):
    config = _config(tmp_path)
    resolved = resolve_workspace(config, "demo")
    assert resolved.path == (tmp_path / "workspace").resolve()


def test_git_operations_are_fixed_and_reject_args():
    request = DiagnosticRequest("demo", "git.status")
    assert build_command(request) == ["git", "status", "--short", "--branch"]
    with pytest.raises(BridgeError, match="does not accept args"):
        build_command(DiagnosticRequest("demo", "git.status", ("--porcelain=v2",)))


def test_unknown_operation_is_rejected():
    with pytest.raises(BridgeError, match="Unsupported operation"):
        build_command(DiagnosticRequest("demo", "shell", ("whoami",)))


def test_pytest_rejects_absolute_and_parent_paths():
    with pytest.raises(BridgeError, match="Absolute paths"):
        build_command(DiagnosticRequest("demo", "pytest", (r"C:\\outside\\test_x.py",)))
    with pytest.raises(BridgeError, match="Parent traversal"):
        build_command(DiagnosticRequest("demo", "pytest", ("../outside/test_x.py",)))


def test_pytest_allows_bounded_test_targets():
    command = build_command(
        DiagnosticRequest(
            "demo",
            "pytest",
            ("-q", "tests/test_example.py::test_case", "--maxfail=1"),
        )
    )
    assert command[1:3] == ["-m", "pytest"]
    assert command[-3:] == ["-q", "tests/test_example.py::test_case", "--maxfail=1"]
