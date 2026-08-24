"""Execution-evidence identities for BirdEye governed commands.

Implements the DOCUMENTED requirement alongside the existing workspace
receipt SHA:

  COMMAND_HASH              what was requested (normalized argv)
  DIFF_STATE_SHA256_BEFORE  exact local Git change state observed before
  DIFF_STATE_SHA256_AFTER   exact local Git change state observed after
  EXECUTION_EVIDENCE_SHA256 identity of this complete observed execution

Also separates PREEXISTING changed paths from COMMAND_INTRODUCED paths so a
PASS verdict with side effects is surfaced instead of hidden.

All functions are read-only against the workspace.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

_MAX_UNTRACKED_FILES = 500
_MAX_UNTRACKED_FILE_BYTES = 1_000_000


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256(text.encode("utf-8"))


def _canonical_sha256(value: Any) -> str:
    return _sha256_text(json.dumps(value, sort_keys=True, ensure_ascii=False))


def _git(root: Path, *args: str, timeout: int = 30) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            cwd=str(root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
        )
        return completed.returncode, completed.stdout
    except (subprocess.TimeoutExpired, OSError):
        return 1, ""


def is_git_repository(root: Path) -> bool:
    code, out = _git(root, "rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


def _changed_paths(root: Path) -> list[str]:
    code, out = _git(root, "status", "--porcelain")
    if code != 0:
        return []
    return [line[3:].strip().strip('"') for line in out.splitlines() if len(line) >= 4]


def capture_diff_state(root: Path) -> dict[str, Any]:
    """Deterministic canonical local-diff identity for a Git worktree."""
    if not is_git_repository(root):
        return {"is_repository": False}

    _, head = _git(root, "rev-parse", "HEAD")
    _, branch_out = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    _, unstaged = _git(root, "diff", "--no-ext-diff")
    _, staged = _git(root, "diff", "--cached", "--no-ext-diff")

    untracked: list[dict[str, str]] = []
    code, others = _git(root, "ls-files", "--others", "--exclude-standard")
    if code == 0:
        for line in others.splitlines():
            path = line.strip()
            if not path or len(untracked) >= _MAX_UNTRACKED_FILES:
                break
            file_path = root / path
            try:
                if file_path.stat().st_size > _MAX_UNTRACKED_FILE_BYTES:
                    untracked.append({"path": path, "sha256": "TOO_LARGE"})
                    continue
                untracked.append({"path": path, "sha256": _sha256(file_path.read_bytes())})
            except OSError:
                untracked.append({"path": path, "sha256": "UNREADABLE"})

    canonical = {
        "head": head.strip(),
        "unstaged_patch_sha256": _sha256_text(unstaged),
        "staged_patch_sha256": _sha256_text(staged),
        "untracked": sorted(untracked, key=lambda item: item["path"]),
    }
    return {
        "is_repository": True,
        "head": canonical["head"],
        "branch": branch_out.strip() or None,
        "changed_paths": _changed_paths(root),
        "unstaged_patch_sha256": canonical["unstaged_patch_sha256"],
        "staged_patch_sha256": canonical["staged_patch_sha256"],
        "untracked_count": len(canonical["untracked"]),
        "diff_state_sha256": _canonical_sha256(canonical),
    }


def empty_diff_state() -> dict[str, Any]:
    return {
        "is_repository": False,
        "head": None,
        "branch": None,
        "changed_paths": [],
        "diff_state_sha256": _sha256_text("non-repository"),
    }


def build_execution_receipt(
    *,
    root: Path,
    argv: Sequence[str],
    exit_code: int | None,
    timed_out: bool,
    stdout: str,
    stderr: str,
    started_at: str,
    completed_at: str,
    elapsed_seconds: float,
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Build the full before/after execution evidence receipt."""
    before_paths = set(before.get("changed_paths") or [])
    after_paths = set(after.get("changed_paths") or [])
    introduced = sorted(after_paths - before_paths)
    removed = sorted(before_paths - after_paths)

    command_hash = _canonical_sha256({"argv": list(argv)})
    stdout_sha = _sha256_text(stdout)
    stderr_sha = _sha256_text(stderr)
    changed_by_command = (
        before.get("diff_state_sha256") != after.get("diff_state_sha256")
        or bool(introduced)
        or bool(removed)
    )

    canonical_receipt = {
        "command_hash": command_hash,
        "argv": list(argv),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "stdout_sha256": stdout_sha,
        "stderr_sha256": stderr_sha,
        "started_at": started_at,
        "completed_at": completed_at,
        "elapsed_seconds": elapsed_seconds,
        "before": {k: before.get(k) for k in ("head", "branch", "diff_state_sha256")},
        "after": {k: after.get(k) for k in ("head", "branch", "diff_state_sha256")},
        "workspace_changed_by_command": changed_by_command,
        "command_introduced_paths": introduced,
        "command_removed_paths": removed,
    }

    return {
        **canonical_receipt,
        "preexisting_changed_paths": sorted(before_paths),
        "changed_paths_after": sorted(after_paths),
        "overall_evidence": (
            "REVIEW" if (exit_code == 0 and changed_by_command)
            else ("PROVEN" if exit_code == 0 else "FAILED")
        ),
        "execution_evidence_sha256": _canonical_sha256(canonical_receipt),
    }


# ---- Chronological execution history (Cline-style runtime events) ----

class ExecutionHistory:
    """Structured, ordered runtime events for one BirdEye execution.

    Events are held in memory and persisted as JSONL at finalize time:
      <state_root>/executions/<execution_id>/events.jsonl
    The persisted file's sha256 becomes event_history_sha256.
    """

    def __init__(self, state_root: Path, workspace: str, argv: Sequence[str]):
        self.execution_id = f"bird-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.workspace = workspace
        self._sequence = 0
        self._events: list[dict[str, Any]] = []
        self.emit("execution.started", argv=list(argv))

    def emit(self, event: str, **payload: Any) -> dict[str, Any]:
        self._sequence += 1
        entry = {
            "execution_id": self.execution_id,
            "sequence": self._sequence,
            "event": event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **payload,
        }
        self._events.append(entry)
        return entry

    def finalize(self, state_root: Path, receipt: dict[str, Any]) -> dict[str, Any]:
        self.emit("execution.receipt",
                  exit_code=receipt.get("exit_code"),
                  overall_evidence=receipt.get("overall_evidence"),
                  execution_evidence_sha256=receipt.get("execution_evidence_sha256"))
        out_dir = Path(state_root) / "executions" / self.execution_id
        out_dir.mkdir(parents=True, exist_ok=True)
        events_file = out_dir / "events.jsonl"
        payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in self._events)
        events_file.write_text(payload, encoding="utf-8")
        receipt_file = out_dir / "receipt.json"
        receipt_file.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
        event_history_sha256 = _sha256_text(payload)
        self.emit("history.persisted", path=str(events_file), event_history_sha256=event_history_sha256)
        return {
            "execution_id": self.execution_id,
            "events_path": str(events_file),
            "receipt_path": str(receipt_file),
            "event_count": len(self._events),
            "event_history_sha256": event_history_sha256,
        }
