from __future__ import annotations

from pathlib import Path

import agent


def test_trace_workspace_with_narrowed_context_reconciles_only_selected_root(tmp_path, monkeypatch):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    a_keep = root_a / "keep.txt"
    a_delete = root_a / "delete.txt"
    b_keep = root_b / "keep.txt"

    a_keep.write_text("a-v1", encoding="utf-8")
    a_delete.write_text("delete-me", encoding="utf-8")
    b_keep.write_text("b-v1", encoding="utf-8")

    state = tmp_path / "state"
    monkeypatch.setattr(agent, "STATE_DIR", state)
    monkeypatch.setattr(agent, "DATABASE_PATH", state / "workspace.db")
    monkeypatch.setattr(agent, "PROGRESS_PATH", state / "trace_progress.json")
    monkeypatch.setattr(agent, "SUMMARY_PATH", state / "workspace_trace.json")

    full = agent.Context(
        config={"max_file_bytes": 5_000_000},
        governance={},
        roots=(
            agent.KnowledgeRoot("a", root_a, "workspace"),
            agent.KnowledgeRoot("b", root_b, "workspace"),
        ),
    )

    agent.trace_workspace(full, progress_every=100, checkpoint_every=100)

    before_b = agent.open_database().execute(
        "SELECT sha256 FROM files WHERE root=? AND path=?",
        ("b", "b/keep.txt"),
    ).fetchone()["sha256"]

    a_keep.write_text("a-v2", encoding="utf-8")
    a_delete.unlink()
    (root_a / "new.txt").write_text("new", encoding="utf-8")

    scoped = agent.Context(
        config=full.config,
        governance=full.governance,
        roots=(full.roots[0],),
    )

    agent.trace_workspace(scoped, progress_every=100, checkpoint_every=100)

    connection = agent.open_database()
    try:
        assert connection.execute(
            "SELECT 1 FROM files WHERE root=? AND path=?",
            ("a", "a/delete.txt"),
        ).fetchone() is None

        assert connection.execute(
            "SELECT 1 FROM files WHERE root=? AND path=?",
            ("a", "a/new.txt"),
        ).fetchone() is not None

        assert connection.execute(
            "SELECT sha256 FROM files WHERE root=? AND path=?",
            ("a", "a/keep.txt"),
        ).fetchone()["sha256"] == agent.sha256_file(a_keep)

        assert connection.execute(
            "SELECT sha256 FROM files WHERE root=? AND path=?",
            ("b", "b/keep.txt"),
        ).fetchone()["sha256"] == before_b
    finally:
        connection.close()


def test_reconcile_roots_validates_and_scopes(tmp_path, monkeypatch):
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "a.txt").write_text("a", encoding="utf-8")
    (root_b / "b.txt").write_text("b", encoding="utf-8")

    state = tmp_path / "state"
    monkeypatch.setattr(agent, "STATE_DIR", state)
    monkeypatch.setattr(agent, "DATABASE_PATH", state / "workspace.db")
    monkeypatch.setattr(agent, "PROGRESS_PATH", state / "trace_progress.json")
    monkeypatch.setattr(agent, "SUMMARY_PATH", state / "workspace_trace.json")

    ctx = agent.Context(
        config={"max_file_bytes": 5_000_000},
        governance={},
        roots=(
            agent.KnowledgeRoot("a", root_a, "workspace"),
            agent.KnowledgeRoot("b", root_b, "workspace"),
        ),
    )

    summary = agent.reconcile_roots(ctx, ["a"], progress_every=100, checkpoint_every=100)
    assert [root["name"] for root in summary["knowledge_roots"]] == ["a"]

    connection = agent.open_database()
    try:
        assert connection.execute("SELECT 1 FROM files WHERE root='a'").fetchone() is not None
        assert connection.execute("SELECT 1 FROM files WHERE root='b'").fetchone() is None
    finally:
        connection.close()

    try:
        agent.reconcile_roots(ctx, ["missing"], progress_every=100, checkpoint_every=100)
    except agent.GovernanceError:
        pass
    else:
        raise AssertionError("Unknown root must be rejected")
