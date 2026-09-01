"""EYES configuration loader for BirdEye workspace, memory, and skills roots.

EYES is deliberately a small, read-only configuration layer. It loads the
three domain files beside this module and combines them into the existing
BirdEye ``roots`` shape. It does not scan, copy, move, delete, or modify source
files. The existing ``config.json`` remains available as a rollback/fallback
until the migration is explicitly adopted by the runtime.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


EYE_ROOT = Path(__file__).resolve().parent
EYE_WORKSPACE_PATH = EYE_ROOT / "eye_workspace.json"
EYE_MEMORY_PATH = EYE_ROOT / "eye_memory.json"
EYE_SKILLS_PATH = EYE_ROOT / "eye_skills.json"


class EyeConfigError(RuntimeError):
    """Raised when an EYES configuration file is missing or invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise EyeConfigError(f"missing EYES configuration: {path}") from exc
    except json.JSONDecodeError as exc:
        raise EyeConfigError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise EyeConfigError(f"EYES configuration must be an object: {path}")
    return value


def load_documents() -> dict[str, dict[str, Any]]:
    """Load the three domain-specific EYES JSON documents."""
    return {
        "workspace": _read_json(EYE_WORKSPACE_PATH),
        "memory": _read_json(EYE_MEMORY_PATH),
        "skills": _read_json(EYE_SKILLS_PATH),
    }


def load_config() -> dict[str, Any]:
    """Return the combined configuration in BirdEye's existing root format."""
    documents = load_documents()
    workspace_roots = documents["workspace"].get("roots", [])
    memory_root = documents["memory"].get("root")
    skills_root = documents["skills"].get("root")

    if not isinstance(workspace_roots, list):
        raise EyeConfigError("eye_workspace.json roots must be an array")
    if not isinstance(memory_root, dict):
        raise EyeConfigError("eye_memory.json root must be an object")
    if not isinstance(skills_root, dict):
        raise EyeConfigError("eye_skills.json root must be an object")

    roots = [*workspace_roots, memory_root, skills_root]
    ids = [root.get("id") for root in roots if isinstance(root, dict)]
    if any(not isinstance(root, dict) for root in roots):
        raise EyeConfigError("EYES roots must contain only objects")
    if len(ids) != len(set(ids)):
        raise EyeConfigError("EYES root ids must be unique")

    return {
        "schema_version": 1,
        "indexes": {
            "shared_vector": {
                "path": str(EYE_ROOT / "eye_Databa" / "eye_memory_query_01.db"),
                "gitignored": True,
                "namespaces": ["memory"],
            },
            "skills_query": {
                "path": str(EYE_ROOT / "eye_Databa" / "eye_skills_query_01.db"),
                "gitignored": True,
                "namespaces": ["skills"],
            }
        },
        "roots": roots,
        "source": "EYES",
        "source_files": {
            "workspace": str(EYE_WORKSPACE_PATH),
            "memory": str(EYE_MEMORY_PATH),
            "skills": str(EYE_SKILLS_PATH),
        },
    }


def validate() -> dict[str, Any]:
    """Validate all EYES files and return a compact summary."""
    config = load_config()
    roots = config["roots"]
    memory = next(root for root in roots if root.get("id") == "memory")
    return {
        "ok": True,
        "source": "EYES",
        "files": config["source_files"],
        "workspace_roots": sum(1 for root in roots if root.get("class") == "workspace"),
        "memory_sources": len(memory.get("sources", [])),
        "skills_roots": sum(1 for root in roots if root.get("id") == "skills"),
        "index_path": config["indexes"]["shared_vector"]["path"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate and print the EYES configuration map.")
    parser.add_argument("--json", action="store_true", help="print the combined configuration")
    args = parser.parse_args()
    try:
        result = load_config() if args.json else validate()
    except EyeConfigError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())