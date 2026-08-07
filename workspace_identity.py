"""Read-only workspace and Git revision identity evidence for BirdEye.

This module never mutates Git state. It binds identity/status evidence to a
configured workspace root and the exact HEAD observed while the tool runs.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent import Context, GovernanceError


_GIT_TIMEOUT_SECONDS = 10


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _workspace_root(ctx: Context, workspace: str | None = None):
    roots = [root for root in ctx.roots if root.root_class == "workspace"]
    if workspace is not None:
        for root in roots:
            if root.name == workspace:
                return root
        raise GovernanceError(f"Unknown workspace root: {workspace}")
    if len(roots) == 1:
        return roots[0]
    if not roots:
        raise GovernanceError("No workspace root is configured")
    raise GovernanceError(
        "Multiple workspace roots are configured; pass the workspace root name"
    )


def _git(path: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", "-C", str(path), *args],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GIT_TIMEOUT_SECONDS,
        shell=False,
        check=False,
    )
    if check and process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip() or "git command failed"
        raise GovernanceError(message)
    return process


def _git_value(path: Path, *args: str) -> str:
    return _git(path, *args).stdout.strip()


def _git_repository(path: Path) -> bool:
    result = _git(path, "rev-parse", "--is-inside-work-tree", check=False)
    return result.returncode == 0 and result.stdout.strip().lower() == "true"


def _branch_and_head(path: Path) -> tuple[str | None, str | None, bool]:
    head_result = _git(path, "rev-parse", "HEAD", check=False)
    if head_result.returncode != 0:
        return None, None, False
    head = head_result.stdout.strip()
    branch_result = _git(path, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    detached = branch_result.returncode != 0
    branch = None if detached else branch_result.stdout.strip()
    return branch, head, detached


def _status_porcelain(path: Path) -> dict[str, Any]:
    result = _git(
        path,
        "status",
        "--porcelain=v2",
        "--branch",
        "--untracked-files=all",
    )
    staged: set[str] = set()
    unstaged: set[str] = set()
    untracked: set[str] = set()
    branch: str | None = None
    upstream: str | None = None
    ahead = 0
    behind = 0

    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("# branch.head "):
            value = line[len("# branch.head "):].strip()
            branch = None if value == "(detached)" else value
            continue
        if line.startswith("# branch.upstream "):
            upstream = line[len("# branch.upstream "):].strip() or None
            continue
        if line.startswith("# branch.ab "):
            parts = line.split()
            for part in parts[2:]:
                if part.startswith("+"):
                    ahead = int(part[1:] or "0")
                elif part.startswith("-"):
                    behind = int(part[1:] or "0")
            continue
        if line.startswith("? "):
            untracked.add(line[2:])
            continue
        if not line or line[0] not in {"1", "2", "u"}:
            continue

        parts = line.split(" ")
        if len(parts) < 2:
            continue
        xy = parts[1]
        # Porcelain v2 ordinary entries place the path after fixed metadata.
        # Splitting from the documented field count keeps spaces in paths usable.
        if line.startswith("1 "):
            path_value = line.split(" ", 8)[-1]
        elif line.startswith("2 "):
            path_value = line.split(" ", 9)[-1].split("\t", 1)[0]
        else:  # unmerged
            path_value = line.split(" ", 10)[-1]
        if len(xy) >= 1 and xy[0] != ".":
            staged.add(path_value)
        if len(xy) >= 2 and xy[1] != ".":
            unstaged.add(path_value)

    changed = sorted(staged | unstaged | untracked)
    return {
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "staged_paths": sorted(staged),
        "unstaged_paths": sorted(unstaged),
        "untracked_paths": sorted(untracked),
        "changed_paths": changed,
        "changed_path_count": len(changed),
        "dirty": bool(changed),
    }


def workspace_identity(workspace: str | None = None) -> dict[str, Any]:
    """Return workspace identity bound to the Git HEAD observed at collection."""
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
            "git": {"is_repository": False},
        }

    branch, head, detached = _branch_and_head(root_path)
    status = _status_porcelain(root_path)
    git_root = Path(_git_value(root_path, "rev-parse", "--show-toplevel")).resolve()
    git_dir = _git_value(root_path, "rev-parse", "--git-dir")
    common_dir = _git_value(root_path, "rev-parse", "--git-common-dir")
    worktree = _git_value(root_path, "rev-parse", "--show-toplevel")
    submodules = _git(root_path, "submodule", "status", "--recursive", check=False)
    submodule_lines = [line for line in submodules.stdout.splitlines() if line.strip()]

    return {
        "ok": True,
        "workspace_id": root.name,
        "workspace_root": str(root_path),
        "root_class": root.root_class,
        "observed_at": observed_at,
        "git": {
            "is_repository": True,
            "repository_root": str(git_root),
            "branch": branch,
            "head_sha": head,
            "detached_head": detached,
            "dirty": status["dirty"],
            "worktree": worktree,
            "git_dir": git_dir,
            "git_common_dir": common_dir,
            "submodules_present": bool(submodule_lines),
            "submodule_status": submodule_lines,
        },
        "evidence_binding": {
            "workspace_root": str(root_path),
            "head_sha": head,
            "observed_at": observed_at,
        },
    }


def revision_status(workspace: str | None = None) -> dict[str, Any]:
    """Return read-only working-tree/revision status bound to the observed HEAD."""
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
            "git": {"is_repository": False},
        }

    branch, head, detached = _branch_and_head(root_path)
    status = _status_porcelain(root_path)
    return {
        "ok": True,
        "workspace_id": root.name,
        "workspace_root": str(root_path),
        "observed_at": observed_at,
        "git": {
            "is_repository": True,
            "head_sha": head,
            "branch": branch or status["branch"],
            "detached_head": detached,
            "upstream_ref": status["upstream"],
            "ahead": status["ahead"],
            "behind": status["behind"],
            "staged_paths": status["staged_paths"],
            "unstaged_paths": status["unstaged_paths"],
            "untracked_paths": status["untracked_paths"],
            "changed_paths": status["changed_paths"],
            "changed_path_count": status["changed_path_count"],
            "dirty": status["dirty"],
        },
        "path_states": {
            "changed": status["changed_paths"],
            "runtime_active": "unverified",
            "validated": "unverified",
        },
        "evidence_binding": {
            "workspace_root": str(root_path),
            "head_sha": head,
            "observed_at": observed_at,
        },
    }
