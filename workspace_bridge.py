from __future__ import annotations

import argparse
import fnmatch
import hashlib
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
CIRCUIT_FAILURE_THRESHOLD = 2


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

    # The active BirdEye registry uses ``roots`` with ``id`` fields. Keep
    # accepting the older ``knowledge_roots``/``name`` shape for compatibility.
    roots = config.get("roots")
    root_name_key = "id"
    if roots is None:
        roots = config.get("knowledge_roots")
        root_name_key = "name"
    if not isinstance(roots, list) or not roots:
        raise BridgeError("BirdEye config requires a non-empty roots or knowledge_roots list")

    result: list[Workspace] = []
    seen_names: set[str] = set()
    seen_paths: set[str] = set()
    for item in roots:
        if not isinstance(item, dict):
            raise BridgeError("Each root entry must be an object")
        name = _required_text(item.get(root_name_key), f"{root_name_key}").strip().lower()
        raw_path = _required_text(item.get("path"), f"roots[{name}].path")
        path = Path(raw_path).expanduser().resolve()
        # Unrelated roots may live on drives not present on this machine
        # (removable/mapped drives). They must not block execution in the
        # requested workspace; existence is enforced in resolve_workspace.
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
            if not workspace.path.is_dir():
                raise BridgeError(f"Registered workspace does not exist: {workspace.path}")
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
    intent: str | None = None
    capability: str | None = None
    context_evidence: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RunRequest":
        allowed = {"workspace", "argv", "timeout_seconds", "request_id", "task_id", "intent", "capability", "context_evidence"}
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
        intent = value.get("intent")
        if intent is not None:
            intent = _required_text(intent, "intent")
        capability = value.get("capability")
        if capability is not None:
            capability = _required_text(capability, "capability")
        context_evidence = value.get("context_evidence")
        if context_evidence is not None and not isinstance(context_evidence, dict):
            raise BridgeError("context_evidence must be an object")
        return cls(workspace=workspace, argv=tuple(raw_argv), timeout_seconds=timeout, request_id=request_id,
                   task_id=task_id, intent=intent, capability=capability,
                   context_evidence=context_evidence)


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
    intent: str | None = None
    capability: str | None = None
    context_evidence: dict[str, Any] | None = None

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "RunSequenceRequest":
        allowed = {"workspace", "commands", "stop_on_failure", "request_id", "task_id", "intent", "capability", "context_evidence"}
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
        intent = value.get("intent")
        if intent is not None:
            intent = _required_text(intent, "intent")
        capability = value.get("capability")
        if capability is not None:
            capability = _required_text(capability, "capability")
        context_evidence = value.get("context_evidence")
        if context_evidence is not None and not isinstance(context_evidence, dict):
            raise BridgeError("context_evidence must be an object")
        return cls(workspace=workspace, commands=commands, stop_on_failure=stop_on_failure, request_id=request_id,
                   task_id=task_id, intent=intent, capability=capability,
                   context_evidence=context_evidence)


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


def _load_execution_policy(config_path: Path | None) -> dict[str, Any]:
    if not config_path:
        return {}
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
        policy = config.get("execution_policy")
        allow_global = False
        extra_allowed = set()

        if isinstance(policy, str):
            if policy.lower() in {"unrestricted", "global", "unbounded", "allow_all"}:
                allow_global = True
        elif isinstance(policy, dict):
            if policy.get("mode", "").lower() in {"unrestricted", "global", "unbounded", "allow_all"}:
                allow_global = True
            raw_extra = policy.get("extra_allowed_executables", [])
            if isinstance(raw_extra, list):
                extra_allowed = {str(x).lower() for x in raw_extra}

        if config.get("allow_global_execution") is True:
            allow_global = True

        raw_extra_top = config.get("extra_allowed_executables", [])
        if isinstance(raw_extra_top, list):
            extra_allowed.update(str(x).lower() for x in raw_extra_top)

        return {
            "allow_global": allow_global,
            "extra_allowed": extra_allowed,
        }
    except Exception:
        return {}


def _command_allowed(argv: tuple[str, ...], workspace_path: Path, config_path: Path | None = None) -> tuple[bool, str]:
    if not argv:
        return False, "empty command"
    dangerous, reason = _is_dangerous_command(argv)
    if dangerous:
        return False, reason
    executable = Path(argv[0]).name.lower()

    policy_info = _load_execution_policy(config_path)
    allow_global = policy_info.get("allow_global", False)
    extra_allowed = policy_info.get("extra_allowed", set())

    if allow_global or "*" in extra_allowed:
        return True, "global execution policy"

    if executable in extra_allowed or f"{executable}.exe" in extra_allowed:
        return True, f"allowlisted executable: {executable}"

    if _is_shell_wrapper(argv):
        return False, "shell wrappers are forbidden"
    if executable == "git":
        operation = argv[1].lower() if len(argv) > 1 else ""
        allowed_ops = {"status", "diff", "log", "show", "branch", "rev-parse", "ls-files", "grep", "worktree", "fetch", "add", "commit"}
        if operation in allowed_ops:
            return True, f"git {operation}"
        # Pull is only allowed in its bounded fast-forward form so governed
        # execution can sync a workspace without merge/rebase side effects.
        if operation == "pull" and "--ff-only" in [a.lower() for a in argv]:
            return True, "git pull --ff-only"
        return False, f"unsupported git operation: {operation}"
    if executable in {"npm", "npm.cmd", "npm.exe"}:
        if len(argv) > 1 and argv[1] == "run":
            project_scripts = _load_project_scripts(workspace_path)
            if len(argv) > 2 and argv[2] in project_scripts:
                return True, "npm script"
        elif len(argv) > 1 and argv[1] == "install":
            return True, "npm install"
        elif len(argv) > 1 and argv[1] == "ci":
            return True, "npm ci"
        elif len(argv) == 2 and argv[1] == "--version":
            return True, "npm version"
        return False, f"npm command not allowlisted: {argv[1] if len(argv) > 1 else 'unknown'}"
    if executable in {"node", "node.exe", "node.cmd"}:
        if len(argv) == 2 and argv[1] == "--version":
            return True, "node version"
        if len(argv) > 1:
            script = Path(argv[1])
            if script.suffix.lower() in {".js", ".mjs", ".cjs"} and not _path_escapes_workspace(workspace_path, argv[1]):
                candidate = (workspace_path / script).resolve()
                if candidate.is_file():
                    return True, "workspace node script"
        return False, "node command requires an existing workspace script"
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


def _circuit_path(config_path: Path) -> Path:
    """Return the durable, workspace-local circuit state path."""
    return _journal_path(config_path).with_name("workspace_circuits.json")


def _intent_for(argv: tuple[str, ...], explicit: str | None = None) -> str:
    if explicit:
        return explicit.strip().lower()
    return " ".join(argv[:3]).strip().lower()


def _failure_class(result: dict[str, Any]) -> str | None:
    if result.get("timed_out"):
        return "timeout"
    exit_code = result.get("exit_code")
    if exit_code not in (None, 0):
        return "process_failure"
    if result.get("ok") is False:
        return "execution_failure"
    return None


def _load_circuits(config_path: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(_circuit_path(config_path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_circuits(config_path: Path, circuits: dict[str, dict[str, Any]]) -> None:
    path = _circuit_path(config_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(circuits, sort_keys=True, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        # Execution must not become unsafe merely because telemetry storage is unavailable.
        pass


def _authority_gate(
    config_path: Path,
    workspace: Workspace,
    argv: tuple[str, ...],
    *,
    intent: str | None = None,
    capability: str | None = None,
    context_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make the deterministic pre-execution decision for a command.

    Read-only commands need no external context. Mutations require an explicit
    capability and evidence naming the resolved workspace; BirdEye/GPT-K may
    supply that evidence, but they are not the authority.
    """
    mutation = _is_mutating_command(argv)
    resolved_intent = _intent_for(argv, intent)
    circuit_key = f"{workspace.name}:{resolved_intent}"
    circuits = _load_circuits(config_path)
    circuit = circuits.get(circuit_key, {})
    evidence_hash = hashlib.sha256(
        json.dumps(context_evidence or {}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    # New provider evidence is a legitimate reconciliation point and permits
    # a fresh bounded attempt for this intent.
    if circuit.get("tripped") and circuit.get("evidence_hash") != evidence_hash and context_evidence:
        circuits.pop(circuit_key, None)
        _save_circuits(config_path, circuits)
        circuit = {}
    if circuit.get("tripped"):
        raise BridgeError(
            f"WORKSPACE_CIRCUIT_OPEN\n\nWorkspace: {workspace.name}\n"
            f"Intent: {resolved_intent}\nFailure class: {circuit.get('failure_class', 'unknown')}\n"
            "Reason: repeated failures without new runtime evidence"
        )
    if mutation:
        if capability != "workspace.mutate":
            raise BridgeError(
                "WORKSPACE_CAPABILITY_REQUIRED\n\nMutation requires capability 'workspace.mutate' "
                "and explicit runtime approval"
            )
        if not isinstance(context_evidence, dict) or context_evidence.get("workspace") not in {workspace.name, str(workspace.path)}:
            raise BridgeError(
                "WORKSPACE_CONTEXT_REQUIRED\n\nMutation requires context_evidence.workspace matching the resolved workspace"
            )
    return {
        "authority": "lbe-runtime",
        "decision": "ALLOW",
        "workspace": workspace.name,
        "intent": resolved_intent,
        "mutation": mutation,
        "capability": capability,
        "context_evidence": bool(context_evidence),
        "circuit_key": circuit_key,
        "evidence_hash": evidence_hash,
    }


def _record_circuit_result(config_path: Path, gate: dict[str, Any], result: dict[str, Any]) -> None:
    failure_class = _failure_class(result)
    if not failure_class:
        # A successful execution is new evidence and clears only this intent.
        circuits = _load_circuits(config_path)
        if gate.get("circuit_key") in circuits:
            circuits.pop(gate["circuit_key"], None)
            _save_circuits(config_path, circuits)
        return
    circuits = _load_circuits(config_path)
    key = gate.get("circuit_key")
    previous = circuits.get(key, {})
    count = int(previous.get("count", 0)) + 1 if previous.get("failure_class") == failure_class else 1
    circuits[key] = {
        "count": count,
        "failure_class": failure_class,
        "tripped": count >= CIRCUIT_FAILURE_THRESHOLD,
        "last_failure_at": utc_now(),
        "evidence_hash": gate.get("evidence_hash"),
    }
    _save_circuits(config_path, circuits)



def _is_mutating_command(argv: tuple[str, ...]) -> bool:
    if not argv or len(argv) < 2:
        return False
    exe = Path(argv[0]).name.lower()
    verb = argv[1].lower()
    if exe in {"git", "git.exe"}:
        return verb not in {
            "status", "diff", "log", "show", "rev-parse", "ls-files",
            "grep", "remote", "config",
        }
    if exe in {"npm", "npm.cmd", "npm.exe"}:
        return verb in {"install", "ci", "publish"}
    if exe in {"python", "python.exe", "python3"}:
        if argv[1:3] == ("-m", "pip"):
            return any(a == "install" for a in argv[3:])
    return False


def _lbe_before_spawn(workspace: Workspace, argv: tuple[str, ...]) -> dict[str, Any]:
    """Enforce machine LBE authority at the final subprocess boundary."""
    try:
        from authority import bridge_guard as bg
        from authority import controller as authority_controller
    except Exception as exc:
        if _is_mutating_command(argv):
            return {
                "decision": "DENY",
                "reason": "LBE_AUTHORITY_UNAVAILABLE",
                "detail": str(exc),
                "spawned": False,
            }
        return {"decision": "NOT_REQUIRED", "reason": "read_only", "spawned": False}

    effect = bg.effect_for_argv(argv)
    if not bg.requires_authority_for_argv(argv):
        return {
            "decision": "NOT_REQUIRED",
            "reason": "read_only",
            "effect": effect,
            "spawned": False,
        }

    capability = bg.capability_for_argv(argv)
    targets = bg.targets_for_argv(argv, workspace.path)
    try:
        receipt = bg.authorize_mutation(
            capability=capability,
            targets=targets,
            workspace=workspace.name,
            operation="workspace-exec",
            effect=effect,
        )
    except bg.MutationDenied as exc:
        return {
            "decision": "DENY",
            "reason": exc.reason,
            "receipt": exc.receipt,
            "capability": capability,
            "targets": targets,
            "effect": effect,
            "spawned": False,
        }

    return {
        "decision": authority_controller.ALLOW,
        "receipt": receipt,
        "capability": capability,
        "targets": targets,
        "effect": effect,
        "spawned": False,
    }


def _execute_argv(
    workspace: Workspace,
    argv: tuple[str, ...],
    timeout_seconds: int,
    config_path: Path,
    task_id: str | None = None,
    capture_mutation: bool = False,
    history: Any = None,
) -> dict[str, Any]:
    from execution_evidence import build_execution_receipt, capture_diff_state, empty_diff_state

    started_at = utc_now()
    started = time.monotonic()
    head_before = None
    head_after = None
    head_ok = False

    authority_decision = _lbe_before_spawn(workspace, argv)
    if authority_decision.get("decision") == "DENY":
        elapsed = round(time.monotonic() - started, 3)
        result = {
            "ok": False,
            "workspace": workspace.name,
            "cwd": f"<workspace:{workspace.name}>",
            "argv": list(argv),
            "timeout_seconds": timeout_seconds,
            "started_at": started_at,
            "completed_at": utc_now(),
            "elapsed_seconds": elapsed,
            "timed_out": False,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "classification": "authority_denied",
            "spawned": False,
            "error": authority_decision.get("reason"),
            "authority": authority_decision,
        }
        _append_journal(
            config_path,
            workspace=workspace.name,
            task_id=task_id,
            argv=argv,
            exit_code=None,
            duration=elapsed,
            head_before=None,
            head_after=None,
            classification="authority_denied",
            timed_out=False,
            error=authority_decision.get("reason"),
        )
        return result

    if capture_mutation:
        head_before, head_ok = _git_head(workspace.path)

    diff_before = capture_diff_state(workspace.path)
    if history is not None and diff_before.get("is_repository"):
        history.emit("git.before_evidence",
                     diff_state_sha256=diff_before["diff_state_sha256"],
                     head=diff_before["head"],
                     changed_path_count=len(diff_before["changed_paths"]))
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
        "spawned": True,
        "authority": {**authority_decision, "spawned": True},
    }

    if capture_mutation:
        result["before"] = {"head": head_before, "head_available": head_ok}
        result["after"] = {"head": head_after, "head_available": head_ok and head_after is not None}

    try:
        diff_after = capture_diff_state(workspace.path)
        if history is not None:
            history.emit("command.completed",
                         argv=list(argv),
                         exit_code=exit_code,
                         timed_out=timed_out,
                         stdout_sha256=hashlib.sha256((stdout or "").encode("utf-8")).hexdigest(),
                         stderr_sha256=hashlib.sha256((stderr or "").encode("utf-8")).hexdigest())
            if diff_after.get("is_repository"):
                history.emit("git.after_evidence",
                             diff_state_sha256=diff_after["diff_state_sha256"],
                             head=diff_after["head"],
                             changed_path_count=len(diff_after["changed_paths"]))
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

    allowed, reason = _command_allowed(request.argv, workspace_path, config_path=config_path)
    if not allowed:
        raise BridgeError(
            f"WORKSPACE_COMMAND_BLOCKED\n\nWorkspace: {request.workspace}\n"
            f"Requested argv: {request.argv}\nPolicy rule: command-policy\n"
            f"Reason: {reason}\nSafe alternative: use a diagnostic or project-defined command"
        )

    for arg in request.argv:
        if _path_escapes_workspace(workspace_path, arg):
            raise BridgeError(f"path escapes workspace: {arg}")

    capture_mutation = _is_mutating_command(request.argv)
    gate = _authority_gate(
        config_path,
        workspace,
        request.argv,
        intent=request.intent,
        capability=request.capability,
        context_evidence=request.context_evidence,
    )

    from execution_evidence import ExecutionHistory
    history = ExecutionHistory(Path(__file__).resolve().parent / "state", workspace.name, request.argv)
    result = _execute_argv(
        workspace=workspace,
        argv=request.argv,
        timeout_seconds=request.timeout_seconds,
        config_path=config_path,
        task_id=request.task_id,
        capture_mutation=capture_mutation,
        history=history,
    )
    result["compatibility_preflight"] = gate
    result["reconciliation"] = {
        "performed": isinstance(result.get("execution_evidence"), dict),
        "source": "runtime",
        "changed": bool((result.get("execution_evidence") or {}).get("workspace_changed_by_command", False)),
    }
    _record_circuit_result(config_path, gate, result)
    evidence = result.get("execution_evidence") or {}
    if "command_hash" in evidence:
        result["execution_history"] = history.finalize(
            Path(__file__).resolve().parent / "state", evidence
        )
    return result


def run_sequence(request: RunSequenceRequest, config_path: Path) -> dict[str, Any]:
    workspace = resolve_workspace(config_path, request.workspace)
    workspace_path = workspace.path.resolve()

    results: list[dict[str, Any]] = []
    stopped_at: int | None = None
    history: Any = None

    for index, step in enumerate(request.commands, start=1):
        allowed, reason = _command_allowed(step.argv, workspace_path, config_path=config_path)
        if not allowed:
            raise BridgeError(
                f"WORKSPACE_COMMAND_BLOCKED\n\nWorkspace: {request.workspace}\n"
                f"Requested argv: {step.argv}\nPolicy rule: command-policy\n"
                f"Reason: {reason}\nSafe alternative: use a diagnostic or project-defined command"
            )

        for arg in step.argv:
            if _path_escapes_workspace(workspace_path, arg):
                raise BridgeError(f"path escapes workspace: {arg}")
        capture_mutation = _is_mutating_command(step.argv)
        gate = _authority_gate(
            config_path,
            workspace,
            step.argv,
            intent=request.intent,
            capability=request.capability,
            context_evidence=request.context_evidence,
        )

        if history is None:
            from execution_evidence import ExecutionHistory
            history = ExecutionHistory(Path(__file__).resolve().parent / "state", workspace.name, step.argv)

        step_result = _execute_argv(
            workspace=workspace,
            argv=step.argv,
            timeout_seconds=step.timeout_seconds,
            config_path=config_path,
            task_id=request.task_id,
            capture_mutation=capture_mutation,
            history=history,
        )
        step_result["index"] = index
        step_result["step_id"] = step.step_id
        step_result["compatibility_preflight"] = gate
        step_result["reconciliation"] = {
            "performed": isinstance(step_result.get("execution_evidence"), dict),
            "source": "runtime",
            "changed": bool((step_result.get("execution_evidence") or {}).get("workspace_changed_by_command", False)),
        }
        _record_circuit_result(config_path, gate, step_result)
        results.append(step_result)

        if step_result.get("exit_code", 0) != 0 or step_result.get("timed_out"):
            if request.stop_on_failure:
                stopped_at = index
                break

    status = "completed" if stopped_at is None else "failed"

    response = {
        "status": status,
        "workspace": workspace.name,
        "cwd": f"<workspace:{workspace.name}>",
        "stop_on_failure": request.stop_on_failure,
        "stopped_at": stopped_at,
        "commands": results,
    }

    if history is not None:
        last_evidence = next(
            (r.get("execution_evidence") for r in reversed(results)
             if isinstance(r.get("execution_evidence"), dict) and "command_hash" in r["execution_evidence"]),
            None,
        )
        if last_evidence:
            response["execution_history"] = history.finalize(
                Path(__file__).resolve().parent / "state", last_evidence
            )
    return response


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
