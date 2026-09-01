# EYES Replay / Projection Contract

> **Status: frozen contract.** This file is the normative definition of EYES
> authority and projection/replay semantics. Code in `eye_database.py`,
> `eye_query.py`, and `birdeye_watcher.py` must conform to it. Treat older or
> looser descriptions elsewhere as historical, not authoritative.

## 1. Authority model

```text
source files              = canonical content authority
eye_*_data_01.db          = canonical metadata + mutation/change ledger
eye_*_query_01.db         = disposable, replayable query projection
state\workspace.db        = temporary compatibility authority/path ONLY
                            until the retirement gate passes
```

- Source files are never copied or moved. Every derived record stores identity
  metadata and a SHA-256 reference to the source; the source remains canonical.
- `eye_*_data_01.db` is the durable, append-safe ledger. Mutations in
  `record_file_event()` run in one `BEGIN IMMEDIATE` transaction that advances
  `meta.canonical_generation`, upserts the `files` row (recording
  `files.last_generation`), and appends one `changes` row. `changes.generation`
  is unique, so the ledger is strictly monotonic.
- `eye_*_query_01.db` is a projection: it is disposable and rebuildable. It is
  NEVER written as an independent authority. There is no two-database atomic
  commit; projection has eventual consistency with a measurable lag.

## 2. Generation semantics

```text
canonical_generation  = authoritative generation, owned by the data DB (meta)
applied_generation    = highest generation FULLY reflected in the query DB (meta)
lag                   = canonical_generation - applied_generation
```

- `canonical_generation` is monotonically increasing and unique-committed on the
  data side. It is the only authority for "what changed and in what order."
- `applied_generation` lives in the QUERY DB (`meta.applied_generation`). It
  records how far the projection has converged toward canonical truth.
- The projector is the ONLY writer that advances `applied_generation`. Nothing
  else may touch it.
- `lag == 0` means the projection is fully converged. `lag > 0` is expected and
  is surfaced as measurable, health-reportable divergence — never silently
  absorbed.

## 3. Replay semantics (the projector)

```text
projector reads:  changes WHERE generation > applied_generation
                  ORDER BY generation
applies each change idempotently to the query projection
advances applied_generation to canonical_generation
  IN THE SAME query-DB transaction as the projection mutations
```

Rules that cannot be relaxed:

1. **Ledger-driven.** The projector is driven by the canonical `changes` ledger.
   It never scans source roots by itself to decide what changed.
2. **Ordered.** Pending changes are processed in ascending `generation`.
3. **Idempotent.** Applying any change, or any subset, leaves the projection in
   a state determined solely by the latest change for each key. Reapplying a
   generation that was already applied is a no-op.
4. **Same-transaction watermark.** Query projection mutations and the
   `applied_generation` advancement are committed together in one query-DB
   transaction. The watermark never advances "half-applied."
5. **Watermark after success.** `applied_generation` advances to
   `canonical_generation` only after every pending projection change in that
   batch is applied.
6. **Crash safety.**
   - Crash before the query commit  → `applied_generation` is unchanged → the
     same generations are replayed. Safe.
   - Crash after the query commit    → `applied_generation` already advanced →
     no duplicate semantic effect on replay. Safe.

## 4. Deterministic rebuild (separate from replay)

- **Replay** = incremental convergence from `applied_generation` toward
  `canonical_generation`. Used by the live projector.
- **Rebuild** = a separate operation that drops an empty `eye_*_query_01.db`
  and reconstructs it from the EYES canonical data ledger + source files ONLY,
  with no dependency on `state\workspace.db` or any legacy store.
- The two operations must remain distinct concepts; do not merge them. Keeping
  them separate is what makes the retirement gate provable.

## 5. Projection key mapping

The projection `files` table keys on `(root, path)`. Given a canonical
`changes`/`files` row keyed on `(source_id, relative_path)`:

```text
projection root = safe_root_name(source_id)            # same fn as agent.Context
projection path = safe_root_name(source_id)            # when relative == "."
                = f"{safe_root_name(source_id)}/{relative_path-as-posix}"
```

`safe_root_name` is `agent.safe_root_name` — the single deterministic name
sanitizer shared with runtime root construction. The projector must use it (or
an identical pure function) so projection keys always match the runtime watcher.
For deleted files, apply the same mapping and delete the projection row.

## 6. `journal_replayable` gate

```text
journal_replayable == PASS  means an interrupted projector can resume from
applied_generation and converge to canonical_generation without legacy
assistance or manual repair.
```

Concretely, `journal_replayable == PASS` requires, at minimum:

- `applied_generation` persists in the query DB and survives restart.
- Replaying pending generations converges the projection to canonical truth
  (`lag -> 0`).
- A crash at any point (before/after query commit) is recoverable by simply
  running the projector again.
- The same success is provable on a deterministic rebuild (empty query DB,
  EYES-only).
