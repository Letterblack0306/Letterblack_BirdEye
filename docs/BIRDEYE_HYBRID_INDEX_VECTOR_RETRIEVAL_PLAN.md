# BirdEye Hybrid Index + Vector Retrieval Plan

> **Status: PLAN ONLY — NOT IMPLEMENTATION TRUTH**
>
> This document records a proposed retrieval architecture. It must not be treated as implemented, enabled, production-ready, or runtime-proven until the corresponding source, tests, and live MCP behavior are independently validated. Current BirdEye source/runtime evidence remains authoritative.

## Purpose

Define a clean retrieval architecture for BirdEye that keeps deterministic current-state indexing as the source of truth while adding vectors only where semantic retrieval provides clear value.

The core rule is:

```text
Vector = discovery
Canonical DB / index / SHA = truth
```

Vectors must never become a second authority for file identity, freshness, ownership, or provenance.

---

## 1. Canonical Ownership

```text
BirdEye
= canonical local filesystem identity, indexing, SHA-256, root/path scope,
  cached content, freshness/version status, and shared MCP retrieval surface

Memory
= historical conversations, agent sessions, runtime history, and durable memory

Skills
= curated specialized skill content and workflow guidance

Agents
= reasoning consumers of BirdEye MCP capabilities

Vector index
= optional semantic retrieval accelerator derived from canonical indexed content
```

BirdEye remains the canonical evidence layer even when vectors are used.

---

## 2. Data Categories

### 2.1 Workspace / Current Source

Workspace files change frequently.

Primary storage:

```text
SQLite / BirdEye index
+ root
+ path
+ physical_path
+ cached content
+ size
+ modified time
+ SHA-256
+ content_status
+ version_status
```

Recommended search model:

```text
exact path / symbol / error / filename
→ BirdEye indexed lexical search

semantic concept query
→ optional vector search
→ return candidate file IDs
→ BirdEye verifies exact path / SHA / version
```

Vectors are optional for workspace data.

They must be keyed by current SHA so changed files replace their previous vector representation rather than accumulating duplicates.

---

### 2.2 ChatGPT / OpenAI Conversation History

This is a strong vector candidate because imported historical conversations are mostly append-only.

Flow:

```text
canonical Memory conversation
→ preserve source / conversation / message identity
→ chunk meaningful content
→ generate embeddings once
→ store vectors
```

New history:

```text
new conversation/messages
→ append canonical records
→ vectorize only new content
```

Existing unchanged conversations do not need to be re-vectorized.

Typical semantic queries:

```text
"What did we decide about MCP ownership?"
"Find the discussion about skill routing."
"Where did we talk about BirdEye indexing?"
```

This is where vectors provide significantly better retrieval than exact keyword search alone.

---

### 2.3 Agent Runtime / Session Logs

Agent runtime history is also a strong vector candidate because completed runs are normally append-only.

Recommended metadata:

```text
agent
session_id
workspace_id
timestamp
event_type
source
SHA
```

Flow:

```text
completed/new runtime record
→ canonical Memory storage
→ vectorize meaningful chunks/events
→ preserve metadata filters
```

Do not repeatedly vectorize unchanged historical runs.

Use vectors for semantic discovery, then use the canonical Memory record for exact evidence.

---

### 2.4 Skills

Skills are an excellent vector candidate because they are:

- curated;
- relatively small;
- changed infrequently;
- often searched by meaning rather than exact title.

Flow:

```text
SKILL.md
→ canonical BirdEye/Skills identity
→ SHA-256
→ chunk
→ embedding
```

If the SHA is unchanged:

```text
reuse existing vectors
```

If the skill changes:

```text
new SHA
→ replace old vectors for that skill
→ generate new vectors
```

Typical query:

```text
"I need a workflow for runtime acceptance."
```

Vector retrieval may identify a skill even when the file uses different terminology such as:

```text
end-to-end proof
live validation
runtime evidence
```

---

## 3. Vector Update Model

Vectors should be treated as a derived cache, not append-only history.

Recommended key:

```text
root
path
file_sha256
chunk_id
embedding
```

### Same file, same SHA

```text
do nothing
reuse existing vectors
```

### Same file, new SHA

```text
detect new SHA
→ remove/replace old vectors for that file
→ chunk new indexed content
→ generate new embeddings
```

### Deleted file

```text
BirdEye reconciliation deletes canonical row
→ delete associated vectors
```

### New file

```text
BirdEye indexes file
→ SHA generated
→ cached content available
→ vectorize if that data category is vector-enabled
```

The vector database should therefore represent the current canonical state unless a separate historical-vector policy is explicitly enabled.

---

## 4. Avoiding Duplicate Processing

Do not create a second filesystem crawler for vectors.

Correct flow:

```text
filesystem
→ BirdEye index/cache
→ cached indexed content
→ vectorization job
```

Not:

```text
filesystem
→ BirdEye crawler

filesystem
→ second vector crawler
```

This avoids:

- duplicate filesystem scans;
- duplicate parsing;
- duplicate freshness logic;
- conflicting ownership;
- unnecessary I/O.

BirdEye should feed the vectorization layer from already indexed content.

---

## 5. Query Routing

### Exact / deterministic query

Examples:

```text
AgentExecutive
skills.activated
ProviderEvidencePanel.tsx
exact error message
specific path
specific root
specific SHA
```

Route:

```text
Agent
→ BirdEye indexed lexical/path search
→ exact result
```

### Semantic / conceptual query

Examples:

```text
"where is session recovery handled?"
"find code related to browser restart logic"
"which files describe MCP capability discovery?"
```

Route:

```text
Agent
→ vector semantic search
→ top-K candidate IDs
→ BirdEye
→ root/path/governance filtering
→ SHA/version/provenance verification
→ result
```

---

## 6. Recommended Priority

### Phase 1 — Keep current BirdEye indexing authoritative

Complete and stabilize:

```text
SQLite file index
SHA-256
cached content
root filtering
path_prefix filtering
content_status
version_status
verify_freshness
targeted refresh
```

### Phase 2 — Add vectors for low-change/high-semantic-value domains

Priority:

```text
1. Skills
2. ChatGPT / OpenAI history
3. Agent runtime/session history
```

These provide high semantic-search value with low update cost.

### Phase 3 — Optional workspace vectors

Only add workspace vectors if semantic search over large code/document corpora materially improves retrieval.

Workspace vectors remain derived and SHA-bound.

---

## 7. Recommended Architecture

```text
                         BirdEye
                canonical index / evidence
                         │
        ┌────────────────┼─────────────────┐
        │                │                 │
   Workspace          Memory            Skills
        │                │                 │
 SQLite/cache      canonical history    curated files
 SHA/current       append-oriented      SHA/version
        │                │                 │
 optional vector     vector index       vector index
        │                │                 │
        └──────────── semantic retrieval ──┘
                         │
                       Agent
```

---

## 8. Authority Rule

Never treat vector similarity as proof.

```text
vector result
= candidate / discovery evidence

BirdEye / Memory / Skills canonical record
= exact evidence / truth
```

A vector hit must resolve back to a canonical record before being used for consequential claims.

---

## 9. Storage / Processing Strategy

### Workspace

```text
Primary: BirdEye SQLite/cache
Vector: optional
Update model: replace on SHA change
```

### ChatGPT/OpenAI history

```text
Primary: Memory canonical history
Vector: recommended
Update model: append only new conversation/message content
```

### Agent runtime logs

```text
Primary: Memory canonical history
Vector: recommended
Update model: append only new completed/runtime records
```

### Skills

```text
Primary: Skills + BirdEye indexed identity
Vector: recommended
Update model: regenerate only when skill SHA changes
```

---

## 10. Validation Gate Before Promotion

This document must remain classified as **PLAN ONLY** until all applicable implementation claims are proven through current BirdEye evidence.

Minimum promotion gate:

```text
source implementation present
→ focused tests pass
→ migration/update behavior validated
→ vector records are SHA-bound
→ unchanged SHA does not re-vectorize
→ changed SHA replaces stale vectors
→ deleted canonical records remove derived vectors
→ query routing distinguishes exact vs semantic retrieval
→ vector hits resolve back to canonical BirdEye/Memory/Skills records
→ live MCP retrieval path proven
```

Until that gate is satisfied, do not describe vector retrieval as implemented, enabled, complete, or production-ready.

---

## 11. Final Planned Direction

```text
BirdEye index/cache
= mandatory canonical layer

Vector retrieval
= optional semantic layer

Memory/history
= strong vector candidate

Skills
= strong vector candidate

Workspace
= hybrid, vectors only when semantic retrieval is useful
```

The intended result is a hybrid system that preserves deterministic evidence and freshness while adding fast semantic discovery without duplicating authority or repeatedly processing unchanged data.
