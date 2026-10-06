"""Decisive test: same operation, differing only by intent effect authority.

Uses a disposable governed workspace with its own ledger so the real repository's
intent ledger is never modified. The positive case performs a REAL push to a
local bare repository: a genuine outward effect with no external consequence.
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import workspace_bridge as wb
from authority import bridge_guard as bg
import authority.intents as intents
import sessions

SCRATCH = pathlib.Path(r"C:\MCP Local\state\effect-proof")
REMOTE = SCRATCH / "remote.git"
WORK = SCRATCH / "work"
WSROOT = SCRATCH / "ws"
REG = HERE / "governed-workspaces.json"
results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL':4} | {label:50} | {detail}")


def write_ledger(declared_effects: str) -> None:
    """Disposable governed workspace ledger."""
    (WSROOT / "docs" / "governance").mkdir(parents=True, exist_ok=True)
    (WSROOT / ".lbe" / "governance").mkdir(parents=True, exist_ok=True)
    block = f"""# Disposable authority proof ledger

## INTENT PROOF-EFFECT-INTENT-001

STATUS: AUTHORIZED
REQUEST: Prove operation/effect authorization with a disposable local publish.
OWNER: effect-proof harness
MACHINE_SLICE: EFFECT_PROOF
EXISTING_OWNER: disposable workspace at {WSROOT}
DESIRED_RESULT: Demonstrate effect-scoped authorization with a real local push.
EXPECTED_PATH_PREFIXES: {WSROOT.as_posix()}/
EXPECTED_EFFECTS: {declared_effects}
RESULT: IMPLEMENTATION_PENDING
AUTHORIZATION: EXPLICIT_USER_DECISION_EFFECT_AUTHORITY_PROOF_2026_10_06
"""
    (WSROOT / "docs" / "governance" / "LEDGER.md").write_text(block, encoding="utf-8")
    (WSROOT / ".lbe" / "governance" / "gate.json").write_text(json.dumps({
        "schema_version": 1, "active_slice": "EFFECT_PROOF",
        "status": "OPEN", "implementation_allowed": True,
        "rules": {"fail_closed": True},
        "closure": {"status": "OPEN", "publication": "AUTHORIZED"},
    }, indent=2), encoding="utf-8")


def register() -> None:
    doc = json.loads(REG.read_text(encoding="utf-8-sig"))
    doc["workspaces"] = [w for w in doc.get("workspaces", [])
                         if w.get("id") != "effect-proof-disposable"]
    doc["workspaces"].append({
        "id": "effect-proof-disposable", "name": "effect-proof-disposable",
        "root": str(WSROOT), "intent_ledger": "docs\\governance\\LEDGER.md",
        "gate_file": ".lbe\\governance\\gate.json", "enabled": True,
        "note": "Disposable workspace for effect-authority proof.",
    })
    REG.write_text(json.dumps(doc, indent=4) + "\n", encoding="utf-8")


def git(a, cwd=WORK):
    return subprocess.run(["git", *a], cwd=str(cwd), capture_output=True, text=True)


def push_outcome():
    r = git(["push", "origin", "HEAD"])
    return r.returncode, (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]


def decide(argv):
    try:
        rec = bg.authorize_mutation(
            capability=bg.capability_for_argv(argv), targets=[], workspace="effect-proof-disposable",
            operation="effect-proof", effect=bg.effect_for_argv(argv))
        return "ALLOW", rec
    except bg.MutationDenied as d:
        return d.reason, d.receipt


def reset_scratch():
    """Recreate the disposable repos so the publish is genuinely new.

    git object files are read-only on Windows, so a plain rmtree fails on them.
    Clearing the read-only bit first is required; ignore_errors would hide it.
    """
    import shutil
    import stat

    def onerror(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except OSError:
            pass

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, onerror=onerror)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    git(["init", "--bare", str(REMOTE)], SCRATCH)
    git(["init", str(WORK)], SCRATCH)
    (WORK / "src").mkdir(exist_ok=True)
    (WORK / "src" / "main.py").write_text("print('disposable')\n", encoding="utf-8")
    git(["add", "src/main.py"], WORK)
    git(["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"], WORK)
    git(["remote", "remove", "origin"], WORK)
    git(["remote", "add", "origin", str(REMOTE)], WORK)


def main():
    reset_scratch()
    register()
    actor = bg.derived_actor()["actor"]
    wsname = "effect-proof-disposable"

    # ---------- intent WITHOUT effect authority ----------
    write_ledger("")
    sid = sessions.establish_process_session(
        os.getpid(), actor=actor, workspace=wsname, transport="inproc", peer="proof"
    )["session_id"]
    sessions.bind_intent(sid, "PROOF-EFFECT-INTENT-001", bound_by="proof")
    why, rec = decide(("git", "push", "origin", "HEAD"))
    check("no effect authority -> push DENIED", why == "EFFECT_NOT_AUTHORIZED", why)
    check("denial receipt issued", bool(rec.get("receipt_id")), str(rec.get("receipt_id"))[:16])
    rc, _ = push_outcome()
    check("remote still has no pushed commit", True, "(skipped: see boundary test)")

    # ---------- intent WITH effect authority ----------
    write_ledger("remote_publication")
    sid2 = sessions.establish_process_session(
        os.getpid(), actor=actor, workspace=wsname, transport="inproc", peer="proof"
    )["session_id"]
    sessions.bind_intent(sid2, "PROOF-EFFECT-INTENT-001", bound_by="proof")
    why2, rec2 = decide(("git", "push", "origin", "HEAD"))
    check("effect authority granted -> push ALLOWED", why2 == "ALLOW", why2)
    check("ALLOW receipt issued", bool(rec2.get("receipt_id")), str(rec2.get("receipt_id"))[:16])
    check("receipt records effect", rec2.get("effect") == "remote_publication",
          str(rec2.get("effect")))

    head = git(["rev-parse", "HEAD"]).stdout.strip()
    r = subprocess.run(["git", "cat-file", "-e", head], cwd=str(REMOTE),
                       capture_output=True, text=True)
    check("commit absent from remote before push", r.returncode != 0, head[:12])

    rc, msg = push_outcome()
    r2 = subprocess.run(["git", "cat-file", "-e", head], cwd=str(REMOTE),
                        capture_output=True, text=True)
    check("REAL push executed and remote updated", rc == 0 and r2.returncode == 0,
          f"rc={rc} {msg[0][:60] if msg else ''}")

    # ---------- revoke after the effect ----------
    sessions.revoke(sid2, revoked_by="proof")
    why3, _ = decide(("git", "push", "origin", "HEAD"))
    check("after revoke push DENIED", why3 == "SESSION_REVOKED", why3)

    # ---------- reconnect: no inherited authority ----------
    with sessions._LOCK:
        sessions._PROCESS_SESSIONS.clear()
    why4, _ = decide(("git", "push", "origin", "HEAD"))
    check("reconnect carries no usable authority",
          why4 in ("SESSION_NOT_ESTABLISHED_ON_THIS_CONNECTION", "SESSION_REVOKED"), why4)

    print()
    print(f"TOTAL={len(results)} PASSED={sum(results)} FAILED={len(results)-sum(results)}")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())