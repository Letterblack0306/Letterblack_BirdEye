"""Focused tests for the EYES canonical generation/change journal contract."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import eye_database
import eye_query


def _config(root: Path) -> dict:
    return {
        "roots": [
            {
                "id": "workspace-test",
                "path": str(root),
                "class": "workspace",
            }
        ]
    }


def test_mutations_advance_generation_and_unchanged_does_not(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    target = source_root / "file.txt"
    target.write_text("one", encoding="utf-8")
    data_dir = tmp_path / "eye_Databa"
    monkeypatch.setattr(eye_database, "EYE_DATA_DIR", data_dir)
    monkeypatch.setattr(eye_database, "load_config", lambda: _config(source_root))

    first = eye_database.record_file_event(target, "created")
    unchanged = eye_database.record_file_event(target, "modified")
    target.write_text("two", encoding="utf-8")
    second = eye_database.record_file_event(target, "modified")
    deleted = eye_database.record_file_event(target, "deleted")

    assert first["ok"] and second["ok"] and deleted["ok"]
    conn = sqlite3.connect(data_dir / "eye_workspace_data_01.db")
    try:
        assert conn.execute("SELECT value FROM meta WHERE key='canonical_generation'").fetchone()[0] == "3"
        rows = conn.execute(
            "SELECT generation,event FROM changes ORDER BY generation"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [(1, "created"), (2, "modified"), (3, "deleted")]
    assert unchanged["action"] == "indexed"


def test_existing_schema_is_upgraded_with_generation_metadata(tmp_path):
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE files(domain TEXT, source_id TEXT, relative_path TEXT, PRIMARY KEY(domain, source_id, relative_path));"
        "CREATE TABLE changes(id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT, source_id TEXT, relative_path TEXT, event TEXT, old_sha256 TEXT, new_sha256 TEXT, observed_at TEXT);"
    )
    conn.close()

    conn = eye_database._connect(db)
    try:
        assert "last_generation" in {row[1] for row in conn.execute("PRAGMA table_info(files)")}
        assert "generation" in {row[1] for row in conn.execute("PRAGMA table_info(changes)")}
        assert conn.execute("SELECT value FROM meta WHERE key='canonical_generation'").fetchone()[0] == "0"
    finally:
        conn.close()


def test_sync_reconciles_deleted_files_and_projection(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    target = source_root / "delete.txt"
    target.write_text("remove me", encoding="utf-8")
    data_dir = tmp_path / "eye_Databa"
    monkeypatch.setattr(eye_database, "EYE_DATA_DIR", data_dir)
    monkeypatch.setattr(eye_database, "load_config", lambda: _config(source_root))
    monkeypatch.setattr(eye_query, "EYE_DATABASE_DIR", data_dir)

    eye_database.sync_all()
    assert eye_query.project_pending_changes("workspace")["ok"] is True
    target.unlink()
    eye_database.sync_all()
    eye_query.project_pending_changes("workspace")

    data_conn = sqlite3.connect(data_dir / "eye_workspace_data_01.db")
    query_conn = sqlite3.connect(data_dir / "eye_workspace_query_01.db")
    try:
        assert data_conn.execute("SELECT 1 FROM files WHERE relative_path='delete.txt'").fetchone() is None
        assert query_conn.execute("SELECT 1 FROM files WHERE path='workspace-test/delete.txt'").fetchone() is None
    finally:
        data_conn.close()
        query_conn.close()


def test_sync_reconciles_renamed_files_and_projection(tmp_path, monkeypatch):
    source_root = tmp_path / "source"
    source_root.mkdir()
    old = source_root / "old.txt"
    new = source_root / "new.txt"
    old.write_text("move me", encoding="utf-8")
    data_dir = tmp_path / "eye_Databa"
    monkeypatch.setattr(eye_database, "EYE_DATA_DIR", data_dir)
    monkeypatch.setattr(eye_database, "load_config", lambda: _config(source_root))
    monkeypatch.setattr(eye_query, "EYE_DATABASE_DIR", data_dir)

    eye_database.sync_all()
    eye_query.project_pending_changes("workspace")
    old.rename(new)
    eye_database.sync_all()
    eye_query.project_pending_changes("workspace")

    query_conn = sqlite3.connect(data_dir / "eye_workspace_query_01.db")
    try:
        assert query_conn.execute("SELECT 1 FROM files WHERE path='workspace-test/old.txt'").fetchone() is None
        assert query_conn.execute("SELECT 1 FROM files WHERE path='workspace-test/new.txt'").fetchone() is not None
    finally:
        query_conn.close()