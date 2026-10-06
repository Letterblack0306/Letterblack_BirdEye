#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any


@asynccontextmanager
async def _http_client(headers: dict[str, str]):
    """Yield an httpx AsyncClient carrying the bearer token.

    The installed mcp SDK's streamable_http_client() accepts an http_client
    but no headers= argument, so auth must be attached to the client itself.
    """
    import httpx

    client = httpx.AsyncClient(headers=headers, timeout=60.0)
    try:
        yield client
    finally:
        await client.aclose()


def _payload(result: Any) -> dict[str, Any]:
    for attr in ("structured_content", "structuredContent"):
        value = getattr(result, attr, None)
        if isinstance(value, dict):
            return value
    for part in getattr(result, "content", []) or []:
        text = getattr(part, "text", None)
        if not text:
            continue
        try:
            value = json.loads(text)
        except Exception:
            continue
        if isinstance(value, dict):
            return value
    raise RuntimeError(f"tool result did not contain JSON: {result!r}")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    parser.add_argument("--root", required=True)
    parser.add_argument("--python", required=True)
    args = parser.parse_args()

    token = os.environ.get("LOCAL_MINI_BRIDGE_TOKEN", "")
    if not token:
        raise RuntimeError("LOCAL_MINI_BRIDGE_TOKEN is not set")

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {token}"}
    test_text = "local-mini-smoke\n"
    test_path = Path(args.root).resolve() / f".local-mini-smoke-{os.getpid()}.txt"
    expected_hash = hashlib.sha256(test_text.encode("utf-8")).hexdigest()

    required = {
        "health", "system_info", "list_drives", "list_dir", "read_text",
        "stat_path", "file_hash", "write_text", "mkdir", "copy_file",
        "move_file", "delete_file", "run_process",
    }

    async with _http_client(headers) as client:
        async with streamable_http_client(args.url, http_client=client) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                listed = await session.list_tools()
                names = {tool.name for tool in listed.tools}
                missing = sorted(required - names)
                if missing:
                    raise RuntimeError("tools/list missing: " + ",".join(missing))

                try:
                    result = _payload(await session.call_tool(
                        "write_text",
                        {"path": str(test_path), "text": test_text, "overwrite": True},
                    ))
                    if not result.get("ok"):
                        raise RuntimeError(f"write_text failed: {result}")

                    result = _payload(await session.call_tool(
                        "file_hash", {"path": str(test_path)}
                    ))
                    if not result.get("ok") or result.get("sha256") != expected_hash:
                        raise RuntimeError(f"file_hash mismatch: {result}")

                    result = _payload(await session.call_tool(
                        "run_process",
                        {
                            "executable": args.python,
                            "args": ["-c", "print('SMOKE_EXEC_OK')"],
                            "cwd": str(Path(args.root).resolve()),
                            "timeout_seconds": 20,
                        },
                    ))
                    if not result.get("ok") or "SMOKE_EXEC_OK" not in result.get("stdout", ""):
                        raise RuntimeError(f"run_process failed: {result}")
                finally:
                    try:
                        await session.call_tool("delete_file", {"path": str(test_path)})
                    except Exception:
                        pass

    print("MCP_SMOKE=PASS")
    print("TOOLS_LIST=" + ",".join(sorted(required)))


if __name__ == "__main__":
    asyncio.run(main())
