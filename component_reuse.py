"""Manifest-derived preflight for BirdEye-managed component changes.

This is a narrow governance check for declared/recognizable component operations.
It does not replace BirdEye authorization, receipts, or OS-level permissions.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

_COMPONENT = "birdeye-browser-relay"
_MARKERS = ("browser_relay", "birdeyerelay", "browser relay")
_MUTATORS = re.compile(
    r"\b(new-item|set-content|add-content|remove-item|move-item|copy-item|"
    r"mkdir|makedirs|writealltext|write_text|write_bytes|git\s+clone|"
    r"install_release|install_browser_relay|deploy|create|duplicate)\b",
    re.IGNORECASE,
)
_READERS = re.compile(
    r"^\s*(?:get-content|get-item|get-childitem|get-filehash|test-path|"
    r"git\s+(?:status|log|show|diff)|type\s|dir\s|echo\s)",
    re.IGNORECASE,
)


def inventory(releases_root: Path) -> dict[str, Any]:
    """Read canonical release manifests; do not infer absence from an unreadable root."""
    root = Path(releases_root)
    result: dict[str, Any] = {"components": {}, "errors": [], "complete": False}
    if not root.is_dir():
        result["errors"].append(f"release root unavailable: {root}")
        return result
    count = 0
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError as exc:
        result["errors"].append(f"release root unreadable: {type(exc).__name__}")
        return result
    for folder in folders:
        path = folder / "release-manifest.json"
        if not path.is_file():
            result["errors"].append(f"release manifest missing: {folder.name}")
            continue
        try:
            raw = path.read_bytes()
            data = json.loads(raw)
            component = data["component"]
            version = data["version"]
            files = data["files"]
            if not isinstance(component, str) or not isinstance(version, str) or not isinstance(files, dict):
                raise ValueError("invalid component/version/files schema")
            result["components"].setdefault(component.casefold(), []).append({
                "version": version,
                "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "file_count": len(files),
                "path": str(path),
            })
            count += 1
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result["errors"].append(f"invalid manifest {folder.name}: {type(exc).__name__}")
    result["complete"] = count > 0 and not result["errors"]
    return result


def _commands(params: dict[str, Any]) -> list[list[str]]:
    if "commands" in params:
        return [s.get("argv", []) for s in params.get("commands", []) if isinstance(s, dict)]
    return [params.get("argv", [])]


def check_request(
    params: dict[str, Any], releases_root: Path | None = None
) -> dict[str, Any] | None:
    """Return a DENY result for recognized managed-component changes; None means no denial.

    A None result is *not* a certificate that arbitrary argv is read-only.
    The existing workspace authority must still authorize execution.
    """
    context = params.get("context_evidence") or {}
    declaration = context.get("component_change") if isinstance(context, dict) else None
    if declaration is not None and not isinstance(declaration, dict):
        return {"ok": False, "error": "INVALID_COMPONENT_DECLARATION"}
    joined = " ".join(" ".join(str(a) for a in argv) for argv in _commands(params))
    intent = str(params.get("intent") or "")
    combined = (joined + " " + intent).lower()
    marker = any(token in combined for token in _MARKERS)
    claimed_operation = str(declaration.get("operation", "")).casefold() if declaration else ""
    claimed_component = str(declaration.get("component", "")).casefold() if declaration else ""
    if not marker and not declaration:
        return None
    # Leave recognized read-only commands alone unless the caller declares a mutation.
    if not declaration and not _MUTATORS.search(combined):
        return None
    if not declaration and _READERS.match(joined) and not _MUTATORS.search(joined):
        return None
    if not declaration:
        return {"ok": False, "error": "COMPONENT_PREFLIGHT_REQUIRED",
                "component": _COMPONENT if marker else None,
                "hint": "Declare context_evidence.component_change with component, operation and manifest_sha256."}
    if not claimed_component or not claimed_operation:
        return {"ok": False, "error": "INVALID_COMPONENT_DECLARATION"}
    root = releases_root if releases_root is not None else Path(os.environ.get("BIRDEYE_RELEASES_ROOT", str(Path(__file__).resolve().parent.parent / "birdeye-releases" / "browser-relay")))
    observed = inventory(root)
    if not observed["complete"]:
        return {"ok": False, "error": "COMPONENT_INVENTORY_INCOMPLETE",
                "errors": observed["errors"][:5]}
    versions = observed["components"].get(claimed_component, [])
    if claimed_operation in {"create", "register_new", "install_new"}:
        if versions:
            return {"ok": False, "error": "DUPLICATE_COMPONENT",
                    "component": claimed_component,
                    "existing_releases": [v["version"] for v in versions],
                    "hint": "Reuse and upgrade the existing component via its canonical release manifest."}
        return {"ok": False, "error": "NEW_COMPONENT_AUTHORITY_UNVERIFIED",
                "component": claimed_component,
                "hint": "No release match does not prove absence across machines; reconcile ownership first."}
    if claimed_operation in {"modify_existing", "upgrade_existing", "deploy_existing"}:
        # A claimed in-place update must not conceal an obvious new component.
        if re.search(r"\b(new-item|mkdir|makedirs|git\s+clone|install_browser_relay)\b",
                     joined, re.IGNORECASE):
            return {"ok": False, "error": "COMPONENT_OPERATION_MISMATCH",
                    "component": claimed_component,
                    "hint": "Creation requires ownership discovery, not an update declaration."}
        match_hash = str(declaration.get("manifest_sha256", "")).casefold()
        if versions and any(v["manifest_sha256"] == match_hash for v in versions):
            return None
        return {"ok": False, "error": "COMPONENT_MANIFEST_MISMATCH",
                "component": claimed_component,
                "hint": "Use a current hash from the canonical release manifest."}
    return {"ok": False, "error": "UNSUPPORTED_COMPONENT_OPERATION"}
