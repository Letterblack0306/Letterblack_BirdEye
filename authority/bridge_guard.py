"""Authority enforcement for the common execution boundary.

Imported by workspace_bridge._execute_argv. That function is the single place
where BirdEye spawns a process, so enforcing here means every mutation route
(workspace_run, workspace_run_sequence, and any future caller) passes the same
decision. There is no legacy fallback.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

AUTHORITY_DIR = Path(__file__).resolve().parent
if str(AUTHORITY_DIR) not in sys.path:
    sys.path.insert(0, str(AUTHORITY_DIR))

import controller as ctl  # noqa: E402
import identity_probe  # noqa: E402
import sessions  # noqa: E402

CLIENT_REGISTRY = AUTHORITY_DIR / "clients.json"
ALLOWED_PRINCIPALS = AUTHORITY_DIR / "allowed-principals.json"


class MutationDenied(PermissionError):
    """Raised before any side effect. Carries a reason code and a receipt."""

    def __init__(self, reason: str, receipt: dict | None = None):
        self.reason = reason
        self.receipt = receipt or {}
        super().__init__(reason)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        import json
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def derived_actor() -> dict:
    """Identity derived from OS evidence about the calling agent.

    Uses the parent process, matching the control plane. Returns an error key
    rather than raising so callers can fail closed uniformly.
    """
    parent = os.getppid()
    sid = identity_probe.token_sid(parent)
    image = identity_probe.process_image(parent)
    if not sid:
        return {"error": "IDENTITY_UNRESOLVED"}
    allowed = _load(ALLOWED_PRINCIPALS)
    if str(allowed.get("identity_primitive", "")).lower() != "sid":
        return {"error": "ALLOWLIST_NOT_SID_BASED"}
    principals = [str(p).strip().upper() for p in (allowed.get("principals") or [])]
    if not principals:
        return {"error": "ALLOWED_PRINCIPALS_MISSING"}
    if sid.strip().upper() not in principals:
        return {"error": "PRINCIPAL_NOT_ALLOWED", "sid": sid}

    from pathlib import Path as _P
    base = _P(image).name if image else ""
    actor, client_id = f"unregistered:{base or 'unknown'}", "unregistered"
    for entry in _load(CLIENT_REGISTRY).get("clients") or []:
        declared = str(entry.get("image", "")).lower()
        if declared and declared in (base.lower(), image.lower()):
            actor = str(entry.get("actor", "unknown"))
            client_id = str(entry.get("id", base))
            break
    return {"sid": sid, "pid": parent, "image": image, "actor": actor,
            "client_id": client_id, "identity_primitive": "sid",
            "derived_server_side": True}


def authorize_mutation(*, capability: str, targets: list[str], workspace: str,
                       operation: str, effect: str | None = None) -> dict:
    """Authorize one mutation. Raise MutationDenied before any side effect."""
    identity = derived_actor()
    if identity.get("error"):
        rec = _denial(identity["error"], {"stage": "identity", "identity": identity},
                      capability, targets, operation, workspace)
        raise MutationDenied(identity["error"], rec)

    session_id = sessions.session_for_process(os.getpid())
    if not session_id:
        rec = _denial("SESSION_NOT_ESTABLISHED_ON_THIS_CONNECTION",
                      {"rule": "no ambient fallback"}, capability, targets,
                      operation, workspace, identity=identity)
        raise MutationDenied("SESSION_NOT_ESTABLISHED_ON_THIS_CONNECTION", rec)

    try:
        ctx = sessions.context_for_session(session_id)
    except sessions.SessionUnbound as exc:
        rec = _denial(exc.reason, {"detail": exc.detail, "rule": "no fallback to any older gate"},
                      capability, targets, operation, workspace, identity=identity)
        raise MutationDenied(exc.reason, rec)

    try:
        decision = ctl.authorize(
            intent_id=ctx["intent_id"],
            actor=ctx["actor"],
            capability=capability,
            targets=targets,
            workspace=ctx.get("workspace") or workspace,
            operation=operation,
            effect=effect,
        )
    except ctl.AuthorityDenied as exc:
        rec = _denial(exc.reason, {"detail": exc.detail}, capability, targets,
                      operation, workspace, identity=identity, ctx=ctx)
        raise MutationDenied(exc.reason, rec)

    receipt = ctl.receipt(decision)
    receipt.update({"outcome": "ALLOWED", "session_id": session_id, "effect": effect,
                    "identity": identity, "ts": time.time()})
    _write_evidence(receipt)
    return receipt


def _denial(reason: str, extra: dict, capability: str, targets: list[str],
            operation: str, workspace: str, *, identity: dict | None = None,
            ctx: dict | None = None) -> dict:
    rec = {
        "outcome": "DENIED", "stage": "authority", "reason": reason,
        "capability": capability, "targets": targets, "operation": operation,
        "workspace": workspace, "identity": identity, "ts": time.time(),
        "before_evidence": None, "after_evidence": None,
    }
    if ctx:
        rec["session_id"] = ctx.get("session_id")
        rec["intent_id"] = ctx.get("intent_id")
        rec["actor"] = ctx.get("actor")
    rec.update(extra)
    rec["receipt_id"] = ctl.receipt(rec)["receipt_id"]
    _write_evidence(rec)
    return rec


def _write_evidence(record: dict) -> None:
    try:
        import json
        out = AUTHORITY_DIR.parent / "state" / "authority-evidence"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"authority-evidence-{time.strftime('%Y%m%d')}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


def capability_for_argv(argv: tuple[str, ...]) -> str:
    """Map a command to the capability it requires."""
    if not argv:
        return "process.exec"
    exe = Path(str(argv[0])).name.lower()
    verb = str(argv[1]).lower() if len(argv) > 1 else ""
    if exe == "git":
        if verb in {"commit", "push", "merge", "rebase"}:
            return "git.mutate"
        return "process.exec"
    return "process.exec"


# Effect classification derived from observable command semantics, not from a
# list of historical incidents. A command that transmits state outward, or that
# creates durable structure outside the working tree, has an effect that path
# scope cannot constrain -- especially when it carries no path argument.
_EXTERNAL_VERBS = {
    "push": "remote_publication",
    "publish": "remote_publication",
    "upload": "remote_publication",
}
_BRANCH_CREATING = {"branch", "switch", "checkout"}
_WORKTREE_CREATING = {"worktree"}


def effect_for_argv(argv: tuple[str, ...]) -> str:
    """Classify the kind of effect a command would produce.

    Workspace-contained effects are governed by path scope. Effects that leave
    the workspace are named explicitly so the controller can require the intent
    to authorize that kind of effect.
    """
    if not argv:
        return ctl.CONTAINED_EFFECT
    exe = Path(str(argv[0])).name.lower()
    verb = str(argv[1]).lower() if len(argv) > 1 else ""

    if exe in {"git", "git.exe"}:
        if verb in _EXTERNAL_VERBS:
            return "remote_publication"
        if verb == "worktree":
            # `git worktree list` is read-only; everything else creates one.
            sub = str(argv[2]).lower() if len(argv) > 2 else ""
            return "workspace_contained" if sub in {"list", "prune"} else "worktree_creation"
        if verb == "branch":
            creates = not any(a in {"-d", "-D", "--delete", "-m", "-M", "--move", "-l", "--list"}
                              for a in argv[2:])
            return "branch_creation" if creates else ctl.CONTAINED_EFFECT
        if verb in _BRANCH_CREATING:
            # switching or checking out an existing ref changes local state only
            return ctl.CONTAINED_EFFECT
        return ctl.CONTAINED_EFFECT

    if exe in {"npm", "npm.cmd"}:
        return "remote_publication" if verb == "publish" else ctl.CONTAINED_EFFECT
    if exe in {"twine", "uv", "pip", "pip.exe"}:
        if verb in {"upload", "publish"}:
            return "remote_publication"
        if verb == "install":
            return "environment_mutation"
        return ctl.CONTAINED_EFFECT

    return ctl.CONTAINED_EFFECT