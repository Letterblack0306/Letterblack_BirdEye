from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sqlite3
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BridgeError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BridgeError(f"Expected JSON object: {path}")
    return value


def run_command(args: list[str], cwd: Path, timeout: int = 120) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run(
        args,
        cwd=str(cwd),
        text=True,
        capture_output=True,
        timeout=timeout,
        shell=False,
        check=False,
    )
    return {
        "argv": args,
        "exitCode": completed.returncode,
        "stdout": completed.stdout[-20000:],
        "stderr": completed.stderr[-20000:],
        "elapsedSeconds": round(time.monotonic() - started, 3),
    }


def redact(value: Any, roots: list[Path]) -> Any:
    if isinstance(value, dict):
        return {k: redact(v, roots) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, roots) for v in value]
    if isinstance(value, str):
        text = value
        for root in roots:
            text = text.replace(str(root), f"<workspace:{root.name}>")
        for key in ("GITHUB_TOKEN", "BIRDEYE_GITHUB_TOKEN"):
            token = os.environ.get(key)
            if token:
                text = text.replace(token, "<redacted-token>")
        return text
    return value


class GitHubContentsClient:
    def __init__(self, repository: str, branch: str, token: str):
        self.repository = repository
        self.branch = branch
        self.token = token
        self.base = f"https://api.github.com/repos/{repository}/contents"

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        url = f"{self.base}/{urllib.parse.quote(path)}?ref={urllib.parse.quote(self.branch)}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", "Letterblack-BirdEye-Bridge/1")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            detail = exc.read().decode("utf-8", errors="replace")
            raise BridgeError(f"GitHub API {method} {path} failed: {exc.code} {detail}") from exc

    def list_dir(self, path: str) -> list[dict[str, Any]]:
        value = self._request("GET", path)
        if value is None:
            return []
        if not isinstance(value, list):
            raise BridgeError(f"Expected directory listing for {path}")
        return value

    def read_json(self, path: str) -> tuple[dict[str, Any], str] | None:
        value = self._request("GET", path)
        if value is None:
            return None
        raw = base64.b64decode(value["content"]).decode("utf-8")
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise BridgeError(f"Expected JSON object at {path}")
        return parsed, value["sha"]

    def write_json(self, path: str, value: dict[str, Any], message: str) -> None:
        existing = self._request("GET", path)
        payload = {
            "message": message,
            "branch": self.branch,
            "content": base64.b64encode(
                (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
            ).decode("ascii"),
        }
        if isinstance(existing, dict) and existing.get("sha"):
            payload["sha"] = existing["sha"]
        self._request("PUT", path, payload)


@dataclass(frozen=True)
class Workspace:
    workspace_id: str
    root: Path
    database: Path | None
    validation_profiles: dict[str, list[list[str]]]


class RequestBridge:
    MAX_FILE_STATE_PATHS = 100
    ALLOWED_OPERATIONS = {
        "workspace_status",
        "workspace_diagnosis",
        "workspace_file_state",
        "git_compare",
        "run_validation_profile",
        "refresh_index",
    }

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.machine_id = str(config["machineId"])
        self.poll_seconds = max(10, int(config.get("pollSeconds", 45)))
        token = os.environ.get(str(config.get("tokenEnv", "BIRDEYE_GITHUB_TOKEN")))
        if not token:
            raise BridgeError("Missing GitHub token environment variable")
        self.client = GitHubContentsClient(
            str(config["repository"]), str(config["branch"]), token
        )
        self.workspaces: dict[str, Workspace] = {}
        for workspace_id, item in config.get("workspaces", {}).items():
            root = Path(item["root"]).expanduser().resolve()
            database = item.get("database")
            profiles = item.get("validationProfiles", {})
            self.workspaces[workspace_id] = Workspace(
                workspace_id,
                root,
                Path(database).expanduser().resolve() if database else None,
                profiles,
            )

    def _validate_request(self, request: dict[str, Any]) -> Workspace:
        required = {"schemaVersion", "requestId", "createdAt", "expiresAt", "workspaceId", "operation"}
        missing = sorted(required - request.keys())
        if missing:
            raise BridgeError(f"Request missing fields: {', '.join(missing)}")
        if request["schemaVersion"] != 1:
            raise BridgeError("Unsupported request schemaVersion")
        operation = str(request["operation"])
        if operation not in self.ALLOWED_OPERATIONS:
            raise BridgeError(f"Operation not allowed: {operation}")
        if request.get("mutationAllowed") is True:
            raise BridgeError("Mutation requests are not supported")
        expires = datetime.fromisoformat(str(request["expiresAt"]).replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise BridgeError("Request expired")
        workspace_id = str(request["workspaceId"])
        workspace = self.workspaces.get(workspace_id)
        if workspace is None:
            raise BridgeError(f"Unknown workspaceId: {workspace_id}")
        if not workspace.root.is_dir():
            raise BridgeError(f"Workspace root unavailable: {workspace_id}")
        return workspace

    def _git_status(self, workspace: Workspace) -> dict[str, Any]:
        branch = run_command(["git", "branch", "--show-current"], workspace.root)
        head = run_command(["git", "rev-parse", "HEAD"], workspace.root)
        status = run_command(["git", "status", "--short"], workspace.root)
        upstream = run_command(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], workspace.root)
        divergence = None
        if upstream["exitCode"] == 0:
            divergence = run_command(
                ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"],
                workspace.root,
            )
        return {
            "branch": branch["stdout"].strip() if branch["exitCode"] == 0 else None,
            "head": head["stdout"].strip() if head["exitCode"] == 0 else None,
            "dirty": bool(status["stdout"].strip()),
            "statusShort": status["stdout"].splitlines(),
            "upstream": upstream["stdout"].strip() if upstream["exitCode"] == 0 else None,
            "divergence": divergence["stdout"].strip() if divergence and divergence["exitCode"] == 0 else None,
        }

    def _index_status(self, workspace: Workspace) -> dict[str, Any]:
        database = workspace.database
        if database is None or not database.exists():
            return {"available": False}
        with sqlite3.connect(database) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            result: dict[str, Any] = {"available": True, "tables": sorted(tables)}
            for candidate in ("files", "workspace_files", "indexed_files"):
                if candidate in tables:
                    result["indexedFileCount"] = connection.execute(
                        f'SELECT COUNT(*) FROM "{candidate}"'
                    ).fetchone()[0]
                    result["fileTable"] = candidate
                    break
            return result

    def _indexed_file(self, workspace: Workspace, relative_path: str) -> bool | None:
        database = workspace.database
        if database is None or not database.exists():
            return None
        normalized = relative_path.replace("\\", "/")
        with sqlite3.connect(database) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            for table in ("files", "workspace_files", "indexed_files"):
                if table not in tables:
                    continue
                columns = {
                    row[1]
                    for row in connection.execute(f'PRAGMA table_info("{table}")')
                }
                for column in ("relative_path", "path", "file_path", "filepath", "full_path"):
                    if column not in columns:
                        continue
                    row = connection.execute(
                        f'SELECT 1 FROM "{table}" WHERE REPLACE("{column}", "\\", "/") = ? LIMIT 1',
                        (normalized,),
                    ).fetchone()
                    if row is not None:
                        return True
            return False

    def _resolve_file_state_paths(self, workspace: Workspace, scope: dict[str, Any]) -> list[tuple[str, Path]]:
        requested = scope.get("files")
        if not isinstance(requested, list) or not requested:
            raise BridgeError("workspace_file_state requires scope.files")
        if len(requested) > self.MAX_FILE_STATE_PATHS:
            raise BridgeError(
                f"workspace_file_state supports at most {self.MAX_FILE_STATE_PATHS} files"
            )
        resolved: list[tuple[str, Path]] = []
        seen: set[str] = set()
        for raw in requested:
            if not isinstance(raw, str) or not raw.strip():
                raise BridgeError("workspace_file_state paths must be non-empty strings")
            candidate_input = Path(raw)
            if candidate_input.is_absolute():
                raise BridgeError("workspace_file_state paths must be workspace-relative")
            candidate = (workspace.root / candidate_input).resolve()
            try:
                relative = candidate.relative_to(workspace.root).as_posix()
            except ValueError as exc:
                raise BridgeError(f"workspace_file_state path outside workspace: {raw}") from exc
            if relative in seen:
                continue
            seen.add(relative)
            resolved.append((relative, candidate))
        return resolved

    def _file_git_status(self, workspace: Workspace, relative_path: str) -> str:
        result = run_command(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", relative_path],
            workspace.root,
        )
        if result["exitCode"] != 0:
            return "UNAVAILABLE"
        line = next((line for line in result["stdout"].splitlines() if line.strip()), "")
        return line[:2] if line else "CLEAN"

    def _workspace_file_state(self, workspace: Workspace, scope: dict[str, Any]) -> dict[str, Any]:
        observed_at = utc_now()
        files: list[dict[str, Any]] = []
        for relative, candidate in self._resolve_file_state_paths(workspace, scope):
            if not candidate.exists():
                files.append(
                    {
                        "path": relative,
                        "state": "MISSING",
                        "sha256": None,
                        "size": None,
                        "mtime": None,
                        "gitStatus": self._file_git_status(workspace, relative),
                        "indexed": self._indexed_file(workspace, relative),
                    }
                )
                continue
            if not candidate.is_file():
                raise BridgeError(f"workspace_file_state path is not a regular file: {relative}")
            digest = hashlib.sha256()
            with candidate.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            stat = candidate.stat()
            files.append(
                {
                    "path": relative,
                    "state": "PRESENT",
                    "sha256": digest.hexdigest(),
                    "size": stat.st_size,
                    "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                    "gitStatus": self._file_git_status(workspace, relative),
                    "indexed": self._indexed_file(workspace, relative),
                }
            )
        head = run_command(["git", "rev-parse", "HEAD"], workspace.root)
        return {
            "workspaceId": workspace.workspace_id,
            "sourceHead": head["stdout"].strip() if head["exitCode"] == 0 else None,
            "observedAt": observed_at,
            "files": files,
        }

    def _run_profile(self, workspace: Workspace, profile: str) -> list[dict[str, Any]]:
        commands = workspace.validation_profiles.get(profile)
        if commands is None:
            raise BridgeError(f"Unknown validation profile: {profile}")
        results = []
        for argv in commands:
            if not isinstance(argv, list) or not argv or not all(isinstance(v, str) for v in argv):
                raise BridgeError(f"Invalid local validation profile: {profile}")
            result = run_command(argv, workspace.root, timeout=300)
            results.append(result)
            if result["exitCode"] != 0:
                break
        return results

    def process(self, request: dict[str, Any]) -> dict[str, Any]:
        workspace = self._validate_request(request)
        operation = str(request["operation"])
        result: dict[str, Any] = {
            "schemaVersion": 1,
            "requestId": request["requestId"],
            "machineId": self.machine_id,
            "workspaceId": workspace.workspace_id,
            "operation": operation,
            "status": "completed",
            "completedAt": utc_now(),
            "git": self._git_status(workspace),
            "index": self._index_status(workspace),
        }
        scope = request.get("scope") if isinstance(request.get("scope"), dict) else {}
        if operation == "workspace_file_state":
            result["fileState"] = self._workspace_file_state(workspace, scope)
        if operation in {"workspace_diagnosis", "run_validation_profile"}:
            profile = str(scope.get("validationProfile", "default"))
            checks = self._run_profile(workspace, profile)
            result["validation"] = {
                "profile": profile,
                "checks": checks,
                "verdict": "PASS" if checks and all(c["exitCode"] == 0 for c in checks) else "FAIL",
            }
        result["verdict"] = self._verdict(result)
        return redact(result, [workspace.root])

    @staticmethod
    def _verdict(result: dict[str, Any]) -> str:
        validation = result.get("validation")
        if isinstance(validation, dict) and validation.get("verdict") == "FAIL":
            return "FAIL"
        git = result.get("git", {})
        if git.get("dirty"):
            return "REVIEW"
        if not result.get("index", {}).get("available"):
            return "REVIEW"
        return "PASS"

    def poll_once(self) -> int:
        prefix = "requests/pending"
        processed = 0
        for item in self.client.list_dir(prefix):
            if item.get("type") != "file" or not str(item.get("name", "")).endswith(".json"):
                continue
            request_path = str(item["path"])
            loaded = self.client.read_json(request_path)
            if loaded is None:
                continue
            request, _ = loaded
            request_id = str(request.get("requestId", Path(request_path).stem))
            response_path = f"responses/{self.machine_id}/{request_id}/result.json"
            if self.client.read_json(response_path) is not None:
                continue
            try:
                response = self.process(request)
            except Exception as exc:
                response = {
                    "schemaVersion": 1,
                    "requestId": request_id,
                    "machineId": self.machine_id,
                    "status": "failed",
                    "completedAt": utc_now(),
                    "error": str(exc),
                    "verdict": "FAIL",
                }
            self.client.write_json(
                response_path,
                response,
                f"bird-eye: respond to {request_id} from {self.machine_id}",
            )
            processed += 1
        return processed

    def run(self) -> None:
        while True:
            processed = self.poll_once()
            print(f"BIRDEYE_BRIDGE_POLL processed={processed} at={utc_now()}", flush=True)
            time.sleep(self.poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="BirdEye GitHub request bridge")
    parser.add_argument("command", choices=("once", "run"))
    parser.add_argument("--config", default="bridge.config.json")
    args = parser.parse_args()
    bridge = RequestBridge(load_json(Path(args.config)))
    if args.command == "once":
        print(json.dumps({"processed": bridge.poll_once()}, indent=2))
        return 0
    bridge.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
