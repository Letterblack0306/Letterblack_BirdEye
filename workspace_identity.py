from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_root(ctx: Any, workspace: str | None) -> Any:
    if workspace is not None:
        workspace = workspace.strip()
        if not workspace:
            raise ValueError("workspace must not be empty")
    roots = [root for root in ctx.roots if root.root_class == "workspace"]
    if workspace is not None:
        for root in roots:
            if root.name == workspace:
                return root
        raise ValueError(f"Unknown workspace root: {workspace}")
    if len(roots) == 1:
        return roots[0]
    if not roots:
        raise ValueError("No workspace root is configured")
    raise ValueError("Multiple workspace roots are configured; pass the workspace root name")


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=check,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        shell=False,
    )


def _git_value(path: Path, *args: str) -> str | None:
    try:
        completed = _git(path, *args, check=False)
    except subprocess.TimeoutExpired:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value if value else None


def _git_repository(path: Path) -> bool:
    try:
        completed = _git(path, "rev-parse", "--is-inside-work-tree", check=False)
    except subprocess.TimeoutExpired:
        return False
    return completed.returncode == 0 and completed.stdout.strip() == "true"


def _branch_and_head(path: Path) -> tuple[str, str, bool]:
    head_result = _git_value(path, "rev-parse", "HEAD")
    head = head_result or ""
    detached = not head
    branch = ""
    if not detached:
        branch_result = _git_value(path, "symbolic-ref", "--quiet", "--short", "HEAD")
        branch = branch_result or ""
    return branch, head, detached


def _status_porcelain(path: Path) -> list[str]:
    try:
        completed = _git(path, "status", "--porcelain", check=False)
    except (subprocess.TimeoutExpired, OSError):
        return []
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _tracking_offset(path: Path) -> tuple[str, str]:
    upstream_ref = _git_value(path, "rev-parse", "--abbrev-ref", "@{upstream}")
    if not upstream_ref:
        return "", ""
    parts = upstream_ref.split(",")
    ahead = behind = 0
    for token in parts:
        token = token.strip()
        if token.startswith("ahead "):
            ahead = int(token.split()[1])
        elif token.startswith("behind "):
            behind = int(token.split()[1])
    return str(ahead), str(behind)


def _git_status_detail(path: Path) -> dict[str, Any]:
    lines = _status_porcelain(path)
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []
    for line in lines:
        if len(line) < 2:
            continue
        xy = line[:2]
        rest = line[3:]
        if xy.startswith("?"):
            untracked.append(rest)
            continue
        if xy[0] not in (" ", "?"):
            staged.append(rest)
        if xy[1] != " ":
            unstaged.append(rest)
    upstream_ref = _git_value(path, "rev-parse", "--abbrev-ref", "@{upstream}")
    upstream = upstream_ref or ""
    ahead, behind = "", ""
    if upstream:
        ahead, behind = _tracking_offset(path)
    return {
        "staged_paths": staged,
        "unstaged_paths": unstaged,
        "untracked_paths": untracked,
        "changed_paths": sorted(set(staged + unstaged)),
        "changed_path_count": len(set(staged + unstaged)),
        "dirty": bool(lines),
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
    }


def workspace_identity(workspace: str | None = None) -> dict[str, Any]:
    try:
        from agent import Context
    except ImportError:
        raise RuntimeError("agent module is required") from None

    ctx = Context.load()
    root = _workspace_root(ctx, workspace)
    root_path = root.path.resolve()
    observed_at = _utc_now()

    if not _git_repository(root_path):
        return {
            "ok": True,
            "workspace_id": root.name,
            "workspace_root": str(root_path),
            "root_class": root.root_class,
            "observed_at": observed_at,
            "git": {
                "is_repository": False,
            },
        }

    branch, head, detached = _branch_and_head(root_path)
    status = _git_status_detail(root_path)
    git_root = _git_value(root_path, "rev-parse", "--show-toplevel") or str(root_path)
    git_dir = _git_value(root_path, "rev-parse", "--git-dir") or ""
    git_common_dir = _git_value(root_path, "rev-parse", "--git-common-dir") or ""
    worktree = str(root_path) if git_root.lower() != str(root_path).lower() else None
    submodules_present = False
    submodule_status: list[str] = []
    try:
        completed = _git(root_path, "submodule", "status", "--recursive", check=False)
        if completed.returncode == 0 and completed.stdout.strip():
            submodules_present = True
            submodule_status = [line for line in completed.stdout.splitlines() if line.strip()]
    except subprocess.TimeoutExpired:
        pass

    return {
        "ok": True,
        "workspace_id": root.name,
        "workspace_root": str(root_path),
        "root_class": root.root_class,
        "observed_at": observed_at,
        "git": {
            "is_repository": True,
            "repository_root": git_root,
            "branch": branch,
            "head_sha": head,
            "detached_head": detached,
            "dirty": status["dirty"],
            "worktree": worktree,
            "git_dir": git_dir,
            "git_common_dir": git_common_dir,
            "submodules_present": submodules_present,
            "submodule_status": submodule_status,
            "evidence_binding": {
                "head": head,
                "observed_at": observed_at,
                "root": str(root_path),
            },
        },
    }


def revision_status(workspace: str | None = None) -> dict[str, Any]:
    try:
        from agent import Context
    except ImportError:
        raise RuntimeError("agent module is required") from None

    ctx = Context.load()
    root = _workspace_root(ctx, workspace)
    root_path = root.path.resolve()
    observed_at = _utc_now()

    if not _git_repository(root_path):
        return {
            "ok": True,
            "workspace_id": root.name,
            "workspace_root": str(root_path),
            "observed_at": observed_at,
            "git": {
                "is_repository": False,
            },
            "runtime_active": "unverified",
        }

    branch, head, detached = _branch_and_head(root_path)
    status = _git_status_detail(root_path)
    upstream = status.get("upstream", "")
    ahead = status.get("ahead", "")
    behind = status.get("behind", "")

    return {
        "ok": True,
        "workspace_id": root.name,
        "workspace_root": str(root_path),
        "observed_at": observed_at,
        "git": {
            "is_repository": True,
            "head_sha": head,
            "branch": branch,
            "detached_head": detached,
            "upstream_ref": upstream or None,
            "upstream": upstream or None,
            "ahead": ahead or None,
            "behind": behind or None,
            "staged_paths": status["staged_paths"],
            "unstaged_paths": status["unstaged_paths"],
            "untracked_paths": status["untracked_paths"],
            "changed_paths": status["changed_paths"],
            "changed_path_count": status["changed_path_count"],
            "dirty": status["dirty"],
            "path_states": {
                key: len(value) if isinstance(value, list) else value
                for key, value in status.items()
                if key.endswith("_paths")
            },
        },
        "runtime_active": "validated",
        "evidence_binding": {
            "head": head,
            "observed_at": observed_at,
            "root": str(root_path),
        },
    }

