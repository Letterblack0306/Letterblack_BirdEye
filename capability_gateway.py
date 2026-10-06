from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workspace_bridge import (
    BridgeError,
    RunRequest,
    RunSequenceRequest,
    run_command,
    run_sequence,
)

BIRDEYE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class CapabilityOwner:
    name: str
    owner: str
    authority_path: str
    invocation: str
    mutation_authority: str | None = None
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "owner": self.owner,
            "authority_path": self.authority_path,
            "invocation": self.invocation,
            "mutation_authority": self.mutation_authority,
            "notes": self.notes,
        }


_CAPABILITIES: tuple[CapabilityOwner, ...] = (
    CapabilityOwner(
        name="workspace.command",
        owner="workspace_bridge.run_command",
        authority_path="RunRequest -> resolve_workspace -> _command_allowed -> _authority_gate -> _execute_argv -> execution receipt",
        invocation="live",
        mutation_authority="workspace.mutate",
        notes="Single governed argv-array command inside a registered workspace.",
    ),
    CapabilityOwner(
        name="workspace.sequence",
        owner="workspace_bridge.run_sequence",
        authority_path="RunSequenceRequest -> resolve_workspace -> per-command policy/authority -> execution receipt",
        invocation="live",
        mutation_authority="workspace.mutate",
        notes="Ordered governed command sequence inside a registered workspace.",
    ),
    CapabilityOwner(
        name="github.request-bridge",
        owner="bridge/birdeye_request_bridge.py",
        authority_path="GitHub request JSON -> local BirdEye outbound poller -> bounded workspace diagnostics -> response JSON",
        invocation="transport-only",
        notes="Existing GitHub transport is not a general downstream GitHub action provider and is not invoked by capability_invoke.",
    ),
    CapabilityOwner(
        name="browser.relay",
        owner="browser_relay/server.py",
        authority_path="registered browser tab -> localhost relay -> BirdEye loop",
        invocation="transport-only",
        notes="Existing browser relay is transport; no generic downstream browser capability is claimed here.",
    ),
    CapabilityOwner(
        name="remote.mcp",
        owner="remote_transport.py",
        authority_path="authenticated transport -> BirdEye MCP stdio",
        invocation="transport-only",
        notes="Inbound BirdEye transport, not a downstream provider.",
    ),
)


def capability_discover(name: str | None = None) -> dict[str, Any]:
    selected = [item for item in _CAPABILITIES if name is None or item.name == name]
    return {
        "ok": True,
        "authority": "birdeye-capability-gateway",
        "capabilities": [item.as_dict() for item in selected],
        "unknown": bool(name and not selected),
    }


def capability_invoke(name: str, request: dict[str, Any], config_path: Path) -> dict[str, Any]:
    """Route only to proven existing BirdEye owners.

    This function does not accept caller identity fields and does not create a
    second authorization system. The selected owner remains responsible for
    workspace resolution, policy, capability checks, mutation authority,
    execution evidence, and receipts.
    """
    if not isinstance(request, dict):
        raise BridgeError("capability request must be an object")

    forbidden_identity_fields = {"actor", "actor_id", "user", "user_id", "sid"}
    supplied = sorted(forbidden_identity_fields & set(request))
    if supplied:
        raise BridgeError(
            "CALLER_IDENTITY_NOT_ACCEPTED\n\n"
            f"Caller identity must come from trusted runtime/session evidence, not request fields: {', '.join(supplied)}"
        )

    if name == "workspace.command":
        result = run_command(RunRequest.from_mapping(request), config_path)
    elif name == "workspace.sequence":
        result = run_sequence(RunSequenceRequest.from_mapping(request), config_path)
    else:
        known = next((item for item in _CAPABILITIES if item.name == name), None)
        if known is None:
            raise BridgeError(f"UNKNOWN_CAPABILITY\n\nUnknown BirdEye capability: {name}")
        raise BridgeError(
            "CAPABILITY_NOT_INVOKABLE\n\n"
            f"{name} is currently {known.invocation}; BirdEye will not pretend it has a proven downstream invocation path"
        )

    return {
        "ok": bool(result.get("ok", result.get("status") != "failed")),
        "authority": "birdeye-capability-gateway",
        "capability": name,
        "owner": next(item.owner for item in _CAPABILITIES if item.name == name),
        "result": result,
    }
