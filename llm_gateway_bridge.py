"""Manage the local FreeLLMAPI runtime and expose its wire APIs on BirdEye."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import psutil
import socket
import sqlite3
import subprocess

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse


RUNTIME = Path(__file__).resolve().parent.parent / "freellmapi-sharekit" / "runtime"
PORT_REGISTRY = Path(__file__).resolve().parent / "managed_child_ports.json"


def _registered_port() -> int:
    registry = json.loads(PORT_REGISTRY.read_text(encoding="utf-8"))
    if registry.get("front_door") != 8766 or registry.get("reserved_range") != [8767, 8799]:
        raise RuntimeError("BirdEye child port registry has an invalid reserved block")
    services = registry.get("services")
    if not isinstance(services, dict) or not services:
        raise RuntimeError("BirdEye child port registry has no services")
    ports = list(services.values())
    if any(type(port) is not int or not 8767 <= port <= 8799 for port in ports) or len(ports) != len(set(ports)):
        raise RuntimeError("BirdEye child port registry has an invalid or duplicate port")
    port = services.get("freellmapi")
    if port is None:
        raise RuntimeError("FreeLLMAPI is missing from BirdEye child port registry")
    return port


PORT = _registered_port()
UPSTREAM = f"http://127.0.0.1:{PORT}"
LOG_DIR = Path(__file__).resolve().parent / "state" / "llm_gateway"
_HOP_HEADERS = {
    "connection", "content-length", "host", "keep-alive", "proxy-authenticate",
    "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade",
}


class ManagedGateway:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self._log = None
        self._job = None

    def start(self) -> None:
        entry = RUNTIME / "server" / "dist" / "index.js"
        env_file = RUNTIME / ".env"
        if not entry.is_file() or not env_file.is_file():
            raise RuntimeError(f"FreeLLMAPI runtime is incomplete at {RUNTIME}")
        env_settings = dict(
            line.split("=", 1) for line in env_file.read_text(encoding="utf-8-sig").splitlines()
            if "=" in line and not line.lstrip().startswith("#")
        )
        if env_settings.get("PORT") != str(PORT) or env_settings.get("HOST") != "127.0.0.1":
            raise RuntimeError(f"FreeLLMAPI .env must set PORT={PORT} and HOST=127.0.0.1")
        owners = [connection.pid for connection in psutil.net_connections(kind="tcp")
                  if connection.status == psutil.CONN_LISTEN and connection.laddr.port == PORT]
        if owners:
            raise RuntimeError(f"FreeLLMAPI port {PORT} in use by PID {owners[0] or 'unknown'}")
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", PORT)) == 0:
                raise RuntimeError(f"FreeLLMAPI port {PORT} in use by PID unknown")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        self._log = (LOG_DIR / "runtime.log").open("ab", buffering=0)
        env = os.environ.copy()
        env.update({
            "HOST": "127.0.0.1",
            "PORT": str(PORT),
            "FREEAPI_ENV_PATH": str(env_file),
        })
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            ["node", str(entry)], cwd=RUNTIME, env=env,
            stdin=subprocess.DEVNULL, stdout=self._log, stderr=subprocess.STDOUT,
            creationflags=flags,
        )
        if os.name == "nt":
            # Task Scheduler can terminate pythonw without running finally.
            # A Windows job closes with BirdEye and kills its managed child.
            import win32api
            import win32con
            import win32job

            try:
                job = win32job.CreateJobObject(None, "")
                limits = win32job.QueryInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation
                )
                limits["BasicLimitInformation"]["LimitFlags"] |= (
                    win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                )
                win32job.SetInformationJobObject(
                    job, win32job.JobObjectExtendedLimitInformation, limits
                )
                handle = win32api.OpenProcess(win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE, False, self.process.pid)
                try:
                    win32job.AssignProcessToJobObject(job, handle)
                finally:
                    win32api.CloseHandle(handle)
                self._job = job
            except Exception:
                self.stop()
                raise

    async def wait_ready(self, seconds: float = 30) -> bool:
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(int(seconds * 4)):
                if self.process is None or self.process.poll() is not None:
                    return False
                try:
                    response = await client.get(f"{UPSTREAM}/api/ping")
                    if response.status_code == 200:
                        return True
                except httpx.RequestError:
                    pass
                await asyncio.sleep(0.25)
        return False

    def stop(self) -> None:
        if self._job is not None:
            import win32api

            win32api.CloseHandle(self._job)
            self._job = None
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._log is not None:
            self._log.close()
        self.process = None
        self._log = None


async def proxy(request: Request):
    """Preserve FreeLLMAPI authentication, status, and SSE bytes."""
    path = request.url.path
    url = f"{UPSTREAM}{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    headers = {key: value for key, value in request.headers.items()
               if key.lower() not in _HOP_HEADERS}
    client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=None, write=60, pool=5))
    try:
        upstream_request = client.build_request(
            request.method, url, headers=headers, content=await request.body()
        )
        upstream = await client.send(upstream_request, stream=True)
    except httpx.RequestError:
        await client.aclose()
        return JSONResponse({"error": "llm_gateway_unavailable"}, status_code=503)

    async def chunks():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response_headers = {key: value for key, value in upstream.headers.items()
                        if key.lower() not in _HOP_HEADERS}
    return StreamingResponse(chunks(), status_code=upstream.status_code,
                             headers=response_headers)


def register_routes(app) -> None:
    for prefix in ("/v1", "/v1beta"):
        app.router.add_route(prefix, proxy, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
        app.router.add_route(f"{prefix}/{{path:path}}", proxy,
                             methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
    app.router.add_route("/api/ping", proxy, methods=["GET"])


def _unified_key() -> str:
    db_file = RUNTIME / "server" / "data" / "freeapi.db"
    if not db_file.is_file():
        raise RuntimeError("FreeLLMAPI database is missing")
    with sqlite3.connect(str(db_file)) as db:
        row = db.execute(
            "SELECT value FROM settings WHERE key = ?", ("unified_api_key",)
        ).fetchone()
    if not row or not isinstance(row[0], str) or not row[0]:
        raise RuntimeError("FreeLLMAPI unified key is unavailable")
    return row[0]


def invoke_tool(name: str, params: dict) -> dict:
    """Small MCP surface over the existing FreeLLMAPI routing owner."""
    try:
        with httpx.Client(timeout=60) as client:
            if name == "llm_status":
                response = client.get(f"{UPSTREAM}/api/ping")
                return {"ok": response.status_code == 200,
                        "gateway": "freellmapi", "status_code": response.status_code}

            headers = {"Authorization": f"Bearer {_unified_key()}"}
            if name == "llm_models":
                response = client.get(f"{UPSTREAM}/v1/models", headers=headers)
                response.raise_for_status()
                models = response.json().get("data", [])
                limit = params.get("limit", 50)
                if type(limit) is not int or not 1 <= limit <= 200:
                    raise ValueError("limit must be between 1 and 200")
                return {"ok": True, "count": len(models),
                        "models": [item.get("id") for item in models[:limit]]}

            if name == "llm_generate":
                prompt = params.get("prompt")
                model = params.get("model", "auto")
                max_tokens = params.get("max_tokens", 256)
                if not isinstance(prompt, str) or not 1 <= len(prompt) <= 20000:
                    raise ValueError("prompt length must be 1-20000")
                if not isinstance(model, str) or not 1 <= len(model) <= 200:
                    raise ValueError("model length must be 1-200")
                if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
                    raise ValueError("max_tokens must be 1-4096")
                response = client.post(
                    f"{UPSTREAM}/v1/chat/completions", headers=headers,
                    json={"model": model, "max_tokens": max_tokens,
                          "messages": [{"role": "user", "content": prompt}]},
                )
                response.raise_for_status()
                result = response.json()
                choices = result.get("choices") or []
                if not choices:
                    return {"ok": False, "error": "empty_gateway_response"}
                return {"ok": True, "model": result.get("model"),
                        "text": choices[0].get("message", {}).get("content", ""),
                        "usage": result.get("usage")}

        raise ValueError(f"unknown LLM tool: {name}")
    except (httpx.HTTPError, sqlite3.Error, RuntimeError, ValueError) as exc:
        if isinstance(exc, httpx.HTTPStatusError):
            return {"ok": False, "error": "gateway_http_error",
                    "status_code": exc.response.status_code}
        return {"ok": False, "error": type(exc).__name__, "message": str(exc)}
