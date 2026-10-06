"""Machine-owned LBE control plane, exposed as an MCP server over stdio.

Transport: the `mcp` SDK stdio transport, already in production use by the
existing local servers. Replaces the hand-written named-pipe accept loop, which
dropped requests. stdio has no accept loop and needs no retry.

Identity: derived from the PARENT PROCESS, server side.
    parent PID -> token SID -> process image -> logical actor
The reasoning client never supplies actor or session identity. Those fields are
accepted and ignored.

Authority: external. Every mutation resolves through the machine-owned
controller against the governed workspace intent ledger and the external scope.
No legacy fallback exists on this path.

This service never initiates work. It answers the user's already-requested
actions and denies everything else.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import controller as ctl
import identity_probe
import intents
import sessions

CLIENT_REGISTRY = HERE / "clients.json"
ALLOWED_PRINCIPALS = HERE / "allowed-principals.json"
EVIDENCE_DIR = HERE.parent / "state" / "authority-evidence"

_LOCK = threading.Lock()
_CLIENT_IDENTITY: dict | None = None


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def client_identity() -> dict:
    """Resolve the connected client's identity from OS evidence. Cache it.

    Fail closed: any unresolved component yields None identity, and every
    mutation is then denied.
    """
    global _CLIENT_IDENTITY
    with _LOCK:
        if _CLIENT_IDENTITY is not None:
            return _CLIENT_IDENTITY
        allowed = _load(ALLOWED_PRINCIPALS)
        if str(allowed.get("identity_primitive", "")).lower() != "sid":
            _CLIENT_IDENTITY = {"error": "ALLOWLIST_NOT_SID_BASED"}
            return _CLIENT_IDENTITY
        principals = [str(p).strip().upper() for p in (allowed.get("principals") or [])]
        if not principals:
            _CLIENT_IDENTITY = {"error": "ALLOWED_PRINCIPALS_MISSING"}
            return _CLIENT_IDENTITY

        parent_pid = os.getppid()
        sid = identity_probe.token_sid(parent_pid)
        image = identity_probe.process_image(parent_pid)
        if not sid:
            _CLIENT_IDENTITY = {"error": "IDENTITY_UNRESOLVED"}
            return _CLIENT_IDENTITY
        if sid.strip().upper() not in principals:
            _CLIENT_IDENTITY = {"error": "PRINCIPAL_NOT_ALLOWED", "sid": sid}
            return _CLIENT_IDENTITY

        base = Path(image).name if image else ""
        actor, client_id = f"unregistered:{base or 'unknown'}", "unregistered"
        for entry in _load(CLIENT_REGISTRY).get("clients") or []:
            declared = str(entry.get("image", "")).lower()
            if declared and declared in (base.lower(), image.lower()):
                actor = str(entry.get("actor", "unknown"))
                client_id = str(entry.get("id", base))
                break

        _CLIENT_IDENTITY = {
            "sid": sid,
            "pid": parent_pid,
            "image": image,
            "actor": actor,
            "client_id": client_id,
            "identity_primitive": "sid",
            "derived_server_side": True,
        }
        return _CLIENT_IDENTITY


def session_context() -> dict:
    """Session bound to THIS process's connection. Never ambient, never supplied."""
    ident = client_identity()
    if ident.get("error"):
        raise sessions.SessionUnbound(ident["error"], {"identity": ident})
    sid_for_conn = sessions.session_for_process(os.getpid())
    if not sid_for_conn:
        raise sessions.SessionUnbound(
            "SESSION_NOT_ESTABLISHED_ON_THIS_CONNECTION",
            {"rule": "no ambient fallback; session must be established on this connection"},
        )
    return sessions.context_for_session(sid_for_conn)


def write_evidence(record: dict) -> str | None:
    """Persist receipt/evidence using the existing evidence directory."""
    try:
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        day = time.strftime("%Y%m%d")
        path = EVIDENCE_DIR / f"authority-evidence-{day}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return str(path)
    except OSError:
        return None


# ------------------------------------------------------------------ tools

def op_whoami(supplied: dict) -> dict:
    ident = client_identity()
    return {
        "ok": "error" not in ident,
        "identity": ident,
        "supplied_actor_ignored": supplied.get("actor"),
        "supplied_session_ignored": supplied.get("session_id"),
    }


def op_establish(supplied: dict) -> dict:
    ident = client_identity()
    if ident.get("error"):
        return {"ok": False, "error": ident["error"], "identity": ident}
    record = sessions.establish_process_session(
        os.getpid(), actor=ident["actor"], workspace=supplied.get("workspace"),
        transport="stdio", peer=f"{ident['sid']}:{ident['image']}:{ident['pid']}",
    )
    return {
        "ok": True,
        "session": record,
        "identity": ident,
        "note": "actor derived server-side; unbound until an operator binds an intent",
    }


def op_current() -> dict:
    try:
        return {"ok": True, "context": session_context()}
    except sessions.SessionUnbound as exc:
        return {"ok": False, "error": exc.reason, "detail": exc.detail,
                "rule": "no fallback to any older gate"}


def op_authorize(supplied: dict) -> dict:
    try:
        ctx = session_context()
    except sessions.SessionUnbound as exc:
        rec = {
            "outcome": "DENIED", "stage": "session", "reason": exc.reason,
            "detail": exc.detail, "operation": supplied.get("operation"),
            "targets": supplied.get("targets"), "ts": time.time(),
            "identity": client_identity(),
        }
        rec["receipt_id"] = ctl.receipt(rec)["receipt_id"]
        write_evidence(rec)
        return {"ok": False, "error": exc.reason, "detail": exc.detail,
                "receipt": rec["receipt_id"],
                "rule": "no fallback to any older gate"}

    ident = client_identity()
    try:
        decision = ctl.authorize(
            intent_id=ctx["intent_id"],
            actor=ctx["actor"],
            capability=str(supplied.get("capability", "")),
            targets=supplied.get("targets") or [],
            workspace=supplied.get("workspace") or ctx.get("workspace"),
            operation=supplied.get("operation"),
            effect=supplied.get("effect"),
        )
    except ctl.AuthorityDenied as exc:
        rec = {
            "outcome": "DENIED", "stage": "authorization", "reason": exc.reason,
            "detail": exc.detail, "session_id": ctx["session_id"],
            "intent_id": ctx["intent_id"], "actor": ctx["actor"],
            "capability": supplied.get("capability"),
            "operation": supplied.get("operation"),
            "effect": supplied.get("effect"),
            "targets": supplied.get("targets"), "ts": time.time(), "identity": ident,
        }
        rec["receipt_id"] = ctl.receipt(rec)["receipt_id"]
        write_evidence(rec)
        return {"ok": False, "error": exc.reason, "detail": exc.detail,
                "receipt": rec["receipt_id"]}

    receipt = ctl.receipt(decision)
    receipt.update({
        "outcome": "ALLOWED", "stage": "authorization",
        "session_id": ctx["session_id"], "targets": decision.get("targets_checked"),
        "identity": ident, "ts": time.time(),
    })
    write_evidence(receipt)
    return {"ok": True, "decision": decision, "receipt": receipt,
            "connection_bound_session": ctx["session_id"]}


def op_intent_show(intent_id: str) -> dict:
    for ws in intents.load_workspaces():
        intent = intents.read_intent(str(intent_id or ""), ws)
        if intent:
            live, reason = intents.is_live(intent)
            matched, sreason = intents.slice_matches(intent, ws)
            return {"ok": True, "workspace": ws["name"], "intent": intent,
                    "live": live, "live_reason": reason,
                    "slice_matches": matched, "slice_reason": sreason}
    return {"ok": False, "error": "INTENT_NOT_REGISTERED"}


def dispatch(tool: str, supplied: dict) -> dict:
    if tool == "lbe_whoami":
        return op_whoami(supplied)
    if tool == "lbe_establish":
        return op_establish(supplied)
    if tool == "lbe_current":
        return op_current()
    if tool == "lbe_authorize":
        return op_authorize(supplied)
    if tool == "lbe_intent_show":
        return op_intent_show(supplied.get("intent_id", ""))
    return {"ok": False, "error": "UNKNOWN_TOOL", "tool": tool}


def build_server():
    try:
        from mcp.server.mcpserver import MCPServer as _S
    except Exception:
        from mcp.server.fastmcp import FastMCP as _S

    mcp = _S("lbe-control-plane")

    @mcp.tool()
    def lbe_whoami(actor: str | None = None, session_id: str | None = None) -> dict:
        """Report the OS-derived identity of this connection.

        Any actor/session supplied by the caller is ignored and echoed back so
        the caller can observe that it was not used.
        """
        return op_whoami({"actor": actor, "session_id": session_id})

    @mcp.tool()
    def lbe_establish(workspace: str | None = None) -> dict:
        """Establish an authority session bound to this connection.

        The session starts UNBOUND. An operator must bind an existing authorized
        intent before any mutation is permitted.
        """
        return op_establish({"workspace": workspace})

    @mcp.tool()
    def lbe_current() -> dict:
        """Return this connection's authority context."""
        return op_current()

    @mcp.tool()
    def lbe_authorize(capability: str, targets: list[str] | None = None,
                      workspace: str | None = None,
                      operation: str | None = None,
                      effect: str | None = None) -> dict:
        """Authorize one requested mutation. Executes nothing.

        Returns ALLOW with a receipt, or DENY with a reason and a denial receipt.
        """
        return op_authorize({"capability": capability, "targets": targets or [],
                             "workspace": workspace, "operation": operation,
                             "effect": effect})

    @mcp.tool()
    def lbe_intent_show(intent_id: str) -> dict:
        """Report authoritative lifecycle state for a registered LBE intent."""
        return op_intent_show(intent_id)

    return mcp


def main() -> int:
    server = build_server()
    print("[lbe-control-plane] stdio transport; identity derived from parent process",
          file=sys.stderr, flush=True)
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())