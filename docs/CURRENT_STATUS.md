# BirdEye current status

**Status date:** 2026-10-02

This document separates repository truth from machine-local/runtime observations. Re-check current GitHub and runtime state before treating it as a permanent status ledger.

## Canonical repository position

At the start of this documentation update, GitHub `main` was:

```text
f8fd4eaecbad911f1baf8466779d6df9dd17e2b8
```

That commit merged PR #19. Recent integrated work:

| Area | Evidence | Classification |
| --- | --- | --- |
| Read-only live SHA-256 verification | PR #16 merged; MCP JSON-RPC acceptance exercised match, mismatch, >5 MB, missing path, and no generation mutation | RUNTIME_PROVEN |
| Dirty-checkout reconciliation | PR #18 merged; tracked source/config reconciled while unrelated untracked artifacts were preserved | PROVEN |
| Root-filtered search without query-time reconciliation | PR #19 merged after bounded A/B investigation | PROVEN |
| Watch/root indexing model | Current committed source plus `BIRDEYE_WATCH.md` | IMPLEMENTED / documented by component |
| Opt-in system-access contract | PR #17 remains open and is not part of `main` | DOCUMENTED, not integrated |

## Local runtime observations

The following observations describe the machine used during 2026-10-01/02 acceptance; they are not automatically GitHub-main implementation truth.

The installed launcher resolved at `C:\Users\prave\.letterblack\bin\birdeye.cmd`. The persistent User PATH contained that directory. The launcher initially depended on a nonexistent global `python` command; the machine exposed `C:\Windows\py.exe`, and the installed launcher was corrected to use the available Python launcher.

The user successfully ran `birdeye access open` and received `Access tunnel opened.` A separate authorized session invoked the installed launcher and observed `Access tunnel: open`. The user later ran `birdeye access close`, and the closed state was externally observable.

| Claim | Classification |
| --- | --- |
| Installed `birdeye.cmd` launcher executes on that machine | RUNTIME_PROVEN |
| `access open/status/close` control-state lifecycle executes | RUNTIME_PROVEN |
| OPEN/CLOSED state is observable from a separate authorized session | RUNTIME_PROVEN |
| 30-minute idle-expiry logic exists in the reported local CLI implementation | IMPLEMENTED / partial runtime evidence |
| System command is denied through BirdEye while CLOSED | UNVERIFIED |
| System command executes through BirdEye while OPEN | UNVERIFIED |
| System execution produces required BirdEye receipt/journal | UNVERIFIED |
| Restart/reboot always returns system access to CLOSED | UNVERIFIED |
| System executor is local-only with no unintended network surface | UNVERIFIED |

Do not collapse the last five rows into "tunnel works". The control-state CLI and actual system executor are different proof boundaries.

## Source/runtime divergence

A local report identified a `birdeye.py` CLI implementation in a local commit, but `birdeye.py` was not present on GitHub `main` when this documentation branch was created. The installed/local access CLI therefore must not be represented as committed mainline functionality until its source is reconciled through GitHub.

PR #17 documents the intended system-access contract but remains open and is based on an older mainline position. Its text is useful as a contract; its open state is not implementation proof.

## Preserved local artifacts

During the earlier BirdEye reconciliation, tracked changes were reconciled without deleting unrelated untracked/local artifacts. Those artifacts were intentionally left unclassified rather than treated as repository debt. Future cleanup must identify ownership and purpose before deletion or integration.

## Next system-access acceptance boundary

```text
CLOSED
-> attempt harmless command through BirdEye system executor
-> authoritative denial
-> local user opens access
-> repeat the same BirdEye operation
-> command executes
-> execution receipt/journal is observable
-> user closes access
-> same operation is denied again
```

This must use BirdEye's own system-execution surface. Success through an independent remote-control product does not prove BirdEye's executor.