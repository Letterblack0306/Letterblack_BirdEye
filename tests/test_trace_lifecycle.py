from __future__ import annotations

from pathlib import Path

import agent


def test_multi_root_trace_persists_terminal_state_when_memory_is_last(tmp_path, monkeypatch) -> None:
    first_root = tmp_path / "workspace"
    memory_root = tmp_path / "memory"
    first_root.mkdir()
    memory_root.mkdir()
    (first_root / "workspace.txt").write_text("workspace", encoding="utf-8")
    (memory_root / "memory.txt").write_text("memory", encoding="utf-8")

    database_path = tmp_path / "state" / "workspace.db"
    monkeypatch.setattr(agent, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(agent, "DATABASE_PATH", database_path)
    monkeypatch.setattr(agent, "PROGRESS_PATH", tmp_path / "state" / "trace_progress.json")
    monkeypatch.setattr(agent, "SUMMARY_PATH", tmp_path / "state" / "workspace_trace.json")

    context = agent.Context(
        config={"max_file_bytes": 5_000_000},
        governance={},
        roots=(
            agent.KnowledgeRoot("workspace", Path(first_root), "workspace"),
            agent.KnowledgeRoot("memory", Path(memory_root), "workspace"),
        ),
    )

    summary = agent.trace_workspace(context, progress_every=100, checkpoint_every=100)
    persisted = agent.database_status()

    assert summary["status"] == "completed"
    assert summary["completed_at"] is not None
    assert persisted["last_run"]["status"] == "completed"
    assert persisted["last_run"]["completed_at"] is not None
    assert persisted["last_run"]["error"] is None
