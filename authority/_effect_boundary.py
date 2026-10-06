"""Acceptance for effect authority at the real subprocess boundary.

Uses disposable local repositories only. The same git push is first denied
before spawn, then allowed and executed after the intent gains the effect.
"""
from __future__ import annotations
import json
import os
import pathlib
import shutil
import stat
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import workspace_bridge as wb
from authority import bridge_guard as bg
import sessions

SCRATCH = ROOT / "state" / "effect-boundary-proof"
WSROOT = SCRATCH / "ws"
WORK = WSROOT / "work"
REMOTE = SCRATCH / "remote.git"
SCOPE = HERE / "workspaces" / "effect-proof-disposable" / "active-scope.json"
LEDGER = WSROOT / "docs" / "governance" / "LEDGER.md"
GATE = WSROOT / ".lbe" / "governance" / "gate.json"
CONFIG = ROOT / "config.json"
WSNAME = "effect-proof-disposable"
INTENT = "PROOF-EFFECT-INTENT-001"
results = []

def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL':4} | {label:58} | {detail}")

def git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)

def remove_tree(path):
    def onerror(func, name, _exc):
        os.chmod(name, stat.S_IWRITE)
        func(name)
    if path.exists():
        shutil.rmtree(path, onerror=onerror)

def build():
    remove_tree(SCRATCH)
    SCRATCH.mkdir(parents=True)
    WORK.mkdir(parents=True)
    git(["init", "--bare", str(REMOTE)], SCRATCH)
    git(["init"], WORK)
    (WORK / "src").mkdir()
    (WORK / "src" / "main.py").write_text("print('boundary-proof')\n", encoding="utf-8")
    git(["add", "src/main.py"], WORK)
    git(["-c", "user.email=proof@local", "-c", "user.name=proof", "commit", "-m", "proof"], WORK)
    git(["remote", "add", "origin", str(REMOTE)], WORK)
    return git(["rev-parse", "HEAD"], WORK).stdout.strip()

def write_authority(effect, actor):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    GATE.parent.mkdir(parents=True, exist_ok=True)
    SCOPE.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(
        "# Disposable effect-boundary ledger\n\n"
        f"## INTENT {INTENT}\n\n"
        "STATUS: AUTHORIZED\n"
        "REQUEST: Prove LBE effect authorization at the common spawn boundary.\n"
        "MACHINE_SLICE: EFFECT_PROOF\n"
        f"EXISTING_OWNER: disposable workspace at {WSROOT}\n"
        "EXPECTED_PATH_PREFIXES: work/\n"
        f"EXPECTED_EFFECTS: {'remote_publication' if effect else ''}\n"
        "RESULT: IMPLEMENTATION_PENDING\n",
        encoding="utf-8")
    GATE.write_text(json.dumps({
        "schema_version": 1, "active_slice": "EFFECT_PROOF",
        "rules": {"fail_closed": True},
        "closure": {"status": "OPEN", "publication": "AUTHORIZED"},
    }, indent=2) + "\n", encoding="utf-8")
    SCOPE.write_text(json.dumps({
        "schema_version": 1, "state": "ACTIVE", "actors": [actor],
        "capabilities": ["git.mutate"], "paths": [str(WSROOT)], "deny": [],
    }, indent=2) + "\n", encoding="utf-8")

def remote_ref():
    r = git(["rev-parse", "--verify", "refs/heads/proof"], REMOTE)
    return r.stdout.strip() if r.returncode == 0 else None

def execute_push():
    return wb._execute_argv(
        workspace=wb.Workspace(WSNAME, WORK),
        argv=("git", "push", "origin", "HEAD:refs/heads/proof"),
        timeout_seconds=30, config_path=CONFIG,
        task_id="effect-boundary-proof", capture_mutation=True)

def main():
    head = build()
    ident = bg.derived_actor()
    check("trusted local identity resolved", not ident.get("error"), str(ident.get("actor")))
    if ident.get("error"):
        return 1
    actor = ident["actor"]

    write_authority(False, actor)
    sid = sessions.establish_process_session(
        os.getpid(), actor=actor, workspace=WSNAME,
        transport="acceptance", peer="effect-boundary-proof")["session_id"]
    sessions.bind_intent(sid, INTENT, bound_by="acceptance")
    denied = execute_push()
    check("DENY returned through common execution boundary",
          denied.get("error") == "EFFECT_NOT_AUTHORIZED", str(denied.get("error")))
    check("DENY did not spawn child", denied.get("spawned") is False, str(denied.get("spawned")))
    check("DENY left remote ref unchanged", remote_ref() is None, str(remote_ref()))
    check("DENY carries authority receipt", bool((denied.get("authority") or {}).get("receipt")), "")

    write_authority(True, actor)
    sid2 = sessions.establish_process_session(
        os.getpid(), actor=actor, workspace=WSNAME,
        transport="acceptance", peer="effect-boundary-proof")["session_id"]
    sessions.bind_intent(sid2, INTENT, bound_by="acceptance")
    allowed = execute_push()
    check("ALLOW returned through same boundary", allowed.get("ok") is True, str(allowed.get("error", "")))
    check("ALLOW spawned child", allowed.get("spawned") is True, str(allowed.get("spawned")))
    check("ALLOW changed remote exactly to local HEAD", remote_ref() == head, str(remote_ref()))
    auth = allowed.get("authority") or {}
    check("ALLOW receipt records remote_publication",
          auth.get("effect") == "remote_publication" and bool(auth.get("receipt")), str(auth.get("effect")))
    check("execution evidence produced",
          isinstance(allowed.get("execution_evidence"), dict)
          and bool((allowed.get("execution_evidence") or {}).get("command_hash")), "")

    before = remote_ref()
    sessions.revoke(sid2, revoked_by="acceptance")
    revoked = execute_push()
    check("REVOKE denies same route", revoked.get("error") == "SESSION_REVOKED", str(revoked.get("error")))
    check("REVOKE prevents spawn", revoked.get("spawned") is False, str(revoked.get("spawned")))
    check("REVOKE leaves remote unchanged", remote_ref() == before, str(remote_ref()))

    sid3 = sessions.establish_process_session(
        os.getpid(), actor=actor, workspace=WSNAME,
        transport="acceptance", peer="effect-boundary-proof-reconnect")["session_id"]
    fresh = execute_push()
    check("fresh session differs", sid3 not in {sid, sid2}, sid3[:18])
    check("reconnect is fresh UNBOUND",
          fresh.get("error") == "SESSION_AUTHORITY_UNBOUND", str(fresh.get("error")))
    check("UNBOUND prevents spawn", fresh.get("spawned") is False, str(fresh.get("spawned")))

    try:
        SCOPE.unlink()
    except OSError:
        pass
    print()
    print(f"TOTAL={len(results)} PASSED={sum(results)} FAILED={len(results)-sum(results)}")
    return 0 if all(results) else 1

if __name__ == "__main__":
    raise SystemExit(main())
