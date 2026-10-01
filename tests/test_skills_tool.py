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
        "query", "fetch", "resolve", "fetch_locked", "relock",
        "complete_task", "promote", "check_drift", "status"
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
