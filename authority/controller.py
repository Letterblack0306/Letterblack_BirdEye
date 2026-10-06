"""LBE Authority Controller - the single mutation decision point.

Placed below tool surfaces. Adapters call `authorize()` before any side effect.
The controller is the only component that answers "may this mutation happen".

Authority requires ALL of:
  1. intent_id resolves to a record in the governed workspace intent ledger
  2. that record is live per the repository's own lifecycle vocabulary
  3. its machine slice equals the workspace gate's active slice
  4. the actor is bound by the external machine scope
  5. the capability is granted by the external machine scope
  6. every target is inside a declared allowed path
  7. no target is inside a declared deny path

Any gap denies. Missing, unreadable, or unparseable authority is a denial,
never an allow. `task_id` is deliberately not an authorization input.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterable

import intents

AUTHORITY_DIR = Path(__file__).resolve().parent

ALLOW = "ALLOW"
DENY = "DENY"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"

# An effect that only changes state inside the governed workspace. Path scope
# fully constrains these, so they need no separate effect declaration.
CONTAINED_EFFECT = "workspace_contained"


class AuthorityDenied(PermissionError):
    """Raised when a mutation is not authorized. Carries the reason code."""

    def __init__(self, reason: str, detail: dict[str, Any]):
        self.reason = reason
        self.detail = detail
        super().__init__(reason)


def _external_scope(workspace_name: str) -> dict[str, Any]:
    path = AUTHORITY_DIR / "workspaces" / workspace_name.lower() / "active-scope.json"
    if not path.is_file():
        raise AuthorityDenied(
            "AUTHORITY_SCOPE_MISSING",
            {"expected": str(path), "message": "No external machine scope exists."},
        )
    try:
        scope = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AuthorityDenied(
            "AUTHORITY_SCOPE_UNREADABLE", {"path": str(path), "error": str(exc)}
        ) from exc
    if not isinstance(scope, dict):
        raise AuthorityDenied("AUTHORITY_SCOPE_INVALID", {"path": str(path)})
    return scope


def _contains(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_targets(targets: Iterable[str | Path] | None, workspace: dict[str, Any]) -> list[Path]:
    out: list[Path] = []
    root = workspace["root"]
    for item in targets or ():
        candidate = Path(str(item).strip().strip('"'))
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            out.append(candidate.resolve())
        except OSError:
            continue
    return out


def authorize(
    *,
    intent_id: str | None,
    actor: str | None,
    capability: str,
    targets: Iterable[str | Path] | None = None,
    workspace: str | None = None,
    operation: str | None = None,
    effect: str | None = None,
) -> dict[str, Any]:
    """Decide one mutation. Raise AuthorityDenied on any gap.

    `intent_id` is the canonical authorization identity. There is no task_id
    alias: a caller cannot grant itself authority through an alternate field.
    """
    decision: dict[str, Any] = {
        "decision": DENY,
        "intent_id": intent_id,
        "actor": actor,
        "capability": capability,
        "operation": operation,
        "workspace": workspace,
        "authority": "lbe-controller",
    }

    # 1. identity must be ambient and present
    if not intent_id or not str(intent_id).strip():
        raise AuthorityDenied("INTENT_ID_REQUIRED", decision)
    if not actor or not str(actor).strip():
        raise AuthorityDenied("ACTOR_REQUIRED", decision)

    # 2. resolve the governed workspace
    ws = intents.workspace_by_name(workspace or "")
    if ws is None:
        raise AuthorityDenied("WORKSPACE_NOT_REGISTERED", decision)
    decision["workspace"] = ws["name"]

    # 3. intent must exist in the authoritative ledger
    intent = intents.read_intent(str(intent_id).strip(), ws)
    if intent is None:
        raise AuthorityDenied("INTENT_NOT_REGISTERED", decision)
    decision["intent_status"] = intent["status"]
    decision["intent_slice"] = intent["machine_slice"]
    decision["ledger_path"] = intent["ledger_path"]

    # 4. lifecycle: the repository decides live vs not
    live, reason = intents.is_live(intent)
    if not live:
        raise AuthorityDenied(reason, decision)

    # 5. slice must match the gate
    matched, reason = intents.slice_matches(intent, ws)
    if not matched:
        raise AuthorityDenied(reason, decision)

    # 6/7. external machine scope: actor, capability, paths, deny
    scope = _external_scope(ws["name"])
    if str(scope.get("state", "")).upper() != "ACTIVE":
        raise AuthorityDenied("AUTHORITY_SCOPE_INACTIVE", {**decision, "state": scope.get("state")})

    actors = scope.get("actors")
    if not isinstance(actors, list) or not actors:
        raise AuthorityDenied("ACTOR_NOT_BOUND", {**decision, "note": "no actors declared"})
    if actor not in actors:
        raise AuthorityDenied(
            "ACTOR_NOT_BOUND", {**decision, "bound_actors": [str(a) for a in actors]}
        )

    granted = scope.get("capabilities")
    if not isinstance(granted, list) or capability not in granted:
        raise AuthorityDenied(
            "CAPABILITY_NOT_GRANTED",
            {**decision, "granted": [str(c) for c in granted] if isinstance(granted, list) else []},
        )

    allowed = [Path(p).expanduser().resolve() for p in (scope.get("paths") or [])]
    deny = [Path(p).expanduser().resolve() for p in (scope.get("deny") or [])]
    if not allowed:
        raise AuthorityDenied("AUTHORITY_PATHS_UNDECLARED", decision)

    # 7. effect scope. Path scope cannot constrain an operation whose effect
    # leaves the workspace, because such an operation may carry no path at all.
    # The intent must therefore authorize the kind of effect requested.
    requested_effect = str(effect or "").strip().lower()
    declared_effects = [e.lower() for e in (intent.get("expected_effects") or [])]
    decision["effect"] = requested_effect
    decision["intent_effects"] = declared_effects

    if requested_effect and requested_effect != CONTAINED_EFFECT:
        if requested_effect not in declared_effects:
            raise AuthorityDenied(
                "EFFECT_NOT_AUTHORIZED",
                {
                    **decision,
                    "message": "This intent does not authorize the requested effect. "
                               "An effect that leaves the workspace cannot be "
                               "constrained by path scope alone.",
                },
            )
    elif not requested_effect and not declared_effects and targets_is_empty:
        # No effect could be determined and the intent declares none: ambiguous.
        raise AuthorityDenied("EFFECT_UNDETERMINED", decision)

    # Workspace rules may independently forbid an effect the intent allows.
    gate = intents.gate_state(ws)
    rules = gate.get("rules") if isinstance(gate.get("rules"), dict) else {}
    if gate:
        effect_rules = {
            "remote_publication": "publication_requires_explicit_user_authorization",
            "branch_creation": "deny_branch_creation",
            "worktree_creation": "deny_worktree_creation",
        }
        rule_name = effect_rules.get(requested_effect)
        if rule_name and rules.get(rule_name) is True:
            raise AuthorityDenied(
                "EFFECT_BLOCKED_BY_WORKSPACE_RULE",
                {**decision, "rule": rule_name,
                 "message": "The workspace gate forbids this effect outright."},
            )
        publication = (gate.get("closure") or {}).get("publication")
        if requested_effect == "remote_publication" and str(publication).upper() == "LOCKED":
            raise AuthorityDenied(
                "PUBLICATION_LOCKED",
                {**decision, "closure_publication": publication,
                 "message": "Workspace publication is locked; it cannot be authorized by intent alone."},
            )

    resolved = _normalize_targets(targets, ws)
    targets_is_empty = not resolved
    for target in resolved:
        if any(_contains(d, target) for d in deny):
            raise AuthorityDenied("PATH_DENIED", {**decision, "target": str(target)})
        if not any(_contains(a, target) for a in allowed):
            raise AuthorityDenied(
                "TARGET_OUT_OF_SCOPE",
                {**decision, "target": str(target), "allowed": [str(a) for a in allowed]},
            )
        if not _contains(ws["root"], target):
            raise AuthorityDenied("TARGET_OUTSIDE_WORKSPACE", {**decision, "target": str(target)})

    # intent-declared path prefixes are an additional constraint, not an override
    prefixes = [ws["root"] / p for p in intent["expected_path_prefixes"]]
    for target in resolved:
        if not any(_contains(p, target) for p in prefixes):
            raise AuthorityDenied(
                "INTENT_SCOPE_MISMATCH",
                {
                    **decision,
                    "target": str(target),
                    "intent_prefixes": intent["expected_path_prefixes"],
                },
            )

    decision["decision"] = ALLOW
    decision["targets_checked"] = [str(t) for t in resolved]
    decision["allowed_paths"] = [str(a) for a in allowed]
    decision["objective"] = intent["objective"]
    decision["owner"] = intent["owner"]
    return decision


def receipt(decision: dict[str, Any]) -> dict[str, Any]:
    """Evidence record for one authorized execution."""
    payload = json.dumps(decision, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "receipt_id": hashlib.sha256(payload).hexdigest(),
        "issued_at": time.time(),
        "decision": decision.get("decision"),
        "intent_id": decision.get("intent_id"),
        "actor": decision.get("actor"),
        "capability": decision.get("capability"),
        "operation": decision.get("operation"),
        "workspace": decision.get("workspace"),
        "authority": decision.get("authority"),
        "decision_hash": hashlib.sha256(payload).hexdigest(),
    }