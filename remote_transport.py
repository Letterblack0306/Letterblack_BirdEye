from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path


BIRDEYE_DIR = Path(__file__).resolve().parent
STATE_FILE = BIRDEYE_DIR / "state" / "access_tunnel.json"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _load_lease() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"open": False, "last_activity": 0}
    return {
        "open": bool(data.get("open", False)),
        "last_activity": float(data.get("last_activity", 0) or 0),
    }


def lease_is_open(now: float | None = None, idle_timeout_seconds: int = 1800) -> bool:
    state = _load_lease()
    if not state["open"]:
        return False
    now = time.time() if now is None else now
    return (now - state["last_activity"]) <= idle_timeout_seconds


def _token() -> str:
    value = os.environ.get("BIRDEYE_REMOTE_TOKEN", "")
    if not value:
        raise RuntimeError("BIRDEYE_REMOTE_TOKEN is required")
    return value


def _constant_time_match(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _spawn_mcp() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, str(BIRDEYE_DIR / "loop_mcp_server.py"), "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(BIRDEYE_DIR),
        bufsize=0,
    )


def _relay(src, dst) -> None:
    try:
        while True:
            chunk = src.read(65536)
            if not chunk:
                break
            dst.write(chunk)
            dst.flush()
    except (BrokenPipeError, ConnectionResetError, OSError):
        pass


def handle_client(conn: socket.socket, idle_timeout_seconds: int) -> None:
    conn_file = conn.makefile("rwb", buffering=0)
    try:
        if not lease_is_open(idle_timeout_seconds=idle_timeout_seconds):
            conn_file.write(b'{"ok":false,"error":"access_closed"}\n')
            return

        first = conn_file.readline(8192)
        if not first:
            return
        try:
            hello = json.loads(first.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            conn_file.write(b'{"ok":false,"error":"invalid_handshake"}\n')
            return

        provided = str(hello.get("token", ""))
        if not _constant_time_match(provided, _token()):
            conn_file.write(b'{"ok":false,"error":"unauthorized"}\n')
            return

        conn_file.write(b'{"ok":true,"transport":"birdeye-mcp-stdio"}\n')
        proc = _spawn_mcp()
        assert proc.stdin is not None
        assert proc.stdout is not None

        upstream = threading.Thread(target=_relay, args=(conn_file, proc.stdin), daemon=True)
        downstream = threading.Thread(target=_relay, args=(proc.stdout, conn_file), daemon=True)
        upstream.start()
        downstream.start()

        while proc.poll() is None and lease_is_open(idle_timeout_seconds=idle_timeout_seconds):
            time.sleep(0.25)

        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    finally:
        try:
            conn_file.close()
        finally:
            conn.close()


def serve(host: str, port: int, idle_timeout_seconds: int) -> None:
    _token()
    with socket.create_server((host, port), reuse_port=False) as server:
        print(json.dumps({"ok": True, "host": host, "port": port}), flush=True)
        while True:
            conn, _addr = server.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, idle_timeout_seconds),
                daemon=True,
            ).start()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Authenticated TCP bridge to BirdEye MCP stdio")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--idle-timeout-seconds", type=int, default=1800)
    args = parser.parse_args(argv)
    serve(args.host, args.port, args.idle_timeout_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
