"""Focused tests for the BirdEye local project workspace projection.

All tests are read-only: they use temporary registries and temp git repos.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import birdeye_projection as bp


def _write_config(tmp_path: Path, projects: list[dict]) -> Path:
    config = tmp_path / "birdeye_projection_config.json"
    config.write_text(json.dumps({"schemaVersion": 1, "projects": projects}), encoding="utf-8")
    return config


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    (path / "file.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "file.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"],
        check=True,
    )


def _project(root: Path | None, **overrides) -> dict:
    entry = {
        "projectId": "p1",
        "projectName": "P One",
        "workspaceId": "p1",
        "localWorkspaceRoot": str(root) if root else None,
        "repository": "Letterblack0306/p-one",
        "branch": "main",
        "planDocument": None,
        "statusDocument": None,
        "validationProfile": "default",
        "runtimeStatusSource": None,
    }
    entry.update(overrides)
    return entry


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_resolves_explicit_project(tmp_path: Path):
    root = tmp_path / "ws"
    root.mkdir()
    config = _write_config(tmp_path, [_project(root)])
    registry = bp.Registry(config)
    project = registry.resolve("p1")
    assert project.project_id == "p1"
    assert project.root_path == root


def test_registry_rejects_unknown_project(tmp_path: Path):
    config = _write_config(tmp_path, [_project(None)])
    with pytest.raises(bp.ProjectionError):
        bp.Registry(config).resolve("does-not-exist")


def test_registry_rejects_duplicate_ids(tmp_path: Path):
    config = _write_config(tmp_path, [_project(None), _project(None)])
    with pytest.raises(bp.ProjectionError, match="duplicate projectId"):
        bp.Registry(config)


def test_registry_rejects_unknown_request_workspace(tmp_path: Path):
    config = _write_config(tmp_path, [_project(None)])
    with pytest.raises(bp.ProjectionError, match="unknown workspace"):
        bp.Registry(config).resolve_for_request("nope")


# ---------------------------------------------------------------------------
# Git audit
# ---------------------------------------------------------------------------


def test_audit_clean_repo_is_proven_and_not_dirty(tmp_path: Path):
    root = tmp_path / "clean"
    _init_repo(root)
    config = _write_config(tmp_path, [_project(root)])
    project = bp.Registry(config).resolve("p1")
    audit = bp.git_audit(project)
    assert audit["isRepository"] is True
    assert audit["evidenceLevel"] == bp.EVIDENCE_PROVEN
    assert audit["dirty"] is False
    assert audit["head"]
    assert isinstance(audit["changedPathCount"], int)


def test_audit_dirty_repo_reports_modified_and_untracked(tmp_path: Path):
    root = tmp_path / "dirty"
    _init_repo(root)
    (root / "modified.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "modified.txt"], check=True)
    (root / "untracked.txt").write_text("y\n", encoding="utf-8")
    config = _write_config(tmp_path, [_project(root)])
    project = bp.Registry(config).resolve("p1")
    audit = bp.git_audit(project)
    assert audit["dirty"] is True
    assert "modified.txt" in audit["stagedPaths"]
    assert "untracked.txt" in audit["untrackedPaths"]


def test_audit_missing_root_is_blocked_not_fabricated(tmp_path: Path):
    config = _write_config(tmp_path, [_project(tmp_path / "not-there")])
    audit = bp.git_audit(bp.Registry(config).resolve("p1"))
    assert audit["isRepository"] is False
    assert audit["evidenceLevel"] == bp.EVIDENCE_BLOCKED


def test_audit_no_root_is_unknown(tmp_path: Path):
    config = _write_config(tmp_path, [_project(None)])
    audit = bp.git_audit(bp.Registry(config).resolve("p1"))
    assert audit["evidenceLevel"] == bp.EVIDENCE_UNKNOWN


def test_audit_non_git_directory_is_supported_level(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    config = _write_config(tmp_path, [_project(plain)])
    audit = bp.git_audit(bp.Registry(config).resolve("p1"))
    assert audit["isRepository"] is False
    assert audit["evidenceLevel"] == bp.EVIDENCE_SUPPORTED


# ---------------------------------------------------------------------------
# Bounded request model
# ---------------------------------------------------------------------------


def _request(**overrides) -> dict:
    base = {
        "workspaceId": "p1",
        "requestId": "req-1",
        "operation": "workspace_status",
        "scope": {},
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "expiresAt": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
        "mutationAllowed": False,
    }
    base.update(overrides)
    return base


def test_request_accepts_valid_bounded_request(tmp_path: Path):
    root = tmp_path / "ws"
    _init_repo(root)
    config = _write_config(tmp_path, [_project(root)])
    response = bp.handle_request(_request(), config)
    assert response["status"] == "completed"
    assert response["result"]["attribution"]["requestId"] == "req-1"
    assert response["result"]["attribution"]["head"]
    assert response.get("replayed") is not True


def test_request_is_idempotent_for_identical_content(tmp_path: Path):
    root = tmp_path / "ws"
    _init_repo(root)
    config = _write_config(tmp_path, [_project(root)])
    first = bp.handle_request(_request(), config)
    second = bp.handle_request(_request(), config)
    assert second.get("replayed") is True
    assert first["result"]["attribution"]["observedAt"] == second["result"]["attribution"]["observedAt"]


def test_request_rejects_conflicting_duplicate_id(tmp_path: Path):
    root = tmp_path / "ws"
    _init_repo(root)
    config = _write_config(tmp_path, [_project(root)])
    bp.handle_request(_request(), config)
    with pytest.raises(bp.ProjectionError, match="conflicting content"):
        bp.handle_request(_request(operation="git_compare"), config)


def test_request_rejects_unknown_workspace(tmp_path: Path):
    config = _write_config(tmp_path, [_project(None)])
    with pytest.raises(bp.ProjectionError, match="unknown workspace"):
        bp.handle_request(_request(workspaceId="ghost"), config)


def test_request_rejects_mutation(tmp_path: Path):
    with pytest.raises(bp.ProjectionError, match="mutation"):
        bp.parse_bounded_request(_request(mutationAllowed=True))


def test_request_rejects_expired(tmp_path: Path):
    expired = _request(
        expiresAt=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    )
    with pytest.raises(bp.ProjectionError, match="expired"):
        bp.parse_bounded_request(expired)


def test_request_rejects_invalid_operation(tmp_path: Path):
    with pytest.raises(bp.ProjectionError, match="unsupported operation"):
        bp.parse_bounded_request(_request(operation="shell_exec"))


def test_request_history_is_journaled(tmp_path: Path):
    root = tmp_path / "ws"
    _init_repo(root)
    config = _write_config(tmp_path, [_project(root)])
    bp.handle_request(_request(requestId="r-a"), config)
    history = bp.request_history(config)
    assert history["count"] == 1
    assert history["records"][0]["requestId"] == "r-a"


# ---------------------------------------------------------------------------
# Plan / status + projection assembly
# ---------------------------------------------------------------------------


def test_plan_status_missing_documents_reports_plan_owner_missing(tmp_path: Path):
    config = _write_config(
        tmp_path,
        [
            _project(
                None,
                planDocument=str(tmp_path / "missing" / "plan.json"),
                statusDocument=str(tmp_path / "missing" / "status.json"),
            )
        ],
    )
    project = bp.Registry(config).resolve("p1")
    plan = bp.plan_status_projection(project)
    assert plan["planState"] == "PLAN_OWNER_MISSING"
    assert plan["authoritative"] is False


def test_plan_status_documented_current_is_authoritative(tmp_path: Path):
    plan_doc = tmp_path / "plan.json"
    plan_doc.write_text(json.dumps({"active_node": "gate-1"}), encoding="utf-8")
    status_doc = tmp_path / "status.json"
    status_doc.write_text(
        json.dumps({"next_acceptance": {"question": "Does the gate hold?"}}), encoding="utf-8"
    )
    config = _write_config(
        tmp_path, [_project(None, planDocument=str(plan_doc), statusDocument=str(status_doc))]
    )
    project = bp.Registry(config).resolve("p1")
    plan = bp.plan_status_projection(project)
    assert plan["planState"] == "DOCUMENTED_CURRENT"
    assert plan["authoritative"] is True
    assert plan["activeGate"] == "gate-1"
    assert plan["nextSingleQuestion"] == "Does the gate hold?"


def test_projection_attributes_evidence_and_never_writes_plan(tmp_path: Path):
    root = tmp_path / "ws"
    _init_repo(root)
    plan_doc = tmp_path / "plan.json"
    plan_doc.write_text(json.dumps({"active_node": "gate-9"}), encoding="utf-8")
    config = _write_config(tmp_path, [_project(root, planDocument=str(plan_doc))])
    project = bp.Registry(config).resolve("p1")
    before = plan_doc.read_text(encoding="utf-8")
    projection = bp.produce_projection(project)
    assert projection["attribution"]["projectId"] == "p1"
    assert projection["attribution"]["workspaceId"] == "p1"
    assert projection["attribution"]["repository"] == "Letterblack0306/p-one"
    observed_branch = projection["attribution"]["branch"]
    assert isinstance(observed_branch, str) and observed_branch
    assert projection["git"]["branch"] == observed_branch
    assert projection["attribution"]["head"]
    assert projection["attribution"]["observedAt"]
    assert projection["git"]["evidenceLevel"] == bp.EVIDENCE_PROVEN
    assert projection["planStatus"]["activeGate"] == "gate-9"
    assert projection["sources"][0]["noWebhook"] is True
    assert projection["sources"][0]["readOnly"] is True
    assert projection["syncState"] in bp.VALID_SYNC_STATES
    assert plan_doc.read_text(encoding="utf-8") == before


def test_projection_dirty_workspace_yields_review_not_failure(tmp_path: Path):
    root = tmp_path / "dirty"
    _init_repo(root)
    (root / "extra.txt").write_text("z\n", encoding="utf-8")
    config = _write_config(tmp_path, [_project(root)])
    projection = bp.produce_projection(bp.Registry(config).resolve("p1"))
    assert projection["verdict"] == "REVIEW"


# ---------------------------------------------------------------------------
# Git command allowlist (no arbitrary execution)
# ---------------------------------------------------------------------------


def test_git_helper_refuses_non_allowlisted_subcommand(tmp_path: Path):
    with pytest.raises(bp.ProjectionError, match="disallowed"):
        bp._git(tmp_path, "push", "origin", "main")


# ---------------------------------------------------------------------------
# Documented vs observed head alignment
# ---------------------------------------------------------------------------


def test_alignment_diverged_when_documented_head_differs(tmp_path: Path):
    root = tmp_path / "ws"
    _init_repo(root)
    plan_doc = tmp_path / "plan.json"
    plan_doc.write_text(
        json.dumps({"source_head": "26c72b455de71d6ff5d8505888c6dfd6304bec65"}), encoding="utf-8"
    )
    config = _write_config(tmp_path, [_project(root, planDocument=str(plan_doc))])
    project = bp.Registry(config).resolve("p1")
    audit = bp.git_audit(project)
    alignment = bp.projection_alignment(project, audit, {}, bp.utc_now())
    assert alignment["state"] == "DIVERGED"
    assert alignment["documentedSourceHead"] == "26c72b455de71d6ff5d8505888c6dfd6304bec65"
    assert alignment["observedHead"] == audit["head"]
    assert alignment["evidenceLevel"] == bp.EVIDENCE_PROVEN
    projection = bp.produce_projection(project)
    assert projection["verdict"] == "REVIEW"


def test_alignment_aligned_when_documented_head_matches(tmp_path: Path):
    root = tmp_path / "ws"
    _init_repo(root)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    plan_doc = tmp_path / "plan.json"
    plan_doc.write_text(json.dumps({"source_head": head}), encoding="utf-8")
    config = _write_config(tmp_path, [_project(root, planDocument=str(plan_doc))])
    project = bp.Registry(config).resolve("p1")
    alignment = bp.projection_alignment(project, bp.git_audit(project), {}, bp.utc_now())
    assert alignment["state"] == "ALIGNED"


def test_alignment_unknown_without_documented_head(tmp_path: Path):
    config = _write_config(tmp_path, [_project(None)])
    project = bp.Registry(config).resolve("p1")
    alignment = bp.projection_alignment(project, {"head": None}, {}, bp.utc_now())
    assert alignment["state"] == "UNKNOWN"
    assert alignment["evidenceLevel"] == bp.EVIDENCE_UNKNOWN