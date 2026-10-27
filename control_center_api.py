from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

from starlette.responses import JSONResponse

import mcp_server as bird

ROOT = Path(__file__).resolve().parent
STATE = ROOT / "state"
RECEIPT_ID = re.compile(r"^bird-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}$")
NO_CACHE = {"Cache-Control": "no-store"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def allowed(request) -> bool:
    client = request.client
    token = os.environ.get("BIRDEYE_HTTP_TOKEN", "")
    return bool(client and client.host in ("127.0.0.1", "::1", "localhost")) and (
        not token or hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token)
    )


def failure(reason: str, code: int = 400):
    return JSONResponse({"ok": False, "error": reason}, status_code=code, headers=NO_CACHE)


def response(data: dict, status: int = 200):
    return JSONResponse(data, status_code=status, headers=NO_CACHE)


def tool_category(name: str) -> str:
    if name.startswith(("workspace_", "revision_", "local_")):
        return "Execution"
    if name.startswith(("birdeye_", "eyes_")):
        return "Evidence"
    if name.startswith("memory_"):
        return "Memory"
    if name.startswith(("knowledge_", "skills")):
        return "Knowledge"
    if name.startswith(("browser_", "desktop_")):
        return "Control"
    if name.startswith("llm_"):
        return "Models"
    if name.startswith("loop_"):
        return "Loops"
    return "Other"


def service_probe(url: str) -> dict:
    try:
        with urlopen(url, timeout=1.4) as conn:
            data = json.loads(conn.read(2048))
            healthy = bool(data.get("ok") is True or (conn.status == 200 and data.get("status") == "ok"))
            result = {"ok": healthy, "status": conn.status,
                      "version": data.get("browser_tool_version"), "process_id": data.get("process_id"),
                      "cdp": data.get("cdp")}
            # Browser relay process health is not proof the Chrome extension is attached.
            if data.get("service") == "birdeye-browser-relay":
                import time
                client = data.get("browser_client")
                seen = client.get("seen") if isinstance(client, dict) else None
                age = max(0.0, time.time() - seen) if isinstance(seen, (int, float)) else None
                result["client_connected"] = age is not None and age <= 30
                result["client_age_seconds"] = round(age, 1) if age is not None else None
                result["client_version"] = client.get("version") if isinstance(client, dict) else None
                if healthy and not result["client_connected"]:
                    result["client_reason"] = "Browser extension has not checked in within 30 seconds"
                    result["client_next_action"] = "Check installed Chrome extension and its polling connection to the existing relay"
            if not healthy:
                result["reason"] = "HTTP " + str(conn.status) + "; endpoint did not report healthy"
                result["next_action"] = "Inspect the service response and runtime owner"
            return result
    except (OSError, ValueError, TimeoutError) as exc:
        # Expose a bounded diagnostic, never the raw URL, token or traceback.
        result = {"ok": False, "reason": type(exc).__name__}
        if isinstance(exc, ConnectionRefusedError):
            result["next_action"] = "Check whether the existing service is running and listening on its configured port"
        elif isinstance(exc, TimeoutError):
            result["next_action"] = "Check service responsiveness, logs and current load"
        else:
            result["next_action"] = "Inspect the existing service task and local network listener"
        return result


def collect_snapshot() -> dict:
    import loop_mcp_server as loop
    import app_gateway
    tools = list(bird._TOOL_DEFINITIONS) + [t for t in loop._LOOP_TOOLS if t["name"].startswith("loop_")]
    definitions = []
    for tool in tools:
        schema = tool.get("inputSchema") or {}
        definitions.append({
            "name": tool["name"],
            "description": tool.get("description") or "",
            "category": tool_category(tool["name"]),
            "inputs": list((schema.get("properties") or {}).keys()),
            "required": schema.get("required") or [],
            "schema": schema,
        })
    root_info = bird.invoke("birdeye_roots", {})
    eyes = bird.invoke("birdeye_status", {})
    skills = bird.invoke("skills", {"operation": "status"})
    if any(x.get("ok") is False for x in (root_info, eyes, skills)):
        raise RuntimeError("BirdEye source health is unavailable")
    config = read_config()
    return {
        "ok": True,
        "host": socket.gethostname(),
        "observed_at": utc_now(),
        "transport": {"url": "http://127.0.0.1:8766/mcp", "scope": "loopback-only"},
        "tools": definitions,
        "roots": root_info,
        "eyes": eyes,
        "skills": skills,
        "clients": {"http_tool_count": len(definitions), "direct_connector_count": None,
                    "note": "Client-exposed tools depend on the connected client"},
        "browser": service_probe("http://127.0.0.1:7726/health"),
        "llm": service_probe("http://127.0.0.1:8767/api/ping"),
        "apps": [{"id": spec.id, "name": spec.name, "path": spec.path,
                  "running": app_gateway._running(spec.id)}
                 for spec in app_gateway.APPS.values()],
        "source": {"host": socket.gethostname(), "mode": "live", "path": str(ROOT)},
        "config": {"root_count": len(config.get("roots", [])), "schema_version": config.get("schema_version")},
    }


async def snapshot(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    try:
        return response(await asyncio.to_thread(collect_snapshot))
    except (ValueError, RuntimeError, OSError) as exc:
        return failure("snapshot unavailable: " + str(exc), 503)


async def skill_query(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    q = request.query_params.get("q", "").strip()
    if not 1 <= len(q) <= 160:
        return failure("query must be 1-160 characters")
    result = await asyncio.to_thread(bird.invoke, "skills", {"operation": "query", "query": q, "max_results": 16})
    return response(result, 200 if result.get("ok") else 503)


def read_config() -> dict:
    return json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))


def resolve_root(root_id: str):
    config = read_config()
    if root_id == "__birdeye__":
        return ROOT, {"id": "birdeye", "class": "workspace", "enabled": True, "path": str(ROOT)}
    for item in config.get("roots", []):
        if item.get("id") == root_id:
            return Path(item["path"]).resolve(), item
    return None, None


def describe_workspace(root_id: str) -> dict:
    path, item = resolve_root(root_id)
    if item is None:
        return {"ok": False, "error": "unknown workspace root"}
    exists = path.is_dir()
    candidate_names = [
        "AGENTS.md", "CLAUDE.md", "README.md", "CONTRIBUTING.md",
        "package.json", "pnpm-workspace.yaml", "pyproject.toml", "requirements.txt",
        "Cargo.toml", "go.mod", "pom.xml", "Gemfile", "Dockerfile", "Makefile",
        "tasks/ACTIVE_TASK.json", ".github/copilot-instructions.md", "tsconfig.json",
        "vite.config.ts", "next.config.js", "next.config.mjs",
    ]
    found = [name for name in candidate_names if (path / name).is_file()] if exists else []
    signals = []
    if "package.json" in found:
        signals.append("Node.js manifest detected")
    if "pyproject.toml" in found or "requirements.txt" in found:
        signals.append("Python manifest detected")
    if "Cargo.toml" in found:
        signals.append("Rust manifest detected")
    if "go.mod" in found:
        signals.append("Go manifest detected")
    if "AGENTS.md" in found or "CLAUDE.md" in found:
        signals.append("Workspace instructions present")
    if "tasks/ACTIVE_TASK.json" in found:
        signals.append("Workspace task authority present")
    instructions = [n for n in found if n in ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md")]
    git = {}
    if exists:
        identity = bird.invoke("workspace_identity", {"workspace": str(path)})
        git = identity.get("git") or {}
    items = []
    if exists:
        try:
            items = sorted(({"name": entry.name, "directory": entry.is_dir()}
                            for entry in path.iterdir() if entry.name not in ("node_modules", ".git", "__pycache__", ".venv")),
                           key=lambda x: (not x["directory"], x["name"].lower()))[:24]
        except OSError:
            items = []
    return {"ok": True, "id": root_id, "path": str(path), "exists": exists,
            "root_class": item.get("class", "workspace"), "enabled": item.get("enabled", True),
            "observed_at": utc_now(), "manifests": found, "instructions": instructions,
            "signals": signals, "entries": items, "git": {"is_repository": git.get("is_repository", False),
            "head_sha": git.get("head_sha"), "branch": git.get("branch"),
            "dirty": git.get("dirty")}, "evidence_source": "live filesystem and BirdEye workspace_identity"}


async def workspace(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    rid = request.query_params.get("root", "")
    result = await asyncio.to_thread(describe_workspace, rid)
    return response(result, 200 if result["ok"] else 404)


def recent_receipts(workspace_id: str = "", limit: int = 24) -> dict:
    folder = STATE / "executions"
    if not folder.exists():
        return {"ok": True, "records": [], "scanned": 0}
    try:
        directories = sorted((p for p in folder.iterdir() if p.is_dir() and RECEIPT_ID.fullmatch(p.name)),
                             key=lambda p: p.name, reverse=True)
    except OSError:
        directories = []
    records, scanned = [], 0
    for directory in directories:
        if scanned >= 2500 or len(records) >= limit:
            break
        scanned += 1
        try:
            data = json.loads((directory / "receipt.json").read_text(encoding="utf-8"))
            if workspace_id and data.get("workspace", "").casefold() != workspace_id.casefold():
                continue
            records.append({
                "execution_id": directory.name,
                "workspace": data.get("workspace", ""),
                "completed_at": data.get("completed_at"),
                "argv": data.get("argv", []),
                "exit_code": data.get("exit_code"),
                "overall_evidence": data.get("overall_evidence"),
                "receipt_sha256": data.get("receipt_sha256"),
                "execution_evidence_sha256": data.get("execution_evidence_sha256"),
                "changed": data.get("workspace_changed_by_command", False),
            })
        except (OSError, ValueError, TypeError):
            continue
    return {"ok": True, "records": records, "scanned": scanned,
            "scan_limit": 2500, "limited": scanned >= 2500,
            "observed_at": utc_now(), "source": "state/executions/*/receipt.json"}


async def history(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    rid = request.query_params.get("root", "")
    if rid and rid != "__all__":
        path, item = resolve_root(rid)
        if item is None:
            return failure("unknown workspace root")
        selected = str(path)
    else:
        selected = ""
    result = await asyncio.to_thread(recent_receipts, selected, 25)
    result["selected_workspace"] = selected
    return response(result)


async def receipt(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    execution_id = request.query_params.get("id", "")
    if not RECEIPT_ID.fullmatch(execution_id):
        return failure("invalid execution ID")
    folder = STATE / "executions" / execution_id
    try:
        saved = json.loads((folder / "receipt.json").read_text(encoding="utf-8"))
        events = [
            json.loads(line) for line in (folder / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (FileNotFoundError, OSError, ValueError):
        return failure("execution receipt not found", 404)
    return response({"ok": True, "receipt": saved, "events": events[:80],
                     "events_truncated": len(events) > 80,
                     "source": str(folder), "observed_at": utc_now()})


async def storage(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    config = await asyncio.to_thread(read_config)
    root = next((r for r in config.get("roots", []) if r.get("class") == "memory"), {})
    sources = [{"id": r.get("id"), "path": r.get("path"), "source_class": r.get("source_class"),
                "exists": Path(r.get("path", "")).exists()}
               for r in root.get("sources", [])]
    stores = [
        ("EYES workspace data", ROOT / "eye_Databa" / "eye_workspace_data_01.db", "Canonical indexed file metadata"),
        ("Workspace search projection", ROOT / "eye_Databa" / "eye_workspace_query_01.db", "Rebuildable query projection"),
        ("Skills search projection", ROOT / "eye_Databa" / "eye_skills_query_01.db", "Indexed skill metadata"),
        ("Legacy workspace DB", STATE / "workspace.db", "Compatibility query source; not retired"),
        ("Shared vector index", STATE / "vectors" / "semantic.db", "Semantic retrieval"),
        ("Memory canonical store", Path(r"C:\MCP Local\Memory\memory.db"), "Historical canonical messages"),
        ("Memory derived store", Path(r"C:\MCP Local\Memory\derived.db"), "Derived memory information"),
        ("Execution journal", STATE / "workspace_journal.jsonl", "Summarized command history"),
        ("Execution receipts", STATE / "executions", "Per-execution events and receipts"),
        ("Browser command store", STATE / "browser-commands.sqlite3", "Durable browser commands"),
        ("Loop registry", STATE / "loops.json", "Conversation routing state"),
    ]
    data = []
    for name, path, role in stores:
        try:
            present = path.exists()
            data.append({"name": name, "path": str(path), "role": role,
                         "exists": present, "bytes": path.stat().st_size if present and path.is_file() else None})
        except OSError:
            data.append({"name": name, "path": str(path), "role": role, "exists": False, "bytes": None})
    return response({"ok": True, "stores": data, "sources": sources, "observed_at": utc_now()})


async def config(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    if request.method == "GET":
        raw = await asyncio.to_thread((ROOT / "config.json").read_bytes)
        data = json.loads(raw)
        return response({"ok": True, "revision": root_revision(raw), "host": socket.gethostname(), "observed_at": utc_now(), "roots": [
            {"id": r["id"], "path": r["path"], "class": r.get("class"),
             "enabled": r.get("enabled", True), "index": r.get("index", True),
             "hash": r.get("hash"), "git": r.get("git", False), "exclusions": r.get("exclusions", [])}
            for r in data.get("roots", [])]})
    if request.headers.get("origin") not in (
        "http://127.0.0.1:8766", "http://localhost:8766",
        "http://127.0.0.1:18768", "http://localhost:18768"
    ):
        return failure("same-origin browser request required", 403)
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return failure("invalid JSON")
    if isinstance(payload, dict) and set(payload) == {"id", "patch", "revision"} and isinstance(payload["id"], str):
        result = await asyncio.to_thread(update_root, payload)
        return response(result, 200 if result["ok"] else 409 if result.get("conflict") else 400)
    if not isinstance(payload, dict) or set(payload) != {"id", "enabled"} or not isinstance(payload["id"], str) or type(payload["enabled"]) is not bool:
        return failure("expected root id and boolean enabled")
    def apply():
        path = ROOT / "config.json"
        original = path.read_bytes()
        data = json.loads(original)
        matches = [r for r in data.get("roots", []) if r.get("id") == payload["id"]]
        if len(matches) != 1:
            return {"ok": False, "error": "unknown or ambiguous root"}
        if matches[0].get("enabled", True) == payload["enabled"]:
            return {"ok": True, "changed": False, "id": payload["id"],
                    "enabled": payload["enabled"]}
        matches[0]["enabled"] = payload["enabled"]
        updated = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        if path.read_bytes() != original:
            return {"ok": False, "error": "config changed concurrently"}
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".birdeye-config-", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            f.write(updated)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        actual = next(r for r in read_config()["roots"] if r["id"] == payload["id"])["enabled"]
        if actual != payload["enabled"]:
            return {"ok": False, "error": "saved config verification failed"}
        event = {"timestamp": utc_now(), "source": "control-center-config",
                 "target": "config.json", "root_id": payload["id"],
                 "enabled_before": not payload["enabled"], "enabled_after": actual,
                 "sha256_before": hashlib.sha256(original).hexdigest(),
                 "sha256_after": hashlib.sha256(path.read_bytes()).hexdigest()}
        audit_error = None
        try:
            audit_path = STATE / "config_changes.jsonl"
            with audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            audit_error = type(exc).__name__
        return {"ok": True, "changed": True, "id": payload["id"], "enabled": actual,
                "restart_required": True, "evidence": event,
                "audit_saved": audit_error is None, "audit_error": audit_error}
    result = await asyncio.to_thread(apply)
    return response(result, 200 if result["ok"] else 409)

EDITABLE_ROOT_FIELDS = {"path", "enabled", "index", "git", "exclusions"}
_CONFIG_WRITE_LOCK = __import__("threading").Lock()


def root_revision(raw):
    return hashlib.sha256(raw).hexdigest()


def update_root(payload):
    with _CONFIG_WRITE_LOCK:
        path = ROOT / "config.json"
        original = path.read_bytes()
        revision = root_revision(original)
        if payload.get("revision") != revision:
            return {"ok": False, "error": "Configuration changed. Reload before saving.", "conflict": True}
        patch = payload.get("patch")
        if not isinstance(patch, dict) or not patch or set(patch) - EDITABLE_ROOT_FIELDS:
            return {"ok": False, "error": "Unsupported root settings"}
        for key, value in patch.items():
            if key in {"enabled", "index", "git"} and type(value) is not bool:
                return {"ok": False, "error": key + " must be boolean"}
            if key == "path" and (not isinstance(value, str) or not value.strip() or len(value) > 2048 or not Path(value).is_absolute() or not Path(value).is_dir()):
                return {"ok": False, "error": "Choose an existing absolute directory on this host"}
            if key == "exclusions" and (not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in value)):
                return {"ok": False, "error": "Exclusions must be up to 100 nonempty patterns"}
        data = json.loads(original)
        matches = [r for r in data.get("roots", []) if r.get("id") == payload.get("id")]
        if len(matches) != 1:
            return {"ok": False, "error": "Unknown or ambiguous root"}
        before = {key: matches[0].get(key) for key in patch}
        matches[0].update(patch)
        if before == patch:
            return {"ok": True, "changed": False, "revision": revision, "root": matches[0]}
        updated = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        if path.read_bytes() != original:
            return {"ok": False, "error": "Configuration changed. Reload before saving.", "conflict": True}
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".birdeye-config-", suffix=".tmp", delete=False) as f:
            tmp = Path(f.name)
            f.write(updated)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        saved = path.read_bytes()
        actual = next(r for r in json.loads(saved)["roots"] if r["id"] == payload["id"])
        verified = all(actual.get(k) == v for k, v in patch.items())
        event = {"timestamp": utc_now(), "host": socket.gethostname(), "source": "control-center-config",
                 "target": str(path), "root_id": payload["id"], "before": before, "after": patch,
                 "sha256_before": revision, "sha256_after": root_revision(saved), "effect_verified": verified}
        try:
            with (STATE / "config_changes.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            audited = True
        except OSError:
            audited = False
        return {"ok": verified, "changed": True, "revision": root_revision(saved), "root": actual,
                "restart_required": True, "audit_saved": audited, "evidence": event}


# Git history is a read-only source view; the workspace's own Git repository is authoritative.
# No fetch, checkout, reset, source modifications or global policy interpretation.
import subprocess

_GIT_SHA = re.compile(r"^[a-fA-F0-9]{40}$")
_GIT_MAX_COMMITS = 48


def _git_read(repo: Path, *argv: str, timeout: float = 8.0) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *argv],
        shell=False, text=True, encoding="utf-8", errors="replace",
        capture_output=True, timeout=timeout, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "Git query unavailable").strip()[:250])
    return completed.stdout


def git_history_for_root(root_id: str) -> dict:
    path, item = resolve_root(root_id)
    if item is None:
        return {"ok": False, "error": "unknown configured root"}
    if not path.is_dir():
        return {"ok": True, "is_repository": False, "root": root_id,
                "path": str(path), "commits": [], "reason": "Directory unavailable on this machine",
                "observed_at": utc_now()}
    try:
        repo_path = _git_read(path, "rev-parse", "--show-toplevel", timeout=4).strip()
        head = _git_read(path, "rev-parse", "HEAD", timeout=4).strip()
        branch = _git_read(path, "branch", "--show-current", timeout=4).strip()
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return {"ok": True, "is_repository": False, "root": root_id,
                "path": str(path), "commits": [],
                "reason": "No readable Git repository at the selected workspace",
                "observed_at": utc_now()}
    try:
        raw = _git_read(path, "log", "--all", "--topo-order",
                        "--decorate=short", "--max-count=" + str(_GIT_MAX_COMMITS),
                        "--pretty=format:%H%x1f%P%x1f%h%x1f%an%x1f%aI%x1f%s%x1f%D%x1e",
                        timeout=10)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": "Git history unavailable: " + str(exc)[:170]}
    commits = []
    for entry in raw.split("\x1e"):
        parts = entry.strip("\r\n ").split("\x1f", 6)
        if len(parts) != 7:
            continue
        sha, parents, short_sha, author, authored, title, refs = parts
        if not _GIT_SHA.fullmatch(sha):
            continue
        commits.append({
            "sha": sha, "short": short_sha,
            "parents": [p for p in parents.split() if _GIT_SHA.fullmatch(p)],
            "author": author, "date": authored, "subject": title,
            "refs": [r.strip() for r in refs.split(",") if r.strip()],
            "is_head": sha.lower() == head.lower(),
        })
    return {"ok": True, "is_repository": True, "root": root_id,
            "path": str(path), "repo_root": repo_path, "branch": branch or "(detached HEAD)",
            "head": head, "commits": commits, "worktree": git_worktree_status(path),
            "range": "all available local references",
            "limit": _GIT_MAX_COMMITS, "observed_at": utc_now()}


async def git_history(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    root_id = request.query_params.get("root", "__birdeye__")
    data = await asyncio.to_thread(git_history_for_root, root_id)
    return response(data, 200 if data["ok"] else 404)


def git_commit_for_root(root_id: str, sha: str) -> dict:
    if not _GIT_SHA.fullmatch(sha or ""):
        return {"ok": False, "error": "expected 40-character Git commit ID"}
    path, item = resolve_root(root_id)
    if item is None or not path.is_dir():
        return {"ok": False, "error": "unknown or inaccessible workspace"}
    try:
        commit_type = _git_read(path, "cat-file", "-t", sha, timeout=5).strip()
        if commit_type != "commit":
            return {"ok": False, "error": "requested object is not a Git commit"}
        output = _git_read(path, "show", "--no-renames", "--no-ext-diff",
                           "--format=", "--numstat", "--max-count=1", sha, timeout=12)
    except (RuntimeError, subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": "Git commit cannot be inspected: " + str(exc)[:150]}
    files = []
    for raw in output.splitlines():
        if len(files) >= 75:
            break
        parts = raw.split("\t", 2)
        if len(parts) != 3:
            continue
        added, deleted, file_path = parts
        files.append({
            "path": file_path[:320], "added": int(added) if added.isdecimal() else None,
            "deleted": int(deleted) if deleted.isdecimal() else None,
        })
    return {"ok": True, "root": root_id, "sha": sha,
            "files": files, "truncated": len(output.splitlines()) > 75,
            "observed_at": utc_now(), "source": "local Git commit tree"}


async def git_commit(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    root_id = request.query_params.get("root", "__birdeye__")
    sha = request.query_params.get("commit", "")
    result = await asyncio.to_thread(git_commit_for_root, root_id, sha)
    return response(result, 200 if result["ok"] else 400)

# Git status and remote work are observational. Local Git is the source of working
# tree truth; open issues/PRs are separate remote GitHub truth.
_GITHUB_OWNER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_GITHUB_ISSUE_LIMIT = 100


def git_worktree_status(repo: Path) -> dict:
    try:
        text = _git_read(repo, "status", "--porcelain=v1", "--untracked-files=normal", timeout=8)
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return {"available": False, "reason": "Git worktree status cannot be read"}
    status = {"available": True, "staged": 0, "unstaged": 0, "untracked": 0,
              "conflicted": 0, "dirty": False, "files": [], "file_list_limited": False,
              "ahead": None, "behind": None, "upstream": None}
    for line in text.splitlines():
        if len(line) < 4:
            continue
        flags = line[:2]
        if flags == "??":
            status["untracked"] += 1
        elif "U" in flags or flags in ("AA", "DD"):
            status["conflicted"] += 1
        else:
            if flags[0] != " ":
                status["staged"] += 1
            if flags[1] != " ":
                status["unstaged"] += 1
        if len(status["files"]) < 20:
            status["files"].append({"status": flags, "path": line[3:][:280]})
        else:
            status["file_list_limited"] = True
    status["dirty"] = any(status[k] for k in ("staged", "unstaged", "untracked", "conflicted"))
    try:
        upstream = _git_read(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name",
                             "@{upstream}", timeout=4).strip()
        counts = _git_read(repo, "rev-list", "--left-right", "--count",
                            "HEAD...@{upstream}", timeout=6).strip().split()
        if len(counts) == 2:
            status.update({"upstream": upstream, "ahead": int(counts[0]),
                           "behind": int(counts[1])})
    except (RuntimeError, subprocess.TimeoutExpired, OSError, ValueError):
        pass  # No upstream (or unable to compare) must not be reported as 0/0.
    return status


def github_repository_for_root(root_id: str) -> dict:
    path, info = resolve_root(root_id)
    if info is None:
        return {"ok": False, "reason": "Unknown configured workspace root"}
    if not path.is_dir():
        return {"ok": False, "reason": "Workspace directory is unavailable"}
    try:
        remote = _git_read(path, "remote", "get-url", "origin", timeout=5).strip()
    except (RuntimeError, subprocess.TimeoutExpired, OSError):
        return {"ok": False, "reason": "No readable origin remote for this workspace"}
    # Only explicitly recognized GitHub remote formats are used for API routing.
    prefix = None
    for candidate in ("https://github.com/", "git@github.com:",
                      "ssh://git@github.com/"):
        if remote.startswith(candidate):
            prefix = candidate
            break
    if prefix is None:
        return {"ok": False, "reason": "Origin is not a supported GitHub remote"}
    name = remote[len(prefix):]
    if name.endswith(".git"):
        name = name[:-4]
    parts = name.split("/")
    if len(parts) != 2 or not _GITHUB_OWNER_RE.fullmatch(parts[0]) or not _GITHUB_REPO_RE.fullmatch(parts[1]) or parts[1] in (".", ".."):
        return {"ok": False, "reason": "GitHub repository identity could not be validated"}
    return {"ok": True, "repository": parts[0] + "/" + parts[1],
            "url": "https://github.com/" + parts[0] + "/" + parts[1]}


def _github_get(repo: str, kind: str) -> list:
    # gh uses local credentials, no token is passed through the UI or returned.
    endpoint = "repos/" + repo + "/" + kind + "?state=open&per_page=" + str(_GITHUB_ISSUE_LIMIT)
    result = subprocess.run(["gh", "api", "-X", "GET", endpoint],
                            shell=False, capture_output=True, text=True, timeout=16,
                            encoding="utf-8", errors="replace", check=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise RuntimeError("GitHub API not available (authorization, connectivity, or repository access)")
    payload = json.loads(result.stdout)
    if not isinstance(payload, list):
        raise ValueError("Unexpected GitHub API response shape")
    return payload


def github_work_for_root(root_id: str) -> dict:
    remote = github_repository_for_root(root_id)
    base = {"ok": True, "available": False, "repository": remote.get("repository"),
            "repository_url": remote.get("url"), "root": root_id,
            "observed_at": utc_now(), "source": "GitHub via configured origin remote",
            "issues": [], "pull_requests": [],
            "issues_status": "unavailable", "pull_requests_status": "unavailable",
            "issues_limited": False, "pull_requests_limited": False}
    if not remote["ok"]:
        base["reason"] = remote["reason"]
        return base
    for kind, field, flag, status_key in (
            ("issues", "issues", "issues_limited", "issues_status"),
            ("pulls", "pull_requests", "pull_requests_limited", "pull_requests_status")):
        try:
            fetched = _github_get(remote["repository"], kind)
        except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired,
                OSError, ValueError, json.JSONDecodeError):
            base[status_key] = "unavailable"
            continue
        records = []
        for item in fetched:
            if not isinstance(item, dict) or (kind == "issues" and "pull_request" in item):
                continue
            number = item.get("number")
            if not isinstance(number, int):
                continue
            records.append({
                "number": number, "title": str(item.get("title") or "")[:300],
                "url": remote["url"] + ("/issues/" if kind == "issues" else "/pull/") + str(number),
                "updated_at": item.get("updated_at"),
                "labels": [str(label.get("name"))[:65] for label in item.get("labels", [])
                           if isinstance(label, dict) and label.get("name")][:8],
                "draft": bool(item.get("draft", False)) if kind == "pulls" else None,
                "head": str((item.get("head") or {}).get("ref") or "")[:120]
                        if kind == "pulls" else None,
                "base": str((item.get("base") or {}).get("ref") or "")[:120]
                        if kind == "pulls" else None,
            })
        base[field] = records
        base[flag] = len(fetched) >= _GITHUB_ISSUE_LIMIT
        base[status_key] = "live"
    base["available"] = base["issues_status"] == "live" and base["pull_requests_status"] == "live"
    if not base["available"]:
        base["reason"] = "At least one GitHub API query is unavailable. Unavailable is not zero."
    return base


async def github_work(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    root_id = request.query_params.get("root", "__birdeye__")
    if resolve_root(root_id)[1] is None:
        return failure("unknown configured root", 404)
    return response(await asyncio.to_thread(github_work_for_root, root_id))

REGISTRY_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


def _valid_registry_path(value, mode="either"):
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        return None
    candidate = Path(value.strip())
    if not candidate.is_absolute():
        return None
    try:
        path = candidate.resolve(strict=True)
    except (OSError, ValueError, RuntimeError):
        return None
    if mode == "directory" and not path.is_dir():
        return None
    if mode == "file" and not path.is_file():
        return None
    if mode == "either" and not (path.is_dir() or path.is_file()):
        return None
    return str(path)


def _save_registry_change(raw: bytes, data: dict, change: dict) -> dict:
    path = ROOT / "config.json"
    if path.read_bytes() != raw:
        return {"ok": False, "error": "Configuration changed. Reload before saving.", "conflict": True}
    updated = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".birdeye-config-", suffix=".tmp", delete=False) as f:
        temp = Path(f.name)
        f.write(updated)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    fresh = path.read_bytes()
    verified = fresh == updated
    event = {
        "timestamp": utc_now(), "source": "control-center-registry",
        "action": change["action"], "id": change["id"],
        "sha256_before": root_revision(raw), "sha256_after": root_revision(fresh),
        "effect_verified": verified,
    }
    audit_saved = False
    try:
        with (STATE / "config_changes.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        audit_saved = True
    except OSError:
        pass
    return {"ok": verified, "changed": True, "revision": root_revision(fresh),
            "restart_required": True, "audit_saved": audit_saved, "evidence": event}


def update_registry(payload: dict) -> dict:
    with _CONFIG_WRITE_LOCK:
        original = (ROOT / "config.json").read_bytes()
        if payload.get("revision") != root_revision(original):
            return {"ok": False, "error": "Configuration changed. Reload before saving.", "conflict": True}
        data = json.loads(original)
        action = payload.get("action")
        rid = payload.get("id")
        if not isinstance(rid, str) or not REGISTRY_ID.fullmatch(rid):
            return {"ok": False, "error": "ID must be 2–64 lowercase letters, numbers, hyphens or underscores"}
        roots = data.setdefault("roots", [])
        if action == "add_root":
            if any(r.get("id") == rid for r in roots) or rid == "birdeye":
                return {"ok": False, "error": "Root ID already registered"}
            path = _valid_registry_path(payload.get("path"), "directory")
            if not path:
                return {"ok": False, "error": "Enter an existing absolute directory on this machine"}
            if any(Path(r.get("path", "")).resolve() == Path(path) for r in roots):
                return {"ok": False, "error": "Directory is already registered"}
            cls = payload.get("class")
            if cls not in ("workspace", "knowledge", "memory", "data"):
                return {"ok": False, "error": "Unsupported root category"}
            roots.append({"id": rid, "path": path, "class": cls, "enabled": True,
                          "index": True, "hash": "sha256", "git": cls == "workspace"})
        elif action == "remove_root":
            matches = [r for r in roots if r.get("id") == rid]
            if len(matches) != 1:
                return {"ok": False, "error": "Root not found"}
            if matches[0].get("sources"):
                return {"ok": False, "error": "Remove registered sources before removing root"}
            roots.remove(matches[0])
        elif action in ("add_source", "remove_source"):
            parent = payload.get("root_id")
            root = next((r for r in roots if r.get("id") == parent), None)
            if root is None or root.get("class") not in ("memory", "knowledge", "data"):
                return {"ok": False, "error": "Select an existing memory, knowledge or data root"}
            sources = root.setdefault("sources", [])
            if action == "add_source":
                if any(x.get("id") == rid for x in sources):
                    return {"ok": False, "error": "Source ID already registered"}
                path = _valid_registry_path(payload.get("path"))
                if not path:
                    return {"ok": False, "error": "Enter an existing file or directory path on this machine"}
                if any(Path(x.get("path", "")).resolve() == Path(path) for x in sources):
                    return {"ok": False, "error": "Source path already registered"}
                sources.append({"id": rid, "path": path, "source_class": "user-path"})
            else:
                source = next((x for x in sources if x.get("id") == rid), None)
                if source is None:
                    return {"ok": False, "error": "Source not found"}
                sources.remove(source)
        elif action in ("add_connection", "remove_connection"):
            targets = data.setdefault("ssh_targets", [])
            if action == "add_connection":
                if any(t.get("id") == rid for t in targets):
                    return {"ok": False, "error": "Connection ID already registered"}
                host, user = payload.get("host"), payload.get("user")
                expected_hostname = payload.get("expected_hostname")
                if not isinstance(expected_hostname, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", expected_hostname):
                    return {"ok": False, "error": "Provide the exact expected SSH machine hostname for identity verification"}
                port = payload.get("port", 22)
                identity = _valid_registry_path(payload.get("identity_file"), "file")
                if not isinstance(host, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,252}", host):
                    return {"ok": False, "error": "Invalid SSH host or IP"}
                if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,63}", user):
                    return {"ok": False, "error": "Invalid SSH username"}
                if type(port) is not int or not 1 <= port <= 65535:
                    return {"ok": False, "error": "SSH port must be between 1 and 65535"}
                if not identity:
                    return {"ok": False, "error": "Select an existing SSH identity file on this machine"}
                directory = payload.get("workspace")
                if not isinstance(directory, str) or not directory.strip() or len(directory) > 2048 or not Path(directory).is_absolute():
                    return {"ok": False, "error": "Provide an absolute workspace path on the remote Windows machine"}
                targets.append({"id": rid, "host": host, "port": port, "user": user,
                                "expected_hostname": expected_hostname,
                                "identity_file": identity, "default_workspace": directory,
                                "birdeye_root": directory, "transport": "lan-ssh"})
            else:
                target = next((x for x in targets if x.get("id") == rid), None)
                if target is None:
                    return {"ok": False, "error": "Connection not found"}
                targets.remove(target)
        else:
            return {"ok": False, "error": "Unsupported registry operation"}
        return _save_registry_change(original, data, {"action": action, "id": rid})


async def registry(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    if request.method == "GET":
        raw = await asyncio.to_thread((ROOT / "config.json").read_bytes)
        config_data = json.loads(raw)
        return response({"ok": True, "revision": root_revision(raw),
                         "roots": [{"id": r["id"], "class": r.get("class"),
                                    "sources": r.get("sources", [])} for r in config_data.get("roots", [])],
                         "connections": [{"id": t.get("id"), "host": t.get("host"),
                                          "port": t.get("port"), "user": t.get("user"),
                                          "expected_hostname": t.get("expected_hostname"),
                                          "workspace": t.get("default_workspace"),
                                          "identity_configured": bool(t.get("identity_file"))}
                                         for t in config_data.get("ssh_targets", [])]})
    if request.headers.get("origin") not in (
        "http://127.0.0.1:8766", "http://localhost:8766",
        "http://127.0.0.1:18768", "http://localhost:18768"
    ):
        return failure("same-origin browser request required", 403)
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return failure("invalid JSON")
    if not isinstance(payload, dict):
        return failure("expected registry operation")
    result = await asyncio.to_thread(update_registry, payload)
    return response(result, 200 if result["ok"] else 409 if result.get("conflict") else 400)


_UPLOAD_LIMIT = 20 * 1024 * 1024
_UPLOAD_NAME = re.compile(r"^[^\\/:*?\"<>|\x00-\x1f]{1,160}$")


def save_uploaded_file(filename: str, blob: bytes, revision: str) -> dict:
    if not isinstance(filename, str) or not _UPLOAD_NAME.fullmatch(filename) or filename in (".", "..") or filename.rstrip(" .") != filename:
        return {"ok": False, "error": "Choose a file with a safe Windows filename"}
    if not isinstance(blob, bytes) or not blob or len(blob) > _UPLOAD_LIMIT:
        return {"ok": False, "error": "File must contain 1 byte to 20 MB of data"}
    with _CONFIG_WRITE_LOCK:
        cfg = ROOT / "config.json"
        original = cfg.read_bytes()
        if revision != root_revision(original):
            return {"ok": False, "error": "Configuration changed. Reload before upload.", "conflict": True}
        config_data = json.loads(original)
        folder = ROOT / "user_data"
        if folder.exists() and (not folder.is_dir() or folder.is_symlink()):
            return {"ok": False, "error": "Managed upload directory is not a regular directory"}
        file_path = folder / filename
        if file_path.exists():
            return {"ok": False, "error": "File already exists. Rename the upload to avoid overwriting."}
        roots = config_data.setdefault("roots", [])
        root = next((r for r in roots if r.get("id") == "user-data"), None)
        if root and Path(root.get("path", "")).resolve() != folder.resolve():
            return {"ok": False, "error": "user-data root conflicts with existing directory"}
        folder.mkdir(exist_ok=True)
        fd = None
        try:
            fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                fd = None
                stream.write(blob)
                stream.flush()
                os.fsync(stream.fileno())
        except OSError:
            if fd is not None:
                os.close(fd)
            return {"ok": False, "error": "Could not save uploaded file"}
        if not root:
            roots.append({"id": "user-data", "path": str(folder), "class": "data",
                          "enabled": True, "index": True, "hash": "sha256", "git": False})
        result = _save_registry_change(original, config_data, {"action": "upload_file", "id": "user-data"})
        if not result["ok"]:
            file_path.unlink(missing_ok=True)
            return result
        result["file"] = {"name": filename, "path": str(file_path),
                          "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest()}
        return result


async def upload_file(request):
    if not allowed(request):
        return failure("local authorization required", 403)
    if request.headers.get("origin") not in (
        "http://127.0.0.1:8766", "http://localhost:8766",
        "http://127.0.0.1:18768", "http://localhost:18768"
    ):
        return failure("same-origin browser request required", 403)
    try:
        filename = request.query_params.get("name", "")
        revision = request.headers.get("x-config-revision", "")
        size = int(request.headers.get("content-length", "0"))
        if size < 1 or size > _UPLOAD_LIMIT:
            return failure("File size must be 1 byte to 20 MB", 413)
        body = await request.body()
        if len(body) != size:
            return failure("Upload length mismatch")
    except (ValueError, OSError):
        return failure("Invalid upload")
    result = await asyncio.to_thread(save_uploaded_file, filename, body, revision)
    return response(result, 200 if result["ok"] else 409 if result.get("conflict") else 400)
