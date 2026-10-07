from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from workspace_bridge import (
    BridgeError,
    RunRequest,
    RunSequenceRequest,
    RunSequenceStep,
    _command_allowed,
    _is_dangerous_command,
    _is_mutating_command,
    _path_escapes_workspace,
    _redact_secret,
    command_history,
    load_workspaces,
    resolve_workspace,
    run_command,
    run_sequence,
)
from workspace_identity import revision_status, workspace_identity


def _config(tmp_path: Path, *, extra_roots: list[dict] | None = None) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("print(1)", encoding="utf-8")
    (workspace / "pyproject.toml").write_text('[tool.poetry]\nname = "demo"\n', encoding="utf-8")
    roots = [{"name": "demo", "path": str(workspace), "root_class": "workspace"}]
    if extra_roots:
        for root in extra_roots:
            target = tmp_path / root["name"]
            target.mkdir()
            roots.append({
                "name": root["name"],
                "path": str(target),
                "root_class": root.get("root_class", "workspace"),
            })
    config = tmp_path / "config.json"
    config.write_text(json.dumps({
        "knowledge_roots": roots,
        "state_dir": str(tmp_path / "state"),
    }), encoding="utf-8")
    return config

def _fake_ctx(config_path: Path):
    ws = load_workspaces(config_path)[0]
    root = type("R", (), {"name": ws.name, "path": ws.path, "root_class": "workspace"})()
    return type("Ctx", (), {"roots": (root,)})()


def test_workspace_identity_read_only(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("agent.Context.load", lambda: _fake_ctx(config))
    result = workspace_identity("demo")
    assert result["workspace_id"] == "demo"
    assert result["root_class"] == "workspace"
    assert "git" in result


def test_revision_status_read_only(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setattr("agent.Context.load", lambda: _fake_ctx(config))
    result = revision_status("demo")
    assert result["workspace_id"] == "demo"
    assert "runtime_active" in result


def test_unknown_workspace_rejection(tmp_path):
    config = _config(tmp_path)
    with pytest.raises(BridgeError, match="Unknown workspace"):
        resolve_workspace(config, "nope")


def test_absolute_existing_directory_resolves_without_registration(tmp_path):
    config = _config(tmp_path)
    unregistered = tmp_path / "outside-registry"
    unregistered.mkdir()

    workspace = resolve_workspace(config, str(unregistered.resolve()))

    assert workspace.path == unregistered.resolve()
    assert workspace.name == str(unregistered.resolve())


def test_missing_absolute_workspace_path_is_rejected(tmp_path):
    config = _config(tmp_path)
    missing = (tmp_path / "missing-workspace").resolve()

    with pytest.raises(BridgeError, match="Workspace path does not exist"):
        resolve_workspace(config, str(missing))


def test_run_command_accepts_unregistered_absolute_workspace(tmp_path, monkeypatch):
    config = _config(tmp_path)
    unregistered = tmp_path / "runtime-project"
    unregistered.mkdir()
    captured = {}

    def fake_run(args, *, cwd, shell, **kwargs):
        captured["cwd"] = str(cwd)
        captured["shell"] = shell
        return type("CP", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

    monkeypatch.setattr("workspace_bridge.subprocess.run", fake_run)
    result = run_command(
        RunRequest(str(unregistered.resolve()), ("cmd", "/c", "echo", "ok")),
        config,
    )

    assert result["ok"] is True
    assert Path(captured["cwd"]) == unregistered.resolve()
    assert captured["shell"] is False
    assert result["authority"]["request_authority"] == "private-personal-mcp"


def test_run_request_rejects_free_form_command():
    with pytest.raises(BridgeError, match="Unsupported request fields"):
        RunRequest.from_mapping({"workspace": "demo", "command": "git status"})


def test_run_request_rejects_non_list_argv():
    with pytest.raises(BridgeError, match="argv must be a non-empty array"):
        RunRequest.from_mapping({"workspace": "demo", "argv": "git status"})


def test_run_request_rejects_non_string_argv():
    with pytest.raises(BridgeError, match="argv must be an array of strings"):
        RunRequest.from_mapping({"workspace": "demo", "argv": ["git status", 1]})


def test_run_command_uses_shell_false(tmp_path, monkeypatch):
    config = _config(tmp_path)
    workspace = resolve_workspace(config, "demo")
    captured: dict = {}

    def fake_run(args, *, cwd, shell, **kwargs):
        captured["cwd"] = str(cwd)
        captured["shell"] = shell
        return type("CP", (), {"returncode": 0, "stdout": "hi", "stderr": ""})()

    monkeypatch.setattr("workspace_bridge.subprocess.run", fake_run)
    result = run_command(RunRequest("demo", ("git", "status", "--short")), config)
    assert result["ok"] is True
    assert captured["shell"] is False
def test_diagnostic_git_allowed():
    allowed, reason = _command_allowed(("git", "status", "--short"), Path("."))
    assert allowed is True
    assert "git" in reason


def test_git_push_is_transport_allowed_and_mutating():
    allowed, reason = _command_allowed(("git", "push", "origin", "main"), Path("."))
    assert allowed is True
    assert "generic" in reason.lower()
    assert _is_mutating_command(("git", "push", "origin", "main")) is True


def test_git_add_is_mutating():
    assert _is_mutating_command(("git", "add", ".")) is True
    assert _is_mutating_command(("git", "commit", "-m", "x")) is True
    assert _is_mutating_command(("git", "status", "--short")) is False


def test_npm_install_is_mutating():
    assert _is_mutating_command(("npm.cmd", "install")) is True
    assert _is_mutating_command(("npm.cmd", "ci")) is True
    assert _is_mutating_command(("npm.cmd", "test")) is False


def test_shell_wrappers_are_transport_allowed():
    for argv in (
        ("powershell", "-Command", "Get-Process"),
        ("bash", "-c", "echo hi"),
        ("cmd", "/c", "echo hi"),
    ):
        allowed, reason = _command_allowed(argv, Path("."))
        assert allowed is True
        assert "generic" in reason.lower()


def test_dangerous_executable_is_not_transport_hardcoded():
    allowed, reason = _command_allowed(("reg", "add"), Path("."))
    assert allowed is True
    assert "generic" in reason.lower()
    dangerous, dangerous_reason = _is_dangerous_command(("reg", "add"))
    assert dangerous is True
    assert "dangerous executable" in dangerous_reason


def test_run_command_allows_absolute_argument_paths(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured = {}

    def fake_run(args, *, cwd, shell, **kwargs):
        captured["args"] = args
        return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("workspace_bridge.subprocess.run", fake_run)
    result = run_command(RunRequest("demo", ("StarDesk.exe", r"C:\\outside\\file.txt")), config)
    assert result["ok"] is True
    assert captured["args"] == ["StarDesk.exe", r"C:\\outside\\file.txt"]


def test_path_escape_detector():
    ws = Path(".")
    assert _path_escapes_workspace(ws, "C:\\\\outside") is True
    assert _path_escapes_workspace(ws, "../up") is True
    assert _path_escapes_workspace(ws, "rel/file.py") is False


def test_destructive_git_operations_blocked():
    for argv in [
        ("git", "reset", "--hard"),
        ("git", "clean", "-fd"),
        ("git", "clean", "-fdx"),
        ("git", "checkout", "--", "."),
        ("git", "restore", "."),
        ("git", "push", "--force"),
        ("git", "reflog", "expire"),
    ]:
        dangerous, reason = _is_dangerous_command(argv)
        assert dangerous is True, f"Expected {argv} to be dangerous ({reason})"
def _make_step_results(argv: tuple[str, ...]):
    if argv == ("python", "-m", "pytest"):
        return {
            "ok": False,
            "workspace": "demo",
            "cwd": "<workspace:demo>",
            "argv": list(argv),
            "exit_code": 1,
            "stdout": "",
            "stderr": "fail",
            "classification": "failed",
        }
    return {
        "ok": True,
        "workspace": "demo",
        "cwd": "<workspace:demo>",
        "argv": list(argv),
        "exit_code": 0,
        "stdout": "",
        "stderr": "",
        "classification": "success",
    }


def test_run_sequence_stops_on_failure(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured: list[tuple[str, ...]] = []

    def fake_execute(workspace, argv, timeout_seconds, config_path, task_id=None, capture_mutation=False, history=None):
        captured.append(argv)
        result = _make_step_results(argv)
        result["index"] = len(captured)
        return result

    monkeypatch.setattr("workspace_bridge._execute_argv", fake_execute)
    request = RunSequenceRequest(
        "demo",
        (
            RunSequenceStep(("git", "status", "--short")),
            RunSequenceStep(("python", "-m", "pytest")),
            RunSequenceStep(("python", "-m", "unittest")),
        ),
        stop_on_failure=True,
    )
    result = run_sequence(request, config)
    assert result["status"] == "failed"
    assert result["stopped_at"] == 2
    assert len(result["commands"]) == 2
    assert [c["argv"] for c in result["commands"]] == [
        ["git", "status", "--short"],
        ["python", "-m", "pytest"],
    ]


def test_run_sequence_allows_stop_on_failure_false(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured: list[tuple[str, ...]] = []

    def fake_execute(workspace, argv, timeout_seconds, config_path, task_id=None, capture_mutation=False, history=None):
        captured.append(argv)
        return _make_step_results(argv)

    monkeypatch.setattr("workspace_bridge._execute_argv", fake_execute)
    request = RunSequenceRequest(
        "demo",
        (
            RunSequenceStep(("git", "status", "--short")),
            RunSequenceStep(("python", "-m", "pytest")),
            RunSequenceStep(("python", "-m", "unittest")),
        ),
        stop_on_failure=False,
    )
    result = run_sequence(request, config)
    assert result["status"] == "completed"
    assert result["stopped_at"] is None
    assert len(result["commands"]) == 3
def test_run_command_timeout_captured(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def fake_run(args, *, cwd, shell, timeout, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout, output=b"partial", stderr=b"")

    monkeypatch.setattr("workspace_bridge.subprocess.run", fake_run)
    result = run_command(RunRequest("demo", ("python", "-m", "pytest"), timeout_seconds=1), config)
    assert result["ok"] is False
    assert result["timed_out"] is True
    assert result["classification"] == "timeout"


def test_command_history_persists_and_filters(tmp_path):
    config = _config(tmp_path)
    journal = config.parent / "state" / "workspace_journal.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    for workspace, argv in [("demo", ["git", "status"]), ("other", ["ls"])]:
        with journal.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "workspace": workspace,
                "argv": argv,
                "timestamp": "2026-01-01T00:00:00+00:00",
            }) + "\n")
    result = command_history(config, limit=50)
    assert result["count"] == 2
    filtered = command_history(config, limit=50, workspace="demo")
    assert filtered["count"] == 1
    assert filtered["records"][0]["workspace"] == "demo"


def test_secret_redaction():
    for value in ("my_secret_key.pem", "credentials.json", "secrets.pfx"):
        assert "REDACTED" in _redact_secret(value)
    assert _redact_secret("main.py") == "main.py"


def test_stdout_stderr_captured(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def fake_run(args, *, cwd, shell, **kwargs):
        return type("CP", (), {
            "returncode": 2,
            "stdout": "out data",
            "stderr": "err data",
        })()

    monkeypatch.setattr("workspace_bridge.subprocess.run", fake_run)
    result = run_command(RunRequest("demo", ("python", "-m", "pytest")), config)
    assert result["stdout"] == "out data"
    assert result["stderr"] == "err data"
    assert result["exit_code"] == 2
    assert result["ok"] is False


def test_mcp_tools_list_exposes_only_allowed_tools():
    from mcp_server import _TOOL_DEFINITIONS
    names = {tool["name"] for tool in _TOOL_DEFINITIONS}
    assert "workspace_command_history" in names
    assert "workspace_identity" in names
    assert "revision_status" in names
    assert "workspace_run" in names
    for forbidden in ("terminal", "shell", "powershell", "exec", "run_anything"):
        assert forbidden not in names


def test_mcp_invokes_workspace_command_history(tmp_path, monkeypatch):
    from mcp_server import invoke

    expected = {"count": 0, "records": []}

    def fake_history(config_path, *, limit=50, workspace=None):
        return expected

    monkeypatch.setattr("mcp_server.command_history", fake_history)
    result = invoke("workspace_command_history", {})
    assert result == expected


def test_stdio_error_flag_requires_explicit_false():
    from mcp_server import _mcp_result_is_error, invoke

    # command history is a successful read-only payload without an `ok` key.
    # The stdio envelope must not convert that omitted field into isError=true.
    result = invoke("workspace_command_history", {})
    assert "ok" not in result or result["ok"] is True
    assert result.get("count", 0) >= 0
    assert _mcp_result_is_error(result) is False
    assert _mcp_result_is_error({"ok": True, "count": 0}) is False
    assert _mcp_result_is_error({"ok": False, "error": "failed"}) is True
    for value in ("my_secret_key.pem", "credentials.json", "secrets.pfx"):
        assert "REDACTED" in _redact_secret(value)
    assert _redact_secret("main.py") == "main.py"


def test_stdout_stderr_captured(tmp_path, monkeypatch):
    config = _config(tmp_path)

    def fake_run(args, *, cwd, shell, **kwargs):
        return type("CP", (), {
            "returncode": 2,
            "stdout": "out data",
            "stderr": "err data",
        })()

    monkeypatch.setattr("workspace_bridge.subprocess.run", fake_run)
    result = run_command(RunRequest("demo", ("python", "-m", "pytest")), config)
    assert result["stdout"] == "out data"
    assert result["stderr"] == "err data"
    assert result["exit_code"] == 2
    assert result["ok"] is False
def test_generic_executables_are_transport_allowed():
    for argv in (
        ("curl", "https://example.com"),
        ("certutil", "-decode", "x"),
        ("rundll32", "shell32.dll", "Control_RunDLL"),
        ("python", "arbitrary_script.py"),
        ("python", "-c", "print('ok')"),
        ("npm.cmd", "exec", "something"),
        ("unknown.exe", "arg"),
        ("StarDesk.exe",),
    ):
        allowed, reason = _command_allowed(argv, Path("."))
        assert allowed is True
        assert "generic" in reason.lower()


def test_generic_execution_runs_without_secondary_capability_gate(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured = {}

    def fake_run(args, *, cwd, shell, **kwargs):
        captured["args"] = args
        captured["cwd"] = str(cwd)
        captured["shell"] = shell
        return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("workspace_bridge.subprocess.run", fake_run)
    result = run_command(RunRequest("demo", ("StarDesk.exe", "--open")), config)
    assert result["ok"] is True
    assert captured["args"] == ["StarDesk.exe", "--open"]
    assert captured["shell"] is False
    assert result["authority"]["authority"] == "birdeye-mcp-user"
    assert result["authority"]["request_authority"] == "private-personal-mcp"


def test_absolute_executable_path_runs_without_secondary_gate(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured = {}

    def fake_run(args, *, cwd, shell, **kwargs):
        captured["args"] = args
        return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("workspace_bridge.subprocess.run", fake_run)
    executable = r"C:\\Program Files\\StarDesk\\StarDesk.exe"
    result = run_command(RunRequest("demo", (executable,)), config)
    assert result["ok"] is True
    assert captured["args"][0] == executable


def test_git_push_reaches_execution_without_secondary_capability_gate(tmp_path, monkeypatch):
    config = _config(tmp_path)
    captured = {}

    def fake_run(args, *, cwd, shell, **kwargs):
        captured["args"] = args
        return type("CP", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr("workspace_bridge.subprocess.run", fake_run)
    result = run_command(RunRequest("demo", ("git", "push", "origin", "main")), config)
    assert result["ok"] is True
    assert captured["args"] == ["git", "push", "origin", "main"]
    assert result["authority"]["mutation"] is True
