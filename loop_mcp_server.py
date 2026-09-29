from __future__ import annotations

import argparse
import json
import sys
import threading
from queue import Queue

import mcp_server as bird
from loop_registry import LoopRegistry


LOOP_REGISTRY_PATH = bird.BIRDEYE_DIR / "state" / "loops.json"
_LOOP_REGISTRY = LoopRegistry(LOOP_REGISTRY_PATH)

_LOOP_TOOLS = [
    {
        "name": "loop_register",
        "description": "Register or replace a ChatGPT command-loop endpoint. Thread routing state is separate from BirdEye indexed file/SHA records.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "loop_id": {"type": "string", "description": "Stable loop identifier. Omit to generate one."},
                "thread_url": {"type": "string", "description": "Exact ChatGPT conversation URL served by this loop."},
                "browser_profile": {"type": "string"},
                "cdp_endpoint": {"type": "string"},
                "page_id": {"type": "string"},
                "workspace": {"type": "string"},
                "status": {"type": "string", "enum": ["waiting", "executing", "stopped"]},
            },
            "required": ["thread_url"],
        },
    },
    {
        "name": "loop_get",
        "description": "Read one registered ChatGPT loop endpoint.",
        "inputSchema": {
            "type": "object",
            "properties": {"loop_id": {"type": "string"}},
            "required": ["loop_id"],
        },
    },
    {
        "name": "loop_list",
        "description": "List registered ChatGPT loop endpoints, optionally filtered by status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["waiting", "executing", "stopped"]},
            },
            "required": [],
        },
    },
    {
        "name": "loop_update",
        "description": "Update routing/runtime state for an existing ChatGPT loop. Does not create or modify BirdEye file hashes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "loop_id": {"type": "string"},
                "thread_url": {"type": "string"},
                "browser_profile": {"type": "string"},
                "cdp_endpoint": {"type": "string"},
                "page_id": {"type": "string"},
                "workspace": {"type": "string"},
                "status": {"type": "string", "enum": ["waiting", "executing", "stopped"]},
                "last_message_hash": {"type": "string"},
                "last_action_id": {"type": "string"},
            },
            "required": ["loop_id"],
        },
    },
    {
        "name": "loop_remove",
        "description": "Remove one registered ChatGPT loop endpoint.",
        "inputSchema": {
            "type": "object",
            "properties": {"loop_id": {"type": "string"}},
            "required": ["loop_id"],
        },
    },
]

_LOOP_NAMES = {tool["name"] for tool in _LOOP_TOOLS}


def invoke(tool: str, params: dict) -> dict:
    if tool == "loop_register":
        return _LOOP_REGISTRY.register(
            loop_id=params.get("loop_id"),
            thread_url=params.get("thread_url", ""),
            browser_profile=params.get("browser_profile"),
            cdp_endpoint=params.get("cdp_endpoint"),
            page_id=params.get("page_id"),
            workspace=params.get("workspace"),
            status=params.get("status", "waiting"),
        )
    if tool == "loop_get":
        return _LOOP_REGISTRY.get(str(params.get("loop_id", "")))
    if tool == "loop_list":
        return _LOOP_REGISTRY.list(status=params.get("status"))
    if tool == "loop_update":
        return _LOOP_REGISTRY.update(
            str(params.get("loop_id", "")),
            thread_url=params.get("thread_url"),
            browser_profile=params.get("browser_profile"),
            cdp_endpoint=params.get("cdp_endpoint"),
            page_id=params.get("page_id"),
            workspace=params.get("workspace"),
            status=params.get("status"),
            last_message_hash=params.get("last_message_hash"),
            last_action_id=params.get("last_action_id"),
        )
    if tool == "loop_remove":
        return _LOOP_REGISTRY.remove(str(params.get("loop_id", "")))
    return bird.invoke(tool, params)


def _send(message: dict) -> None:
    sys.stdout.buffer.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def _text_content(value: dict) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]


def _is_error(result: dict) -> bool:
    return result.get("ok") is False


def serve_stdio() -> None:
    watcher = None
    process_lease = bird._McpProcessLease()
    reader_queue: Queue[str | None] = Queue()

    def read_stdin() -> None:
        try:
            for line in sys.stdin:
                reader_queue.put(line)
        finally:
            reader_queue.put(None)

    try:
        if not process_lease.acquire():
            print(
                "[birdeye-loop] another BirdEye MCP process owns the diagnostic marker; continuing without it",
                file=sys.stderr,
                flush=True,
            )
        try:
            watcher = bird.start_watcher(bird._load_ctx())
        except (bird.GovernanceError, OSError, RuntimeError, ValueError) as exc:
            print(f"[birdeye-loop] watcher unavailable: {exc}", file=sys.stderr, flush=True)

        reader = threading.Thread(target=read_stdin, name="birdeye-loop-stdio-reader", daemon=True)
        reader.start()

        while True:
            line = reader_queue.get()
            if line is None:
                break
            try:
                message = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue

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
                        "serverInfo": {"name": "birdeye-loop", "version": "0.1.0"},
                    },
                })
                continue

            if method == "notifications/initialized":
                continue

            if method == "ping":
                if request_id is not None:
                    _send({"jsonrpc": "2.0", "id": request_id, "result": {}})
                continue

            if method == "shutdown":
                if request_id is not None:
                    _send({"jsonrpc": "2.0", "id": request_id, "result": {}})
                break

            if method == "notifications/exit":
                break

            if method == "tools/list":
                _send({
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": bird._TOOL_DEFINITIONS + _LOOP_TOOLS},
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
                            "error": {
                                "code": -32602,
                                "message": "Invalid params: name and arguments are required",
                            },
                        })
                    continue
                try:
                    result = invoke(name, arguments)
                except (bird.BridgeError, bird.GovernanceError, ValueError, OSError) as exc:
                    result = {"ok": False, "error": type(exc).__name__, "message": str(exc)}
                if request_id is not None:
                    _send({
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": _text_content(result),
                            "isError": _is_error(result),
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
        bird.stop_watcher(watcher)
        process_lease.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BirdEye MCP with persistent ChatGPT loop routing")
    parser.add_argument("--stdio", action="store_true", help="run the MCP stdio transport")
    args = parser.parse_args(argv)
    if not args.stdio:
        parser.error("--stdio is required")
    serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
