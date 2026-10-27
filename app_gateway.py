from __future__ import annotations

import asyncio
import atexit
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import websockets
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.websockets import WebSocket, WebSocketDisconnect


@dataclass(frozen=True)
class AppSpec:
    id: str
    name: str
    description: str
    path: str
    workspace: Path
    command: tuple[str, ...]


APPS: dict[str, AppSpec] = {
    "effects": AppSpec(
        id="effects",
        name="LB Effects Editor",
        description="Agent-driven motion composition and effects editor.",
        path="/apps/effects/",
        workspace=Path(r"D:\Developement\_Web Animator"),
        command=("cmd.exe", "/c", "npm", "run", "dev"),
    ),
}

_processes: dict[str, subprocess.Popen[Any]] = {}
_ports: dict[str, int] = {}
_start_lock = asyncio.Lock()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _running(app_id: str) -> bool:
    proc = _processes.get(app_id)
    return bool(proc and proc.poll() is None and _ports.get(app_id))


async def _wait_ready(port: int, timeout: float = 25.0) -> None:
    deadline = time.monotonic() + timeout
    async with httpx.AsyncClient(timeout=1.5) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(f"http://127.0.0.1:{port}/")
                if response.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.25)
    raise RuntimeError(f"app did not become ready on internal port {port}")


async def ensure_app(app_id: str) -> int:
    spec = APPS[app_id]
    if _running(app_id):
        return _ports[app_id]

    async with _start_lock:
        if _running(app_id):
            return _ports[app_id]

        if not spec.workspace.exists():
            raise RuntimeError(f"workspace not found: {spec.workspace}")

        port = _free_port()
        env = os.environ.copy()
        env.update(
            {
                "PORT": str(port),
                "HOST": "127.0.0.1",
                "FRAME_EDIT_BASE_PATH": spec.path,
                "DISABLE_HMR": "true",
            }
        )
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            list(spec.command),
            cwd=str(spec.workspace),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        _processes[app_id] = proc
        _ports[app_id] = port
        try:
            await _wait_ready(port)
        except Exception:
            proc.terminate()
            _processes.pop(app_id, None)
            _ports.pop(app_id, None)
            raise
        return port


def stop_apps() -> None:
    for proc in list(_processes.values()):
        if proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass
    _processes.clear()
    _ports.clear()


atexit.register(stop_apps)


def dashboard_html() -> str:
    cards = "".join(
        (
            '<a class="card" href="' + spec.path + '">'
            '<div class="icon">LB</div>'
            '<div class="copy"><strong>' + spec.name + '</strong><span>' + spec.description + '</span></div>'
            '<span class="arrow">↗</span></a>'
        )
        for spec in APPS.values()
    )
    cards += ('<a class="card" href="/apps/birdeye-control/">'
              '<div class="icon" style="background:#245c58">BE</div>'
              '<div class="copy"><strong>BirdEye Control Center</strong>'
              '<span>Workspaces, tools, skills, execution receipts and configuration.</span></div>'
              '<span class="arrow">↗</span></a>')
    return """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>LetterBlack</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0d0d0f;color:#ececf0;font:14px Inter,Segoe UI,sans-serif}
main{max-width:760px;margin:0 auto;padding:64px 24px}header{margin-bottom:24px}h1{font-size:20px;margin:0 0 6px}p{margin:0;color:#74747d;font-size:12px}
.grid{display:grid;gap:10px}.card{display:flex;align-items:center;gap:14px;padding:16px;border:1px solid #25252a;border-radius:12px;background:#151518;color:inherit;text-decoration:none;transition:.15s}
.card:hover{background:#1b1b1f;border-color:#3b3b44;transform:translateY(-1px)}.icon{width:38px;height:38px;border-radius:9px;display:grid;place-items:center;background:#343c8e;color:white;font-weight:800}
.copy{flex:1;display:flex;flex-direction:column;gap:4px}.copy strong{font-size:13px}.copy span{color:#777780;font-size:11px}.arrow{color:#62626b}
footer{margin-top:22px;color:#505059;font:10px ui-monospace,monospace}code{color:#85858f}
</style>
</head>
<body><main><header><h1>LetterBlack</h1><p>Local tools through one gateway.</p></header>
<div class="grid">""" + cards + """</div>
<footer>MCP <code>/mcp</code> · Health <code>/health</code></footer>
</main></body></html>"""


async def dashboard(_request: Request) -> HTMLResponse:
    return HTMLResponse(dashboard_html())


async def app_status(_request: Request) -> JSONResponse:
    return JSONResponse({"ok": True, "apps": [
        {"id": spec.id, "name": spec.name, "path": spec.path, "running": _running(spec.id)}
        for spec in APPS.values()
    ]})

_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


async def proxy_http(request: Request) -> Response:
    app_id = str(request.path_params["app_id"])
    if app_id not in APPS:
        return JSONResponse({"error": "unknown app"}, status_code=404)

    port = await ensure_app(app_id)
    tail = str(request.path_params.get("path") or "")
    target_path = "/" + tail if tail else "/"
    query = request.url.query
    url = f"http://127.0.0.1:{port}{target_path}" + (("?" + query) if query else "")

    headers = {key: value for key, value in request.headers.items() if key.lower() not in _HOP_HEADERS}
    body = await request.body()

    async with httpx.AsyncClient(follow_redirects=False, timeout=120.0) as client:
        upstream = await client.request(request.method, url, headers=headers, content=body)

    response_headers = {
        key: value
        for key, value in upstream.headers.items()
        if key.lower() not in _HOP_HEADERS and key.lower() != "content-encoding"
    }
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


async def proxy_websocket(websocket: WebSocket) -> None:
    app_id = str(websocket.path_params["app_id"])
    if app_id not in APPS:
        await websocket.close(code=1008)
        return

    port = await ensure_app(app_id)
    tail = str(websocket.path_params.get("path") or "ws")
    upstream_url = f"ws://127.0.0.1:{port}/" + tail.lstrip("/")

    await websocket.accept()
    try:
        async with websockets.connect(upstream_url, max_size=None) as upstream:
            async def client_to_upstream() -> None:
                while True:
                    message = await websocket.receive()
                    if message["type"] == "websocket.disconnect":
                        return
                    if message.get("text") is not None:
                        await upstream.send(message["text"])
                    elif message.get("bytes") is not None:
                        await upstream.send(message["bytes"])

            async def upstream_to_client() -> None:
                async for message in upstream:
                    if isinstance(message, bytes):
                        await websocket.send_bytes(message)
                    else:
                        await websocket.send_text(message)

            tasks = [
                asyncio.create_task(client_to_upstream()),
                asyncio.create_task(upstream_to_client()),
            ]
            _done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except (WebSocketDisconnect, websockets.ConnectionClosed):
        pass
    finally:
        try:
            await websocket.close()
        except RuntimeError:
            pass


def register_routes(app: Any) -> None:
    methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    from control_center_api import snapshot, skill_query, history, config, workspace, receipt, storage, git_history, git_commit, github_work, registry, upload_file
    app.router.add_route("/", dashboard, methods=["GET"])
    from starlette.responses import FileResponse
    app.router.add_route("/apps/birdeye-control/", lambda request: HTMLResponse((Path(__file__).with_name("control_center.html")).read_text(encoding="utf-8")), methods=["GET"])
    app.router.add_route("/apps/birdeye-control", lambda request: HTMLResponse((Path(__file__).with_name("control_center.html")).read_text(encoding="utf-8")), methods=["GET"])
    # The Control Center is a native BirdEye app, not an APPS reverse-proxy target.
    # Normalize alternate browser/launcher entrypoints before generic /apps/{app_id}.
    from starlette.responses import RedirectResponse
    async def control_center_alias(request: Request) -> Response:
        suffix = str(request.path_params.get("path") or "").strip("/")
        if suffix not in ("", "index.html"):
            return JSONResponse({"error": "unknown Control Center route"}, status_code=404)
        return RedirectResponse(url="/apps/birdeye-control/" + (("?" + request.url.query) if request.url.query else ""), status_code=307)
    app.router.add_route("/apps/birdeye-control/{path:path}", control_center_alias, methods=["GET"])

    app.router.add_route("/control.css", lambda request: FileResponse(Path(__file__).with_name("control_center.css"), media_type="text/css", headers={"Cache-Control":"no-store"}), methods=["GET"])
    app.router.add_route("/control.js", lambda request: FileResponse(Path(__file__).with_name("control_center.js"), media_type="text/javascript", headers={"Cache-Control":"no-store"}), methods=["GET"])
    app.router.add_route("/control/api/snapshot", snapshot, methods=["GET"])
    app.router.add_route("/control/api/skills", skill_query, methods=["GET"])
    app.router.add_route("/control/api/history", history, methods=["GET"])
    app.router.add_route("/control/api/workspace", workspace, methods=["GET"])
    app.router.add_route("/control/api/git", git_history, methods=["GET"])
    app.router.add_route("/control/api/github", github_work, methods=["GET"])
    app.router.add_route("/control/api/git/commit", git_commit, methods=["GET"])
    app.router.add_route("/control/api/receipt", receipt, methods=["GET"])
    app.router.add_route("/control/api/storage", storage, methods=["GET"])
    app.router.add_route("/control/api/config", config, methods=["GET", "POST"])
    app.router.add_route("/control/api/registry", registry, methods=["GET", "POST"])
    app.router.add_route("/control/api/upload", upload_file, methods=["POST"])
    app.router.add_route("/apps/status", app_status, methods=["GET"])
    app.router.add_route("/apps/{app_id}", proxy_http, methods=methods)
    app.router.add_route("/apps/{app_id}/{path:path}", proxy_http, methods=methods)
    app.router.add_websocket_route("/apps/{app_id}/{path:path}", proxy_websocket)
