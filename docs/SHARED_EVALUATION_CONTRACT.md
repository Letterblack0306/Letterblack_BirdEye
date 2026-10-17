# Shared Evaluation Contract v1 — proposal

**Scope:** portable result validation and aggregation. This PR does not install a runtime evaluator, register an MCP tool, grant execution authority, or assert system acceptance.

## Ownership

- Agent: reasons, decides which requirements and evals apply.
- Project runner: executes its existing tests/acceptance workflows.
- BirdEye: may discover and aggregate evaluation records, without independently deciding authorization.
- LBE: remains authoritative for identity, authorization and governed execution.
- Public Agents Wall: can consume this versioned JSON contract without depending on private BirdEye or LBE services.
- Brew: retains its single-reasoning-agent architecture and existing test/readiness/release commands.

## Minimal record

```json
{
  "schema_version": "1",
  "evaluation_id": "runtime.allow",
  "project_id": "birdeye",
  "requirement_id": "legitimate-allow",
  "status": "PASS",
  "source_revision": "observed-commit-sha",
  "runner": "project-owned-integration-runner",
  "evidence": [{"kind": "runtime_receipt", "reference": "receipt-id"}]
}
```

Status is exactly PASS, FAIL, BLOCKED or UNVERIFIED. PASS requires at least one evidence reference, but a reference alone does not prove a real effect. A consumer must independently establish provenance, runtime identity, authority, effect and receipt integrity before claiming end-to-end acceptance.

The aggregator marks absent required IDs UNVERIFIED and duplicate IDs FAIL instead of silently treating duplicates as successful. The digest supports deterministic comparison, **not** authenticity.

## Boundary conditions

1. No automatic execution, shell invocation, background monitoring, or MCP registration is added by this contract.
2. Results from another machine or revision must not be used as current-runtime acceptance.
3. Agents must not upgrade DOCUMENTED or IMPLEMENTED status to RUNTIME_PROVEN based only on this format.
4. A DENY test alone cannot establish execution correctness; legitimate ALLOW with real effect and receipt must pass.
5. There is no automatic compatibility promise until independent consumers test serialization and semantics.

## Follow-on PRs after runtime proof

- Inspect current local BirdEye service ownership, MCP dispatch and LBE boundary.
- Add read-only evaluation discovery and results surfaces *only if absent*.
- Wire an authorized runner via existing execution authority with request/effect/receipt correlation.
- Add cross-repository contract tests and a portable consumer adapter for Agents Wall.
- Integrate Brew's existing tests without changing its reasoning architecture.

Run contract tests: `python -m unittest discover -s tests -p test_evaluation_contract.py`.

No deployment, runtime acceptance or downstream integration is claimed by this PR.
