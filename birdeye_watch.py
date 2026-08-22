from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_git(workspace: Path, *args: str) -> str:
    process = subprocess.run(
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if process.returncode != 0:
        message = process.stderr.strip() or process.stdout.strip()
        raise RuntimeError(message or f"git {' '.join(args)} failed")
    return process.stdout.strip()


def discover_workspace(start: Path) -> Path:
    current = start.expanduser().resolve()
    if current.is_file():
        current = current.parent

    try:
        root = run_git(current, "rev-parse", "--show-toplevel")
        return Path(root).resolve()
    except RuntimeError:
        pass

    marker_names = {
        ".access-browser-agent.policy.json",
        "package.json",
        "pyproject.toml",
        "Cargo.toml",
        "go.mod",
        ".project",
    }

    probe = current
    while True:
        if any((probe / name).exists() for name in marker_names):
            return probe
        if probe.parent == probe:
            break
        probe = probe.parent

    return current


def repository_identity(workspace: Path) -> dict[str, Any]:
    try:
        git_root = Path(run_git(workspace, "rev-parse", "--show-toplevel")).resolve()
        branch = run_git(workspace, "branch", "--show-current") or None
        head = run_git(workspace, "rev-parse", "HEAD")
        remote = None
        try:
            remote = run_git(workspace, "remote", "get-url", "origin") or None
        except RuntimeError:
            remote = None
        status_lines = run_git(workspace, "status", "--porcelain=v1", "-z")
        changed_paths = []
        if status_lines:
            entries = [item for item in status_lines.split("\0") if item]
            for entry in entries:
                changed_paths.append(entry[3:] if len(entry) > 3 else entry)
        diff_stat = run_git(workspace, "diff", "--stat", "--no-ext-diff")
        staged_stat = run_git(workspace, "diff", "--cached", "--stat", "--no-ext-diff")
        payload = {
            "workspaceRoot": git_root.as_posix(),
            "repository": remote,
            "branch": branch,
            "gitHead": head,
            "changedPaths": changed_paths,
            "diffStat": diff_stat or None,
            "stagedDiffStat": staged_stat or None,
        }
    except RuntimeError:
        payload = {
            "workspaceRoot": workspace.as_posix(),
            "repository": None,
            "branch": None,
            "gitHead": None,
            "changedPaths": [],
            "diffStat": None,
            "stagedDiffStat": None,
        }

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    payload["receiptSha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    payload["capturedAt"] = utc_now()
    return payload


def safe_project_id(workspace: Path) -> str:
    name = workspace.name.strip() or "workspace"
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in name)
    normalized = "-".join(part for part in normalized.split("-") if part)
    suffix = hashlib.sha256(workspace.as_posix().casefold().encode("utf-8")).hexdigest()[:10]
    return f"{normalized or 'workspace'}-{suffix}"


def load_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return fallback


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def append_history(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(receipt, ensure_ascii=False, sort_keys=True) + "\n")


def sync_once(workspace: Path, state_root: Path) -> dict[str, Any]:
    receipt = repository_identity(workspace)
    project_id = safe_project_id(workspace)
    project_root = state_root / "projects" / project_id
    latest_path = project_root / "latest.json"
    history_path = project_root / "history.jsonl"

    previous = load_json(latest_path, {})
    if previous.get("receiptSha256") == receipt["receiptSha256"]:
        return {
            "status": "unchanged",
            "projectId": project_id,
            "workspaceRoot": receipt["workspaceRoot"],
            "receiptSha256": receipt["receiptSha256"],
        }

    write_json_atomic(latest_path, receipt)
    append_history(history_path, receipt)
    return {
        "status": "updated",
        "projectId": project_id,
        "workspaceRoot": receipt["workspaceRoot"],
        "receiptSha256": receipt["receiptSha256"],
        "latest": str(latest_path),
        "history": str(history_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch the current workspace and maintain compact BirdEye receipts.")
    parser.add_argument("--workspace", default=".", help="Workspace path; defaults to the current directory.")
    parser.add_argument("--state-root", default=os.environ.get("BIRDEYE_STATE_ROOT", "state"), help="BirdEye state directory.")
    parser.add_argument("--interval", type=float, default=300.0, help="Polling interval in seconds.")
    parser.add_argument("--once", action="store_true", help="Record one receipt and exit.")
    args = parser.parse_args()

    workspace = discover_workspace(Path(args.workspace))
    state_root = Path(args.state_root).expanduser().resolve()
    interval = max(1.0, float(args.interval))

    print(json.dumps({"status": "watching", "workspace": str(workspace), "stateRoot": str(state_root), "intervalSeconds": interval}))

    while True:
        try:
            result = sync_once(workspace, state_root)
            print(json.dumps(result, ensure_ascii=False))
        except KeyboardInterrupt:
            print(json.dumps({"status": "stopped", "workspace": str(workspace)}))
            return 0
        except Exception as error:
            print(json.dumps({"status": "error", "workspace": str(workspace), "error": str(error)}), file=sys.stderr)

        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
