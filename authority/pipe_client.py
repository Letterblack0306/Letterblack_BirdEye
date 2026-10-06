"""Client for the LBE control-plane named pipes.

Usage:
  python pipe_client.py <agent|operator> <json-request>
"""
import json
import sys
import time


def call(pipe: str, request: dict, *, timeout_ms: int = 8000) -> dict:
    import win32file
    import pywintypes

    deadline = time.time() + timeout_ms / 1000.0
    last = None
    while time.time() < deadline:
        try:
            handle = win32file.CreateFile(
                pipe,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                # Share read+write. An exclusive open (0) blocks the server's
                # ConnectNamedPipe, so the first connection was never accepted
                # and the client saw an empty pipe.
                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
            try:
                win32file.WriteFile(handle, json.dumps(request).encode("utf-8"))
                chunks = []
                while True:
                    try:
                        _, data = win32file.ReadFile(handle, 65536)
                    except Exception:
                        break
                    if not data:
                        break
                    chunks.append(data)
                if not chunks:
                    return {"ok": False, "error": "EMPTY_RESPONSE"}
                return json.loads(b"".join(chunks).decode("utf-8", "replace"))
            finally:
                win32file.CloseHandle(handle)
        except Exception as exc:  # busy pipe / not listening
            last = exc
            time.sleep(0.25)
    return {"ok": False, "error": "CONNECT_FAILED", "detail": repr(last)}


AGENT = r"\\.\pipe\LetterBlack-BirdEye"
OPERATOR = r"\\.\pipe\LetterBlack-LBE-Operator"

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "agent"
    req = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {"op": "ping"}
    print(json.dumps(call(OPERATOR if which == "operator" else AGENT, req), indent=2, default=str))