#!/usr/bin/env python
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "bridge_config.json"
LOG_DIR = HERE / "logs"
BRIDGE_NAME = "local-mini-remote-bridge"

WRITE_TOOLS = {"write_text", "mkdir", "copy_file", "move_file", "delete_file"}
EXEC_TOOLS = {"run_process"}

REMOTE_WRITE_DISABLED = "REMOTE_WRITE_DISABLED"
REMOTE_EXEC_DISABLED = "REMOTE_EXEC_DISABLED"
TOOL_NOT_EXPOSED = "TOOL_NOT_EXPOSED"


def log(msg: str) -> None:
    print(f"[bridge] {msg}", file=sys.stderr, flush=True)


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().lower()


def verify_server(cfg: dict[str, Any]) -> Path:
    server = Path(cfg["server"]).resolve()
    if not server.is_file():
        raise SystemExit(f"missing MCP server: {server}")
    expected = str(cfg.get("server_sha256") or "").lower().strip()
    if not expected:
        raise SystemExit("bridge_config.json is missing server_sha256")
    actual = sha256_file(server)
    if actual != expected:
        raise SystemExit(
            "SERVER FILE HASH MISMATCH\n"
            f"  expected {expected}\n"
            f"  actual   {actual}\n"
            f"  path     {server}"
        )
    log(f"server hash ok: {actual[:16]}...")
    return server


def write_enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("write_enabled", False))


def exec_enabled(cfg: dict[str, Any]) -> bool:
    return bool(cfg.get("exec_enabled", False))


def audit(event: dict[str, Any]) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    with (LOG_DIR / "bridge_audit.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, separators=(",", ":"), default=str) + "\n")


def args_hash(args: Any) -> str:
    try:
        blob = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        blob = repr(args)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def policy_check(cfg: dict[str, Any], name: str) -> str | None:
    if name not in set(cfg.get("remote_tools", [])):
        return TOOL_NOT_EXPOSED
    if name in WRITE_TOOLS and not write_enabled(cfg):
        return REMOTE_WRITE_DISABLED
    if name in EXEC_TOOLS and not exec_enabled(cfg):
        return REMOTE_EXEC_DISABLED
    return None


async def run(cfg: dict[str, Any]) -> None:
    server_path = verify_server(cfg)

    token = os.environ.get("LOCAL_MINI_BRIDGE_TOKEN", "")
    if not token:
        raise SystemExit("LOCAL_MINI_BRIDGE_TOKEN is not set; refusing to start.")

    host = cfg.get("listen_host", "127.0.0.1")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"refusing non-loopback bind: {host!r}")

    port = int(cfg.get("listen_port", 8765))
    max_exec = max(1, min(int(cfg.get("max_exec_timeout_seconds", 300)), 600))
    roots = str(cfg.get("roots") or server_path.parent)

    child_env = dict(os.environ)
    child_env["MINI_MCP_ROOTS"] = roots
    child_env.pop("LOCAL_MINI_REMOTE_WRITE", None)
    child_env.pop("LOCAL_MINI_REMOTE_EXEC", None)

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.server.lowlevel import Server
    import mcp.types as types
    from starlette.responses import JSONResponse
    import uvicorn

    params = StdioServerParameters(
        command=cfg["python"], args=[cfg["server"]], env=child_env
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as upstream:
            await upstream.initialize()
            log("stdio child initialized")

            exposed = set(cfg.get("remote_tools", []))

            async def on_list_tools(ctx, params):
                result = await upstream.list_tools()
                keep = []
                for tool in result.tools:
                    if tool.name not in exposed:
                        continue
                    if tool.name in WRITE_TOOLS and not write_enabled(cfg):
                        continue
                    if tool.name in EXEC_TOOLS and not exec_enabled(cfg):
                        continue
                    keep.append(tool)
                return types.ListToolsResult(tools=keep)

            async def on_call_tool(ctx, params):
                name = params.name
                args = params.arguments or {}
                started = time.monotonic()

                denial = policy_check(cfg, name)
                if denial:
                    audit({
                        "event": "call_denied",
                        "tool": name,
                        "args_hash": args_hash(args),
                        "status": denial,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    })
                    raise ValueError(denial)

                call_args = dict(args)
                if name == "run_process":
                    requested = int(call_args.get("timeout_seconds", 60))
                    if requested > max_exec:
                        call_args["timeout_seconds"] = max_exec
                        audit({
                            "event": "timeout_capped",
                            "tool": name,
                            "args_hash": args_hash(args),
                            "requested": requested,
                            "applied": max_exec,
                        })

                try:
                    result = await upstream.call_tool(name, call_args)
                except Exception as exc:
                    audit({
                        "event": "call_error",
                        "tool": name,
                        "args_hash": args_hash(args),
                        "status": "error",
                        "error_type": type(exc).__name__,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                    })
                    raise

                is_error = bool(
                    getattr(result, "is_error", getattr(result, "isError", False))
                )
                audit({
                    "event": "call",
                    "tool": name,
                    "args_hash": args_hash(args),
                    "status": "error" if is_error else "ok",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                })
                return result

            server = Server(
                BRIDGE_NAME,
                version="1.2",
                on_list_tools=on_list_tools,
                on_call_tool=on_call_tool,
            )
            app = server.streamable_http_app(streamable_http_path="/mcp")

            prmd_paths = (
                "/.well-known/oauth-protected-resource",
                "/.well-known/oauth-protected-resource/mcp",
            )

            async def prmd(request):
                base = str(request.base_url).rstrip("/")
                return JSONResponse({
                    "resource": f"{base}/mcp",
                    "resource_name": BRIDGE_NAME,
                    "bearer_methods_supported": ["header"],
                })

            for path in prmd_paths:
                app.router.add_route(path, prmd, methods=["GET"])

            class Auth:
                def __init__(self, wrapped, bearer):
                    self.app = wrapped
                    self.token = bearer

                async def __call__(self, scope, receive, send):
                    if scope["type"] == "http" and scope.get("path", "") in prmd_paths:
                        await self.app(scope, receive, send)
                        return
                    if scope["type"] != "http":
                        await self.app(scope, receive, send)
                        return
                    headers = dict(scope.get("headers") or [])
                    raw = headers.get(b"authorization", b"").decode("latin-1")
                    if not secrets.compare_digest(raw, f"Bearer {self.token}"):
                        await JSONResponse(
                            {"error": "unauthorized"},
                            status_code=401,
                        )(scope, receive, send)
                        return
                    await self.app(scope, receive, send)

            async def health(request):
                return JSONResponse({
                    "ok": True,
                    "bridge": BRIDGE_NAME,
                    "write_enabled": write_enabled(cfg),
                    "exec_enabled": exec_enabled(cfg),
                    "roots": roots,
                    "server_sha256": cfg.get("server_sha256"),
                })

            app.router.add_route("/health", health, methods=["GET"])
            app = Auth(app, token)

            log(f"listening http://{host}:{port}/mcp")
            log(
                f"write_enabled={write_enabled(cfg)} "
                f"exec_enabled={exec_enabled(cfg)} roots={roots}"
            )
            await uvicorn.Server(
                uvicorn.Config(app, host=host, port=port, log_level="warning")
            ).serve()


if __name__ == "__main__":
    asyncio.run(run(load_config()))
