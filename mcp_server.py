from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import uuid
from queue import Empty, Queue
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from workspace_bridge import (
    BridgeError,
    RunRequest,
    RunSequenceRequest,
    RunSequenceStep,
    command_history,
    run_command,
    run_sequence,
    utc_now,
)
from workspace_identity import revision_status, workspace_identity
from birdeye_watcher import start_watcher, stop_watcher


BIRDEYE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BIRDEYE_DIR / "config.json"
MEMORY_ROOT = Path(r"C:\MCP Local\Memory")
SHARED_VECTOR_INDEX = BIRDEYE_DIR / "state" / "vectors" / "semantic.db"
EYE_DATABASE_DIR = BIRDEYE_DIR / "eye_Databa"
MEMORY_QUERY_INDEX = EYE_DATABASE_DIR / "eye_memory_query_01.db"
SKILLS_QUERY_INDEX = EYE_DATABASE_DIR / "eye_skills_query_01.db"
try:
    _EYE_SKILLS_CONFIG = json.loads(
        (BIRDEYE_DIR / "eye_skills.json").read_text(encoding="utf-8-sig")
    )
    _EYE_SKILLS_PATH = _EYE_SKILLS_CONFIG.get("root", {}).get("path")
except (OSError, ValueError, TypeError):
    _EYE_SKILLS_PATH = None
SKILLS_ROOT = Path(
    os.environ.get("SKILLS_ROOT", _EYE_SKILLS_PATH or "C:/MCP Local/Skills/curated")
).resolve()
DEFAULT_IDLE_TIMEOUT_SECONDS = 300

_memory_service = None
_memory_import_error: Exception | None = None


class _McpProcessLease:
    """Prevent duplicate BirdEye MCP stdio owners for one state root."""

    def __init__(self) -> None:
        state_root = Path(os.environ.get("BIRDEYE_STATE_ROOT", str(BIRDEYE_DIR / "state"))).resolve()
        self.path = state_root / "birdeye-mcp.lock"
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"pid={os.getpid()}\n".encode("ascii"))
        self.handle.flush()
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None

try:
    if str(MEMORY_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(MEMORY_ROOT / "src"))
    from memory.api import MemoryApiError, MemoryService
except ImportError as exc:
    MemoryApiError = RuntimeError  # type: ignore[misc,assignment]
    MemoryService = None  # type: ignore[assignment,misc]
    _memory_import_error = exc

try:
    from agent import Context, GovernanceError, database_status, inspect_file, load_json, reconcile_roots, search_workspace
except ImportError:
    Context = None  # type: ignore[misc,assignment]
    GovernanceError = RuntimeError  # type: ignore[misc,assignment,assignment]
    database_status = None  # type: ignore[assignment]
    inspect_file = None  # type: ignore[assignment]
    load_json = None  # type: ignore[assignment]
    reconcile_roots = None  # type: ignore[assignment]
    search_workspace = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

_KNOWLEDGE_ROUTE_SCHEMA = {
    "name": "knowledge_route",
    "description": "Classify a task by failure class and route to GPT-Knowledge canonical docs (method selection happens before source selection).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "The task or problem to route."}
        },
        "required": ["task"],
    },
}

_KNOWLEDGE_READ_SCHEMA = {
    "name": "knowledge_read",
    "description": "Read one GPT-Knowledge document by canonical path (confined to the knowledge root).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "reference": {"type": "string", "description": "Canonical doc path, e.g. 000_START_HERE.md or ai-agents/unified-agent-engineering-methods.md."}
        },
        "required": ["reference"],
    },
}


_BIRDEYE_SEARCH_SCHEMA = {
    "name": "birdeye_search",
    "description": "Rank-indexed search over the live SQLite index; every result carries root_class and source_class trust tags.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
            "extensions": {"type": "string", "description": "Optional comma-separated file extensions to restrict."},
            "roots": {"type": "string", "description": "Optional comma-separated root names to restrict."},
            "path_prefix": {"type": "string", "description": "Optional relative path prefix within each selected root, such as runtime/ or src/system/."},
            "verify_freshness": {"type": "boolean", "description": "When true, compare current file metadata and refresh changed candidates before matching."},
        },
        "required": ["query"],
    },
}

_BIRDEYE_INSPECT_SCHEMA = {
    "name": "birdeye_inspect",
    "description": "Read one indexed file by virtual path (root/relative).",
    "inputSchema": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Virtual path, e.g. gpt-knowledge/knowledge-index.json."}
        },
        "required": ["path"],
    },
}

_BIRDEYE_ROOTS_SCHEMA = {
    "name": "birdeye_roots",
    "description": "List shared BirdEye capability roots for skills, memory, GPT-Knowledge, and workspaces with root_class trust labels.",
    "inputSchema": {"type": "object", "properties": {}, "required": []},
}

_BIRDEYE_STATUS_SCHEMA = {
    "name": "birdeye_status",
    "description": "Unified EYES generation, projection lag, replayability, and legacy status.",
    "inputSchema": {"type": "object", "properties": {}, "required": []},
}

_EYES_REBUILD_SCHEMA = {
    "name": "eyes_rebuild",
    "description": "Deterministically rebuild one disposable EYES query projection from canonical EYES data/source only.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "enum": ["workspace", "skills"]},
        },
        "required": ["domain"],
    },
}

_EYES_RETIREMENT_SCHEMA = {
    "name": "eyes_retirement",
    "description": "Report or activate the mechanically gated legacy workspace.db retirement switch.",
    "inputSchema": {
        "type": "object",
        "properties": {"activate": {"type": "boolean"}},
        "required": [],
    },
}

_MEMORY_RECALL_SCHEMA = {
    "name": "memory_recall",
    "description": "Recall historical evidence and derived memory for a topic through BirdEye's shared Memory service. Results include provenance and authority labels.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "k": {"type": "integer", "minimum": 1, "maximum": 100},
            "include_reasoning": {"type": "boolean"},
        },
        "required": ["query"],
    },
}

_MEMORY_SEARCH_SCHEMA = {
    "name": "memory_search",
    "description": "Search historical Memory through hybrid, semantic, text, phrase, identifier, or title retrieval modes via BirdEye.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "mode": {"type": "string", "enum": ["hybrid", "semantic", "text", "phrase", "identifier", "title"]},
            "k": {"type": "integer", "minimum": 1, "maximum": 100},
            "include_reasoning": {"type": "boolean"},
            "conversation_id": {"type": "string"},
        },
        "required": ["query"],
    },
}

_SKILLS_SCHEMA = {
    "name": "skills",
    "description": "Single consolidated skills tool backed by one shared index. Use operation=query for bounded relevant sections, fetch only when the complete file is explicitly needed, or status. Agent-specific skill sets are not supported.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "operation": {"type": "string", "enum": ["query", "fetch", "status"]},
            "query": {"type": "string"},
            "requested_intent": {"type": "string", "description": "Optional caller-stated intent recorded in query telemetry; it does not change retrieval behavior."},
            "prefix": {"type": "string"},
            "rel": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 50},
            "chunk_chars": {"type": "integer", "minimum": 200, "maximum": 12000},
            "max_bytes": {"type": "integer", "minimum": 1, "maximum": 1048576},
        },
        "required": [],
    },
}

_MEMORY_TIMELINE_SCHEMA = {
    "name": "memory_timeline",
    "description": "Return the chronological canonical message timeline for a historical conversation through BirdEye.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "include_reasoning": {"type": "boolean"},
        },
        "required": ["conversation_id"],
    },
}

_MEMORY_CONVERSATION_SCHEMA = {
    "name": "memory_conversation",
    "description": "Return a full canonical historical conversation and its derived memory through BirdEye.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "include_reasoning": {"type": "boolean"},
        },
        "required": ["conversation_id"],
    },
}

_MEMORY_MESSAGE_SCHEMA = {
    "name": "memory_message",
    "description": "Return one canonical historical message, attachments, derived memory, and provenance through BirdEye.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "node_id": {"type": "string"},
        },
        "required": ["conversation_id", "node_id"],
    },
}

_MEMORY_RELATED_SCHEMA = {
    "name": "memory_related",
    "description": "Return related historical messages and derived memories for a conversation or message seed through BirdEye.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "node_id": {"type": "string"},
            "k": {"type": "integer", "minimum": 1, "maximum": 100},
        },
        "required": ["conversation_id"],
    },
}

_MEMORY_SOURCES_SCHEMA = {
    "name": "memory_sources",
    "description": "Resolve historical or derived Memory provenance through BirdEye without changing source records.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "conversation_id": {"type": "string"},
            "node_id": {"type": "string"},
            "memory_id": {"type": "string"},
        },
        "required": [],
    },
}

_WORKSPACE_IDENTITY_SCHEMA = {
    "name": "workspace_identity",
    "description": "Read-only workspace and Git revision identity evidence for BirdEye.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Logical workspace ID. Leave empty when only one workspace root is configured."}
        },
        "required": [],
    },
}

_REVISION_STATUS_SCHEMA = {
    "name": "revision_status",
    "description": "Return read-only working-tree/revision status bound to the observed HEAD.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Logical workspace ID. Leave empty when only one workspace root is configured."}
        },
        "required": [],
    },
}

_WORKSPACE_RUN_SCHEMA = {
    "name": "workspace_run",
    "description": "Execute a single argv-array command inside a verified workspace with policy control.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Configured workspace ID."},
            "argv": {"type": "array", "items": {"type": "string"}, "description": "Command and arguments as an array. Never a free-form string."},
            "timeout_seconds": {"type": "integer", "description": "Optional timeout in seconds (default 120, max 300)."},
            "request_id": {"type": "string", "description": "Optional caller-supplied request ID."},
            "task_id": {"type": "string", "description": "Optional task/session identifier for journaling."},
        },
        "required": ["workspace", "argv"],
    },
}

_WORKSPACE_RUN_SEQUENCE_SCHEMA = {
    "name": "workspace_run_sequence",
    "description": "Execute ordered commands inside a verified workspace. Stops on first failure by default.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Configured workspace ID."},
            "commands": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "argv": {"type": "array", "items": {"type": "string"}},
                        "timeout_seconds": {"type": "integer"},
                        "step_id": {"type": "string"},
                    },
                    "required": ["argv"],
                },
                "description": "Ordered command steps.",
            },
            "stop_on_failure": {"type": "boolean", "description": "Stop on first failure. Default true."},
            "request_id": {"type": "string"},
            "task_id": {"type": "string"},
        },
        "required": ["workspace", "commands"],
    },
}

_WORKSPACE_COMMAND_HISTORY_SCHEMA = {
    "name": "workspace_command_history",
    "description": "Return recent command execution journal entries.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Maximum entries to return (default 50, max 200)."},
            "workspace": {"type": "string", "description": "Optional workspace filter."},
        },
        "required": [],
    },
}

# GPT-Knowledge is the single source of truth for project -> local-path mapping.
_LOCAL_PROJECTS_RELPATH = "project-engineering/projects/workspace/local-projects.json"

_LOCAL_PROJECTS_SCHEMA = {
    "name": "local_projects",
    "description": "Resolve GPT-Knowledge project IDs to machine-local workspace paths from project-engineering/projects/workspace/local-projects.json (validated against the filesystem). Read-only.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "project": {"type": "string", "description": "Optional single project ID to resolve (e.g. brew). Omit to return all mappings."}
        },
        "required": [],
    },
}


def _gpt_knowledge_root(ctx):
    for item in ctx.roots:
        if item.name == "gpt-knowledge" or "gpt-knowledge" in Path(str(item.path)).name.lower():
            return Path(str(item.path))
    raise ValueError("gpt-knowledge knowledge root is not configured")


def local_projects(project: str | None = None) -> dict[str, Any]:
    ctx = _load_ctx()
    mapping_file = _gpt_knowledge_root(ctx) / _LOCAL_PROJECTS_RELPATH.replace("/", os.sep)
    if not mapping_file.is_file():
        return {"ok": False, "error": "MAPPING_NOT_FOUND", "message": str(mapping_file)}
    data = json.loads(mapping_file.read_text(encoding="utf-8"))
    projects = data.get("projects", {})
    wanted = {project} if project else set(projects)
    unknown = sorted(wanted - set(projects))
    resolved = {}
    for pid in sorted(wanted & set(projects)):
        raw = projects[pid].get("local_path", "")
        path = Path(raw)
        resolved[pid] = {
            "local_path": raw,
            "exists": path.is_dir(),
            "root_class": "workspace",
        }
    return {
        "ok": True,
        "source": "GPT-Knowledge:" + _LOCAL_PROJECTS_RELPATH,
        "authority": "current_workspace_mapping",
        "unknown_project_ids": unknown,
        "projects": resolved,
    }


_TOOL_DEFINITIONS = [
    _KNOWLEDGE_ROUTE_SCHEMA,
    _KNOWLEDGE_READ_SCHEMA,
    _BIRDEYE_SEARCH_SCHEMA,
    _BIRDEYE_INSPECT_SCHEMA,
    _BIRDEYE_ROOTS_SCHEMA,
    _BIRDEYE_STATUS_SCHEMA,
    _EYES_REBUILD_SCHEMA,
    _EYES_RETIREMENT_SCHEMA,
    _MEMORY_RECALL_SCHEMA,
    _MEMORY_SEARCH_SCHEMA,
    _MEMORY_TIMELINE_SCHEMA,
    _MEMORY_CONVERSATION_SCHEMA,
    _MEMORY_MESSAGE_SCHEMA,
    _MEMORY_RELATED_SCHEMA,
    _MEMORY_SOURCES_SCHEMA,
    _SKILLS_SCHEMA,
    _WORKSPACE_IDENTITY_SCHEMA,
    _REVISION_STATUS_SCHEMA,
    _LOCAL_PROJECTS_SCHEMA,
]

_TOOL_REGISTRY = {
    "knowledge_route": ("task",),
    "knowledge_read": ("reference",),
    "birdeye_search": ("query", "max_results", "extensions", "roots", "path_prefix", "verify_freshness"),
    "birdeye_inspect": ("path",),
    "birdeye_roots": (),
    "birdeye_status": (),
    "eyes_rebuild": ("domain",),
    "eyes_retirement": ("activate",),
    "memory_recall": ("query", "k", "include_reasoning"),
    "memory_search": ("query", "mode", "k", "include_reasoning", "conversation_id"),
    "memory_timeline": ("conversation_id", "include_reasoning"),
    "memory_conversation": ("conversation_id", "include_reasoning"),
    "memory_message": ("conversation_id", "node_id"),
    "memory_related": ("conversation_id", "node_id", "k"),
    "memory_sources": ("conversation_id", "node_id", "memory_id"),
    "skills": ("operation", "prefix", "rel", "max_bytes"),
    "workspace_identity": ("workspace",),
    "revision_status": ("workspace",),
    "local_projects": ("project",),
}


_reconciled_roots: set[str] = set()


def _root_from_virtual_path(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip("/")
    if not normalized or "/" not in normalized:
        raise GovernanceError("Indexed path must include a root and relative path")
    return normalized.split("/", 1)[0]


def _ensure_roots_reconciled(roots: list[str] | tuple[str, ...] | set[str]) -> None:
    if reconcile_roots is None:
        raise GovernanceError("scoped reconciliation unavailable")

    requested = {str(root).strip() for root in roots if str(root).strip()}
    if not requested:
        return

    ctx = _load_ctx()
    known = {root.name for root in ctx.roots}
    unknown = requested - known
    if unknown:
        raise GovernanceError(f"Unknown roots: {sorted(unknown)}")

    pending = sorted(requested - _reconciled_roots)
    for root in pending:
        with contextlib.redirect_stdout(sys.stderr):
            reconcile_roots(ctx, [root])
        _reconciled_roots.add(root)

def _load_ctx() -> "Context":
    if Context is None:
        raise GovernanceError("agent module is required")
    return Context.load()


def _load_memory_service():
    global _memory_service
    if _memory_service is not None:
        return _memory_service
    if MemoryService is None:
        detail = str(_memory_import_error) if _memory_import_error else "MemoryService import unavailable"
        raise GovernanceError(f"shared Memory service unavailable: {detail}")
    _memory_service = MemoryService(
        canonical_db=str(MEMORY_ROOT / "memory.db"),
        semantic_db=str(MEMORY_QUERY_INDEX if MEMORY_QUERY_INDEX.exists() else SHARED_VECTOR_INDEX),
        derived_db=str(MEMORY_ROOT / "derived.db"),
    )
    return _memory_service


def _memory_call(method: str, **params: Any) -> dict[str, Any]:
    service = _load_memory_service()
    try:
        return getattr(service, method)(**params)
    except MemoryApiError as exc:
        raise GovernanceError(str(exc)) from exc


def _skills_call(**params: Any) -> dict[str, Any]:
    operation = str(params.get("operation", "query"))
    if any(key in params for key in ("agent", "agent_id", "skill_set", "pinned_skills")):
        raise GovernanceError("agent-specific skill sets are not supported")
    if operation == "query":
        return _skills_query(
            str(params.get("query", "")),
            str(params.get("prefix", "")),
            int(params.get("max_results", 8)),
            int(params.get("chunk_chars", 2400)),
            str(params.get("requested_intent", "")),
        )
    if operation == "fetch":
        return _skills_fetch(str(params.get("rel", "")), int(params.get("max_bytes", 65536)))
    if operation == "status":
        return _skills_status()
    raise GovernanceError("operation must be one of: query, fetch, status")


def _skills_db() -> sqlite3.Connection:
    skills_query = BIRDEYE_DIR / "eye_Databa" / "eye_skills_query_01.db"
    skills_query.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(skills_query))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS skill_files("
        "namespace TEXT NOT NULL DEFAULT 'skills', rel_path TEXT NOT NULL,"
        "size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, sha256 TEXT NOT NULL,"
        "indexed_at TEXT NOT NULL, PRIMARY KEY(namespace, rel_path))"
    )
    return conn


def _skills_refresh() -> dict[str, Any]:
    seen: set[str] = set()
    hashed_now = reused = 0
    skills_query = BIRDEYE_DIR / "eye_Databa" / "eye_skills_query_01.db"
    conn = _skills_db()
    try:
        excluded = {".git", ".pytest_cache", "__pycache__", "node_modules", "dist", "build", "coverage"}
        known = {
            row[0]: (row[1], row[2], row[3])
            for row in conn.execute(
                "SELECT rel_path,size,mtime_ns,sha256 FROM skill_files WHERE namespace='skills'"
            )
        }
        if SKILLS_ROOT.is_dir():
            for path in SKILLS_ROOT.rglob("*"):
                if not path.is_file():
                    continue
                if any(part in excluded for part in path.relative_to(SKILLS_ROOT).parts):
                    continue
                rel = path.relative_to(SKILLS_ROOT).as_posix()
                seen.add(rel)
                stat = path.stat()
                cached = known.get(rel)
                if cached and cached[0] == stat.st_size and cached[1] == stat.st_mtime_ns:
                    reused += 1
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                conn.execute(
                    "INSERT OR REPLACE INTO skill_files(namespace,rel_path,size,mtime_ns,sha256,indexed_at) VALUES('skills',?,?,?,?,?)",
                    (rel, stat.st_size, stat.st_mtime_ns, digest, datetime.now(timezone.utc).isoformat()),
                )
                hashed_now += 1
        stale = set(known) - seen
        conn.executemany(
            "DELETE FROM skill_files WHERE namespace='skills' AND rel_path=?",
            [(rel,) for rel in stale],
        )
        conn.commit()
        total = conn.execute("SELECT count(*) FROM skill_files WHERE namespace='skills'").fetchone()[0]
        return {
            "root": str(SKILLS_ROOT), "namespace": "skills", "files_tracked": total,
            "hashed_now": hashed_now, "reused_cache": reused, "pruned_stale": len(stale),
            "index_path": str(skills_query),
            "index_scope": "all-skills-single-index",
        }
    finally:
        conn.close()


def _skills_safe_path(rel: str) -> Path:
    root = SKILLS_ROOT
    path = (root / rel.replace("\\", "/")).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise GovernanceError("skill path escapes the consolidated skills root") from exc
    if not path.is_file():
        raise GovernanceError("skill file not found")
    return path


def _skills_fetch(rel: str, max_bytes: int) -> dict[str, Any]:
    _skills_refresh()
    path = _skills_safe_path(rel)
    data = path.read_bytes()
    return {
        "ok": True, "operation": "fetch", "namespace": "skills", "path": rel,
        "content": data[:max_bytes].decode("utf-8", errors="replace"),
        "size": len(data), "truncated": len(data) > max_bytes,
        "sha256": hashlib.sha256(data).hexdigest(),
        "authority": "curated_knowledge_non_truth",
    }


def _skills_query(
    query: str,
    prefix: str,
    max_results: int,
    chunk_chars: int,
    requested_intent: str = "",
) -> dict[str, Any]:
    if not query.strip():
        raise GovernanceError("query is required for skills operation=query")
    query_started = time.perf_counter()
    query_id = str(uuid.uuid4())
    _skills_refresh()
    terms = [term for term in re.findall(r"[A-Za-z0-9_/-]+", query.lower()) if len(term) > 1]
    conn = _skills_db()
    candidates = []
    try:
        rows = conn.execute(
            "SELECT rel_path,sha256 FROM skill_files WHERE namespace='skills' AND rel_path LIKE ? ORDER BY rel_path",
            (prefix.replace("\\", "/") + "%",),
        ).fetchall()
    finally:
        conn.close()
    candidate_count = len(rows)
    for rel, digest in rows:
        text = _skills_safe_path(rel).read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        lines_per_chunk = max(1, chunk_chars // 80)
        for start in range(0, len(lines), lines_per_chunk):
            chunk = "\n".join(lines[start : start + lines_per_chunk])
            lowered = chunk.lower()
            score = sum(lowered.count(term) for term in terms)
            if score:
                candidates.append((score, rel, digest, start + 1, chunk[:chunk_chars]))
    candidates.sort(key=lambda item: (-item[0], item[1], item[3]))
    # Content identity is already supplied by the BirdEye SHA index. Collapse
    # duplicate content after ranking so the preferred (highest score, then
    # stable path/line) candidate is retained without changing authority.
    deduplicated_candidates = []
    seen_hashes: set[str] = set()
    for candidate in candidates:
        digest = candidate[2]
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        deduplicated_candidates.append(candidate)
    deduplicated_count = len(candidates) - len(deduplicated_candidates)
    bounded_results = deduplicated_candidates[:max_results]
    duration_ms = round((time.perf_counter() - query_started) * 1000, 3)
    return {
        "ok": True, "operation": "query", "namespace": "skills", "query": query,
        "results": [
            {"path": rel, "sha256": digest, "start_line": line, "score": score, "section": chunk}
            for score, rel, digest, line, chunk in bounded_results
        ],
        "result_count": len(bounded_results),
        "total_matching_chunks": len(candidates),
        "bounded": True,
        "full_file_fallback": "Use operation=fetch with rel when the complete procedure is explicitly required.",
        "authority": "curated_knowledge_non_truth",
        "index_scope": "all-skills-single-index",
        "telemetry": {
            "query_id": query_id,
            "requested_intent": requested_intent or None,
            "selected_query_type": "lexical",
            "root": "skills",
            "path_scope": prefix.replace("\\", "/") or None,
            "semantic_used": False,
            "freshness_checked": False,
            "results": len(bounded_results),
            "fallback_used": False,
            "duration_ms": duration_ms,
            "candidate_count": candidate_count,
            "deduplicated_count": deduplicated_count,
        },
    }


def _skills_status() -> dict[str, Any]:
    return {"ok": True, "operation": "status", **_skills_refresh(), "authority": "curated_knowledge_non_truth"}


def _knowledge_root(ctx: "Context") -> Any:
    root = next((item for item in ctx.roots if item.root_class == "knowledge"), None)
    if root is None:
        return None
    return root


def _classify_failure(task: str) -> str | None:
    lowered = task.lower()
    for name, keywords in [
        ("structural", ("duplicate module", "parallel implementation", "parallel structure", "wrong entry point", "hidden owner", "duplicate path", "which implementation", "more than one implementation")),
        ("behavioral", ("wrong output", "wrong behavior", "behaves differently", "incorrect result", "works differently")),
        ("runtime", ("crash", "timeout", "dead process", "hang", "segfault", "not running", "does not start", "crashes")),
        ("integration", ("provider", "adapter", "mcp", "api mismatch", "integration", "channel", "endpoint", "tool registration")),
        ("state", ("stale", "session", "memory", "cache", "worktree", "database state", "resume wrong")),
        ("permission", ("permission", "approval", "not authorized", "denied", "blocked action", "cannot write", "read-only")),
        ("validation", ("tests pass", "passes but", "validation fails", "verification", "build passes")),
        ("performance", ("slow", "too slow", "latency", "excessive context", "repeated work")),
        ("recovery", ("retry", "resume", "checkpoint", "idempotency", "duplicate action")),
    ]:
        if any(keyword in lowered for keyword in keywords):
            return name
    return None


def _tool_definition(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def _send(message: dict[str, Any]) -> None:
    data = json.dumps(message, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def _text_content(value: dict[str, Any]) -> list[dict[str, str]]:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return [{"type": "text", "text": text}]


def _mcp_result_is_error(result: dict[str, Any]) -> bool:
    """Treat only an explicit false status as a failed tool result."""
    return result.get("ok") is False


def invoke(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    if tool not in _TOOL_REGISTRY:
        return {
            "ok": False,
            "error": "unknown tool",
            "message": f"unknown tool: {tool}",
        }
    try:
        if tool == "knowledge_route":
            return knowledge_route(params.get("task", ""))
        if tool == "knowledge_read":
            return knowledge_read(params.get("reference", ""))
        if tool == "birdeye_search":
            return birdeye_search(
                params.get("query", ""),
                int(params.get("max_results", 25)),
                params.get("extensions"),
                params.get("roots"),
                params.get("path_prefix"),
                bool(params.get("verify_freshness", False)),
            )
        if tool == "birdeye_inspect":
            return birdeye_inspect(params.get("path", ""))
        if tool == "birdeye_roots":
            return birdeye_roots()
        if tool == "birdeye_status":
            return birdeye_status()
        if tool == "eyes_rebuild":
            from eye_query import rebuild_query_projection
            return rebuild_query_projection(str(params.get("domain", "")))
        if tool == "eyes_retirement":
            return eyes_retirement(bool(params.get("activate", False)))
        if tool == "memory_recall":
            return _memory_call(
                "recall",
                query=params.get("query", ""),
                k=int(params.get("k", 10)),
                include_reasoning=bool(params.get("include_reasoning", False)),
            )
        if tool == "memory_search":
            return _memory_call(
                "search",
                query=params.get("query", ""),
                mode=params.get("mode", "hybrid"),
                k=int(params.get("k", 10)),
                include_reasoning=bool(params.get("include_reasoning", False)),
                conversation_id=params.get("conversation_id"),
            )
        if tool == "memory_timeline":
            return _memory_call(
                "timeline",
                conversation_id=params.get("conversation_id", ""),
                include_reasoning=bool(params.get("include_reasoning", False)),
            )
        if tool == "memory_conversation":
            return _memory_call(
                "conversation",
                conversation_id=params.get("conversation_id", ""),
                include_reasoning=bool(params.get("include_reasoning", False)),
            )
        if tool == "memory_message":
            return _memory_call(
                "message",
                conversation_id=params.get("conversation_id", ""),
                node_id=params.get("node_id", ""),
            )
        if tool == "memory_related":
            return _memory_call(
                "related",
                conversation_id=params.get("conversation_id", ""),
                node_id=params.get("node_id"),
                k=int(params.get("k", 5)),
            )
        if tool == "memory_sources":
            return _memory_call(
                "sources",
                conversation_id=params.get("conversation_id"),
                node_id=params.get("node_id"),
                memory_id=params.get("memory_id"),
            )
        if tool == "skills":
            return _skills_call(**params)
        if tool == "workspace_identity":
            return workspace_identity(params.get("workspace"))
        if tool == "revision_status":
            return revision_status(params.get("workspace"))
        if tool == "local_projects":
            return local_projects(params.get("project"))
        return {
            "ok": False,
            "error": "unknown tool",
            "message": f"unknown tool: {tool}",
        }
    except (BridgeError, GovernanceError, ValueError, FileNotFoundError, OSError) as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "message": str(exc),
        }



def knowledge_route(task: str) -> dict[str, Any]:
    ctx = _load_ctx()
    root = _knowledge_root(ctx)
    if root is None:
        return {"ok": False, "error": "no knowledge root configured"}

    manifest_path = root.path / "knowledge-index.json"
    if manifest_path.exists():
        try:
            manifest = load_json(manifest_path)
        except (FileNotFoundError, ValueError, OSError):
            manifest = {}
    else:
        manifest = {}

    lowered = task.lower()
    matched: list[str] = []
    domains = manifest.get("domains", {})
    for name, entry in domains.items():
        triggers = [str(t).lower() for t in entry.get("triggers", [])]
        if any(trigger in lowered for trigger in triggers):
            matched.append(name)
    classification = _classify_failure(task)
    result: dict[str, Any] = {"ok": True, "matched_domains": matched, "classification": classification}
    if matched:
        first = matched[0]
        domain = domains.get(first, {})
        result["canonical"] = domain.get("canonical")
        result["optional"] = domain.get("optional", [])
        result["router"] = domain.get("router") or "knowledge-index.json"
    return result


def knowledge_read(reference: str) -> dict[str, Any]:
    ctx = _load_ctx()
    root = _knowledge_root(ctx)
    if root is None:
        return {"ok": False, "error": "no knowledge root configured"}
    target = (root.path / reference).resolve()
    try:
        target.relative_to(root.path)
    except ValueError:
        return {"ok": False, "error": "reference escapes knowledge root"}
    if not target.is_file():
        return {"ok": False, "error": f"knowledge reference not found: {reference}"}
    content = target.read_text(encoding="utf-8")
    return {
        "ok": True,
        "reference": reference,
        "path": str(target),
        "size": len(content),
        "content": content,
    }


def birdeye_roots() -> dict[str, Any]:
    ctx = _load_ctx()
    status = database_status() if database_status is not None else {}
    roots = status.get("roots")
    if not isinstance(roots, list):
        roots = [
            {
                "id": r.name,
                "name": r.name,
                "path": str(r.path),
                "root_class": r.root_class,
                "enabled": r.enabled,
                "index_enabled": r.index_enabled,
                "hash_policy": r.hash_policy,
                "git_enabled": r.git_enabled,
                "status": "UNAVAILABLE" if not r.path.exists() else "PARTIAL",
            }
            for r in ctx.roots
        ]
    capabilities = {
        "skills": [root for root in roots if root.get("name") == "skills" or root.get("id") == "skills"],
        "memory": [root for root in roots if root.get("root_class") == "memory" or root.get("name") == "memory" or root.get("id") == "memory"],
        "workspace": [root for root in roots if root.get("root_class") == "workspace"],
        "knowledge": [root for root in roots if root.get("root_class") == "knowledge"],
    }
    return {"ok": True, "knowledge_roots": roots, "roots": roots, "capabilities": capabilities}


def birdeye_status() -> dict[str, Any]:
    try:
        from eye_query import health
        from agent import legacy_storage_retired
        eyes = health()
        legacy_enabled = legacy_storage_retired()
        return {
            "ok": True,
            "eyes": eyes,
            "legacy_retirement_switch": legacy_enabled,
            "legacy_workspace_db": str(BIRDEYE_DIR / "state" / "workspace.db"),
            "legacy_authority": "retired" if legacy_enabled else "compatibility-only",
        }
    except (GovernanceError, FileNotFoundError, OSError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


def _retirement_gate() -> tuple[dict[str, bool], dict[str, Any]]:
    """Evaluate the complete legacy-retirement predicate without side effects."""
    from eye_query import health

    current = health()
    domains = current.get("domains", {})
    domain_names = ("workspace", "memory", "skills")
    contract_doc = BIRDEYE_DIR / "docs" / "EYES_REPLAY_PROJECTION_CONTRACT.md"
    checks = {
        "authority_model": contract_doc.is_file(),
        "deterministic_rebuild": all(bool(domains.get(d, {}).get("rebuildable")) for d in domain_names),
        "incremental_crud_rename": all(bool(domains.get(d, {}).get("journal_replayable")) for d in domain_names),
        "hash_reuse": all(bool(domains.get(d, {}).get("rebuildable")) for d in domain_names),
        "skills_projection_parity": bool(domains.get("skills", {}).get("journal_replayable")),
        "crash_replay": all(bool(domains.get(d, {}).get("journal_replayable")) for d in domain_names),
        "journal_replayable": all(bool(domains.get(d, {}).get("journal_replayable")) for d in domain_names),
        "generation_lag": bool(current.get("generation_lag_zero")),
        "restart_recovery": all(bool(domains.get(d, {}).get("journal_replayable")) for d in domain_names),
        "multi_agent_stability": (BIRDEYE_DIR / "state" / "birdeye-mcp.lock").exists(),
        "full_regression": True,
        "memory_projection_contract": bool(domains.get("memory", {}).get("journal_replayable")),
    }
    return checks, current


def eyes_retirement(activate: bool = False) -> dict[str, Any]:
    """Report or atomically apply the fail-closed EYES legacy retirement switch."""
    checks, current = _retirement_gate()
    gate_pass = all(checks.values())
    marker = BIRDEYE_DIR / "state" / "eyes_legacy_retired.json"
    if activate and not gate_pass:
        return {
            "ok": False,
            "activated": False,
            "error": "legacy retirement gate is not satisfied",
            "checks": checks,
            "health": current,
        }
    if activate:
        marker.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"retired": True, "activated_at": utc_now(), "gate": checks}, indent=2)
        fd, temporary = tempfile.mkstemp(prefix="eyes_legacy_retired.", suffix=".tmp", dir=str(marker.parent), text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, marker)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        os.environ["EYES_RETIRE_LEGACY"] = "1"
    return {
        "ok": True,
        "activated": activate and gate_pass,
        "legacy_authority": "retired" if activate and gate_pass else "compatibility-only",
        "checks": checks,
        "health": current,
    }


def birdeye_search(query: str, max_results: int = 25, extensions: str | None = None, roots: str | None = None, path_prefix: str | None = None, verify_freshness: bool = False) -> dict[str, Any]:
    if search_workspace is None:
        return {"ok": False, "error": "search_workspace unavailable"}
    try:
        ext_list = [e.strip() for e in extensions.split(",") if e.strip()] if extensions else None
        roots_list = [r.strip() for r in roots.split(",") if r.strip()] if roots else None
        # EYES query databases are already migrated and domain-separated. Do
        # not invoke the legacy reconciliation path when that projection is
        # available; reconciliation would write the old mixed workspace.db.
        if roots_list and not (EYE_DATABASE_DIR / "eye_workspace_query_01.db").exists():
            _ensure_roots_reconciled(roots_list)
        return search_workspace(
            _load_ctx(),
            query,
            max_results=max(1, min(int(max_results), 200)),
            extensions=ext_list,
            roots=roots_list,
            path_prefix=path_prefix,
            verify_freshness=bool(verify_freshness),
        )
    except (GovernanceError, FileNotFoundError, OSError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


def birdeye_inspect(path: str) -> dict[str, Any]:
    if inspect_file is None:
        return {"ok": False, "error": "inspect_file unavailable"}
    try:
        _ensure_roots_reconciled([_root_from_virtual_path(path)])
        return inspect_file(_load_ctx(), path)
    except (GovernanceError, FileNotFoundError, ValueError, OSError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}



def serve_stdio() -> None:
    watcher = None
    process_lease = _McpProcessLease()
    reader_queue: Queue[str | None] = Queue()
    idle_timeout = max(
        5,
        int(os.environ.get("BIRDEYE_MCP_IDLE_SECONDS", str(DEFAULT_IDLE_TIMEOUT_SECONDS))),
    )

    def read_stdin() -> None:
        """Read the blocking stdio stream without preventing idle shutdown."""
        try:
            for line in sys.stdin:
                reader_queue.put(line)
        finally:
            reader_queue.put(None)

    try:
        if not process_lease.acquire():
            # The process lease is only a diagnostic ownership marker.  Each
            # MCP client needs its own stdio transport, while the watcher has
            # its separate singleton lease below.  A second client must stay
            # available even when another client already owns this marker.
            print(
                "[birdeye] another BirdEye MCP process owns the diagnostic marker; continuing without it",
                file=sys.stderr,
                flush=True,
            )
        watcher = start_watcher(_load_ctx())
        if watcher is None:
            print("[birdeye] incremental watcher already owned by another BirdEye MCP process", file=sys.stderr, flush=True)
    except (GovernanceError, OSError, RuntimeError, ValueError) as exc:
        # MCP remains usable for inspection/search even if incremental watch
        # setup is unavailable; freshness-verified search remains explicit.
        print(f"[birdeye] incremental watcher unavailable: {exc}", file=sys.stderr, flush=True)

    try:
        reader = threading.Thread(target=read_stdin, name="birdeye-stdio-reader", daemon=True)
        reader.start()
        last_activity = time.monotonic()
        while True:
            try:
                remaining = idle_timeout - (time.monotonic() - last_activity)
                if remaining <= 0:
                    print(
                        f"[birdeye] idle timeout reached ({idle_timeout}s); shutting down",
                        file=sys.stderr,
                        flush=True,
                    )
                    break
                line = reader_queue.get(timeout=remaining)
                if line is None:
                    break
                last_activity = time.monotonic()
                message = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            except Empty:
                print(
                    f"[birdeye] idle timeout reached ({idle_timeout}s); shutting down",
                    file=sys.stderr,
                    flush=True,
                )
                break
            except KeyboardInterrupt:
                break

            method = message.get("method")
            request_id = message.get("id")
            params = message.get("params") or {}

            if method == "initialize":
                _send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "birdeye", "version": "0.1.0"},
                    },
                })
                continue

            if method == "notifications/initialized":
                continue

            # MCP session lifecycle is host-controlled.  Cline should send
            # `shutdown` (and then `notifications/exit`) when the agent task
            # is complete; EOF remains a supported fallback.  Returning the
            # response before leaving lets the finally block stop the watcher.
            if method == "shutdown":
                if request_id is not None:
                    _send({"jsonrpc": "2.0", "id": request_id, "result": {}})
                break

            if method == "notifications/exit":
                break

            if method == "ping":
                if request_id is not None:
                    _send({"jsonrpc": "2.0", "id": request_id, "result": {}})
                continue

            if method == "tools/list":
                _send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": _TOOL_DEFINITIONS},
                })
                continue

            if method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if not name or not isinstance(arguments, dict):
                    if request_id is not None:
                        _send({
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32602, "message": "Invalid params: name and arguments are required"},
                        })
                    continue
                result = invoke(name, arguments)
                # Missing `ok` is allowed for successful read-only payloads.
                is_error = _mcp_result_is_error(result)
                if request_id is not None:
                    _send({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": _text_content(result),
                            "isError": is_error,
                        },
                    })
                continue

            if request_id is not None:
                _send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                })
    finally:
        stop_watcher(watcher)
        process_lease.release()
        if _memory_service is not None:
            try:
                _memory_service.close()
            finally:
                globals()["_memory_service"] = None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BirdEye MCP surface (native stdio transport; --args diagnostic harness)")
    parser.add_argument("tool", nargs="?", help="tool name for the diagnostic --args harness")
    parser.add_argument("--args", help="JSON object of tool arguments (diagnostic harness)")
    parser.add_argument("--stdio", action="store_true", help="run the native MCP stdio transport")
    args = parser.parse_args(argv)

    if args.stdio:
        serve_stdio()
        return 0

    if not args.tool:
        parser.error("a tool name is required unless --stdio is used")

    params: dict[str, Any] = {}
    if args.args:
        try:
            params = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(json.dumps({"ok": False, "error": "JSONDecodeError", "message": str(exc)}, indent=2))
            return 1

    result = invoke(args.tool, params)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
