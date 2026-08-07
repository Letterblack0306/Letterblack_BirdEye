"""BirdEye thin MCP surface.

Six MCP-style tools over a shared SQLite index:

  Knowledge routing (GPT-Knowledge, method-before-source):
    knowledge_route(task)   -> classify by failure class + route to canonical docs
    knowledge_read(reference) -> read one knowledge document by canonical path

  Workspace evidence (BirdEye read-only indexer):
    birdeye_search(query, ...) -> rank-indexed matches, tagged root_class/source_class
    birdeye_inspect(path)      -> read one file by virtual path (root/relative)
    birdeye_roots()            -> configured knowledge roots + root_class
    birdeye_status()           -> SQLite index health

Trust invariant: root_class distinguishes workspace (live evidence),
knowledge (methodology / decision guidance), and reference (examples /
provenance). Knowledge can never masquerade as evidence about the active
project; that boundary is encoded at the storage/index layer.

Run each tool for proof:
    python mcp_server.py <tool> [--args '<json>']
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from agent import (
    Context,
    GovernanceError,
    database_status,
    inspect_file,
    load_json,
    search_workspace,
)

# Failure-class table consolidated from GPT-Knowledge
# ai-agents/unified-agent-engineering-methods.md (section 2).
_FAILURE_CLASSES: list[tuple[str, tuple[str, ...]]] = [
    ("structural", ("duplicate module", "parallel implementation", "parallel structure",
                    "wrong entry point", "hidden owner", "duplicate path", "which implementation",
                    "more than one implementation")),
    ("behavioral", ("wrong output", "wrong behavior", "behaves differently",
                    "incorrect result", "works differently")),
    ("runtime", ("crash", "timeout", "dead process", "hang", "segfault",
                 "not running", "does not start", "crashes")),
    ("integration", ("provider", "adapter", "mcp", "api mismatch", "integration",
                     "channel", "endpoint", "tool registration")),
    ("state", ("stale", "session", "memory", "cache", "worktree",
               "database state", "resume wrong")),
    ("permission", ("permission", "approval", "not authorized", "denied",
                    "blocked action", "cannot write", "read-only")),
    ("validation", ("tests pass", "passes but", "validation fails", "verification", "build passes")),
    ("performance", ("slow", "too slow", "latency", "excessive context", "repeated work")),
    ("recovery", ("retry", "resume", "checkpoint", "idempotency", "duplicate action")),
]


def _load_ctx() -> Context:
    return Context.load()


def _knowledge_root(ctx: Context):
    for root in ctx.roots:
        if root.root_class == "knowledge":
            return root
    return None


def _classify_failure(task: str) -> str | None:
    lowered = task.lower()
    for name, keywords in _FAILURE_CLASSES:
        if any(keyword in lowered for keyword in keywords):
            return name
    return None


def knowledge_route(task: str) -> dict[str, Any]:
    """Route a task to GPT-Knowledge canonical docs (method before source)."""
    ctx = _load_ctx()
    root = _knowledge_root(ctx)
    if root is None:
        return {"ok": False, "error": "no knowledge root configured"}
    manifest_path = root.path / "knowledge-index.json"
    manifest = load_json(manifest_path) if manifest_path.exists() else {}
    lowered = task.lower()
    matched: list[dict[str, Any]] = []
    for name, entry in manifest.get("domains", {}).items():
        triggers = [str(trigger).lower() for trigger in entry.get("triggers", [])]
        if any(trigger in lowered for trigger in triggers):
            matched.append({
                "domain": name,
                "canonical": entry.get("canonical", []),
                "optional": list((entry.get("optional") or {}).keys()),
            })
    return {
        "ok": True,
        "task": task,
        "classification": {"failure_class": _classify_failure(task)},
        "domains": matched,
        # Canonical agent-engineering guide is the primary method destination
        # for agent work; source-specific studies are provenance only.
        "engineering_guide": "ai-agents/unified-agent-engineering-methods.md",
        "router": str(manifest_path),
    }


def knowledge_read(reference: str) -> dict[str, Any]:
    """Read one knowledge document by canonical path (must stay in the root)."""
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


def birdeye_search(
    query: str,
    max_results: int = 25,
    extensions: str | None = None,
    roots: str | None = None,
) -> dict[str, Any]:
    """Rank-indexed search; every result carries root_class + source_class."""
    ctx = _load_ctx()
    ext = [x.strip() for x in extensions.split(",") if x.strip()] if extensions else None
    rts = [x.strip() for x in roots.split(",") if x.strip()] if roots else None
    return search_workspace(ctx, query, max_results=int(max_results), extensions=ext, roots=rts)


def birdeye_inspect(path: str) -> dict[str, Any]:
    """Read one indexed file by virtual path (root/relative)."""
    return inspect_file(_load_ctx(), path)


def birdeye_roots() -> dict[str, Any]:
    """Configured knowledge roots with their root_class trust label."""
    ctx = _load_ctx()
    return {
        "knowledge_roots": [
            {"name": root.name, "path": str(root.path), "root_class": root.root_class}
            for root in ctx.roots
        ]
    }


def birdeye_status() -> dict[str, Any]:
    """SQLite index health for the shared state/workspace.db."""
    return database_status()


_TOOL_REGISTRY: dict[str, tuple[str, ...]] = {
    "knowledge_route": ("task",),
    "knowledge_read": ("reference",),
    "birdeye_search": ("query", "max_results", "extensions", "roots"),
    "birdeye_inspect": ("path",),
    "birdeye_roots": (),
    "birdeye_status": (),
}


def invoke(tool: str, params: dict[str, Any]) -> dict[str, Any]:
    handler = globals().get(tool)
    if handler is None:
        return {"ok": False, "error": f"unknown tool: {tool}"}
    return handler(**params)



def _tool_definition(name, description, properties, required):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


_TOOL_DEFINITIONS = [
    _tool_definition(
        "knowledge_route",
        "Classify a task by failure class and route to GPT-Knowledge canonical docs "
        "(method selection happens before source selection).",
        {
            "task": {"type": "string", "description": "The task or problem to route."},
        },
        ["task"],
    ),
    _tool_definition(
        "knowledge_read",
        "Read one GPT-Knowledge document by canonical path (confined to the knowledge root).",
        {
            "reference": {"type": "string",
                          "description": "Canonical doc path, e.g. 000_START_HERE.md or "
                                         "ai-agents/unified-agent-engineering-methods.md."},
        },
        ["reference"],
    ),
    _tool_definition(
        "birdeye_search",
        "Rank-indexed search over the live SQLite index; every result carries "
        "root_class and source_class trust tags.",
        {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 200},
            "extensions": {"type": "string", "description": "Optional comma-separated file extensions to restrict."},
            "roots": {"type": "string", "description": "Optional comma-separated root names to restrict."},
        },
        ["query"],
    ),
    _tool_definition(
        "birdeye_inspect",
        "Read one indexed file by virtual path (root/relative).",
        {
            "path": {"type": "string", "description": "Virtual path, e.g. gpt-knowledge/knowledge-index.json."},
        },
        ["path"],
    ),
    _tool_definition(
        "birdeye_roots",
        "List configured knowledge roots with their root_class trust label.",
        {},
        [],
    ),
    _tool_definition(
        "birdeye_status",
        "SQLite index health for the shared state/workspace.db.",
        {},
        [],
    ),
]


def _send(message) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _text_content(value) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]


def serve_stdio() -> None:
    """Native MCP transport: JSON-RPC 2.0 over stdio, newline-delimited.

    Implements initialize / notifications/initialized / ping / tools/list /
    tools/call over the six proven functions. The --args CLI harness remains
    for diagnostics only.
    """
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            _send({
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "birdeye", "version": "0.1.0"},
                },
            })
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            if request_id is not None:
                _send({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": _TOOL_DEFINITIONS}})
        elif method == "tools/call":
            name = params.get("name")
            arguments = dict(params.get("arguments") or {})
            try:
                result = invoke(name, arguments)
                _send({"jsonrpc": "2.0", "id": request_id, "result": {"content": _text_content(result)}})
            except Exception as exc:  # noqa: BLE001 - transport surfaces any tool error
                _send({
                    "jsonrpc": "2.0", "id": request_id,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                    },
                })
        else:
            if request_id is not None:
                _send({
                    "jsonrpc": "2.0", "id": request_id,
                    "error": {"code": -32601, "message": f"method not found: {method}"},
                })


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BirdEye MCP surface (native stdio transport; --args diagnostic harness)")
    parser.add_argument("tool", nargs="?", choices=sorted(_TOOL_REGISTRY), help="tool name for the diagnostic --args harness")
    parser.add_argument("--args", help="JSON object of tool arguments (diagnostic harness)")
    parser.add_argument("--stdio", action="store_true", help="run the native MCP stdio transport")
    args = parser.parse_args(argv)
    if args.stdio:
        serve_stdio()
        return 0
    if not args.tool:
        parser.error("a tool name is required unless --stdio is used")
    params = json.loads(args.args) if args.args else {}
    try:
        result = invoke(args.tool, params)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (GovernanceError, FileNotFoundError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, indent=2))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

