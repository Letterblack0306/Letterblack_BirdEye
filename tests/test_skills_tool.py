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


def test_one_consolidated_skills_tool_is_registered():
    names = [tool["name"] for tool in mcp_server._TOOL_DEFINITIONS]
    assert names.count("skills") == 1
    assert not {"skills_list", "skills_fetch", "skills_hash_status"}.intersection(names)


def test_skills_tool_has_one_shared_namespace_and_no_agent_partition():
    schema = next(tool for tool in mcp_server._TOOL_DEFINITIONS if tool["name"] == "skills")
    props = schema["inputSchema"]["properties"]
    assert schema["inputSchema"]["properties"]["operation"]["enum"] == ["query", "fetch", "status"]
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
