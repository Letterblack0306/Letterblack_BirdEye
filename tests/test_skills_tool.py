from __future__ import annotations

import json
from pathlib import Path

import pytest

import mcp_server


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skills_root = tmp_path / "skills"
    (skills_root / "testing").mkdir(parents=True)
    (skills_root / "testing" / "runtime.md").write_text(
        "# Runtime acceptance\n\nValidate live runtime behavior with a user-visible check.\n",
        encoding="utf-8",
    )
    (skills_root / "governance.md").write_text(
        "# Governance\n\nUse current workspace evidence as the authority boundary.\n",
        encoding="utf-8",
    )
    vector_db = tmp_path / "state" / "vectors" / "semantic.db"
    monkeypatch.setattr(mcp_server, "SKILLS_ROOT", skills_root.resolve())
    monkeypatch.setattr(mcp_server, "SHARED_VECTOR_INDEX", vector_db)
    return skills_root


def test_skills_query_resolves_aliases_through_catalog_first(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    skill_dir = tmp_path / "skills" / "creative" / "theme-factory"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: theme-factory\n"
        "description: Toolkit for styling artifacts with a theme.\n"
        "query_aliases:\n"
        "  - dark\n"
        "  - toned\n"
        "  - muted\n"
        "  - deep tones\n"
        "---\n"
        "# Theme Factory\n\n"
        "## Theme Alias Mapping\n\n"
        "| Query alias(es) | Theme |\n"
        "| dark, midnight, deep tones | Midnight Galaxy |\n",
        encoding="utf-8",
    )
    # A large decoy file full of the word "dark" must NOT outrank the skill
    # catalog: ranking happens inside the catalog candidate set first.
    (tmp_path / "skills" / "themes-docs.md").write_text(
        ("dark css theme " * 500) + "toned muted",
        encoding="utf-8",
    )

    result = mcp_server.invoke("skills", {
        "operation": "query",
        "query": "dark toned theme",
        "max_results": 5,
        "chunk_chars": 800,
    })

    assert result["ok"] is True
    assert result["results"][0]["path"].endswith("theme-factory/SKILL.md")
    assert "Midnight Galaxy" in result["results"][0]["section"]
    assert all(
        not item["path"].endswith("themes-docs.md") for item in result["results"]
    )


def test_skills_query_falls_back_to_full_corpus_without_catalog_match(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)

    result = mcp_server.invoke("skills", {
        "operation": "query",
        "query": "runtime acceptance",
        "max_results": 5,
        "chunk_chars": 400,
    })

    assert result["ok"] is True
    assert result["results"][0]["path"] == "testing/runtime.md"


def test_skills_query_matches_metadata_tags_categories_platforms(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    skill_dir = tmp_path / "skills" / "Build-Install-Release" / "application-packaging"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: application-packaging\n"
        "description: Package applications into distributable installers.\n"
        "aliases:\n"
        "  - installer\n"
        "  - setup exe\n"
        "categories:\n"
        "  - Build-Install-Release\n"
        "  - Development\n"
        "capabilities:\n"
        "  - package\n"
        "  - install\n"
        "  - distribute\n"
        "platforms:\n"
        "  - windows\n"
        "  - linux\n"
        "  - macos\n"
        "technologies:\n"
        "  - msix\n"
        "  - msi\n"
        "  - wix\n"
        "  - inno-setup\n"
        "artifact_types:\n"
        "  - installer\n"
        "  - executable\n"
        "---\n"
        "# Application Packaging\n\n"
        "Build MSIX and WiX installers for distribution.\n",
        encoding="utf-8",
    )

    # Query uses none of the body words — retrieval must come from metadata.
    result = mcp_server.invoke("skills", {
        "operation": "query",
        "query": "make msix installer for windows",
        "max_results": 3,
        "chunk_chars": 800,
    })

    assert result["ok"] is True
    assert result["results"][0]["path"].endswith("application-packaging/SKILL.md")


def test_skills_refresh_build_component_exclusion_rule(tmp_path, monkeypatch):
    """Operational rule: a literal 'build' path component is EXCLUDED by
    _skills_refresh, so category folders must use 'Build-Install-Release'
    (or any name that is not exactly 'build'), never 'build/'."""
    skills_root = _configure(tmp_path, monkeypatch)
    safe = skills_root / "Build-Install-Release" / "some-skill"
    safe.mkdir(parents=True)
    (safe / "SKILL.md").write_text(
        "---\nname: some-skill\ndescription: Safe category folder.\n---\n# Safe\n",
        encoding="utf-8",
    )
    unsafe = skills_root / "build" / "doomed-skill"
    unsafe.mkdir(parents=True)
    (unsafe / "SKILL.md").write_text(
        "---\nname: doomed-skill\ndescription: Inside excluded build component.\n---\n# Doomed\n",
        encoding="utf-8",
    )

    mcp_server._skills_refresh()
    result = mcp_server.invoke("skills", {
        "operation": "query",
        "query": "safe category folder doomed",
        "max_results": 5,
        "chunk_chars": 400,
    })

    indexed = {item["path"] for item in result["results"]}
    assert any(p.endswith("some-skill/SKILL.md") for p in indexed), indexed
    assert not any("doomed-skill" in p for p in indexed), indexed


def test_one_consolidated_skills_tool_is_registered():
    names = [tool["name"] for tool in mcp_server._TOOL_DEFINITIONS]
    assert names.count("skills") == 1
    assert not {"skills_list", "skills_fetch", "skills_hash_status"}.intersection(names)


def test_skills_tool_has_one_shared_namespace_and_no_agent_partition():
    schema = next(tool for tool in mcp_server._TOOL_DEFINITIONS if tool["name"] == "skills")
    props = schema["inputSchema"]["properties"]
    assert schema["inputSchema"]["properties"]["operation"]["enum"] == [
        "catalog", "query", "fetch", "resource", "resolve", "fetch_locked",
        "relock", "complete_task", "promote", "check_drift", "status"
    ]
    assert "agent" not in props
    assert "agent_id" not in props


def test_skills_query_returns_bounded_chunks_without_agent_partition(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)

    result = mcp_server.invoke("skills", {
        "operation": "query",
        "query": "live runtime acceptance",
        "max_results": 5,
        "chunk_chars": 400,
        "requested_intent": "alternate_method",
    })

    assert result["ok"] is True
    assert result["namespace"] == "skills"
    assert result["bounded"] is True
    assert result["results"][0]["path"] == "testing/runtime.md"
    assert "Validate live runtime" in result["results"][0]["section"]
    assert "agent" not in result
    telemetry = result["telemetry"]
    assert telemetry["query_id"]
    assert telemetry["requested_intent"] == "alternate_method"
    assert telemetry["selected_query_type"] == "lexical"
    assert telemetry["root"] == "skills"
    assert telemetry["path_scope"] is None
    assert telemetry["semantic_used"] is False
    assert telemetry["freshness_checked"] is False
    assert telemetry["results"] == result["result_count"]
    assert telemetry["candidate_count"] == 2
    assert telemetry["deduplicated_count"] == 0


def test_full_skill_file_requires_explicit_fetch(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)

    result = mcp_server.invoke("skills", {
        "operation": "fetch",
        "rel": "testing/runtime.md",
        "max_bytes": 1000,
    })

    assert result["ok"] is True
    assert result["operation"] == "fetch"
    assert result["content"].startswith("# Runtime acceptance")


def test_skills_query_suppresses_duplicate_sha_content(tmp_path, monkeypatch):
    skills_root = _configure(tmp_path, monkeypatch)
    duplicate = skills_root / "testing" / "duplicate.md"
    duplicate.write_text(
        (skills_root / "testing" / "runtime.md").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = mcp_server.invoke("skills", {
        "operation": "query",
        "query": "runtime acceptance",
        "max_results": 10,
        "chunk_chars": 400,
    })

    assert result["ok"] is True
    assert [item["path"] for item in result["results"]] == ["testing/duplicate.md"]
    assert result["total_matching_chunks"] == 2
    assert result["telemetry"]["deduplicated_count"] == 1


def test_agent_specific_skill_parameters_are_rejected(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)

    result = mcp_server.invoke("skills", {
        "operation": "query",
        "query": "governance",
        "agent": "cline",
    })

    assert result["ok"] is False
    assert "agent-specific" in result["message"]


def test_memory_mcp_has_no_skill_tools():
    import asyncio
    import sys

    memory_root = Path(r"C:\MCP Local\Memory")
    sys.path.insert(0, str(memory_root))
    sys.path.insert(0, str(memory_root / "src"))
    from memory.mcp_server import _make_mcp_server

    app = _make_mcp_server(
        canonical_db=":memory:",
        semantic_db=str(Path("skills-test-semantic.db")),
        derived_db=":memory:",
    )
    names = {tool.name for tool in asyncio.run(app.list_tools())}
    assert not {"skills", "skills_list", "skills_fetch", "skills_hash_status"}.intersection(names)


def test_agent_skills_portable_metadata_and_invalid_skill_not_selected(tmp_path, monkeypatch):
    root = _configure(tmp_path, monkeypatch)
    valid = root / "agent-workflows" / "task-review" / "SKILL.md"
    valid.parent.mkdir(parents=True)
    valid.write_text("---\nname: task-review\ndescription: Review scoped tasks and evidence.\n---\n# Steps\nReview runtime.\n", encoding="utf-8")
    invalid = root / "agent-workflows" / "wrong-folder" / "SKILL.md"
    invalid.parent.mkdir(parents=True)
    invalid.write_text("---\nname: not-the-folder\ndescription: Review scoped tasks and evidence.\n---\n", encoding="utf-8")
    found = mcp_server.invoke("skills", {"operation": "query", "query": "review scoped tasks", "max_results": 15})
    selected = next(x for x in found["results"] if x["path"].endswith("task-review/SKILL.md"))
    assert selected["skill_metadata"] == {"name": "task-review", "description": "Review scoped tasks and evidence.", "format": "agent-skills"}
    assert mcp_server._agent_skill_metadata("agent-workflows/wrong-folder/SKILL.md", invalid.read_text(encoding="utf-8")) is None


def test_local_projects_parses_bom_without_editing_mapping(tmp_path, monkeypatch):
    source = tmp_path / "knowledge"
    file = source / "projects.json"
    file.parent.mkdir()
    payload = '{"projects":{"brew":{"local_path":"C:/Workspace/Brew"}}}'
    file.write_bytes(b"\xef\xbb\xbf" + payload.encode("utf-8"))
    monkeypatch.setattr(mcp_server, "_load_ctx", lambda: object())
    monkeypatch.setattr(mcp_server, "_gpt_knowledge_root", lambda ctx: source)
    monkeypatch.setattr(mcp_server, "_LOCAL_PROJECTS_RELPATH", "projects.json")
    result = mcp_server.local_projects("brew")
    assert result["ok"] is True
    assert result["projects"]["brew"]["local_path"] == "C:/Workspace/Brew"
    assert file.read_bytes().startswith(b"\xef\xbb\xbf")


def test_catalog_uses_portable_agent_skill_metadata_only_and_paginates(tmp_path, monkeypatch):
    root = _configure(tmp_path, monkeypatch)
    for name in ("audit-review", "code-review"):
        skill_file = root / "portable" / name / "SKILL.md"
        skill_file.parent.mkdir(parents=True)
        skill_file.write_text(
            f"---\nname: {name}\ndescription: Review governed task receipts and runtime.\n---\n"
            "# Private complete skill instructions, never included in catalog.\n",
            encoding="utf-8",
        )
    result = mcp_server.invoke("skills", {
        "operation": "catalog", "prefix": "portable/", "query": "review",
        "max_results": 1,
    })
    assert result["ok"] is True
    assert result["total"] == 2
    assert result["next_offset"] == 1
    assert result["results"][0]["format"] == "agent-skills"
    assert "content" not in result["results"][0]
    assert "Private complete skill instructions" not in json.dumps(result)
    second = mcp_server.invoke("skills", {
        "operation": "catalog", "prefix": "portable/", "query": "review",
        "offset": 1, "max_results": 1,
    })
    assert second["next_offset"] is None
    assert {result["results"][0]["name"], second["results"][0]["name"]} == {
        "audit-review", "code-review"
    }


def test_locked_skill_resource_snapshot_is_stable_and_relative(tmp_path, monkeypatch):
    root = _configure(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    skill = root / "portable" / "audit-review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: audit-review\ndescription: Review governed runtime receipts.\n"
        "---\n# Use a runtime receipt before claiming completion.\n",
        encoding="utf-8",
    )
    reference = skill.parent / "references" / "receipts.md"
    reference.parent.mkdir()
    reference.write_text("receipt verification baseline", encoding="utf-8")
    params = {
        "workspace_root": str(workspace), "task": "review-task",
        "domain": "audit", "query": "governed runtime receipts",
        "prefix": "portable/",
    }
    resolved = mcp_server.invoke("skills", {"operation": "resolve", **params})
    assert resolved["ok"] is True
    assert resolved["skills"][0]["path"] == "portable/audit-review/SKILL.md"
    locked = mcp_server.invoke("skills", {
        "operation": "fetch_locked", "workspace_root": str(workspace),
        "task": "review-task", "domain": "audit",
    })
    assert locked["skills"][0]["sha256"] == resolved["skills"][0]["sha256"]

    fetch = {
        "operation": "resource", "workspace_root": str(workspace),
        "task": "review-task", "domain": "audit", "rel": "references/receipts.md",
    }
    first = mcp_server.invoke("skills", fetch)
    assert first["ok"] and first["content"] == "receipt verification baseline"
    reference.write_text("changed after task lock", encoding="utf-8")
    second = mcp_server.invoke("skills", fetch)
    assert second["content"] == first["content"]
    assert second["sha256"] == first["sha256"]
    assert second["executable"] is False
    skill.unlink()
    reference.unlink()
    third = mcp_server.invoke("skills", fetch)
    assert third["ok"] is True
    assert third["content"] == first["content"]
    assert third["sha256"] == first["sha256"]
    bad = mcp_server.invoke("skills", {**fetch, "rel": "../outside.txt"})
    assert bad["ok"] is False
    assert "relative" in bad["message"] or "escapes" in bad["message"]

    another = mcp_server.invoke("skills", {
        "operation": "resolve", **{**params, "query": "different query"},
    })
    assert another["source"] == "task-lock"
    assert another["skills"][0]["sha256"] == locked["skills"][0]["sha256"]


def test_local_projects_prefers_live_registered_root_and_flags_stale_mapping(tmp_path, monkeypatch):
    from types import SimpleNamespace
    source = tmp_path / "knowledge"
    source.mkdir()
    mapping = source / "projects.json"
    mapped = tmp_path / "old-missing"
    live = tmp_path / "live"
    live.mkdir()
    mapping.write_text(
        json.dumps({"projects": {"brew": {"local_path": str(mapped)}}}),
        encoding="utf-8-sig",
    )
    registered = SimpleNamespace(
        name="brew", path=str(live), root_class="workspace", enabled=True,
    )
    monkeypatch.setattr(mcp_server, "_load_ctx", lambda: SimpleNamespace(roots=[registered]))
    monkeypatch.setattr(mcp_server, "_gpt_knowledge_root", lambda ctx: source)
    monkeypatch.setattr(mcp_server, "_LOCAL_PROJECTS_RELPATH", "projects.json")
    result = mcp_server.local_projects("brew")
    assert result["ok"] is True
    current = result["projects"]["brew"]
    assert current["local_path"] == str(live.resolve())
    assert current["exists"] is True
    assert current["mapped_local_path"] == str(mapped)
    assert current["mapping_drift"] is True
    assert current["resolution_source"] == "registered_live_workspace"
    assert json.loads(mapping.read_text(encoding="utf-8-sig"))["projects"]["brew"]["local_path"] == str(mapped)
