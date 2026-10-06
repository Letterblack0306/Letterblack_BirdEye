#!/usr/bin/env python
from __future__ import annotations

import argparse
import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def _http_client(headers: dict[str, str]):
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


async def run() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end smoke for the canonical full BirdEye HTTP MCP endpoint."
    )
    parser.add_argument("--url", default="http://127.0.0.1:8766/mcp")
    args = parser.parse_args()

    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    required = {
        "birdeye_status",
        "birdeye_roots",
        "birdeye_search",
        "birdeye_inspect",
        "knowledge_route",
        "knowledge_read",
        "memory_recall",
        "skills",
        "workspace_identity",
        "revision_status",
        "workspace_run",
        "workspace_run_sequence",
    }

    token = os.environ.get("BIRDEYE_HTTP_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

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

                status = _payload(await session.call_tool("birdeye_status", {}))
                if status.get("ok") is not True:
                    raise RuntimeError(f"birdeye_status failed: {status}")

    print("BIRDEYE_HTTP_SMOKE=PASS")
    print("ENDPOINT=" + args.url)
    print("TOOLS_PRESENT=" + ",".join(sorted(required)))


if __name__ == "__main__":
    asyncio.run(run())
