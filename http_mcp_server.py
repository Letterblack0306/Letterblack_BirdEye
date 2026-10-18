from __future__ import annotations

import asyncio
import hmac
import json
import os
import sys
from typing import Any

import mcp_server as bird
import loop_mcp_server as loops
import app_gateway
import llm_gateway_bridge


SERVER_NAME = "letterblack-birdeye"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8766
MCP_PATH = "/mcp"


def _is_loopback(host: str) -> bool:
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _configured_host_port() -> tuple[str, int]:
    host = os.environ.get("BIRDEYE_HTTP_HOST", DEFAULT_HOST).strip() or DEFAULT_HOST
    port = int(os.environ.get("BIRDEYE_HTTP_PORT", str(DEFAULT_PORT)))
    if not _is_loopback(host):
        raise RuntimeError(
            f"refusing non-loopback BirdEye HTTP bind: {host!r}; "
            "use a separate authenticated tunnel/relay for remote reachability"
        )
    if not 1 <= port <= 65535:
        raise RuntimeError(f"invalid BirdEye HTTP port: {port}")
    return host, port


def _tool_definitions() -> list[dict[str, Any]]:
    return list(bird._TOOL_DEFINITIONS) + [
        tool for tool in loops._LOOP_TOOLS if tool["name"].startswith("loop_")
    ]


def _invoke_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name in loops._LOOP_NAMES and name.startswith("loop_"):
        return loops.invoke(name, arguments)
    return bird.invoke(name, arguments)


def _tool_names() -> list[str]:
    return [str(tool["name"]) for tool in _tool_definitions()]


def _text_content(value: dict[str, Any]) -> list[dict[str, str]]:
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]


def _is_error(result: dict[str, Any]) -> bool:
    return result.get("ok") is False


def _close_legacy_memory_service() -> None:
    """Close only the legacy process-scoped memory service when one exists.

    Current memory access is thread-local in ``mcp_server``. Older runtimes
    exposed a module-level service, so shutdown must remain compatible without
    assuming that legacy attribute still exists.
    """
    memory_service = getattr(bird, "_memory_service", None)
    if memory_service is None:
        return
    try:
        memory_service.close()
    finally:
        bird._memory_service = None


def build_app():
    from mcp.server.lowlevel import Server
    import mcp.types as types
    from starlette.responses import JSONResponse

    async def on_list_tools(ctx, params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=str(tool["name"]),
                    description=str(tool.get("description") or ""),
                    inputSchema=dict(tool.get("inputSchema") or {"type": "object"}),
                )
                for tool in _tool_definitions()
            ]
        )

    async def on_call_tool(ctx, params):
        name = str(params.name)
        arguments = dict(params.arguments or {})
        result = await asyncio.to_thread(_invoke_tool, name, arguments)
        image_data = result.get("_image_base64") if isinstance(result, dict) else None
        if image_data:
            metadata = {key: value for key, value in result.items() if key != "_image_base64"}
            content = [
                types.TextContent(type="text", text=json.dumps(metadata, ensure_ascii=False)),
                types.ImageContent(type="image", data=image_data, mimeType="image/png"),
            ]
        else:
            content = [types.TextContent(type="text", text=_text_content(result)[0]["text"])]
        return types.CallToolResult(content=content, isError=_is_error(result))

    server = Server(
        SERVER_NAME,
        version="1.0",
        instructions=bird.BIRDEYE_MCP_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )
    app = server.streamable_http_app(streamable_http_path=MCP_PATH)

    async def health(request):
        return JSONResponse(
            {
                "ok": True,
                "server": SERVER_NAME,
                "schema_version": "1",
                "transport": "streamable-http",
                "mcp_path": MCP_PATH,
                "tool_count": len(_tool_names()),
                "auth": "bearer" if os.environ.get("BIRDEYE_HTTP_TOKEN") else "loopback-only",
            }
        )

    app.router.add_route("/health", health, methods=["GET"])
    app_gateway.register_routes(app)
    llm_gateway_bridge.register_routes(app)

    token = os.environ.get("BIRDEYE_HTTP_TOKEN", "")
    if not token:
        return app

    class BearerAuth:
        def __init__(self, wrapped, bearer: str):
            self.app = wrapped
            self.token = bearer

        async def __call__(self, scope, receive, send):
            path = scope.get("path") or ""
            if (scope["type"] != "http" or path in {"/health", "/", "/api/ping", "/v1", "/v1beta"}
                    or path.startswith(("/apps/", "/v1/", "/v1beta/"))):
                await self.app(scope, receive, send)
                return
            headers = dict(scope.get("headers") or [])
            raw = headers.get(b"authorization", b"").decode("latin-1")
            if not hmac.compare_digest(raw, f"Bearer {self.token}"):
                await JSONResponse({"error": "unauthorized"}, status_code=401)(
                    scope, receive, send
                )
                return
            await self.app(scope, receive, send)

    return BearerAuth(app, token)


async def run() -> None:
    host, port = _configured_host_port()

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError(
            "uvicorn is required for BirdEye Streamable HTTP transport"
        ) from exc

    watcher = None
    gateway = llm_gateway_bridge.ManagedGateway()
    process_lease = bird._McpProcessLease()
    try:
        if not process_lease.acquire():
            print(
                "[birdeye-http] another BirdEye MCP process owns the diagnostic marker; "
                "continuing without exclusive marker ownership",
                file=sys.stderr,
                flush=True,
            )
        try:
            watcher = bird.start_watcher(bird._load_ctx())
        except (bird.GovernanceError, OSError, RuntimeError, ValueError) as exc:
            print(
                f"[birdeye-http] watcher unavailable: {exc}",
                file=sys.stderr,
                flush=True,
            )

        try:
            gateway.start()
            if not await gateway.wait_ready():
                print("[birdeye-http] FreeLLMAPI did not become ready", file=sys.stderr, flush=True)
        except (OSError, RuntimeError) as exc:
            print(f"[birdeye-http] FreeLLMAPI unavailable: {exc}", file=sys.stderr, flush=True)

        app = build_app()
        print(
            f"[birdeye-http] listening http://{host}:{port}{MCP_PATH} "
            f"tools={len(_tool_names())}",
            file=sys.stderr,
            flush=True,
        )
        await uvicorn.Server(
            uvicorn.Config(app, host=host, port=port, log_level="warning")
        ).serve()
    finally:
        gateway.stop()
        bird.stop_watcher(watcher)
        process_lease.release()
        _close_legacy_memory_service()


def main() -> int:
    try:
        asyncio.run(run())
    except (RuntimeError, ValueError) as exc:
        print(f"[birdeye-http] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
