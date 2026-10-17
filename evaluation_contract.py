"""Portable, side-effect-free evaluation result contract (version 1).

This module does not execute commands, authorize operations, or infer success.
Projects keep their own runners; BirdEye can validate and aggregate their evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "1"
RESULTS = frozenset({"PASS", "FAIL", "BLOCKED", "UNVERIFIED"})
REQUIRED = ("schema_version", "evaluation_id", "project_id", "requirement_id",
            "status", "evidence", "source_revision", "runner")


class EvaluationContractError(ValueError):
    pass


def validate_result(record: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise EvaluationContractError("result must be an object")
    missing = [field for field in REQUIRED if field not in record]
    if missing:
        raise EvaluationContractError("missing: " + ", ".join(missing))
    if record["schema_version"] != SCHEMA_VERSION:
        raise EvaluationContractError("unsupported schema_version")
    if record["status"] not in RESULTS:
        raise EvaluationContractError("unknown status")
    for field in ("evaluation_id", "project_id", "requirement_id", "source_revision", "runner"):
        if not isinstance(record[field], str) or not record[field].strip():
            raise EvaluationContractError(field + " must be nonempty text")
    evidence = record["evidence"]
    if not isinstance(evidence, list) or any(
        not isinstance(e, dict) or
        not isinstance(e.get("kind"), str) or not e["kind"].strip() or
        not isinstance(e.get("reference"), str) or not e["reference"].strip()
        for e in evidence
    ):
        raise EvaluationContractError("evidence must be an array of {kind, reference}")
    if record["status"] == "PASS" and not evidence:
        raise EvaluationContractError("PASS requires evidence")
    return record


def summarize(records: list[dict[str, Any]], required: list[str]) -> dict[str, Any]:
    """Missing required evaluations never become PASS; contradictory records fail."""
    if len(set(required)) != len(required) or not all(isinstance(x, str) and x for x in required):
        raise EvaluationContractError("required IDs must be unique nonempty strings")
    by_id: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        validate_result(record)
        by_id.setdefault(record["evaluation_id"], []).append(record)
    outcomes: dict[str, str] = {}
    for key in required:
        candidates = by_id.get(key, [])
        if not candidates:
            outcomes[key] = "UNVERIFIED"
        elif len(candidates) != 1:
            outcomes[key] = "FAIL"
        else:
            outcomes[key] = candidates[0]["status"]
    overall = ("FAIL" if "FAIL" in outcomes.values() else
               "BLOCKED" if "BLOCKED" in outcomes.values() else
               "UNVERIFIED" if "UNVERIFIED" in outcomes.values() else "PASS")
    return {"schema_version": SCHEMA_VERSION, "status": overall, "outcomes": outcomes}


def result_digest(record: dict[str, Any]) -> str:
    """Stable content digest; not a signature or proof of a real-world effect."""
    validate_result(record)
    serialized = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def load_results(path: str | Path) -> list[dict[str, Any]]:
    """Explicit local file only. No filesystem traversal or implicit execution."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise EvaluationContractError("result document must be an array")
    for item in payload:
        validate_result(item)
    return payload
