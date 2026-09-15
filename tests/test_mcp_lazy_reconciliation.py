from __future__ import annotations

import mcp_server


def test_lazy_reconcile_runs_once_per_root(monkeypatch):
    calls = []

    class FakeRoot:
        def __init__(self, name):
            self.name = name

    class FakeContext:
        roots = (FakeRoot("a"), FakeRoot("b"))

    monkeypatch.setattr(mcp_server, "_load_ctx", lambda: FakeContext())
    monkeypatch.setattr(
        mcp_server,
        "reconcile_roots",
        lambda ctx, roots: calls.append(tuple(roots)) or {"ok": True},
    )

    mcp_server._reconciled_roots.clear()

    mcp_server._ensure_roots_reconciled(["a"])
    mcp_server._ensure_roots_reconciled(["a"])
    mcp_server._ensure_roots_reconciled(["b"])

    assert calls == [("a",), ("b",)]
    assert mcp_server._reconciled_roots == {"a", "b"}


def test_lazy_reconcile_rejects_unknown_root(monkeypatch):
    class FakeRoot:
        def __init__(self, name):
            self.name = name

    class FakeContext:
        roots = (FakeRoot("a"),)

    monkeypatch.setattr(mcp_server, "_load_ctx", lambda: FakeContext())
    mcp_server._reconciled_roots.clear()

    try:
        mcp_server._ensure_roots_reconciled(["missing"])
    except mcp_server.GovernanceError:
        pass
    else:
        raise AssertionError("Unknown root must be rejected")


def test_inspect_root_extraction():
    assert mcp_server._root_from_virtual_path("a/src/file.py") == "a"


def test_birdeye_search_uses_migrated_query_database_without_legacy_reconcile(monkeypatch):
    events = []

    monkeypatch.setattr(
        mcp_server,
        "_ensure_roots_reconciled",
        lambda roots: events.append(("reconcile", tuple(roots))),
    )
    monkeypatch.setattr(
        mcp_server,
        "search_workspace",
        lambda ctx, query, **kwargs: events.append(("search", tuple(kwargs.get("roots") or ()))) or {"ok": True},
    )
    monkeypatch.setattr(mcp_server, "_load_ctx", lambda: object())

    result = mcp_server.birdeye_search("needle", roots="a")

    assert result["ok"] is True
    assert events == [("search", ("a",))]


def test_birdeye_search_without_explicit_roots_does_not_global_reconcile(monkeypatch):
    reconciled = []

    monkeypatch.setattr(
        mcp_server,
        "_ensure_roots_reconciled",
        lambda roots: reconciled.append(tuple(roots)),
    )
    monkeypatch.setattr(
        mcp_server,
        "search_workspace",
        lambda ctx, query, **kwargs: {"ok": True},
    )
    monkeypatch.setattr(mcp_server, "_load_ctx", lambda: object())

    result = mcp_server.birdeye_search("needle")

    assert result["ok"] is True
    assert reconciled == []


def test_birdeye_inspect_reconciles_path_root_before_inspect(monkeypatch):
    events = []

    monkeypatch.setattr(
        mcp_server,
        "_ensure_roots_reconciled",
        lambda roots: events.append(("reconcile", tuple(roots))),
    )
    monkeypatch.setattr(
        mcp_server,
        "inspect_file",
        lambda ctx, path, **kwargs: events.append(("inspect", path, kwargs)) or {"ok": True},
    )
    monkeypatch.setattr(mcp_server, "_load_ctx", lambda: object())

    result = mcp_server.birdeye_inspect("a/src/file.py")

    assert result["ok"] is True
    assert events == [
        ("reconcile", ("a",)),
        ("inspect", "a/src/file.py", {"start_line": None, "end_line": None}),
    ]
