from __future__ import annotations

import argparse
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7726
STATE_PATH = Path(__file__).resolve().parent / "state.json"


class RelayState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.tabs: dict[str, dict[str, Any]] = {}
        self.commands: dict[str, dict[str, Any]] = {}

    def register_tab(self, payload: dict[str, Any]) -> dict[str, Any]:
        tab_id = str(payload.get("tab_id", "")).strip()
        if not tab_id:
            raise ValueError("tab_id is required")
        with self.lock:
            self.tabs[tab_id] = {
                "tab_id": tab_id,
                "url": payload.get("url"),
                "title": payload.get("title"),
                "thread_url": payload.get("thread_url"),
                "updated_at": time.time(),
            }
            return self.tabs[tab_id].copy()

    def unregister_tab(self, tab_id: str) -> None:
        with self.lock:
            self.tabs.pop(tab_id, None)

    def list_tabs(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.tabs.values())

    def queue(self, tab_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if tab_id not in self.tabs:
                raise KeyError("TAB_NOT_REGISTERED")
            command_id = secrets.token_hex(12)
            command = {
                "command_id": command_id,
                "tab_id": tab_id,
                "action": action,
                "payload": payload,
                "created_at": time.time(),
                "status": "queued",
                "result": None,
            }
            self.commands[command_id] = command
            return command.copy()

    def next_for_tab(self, tab_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [
                command.copy()
                for command in self.commands.values()
                if command["tab_id"] == tab_id and command["status"] == "queued"
            ]

    def claim_for_tab(self, tab_id: str) -> dict[str, Any] | None:
        with self.lock:
            for command in self.commands.values():
                if command["tab_id"] == tab_id and command["status"] == "queued":
                    command["status"] = "dispatched"
                    command["dispatched_at"] = time.time()
                    return command.copy()
            return None

    def complete(self, command_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            command = self.commands.get(command_id)
            if command is None:
                raise KeyError("COMMAND_NOT_FOUND")
            command["status"] = "complete"
            command["result"] = payload
            command["completed_at"] = time.time()
            return command.copy()

    def command(self, command_id: str | None) -> dict[str, Any] | None:
        with self.lock:
            value = self.commands.get(command_id or "")
            return value.copy() if value else None


STATE = RelayState()


class Handler(BaseHTTPRequestHandler):
    server_version = "BirdEyeBrowserRelay/0.1"

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length).decode("utf-8")) if length else {}

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json(200, {"ok": True, "service": "birdeye-browser-relay", "cdp": False})
        elif parsed.path == "/tabs":
            self._json(200, {"ok": True, "tabs": STATE.list_tabs()})
        elif parsed.path == "/commands":
            command_id = parse_qs(parsed.query).get("id", [None])[0]
            command = STATE.command(command_id)
            self._json(200, {"ok": command is not None, "command": command})
        else:
            self._json(404, {"ok": False, "error": "NOT_FOUND"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            payload = self._read()
            if parsed.path == "/tabs/register":
                self._json(200, {"ok": True, "tab": STATE.register_tab(payload)})
            elif parsed.path == "/tabs/unregister":
                STATE.unregister_tab(str(payload.get("tab_id", "")))
                self._json(200, {"ok": True})
            elif parsed.path == "/commands/next":
                self._json(200, {"ok": True, "commands": STATE.next_for_tab(str(payload.get("tab_id", "")))})
            elif parsed.path == "/commands/claim":
                command = STATE.claim_for_tab(str(payload.get("tab_id", "")))
                self._json(200, {"ok": True, "command": command})
            elif parsed.path == "/commands/complete":
                self._json(200, STATE.complete(str(payload.get("command_id", "")), payload.get("result") or {}))
            elif parsed.path == "/read":
                self._json(202, {"ok": True, "command": STATE.queue(str(payload.get("tab_id", "")), "read", {})})
            elif parsed.path == "/diagnose":
                self._json(202, {"ok": True, "command": STATE.queue(str(payload.get("tab_id", "")), "diagnose", {})})
            elif parsed.path == "/reply":
                self._json(202, {"ok": True, "command": STATE.queue(str(payload.get("tab_id", "")), "reply", {"text": str(payload.get("text", ""))})})
            else:
                self._json(404, {"ok": False, "error": "NOT_FOUND"})
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self._json(400, {"ok": False, "error": type(exc).__name__, "message": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="BirdEye browser relay without CDP")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"BirdEye browser relay listening on http://{args.host}:{args.port}", flush=True)
    print("Transport: Chrome extension; CDP: disabled", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
