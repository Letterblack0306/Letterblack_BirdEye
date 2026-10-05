# BirdEye repository guide

## Purpose

BirdEye is a local gateway between reasoning agents and Letterblack project evidence/capabilities. Its job is to make the current workspace observable and actionable without forcing every agent to implement its own filesystem indexing, history lookup, knowledge lookup, execution policy, and evidence recording.

BirdEye does not replace the agent's reasoning loop. It exposes bounded capabilities and trustworthy observations.

## Evidence hierarchy

When sources disagree, use the strongest current evidence:

```text
1. live runtime observation
2. current workspace/source state
3. repository contracts and current committed implementation
4. BirdEye indexed/current evidence
5. GPT-K/reference material
6. memory/history/prior conversations
```

Memory and historical logs are context and decision history, not current implementation truth.

For implementation claims, trace the complete path where relevant:

```text
request
-> registered capability
-> handler
-> state mutation/read
-> downstream consumer
-> actual effect
-> authoritative observation
```

Do not stop at "method exists", "tool is registered", "test passed", or "data is present".

## Main components

| Area | Primary files | Responsibility |
| --- | --- | --- |
| MCP surface | `mcp_server.py` | Tool schemas, registration, dispatch, MCP lifecycle |
| Indexed evidence | `eye_database.py`, `eye_inventory.py` | File/index state, hashes, indexed observations |
| Workspace observation | `birdeye_watcher.py`, `BIRDEYE_WATCH.md` | Incremental root watching and current-state receipts |
| Agent-facing logic | `agent.py` | BirdEye agent-side capability integration |
| Workspace execution | `workspace_bridge.py`, `execution_evidence.py` | Governed workspace execution and evidence |
| Projection/replay | `birdeye_projection.py`, `birdeye_projection_config.json` | Projection/replay behavior |
| Root configuration | `config.json`, `eye_workspace.json` | Explicit configured roots and workspace metadata |
| Browser compatibility | `browser_relay/` | Compatibility/bridge surface; not canonical reasoning authority |
| Contracts/plans | `docs/` | Behavioral contracts and bounded plans |

Always inspect current source before assuming this table proves a particular runtime behavior.

## Root and indexing semantics

The explicit root registry in `config.json` is canonical for unified indexing. Registered roots can be enabled/disabled and carry indexing/hash/authority policy. Historical sources may be indexed for retrieval, but their semantic authority remains historical.

The watcher records file metadata and SHA-256 according to root policy. Unchanged files can reuse prior hashes. A root should not be described as CURRENT unless a completed run establishes that state.

## Hash verification

The current MCP includes read-only live hash verification. PR #16 acceptance established full-file SHA-256 (including >5 MB), missing-path reporting without recording deletion, no canonical-generation mutation from verification, and successful MCP JSON-RPC invocation.

## Query behavior

Root-filtered searches query indexed state without performing implicit whole-root reconciliation on every query. PR #19 removed that query-time reconciliation path. Reconciliation/index refresh and retrieval are separate responsibilities.

## Workspace execution

Prefer `workspace_run` or `workspace_run_sequence` when an operation belongs to a configured workspace. Workspace execution remains policy-aware and execution claims require execution evidence rather than source inspection alone.

## System-wide access

System-wide execution is intentionally separate from workspace execution. The intended user contract is `birdeye access open|status|close`. Opening access is an explicit local-user action; an agent must not silently open it.

The access-state CLI and the system executor are separate acceptance surfaces. An OPEN state proves only the lease/control state. It does not prove system execution is wired, authorized correctly, journaled, or fail-closed. See [Current status](CURRENT_STATUS.md).

## MCP lifecycle

The MCP server is client-facing and normally uses stdio. Watcher startup must not block the initialization handshake. A client-owned stdio process normally ends on EOF/shutdown; preserving a process across client disconnects requires an explicit ownership/lifecycle mechanism rather than merely increasing an idle timeout.

## Change discipline

1. Identify the exact owner and bounded behavior first.
2. Change canonical GitHub source rather than patching an unrelated dirty local checkout.
3. Preserve unrelated local modifications/untracked files.
4. Pull accepted GitHub changes locally with a safe fast-forward where possible.
5. Prove behavior at the level claimed.

Do not delete untracked/local artifacts merely because they are not part of the committed product. Classify ownership first.

## What not to infer

Do not equate file existence, tool registration, handler wiring, a passing test, an OPEN status string, a clean repository, or a mergeable PR with a working end-to-end feature. Each is evidence only for the boundary it actually observes.