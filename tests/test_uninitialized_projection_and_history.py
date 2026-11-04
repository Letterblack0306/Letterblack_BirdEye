"""Regression checks for empty runtime history and uninitialized EYES query state."""
from __future__ import annotations

import sqlite3

import control_center_api
import eye_query


def test_empty_receipt_directory_has_stable_history_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(control_center_api, "STATE", tmp_path)
    result = control_center_api.recent_receipts()
    assert result["ok"] is True
    assert result["records"] == []
    assert result["scanned"] == 0
    assert result["source"] == "state/executions/*/receipt.json"
    assert result["limited"] is False


def test_present_but_uninitialized_projection_is_unverified(tmp_path, monkeypatch):
    def fake_path(domain, kind, number=1):
        return tmp_path / f"{domain}_{kind}.db"
    monkeypatch.setattr(eye_query, "path", fake_path)
    # Simulate a query file that exists but has no BirdEye meta table.
    with sqlite3.connect(tmp_path / "skills_query.db") as conn:
        conn.execute("CREATE TABLE not_meta(value TEXT)")
    result = eye_query.health()
    skills = result["domains"]["skills"]
    assert result["ok"] is True
    assert skills["projection_metadata_verified"] is False
    assert skills["journal_replayable"] is False
    assert "metadata" in skills["reason"]
    assert result["generation_lag_zero"] is False
