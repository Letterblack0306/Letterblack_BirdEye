"""EYES domain-separated query database paths and migration helpers."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
EYE_DATABASE_DIR = ROOT / "eye_Databa"


def path(domain: str, kind: str = "query", number: int = 1) -> Path:
    if domain not in {"workspace", "memory", "skills"}:
        raise ValueError(f"invalid EYES domain: {domain}")
    if kind not in {"data", "query"}:
        raise ValueError(f"invalid EYES database kind: {kind}")
    return EYE_DATABASE_DIR / f"eye_{domain}_{kind}_{number:02d}.db"


def backup_database(source: Path, destination: Path) -> None:
    """Copy a SQLite database consistently, including committed WAL content."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    source_conn = sqlite3.connect(str(source), uri=False)
    try:
        destination_conn = sqlite3.connect(str(destination), uri=False)
        try:
            source_conn.backup(destination_conn)
        finally:
            destination_conn.close()
    finally:
        source_conn.close()


def backup_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def connect_query(domain: str, number: int = 1) -> sqlite3.Connection:
    """Open a domain query database using SQLite WAL and full sync."""
    target = path(domain, "query", number)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def initialize_workspace_query(conn: sqlite3.Connection) -> None:
    """Create the workspace query schema used by search_workspace()."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            root TEXT NOT NULL,
            path TEXT NOT NULL,
            physical_path TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            sha256 TEXT,
            content TEXT,
            hash_status TEXT NOT NULL,
            error TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_seen_run TEXT NOT NULL,
            extension TEXT,
            file_type TEXT,
            line_count INTEGER,
            token_count INTEGER,
            feature_terms TEXT,
            PRIMARY KEY(root, path)
        );
        CREATE INDEX IF NOT EXISTS idx_workspace_query_sha256 ON files(sha256);
        CREATE INDEX IF NOT EXISTS idx_workspace_query_path ON files(path);
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
    for name, definition in (("extension", "TEXT"), ("file_type", "TEXT"), ("line_count", "INTEGER"), ("token_count", "INTEGER"), ("feature_terms", "TEXT")):
        if name not in columns:
            conn.execute(f"ALTER TABLE files ADD COLUMN {name} {definition}")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('applied_generation','0')"
    )
    conn.commit()


def initialize_vector_query(conn: sqlite3.Connection) -> None:
    """Create the isolated Memory/Skills vector query schema."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS idf(feature TEXT PRIMARY KEY, idf REAL);
        CREATE TABLE IF NOT EXISTS embeddings(
            namespace TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            source_path TEXT,
            title TEXT,
            role TEXT,
            content_type TEXT,
            content_text TEXT,
            create_time_iso TEXT,
            is_reasoning INTEGER DEFAULT 0,
            source_shard TEXT,
            content_hash TEXT,
            vec BLOB NOT NULL,
            PRIMARY KEY(namespace, conversation_id, node_id)
        );
        CREATE TABLE IF NOT EXISTS skill_files(
            namespace TEXT NOT NULL DEFAULT 'skills',
            rel_path TEXT NOT NULL,
            size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            PRIMARY KEY(namespace, rel_path)
        );
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('applied_generation','0')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('projection_contract','UNPROVEN')"
    )
    conn.commit()


def _memory_generation(number: int = 1) -> int:
    return _read_canonical_generation("memory", number)


def _memory_canonical_db() -> Path:
    return Path(__import__("os").environ.get("MEMORY_CANONICAL_DB", str(Path(r"C:\MCP Local\Memory\memory.db")))).resolve()


def _rebuild_memory_namespace(query_conn: sqlite3.Connection) -> int:
    """Rebuild Memory embeddings from the canonical Memory store in query_conn."""
    import sys
    memory_root = Path(r"C:\MCP Local\Memory")
    source_path = str(memory_root / "src")
    if source_path not in sys.path:
        sys.path.insert(0, source_path)
    from memory.storage import CanonicalStore
    from memory.vector import DIM, SemanticIndex, _to_vector, features

    store = CanonicalStore(_memory_canonical_db())
    try:
        store.ensure_ready()
        rows = store._conn.execute(
            "SELECT conversation_id,node_id,content_text,is_reasoning,source_shard FROM messages "
            "ORDER BY conversation_id,node_id"
        ).fetchall()
        docs = []
        vocabulary: set[str] = set()
        for row in rows:
            is_reasoning = int(row["is_reasoning"] or 0)
            text = row["content_text"] or ""
            feats = features(text)
            if not feats:
                continue
            vocabulary.update(feats)
            docs.append((row["conversation_id"], row["node_id"], text, feats, is_reasoning, row["source_shard"]))

        query_conn.execute("DELETE FROM embeddings WHERE namespace='memory'")
        query_conn.execute("DELETE FROM idf")
        query_conn.execute("DELETE FROM meta WHERE key IN ('dim','n_docs','include_reasoning')")
        query_conn.executemany(
            "INSERT OR IGNORE INTO idf(feature,idf) VALUES(?,?)",
            [(feature, 1.0) for feature in sorted(vocabulary)],
        )
        query_conn.executemany(
            "INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)",
            [("dim", str(DIM)), ("n_docs", str(len(docs))), ("include_reasoning", "True")],
        )
        for conversation_id, node_id, text, feats, is_reasoning, source_shard in docs:
            content_hash = SemanticIndex._content_hash(conversation_id, node_id, text, is_reasoning)
            vec = _to_vector(feats, {}, DIM)
            query_conn.execute(
                "INSERT OR REPLACE INTO embeddings(namespace,conversation_id,node_id,content_text,"
                "is_reasoning,source_shard,content_hash,vec) VALUES(?,?,?,?,?,?,?,?)",
                ("memory", conversation_id, node_id, text, is_reasoning, source_shard, content_hash, vec.tobytes()),
            )
        return len(docs)
    finally:
        store.close()


def read_applied_generation(conn: sqlite3.Connection) -> int:
    """Return the highest generation fully reflected in the query projection."""
    row = conn.execute("SELECT value FROM meta WHERE key='applied_generation'").fetchone()
    return int(row[0]) if row else 0


def _query_text_features(content: str, extension: str) -> tuple[str, int, int, str]:
    """Derive projection feature columns from content (mirrors watcher behavior)."""
    file_type = {
        ".py": "python",
        ".json": "json",
        ".md": "markdown",
        ".ps1": "powershell",
        ".js": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript-react",
        ".jsx": "javascript-react",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(extension, extension.lstrip(".") or "file")
    line_count = content.count("\n") + (1 if content else 0)
    terms = sorted({term.lower() for term in re.findall(r"[A-Za-z_][A-Za-z0-9_/-]{2,}", content)})
    return file_type, line_count, len(terms), json.dumps(terms[:500], ensure_ascii=False)
def project_pending_changes(domain: str, number: int = 1) -> dict[str, Any]:
    """Replay canonical changes into the query projection and advance the watermark.

    Contract: ``docs/EYES_REPLAY_PROJECTION_CONTRACT.md``

    Reads ``changes WHERE generation > applied_generation ORDER BY generation``
    from the canonical data DB, applies each change idempotently to the query
    ``files`` projection, then advances ``meta.applied_generation`` to
    ``canonical_generation`` in the SAME query-DB transaction.

    Replaying is safe to repeat: re-walks of already-applied generations are
    no-ops (upserts reflect current canonical state; deletes are idempotent),
    and the watermark advances only when that generation batch is committed.
    """
    if domain not in {"workspace", "memory", "skills"}:
        raise ValueError(f"invalid EYES domain: {domain}")
    if domain == "memory":
        data_path = path(domain, "data", number)
        query_conn = connect_query(domain, number)
        try:
            initialize_vector_query(query_conn)
            canonical = _memory_generation(number)
            applied = read_applied_generation(query_conn)
            if canonical <= applied:
                return {
                    "ok": True, "domain": domain,
                    "canonical_generation": canonical,
                    "applied_generation": applied,
                    "lag": canonical - applied, "replayed": 0,
                }
            pending = query_conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='embeddings'"
            ).fetchone()[0]
            document_count = _rebuild_memory_namespace(query_conn)
            query_conn.execute(
                "INSERT INTO meta(key,value) VALUES('applied_generation',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(canonical),),
            )
            query_conn.execute(
                "INSERT INTO meta(key,value) VALUES('projection_contract','PASS') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            query_conn.commit()
            return {
                "ok": True, "domain": domain,
                "canonical_generation": canonical,
                "applied_generation": canonical,
                "lag": 0, "replayed": 1,
                "documents": document_count,
            }
        finally:
            query_conn.close()
    data_path = path(domain, "data", number)
    if not data_path.exists():
        return {
            "ok": True,
            "domain": domain,
            "canonical_generation": 0,
            "applied_generation": 0,
            "lag": 0,
            "replayed": 0,
        }
    data_conn = sqlite3.connect(str(data_path), timeout=60)
    data_conn.row_factory = sqlite3.Row
    query_conn = connect_query(domain, number)
    try:
        initialize_workspace_query(query_conn)
        canonical_row = data_conn.execute(
            "SELECT value FROM meta WHERE key='canonical_generation'"
        ).fetchone()
        canonical = int(canonical_row[0]) if canonical_row else 0
        applied = read_applied_generation(query_conn)
        if canonical <= applied:
            return {
                "ok": True,
                "domain": domain,
                "canonical_generation": canonical,
                "applied_generation": applied,
                "lag": canonical - applied,
                "replayed": 0,
            }

        # Ledger-driven and ordered. Coalesce per key to the latest generation
        # so replay is idempotent: the projected state is determined solely by
        # each key's most recent change.
        changes = data_conn.execute(
            "SELECT generation, source_id, relative_path, event "
            "FROM changes WHERE generation > ? AND generation <= ? "
            "ORDER BY generation",
            (applied, canonical),
        ).fetchall()
        coalesced: dict[tuple[str, str], sqlite3.Row] = {}
        for row in changes:
            coalesced[(row["source_id"], row["relative_path"])] = row

        from agent import safe_root_name, utc_now  # lazy: avoid import cycle
        now = utc_now()
        replayed = 0
        for (source_id, relative), row in coalesced.items():
            root_name = safe_root_name(source_id)
            rel_posix = relative.replace("\\", "/")
            proj_path = root_name if rel_posix == "." else f"{root_name}/{rel_posix}"
            if row["event"] == "deleted":
                query_conn.execute(
                    "DELETE FROM files WHERE root=? AND path=?", (root_name, proj_path)
                )
                replayed += 1
                continue
            file_row = data_conn.execute(
                "SELECT physical_path FROM files "
                "WHERE domain=? AND source_id=? AND relative_path=?",
                (domain, source_id, relative),
            ).fetchone()
            physical = Path(file_row["physical_path"]) if file_row else None
            if physical is None or not physical.is_file():
                query_conn.execute(
                    "DELETE FROM files WHERE root=? AND path=?", (root_name, proj_path)
                )
                replayed += 1
                continue
            stat = physical.stat()
            raw = physical.read_bytes()
            content = raw.decode("utf-8", errors="ignore")
            digest = hashlib.sha256(raw).hexdigest()
            extension = physical.suffix.lower()
            file_type, line_count, token_count, feature_terms = _query_text_features(content, extension)
            existing = query_conn.execute(
                "SELECT first_seen_at FROM files WHERE root=? AND path=?",
                (root_name, proj_path),
            ).fetchone()
            first_seen = existing[0] if existing else now
            query_conn.execute(
                "INSERT INTO files(root,path,physical_path,size,modified_ns,sha256,content,"
                "hash_status,error,first_seen_at,last_seen_at,last_seen_run,extension,file_type,"
                "line_count,token_count,feature_terms) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(root,path) DO UPDATE SET "
                "physical_path=excluded.physical_path,size=excluded.size,modified_ns=excluded.modified_ns,"
                "sha256=excluded.sha256,content=excluded.content,hash_status=excluded.hash_status,"
                "error=excluded.error,first_seen_at=excluded.first_seen_at,last_seen_at=excluded.last_seen_at,"
                "last_seen_run=excluded.last_seen_run,extension=excluded.extension,file_type=excluded.file_type,"
                "line_count=excluded.line_count,token_count=excluded.token_count,feature_terms=excluded.feature_terms",
                (
                    root_name, proj_path, str(physical), stat.st_size, stat.st_mtime_ns,
                    digest, content, "hashed", None, first_seen, now, f"replay:{canonical}",
                    extension, file_type, line_count, token_count, feature_terms,
                ),
            )
            replayed += 1

        # Watermark advances only after all pending changes are applied, in the
        # same query transaction as the projection mutations.
        query_conn.execute(
            "INSERT INTO meta(key,value) VALUES('applied_generation',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(canonical),),
        )
        query_conn.commit()
        return {
            "ok": True,
            "domain": domain,
            "canonical_generation": canonical,
            "applied_generation": canonical,
            "lag": 0,
            "replayed": replayed,
        }
    finally:
        data_conn.close()
        query_conn.close()


_WORKSPACE_STYLE_DOMAINS = ("workspace", "skills")


def _read_canonical_generation(domain: str, number: int = 1) -> int:
    data_path = path(domain, "data", number)
    if not data_path.exists():
        return 0
    conn = sqlite3.connect(str(data_path), timeout=60)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='canonical_generation'").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _read_applied_generation(domain: str, number: int = 1) -> int:
    query_path = path(domain, "query", number)
    if not query_path.exists():
        return 0
    conn = sqlite3.connect(str(query_path), timeout=60)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='applied_generation'").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _query_projection_contract(domain: str, number: int = 1) -> str:
    query_path = path(domain, "query", number)
    if not query_path.exists():
        return "UNPROVEN"
    conn = sqlite3.connect(str(query_path), timeout=60)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='projection_contract'").fetchone()
        return str(row[0]) if row else "UNPROVEN"
    finally:
        conn.close()
def rebuild_query_projection(domain: str, number: int = 1) -> dict[str, Any]:
    """Deterministically rebuild a workspace-style query projection from EYES only.

    Contract: ``docs/EYES_REPLAY_PROJECTION_CONTRACT.md`` section 4.

    Drops the projection rows and resets ``applied_generation`` to 0, then
    replays the entire canonical ledger so the projection converges to
    ``canonical_generation`` (lag 0). Reads ONLY ``eye_<domain>_data_01.db``
    plus source files; it never depends on legacy ``state\\workspace.db``.

    Distinct from replay: replay is incremental; rebuild starts fresh.
    """
    if domain == "memory":
        canonical = _memory_generation(number)
        query_conn = connect_query(domain, number)
        try:
            initialize_vector_query(query_conn)
            query_conn.execute("BEGIN IMMEDIATE")
            document_count = _rebuild_memory_namespace(query_conn)
            query_conn.execute(
                "INSERT INTO meta(key,value) VALUES('applied_generation',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(canonical),),
            )
            query_conn.execute(
                "INSERT INTO meta(key,value) VALUES('projection_contract','PASS') "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
            )
            query_conn.commit()
            return {
                "ok": True,
                "domain": domain,
                "canonical_generation": canonical,
                "applied_generation": canonical,
                "lag": 0,
                "replayed": 1,
                "documents": document_count,
                "rebuild": True,
                "source": "EYES canonical Memory store only (no legacy dependency)",
            }
        except Exception:
            query_conn.rollback()
            raise
        finally:
            query_conn.close()
    if domain not in _WORKSPACE_STYLE_DOMAINS:
        return {"ok": False, "domain": domain, "error": f"unsupported EYES domain: {domain}"}
    data_path = path(domain, "data", number)
    if not data_path.exists():
        return {"ok": False, "domain": domain, "error": f"missing canonical data DB: {data_path}"}
    query_conn = connect_query(domain, number)
    try:
        initialize_workspace_query(query_conn)
        query_conn.execute("DELETE FROM files")
        query_conn.execute(
            "INSERT INTO meta(key,value) VALUES('applied_generation','0') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
        query_conn.commit()
    finally:
        query_conn.close()
    result = project_pending_changes(domain, number)
    result["rebuild"] = True
    result["source"] = "EYES canonical data/files only (no legacy dependency)"
    return result
def journal_replayable(domain: str, number: int = 1) -> dict[str, Any]:
    """Return whether an interrupted projector can resume and converge to canonical.

    Contract: ``docs/EYES_REPLAY_PROJECTION_CONTRACT.md`` section 6.

    Performs the actual replay over the canonical ledger (read-only w.r.t.
    legacy); the projection is replayable when the run completes and converges
    to ``lag == 0`` using only EYES canonical data/source.
    """
    if domain == "memory":
        try:
            result = project_pending_changes(domain, number)
        except Exception as exc:  # noqa: BLE001 - report replay failures for the gate
            return {"ok": False, "domain": domain, "journal_replayable": False, "reason": f"{type(exc).__name__}: {exc}"}
        query_conn = connect_query(domain, number)
        try:
            initialize_vector_query(query_conn)
            contract = query_conn.execute(
                "SELECT value FROM meta WHERE key='projection_contract'"
            ).fetchone()
            contract_pass = bool(contract and contract[0] == "PASS")
        finally:
            query_conn.close()
        replayable = bool(result.get("ok")) and int(result.get("lag", -1)) == 0 and contract_pass
        return {
            "ok": True,
            "domain": domain,
            "journal_replayable": replayable,
            "canonical_generation": result.get("canonical_generation", 0),
            "applied_generation": result.get("applied_generation", 0),
            "lag": result.get("lag", -1),
            "replayed": result.get("replayed", 0),
            "projection_contract": "PASS" if contract_pass else "UNPROVEN",
        }
    if domain not in _WORKSPACE_STYLE_DOMAINS:
        return {"ok": False, "domain": domain, "journal_replayable": False, "reason": "unsupported EYES domain"}
    data_path = path(domain, "data", number)
    if not data_path.exists():
        return {"ok": False, "domain": domain, "journal_replayable": False, "reason": "no canonical data DB"}
    try:
        result = project_pending_changes(domain, number)
    except Exception as exc:  # noqa: BLE001 - report any replay failure for the gate
        return {"ok": False, "domain": domain, "journal_replayable": False, "reason": f"{type(exc).__name__}: {exc}"}
    replayable = bool(result.get("ok")) and int(result.get("lag", -1)) == 0
    return {
        "ok": True,
        "domain": domain,
        "journal_replayable": replayable,
        "canonical_generation": result.get("canonical_generation", 0),
        "applied_generation": result.get("applied_generation", 0),
        "lag": result.get("lag", -1),
        "replayed": result.get("replayed", 0),
    }


def health() -> dict[str, Any]:
    """Unified EYES divergence / replayability health across all domains.

    Reads generation metadata from the canonical data DBs and query projections
    without creating or rebuilding any database. ``generation_lag_zero`` is true
    only when EVERY domain reports ``lag == 0``.
    """
    domains = ("workspace", "memory", "skills")
    report: dict[str, Any] = {}
    for domain in domains:
        canonical = _read_canonical_generation(domain)
        applied = _read_applied_generation(domain)
        lag = canonical - applied
        report[domain] = {
            "canonical_generation": canonical,
            "applied_generation": applied,
            "lag": max(lag, 0),
            "journal_replayable": bool(
                lag == 0
                and path(domain, "data").exists()
                and (
                    domain != "memory"
                    or _query_projection_contract(domain) == "PASS"
                )
            ),
            "rebuildable": True,
            "projection_owner": "Memory deterministic vector projector" if domain == "memory" else "EYES query projector",
            "reason": None if lag == 0 else "projection is behind canonical generation",
        }
    return {
        "ok": True,
        "domains": report,
        "generation_lag_zero": all(entry.get("lag", 0) == 0 for entry in report.values()),
    }