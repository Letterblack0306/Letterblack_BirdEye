"""Disposable-fixture coverage for EYES query-projection bootstrap."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import eye_database
import eye_query


def _canonical(tmp_path: Path, monkeypatch, source_root: Path) -> tuple[Path, sqlite3.Connection]:
    data_dir = tmp_path / "eyes"
    data_dir.mkdir()
    monkeypatch.setattr(eye_query, "EYE_DATABASE_DIR", data_dir)
    monkeypatch.setattr(eye_database, "EYE_DATA_DIR", data_dir)
    data_path = eye_query.path("workspace", "data")
    conn = eye_database._connect(data_path)
    return data_path, conn


def _file_row(conn: sqlite3.Connection, source_root: Path, name: str, content: str) -> None:
    target = source_root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    raw = target.read_bytes()
    now = "2026-09-16T00:00:00+00:00"
    conn.execute(
        "INSERT INTO files(domain,source_id,agent,relative_path,physical_path,size,modified_ns,"
        "sha256,hash_status,error,first_seen_at,last_seen_at,last_event,extension,file_type,"
        "line_count,token_count,feature_terms,last_generation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("workspace", "workspace-test", None, name, str(target), len(raw), target.stat().st_mtime_ns,
         hashlib.sha256(raw).hexdigest(), "hashed", None, now, now, "created", ".txt", "text",
         1, 1, "[]", 1),
    )


def _generation(conn: sqlite3.Connection, value: int) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES('canonical_generation',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(value),),
    )


def _change(conn: sqlite3.Connection, generation: int, name: str, event: str) -> None:
    conn.execute(
        "INSERT INTO changes(generation,domain,source_id,relative_path,event,observed_at) "
        "VALUES(?,?,?,?,?,?)",
        (generation, "workspace", "workspace-test", name, event, "2026-09-16T00:00:00+00:00"),
    )


def _query_files() -> list[tuple]:
    conn = sqlite3.connect(eye_query.path("workspace", "query"))
    try:
        return conn.execute("SELECT path,sha256,content FROM files ORDER BY path").fetchall()
    finally:
        conn.close()


def test_b1_b2_missing_empty_projection_bootstraps_and_is_idempotent(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _, canonical = _canonical(tmp_path, monkeypatch, source)
    _file_row(canonical, source, "v1.txt", "v1")
    _generation(canonical, 1)
    canonical.commit()
    canonical.close()

    first = eye_query.project_pending_changes("workspace")
    query = sqlite3.connect(eye_query.path("workspace", "query"))
    query.execute("DELETE FROM files")
    query.execute("UPDATE meta SET value='0' WHERE key='applied_generation'")
    query.commit()
    query.close()
    empty = eye_query.project_pending_changes("workspace")
    second = eye_query.project_pending_changes("workspace")

    assert first["bootstrap"] is True and first["lag"] == 0 and first["replayed"] == 1
    assert empty["bootstrap"] is True and empty["lag"] == 0 and empty["replayed"] == 1
    assert second["replayed"] == 0 and _query_files() == [("workspace-test/v1.txt", hashlib.sha256(b"v1").hexdigest(), "v1")]


def test_b3_deleted_file_is_not_resurrected(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _, canonical = _canonical(tmp_path, monkeypatch, source)
    _file_row(canonical, source, "gone.txt", "gone")
    _change(canonical, 1, "gone.txt", "created")
    _generation(canonical, 1)
    canonical.commit()
    canonical.close()
    eye_query.project_pending_changes("workspace")

    canonical = sqlite3.connect(eye_query.path("workspace", "data"))
    canonical.row_factory = sqlite3.Row
    canonical.execute("DELETE FROM files WHERE relative_path='gone.txt'")
    _change(canonical, 2, "gone.txt", "deleted")
    _generation(canonical, 2)
    canonical.commit()
    canonical.close()

    result = eye_query.project_pending_changes("workspace")
    assert result["lag"] == 0
    assert _query_files() == []


def test_b4_current_sha_and_b6_rebootstrap_after_disposable_query_delete(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _, canonical = _canonical(tmp_path, monkeypatch, source)
    _file_row(canonical, source, "version.txt", "v1")
    _generation(canonical, 1)
    canonical.commit()
    canonical.close()
    eye_query.project_pending_changes("workspace")

    target = source / "version.txt"
    target.write_text("v2", encoding="utf-8")
    canonical = sqlite3.connect(eye_query.path("workspace", "data"))
    canonical.row_factory = sqlite3.Row
    canonical.execute("UPDATE files SET size=?,sha256=?,last_generation=2 WHERE relative_path='version.txt'",
                     (2, hashlib.sha256(b"v2").hexdigest()))
    _generation(canonical, 2)
    canonical.commit()
    canonical.close()

    query = eye_query.path("workspace", "query")
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(query) + suffix)
        if candidate.exists():
            candidate.unlink()
    result = eye_query.project_pending_changes("workspace")
    assert result["bootstrap"] is True and result["lag"] == 0
    assert _query_files() == [("workspace-test/version.txt", hashlib.sha256(b"v2").hexdigest(), "v2")]


def test_b7_empty_canonical_state_has_no_fabricated_rows_and_truthful_lag(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    _, canonical = _canonical(tmp_path, monkeypatch, source)
    _generation(canonical, 7)
    canonical.commit()
    canonical.close()

    result = eye_query.project_pending_changes("workspace")
    assert result == {
        "ok": True, "domain": "workspace", "canonical_generation": 7,
        "applied_generation": 7, "lag": 0, "replayed": 0, "bootstrap": True,
    }
    assert _query_files() == []
