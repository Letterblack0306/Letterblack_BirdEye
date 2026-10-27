"""Proof that BirdEye Control Center is card-first and Git history is sourced from real local Git."""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

import control_center_api as api
import http_mcp_server


def test_card_navigation_uses_one_dashboard_and_anchored_floating_panel():
    html = (api.ROOT / "control_center.html").read_text(encoding="utf-8")
    js = (api.ROOT / "control_center.js").read_text(encoding="utf-8")
    css = (api.ROOT / "control_center.css").read_text(encoding="utf-8")
    assert '<aside class="sidebar"' not in html
    assert 'id="detail-panel"' in html and 'aria-modal="true"' in html
    assert 'id="panel-scrim"' in html
    assert 'id="panel-minimize"' in html and 'id="panel-close"' in html
    assert 'function openFloatingPanel(' in js and 'function closeFloatingPanel(' in js
    assert 'function cardGeometry(' in js and 'movePanel(true).then(finish)' in js
    assert "document.getElementById('main').inert=true" in js
    assert "if(event.key==='Escape')" in js
    assert 'git-graph-svg' in css and 'backdrop-filter:blur(' in css
    assert 'function gitView(options={})' in js
    for id in ("workspaces", "tools", "skills", "planning", "git",
               "execution", "data", "connections", "settings"):
        assert "id:'" + id + "'" in js or "id:\"" + id + "\"" in js


def test_git_graph_is_real_and_readonly_on_current_canonical_repo():
    d = api.git_history_for_root("__birdeye__")
    assert d["ok"] and d["is_repository"] is True
    assert d["head"] and d["branch"]
    expected = min(api._GIT_MAX_COMMITS, int(api._git_read(api.ROOT, "rev-list", "--count", "--all").strip()))
    assert len(d["commits"]) == expected, "Undecorated commit rows were silently lost"
    assert any(c["is_head"] for c in d["commits"])
    for c in d["commits"]:
        assert len(c["sha"]) == 40
        assert isinstance(c["parents"], list)
        assert c["subject"] and c["author"]
    detail = api.git_commit_for_root("__birdeye__", d["head"])
    assert detail["ok"] and detail["sha"] == d["head"]
    assert detail["source"] == "local Git commit tree"


def test_git_denies_unknown_roots_and_unsafe_ids_without_touching_files():
    before = (api.ROOT / "config.json").read_bytes()
    assert not api.git_history_for_root("C:\\Windows")["ok"]
    assert not api.git_commit_for_root("__birdeye__", "../../etc/passwd")["ok"]
    assert not api.git_commit_for_root("unknown", "0" * 40)["ok"]
    assert (api.ROOT / "config.json").read_bytes() == before


async def live_route_cases():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=http_mcp_server.build_app()),
        base_url="http://127.0.0.1:8766",
        timeout=35,
    ) as client:
        results = {}
        for path in ["/", "/apps/birdeye-control/", "/control.css",
                     "/control.js", "/control/api/git?root=__birdeye__",
                     "/control/api/git?root=bad-root",
                     "/control/api/git/commit?root=__birdeye__&commit=not-a-hash",
                     "/apps/status"]:
            results[path] = await client.get(path)
        return results


def test_routes_and_existing_launcher_regression():
    r = asyncio.run(live_route_cases())
    assert r["/"].status_code == 200
    assert "LB Effects Editor" in r["/"].text
    assert "BirdEye Control Center" in r["/"].text
    assert r["/apps/birdeye-control/"].status_code == 200
    assert r["/control.css"].status_code == 200
    assert r["/control.js"].status_code == 200
    assert r["/control/api/git?root=__birdeye__"].status_code == 200
    assert r["/control/api/git?root=bad-root"].status_code == 404
    assert r["/control/api/git/commit?root=__birdeye__&commit=not-a-hash"].status_code == 400
    assert r["/apps/status"].status_code == 200


def test_workspace_inspection_reuses_visual_git_renderer_and_remote_work():
    js = (api.ROOT / "control_center.js").read_text(encoding="utf-8")
    assert "gitView({host:visual,root:selected,embedded:true})" in js
    assert "function gitView(options={})" in js
    assert "gitWorktreeSurface(worktree,data)" in js
    assert "gitRemoteWorkSurface(remoteBody,github,embedded)" in js
    assert "const commits=embedded?(data.commits||[]).slice(0,10)" in js
    assert "Open pull requests" in js and "Open issues" in js
    assert "detail.body.append(visual,sourceExtra)" in js
    assert "sourceExtra.append(el('summary'" in js
    assert "if(embedded)host.append(layout,remote.outer)" in js
    assert "else host.append(remote.outer,layout)" in js


def test_workspace_git_status_reflects_actual_local_repository():
    data = api.git_history_for_root("__birdeye__")
    assert data["ok"] and data["is_repository"]
    status = data["worktree"]
    assert status["available"]
    for name in ("staged", "unstaged", "untracked", "conflicted"):
        assert isinstance(status[name], int) and status[name] >= 0
    assert status["dirty"] == any(status[name] > 0 for name in
                                 ("staged", "unstaged", "untracked", "conflicted"))
    assert status["upstream"] is None or isinstance(status["upstream"], str)
    assert len(status["files"]) <= 20


def test_git_remote_origin_validation_rejects_non_github_hosts(monkeypatch):
    monkeypatch.setattr(api, "_git_read", lambda path, *args, **kwargs: "https://github.com/example/project.git")
    good = api.github_repository_for_root("__birdeye__")
    assert good["ok"] and good["repository"] == "example/project"
    monkeypatch.setattr(api, "_git_read", lambda path, *args, **kwargs: "git@github.com:example/project.git")
    assert api.github_repository_for_root("__birdeye__")["ok"]
    for url in ("https://example.com/example/project.git",
                "https://username:secret@github.com/example/project.git",
                "https://github.com/example/project/extra.git",
                "https://github.com/../../etc/passwd"):
        monkeypatch.setattr(api, "_git_read", lambda path, *args, **kwargs: url)
        assert not api.github_repository_for_root("__birdeye__")["ok"]


def test_github_open_issues_and_prs_are_separate_and_actual(monkeypatch):
    monkeypatch.setattr(api, "github_repository_for_root", lambda root: {
        "ok": True, "repository": "testowner/repo", "url": "https://github.com/testowner/repo"})
    def fake_github(repo, kind):
        assert repo == "testowner/repo"
        if kind == "issues":
            return [
                {"number": 12, "title": "Fix indexing", "labels": [{"name": "bug"}],
                 "updated_at": "2026-10-01T12:00:00Z"},
                {"number": 14, "title": "PR masquerading as issue",
                 "pull_request": {"url": "https://api.github.com/x"}},
            ]
        return [{"number": 32, "title": "Add UI panel", "draft": True,
                 "head": {"ref": "feature/new-ui"}, "base": {"ref": "main"}}]
    monkeypatch.setattr(api, "_github_get", fake_github)
    data = api.github_work_for_root("__birdeye__")
    assert data["available"] and data["issues_status"] == data["pull_requests_status"] == "live"
    assert len(data["issues"]) == 1 and len(data["pull_requests"]) == 1
    assert data["issues"][0]["url"] == "https://github.com/testowner/repo/issues/12"
    assert data["pull_requests"][0]["url"] == "https://github.com/testowner/repo/pull/32"
    assert data["pull_requests"][0]["draft"] is True


def test_github_unavailable_does_not_masquerade_as_no_open_work(monkeypatch):
    monkeypatch.setattr(api, "github_repository_for_root", lambda root: {
        "ok": True, "repository": "testowner/repo", "url": "https://github.com/testowner/repo"})
    def unavailable(repo, kind):
        raise RuntimeError("Remote API request failed")
    monkeypatch.setattr(api, "_github_get", unavailable)
    data = api.github_work_for_root("__birdeye__")
    assert not data["available"]
    assert data["issues_status"] == "unavailable"
    assert data["pull_requests_status"] == "unavailable"
    assert "Unavailable" in data["reason"] or "unavailable" in data["reason"]
