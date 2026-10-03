# Letterblack BirdEye

BirdEye is Letterblack's local evidence and workspace gateway for agents. It gives clients one bounded interface for discovering configured workspaces, inspecting current indexed state, retrieving historical context, resolving skills/knowledge, and performing governed workspace-scoped execution.

BirdEye is **not** the reasoning agent and its memory is **not** authoritative truth. The calling agent owns reasoning. BirdEye supplies evidence, capabilities, policy boundaries, execution surfaces, and receipts.

## Start here

The normal runtime is the MCP server:

```text
python mcp_server.py --stdio
```

Clients should use the configured Python interpreter for the installation rather than assuming a global `python` command exists.

For a new maintainer or agent, read these documents in order:

1. [Repository guide](docs/REPOSITORY_GUIDE.md) — architecture, evidence hierarchy, important files, and operating rules.
2. [Current status](docs/CURRENT_STATUS.md) — what is proven, implemented, historical, or still unverified.
3. [BirdEye watch](BIRDEYE_WATCH.md) — root registry, watcher, indexing, hashes, and receipts.
4. [Eyes replay/projection contract](docs/EYES_REPLAY_PROJECTION_CONTRACT.md) — replay/projection semantics.
5. [Hybrid retrieval plan](docs/BIRDEYE_HYBRID_INDEX_VECTOR_RETRIEVAL_PLAN.md) — planned retrieval evolution.

## Core model

```text
Agent / MCP client
        |
        v
BirdEye MCP
        |
        +--> configured workspace roots / current source evidence
        +--> workspace identity + indexed file metadata / hashes
        +--> memory and prior actions (historical context)
        +--> GPT-K / skills (reference and procedure)
        +--> governed workspace execution
        +--> execution evidence / receipts
```

Evidence precedence is intentionally conservative:

```text
live runtime evidence
> current workspace/source
> repository contracts
> current indexed evidence
> GPT-K/reference material
> memory/history
```

Historical material can explain prior decisions; it must not silently override current source or runtime observations.

## Execution boundaries

`workspace_run` and `workspace_run_sequence` are the normal governed execution surfaces. Workspace confinement and policy remain in force.

A separate **opt-in system-access tunnel** is being developed for operations that genuinely need to cross configured workspace boundaries. It is fail-closed and user-controlled. The user-facing contract is:

```text
birdeye access open
birdeye access status
birdeye access close
```

Do not infer that the system executor is complete merely because the access-state CLI works. See [Current status](docs/CURRENT_STATUS.md).

## Evidence language

Use explicit evidence classifications instead of treating a passing check as proof of a feature:

- **RUNTIME_PROVEN** — behavior observed through the relevant runtime surface.
- **PROVEN** — directly established by authoritative evidence.
- **IMPLEMENTED** — source exists, but runtime behavior may still need proof.
- **DOCUMENTED** — contract/design is written, not necessarily implemented.
- **INFERRED** — indirect conclusion requiring stronger proof.
- **UNVERIFIED** — required evidence has not been obtained.
- **STALE** — evidence no longer represents current state.
- **BLOCKED** — verification cannot proceed until a stated blocker is resolved.

A green test, file presence, registration, or wiring alone does not prove end-to-end behavior.

## Repository

Canonical GitHub repository: `Letterblack0306/Letterblack_BirdEye`. Repository source is authoritative for committed implementation. Local databases, leases, logs, and generated evidence are machine state and should not be committed merely to make the repository describe a live machine.