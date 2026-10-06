"""Trusted session registry and operator intent binding.

Separation of concerns:

  * The service establishes session_id and actor. It derives them from the
    transport/connection, never from a value the agent supplies.
  * The operator binds an existing session to an existing authorized intent_id.
    That binding does NOT grant authority. It only records which intent the
    session claims to be working under.
  * controller.authorize() remains solely responsible for proving the intent
    is live, slice-matched, owned, scoped, and that the target/capability fit.

A missing or unbound session is a denial, never a fallback to any older gate.
There is exactly one way to obtain authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

AUTHORITY_DIR = Path(__file__).resolve().parent
SESSIONS = AUTHORITY_DIR / "sessions"
LEDGER = SESSIONS / "session-registry.json"

BOUND = "BOUND"
UNBOUND = "UNBOUND"
REVOKED = "REVOKED"

_LOCK = threading.Lock()


class SessionUnbound(PermissionError):
    def __init__(self, reason: str, detail: dict[str, Any] | None = None):
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)


def _read() -> dict[str, Any]:
    if not LEDGER.is_file():
        return {"schema_version": 1, "sessions": {}}
    try:
        doc = json.loads(LEDGER.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        # Fail closed: an unreadable registry means no session is trusted.
        return {"schema_version": 1, "sessions": {}}
    if not isinstance(doc, dict) or not isinstance(doc.get("sessions"), dict):
        return {"schema_version": 1, "sessions": {}}
    return doc


def _write(doc: dict[str, Any]) -> None:
    SESSIONS.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=4, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(LEDGER)


def establish_session(*, actor: str, workspace: str | None, transport: str, peer: str | None = None) -> dict[str, Any]:
    """Service side. Creates session_id and records the established identity.

    `actor` must come from the connection, not from an agent-supplied argument.
    """
    if not actor or not str(actor).strip():
        raise ValueError("actor is required and must be established by the service")
    session_id = "sess_" + secrets.token_hex(16)
    record = {
        "session_id": session_id,
        "actor": str(actor).strip(),
        "workspace": (str(workspace).strip().lower() if workspace else None),
        "transport": str(transport),
        "peer": peer,
        "state": UNBOUND,
        "intent_id": None,
        "created_at": time.time(),
        "bound_at": None,
        "bound_by": None,
    }
    with _LOCK:
        doc = _read()
        doc["sessions"][session_id] = record
        _write(doc)
    return record


def bind_intent(session_id: str, intent_id: str, *, bound_by: str) -> dict[str, Any]:
    """Operator side. Records the claim. Grants nothing."""
    if not bound_by or not str(bound_by).strip():
        raise ValueError("bound_by is required: an operator must perform the binding")
    if not intent_id or not str(intent_id).strip():
        raise ValueError("intent_id is required")
    with _LOCK:
        doc = _read()
        session = doc["sessions"].get(session_id)
        if session is None:
            raise SessionUnbound("SESSION_UNKNOWN", {"session_id": session_id})
        if session.get("state") == REVOKED:
            raise SessionUnbound("SESSION_REVOKED", {"session_id": session_id})
        session["intent_id"] = str(intent_id).strip()
        session["state"] = BOUND
        session["bound_at"] = time.time()
        session["bound_by"] = str(bound_by).strip()
        _write(doc)
        return dict(session)


def revoke(session_id: str, *, revoked_by: str) -> dict[str, Any]:
    with _LOCK:
        doc = _read()
        session = doc["sessions"].get(session_id)
        if session is None:
            raise SessionUnbound("SESSION_UNKNOWN", {"session_id": session_id})
        session["state"] = REVOKED
        session["intent_id"] = None
        session["bound_at"] = None
        session["bound_by"] = None
        session["revoked_at"] = time.time()
        session["revoked_by"] = str(revoked_by or "operator").strip()
        _write(doc)
        return dict(session)


def ambient_session_id() -> str | None:
    """Session the current adapter call belongs to.

    Set by the service at connect time. Never settable by an agent tool argument.
    """
    value = os.environ.get("LBE_SESSION_ID", "").strip()
    return value or None


def require_context() -> dict[str, Any]:
    """The adapter entry point. Raises when no trusted context exists.

    This is what every mutation adapter calls before any side effect.
    """
    session_id = ambient_session_id()
    if not session_id:
        raise SessionUnbound("SESSION_AUTHORITY_UNBOUND", {"reason": "no session on this connection"})
    with _LOCK:
        doc = _read()
        session = doc["sessions"].get(session_id)
    if session is None:
        raise SessionUnbound("SESSION_UNKNOWN", {"session_id": session_id})
    state = session.get("state")
    if state == REVOKED:
        raise SessionUnbound("SESSION_REVOKED", {"session_id": session_id})
    if state != BOUND or not session.get("intent_id"):
        raise SessionUnbound(
            "SESSION_AUTHORITY_UNBOUND",
            {"session_id": session_id, "state": state, "reason": "operator has not bound an intent"},
        )
    return {
        "session_id": session_id,
        "actor": session["actor"],
        "intent_id": session["intent_id"],
        "workspace": session.get("workspace"),
        "bound_at": session.get("bound_at"),
        "bound_by": session.get("bound_by"),
    }


def list_sessions() -> list[dict[str, Any]]:
    with _LOCK:
        doc = _read()
        return sorted(doc["sessions"].values(), key=lambda s: s.get("created_at", 0))


# --------------------------------------------------------------------------
# Connection-bound sessions.
#
# `session_id` is never an authentication input. A session is bound to the
# serving process that established it, and only that process can resolve its
# own context. A caller cannot present another session's id and inherit it,
# and two processes running as the same Windows account get distinct bindings.
# --------------------------------------------------------------------------

_PROCESS_SESSIONS: dict[int, str] = {}


def _revoke_locked(session_id: str, reason: str) -> None:
    """Mark a session unusable. Caller holds _LOCK."""
    doc = _read()
    rec = doc["sessions"].get(session_id)
    if rec is None:
        return
    rec["state"] = REVOKED
    rec["intent_id"] = None
    rec["bound_at"] = None
    rec["bound_by"] = None
    rec["revoked_at"] = time.time()
    rec["revoked_by"] = reason
    _write(doc)


def establish_process_session(
    pid: int,
    *,
    actor: str,
    workspace: str | None,
    transport: str,
    peer: str | None = None,
    process_image: str | None = None,
) -> dict[str, Any]:
    """Establish a session bound to this serving process.

    A connection has exactly one live authority context. Establishing a new
    session supersedes any prior one for the same process, so revoking the
    current session cannot leave a previously-bound session able to inherit
    authority on the same connection.
    """
    record = establish_session(
        actor=actor, workspace=workspace, transport=transport, peer=peer
    )
    with _LOCK:
        previous = _PROCESS_SESSIONS.get(int(pid))
        _PROCESS_SESSIONS[int(pid)] = record["session_id"]
        if previous and previous != record["session_id"]:
            _revoke_locked(previous, "superseded_by:%s" % record["session_id"])
    with _LOCK:
        _PROCESS_SESSIONS[int(pid)] = record["session_id"]
        doc = _read()
        rec = doc["sessions"].get(record["session_id"])
        if rec is not None:
            # Persisted so another process on the same connection can resolve
            # this session by pid. This is a lookup key only; it grants nothing.
            rec["pid"] = int(pid)
            if process_image:
                rec["process_image"] = process_image
            _write(doc)
    return record


def session_for_process(pid: int) -> str | None:
    """Session bound to this pid. Lookup only; never an authorization grant."""
    with _LOCK:
        found = _PROCESS_SESSIONS.get(int(pid))
        if found:
            return found
        doc = _read()
    for record in doc.get("sessions", {}).values():
        if int(record.get("pid", -1) or -1) == int(pid):
            return str(record.get("session_id"))
    return None


def context_for_session(session_id: str) -> dict[str, Any]:
    """Load one specific session's context. Raises when unusable."""
    for record in list_sessions():
        if record.get("session_id") != session_id:
            continue
        state = record.get("state")
        if state == REVOKED:
            raise SessionUnbound("SESSION_REVOKED", {"session_id": session_id})
        if state != BOUND or not record.get("intent_id"):
            raise SessionUnbound(
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
    raise SessionUnbound("SESSION_UNKNOWN", {"session_id": session_id})