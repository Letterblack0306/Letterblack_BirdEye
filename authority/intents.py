"""Authoritative LBE intent lifecycle reader.

Reads the governed workspace intent ledger and the machine gate, and answers
lifecycle questions. This module does not decide anything; it only reports what
the repository actually records. It never infers authority from a caller.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

AUTHORITY_DIR = Path(__file__).resolve().parent
REGISTRY = AUTHORITY_DIR / "governed-workspaces.json"

_HEADING = re.compile(r"^## INTENT (\S+)", re.MULTILINE)
_BLOCK = re.compile(r"^## INTENT (\S+).*?(?=^## INTENT |\Z)", re.MULTILINE | re.DOTALL)

# Statuses the repository gate treats as authorization-bearing.
LIVE_STATUSES = {"ACTIVE", "AUTHORIZED"}


def load_workspaces() -> tuple[dict[str, Any], ...]:
    if not REGISTRY.is_file():
        return ()
    try:
        doc = json.loads(REGISTRY.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return ()
    out = []
    for item in doc.get("workspaces") or ():
        if not isinstance(item, dict):
            continue
        if item.get("enabled") is False:
            continue
        root = item.get("root")
        if not root:
            continue
        out.append(
            {
                "id": str(item.get("id") or Path(root).name),
                "name": str(item.get("name") or item.get("id") or Path(root).name),
                "root": Path(root).expanduser().resolve(),
                "intent_ledger": item.get("intent_ledger"),
                "gate_file": item.get("gate_file"),
            }
        )
    return tuple(out)


def _field(block: str, name: str) -> str:
    match = re.search(rf"^{re.escape(name)}:\s*(.+)$", block, re.MULTILINE)
    return match.group(1).strip() if match else ""


def workspace_by_name(name: str) -> dict[str, Any] | None:
    wanted = str(name or "").strip().lower()
    for ws in load_workspaces():
        if ws["name"].lower() == wanted or ws["id"].lower() == wanted:
            return ws
    return None


def gate_state(workspace: dict[str, Any]) -> dict[str, Any]:
    """Read the machine gate. Returns {} when unavailable; caller must fail closed."""
    gate_rel = workspace.get("gate_file")
    if not gate_rel:
        return {}
    path = workspace["root"] / Path(gate_rel)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_intent(intent_id: str, workspace: dict[str, Any]) -> dict[str, Any] | None:
    """Return the registered intent block, or None when it is not in the ledger."""
    ledger_rel = workspace.get("intent_ledger")
    if not ledger_rel:
        return None
    path = workspace["root"] / Path(ledger_rel)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    wanted = str(intent_id or "").strip()
    if not wanted:
        return None
    for match in _BLOCK.finditer(text):
        if match.group(1) == wanted:
            block = match.group(0)
            return {
                "intent_id": wanted,
                "status": _field(block, "STATUS").upper(),
                "machine_slice": _field(block, "MACHINE_SLICE"),
                "owner": _field(block, "EXISTING_OWNER"),
                "objective": _field(block, "REQUEST") or _field(block, "DESIRED_RESULT"),
                "result": _field(block, "RESULT"),
                "authorization": _field(block, "AUTHORIZATION"),
                "expected_path_prefixes": [
                    item.strip()
                    for item in _field(block, "EXPECTED_PATH_PREFIXES").split(",")
                    if item.strip()
                ],
                # Effect scope: the kinds of effect this intent authorizes,
                # beyond what path prefixes can express. Same declaration
                # convention as EXPECTED_PATH_PREFIXES. An intent that declares
                # none authorizes only workspace-contained effects.
                "expected_effects": [
                    item.strip()
                    for item in _field(block, "EXPECTED_EFFECTS").split(",")
                    if item.strip()
                ],
                "ledger_path": str(path),
                "registered": True,
            }
    return None


def is_live(intent: dict[str, Any]) -> tuple[bool, str]:
    """An intent grants authority only while the repository says it is live."""
    if not intent:
        return False, "INTENT_NOT_REGISTERED"
    status = intent.get("status", "")
    if status not in LIVE_STATUSES:
        return False, f"INTENT_NOT_LIVE:{status or 'MISSING_STATUS'}"
    if not intent.get("machine_slice"):
        return False, "INTENT_SLICE_UNDECLARED"
    if not intent.get("expected_path_prefixes"):
        return False, "INTENT_SCOPE_UNDECLARED"
    if not intent.get("owner"):
        return False, "INTENT_OWNER_UNDECLARED"
    return True, "OK"


def slice_matches(intent: dict[str, Any], workspace: dict[str, Any]) -> tuple[bool, str]:
    """Intent slice must equal the workspace gate's active slice."""
    gate = gate_state(workspace)
    if not gate:
        return False, "GATE_UNAVAILABLE"
    active = str(gate.get("active_slice", "")).strip()
    if not active:
        return False, "GATE_SLICE_UNDECLARED"
    if intent.get("machine_slice") != active:
        return False, "INTENT_SLICE_MISMATCH"
    return True, "OK"