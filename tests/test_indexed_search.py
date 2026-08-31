from __future__ import annotations

import json
from pathlib import Path

import agent


def _context(tmp_path: Path) -> agent.Context:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps({
            "roots": [{"id": "demo", "path": str(workspace), "class": "workspace"}],
        }),
        encoding="utf-8",
    )
    governance = tmp_path / "governance.json"
    governance.write_text(json.dumps({}), encoding="utf-8")
    return agent.Context(
        config={"max_file_bytes": 5_000_000},
        governance={},
        roots=(agent.KnowledgeRoot("demo", workspace.resolve(), "workspace"),),
    )


def _use_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(agent, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(agent, "DATABASE_PATH", tmp_path / "state" / "workspace.db")
    monkeypatch.setattr(agent, "LAST_SEARCH_PATH", tmp_path / "state" / "last_search.json")


def test_search_uses_indexed_content_and_path_prefix_without_reading_files(tmp_path, monkeypatch):
    _use_database(tmp_path, monkeypatch)
    context = _context(tmp_path)
    connection = agent.open_database()
    connection.execute(
        """
        INSERT INTO files(
            root,path,physical_path,size,modified_ns,sha256,content,hash_status,error,
            first_seen_at,last_seen_at,last_seen_run
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "demo",
            "demo/runtime/authorization.js",
            str(tmp_path / "does-not-exist.js"),
            64,
            1,
            "a" * 64,
            "authorization resolver handles MCP event transport",
            "cached",
            None,
            agent.utc_now(),
            agent.utc_now(),
            "run-1",
        ),
    )
    connection.commit()
    connection.close()

    result = agent.search_workspace(
        context,
        "authorization resolver",
        path_prefix="runtime",
    )

    assert result["result_count"] == 1
    assert result["results"][0]["path"] == "demo/runtime/authorization.js"
    assert result["results"][0]["content_status"] == "cached"
    assert result["results"][0]["version_status"] == "indexed"
    assert result["applied_filters"]["path_prefix"] == "runtime"


def test_search_freshness_refreshes_changed_indexed_file(tmp_path, monkeypatch):
    _use_database(tmp_path, monkeypatch)
    context = _context(tmp_path)
    source = tmp_path / "workspace" / "runtime.js"
    source.write_text("old content", encoding="utf-8")
    stat = source.stat()
    connection = agent.open_database()
    connection.execute(
        """
        INSERT INTO files(
            root,path,physical_path,size,modified_ns,sha256,content,hash_status,error,
            first_seen_at,last_seen_at,last_seen_run
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "demo",
            "demo/runtime.js",
            str(source),
            stat.st_size,
            stat.st_mtime_ns,
            agent.sha256_file(source),
            "old content",
            "hashed",
            None,
            agent.utc_now(),
            agent.utc_now(),
            "run-1",
        ),
    )
    connection.commit()
    connection.close()

    source.write_text("authorization resolver", encoding="utf-8")
    result = agent.search_workspace(context, "authorization resolver", verify_freshness=True)

    assert result["result_count"] == 1
    item = result["results"][0]
    assert item["version_status"] == "refreshed"
    assert item["content_status"] == "cached"
    assert item["sha256"] == agent.sha256_file(source)

    connection = agent.open_database()
    row = connection.execute(
        "SELECT content, sha256 FROM files WHERE root=? AND path=?",
        ("demo", "demo/runtime.js"),
    ).fetchone()
    connection.close()
    assert row["content"] == "authorization resolver"
    assert row["sha256"] == item["sha256"]