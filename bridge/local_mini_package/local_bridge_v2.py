#!/usr/bin/env python
"""Authenticated Streamable-HTTP policy bridge for local-mini v2."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
DEFAULT_CONFIG = HERE / "bridge_config.json"
PRMD_PATHS = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/mcp",
)

WRITE_TOOLS = {"write_text", "patch_text"}
EXEC_TOOLS = {"run_process", "process_start", "process_stop"}


def _load_config() -> tuple[Path, dict[str, Any]]:
    cfg_path = Path(
        os.environ.get("LOCAL_MINI_BRIDGE_CONFIG", str(DEFAULT_CONFIG))
    ).expanduser().resolve()
    return cfg_path, json.loads(cfg_path.read_text(encoding="utf-8"))


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _gate(cfg: dict[str, Any], name: str) -> str | None:
    if name not in set(cfg.get("remote_tools", [])):
        return "TOOL_NOT_EXPOSED"
    if name in WRITE_TOOLS and not bool(cfg.get("write_enabled", False)):
        return "REMOTE_WRITE_DISABLED"
    if name in EXEC_TOOLS and not bool(cfg.get("exec_enabled", False)):
        return "REMOTE_EXEC_DISABLED"
    return None


def _audit(log_dir: Path, event: dict[str, Any]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    with (log_dir / "bridge_audit.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                record, sort_keys=True, separators=(",", ":"), default=str
            )
            + "\n"
        )


def _args_hash(args: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            args, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()[:16]


async def run() -> None:
    cfg_path, cfg = _load_config()
    server = Path(cfg["server"]).expanduser().resolve()
    if not server.is_file():
        raise SystemExit(f"server not found: {server}")

    expected = str(cfg.get("expected_server_sha256", "")).lower().strip()
    actual = _sha256_file(server)
    if not expected:
        raise SystemExit("expected_server_sha256 is required")
    if actual != expected:
        raise SystemExit(
            f"SERVER HASH MISMATCH expected={expected} actual={actual}"
        )

    token = os.environ.get("LOCAL_MINI_BRIDGE_TOKEN", "")
    if not token:
        raise SystemExit("LOCAL_MINI_BRIDGE_TOKEN is not set")

    host = str(cfg.get("listen_host", "127.0.0.1"))
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit(f"refusing non-loopback bind: {host}")
    port = int(cfg.get("listen_port", 8765))
    log_dir = cfg_path.parent / "logs"

    child_env = dict(os.environ)
    child_env["MINI_MCP_READ_ROOTS"] = ";".join(
        cfg.get("read_roots", ["*"])
    )
    child_env["MINI_MCP_WRITE_ROOTS"] = ";".join(
        cfg.get("write_roots", [])
    )
    child_env["MINI_MCP_EXEC_ALLOW"] = ";".join(
        cfg.get("exec_allow", [])
    )

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.server.lowlevel import Server
    import mcp.types as types
    from starlette.responses import JSONResponse
    import uvicorn

    params = StdioServerParameters(
        command=cfg["python"],
        args=[str(server)],
        env=child_env,
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as upstream:
            await upstream.initialize()

            async def on_list_tools(ctx, params):
                result = await upstream.list_tools()
                return types.ListToolsResult(
                    tools=[
                        t
                        for t in result.tools
                        if _gate(cfg, t.name) is None
                    ]
                )

            async def on_call_tool(ctx, params):
                name = params.name
                args = params.arguments or {}
                started = time.monotonic()
                denial = _gate(cfg, name)
                if denial:
                    _audit(
                        log_dir,
                        {
                            "event": "call_denied",
                            "tool": name,
                            "args_hash": _args_hash(args),
                            "status": denial,
                        },
                    )
                    raise ValueError(denial)
                try:
                    result = await upstream.call_tool(name, args)
                except Exception as exc:
                    _audit(
                        log_dir,
                        {
                            "event": "call_error",
                            "tool": name,
                            "args_hash": _args_hash(args),
                            "error_type": type(exc).__name__,
                            "duration_ms": int(
                                (time.monotonic() - started) * 1000
                            ),
                        },
                    )
                    raise
                _audit(
                    log_dir,
                    {
                        "event": "call",
                        "tool": name,
                        "args_hash": _args_hash(args),
                        "duration_ms": int(
                            (time.monotonic() - started) * 1000
                        ),
                    },
                )
                return result

            mcp_server = Server(
                "local-mini-remote-bridge-v2",
                version="2.0",
                on_list_tools=on_list_tools,
                on_call_tool=on_call_tool,
            )
            app = mcp_server.streamable_http_app(
                streamable_http_path="/mcp"
            )

            async def prmd(request):
                base = str(request.base_url).rstrip("/")
                return JSONResponse(
                    {
                        "resource": f"{base}/mcp",
                        "resource_name": "local-mini-remote-bridge-v2",
                        "bearer_methods_supported": ["header"],
                    }
                )

            for path in PRMD_PATHS:
                app.router.add_route(path, prmd, methods=["GET"])

            async def health(request):
                return JSONResponse(
                    {
                        "ok": True,
                        "bridge": "local-mini-remote-bridge-v2",
                        "server_sha256": actual,
                        "write_enabled": bool(
                            cfg.get("write_enabled", False)
                        ),
                        "exec_enabled": bool(
                            cfg.get("exec_enabled", False)
                        ),
                    }
                )

            app.router.add_route("/health", health, methods=["GET"])

            class Auth:
                def __init__(self, inner, expected_token: str):
                    self.inner = inner
                    self.expected_token = expected_token

                async def __call__(self, scope, receive, send):
                    if (
                        scope["type"] == "http"
                        and scope.get("path", "") in PRMD_PATHS
                    ):
                        return await self.inner(scope, receive, send)
                    if scope["type"] != "http":
                        return await self.inner(scope, receive, send)
                    headers = dict(scope.get("headers") or [])
                    raw = headers.get(
                        b"authorization", b""
                    ).decode("latin-1")
                    if not secrets.compare_digest(
                        raw, f"Bearer {self.expected_token}"
                    ):
                        _audit(
                            log_dir,
                            {
                                "event": "auth_rejected",
                                "path": scope.get("path", ""),
                            },
                        )
                        meta = (
                            f"http://{host}:{port}"
                            "/.well-known/oauth-protected-resource/mcp"
                        )
                        return await JSONResponse(
                            {"error": "unauthorized"},
                            status_code=401,
                            headers={
                                "WWW-Authenticate":
                                    f'Bearer resource_metadata="{meta}"'
                            },
                        )(scope, receive, send)
                    return await self.inner(scope, receive, send)

            app = Auth(app, token)
            await uvicorn.Server(
                uvicorn.Config(
                    app, host=host, port=port, log_level="warning"
                )
            ).serve()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
