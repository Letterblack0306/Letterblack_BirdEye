from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


COMMENT_PREFIX = "/birdeye "
DEFAULT_TIMEOUT_SECONDS = 120
MAX_TIMEOUT_SECONDS = 300
MAX_OUTPUT_CHARS = 60_000


class BridgeError(RuntimeError):
    pass


@dataclass(frozen=True)
class Workspace:
    name: str
    path: Path


@dataclass(frozen=True)
class DiagnosticRequest:
    workspace: str
    operation: str
    args: tuple[str, ...] = ()
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    request_id: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "DiagnosticRequest":
        allowed = {"workspace", "operation", "args", "timeout_seconds", "request_id"}
        extra = sorted(set(value) - allowed)
        if extra:
            raise BridgeError(f"Unsupported request fields: {', '.join(extra)}")

        workspace = _required_text(value.get("workspace"), "workspace")
        operation = _required_text(value.get("operation"), "operation")

        raw_args = value.get("args", [])
        if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
            raise BridgeError("args must be an array of strings")

        timeout = int(value.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        if timeout < 1 or timeout > MAX_TIMEOUT_SECONDS:
            raise BridgeError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}")

        request_id = value.get("request_id")
        if request_id is not None:
            request_id = _required_text(request_id, "request_id")

        return cls(
            workspace=workspace,
            operation=operation,
            args=tuple(raw_args),
            timeout_seconds=timeout,
            request_id=request_id,
        )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_request_from_comment(comment: str) -> DiagnosticRequest:
    if not isinstance(comment, str) or not comment.startswith(COMMENT_PREFIX):
        raise BridgeError(f"Comment must start with {COMMENT_PREFIX!r}")
    payload = comment[len(COMMENT_PREFIX):].strip()
    try:
        value = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"Invalid request JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BridgeError("Request JSON must be an object")
    return DiagnosticRequest.from_mapping(value)


def load_request_file(path: Path) -> DiagnosticRequest:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BridgeError(f"Request file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise BridgeError(f"Invalid request JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise BridgeError("Request JSON must be an object")
    return DiagnosticRequest.from_mapping(value)


def load_workspaces(config_path: Path) -> tuple[Workspace, ...]:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BridgeError(f"BirdEye config not found: {config_path}") from exc
    except json.JSONDecodeError as exc:
        raise BridgeError(f"Invalid BirdEye config JSON: {exc}") from exc

    roots = config.get("knowledge_roots")
    if not isinstance(roots, list) or not roots:
        raise BridgeError("BirdEye config requires a non-empty knowledge_roots list")

    result: list[Workspace] = []
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    for item in roots:
        if not isinstance(item, dict):
            raise BridgeError("Each knowledge_roots entry must be an object")
        name = _required_text(item.get("name"), "knowledge_roots.name").strip().lower()
        raw_path = _required_text(item.get("path"), f"knowledge_roots[{name}].path")
        path = Path(raw_path).expanduser().resolve()
        if not path.is_dir():
            raise BridgeError(f"Registered workspace does not exist: {path}")
        path_key = str(path).casefold()
        if name in seen_names:
            raise BridgeError(f"Duplicate workspace name: {name}")
        if path_key in seen_paths:
            raise BridgeError(f"Duplicate workspace path: {path}")
        seen_names.add(name)
        seen_paths.add(path_key)
        result.append(Workspace(name=name, path=path))
    return tuple(result)


def resolve_workspace(config_path: Path, name: str) -> Workspace:
    normalized = name.strip().lower()
    for workspace in load_workspaces(config_path):
        if workspace.name == normalized:
            return workspace
    available = ", ".join(item.name for item in load_workspaces(config_path))
    raise BridgeError(f"Unknown workspace {name!r}. Registered workspaces: {available}")


def _validate_relative_target(value: str) -> None:
    if not value:
        raise BridgeError("Empty argument is not allowed")
    normalized = value.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", normalized) or normalized.startswith("/") or normalized.startswith("//"):
        raise BridgeError(f"Absolute paths are not allowed: {value}")
    if any(part == ".." for part in normalized.split("/")):
        raise BridgeError(f"Parent traversal is not allowed: {value}")


def build_command(request: DiagnosticRequest) -> list[str]:
    op = request.operation
    args = list(request.args)

    fixed: dict[str, list[str]] = {
        "git.status": ["git", "status", "--short", "--branch"],
        "git.head": ["git", "rev-parse", "HEAD"],
        "git.branch": ["git", "branch", "--show-current"],
        "git.diff-check": ["git", "diff", "--check"],
        "git.diff-stat": ["git", "diff", "--stat"],
        "git.worktree-list": ["git", "worktree", "list"],
    }
    if op in fixed:
        if args:
            raise BridgeError(f"{op} does not accept args")
        return fixed[op]

    if op == "pytest":
        allowed_flags = {"-q", "-v", "-x", "-s", "--lf", "--ff"}
        command = [sys.executable, "-m", "pytest"]
        index = 0
        while index < len(args):
            arg = args[index]
            if arg in allowed_flags or re.fullmatch(r"--maxfail=\d+", arg):
                command.append(arg)
                index += 1
                continue
            if arg in {"-k", "-m"}:
                if index + 1 >= len(args):
                    raise BridgeError(f"{arg} requires a value")
                expression = args[index + 1]
                if not expression.strip() or len(expression) > 500:
                    raise BridgeError(f"Invalid {arg} expression")
                command.extend([arg, expression])
                index += 2
                continue
            _validate_relative_target(arg.split("::", 1)[0])
            command.append(arg)
            index += 1
        return command

    raise BridgeError(
        "Unsupported operation. Allowed operations: "
        "git.status, git.head, git.branch, git.diff-check, git.diff-stat, "
        "git.worktree-list, pytest"
    )


def execute(request: DiagnosticRequest, config_path: Path) -> dict[str, Any]:
    workspace = resolve_workspace(config_path, request.workspace)
    command = build_command(request)
    request_id = request.request_id or f"bd-{uuid.uuid4()}"
    started_at = utc_now()
    started = time.monotonic()

    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    try:
        completed = subprocess.run(
            command,
            cwd=workspace.path,
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=request.timeout_seconds,
            env=env,
        )
        exit_code: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        stdout = _coerce_output(exc.stdout)
        stderr = _coerce_output(exc.stderr)
        timed_out = True

    return {
        "request_id": request_id,
        "started_at": started_at,
        "completed_at": utc_now(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "workspace": workspace.name,
        "workspace_root": str(workspace.path),
        "operation": request.operation,
        "argv": command,
        "timeout_seconds": request.timeout_seconds,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "stdout": _truncate(stdout),
        "stderr": _truncate(stderr),
        "read_only_intent": request.operation.startswith("git.") or request.operation == "pytest",
    }


def _coerce_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _truncate(value: str) -> str:
    if len(value) <= MAX_OUTPUT_CHARS:
        return value
    omitted = len(value) - MAX_OUTPUT_CHARS
    return value[:MAX_OUTPUT_CHARS] + f"\n...[truncated {omitted} chars]"




# ---------------------------------------------------------------------------
# Command execution policy and journaling
# ---------------------------------------------------------------------------

_SHELL_WRAPPERS = frozenset({"powershell", "pwsh", "cmd", "bash", "sh", "wsl"})
_DANGEROUS_EXECUTABLES = frozenset({"reg", "diskpart", "bcdedit", "format", "shutdown", "restart-computer", "net", "sc"})
_DESTRUCTIVE_GIT_OPERATIONS = frozenset({"reset", "clean", "checkout", "restore", "push"})
_DESTRUCTIVE_GIT_FLAGS = frozenset({"--hard", "-fd", "-fdx", "--force", "-f", "--source"})
_SECRET_GLOBS = ("**/.env", "**/.env.*", "**/credentials*", "**/secrets*", "**/*.p12", "**/*.pfx", "**/*.key", "**/*.pem")
_READ_DIAGNOSTIC_PREFIXES = {
    ("git",): {"status", "diff", "log", "show", "branch", "rev-parse", "ls-files", "grep", "worktree", "fetch"},
    ("python",): {"--version"},
    ("python", "-m"): {"pytest", "unittest"},
}
_ALLOWED_NPM_SCRIPTS = {"test", "run:test", "run:lint", "run:check", "run:build", "lint", "check", "build"}
_ALLOWED_PYTHON_MODULES = {"pytest", "unittest", "pip"}

def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"{field} must be a non-empty string")
    return value.strip()



@dataclass(frozen=True)
class RunRequest:
    workspace: str
    argv: tuple[str, ...]
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    request_id: str | None = None
    task_id: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RunRequest":
        allowed = {"workspace", "argv", "timeout_seconds", "request_id", "task_id"}
        extra = sorted(set(value) - allowed)
        if extra:
            raise BridgeError(f"Unsupported request fields: {', '.join(extra)}")
        workspace = _required_text(value.get("workspace"), "workspace")
        raw_argv = value.get("argv")
        if not isinstance(raw_argv, list) or not raw_argv:
            raise BridgeError("argv must be a non-empty array")
        if not all(isinstance(item, str) for item in raw_argv):
            raise BridgeError("argv must be an array of strings")
        timeout = int(value.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        if timeout < 1 or timeout > MAX_TIMEOUT_SECONDS:
            raise BridgeError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}")
        request_id = value.get("request_id")
        if request_id is not None:
            request_id = _required_text(request_id, "request_id")
        task_id = value.get("task_id")
        if task_id is not None:
            task_id = _required_text(task_id, "task_id")
        return cls(workspace=workspace, argv=tuple(raw_argv), timeout_seconds=timeout, request_id=request_id, task_id=task_id)


@dataclass(frozen=True)
class RunSequenceStep:
    argv: tuple[str, ...]
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    step_id: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RunSequenceStep":
        allowed = {"argv", "timeout_seconds", "step_id"}
        extra = sorted(set(value) - allowed)
        if extra:
            raise BridgeError(f"Unsupported step fields: {', '.join(extra)}")
        raw_argv = value.get("argv")
        if not isinstance(raw_argv, list) or not raw_argv:
            raise BridgeError("step.argv must be a non-empty array")
        if not all(isinstance(item, str) for item in raw_argv):
            raise BridgeError("step.argv must be an array of strings")
        timeout = int(value.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        if timeout < 1 or timeout > MAX_TIMEOUT_SECONDS:
            raise BridgeError(f"step.timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}")
        step_id = value.get("step_id")
        if step_id is not None:
            step_id = _required_text(step_id, "step_id")
        return cls(argv=tuple(raw_argv), timeout_seconds=timeout, step_id=step_id)


@dataclass(frozen=True)
class RunSequenceRequest:
    workspace: str
    commands: tuple[RunSequenceStep, ...]
    stop_on_failure: bool = True
    request_id: str | None = None
    task_id: str | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RunSequenceRequest":
        allowed = {"workspace", "commands", "stop_on_failure", "request_id", "task_id"}
        extra = sorted(set(value) - allowed)
        if extra:
            raise BridgeError(f"Unsupported request fields: {', '.join(extra)}")
        workspace = _required_text(value.get("workspace"), "workspace")
        raw_commands = value.get("commands")
        if not isinstance(raw_commands, list) or not raw_commands:
            raise BridgeError("commands must be a non-empty array")
        commands = tuple(RunSequenceStep.from_mapping(item) for item in raw_commands)
        stop_on_failure = bool(value.get("stop_on_failure", True))
        request_id = value.get("request_id")
        if request_id is not None:
            request_id = _required_text(request_id, "request_id")
        task_id = value.get("task_id")
        if task_id is not None:
            task_id = _required_text(task_id, "task_id")
        return cls(workspace=workspace, commands=commands, stop_on_failure=stop_on_failure, request_id=request_id, task_id=task_id)


def _is_shell_wrapper(argv: tuple[str, ...]) -> bool:
    if not argv:
        return False
    return Path(argv[0]).name.lower() in _SHELL_WRAPPERS


def _is_dangerous_command(argv: tuple[str, ...]) -> tuple[bool, str]:
    if not argv:
        return False, ""
    executable = Path(argv[0]).name.lower()
    if executable in _DANGEROUS_EXECUTABLES:
        return True, f"dangerous executable: {executable}"
    if executable == "git":
        joined = " ".join(a.lower() for a in argv[1:])
        for pattern in (
            "reset --hard",
            "clean -fd",
            "clean -fdx",
            "checkout --",
            "restore .",
            "restore --source",
            "push --force",
            "push -f",
            "branch -d",
            "reflog expire",
        ):
            if pattern in joined:
                return True, f"destructive git: {' '.join(argv)}"
    return False, ""


def _path_escapes_workspace(workspace_path: Path, value: str) -> bool:
    if not value:
        return False
    normalized = value.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", normalized) or normalized.startswith("/") or normalized.startswith("//"):
        return True
    if any(part == ".." for part in normalized.split("/")):
        return True
    try:
        resolved_ws = workspace_path.resolve()
        candidate = (resolved_ws / value).resolve()
        candidate.relative_to(resolved_ws)
    except (ValueError, OSError):
        return True
    return False


def _redact_secret(value: str) -> str:
    base = Path(value).name.lower()
    if base in {".env"} or base.endswith(".env"):
        return "***REDACTED***"
    for pattern in _SECRET_GLOBS:
        stripped = pattern.replace("**/", "").lower()
        if fnmatch.fnmatch(base, stripped):
            return "***REDACTED***"
    return value

def _git_head(workspace_path: Path) -> tuple[str | None, bool]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace_path), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None, False
    if completed.returncode != 0:
        return None, False
    return completed.stdout.strip() or None, True


def _load_project_scripts(workspace_path: Path) -> dict[str, list[str]]:
    scripts: dict[str, list[str]] = {}
    package_json = workspace_path / "package.json"
    if package_json.is_file():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            raw = data.get("scripts", {})
            if isinstance(raw, dict):
                scripts = {k: ["npm.cmd", "run", k] for k in raw.keys() if isinstance(k, str)}
        except (json.JSONDecodeError, OSError):
            pass
    return scripts


def _command_allowed(argv: tuple[str, ...], workspace_path: Path) -> tuple[bool, str]:
    if not argv:
        return False, "empty command"
    if _is_shell_wrapper(argv):
        return False, "shell wrappers are forbidden"
    dangerous, reason = _is_dangerous_command(argv)
    if dangerous:
        return False, reason
    executable = Path(argv[0]).name.lower()
    if executable == "git":
        operation = argv[1].lower() if len(argv) > 1 else ""
        allowed_ops = {"status", "diff", "log", "show", "branch", "rev-parse", "ls-files", "grep", "worktree", "fetch", "add", "commit"}
        if operation in allowed_ops:
            return True, f"git {operation}"
        return False, f"unsupported git operation: {operation}"
    if executable == "npm.cmd":
        if len(argv) > 1 and argv[1] == "run":
            if len(argv) > 2 and argv[2] in _ALLOWED_NPM_SCRIPTS:
                return True, "npm script"
        elif len(argv) > 1 and argv[1] == "install":
            return True, "npm install"
        elif len(argv) > 1 and argv[1] == "ci":
            return True, "npm ci"
        elif len(argv) == 2 and argv[1] == "--version":
            return True, "npm version"
        return False, f"npm command not allowlisted: {argv[1] if len(argv) > 1 else 'unknown'}"
    if executable in {"python", "python.exe"}:
        if len(argv) > 2 and argv[1] == "-m":
            module = argv[2].lower()
            if module in _ALLOWED_PYTHON_MODULES:
                return True, "python module"
            if module == "pip" and len(argv) > 3 and argv[3] in {"install"}:
                return True, "pip install"
        elif len(argv) == 2 and argv[1] == "--version":
            return True, "python version"
        return False, f"python command not allowlisted: {' '.join(argv[1:])}"
    project_scripts = _load_project_scripts(workspace_path)
    script_name = Path(argv[0]).stem.lower()
    if script_name in project_scripts:
        return True, "project-defined script"
    for arg in argv[1:]:
        if _path_escapes_workspace(workspace_path, arg):
            return False, f"path escapes workspace: {arg}"
    return False, f"command not allowlisted: {executable}"


def _journal_path(config_path: Path) -> Path:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        state_dir = Path(config.get("state_dir", "state"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        state_dir = Path("state")
    return state_dir / "workspace_journal.jsonl"


def _append_journal(
    config_path: Path,
    *,
    workspace: str,
    task_id: str | None,
    argv: tuple[str, ...],
    exit_code: int | None,
    duration: float,
    head_before: str | None,
    head_after: str | None,
    classification: str,
    timed_out: bool = False,
    error: str | None = None,
) -> None:
    path = _journal_path(config_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "timestamp": utc_now(),
            "workspace": workspace,
            "task_id": task_id,
            "argv": [_redact_secret(arg) for arg in argv],
            "exit_code": exit_code,
            "duration": round(duration, 3),
            "timed_out": timed_out,
            "head_before": head_before,
            "head_after": head_after,
            "classification": classification,
            "error": error,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _read_journal(config_path: Path, *, limit: int = 200) -> list[dict[str, Any]]:
    path = _journal_path(config_path)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return records[-limit:]



def _is_mutating_command(argv: tuple[str, ...]) -> bool:
    if not argv or len(argv) < 2:
        return False
    exe = Path(argv[0]).name.lower()
    if exe == "git":
        return argv[1].lower() in {"add", "commit", "fetch", "pull", "merge", "rebase"}
    if exe in {"npm", "npm.cmd"}:
        return argv[1].lower() in {"install", "ci"}
    if exe in {"python", "python.exe", "python3"}:
        if argv[1:3] == ["-m", "pip"]:
            return any(a == "install" for a in argv[3:])
    return False


def _execute_argv(
    workspace: Workspace,
    argv: tuple[str, ...],
    timeout_seconds: int,
    config_path: Path,
    task_id: str | None = None,
    capture_mutation: bool = False,
) -> dict[str, Any]:
    from execution_evidence import build_execution_receipt, capture_diff_state, empty_diff_state

    started_at = utc_now()
    started = time.monotonic()
    head_before = None
    head_after = None
    head_ok = False

    if capture_mutation:
        head_before, head_ok = _git_head(workspace.path)

    diff_before = capture_diff_state(workspace.path)
    env = os.environ.copy()
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")

    try:
        completed = subprocess.run(
            list(argv),
            cwd=str(workspace.path),
            shell=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            env=env,
        )
        exit_code: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code = None
        stdout = _coerce_output(exc.stdout)
        stderr = _coerce_output(exc.stderr)
        timed_out = True

    elapsed = round(time.monotonic() - started, 3)

    if capture_mutation and head_ok:
        head_after, _ = _git_head(workspace.path)

    classification = "diagnostic"
    if exit_code == 0:
        classification = "success"
    elif exit_code is not None:
        classification = "failed"
    elif timed_out:
        classification = "timeout"

    result = {
        "ok": exit_code == 0 and not timed_out,
        "workspace": workspace.name,
        "cwd": f"<workspace:{workspace.name}>",
        "argv": list(argv),
        "timeout_seconds": timeout_seconds,
        "started_at": started_at,
        "completed_at": utc_now(),
        "elapsed_seconds": elapsed,
        "timed_out": timed_out,
        "exit_code": exit_code,
        "stdout": _truncate(stdout),
        "stderr": _truncate(stderr),
        "classification": classification,
    }

    if capture_mutation:
        result["before"] = {"head": head_before, "head_available": head_ok}
        result["after"] = {"head": head_after, "head_available": head_ok and head_after is not None}

    try:
        diff_after = capture_diff_state(workspace.path)
        result["execution_evidence"] = build_execution_receipt(
            root=workspace.path,
            argv=argv,
            exit_code=exit_code,
            timed_out=timed_out,
            stdout=stdout or "",
            stderr=stderr or "",
            started_at=started_at,
            completed_at=result["completed_at"],
            elapsed_seconds=elapsed,
            before=diff_before if diff_before.get("is_repository") else empty_diff_state(),
            after=diff_after if diff_after.get("is_repository") else empty_diff_state(),
        )
    except (OSError, ValueError) as exc:
        result["execution_evidence"] = {"error": f"EVIDENCE_CAPTURE_FAILED: {exc}"}

    _append_journal(
        config_path,
        workspace=workspace.name,
        task_id=task_id,
        argv=argv,
        exit_code=exit_code,
        duration=elapsed,
        head_before=head_before,
        head_after=head_after,
        classification=classification,
        timed_out=timed_out,
        error=None if exit_code == 0 else f"exit code {exit_code}",
    )

    return result


def run_command(request: RunRequest, config_path: Path) -> dict[str, Any]:
    workspace = resolve_workspace(config_path, request.workspace)
    workspace_path = workspace.path.resolve()

    allowed, reason = _command_allowed(request.argv, workspace_path)
    if not allowed:
        raise BridgeError(
            f"WORKSPACE_COMMAND_BLOCKED\n\nWorkspace: {request.workspace}\n"
            f"Requested argv: {request.argv}\nPolicy rule: command-policy\n"
            f"Reason: {reason}\nSafe alternative: use a diagnostic or project-defined command"
        )

    capture_mutation = _is_mutating_command(request.argv)
    for arg in request.argv:
        if _path_escapes_workspace(workspace_path, arg):
            raise BridgeError(f"path escapes workspace: {arg}")

    return _execute_argv(
        workspace=workspace,
        argv=request.argv,
        timeout_seconds=request.timeout_seconds,
        config_path=config_path,
        task_id=request.task_id,
        capture_mutation=capture_mutation,
    )


def run_sequence(request: RunSequenceRequest, config_path: Path) -> dict[str, Any]:
    workspace = resolve_workspace(config_path, request.workspace)
    workspace_path = workspace.path.resolve()

    results: list[dict[str, Any]] = []
    stopped_at: int | None = None

    for index, step in enumerate(request.commands, start=1):
        allowed, reason = _command_allowed(step.argv, workspace_path)
        if not allowed:
            raise BridgeError(
                f"WORKSPACE_COMMAND_BLOCKED\n\nWorkspace: {request.workspace}\n"
                f"Requested argv: {step.argv}\nPolicy rule: command-policy\n"
                f"Reason: {reason}\nSafe alternative: use a diagnostic or project-defined command"
            )

        capture_mutation = _is_mutating_command(step.argv)
        for arg in step.argv:
            if _path_escapes_workspace(workspace_path, arg):
                raise BridgeError(f"path escapes workspace: {arg}")

        step_result = _execute_argv(
            workspace=workspace,
            argv=step.argv,
            timeout_seconds=step.timeout_seconds,
            config_path=config_path,
            task_id=request.task_id,
            capture_mutation=capture_mutation,
        )
        step_result["index"] = index
        step_result["step_id"] = step.step_id
        results.append(step_result)

        if step_result.get("exit_code", 0) != 0 or step_result.get("timed_out"):
            if request.stop_on_failure:
                stopped_at = index
                break

    status = "completed" if stopped_at is None else "failed"

    return {
        "status": status,
        "workspace": workspace.name,
        "cwd": f"<workspace:{workspace.name}>",
        "stop_on_failure": request.stop_on_failure,
        "stopped_at": stopped_at,
        "commands": results,
    }


def command_history(config_path: Path, *, limit: int = 50, workspace: str | None = None) -> dict[str, Any]:
    records = _read_journal(config_path, limit=limit)
    if workspace is not None:
        workspace = workspace.strip().lower()
        records = [r for r in records if r.get("workspace", "").lower() == workspace]
    return {
        "count": len(records),
        "records": records,
    }

def main() -> None:
    parser = argparse.ArgumentParser(description="BirdEye workspace-scoped diagnostic command bridge")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--comment")
    source.add_argument("--request-file", type=Path)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("BIRDEYE_CONFIG_PATH", "config.json")),
        help="BirdEye config containing registered knowledge_roots",
    )
    parser.add_argument("--result", type=Path)
    args = parser.parse_args()

    try:
        request = load_request_from_comment(args.comment) if args.comment else load_request_file(args.request_file)
        result = execute(request, args.config.expanduser().resolve())
        exit_code = 0
    except BridgeError as exc:
        result = {
            "request_id": None,
            "completed_at": utc_now(),
            "error": type(exc).__name__,
            "message": str(exc),
        }
        exit_code = 2
    except Exception as exc:  # defensive bridge boundary
        result = {
            "request_id": None,
            "completed_at": utc_now(),
            "error": type(exc).__name__,
            "message": str(exc),
        }
        exit_code = 1

    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    if args.result:
        args.result.parent.mkdir(parents=True, exist_ok=True)
        args.result.write_text(rendered, encoding="utf-8")
    print(rendered)
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
