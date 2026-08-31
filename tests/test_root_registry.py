from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import agent


def _context_config(tmp_path: Path, roots: list[dict]) -> Path:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"schema_version": 2, "roots": roots}), encoding="utf-8")
    return config


def test_context_loads_v2_policy_fields(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    config = _context_config(tmp_path, [{
        "id": "skills",
        "path": str(root),
        "class": "knowledge",
        "enabled": True,
        "index": True,
        "hash": "sha256",
        "git": False,
        "exclusions": ["node_modules", ".git"],
    }])
    monkeypatch.setattr(agent, "CONFIG_PATH", config)
    monkeypatch.setattr(agent, "GOVERNANCE_PATH", tmp_path / "governance.json")
    (tmp_path / "governance.json").write_text(json.dumps({}), encoding="utf-8")

    context = agent.Context.load()

    assert context.roots[0] == agent.KnowledgeRoot(
        "skills", root.resolve(), "knowledge", True, True, "sha256", False,
        ("node_modules", ".git"),
    )


def test_context_accepts_legacy_knowledge_roots(tmp_path, monkeypatch):
    root = tmp_path / "legacy"
    root.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"knowledge_roots": [{"name": "legacy", "path": str(root)}]}), encoding="utf-8")
    monkeypatch.setattr(agent, "CONFIG_PATH", config)
    monkeypatch.setattr(agent, "GOVERNANCE_PATH", tmp_path / "governance.json")
    (tmp_path / "governance.json").write_text(json.dumps({}), encoding="utf-8")

    root_record = agent.Context.load().roots[0]

    assert root_record.name == "legacy"
    assert root_record.enabled is True
    assert root_record.index_enabled is True
    assert root_record.hash_policy == "sha256"


def test_memory_root_loads_declarative_source_paths_and_indexes_under_one_namespace(tmp_path, monkeypatch):
    memory_root = tmp_path / "memory"
    source_root = tmp_path / "cline"
    memory_root.mkdir()
    source_root.mkdir()
    (memory_root / "README.md").write_text("memory", encoding="utf-8")
    (source_root / "session.json").write_text("session", encoding="utf-8")
    config = _context_config(tmp_path, [{
        "id": "memory",
        "path": str(memory_root),
        "class": "memory",
        "source_class": "historical-memory",
        "authority": "historical",
        "sources": [{
            "id": "cline",
            "path": str(source_root),
            "source_class": "agent-session",
            "agent": "cline",
        }],
    }])
    monkeypatch.setattr(agent, "CONFIG_PATH", config)
    monkeypatch.setattr(agent, "GOVERNANCE_PATH", tmp_path / "governance.json")
    (tmp_path / "governance.json").write_text(json.dumps({}), encoding="utf-8")

    context = agent.Context.load()
    root = context.roots[0]
    files = list(agent.iter_files(context, root))

    assert len(root.sources) == 1
    assert root.sources[0].name == "cline"
    assert {virtual for _, virtual in files} == {
        "memory/README.md",
        "memory/sources/cline/session.json",
    }


def test_production_config_has_one_memory_root():
    context = agent.Context.load()
    memory_roots = [root for root in context.roots if root.root_class == "memory"]

    assert [root.name for root in memory_roots] == ["memory"]
    assert {source.name for source in memory_roots[0].sources} >= {
        "chatgpt", "cline", "codex", "claude", "gemini", "antigravity", "birdeye",
    }


def test_production_config_uses_curated_only_skills_root():
    context = agent.Context.load()
    skills_roots = [root for root in context.roots if root.name == "skills"]

    assert len(skills_roots) == 1
    assert skills_roots[0].path == Path(r"C:\MCP Local\Skills\curated").resolve()


def test_database_status_does_not_claim_current_without_completed_run(tmp_path, monkeypatch):
    root = tmp_path / "skills"
    root.mkdir()
    (root / "SKILL.md").write_text("content", encoding="utf-8")
    config = _context_config(tmp_path, [{"id": "skills", "path": str(root), "class": "knowledge"}])
    monkeypatch.setattr(agent, "CONFIG_PATH", config)
    monkeypatch.setattr(agent, "GOVERNANCE_PATH", tmp_path / "governance.json")
    monkeypatch.setattr(agent, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(agent, "DATABASE_PATH", tmp_path / "state" / "workspace.db")
    (tmp_path / "governance.json").write_text(json.dumps({}), encoding="utf-8")

    connection = agent.open_database()
    connection.execute(
        "INSERT INTO runs(run_id, started_at, status) VALUES(?, ?, ?)",
        ("run-1", agent.utc_now(), "running"),
    )
    connection.execute(
        "INSERT INTO root_runs(run_id, root, started_at, status) VALUES(?, ?, ?, ?)",
        ("run-1", "skills", agent.utc_now(), "running"),
    )
    connection.commit()
    connection.close()

    status = agent.database_status()

    assert status["status"] == "INDEXING"
    assert status["roots"][0]["status"] == "INDEXING"
    assert status["roots"][0]["last_completed_at"] is None


def test_excluded_root_reports_excluded(tmp_path, monkeypatch):
    root = tmp_path / "metadata-only"
    root.mkdir()
    config = _context_config(tmp_path, [{
        "id": "metadata-only", "path": str(root), "class": "workspace",
        "enabled": True, "index": False, "hash": "sha256",
    }])
    monkeypatch.setattr(agent, "CONFIG_PATH", config)
    monkeypatch.setattr(agent, "GOVERNANCE_PATH", tmp_path / "governance.json")
    monkeypatch.setattr(agent, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(agent, "DATABASE_PATH", tmp_path / "state" / "workspace.db")
    (tmp_path / "governance.json").write_text(json.dumps({}), encoding="utf-8")

    status = agent.database_status()

    assert status["roots"][0]["status"] == "EXCLUDED"
    assert status["roots"][0]["index_enabled"] is False


def test_historical_root_hashes_new_files_and_reuses_unchanged_files(tmp_path, monkeypatch):
    root = tmp_path / "sessions"
    root.mkdir()
    session_file = root / "session.jsonl"
    session_file.write_text("old", encoding="utf-8")
    config = _context_config(tmp_path, [{
        "id": "cline-sessions",
        "path": str(root),
        "class": "memory",
        "source_class": "agent-session",
        "agent": "cline",
        "authority": "historical",
        "enabled": True,
        "index": True,
        "hash": "sha256",
    }])
    monkeypatch.setattr(agent, "CONFIG_PATH", config)
    monkeypatch.setattr(agent, "GOVERNANCE_PATH", tmp_path / "governance.json")
    monkeypatch.setattr(agent, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(agent, "DATABASE_PATH", tmp_path / "state" / "workspace.db")
    monkeypatch.setattr(agent, "PROGRESS_PATH", tmp_path / "state" / "progress.json")
    monkeypatch.setattr(agent, "SUMMARY_PATH", tmp_path / "state" / "summary.json")
    (tmp_path / "governance.json").write_text(json.dumps({}), encoding="utf-8")

    first = agent.trace_workspace(agent.Context.load(), progress_every=100, checkpoint_every=100)
    assert first["statistics"]["files_hashed_this_run"] == 1

    connection = agent.open_database()
    row = connection.execute("SELECT sha256, hash_status FROM files WHERE root=?", ("cline-sessions",)).fetchone()
    connection.close()
    assert row["sha256"] == agent.sha256_file(session_file)
    assert row["hash_status"] == "hashed"

    second = agent.trace_workspace(agent.Context.load(), progress_every=100, checkpoint_every=100)
    assert second["statistics"]["files_hashed_this_run"] == 0
    assert second["statistics"]["files_cached_this_run"] == 1

    session_file.write_text("new", encoding="utf-8")
    third = agent.trace_workspace(agent.Context.load(), progress_every=100, checkpoint_every=100)
    assert third["statistics"]["files_hashed_this_run"] == 1

    connection = agent.open_database()
    row = connection.execute("SELECT sha256, hash_status FROM files WHERE root=?", ("cline-sessions",)).fetchone()
    connection.close()
    assert row["sha256"] == agent.sha256_file(session_file)
    assert row["hash_status"] == "hashed"