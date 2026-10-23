from __future__ import annotations

import hashlib
from pathlib import Path

import agent


def test_large_file_streamed_without_content_loading(tmp_path: Path):
    target = tmp_path / "large.db"
    target.write_bytes(b"database-bytes" * 1000)
    result, status, error = agent.bounded_large_file_sha256(target, target.stat(), 100_000)
    assert status == "hashed_large"
    assert error is None
    assert result == hashlib.sha256(target.read_bytes()).hexdigest()


def test_large_file_ceiling_is_truthfully_unresolved(tmp_path: Path):
    target = tmp_path / "large.db"
    target.write_bytes(b"example" * 100)
    result, status, error = agent.bounded_large_file_sha256(target, target.stat(), 100)
    assert (result, status, error) == (None, "too_large", None)


def test_large_file_rejects_changed_content(tmp_path: Path, monkeypatch):
    target = tmp_path / "large.db"
    target.write_bytes(b"original" * 500)
    initial = target.stat()
    original = agent.sha256_file
    def changing_hash(path):
        digest = original(path)
        path.write_bytes(b"changed!" * 500)
        return digest
    monkeypatch.setattr(agent, "sha256_file", changing_hash)
    value, status, error = agent.bounded_large_file_sha256(target, initial, 100_000)
    assert value is None
    assert status == "unreadable"
    assert "changed during" in error


def test_large_non_database_assets_stay_size_limited(tmp_path: Path):
    target = tmp_path / "video.mp4"
    target.write_bytes(b"x" * 10000)
    assert agent.bounded_large_file_sha256(target, target.stat(), 100_000) == (None, "too_large", None)
