#!/usr/bin/env python
"""Privileged local operator CLI for LBE authority.

Deliberately NOT an MCP tool. The agent cannot reach this surface, so it cannot
bind its own session to an intent.

  session list
  session bind <session_id> <intent_id>
  session revoke <session_id>
  intent show <intent_id>
  authorize <intent_id> <capability> <target>...   (dry-run decision)

Binding is association only. It grants nothing. Every mutation is still
decided by controller.authorize() against live ledger and external-scope state.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import controller as ctl
import intents
import sessions


def _operator() -> str:
    return "local-operator:" + str(__import__("getpass").getuser())


def cmd_session_list() -> int:
    rows = sessions.list_sessions()
    if not rows:
        print("no sessions registered")
        return 0
    print(f"{'SESSION':38} {'STATE':9} {'ACTOR':22} INTENT")
    for s in rows:
        print(
            f"{s['session_id']:38} {s.get('state', '?'):9} "
            f"{str(s.get('actor'))[:22]:22} {s.get('intent_id') or '-'}"
        )
    return 0


def cmd_session_bind(session_id: str, intent_id: str) -> int:
    try:
        rec = sessions.bind_intent(session_id, intent_id, bound_by=_operator())
    except sessions.SessionUnbound as exc:
        print(f"DENY {exc.reason}: {exc.detail}")
        return 2
    except ValueError as exc:
        print(f"DENY {exc}")
        return 2
    print(f"BOUND session={session_id} intent={intent_id} by={rec['bound_by']}")
    print("note: association only. controller.authorize() still decides every mutation.")
    return 0


def cmd_session_revoke(session_id: str) -> int:
    try:
        sessions.revoke(session_id, revoked_by=_operator())
    except sessions.SessionUnbound as exc:
        print(f"DENY {exc.reason}: {exc.detail}")
        return 2
    print(f"REVOKED session={session_id}")
    return 0


def cmd_intent_show(intent_id: str) -> int:
    for ws in intents.load_workspaces():
        intent = intents.read_intent(intent_id, ws)
        if intent:
            live, reason = intents.is_live(intent)
            matched, sreason = intents.slice_matches(intent, ws)
            print(json.dumps(
                {
                    "workspace": ws["name"],
                    "intent_id": intent["intent_id"],
                    "status": intent["status"],
                    "live": live,
                    "live_reason": reason,
                    "slice": intent["machine_slice"],
                    "slice_matches_gate": matched,
                    "slice_reason": sreason,
                    "owner": intent["owner"],
                    "prefixes": intent["expected_path_prefixes"],
                    "ledger": intent["ledger_path"],
                },
                indent=2,
            ))
            return 0
    print(f"INTENT_NOT_REGISTERED {intent_id}")
    return 2


def cmd_authorize(intent_id: str, capability: str, *targets: str) -> int:
    """Dry-run the decision. Executes nothing."""
    scope_paths = []
    for ws in intents.load_workspaces():
        scope_paths.append(ws["name"])
    workspace = None
    for name in scope_paths:
        try:
            ctl.authorize(
                intent_id=intent_id, actor="local-operator:" + __import__("getpass").getuser(),
                capability=capability, targets=list(targets), workspace=name,
            )
            print(f"ALLOW workspace={name}")
            return 0
        except ctl.AuthorityDenied as exc:
            workspace = name
            last = exc
    print(f"DENY {last.reason}")
    print(json.dumps(last.detail, indent=2, default=str))
    return 2


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 64
    cmd, rest = argv[1], argv[2:]
    if cmd == "session" and rest:
        sub = rest[0]
        if sub == "list":
            return cmd_session_list()
        if sub == "bind" and len(rest) == 3:
            return cmd_session_bind(rest[1], rest[2])
        if sub == "revoke" and len(rest) == 2:
            return cmd_session_revoke(rest[1])
    if cmd == "intent" and len(rest) == 2 and rest[0] == "show":
        return cmd_intent_show(rest[1])
    if cmd == "authorize" and len(rest) >= 3:
        return cmd_authorize(rest[1], rest[2], *rest[3:])
    print(__doc__)
    return 64


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))