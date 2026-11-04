"""Consumer access must be checked before treating an inbox as empty."""
import os
import pytest
import oracle_index_sync


def test_missing_results_inbox_is_not_empty_success(tmp_path, monkeypatch):
    monkeypatch.setattr(oracle_index_sync, "SHARED", tmp_path)
    with pytest.raises(RuntimeError, match="queue inaccessible"):
        oracle_index_sync.consume_pending()


def test_permission_denied_is_reported_without_writing_ack(tmp_path, monkeypatch):
    shared = tmp_path / "indexing"
    (shared / "results").mkdir(parents=True)
    (shared / "acks").mkdir()
    monkeypatch.setattr(oracle_index_sync, "SHARED", shared)
    original = os.scandir

    def deny_index_queue(path):
        if os.fspath(path) == os.fspath(shared / "results"):
            raise PermissionError("EACCES: parent directory inaccessible")
        return original(path)

    with monkeypatch.context() as m:
        m.setattr(oracle_index_sync.os, "scandir", deny_index_queue)
        with pytest.raises(RuntimeError, match="PermissionError"):
            oracle_index_sync.consume_pending()
    assert list((shared / "acks").iterdir()) == []


def test_accessible_empty_queue_is_a_valid_zero(tmp_path, monkeypatch):
    shared = tmp_path / "indexing"
    (shared / "results").mkdir(parents=True)
    monkeypatch.setattr(oracle_index_sync, "SHARED", shared)
    assert oracle_index_sync.consume_pending() == 0
