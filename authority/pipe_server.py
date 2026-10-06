"""Named-pipe transport for the LBE control plane.

Transport only. The pipe establishes trusted local connection provenance and
resolves an immutable Windows SID. It grants nothing. LBE still owns
ALLOW / DENY / APPROVAL_REQUIRED via controller.authorize().

Identity resolution is server-side and OS-derived:
    pipe handle -> client PID -> token SID -> registered client image -> actor

A caller-supplied "actor" or "session_id" is never an authorization input.
"""

from __future__ import annotations

import ctypes
import json
import sys
import threading
import time
from ctypes import wintypes as wt
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import controller as ctl
import identity_probe
import intents
import sessions

AGENT_PIPE = r"\\.\pipe\LetterBlack-BirdEye"
OPERATOR_PIPE = r"\\.\pipe\LetterBlack-LBE-Operator"

CLIENT_REGISTRY = HERE / "clients.json"
ALLOWED_PRINCIPALS = HERE / "allowed-principals.json"
_MAX_MESSAGE = 1 << 20
_LOCK = threading.Lock()

# Server-owned connection -> session map.
# Keyed by server-observed connection provenance (SID + PID), never by anything
# the caller supplies. Two processes under the same Windows account therefore
# get different keys, and a later process cannot inherit an earlier session.
# Image is retained as metadata only.
_CONNECTION_SESSIONS: dict[tuple[str, int], str] = {}


def connection_key(identity: "ClientIdentity") -> tuple[str, int]:
    return (str(identity.sid).strip().upper(), int(identity.pid))


def remember_session(identity: "ClientIdentity", session_id: str) -> None:
    with _LOCK:
        _CONNECTION_SESSIONS[connection_key(identity)] = session_id


def session_for_connection(identity: "ClientIdentity") -> str | None:
    with _LOCK:
        return _CONNECTION_SESSIONS.get(connection_key(identity))


def context_for_session(session_id: str) -> dict | None:
    """Load a specific session's context. Connection-scoped, not ambient.

    Deliberately does not call sessions.require_context(), which resolves from
    an environment variable. That is correct for in-process consumers only and
    is not valid for a pipe connection the client cannot share an env with.
    """
    for record in sessions.list_sessions():
        if record.get("session_id") != session_id:
            continue
        state = record.get("state")
        if state == sessions.REVOKED:
            raise sessions.SessionUnbound("SESSION_REVOKED", {"session_id": session_id})
        if state != sessions.BOUND or not record.get("intent_id"):
            raise sessions.SessionUnbound(
                "SESSION_AUTHORITY_UNBOUND",
                {"session_id": session_id, "state": state,
                 "reason": "operator has not bound an intent"},
            )
        return {
            "session_id": session_id,
            "actor": record.get("actor"),
            "intent_id": record.get("intent_id"),
            "workspace": record.get("workspace"),
            "bound_at": record.get("bound_at"),
            "bound_by": record.get("bound_by"),
        }
    raise sessions.SessionUnbound("SESSION_UNKNOWN", {"session_id": session_id})

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
k32.CreateNamedPipeW.argtypes = [
    wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, wt.LPVOID
]
k32.CreateNamedPipeW.restype = wt.HANDLE
k32.ConnectNamedPipe.argtypes = [wt.HANDLE, wt.LPVOID]
k32.ConnectNamedPipe.restype = wt.BOOL
k32.DisconnectNamedPipe.argtypes = [wt.HANDLE]
k32.DisconnectNamedPipe.restype = wt.BOOL
k32.CloseHandle.argtypes = [wt.HANDLE]
k32.CloseHandle.restype = wt.BOOL
k32.FlushFileBuffers.argtypes = [wt.HANDLE]
k32.FlushFileBuffers.restype = wt.BOOL


class ClientIdentity:
    def __init__(self, sid: str, pid: int, image: str, actor: str, client_id: str):
        self.sid = sid
        self.pid = pid
        self.image = image
        self.actor = actor
        self.client_id = client_id

    def as_dict(self) -> dict:
        return {
            "sid": self.sid,
            "pid": self.pid,
            "image": self.image,
            "actor": self.actor,
            "client_id": self.client_id,
            "derived_server_side": True,
            "identity_primitive": "sid",
        }


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolve_client_identity(sid: str, pid: int) -> ClientIdentity:
    """Map an OS-established connection to a logical actor. Fail closed."""
    allowed = _load(ALLOWED_PRINCIPALS)
    if str(allowed.get("identity_primitive", "")).lower() != "sid":
        raise PermissionError("ALLOWLIST_NOT_SID_BASED")
    principals = allowed.get("principals") or []
    if not principals:
        raise PermissionError("ALLOWED_PRINCIPALS_MISSING")
    if not any(str(p).strip().upper() == str(sid).strip().upper() for p in principals):
        raise PermissionError("PRINCIPAL_NOT_ALLOWED")

    image = identity_probe.process_image(pid)
    base = Path(image).name if image else ""
    clients = _load(CLIENT_REGISTRY).get("clients") or []
    match = None
    for entry in clients:
        declared = str(entry.get("image", "")).lower()
        if declared and (declared == base.lower() or declared == image.lower()):
            match = entry
            break
    if match is None:
        return ClientIdentity(sid, pid, image, f"unregistered:{base or 'unknown'}", "unregistered")
    return ClientIdentity(sid, pid, image, str(match.get("actor", "unknown")),
                          str(match.get("id", image)))


# ---------------------------------------------------------------- protocol

def handle_request(req: dict, identity: ClientIdentity | None, *, operator: bool) -> dict:
    op = str(req.get("op", "")).strip()

    if op == "ping":
        return {"ok": True, "service": "lbe-control-plane", "initiates_actions": False}

    if op == "whoami":
        return {
            "ok": True,
            "identity": identity.as_dict() if identity else None,
            "supplied_actor_ignored": req.get("actor"),
            "supplied_session_ignored": req.get("session_id"),
        }

    if op == "establish":
        if identity is None:
            return {"ok": False, "error": "IDENTITY_UNAVAILABLE"}
        record = sessions.establish_session(
            actor=identity.actor,
            workspace=req.get("workspace"),
            transport="named-pipe",
            peer=f"{identity.sid}:{identity.image}:{identity.pid}",
        )
        remember_session(identity, record["session_id"])
        return {"ok": True, "session": record, "identity": identity.as_dict(),
                "note": "actor derived server-side; unbound until operator binds an intent"}

    if op == "current":
        if identity is None:
            return {"ok": False, "error": "IDENTITY_UNAVAILABLE"}
        sid_for_conn = session_for_connection(identity)
        if not sid_for_conn:
            return {"ok": False, "error": "SESSION_NOT_ESTABLISHED_ON_THIS_CONNECTION",
                    "rule": "no ambient fallback"}
        try:
            ctx = context_for_session(sid_for_conn)
        except sessions.SessionUnbound as exc:
            return {"ok": False, "error": exc.reason, "detail": exc.detail,
                    "rule": "no fallback to any older gate"}
        return {"ok": True, "context": ctx}

    if operator:
        if op == "bind":
            try:
                rec = sessions.bind_intent(
                    str(req.get("session_id", "")), str(req.get("intent_id", "")),
                    bound_by=str(req.get("bound_by") or "operator"),
                )
            except sessions.SessionUnbound as exc:
                return {"ok": False, "error": exc.reason, "detail": exc.detail}
            except ValueError as exc:
                return {"ok": False, "error": str(exc)}
            return {"ok": True, "session": rec, "note": "association only; grants no authority"}
        if op == "revoke":
            try:
                rec = sessions.revoke(str(req.get("session_id", "")),
                                      revoked_by=str(req.get("bound_by") or "operator"))
            except sessions.SessionUnbound as exc:
                return {"ok": False, "error": exc.reason}
            return {"ok": True, "session": rec}
        if op == "sessions":
            return {"ok": True, "sessions": sessions.list_sessions()}
        if op == "intent_show":
            for ws in intents.load_workspaces():
                intent = intents.read_intent(str(req.get("intent_id", "")), ws)
                if intent:
                    live, reason = intents.is_live(intent)
                    matched, sreason = intents.slice_matches(intent, ws)
                    return {"ok": True, "intent": intent, "live": live, "live_reason": reason,
                            "slice_matches": matched, "slice_reason": sreason}
            return {"ok": False, "error": "INTENT_NOT_REGISTERED"}
        return {"ok": False, "error": "UNKNOWN_OPERATOR_OP"}

    if op == "authorize":
        if identity is None:
            return {"ok": False, "error": "IDENTITY_UNAVAILABLE"}
        sid_for_conn = session_for_connection(identity)
        if not sid_for_conn:
            return {"ok": False, "error": "SESSION_NOT_ESTABLISHED_ON_THIS_CONNECTION",
                    "rule": "no ambient fallback; session must be established on this connection"}
        try:
            ctx = context_for_session(sid_for_conn)
        except sessions.SessionUnbound as exc:
            return {"ok": False, "error": exc.reason, "detail": exc.detail,
                    "rule": "no fallback to any older gate"}
        try:
            decision = ctl.authorize(
                intent_id=ctx["intent_id"],
                actor=ctx["actor"],
                capability=str(req.get("capability", "")),
                targets=req.get("targets") or [],
                workspace=req.get("workspace") or ctx.get("workspace"),
                operation=req.get("operation"),
            )
        except ctl.AuthorityDenied as exc:
            return {"ok": False, "error": exc.reason, "detail": exc.detail}
        return {"ok": True, "decision": decision, "receipt": ctl.receipt(decision),
                "connection_bound_session": sid_for_conn}

    return {"ok": False, "error": "UNKNOWN_OP"}


def _read_all(handle) -> dict:
    import win32file
    buf = b""
    deadline = time.time() + 8
    while time.time() < deadline:
        try:
            _, chunk = win32file.ReadFile(handle, 65536)
        except Exception:
            break
        if not chunk:
            break
        buf += chunk
        try:
            return json.loads(buf.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return {}


def _reply(handle, payload: dict) -> None:
    import win32file
    try:
        win32file.WriteFile(handle, json.dumps(payload, default=str).encode("utf-8"))
        # Flush before the caller tears the instance down, otherwise the reply
        # can be discarded on close and the client observes an empty pipe.
        pass  # FlushFileBuffers can block until the client reads; it deadlocked
    except Exception:
        pass


def serve_pipe(pipe_name: str, *, operator: bool) -> None:
    """Instance-per-thread pool.

    The previous single-threaded create/connect/serve/close loop dropped roughly
    one request in three: a client connecting while the server was between
    teardown and the next ConnectNamedPipe hit a pipe with no listener. Arming a
    fixed pool of instances up front, each with its own thread, removes the
    window entirely and needs no client-side retry.
    """
    PIPE_ACCESS_DUPLEX = 0x00000003
    PIPE_TYPE_BYTE = 0x00000000
    PIPE_READMODE_BYTE = 0x00000000
    PIPE_WAIT = 0x00000000
    PIPE_UNLIMITED_INSTANCES = 255
    ERROR_PIPE_CONNECTED = 535
    ERROR_NO_DATA = 232
    INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
    POOL = 8

    def handle_one(handle) -> None:
        try:
            req = _read_all(handle)
            pid, sid = identity_probe.resolve(int(handle))
            identity = None
            if pid and sid:
                try:
                    identity = resolve_client_identity(sid, pid)
                except PermissionError as exc:
                    _reply(handle, {"ok": False, "error": str(exc)})
                    return
            _reply(handle, handle_request(req, identity, operator=operator))
        except Exception as exc:
            _reply(handle, {"ok": False, "error": type(exc).__name__})

    def worker() -> None:
        while True:
            handle = k32.CreateNamedPipeW(
                pipe_name, PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
                PIPE_UNLIMITED_INSTANCES, _MAX_MESSAGE, _MAX_MESSAGE, 0, None,
            )
            if handle is None or handle == INVALID_HANDLE_VALUE:
                time.sleep(0.05)
                continue
            try:
                if not k32.ConnectNamedPipe(handle, None):
                    if ctypes.get_last_error() not in (ERROR_PIPE_CONNECTED, ERROR_NO_DATA):
                        continue
                handle_one(handle)
            finally:
                k32.DisconnectNamedPipe(handle)
                k32.CloseHandle(handle)

    for _ in range(POOL):
        threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    print(f"agent pipe   : {AGENT_PIPE}", flush=True)
    print(f"operator pipe: {OPERATOR_PIPE}", flush=True)
    print("transport only; LBE owns ALLOW/DENY. service initiates nothing.", flush=True)
    for name, is_op in ((AGENT_PIPE, False), (OPERATOR_PIPE, True)):
        threading.Thread(target=serve_pipe, args=(name,), kwargs={"operator": is_op},
                         daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main())