from __future__ import annotations

import argparse
import ctypes
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


def native_submit_chrome(window_title: str) -> dict[str, Any]:
    """Submit through the focused visible Chrome UI without CDP."""
    if not window_title:
        raise ValueError("window_title is required")
    user32 = ctypes.windll.user32
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    target = ctypes.c_void_p()

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if "Chrome" in buf.value:
            target.value = hwnd
            return False
        return True

    user32.EnumWindows(enum_proc(callback), 0)
    if not target.value:
        raise RuntimeError("CHROME_WINDOW_NOT_FOUND")
    if not user32.SetForegroundWindow(target):
        raise RuntimeError("CHROME_FOREGROUND_FAILED")
    time.sleep(0.15)
    KEYUP = 0x0002
    VK_RETURN = 0x0D
    user32.keybd_event(VK_RETURN, 0, 0, 0)
    user32.keybd_event(VK_RETURN, 0, KEYUP, 0)
    return {"ok": True, "native": True, "key": "Enter", "window_title": window_title}


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
