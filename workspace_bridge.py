from __future__ import annotations

import argparse
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


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"{field} must be a non-empty string")
    return value.strip()


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
