"""End-to-end acceptance over the stdio control-plane transport.

Proves, with no retries:
  connection -> server-derived identity -> session -> operator intent binding
  -> authorization -> ALLOW -> controlled effect -> receipt -> revoke -> DENY
  -> reconnect -> new UNBOUND session that inherits nothing.

The control plane runs as a child of THIS process, so its parent PID is us and
identity resolution exercises the real parent-derivation path.
"""
import hashlib
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import controller as ctl
import sessions

SERVER = HERE / "control_plane.py"
INTENT = "LBE-INTENT-TUI-INTERACTIVE-ACCEPTANCE-AND-CLEAN-CLONE-001"
SCRATCH = pathlib.Path(
    r"C:\Agents-Memory-Tool-v6-integration\docs\acceptance\_lbe_authority_scratch.txt"
)
results = []


def check(label, ok, detail=""):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL':4} | {label:48} | {detail}")


def sha(p: pathlib.Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else "ABSENT"


async def main() -> int:
    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=None)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()

            tools = sorted(t.name for t in (await s.list_tools()).tools)
            check("transport: tools listed", "lbe_authorize" in tools, ",".join(tools))

            # --- identity, caller claims ignored
            r = await s.call_tool("lbe_whoami",
                                  {"actor": "attacker", "session_id": "sess_forged"})
            ident = (r.content[0].text and json.loads(r.content[0].text)) or {}
            i = ident.get("identity", {})
            check("server-derived SID identity", bool(i.get("sid")), i.get("sid", "")[:28])
            check("actor derived from parent image", i.get("actor", "").startswith("unregistered")
                  or i.get("actor") == "python-host", i.get("actor", ""))
            check("caller actor ignored", ident.get("supplied_actor_ignored") == "attacker", "")
            check("caller session ignored", ident.get("supplied_session_ignored") == "sess_forged", "")

            # --- unbound session cannot mutate
            r = await s.call_tool("lbe_authorize", {
                "capability": "filesystem.write", "targets": [str(SCRATCH)],
                "operation": "write_text"})
            d = json.loads(r.content[0].text)
            check("no-session / unbound denied", d.get("error") in
                  ("SESSION_AUTHORITY_UNBOUND", "SESSION_NOT_ESTABLISHED_ON_THIS_CONNECTION"),
                  d.get("error", ""))
            check("denial produced a receipt", bool(d.get("receipt")), str(d.get("receipt"))[:16])
            check("no effect while unbound", not SCRATCH.exists(), "")

            # --- establish
            r = await s.call_tool("lbe_establish", {"workspace": "lbe-workspace"})
            e = json.loads(r.content[0].text)
            sid = (e.get("session") or {}).get("session_id")
            check("session established", bool(sid), (sid or "")[:24])
            check("established session UNBOUND", (e.get("session") or {}).get("state") == "UNBOUND",
                  (e.get("session") or {}).get("state", ""))

            # --- fabricated intent binding grants nothing
            sessions.bind_intent(sid, "LBE-INTENT-FABRICATED-999", bound_by="acceptance")
            r = await s.call_tool("lbe_authorize", {
                "capability": "filesystem.write", "targets": [str(SCRATCH)],
                "operation": "write_text"})
            d = json.loads(r.content[0].text)
            check("fabricated intent grants nothing", d.get("error") == "INTENT_NOT_REGISTERED",
                  d.get("error", ""))

            # --- bind the real intent
            sessions.bind_intent(sid, INTENT, bound_by="acceptance")

            # --- ALLOW PATH
            r = await s.call_tool("lbe_authorize", {
                "capability": "filesystem.write", "targets": [str(SCRATCH)],
                "operation": "write_text"})
            d = json.loads(r.content[0].text)
            check("ALLOW PATH REACHED", d.get("ok") is True, json.dumps(d)[:120])
            rec = d.get("receipt") or {}
            check("receipt issued", bool(rec.get("receipt_id")), str(rec.get("receipt_id"))[:16])
            check("receipt carries intent", rec.get("intent_id") == INTENT, rec.get("intent_id", ""))
            check("receipt carries session", rec.get("session_id") == sid, rec.get("session_id", ""))
            check("receipt carries actor", bool(rec.get("actor")), rec.get("actor", ""))

            # --- controlled effect with exact restoration
            before = sha(SCRATCH)
            payload = "lbe-authority-scratch\n"
            SCRATCH.write_text(payload, encoding="utf-8")
            written = sha(SCRATCH)
            # Compare against the bytes actually produced: write_text applies
            # platform newline translation, so assuming LF here would be wrong.
            check("effect applied through authorized path",
                  SCRATCH.read_bytes() == payload.encode().replace(b"\n", b"\r\n"),
                  written[:16])
            try:
                SCRATCH.unlink()
            except OSError:
                time.sleep(0.3)
                try:
                    SCRATCH.unlink()
                except OSError:
                    pass
            check("effect reverted exactly", sha(SCRATCH) == "ABSENT", "ABSENT")

            # --- denials on a bound session
            for label, cap, tgt, want in [
                ("outside intent prefixes", "filesystem.write",
                 r"C:\Agents-Memory-Tool-v6-integration\agent.py", "INTENT_SCOPE_MISMATCH"),
                (".git denied by scope", "filesystem.write",
                 r"C:\Agents-Memory-Tool-v6-integration\.git\config", "PATH_DENIED"),
                ("ungranted capability", "browser.act", str(SCRATCH), "CAPABILITY_NOT_GRANTED"),
                ("out-of-scope target", "filesystem.write",
                 r"C:\Windows\System32\drivers\etc\hosts", "TARGET_OUT_OF_SCOPE"),
            ]:
                r = await s.call_tool("lbe_authorize",
                                      {"capability": cap, "targets": [tgt], "operation": "probe"})
                d = json.loads(r.content[0].text)
                check(f"denied: {label}", d.get("error") == want, d.get("error", ""))

            # --- revoke
            sessions.revoke(sid, revoked_by="acceptance")
            r = await s.call_tool("lbe_authorize", {
                "capability": "filesystem.write", "targets": [str(SCRATCH)],
                "operation": "write_text"})
            d = json.loads(r.content[0].text)
            check("post-revoke denied", d.get("error") == "SESSION_REVOKED", d.get("error", ""))
            check("post-revoke no effect", not SCRATCH.exists(), "")

            # --- another connection must not inherit
            with sessions._LOCK:
                sessions._PROCESS_SESSIONS.clear()

    # fresh process = fresh connection
    params2 = StdioServerParameters(command=sys.executable, args=[str(SERVER)], env=None)
    async with stdio_client(params2) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            r = await s.call_tool("lbe_authorize", {
                "capability": "filesystem.write", "targets": [str(SCRATCH)],
                "operation": "write_text"})
            d = json.loads(r.content[0].text)
            check("reconnect inherits nothing", d.get("error") in
                  ("SESSION_NOT_ESTABLISHED_ON_THIS_CONNECTION", "SESSION_AUTHORITY_UNBOUND"),
                  d.get("error", ""))
            r = await s.call_tool("lbe_establish", {"workspace": "lbe-workspace"})
            e = json.loads(r.content[0].text)
            check("reconnect new session UNBOUND",
                  (e.get("session") or {}).get("state") == "UNBOUND",
                  (e.get("session") or {}).get("state", ""))
            check("reconnect session differs", (e.get("session") or {}).get("session_id") != sid, "")
            r = await s.call_tool("lbe_authorize", {
                "capability": "filesystem.write", "targets": [str(SCRATCH)],
                "operation": "write_text"})
            d = json.loads(r.content[0].text)
            check("new session denied before binding", d.get("error") == "SESSION_AUTHORITY_UNBOUND",
                  d.get("error", ""))

    led = sessions.LEDGER
    doc = json.loads(led.read_text(encoding="utf-8"))
    doc["sessions"] = {}
    led.write_text(json.dumps(doc, indent=4, sort_keys=True) + "\n", encoding="utf-8")

    print()
    print("scratch_absent=" + str(not SCRATCH.exists()))
    print("TOTAL=%d PASSED=%d FAILED=%d" % (len(results), sum(results), len(results) - sum(results)))
    return 0 if all(results) else 1


if __name__ == "__main__":
    import asyncio
    raise SystemExit(asyncio.run(main()))