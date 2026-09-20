
from pathlib import Path

import pytest

import mcp_server


def _configure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
 skills_root = tmp_path / "skills"
 (skills_root / "testing").mkdir(parents=True)
 (skills_root / "testing" / "runtime.md").write_text(
 "# Runtime acceptance\n\nValidate live runtime behavior.\n",
 encoding="utf-8",
 )
 monkeypatch.setattr(mcp_server, "SKILLS_ROOT", skills_root.resolve())
 monkeypatch.setattr(
 mcp_server,
 "SHARED_VECTOR_INDEX",
 tmp_path / "state" / "vectors" / "semantic.db",
 )
 return skills_root


def test_catalog_metadata_outranks_full_text_decoy(tmp_path, monkeypatch):
 skills_root = _configure(tmp_path, monkeypatch)
 skill_dir = skills_root / "creative" / "theme-factory"
 skill_dir.mkdir(parents=True)
 (skill_dir / "SKILL.md").write_text(
 "---\n"
 "name: theme-factory\n"
 "description: Toolkit for styling artifacts with a theme.\n"
 "query_aliases:\n"
 " - dark\n"
 " - toned\n"
 "---\n"
 "# Theme Factory\n\nMidnight Galaxy\n",
 encoding="utf-8",
 )
 (skills_root / "themes-docs.md").write_text(
 ("dark toned theme " * 300),
 encoding="utf-8",
 )

 result = mcp_server.invoke(
 "skills",
 {
 "operation": "query",
 "query": "dark toned theme",
 "max_results": 5,
 "chunk_chars": 800,
 },
 )

 assert result["ok"] is True
 assert result["results"][0]["path"].endswith("theme-factory/SKILL.md")
 assert all(
 not item["path"].endswith("themes-docs.md")
 for item in result["results"]
 )


def test_query_falls_back_to_full_corpus_without_catalog_match(tmp_path, monkeypatch):
 _configure(tmp_path, monkeypatch)

 result = mcp_server.invoke(
 "skills",
 {
 "operation": "query",
 "query": "runtime acceptance",
 "max_results": 5,
 "chunk_chars": 400,
 },
 )

 assert result["ok"] is True
 assert result["results"][0]["path"] == "testing/runtime.md"
