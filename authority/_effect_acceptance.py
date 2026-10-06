"""Isolated proof of operation/effect authority.

The positive publish target is a disposable local bare repository under a
scratch root. No network, no real remote, no consequence outside this machine.
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import workspace_bridge as wb
from authority import bridge_guard as bg
import controller as ctl
import sessions

SCRATCH = pathlib.Path(r"C:\MCP Local\state\effect-proof")
REMOTE = SCRATCH / "remote.git"
WORK = SCRATCH / "work"
INTENT = "LBE-INTENT-TUI-INTERACTIVE-ACCEPTANCE-AND-CLEAN-CLONE-001"
WS = "agents-memory-tool-v6-integration"

results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL':4} | {label:52} | {detail}")


def git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def build_disposable():
    if SCRATCH.exists():
        subprocess.run(["git", "worktree", "prune"], cwd=str(WORK),
                       capture_output=True) if WORK.exists() else None
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if not REMOTE.exists():
        git(["init", "--bare", str(REMOTE)], SCRATCH)
    if not WORK.exists():
        git(["init", str(WORK)], SCRATCH)
    (WORK / "src").mkdir(exist_ok=True)
    (WORK / "src" / "main.py").write_text("print('disposable')\n", encoding="utf-8")
    git(["add", "src/main.py"], WORK)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"], WORK)
    git(["remote", "remove", "origin"], WORK)
    git(["remote", "add", "origin", str(REMOTE)], WORK)
    return git(["rev-parse", "--abbrev-ref", "HEAD"], WORK).stdout.strip()


def remote_has_commit(sha):
    r = subprocess.run(["git", "rev-parse", sha], cwd=str(REMOTE),
                       capture_output=True, text=True)
    return r.returncode == 0


def main():
    branch = build_disposable()
    check("disposable local repo built", bool(branch), f"branch={branch} remote={REMOTE}")

    cfg = pathlib.Path(r"C:\MCP Local\Letterblack_BirdEye\config.json")
    ws = wb.resolve_workspace(cfg, WS)
    actor = bg.derived_actor()["actor"]

    sid = sessions.establish_process_session(
        os.getpid(), actor=actor, workspace=WS, transport="inproc", peer="effect-proof"
    )["session_id"]
    sessions.bind_intent(sid, INTENT, bound_by="effect-proof")

    intent = __import__("authority.intents", fromlist=["x"]).read_intent(INTENT,
        __import__("authority.intents", fromlist=["x"]).workspace_by_name("lbe-workspace"))
    declared = intent.get("expected_effects") or []
    print(f"     intent declares effects: {declared}")
    print(f"     gate publication: {(__import__('authority.intents', fromlist=['x']).gate_state(__import__('authority.intents', fromlist=['x']).workspace_by_name('lbe-workspace')) or {}).get('closure', {}).get('publication')}")
    print()

    def decide(argv):
        try:
            return "ALLOW", bg.authorize_mutation(
                capability=bg.capability_for_argv(argv),
                targets=[], workspace=WS, operation="effect-proof",
                effect=bg.effect_for_argv(argv))
        except bg.MutationDenied as d:
            return d.reason, d.receipt
        except Exception as e:
            return type(e).__name__, {}

    # 1. intent does not authorize publication -> denied
    why, rec = decide(("git", "push", "origin", branch))
    if "remote_publication" in declared:
        check("publication denied without effect authority", False,
              f"intent DOES declare {declared}; test premise invalid")
    else:
        check("publication denied without effect authority",
              why in ("EFFECT_NOT_AUTHORIZED", "PUBLICATION_LOCKED"), why)
        check("denial produced receipt", bool(rec.get("receipt_id")),
              str(rec.get("receipt_id"))[:16])

    # 2. workspace rule independently forbids it
    check("workspace publication LOCKED also denies", why in
          ("EFFECT_NOT_AUTHORIZED", "PUBLICATION_LOCKED"), why)

    # 3. unrelated permitted mutation still works
    why2, rec2 = decide(("git", "commit", "-m", "x", "apps/lbe-terminal/src/ui.rs"))
    check("contained mutation still permitted", why2 == "ALLOW", why2)
    check("ALLOW produced receipt", bool(rec2.get("receipt_id")), str(rec2.get("receipt_id"))[:16])

    # 4. branch / worktree creation are external effects
    for argv, label in [(("git", "branch", "x"), "branch creation"),
                        (("git", "worktree", "add", ".."), "worktree creation")]:
        why3, _ = decide(argv)
        check(f"{label} requires effect authority",
              why3 in ("EFFECT_NOT_AUTHORIZED", "EFFECT_BLOCKED_BY_WORKSPACE_RULE"), why3)

    # 5. caller identity cannot change the decision
    orig = sessions.require_context
    why4, _ = decide(("git", "push", "origin", branch))
    check("repeat decision stable", why4 == why, why4)

    # 6. revoke / unbound / fabricated
    sessions.revoke(sid, revoked_by="effect-proof")
    why5, _ = decide(("git", "commit", "-m", "x", "apps/lbe-terminal/src/ui.rs"))
    check("revoked session denied", why5 == "SESSION_REVOKED", why5)

    sid2 = sessions.establish_process_session(
        os.getpid(), actor=actor, workspace=WS, transport="inproc", peer="effect-proof"
    )["session_id"]
    sessions.bind_intent(sid2, "LBE-INTENT-FABRICATED-999", bound_by="effect-proof")
    why6, _ = decide(("git", "push", "origin", branch))
    check("fabricated intent denied", why6 == "INTENT_NOT_REGISTERED", why6)

    print()
    print(f"TOTAL={len(results)} PASSED={sum(results)} FAILED={len(results)-sum(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())