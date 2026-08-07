from __future__ import annotations

import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from workspace_bridge import BridgeError, DiagnosticRequest, execute, utc_now


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8767
MAX_REQUEST_BYTES = 32_768
TOKEN_ENV = "BIRDEYE_BRIDGE_TOKEN"


class TransportError(RuntimeError):
    pass


def _required_token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise TransportError(f"{TOKEN_ENV} must be set")
    return token


def _authorized(headers: Any, token: str) -> bool:
    supplied = str(headers.get("Authorization") or "")
    prefix = "Bearer "
    if not supplied.startswith(prefix):
        return False
    return hmac.compare_digest(supplied[len(prefix):].strip(), token)


def make_handler(*, config_path: Path, token: str):
    class Handler(BaseHTTPRequestHandler):
        server_version = "BirdEyeBridge/1.0"

        def log_message(self, format: str, *args: object) -> None:
            # Avoid emitting request bodies or authorization material.
            return

        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _require_auth(self) -> bool:
            if _authorized(self.headers, token):
                return True
            self._write_json(401, {
                "error": "UNAUTHORIZED",
                "message": "Valid bearer token required.",
                "completed_at": utc_now(),
            })
            return False

        def do_GET(self) -> None:
            if self.path != "/health":
                self._write_json(404, {"error": "NOT_FOUND", "path": self.path})
                return
            if not self._require_auth():
                return
            self._write_json(200, {
                "status": "ok",
                "transport": "loopback-only",
                "operations": [
                    "git.status",
                    "git.head",
                    "git.branch",
                    "git.diff-check",
                    "git.diff-stat",
                    "git.worktree-list",
                    "pytest",
                ],
            })

        def do_POST(self) -> None:
            if self.path != "/diagnostic":
                self._write_json(404, {"error": "NOT_FOUND", "path": self.path})
                return
            if not self._require_auth():
                return

            content_length = self.headers.get("Content-Length")
            try:
                size = int(content_length or "0")
            except ValueError:
                self._write_json(400, {"error": "INVALID_CONTENT_LENGTH"})
                return
            if size <= 0 or size > MAX_REQUEST_BYTES:
                self._write_json(413, {
                    "error": "REQUEST_TOO_LARGE",
                    "max_bytes": MAX_REQUEST_BYTES,
                })
                return

            body = self.rfile.read(size)
            try:
                value = json.loads(body.decode("utf-8"))
                if not isinstance(value, dict):
                    raise BridgeError("Request JSON must be an object")
                request = DiagnosticRequest.from_mapping(value)
                result = execute(request, config_path)
            except (UnicodeDecodeError, json.JSONDecodeError, BridgeError) as exc:
                self._write_json(400, {
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "completed_at": utc_now(),
                })
                return
            except Exception as exc:
                self._write_json(500, {
                    "error": type(exc).__name__,
                    "message": str(exc),
                    "completed_at": utc_now(),
                })
                return

            self._write_json(200, result)

    return Handler


def serve(*, host: str, port: int, config_path: Path, token: str) -> None:
    if host not in {"127.0.0.1", "localhost"}:
        raise TransportError("BirdEye diagnostic transport must remain loopback-only")
    server = ThreadingHTTPServer((host, port), make_handler(config_path=config_path, token=token))
    print(f"BirdEye diagnostic bridge listening on http://{host}:{port}")
    print("Endpoints: GET /health, POST /diagnostic")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Authenticated loopback transport for BirdEye diagnostics")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("BIRDEYE_CONFIG_PATH", "config.json")),
    )
    args = parser.parse_args()

    token = _required_token()
    config_path = args.config.expanduser().resolve()
    serve(host=args.host, port=args.port, config_path=config_path, token=token)


if __name__ == "__main__":
    main()
