"""BirdEye -> GPT-Knowledge project workspace projection (LOCAL, read-only).

Authority
---------
- Request/response + operation contract: ``feat/local-workspace-change-bridge``
  (the documented outbound poller ``bridge/birdeye_request_bridge.py``).
- Git audit collection patterns: ``feat/workspace-diagnostic-bridge`` (active).

Invariants
----------
- No webhook. No new runtime authority. No arbitrary shell execution.
- Project roots come from explicit registry configuration, never inferred from
  a repository name. Unknown workspaces are rejected.
- The projection reads plan/status documents only; it never rewrites them.
- Every evidence record carries project/workspace/repository/branch/HEAD/
  observed_at and, when applicable, request_id attribution.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
DEFAULT_CONFIG = os.environ.get("BIRDEYE_PROJECTION_CONFIG", "birdeye_projection_config.json")
MAX_ROOT_LENGTH = 260

OPERATIONS = frozenset(
    {
        "workspace_status",
        "workspace_diagnosis",
        "git_compare",
        "run_validation_profile",
        "refresh_index",
    }
)

EVIDENCE_PROVEN = "PROVEN"
EVIDENCE_SUPPORTED = "SUPPORTED"
EVIDENCE_UNKNOWN = "UNKNOWN"
EVIDENCE_BLOCKED = "BLOCKED"

VALID_SYNC_STATES = ("LOCAL_ONLY", "SYNC_UNKNOWN", "REPO_STATE", "REPO_SYNCED", "SAVED_TO_REPO")

# Read-only git command families the projection is allowed to invoke. The
# first token matches any argv, so "-C <root>" prefix joins are safe: a git
# invocation is only accepted when every subcommand belongs to this allowlist.
GIT_ALLOWED_SUBCOMMANDS = {
    "status",
    "diff",
    "diff-tree",
    "log",
    "show",
    "rev-parse",
    "rev-list",
    "symbolic-ref",
    "branch",
    "ls-files",
    "merge-base",
    "for-each-ref",
    "name-rev",
}

PLAN_STATES = frozenset(
    {
        "NO_ACTIVE_PLAN",
        "CHAT_PROPOSAL_ONLY",
        "DOCUMENTATION_PENDING",
        "DOCUMENTED_CURRENT",
        "DOCUMENTED_STALE",
        "DOCUMENTATION_BLOCKED",
        "PLAN_OWNER_MISSING",
    }
)

MAX_REQUEST_AGE_SECONDS = 20 * 60


class ProjectionError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _path_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(f"{label} must be a non-empty string")
    return value.strip()


def _git(argv_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Invoke a read-only git command against an absolute repository root."""
    # Guard: git -C requires an absolute root we trust from config.
    if not argv_root.is_absolute():
        raise ProjectionError("workspace root must be absolute")
    # Callers always pass the git subcommand as the first argument after the
    # root: git -C <root> <subcommand> ...
    sub = args[0] if args else ""
    if sub not in GIT_ALLOWED_SUBCOMMANDS:
        raise ProjectionError(f"disallowed git subcommand: {sub}")
    command = ["git", "-C", str(argv_root), *args]
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProjectionError(f"git timed out: {' '.join(args)}") from exc
    except OSError as exc:
        raise ProjectionError(f"git could not be executed: {exc}") from exc


def _git_value(argv_root: Path, *args: str, default: str = "") -> str:
    try:
        result = _git(argv_root, *args)
    except ProjectionError:
        return default
    if result.returncode != 0:
        return default
    return result.stdout.strip()
# ---------------------------------------------------------------------------
# Registry / configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Project:
    """A single mapped GPT-Knowledge project with explicit configuration."""

    project_id: str
    name: str
    subtitle: str
    workspace_id: str
    local_workspace_root: str | None
    repository: str | None
    branch: str | None
    plan_document: str | None
    status_document: str | None
    validation_profile: str
    runtime_status_source: str | None

    @property
    def root_path(self) -> Path | None:
        if not self.local_workspace_root:
            return None
        return Path(self.local_workspace_root).expanduser()

    def to_dict(self) -> dict[str, Any]:
        return {
            "projectId": self.project_id,
            "projectName": self.name,
            "subtitle": self.subtitle,
            "workspaceId": self.workspace_id,
            "localWorkspaceRoot": self.local_workspace_root,
            "repository": self.repository,
            "branch": self.branch,
            "planDocument": self.plan_document,
            "statusDocument": self.status_document,
            "validationProfile": self.validation_profile,
            "runtimeStatusSource": self.runtime_status_source,
            "resolvedRootIsVerified": self.root_path is not None and self.root_path.is_dir(),
        }


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(f"{label} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ProjectionError(f"{label} must be a non-empty string when present")
    return value.strip()


def _parse_project(entry: dict[str, Any]) -> Project:
    project_id = _required_text(entry.get("projectId"), "projectId")
    name = _optional_text(entry.get("projectName"), "projectName") or project_id
    subtitle = _optional_text(entry.get("subtitle"), "subtitle") or ""
    workspace_id = _optional_text(entry.get("workspaceId"), "workspaceId") or project_id
    local_root = _optional_text(entry.get("localWorkspaceRoot"), "localWorkspaceRoot")
    repository = _optional_text(entry.get("repository"), "repository")
    branch = _optional_text(entry.get("branch"), "branch")
    plan_document = _optional_text(entry.get("planDocument"), "planDocument")
    status_document = _optional_text(entry.get("statusDocument"), "statusDocument")
    validation_profile = _optional_text(entry.get("validationProfile"), "validationProfile") or "default"
    runtime_source = _optional_text(entry.get("runtimeStatusSource"), "runtimeStatusSource")
    return Project(
        project_id=project_id,
        name=name,
        subtitle=subtitle,
        workspace_id=workspace_id,
        local_workspace_root=local_root,
        repository=repository,
        branch=branch,
        plan_document=plan_document,
        status_document=status_document,
        validation_profile=validation_profile,
        runtime_status_source=runtime_source,
    )


class Registry:
    """Deterministic project registry backed by explicit configuration."""

    def __init__(self, config_path: Path):
        self.config_path = config_path
        self._projects: list[Project] = []
        self._by_id: dict[str, Project] = {}
        self.reload()

    def reload(self) -> None:
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ProjectionError(f"BirdEye projection config not found: {self.config_path}") from exc
        except json.JSONDecodeError as exc:
            raise ProjectionError(f"Invalid projection config JSON: {exc}") from exc
        if not isinstance(raw, dict) or "projects" not in raw:
            raise ProjectionError("projection config must contain a projects array")
        entries = raw["projects"]
        if not isinstance(entries, list):
            raise ProjectionError("projection config projects must be an array")
        projects: list[Project] = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ProjectionError(f"project entry {index} must be an object")
            projects.append(_parse_project(entry))
        self._projects = projects
        dedupe: dict[str, Project] = {}
        for project in projects:
            if project.project_id in dedupe:
                raise ProjectionError(f"duplicate projectId: {project.project_id}")
            if project.workspace_id in dedupe:
                raise ProjectionError(f"duplicate workspaceId: {project.workspace_id}")
            dedupe[project.project_id] = project
        self._by_id = dict(dedupe)

    @property
    def projects(self) -> list[Project]:
        return list(self._projects)

    def resolve(self, project_id: str) -> Project:
        project = self._by_id.get(project_id.strip())
        if project is None:
            raise ProjectionError(f"unknown project: {project_id}")
        return project

    def resolve_for_request(self, workspace_id: str) -> Project:
        """Resolve a bounded request workspaceId. Rejects unknown workspaces."""
        for project in self._projects:
            if project.workspace_id == workspace_id:
                return project
        raise ProjectionError(f"unknown workspace: {workspace_id}")


# ---------------------------------------------------------------------------
# Git audit (read-only)
# ---------------------------------------------------------------------------


def _is_git_repository(root: Path) -> bool:
    result = _git(root, "rev-parse", "--is-inside-work-tree")
    return result.returncode == 0 and result.stdout.strip() == "true"


def _branch_and_head(root: Path) -> tuple[str | None, str | None, bool]:
    head = _git_value(root, "rev-parse", "HEAD") or None
    detached = head is None
    branch: str | None = None
    if head is not None:
        symbolic = _git_value(root, "symbolic-ref", "--quiet", "--short", "HEAD")
        branch = symbolic or None
    return branch, head, detached


def _tracking_state(root: Path) -> dict[str, Any]:
    branch, _, _ = _branch_and_head(root)
    if not branch:
        return {"upstreamRef": None, "tracked": False}
    upstream = _git_value(root, "rev-parse", "--abbrev-ref", "@{upstream}")
    if not upstream:
        return {"upstreamRef": None, "tracked": False}
    ahead_text = _git_value(root, "rev-list", "--count", "@{upstream}..HEAD")
    behind_text = _git_value(root, "rev-list", "--count", "HEAD..@{upstream}")
    ahead = int(ahead_text) if ahead_text.isdigit() else 0
    behind = int(behind_text) if behind_text.isdigit() else 0
    return {"upstreamRef": upstream, "tracked": True, "ahead": ahead, "behind": behind}


def _porcelain_lines(root: Path) -> list[str]:
    result = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]


def _classify_porcelain(lines: list[str]) -> dict[str, Any]:
    staged: list[str] = []
    modified: list[str] = []
    untracked: list[str] = []
    for line in lines:
        if len(line) < 3:
            continue
        code = line[:2]
        path = line[3:].strip() or line[2:].strip()
        if code[0] in "MARC":
            staged.append(path)
        if code[1] == "M":
            modified.append(path)
        if code[0] == "?":
            untracked.append(path)
    changed = sorted(set(staged) | set(modified) | set(untracked))
    return {
        "staged_paths": staged,
        "modified_paths": modified,
        "untracked_paths": untracked,
        "changed_paths": changed,
        "changed_path_count": len(changed),
        "dirty": bool(changed),
    }


def git_audit(project: Project, observed_at: str | None = None) -> dict[str, Any]:
    """Collect read-only Git evidence for a project's resolved workspace.

    Never fabricates values: if the workspace root is unavailable or is not a
    Git repository, the audit reports the honest evidence level.
    """
    observed_at = observed_at or utc_now()
    root = project.root_path

    if root is None:
        return {
            "workspaceRoot": None,
            "isRepository": False,
            "evidenceLevel": EVIDENCE_UNKNOWN,
            "reason": "no configured workspace root",
            "observedAt": observed_at,
        }
    if not root.is_dir():
        return {
            "workspaceRoot": str(root),
            "isRepository": False,
            "evidenceLevel": EVIDENCE_BLOCKED,
            "reason": "configured workspace root is not present on disk",
            "observedAt": observed_at,
        }

    try:
        is_repo = _is_git_repository(root)
    except ProjectionError as exc:
        return {
            "workspaceRoot": str(root),
            "isRepository": False,
            "evidenceLevel": EVIDENCE_BLOCKED,
            "reason": str(exc),
            "observedAt": observed_at,
        }

    if not is_repo:
        return {
            "workspaceRoot": str(root),
            "isRepository": False,
            "evidenceLevel": EVIDENCE_SUPPORTED,
            "reason": "workspace exists but is not a Git repository",
            "observedAt": observed_at,
        }

    branch, head, detached = _branch_and_head(root)
    lines = _porcelain_lines(root)
    classified = _classify_porcelain(lines)
    tracking = _tracking_state(root)

    repository_root = _git_value(root, "rev-parse", "--show-toplevel")
    return {
        "workspaceRoot": str(root),
        "repositoryRoot": repository_root or None,
        "isRepository": True,
        "branch": branch,
        "head": head,
        "detachedHead": detached,
        "tracked": tracking["tracked"],
        "upstreamRef": tracking.get("upstreamRef"),
        "ahead": tracking.get("ahead"),
        "behind": tracking.get("behind"),
        "dirty": classified["dirty"],
        "stagedPaths": classified["staged_paths"],
        "modifiedPaths": classified["modified_paths"],
        "untrackedPaths": classified["untracked_paths"],
        "changedPaths": classified["changed_paths"],
        "changedPathCount": classified["changed_path_count"],
        "evidenceLevel": EVIDENCE_PROVEN,
        "observedAt": observed_at,
    }


def git_compare(project: Project, target: str, observed_at: str | None = None) -> dict[str, Any]:
    """Report the divergence of resolved HEAD against an explicit target."""
    observed_at = observed_at or utc_now()
    root = project.root_path
    if root is None or not root.is_dir() or not _is_git_repository(root):
        return {
            "evidenceLevel": EVIDENCE_UNKNOWN,
            "reason": "workspace unavailable",
            "observedAt": observed_at,
        }
    ahead_text = _git_value(root, "rev-list", "--count", f"{target}..HEAD")
    behind_text = _git_value(root, "rev-list", "--count", f"HEAD..{target}")
    diff_text = _git_value(root, "diff", "--stat", f"{target}...HEAD")
    return {
        "target": target,
        "ahead": int(ahead_text) if ahead_text.isdigit() else None,
        "behind": int(behind_text) if behind_text.isdigit() else None,
        "diffStat": diff_text or None,
        "evidenceLevel": EVIDENCE_PROVEN,
        "observedAt": observed_at,
    }


# ---------------------------------------------------------------------------
# Plan / status projection (read-only)
# ---------------------------------------------------------------------------


def _read_json_document(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    candidate = Path(path).expanduser()
    try:
        value = json.loads(candidate.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return value if isinstance(value, dict) else None


def _git_dir_revision(path: str | None, observed_at: str) -> str | None:
    """Derive a document revision from its containing git repo, when present."""
    if not path:
        return None
    candidate = Path(path).expanduser()
    in_repo = bool(candidate.is_file())
    if not in_repo:
        return None
    return _git_value(candidate.parent, "rev-parse", "--short", "HEAD") or None


def plan_status_projection(project: Project, observed_at: str | None = None) -> dict[str, Any]:
    """Project the canonical plan/status documents. Read-only; never rewrites.

    Only DOCUMENTED_CURRENT is authoritative. If the document cannot be
    resolved the projection reports a non-authoritative plan state.
    """
    observed_at = observed_at or utc_now()
    plan = _read_json_document(project.plan_document)
    status = _read_json_document(project.status_document)

    plan_doc = project.plan_document
    if plan is None and plan_doc:
        state = "PLAN_OWNER_MISSING"
    elif plan is None:
        state = "PLAN_OWNER_MISSING"
    else:
        state = "DOCUMENTED_CURRENT"

    plan_truth = plan.get("plan_truth") if isinstance(plan, dict) else None
    active_gate = None
    next_question = None
    if isinstance(plan_truth, dict):
        active_gate = plan_truth.get("active_gate") or plan_truth.get("active_node")
        next_question = plan_truth.get("next_single_question")
    if not active_gate and isinstance(plan, dict):
        # Canonical plan documents carry the gate on the node marked blocked /
        # next inside the Active Gate lane; fall back to the declared node.
        active_gate = plan.get("active_node") or plan.get("active_gate")
    if not next_question and isinstance(status, dict):
        acceptance = status.get("next_acceptance")
        if isinstance(acceptance, dict):
            next_question = acceptance.get("question")

    revision = _git_dir_revision(plan_doc, observed_at) if plan_doc else None
    status_revision = _git_dir_revision(project.status_document, observed_at) if project.status_document else None

    return {
        "planState": state,
        "planDocument": plan_doc,
        "statusDocument": project.status_document,
        "documentRevision": revision,
        "statusRevision": status_revision,
        "lastVerified": observed_at,
        "activeGate": active_gate,
        "nextSingleQuestion": next_question,
        "authoritative": state == "DOCUMENTED_CURRENT",
        "evidenceLevel": EVIDENCE_PROVEN if plan is not None else EVIDENCE_UNKNOWN,
    }


def runtime_status_projection(project: Project, observed_at: str | None = None) -> dict[str, Any]:
    """Surface runtime status from the project's explicit runtime source (if any)."""
    observed_at = observed_at or utc_now()
    source = project.runtime_status_source
    if not source:
        return {
            "source": None,
            "evidenceLevel": EVIDENCE_UNKNOWN,
            "reason": "no runtime status source configured",
            "observedAt": observed_at,
        }
    path = Path(source).expanduser()
    if not path.is_file():
        return {
            "source": str(path),
            "evidenceLevel": EVIDENCE_BLOCKED,
            "reason": "runtime status source not present",
            "observedAt": observed_at,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {
            "source": str(path),
            "evidenceLevel": EVIDENCE_BLOCKED,
            "reason": "runtime status source is not valid JSON",
            "observedAt": observed_at,
        }
    return {
        "source": str(path),
        "evidenceLevel": EVIDENCE_PROVEN,
        "value": raw if isinstance(raw, dict) else None,
        "observedAt": observed_at,
    }


# ---------------------------------------------------------------------------
# Bounded request model
# ---------------------------------------------------------------------------


@dataclass
class BoundedRequest:
    workspace_id: str
    request_id: str
    operation: str
    scope: dict[str, Any]
    created_at: str
    expires_at: str | None
    mutation_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "workspaceId": self.workspace_id,
            "requestId": self.request_id,
            "operation": self.operation,
            "scope": self.scope,
            "createdAt": self.created_at,
            "expiresAt": self.expires_at,
            "mutationAllowed": self.mutation_allowed,
        }


def parse_bounded_request(value: Any) -> BoundedRequest:
    if not isinstance(value, dict):
        raise ProjectionError("request must be a JSON object")

    workspace_id = _required_text(value.get("workspaceId"), "workspaceId")
    request_id = _required_text(value.get("requestId"), "requestId")
    operation = _required_text(value.get("operation"), "operation")
    if operation not in OPERATIONS:
        raise ProjectionError(f"unsupported operation: {operation}")

    scope = value.get("scope", {})
    if not isinstance(scope, dict):
        raise ProjectionError("scope must be an object")

    created_at = _required_text(value.get("createdAt"), "createdAt")
    expires_at = value.get("expiresAt")

    mutation = value.get("mutationAllowed", False)
    if not isinstance(mutation, bool):
        raise ProjectionError("mutationAllowed must be a boolean")

    request = BoundedRequest(
        workspace_id=workspace_id,
        request_id=request_id,
        operation=operation,
        scope=scope,
        created_at=created_at,
        expires_at=_optional_text(expires_at, "expiresAt") if expires_at is not None else None,
        mutation_allowed=mutation,
    )

    if request.mutation_allowed:
        raise ProjectionError("mutation requests are rejected: mutationAllowed must be false")

    created = parse_timestamp(request.created_at)
    expires = parse_timestamp(request.expires_at)
    if created is None:
        raise ProjectionError("createdAt must be an ISO-8601 timestamp")
    if expires is not None and expires < datetime.now(timezone.utc):
        raise ProjectionError("expired request")

    profile = scope.get("validationProfile")
    if profile is not None and (not isinstance(profile, str) or not profile.strip()):
        raise ProjectionError("validationProfile must be a non-empty string")

    return request


def _content_fingerprint(request: BoundedRequest) -> str:
    return json.dumps(
        {
            "workspaceId": request.workspace_id,
            "operation": request.operation,
            "scope": request.scope,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


# ---------------------------------------------------------------------------
# Evidence assembler / journal
# ---------------------------------------------------------------------------


def projection_alignment(
    project: Project, audit: dict[str, Any], plan_truth: dict[str, Any], observed_at: str
) -> dict[str, Any]:
    """Compare the documented projection head with the observed repository HEAD.

    Surfaces divergence instead of hiding it: the canonical documents declare
    which repository state is intended to be the current project projection.
    """
    documented = None
    for path in (project.status_document, project.plan_document):
        document = _read_json_document(path)
        if document and document.get("source_head"):
            documented = str(document["source_head"])
            break

    observed = audit.get("head")
    if documented and observed:
        state = "ALIGNED" if documented == observed else "DIVERGED"
        reason = None if state == "ALIGNED" else (
            "documented source_head differs from observed workspace HEAD"
        )
    else:
        state = "UNKNOWN"
        reason = "documented source_head or observed HEAD unavailable"

    return {
        "state": state,
        "documentedSourceHead": documented,
        "observedHead": observed,
        "reason": reason,
        "evidenceLevel": EVIDENCE_PROVEN if state in ("ALIGNED", "DIVERGED") else EVIDENCE_UNKNOWN,
        "observedAt": observed_at,
    }


def produce_projection(
    project: Project, request: BoundedRequest | None = None, observed_at: str | None = None
) -> dict[str, Any]:
    """Assemble a single attributable evidence record for a project."""
    observed_at = observed_at or utc_now()
    audit = git_audit(project, observed_at=observed_at)
    plan_truth = plan_status_projection(project, observed_at=observed_at)
    runtime = runtime_status_projection(project, observed_at=observed_at)
    alignment = projection_alignment(project, audit, plan_truth, observed_at)

    comparison_target = request.scope.get("compareTarget") if request else None
    comparison = None
    if comparison_target:
        comparison = git_compare(project, str(comparison_target), observed_at=observed_at)

    # A dirty workspace is a real fact (REVIEW signal) but not a failure.
    verdict = "PASS"
    if audit.get("evidenceLevel") in (EVIDENCE_UNKNOWN, EVIDENCE_BLOCKED):
        verdict = "REVIEW"
    elif audit.get("dirty"):
        verdict = "REVIEW"
    elif plan_truth.get("authoritative") is not True and plan_truth.get("planState") not in ("PLAN_OWNER_MISSING",):
        verdict = "REVIEW"
    elif runtime.get("evidenceLevel") == EVIDENCE_BLOCKED:
        verdict = "REVIEW"
    elif alignment.get("state") == "DIVERGED":
        verdict = "REVIEW"

    return {
        "schemaVersion": SCHEMA_VERSION,
        "sources": [
            {
                "gateway": "bird-projection-projection",
                "owner": "Letterblack0306/Letterblack_BirdEye (feat/workspace-diagnostic-bridge)",
                "authority": "feat/local-workspace-change-bridge request/response contract",
                "noWebhook": True,
                "readOnly": True,
            }
        ],
        "attribution": {
            "projectId": project.project_id,
            "projectName": project.name,
            "workspaceId": project.workspace_id,
            "repository": project.repository,
            "workspaceRoot": str(project.root_path) if project.root_path else None,
            "branch": audit.get("branch"),
            "head": audit.get("head"),
            "observedAt": observed_at,
            "requestId": request.request_id if request else None,
            "operation": request.operation if request else None,
        },
        "git": audit,
        "planStatus": plan_truth,
        "runtime": runtime,
        "alignment": alignment,
        "comparison": comparison,
        "verdict": verdict,
        "syncState": "LOCAL_ONLY",
    }


def _journal_path(config_path: Path) -> Path:
    return config_path.parent / "state" / "projection_requests.jsonl"


def _evidence_path(config_path: Path, project_id: str) -> Path:
    return config_path.parent / "state" / "projections" / f"{project_id}.json"


def _read_journal(config_path: Path) -> list[dict[str, Any]]:
    journal = _journal_path(config_path)
    records: list[dict[str, Any]] = []
    if journal.exists():
        for raw in journal.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    return records


def _append_journal(config_path: Path, record: dict[str, Any]) -> None:
    journal = _journal_path(config_path)
    journal.parent.mkdir(parents=True, exist_ok=True)
    with open(journal, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def handle_request(raw: Any, config_path: Path) -> dict[str, Any]:
    """Validate, execute, and journal one bounded request. Idempotent."""
    request = parse_bounded_request(raw)
    registry = Registry(config_path)
    project = registry.resolve_for_request(request.workspace_id)

    # Idempotency: an existing response for the same requestId is replayed if
    # its content fingerprint matches; a conflicting duplicate is rejected.
    existing = [r for r in _read_journal(config_path) if r.get("requestId") == request.request_id]
    if existing:
        prior = existing[-1]
        if prior.get("contentFingerprint") == _content_fingerprint(request):
            return _journaled_response(prior, replay=True)
        raise ProjectionError(f"duplicate request with conflicting content: {request.request_id}")

    observed_at = utc_now()
    projection = produce_projection(project, request=request, observed_at=observed_at)
    record = {
        "schemaVersion": SCHEMA_VERSION,
        "requestId": request.request_id,
        "workspaceId": request.workspace_id,
        "projectId": project.project_id,
        "operation": request.operation,
        "createdAt": request.created_at,
        "expiresAt": request.expires_at,
        "contentFingerprint": _content_fingerprint(request),
        "status": "completed",
        "completedAt": observed_at,
        "error": None,
        "result": projection,
        "replay": False,
    }
    _append_journal(config_path, record)
    return _journaled_response(record)


def _journaled_response(record: dict[str, Any], replay: bool = False) -> dict[str, Any]:
    base = {
        "schemaVersion": SCHEMA_VERSION,
        "requestId": record.get("requestId"),
        "workspaceId": record.get("workspaceId"),
        "projectId": record.get("projectId"),
        "operation": record.get("operation"),
        "status": record.get("status"),
        "completedAt": record.get("completedAt"),
        "error": record.get("error"),
        "result": record.get("result"),
    }
    if replay:
        base["replayed"] = True
    return base


def request_history(config_path: Path, *, limit: int = 50) -> dict[str, Any]:
    records = _read_journal(config_path)
    records = records[-limit:]
    return {"count": len(records), "records": records}


def export_projection(config_path: Path, project_id: str, out: str | None = None) -> dict[str, Any]:
    """Produce and persist an evidence snapshot for the UI to render."""
    registry = Registry(config_path)
    project = registry.resolve(project_id)
    projection = produce_projection(project)
    target = None
    if out:
        target = Path(out)
    elif project_id:
        target = _evidence_path(config_path, project.project_id)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(projection, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"projectId": project.project_id, "written": str(target) if target else None, "projection": projection}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BirdEye project workspace projection (local, read-only)")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    sub = parser.add_subparsers(dest="command", required=True)

    p_registry = sub.add_parser("projects", help="list the deterministic project registry")
    p_registry.add_argument("--full", action="store_true")

    p_audit = sub.add_parser("audit", help="read-only git audit for a project")
    p_audit.add_argument("project")
    p_audit.add_argument("--compare", help="comparison target ref for git_compare")

    p_proj = sub.add_parser("projection", help="full attributable projection for a project")
    p_proj.add_argument("project")

    p_req = sub.add_parser("request", help="process one bounded JSON request file")
    p_req.add_argument("--file", required=True)

    p_hist = sub.add_parser("history", help="show bounded request/response history")
    p_hist.add_argument("--limit", type=int, default=50)

    p_export = sub.add_parser("export", help="write an evidence snapshot for the UI")
    p_export.add_argument("project")
    p_export.add_argument("--out")

    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser()

    try:
        if args.command == "projects":
            registry = Registry(config_path)
            result = {"projects": [p.to_dict() for p in registry.projects]}
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.command == "audit":
            registry = Registry(config_path)
            project = registry.resolve(args.project)
            observed_at = utc_now()
            audit = git_audit(project, observed_at=observed_at)
            if args.compare:
                audit["comparison"] = git_compare(project, args.compare, observed_at=observed_at)
            print(json.dumps(audit, indent=2, ensure_ascii=False))
            return 0
        if args.command == "projection":
            registry = Registry(config_path)
            project = registry.resolve(args.project)
            print(json.dumps(produce_projection(project), indent=2, ensure_ascii=False))
            return 0
        if args.command == "request":
            raw = json.loads(Path(args.file).read_text(encoding="utf-8"))
            try:
                response = handle_request(raw, config_path)
            except ProjectionError as exc:
                response = {
                    "schemaVersion": SCHEMA_VERSION,
                    "status": "rejected",
                    "error": type(exc).__name__,
                    "message": str(exc),
                }
            print(json.dumps(response, indent=2, ensure_ascii=False))
            return 2 if response.get("status") == "rejected" else 0
        if args.command == "history":
            print(json.dumps(request_history(config_path, limit=args.limit), indent=2, ensure_ascii=False))
            return 0
        if args.command == "export":
            print(json.dumps(export_projection(config_path, args.project, args.out), indent=2, ensure_ascii=False))
            return 0
    except ProjectionError as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, indent=2, ensure_ascii=False))
        return 2
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": "JSONDecodeError", "message": str(exc)}, indent=2, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())