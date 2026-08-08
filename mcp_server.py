from __future__ import annotations

import argparse
import json
import sys
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


BIRDEYE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BIRDEYE_DIR / "config.json"

try:
    from agent import Context, GovernanceError, database_status, inspect_file, load_json, search_workspace
except ImportError:
    Context = None  # type: ignore[misc,assignment]
    GovernanceError = RuntimeError  # type: ignore[misc,assignment,assignment]
    database_status = None  # type: ignore[assignment]
    inspect_file = None  # type: ignore[assignment]
    load_json = None  # type: ignore[assignment]
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
    "description": "List configured knowledge roots with their root_class trust label.",
    "inputSchema": {"type": "object", "properties": {}, "required": []},
}

_BIRDEYE_STATUS_SCHEMA = {
    "name": "birdeye_status",
    "description": "SQLite index health for the shared state/workspace.db.",
    "inputSchema": {"type": "object", "properties": {}, "required": []},
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

_TOOL_DEFINITIONS = [
    _KNOWLEDGE_ROUTE_SCHEMA,
    _KNOWLEDGE_READ_SCHEMA,
    _BIRDEYE_SEARCH_SCHEMA,
    _BIRDEYE_INSPECT_SCHEMA,
    _BIRDEYE_ROOTS_SCHEMA,
    _BIRDEYE_STATUS_SCHEMA,
    _WORKSPACE_IDENTITY_SCHEMA,
    _REVISION_STATUS_SCHEMA,
    _WORKSPACE_RUN_SCHEMA,
    _WORKSPACE_RUN_SEQUENCE_SCHEMA,
    _WORKSPACE_COMMAND_HISTORY_SCHEMA,
]

_TOOL_REGISTRY = {
    "knowledge_route": ("task",),
    "knowledge_read": ("reference",),
    "birdeye_search": ("query", "max_results", "extensions", "roots"),
    "birdeye_inspect": ("path",),
    "birdeye_roots": (),
    "birdeye_status": (),
    "workspace_identity": ("workspace",),
    "revision_status": ("workspace",),
    "workspace_run": ("workspace", "argv", "timeout_seconds", "request_id", "task_id"),
    "workspace_run_sequence": ("workspace", "commands", "stop_on_failure", "request_id", "task_id"),
    "workspace_command_history": ("limit", "workspace"),
}


def _load_ctx() -> "Context":
    if Context is None:
        raise GovernanceError("agent module is required")
    return Context.load()


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
            )
        if tool == "birdeye_inspect":
            return birdeye_inspect(params.get("path", ""))
        if tool == "birdeye_roots":
            return birdeye_roots()
        if tool == "birdeye_status":
            return birdeye_status()
        if tool == "workspace_identity":
            return workspace_identity(params.get("workspace"))
        if tool == "revision_status":
            return revision_status(params.get("workspace"))
        if tool == "workspace_run":
            return run_command(RunRequest.from_mapping(params), CONFIG_PATH)
        if tool == "workspace_run_sequence":
            return run_sequence(RunSequenceRequest.from_mapping(params), CONFIG_PATH)
        if tool == "workspace_command_history":
            limit = int(params.get("limit", 50))
            limit = max(1, min(limit, 200))
            return command_history(CONFIG_PATH, limit=limit, workspace=params.get("workspace"))
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
    return {
        "ok": True,
        "knowledge_roots": [
            {"name": r.name, "path": str(r.path), "root_class": r.root_class}
            for r in ctx.roots
        ],
    }


def birdeye_status() -> dict[str, Any]:
    if database_status is None:
        return {"ok": False, "error": "database_status unavailable"}
    try:
        return {"ok": True, **database_status()}
    except (GovernanceError, FileNotFoundError, OSError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


def birdeye_search(query: str, max_results: int = 25, extensions: str | None = None, roots: str | None = None) -> dict[str, Any]:
    if search_workspace is None:
        return {"ok": False, "error": "search_workspace unavailable"}
    try:
        ext_list = [e.strip() for e in extensions.split(",") if e.strip()] if extensions else None
        roots_list = [r.strip() for r in roots.split(",") if r.strip()] if roots else None
        return search_workspace(
            _load_ctx(),
            query,
            max_results=max(1, min(int(max_results), 200)),
            extensions=ext_list,
            roots=roots_list,
        )
    except (GovernanceError, FileNotFoundError, OSError, ValueError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}


def birdeye_inspect(path: str) -> dict[str, Any]:
    if inspect_file is None:
        return {"ok": False, "error": "inspect_file unavailable"}
    try:
        return inspect_file(_load_ctx(), path)
    except (GovernanceError, FileNotFoundError, ValueError, OSError) as exc:
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}



def serve_stdio() -> None:
    line = ""
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            message = json.loads(line.strip())
        except (json.JSONDecodeError, ValueError):
            continue
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
            is_error = not result.get("ok", False)
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

