"""One-time, reversible migration from the existing BirdEye indexes to EYES.

The script copies SQLite records; it does not scan or rehash source files.
Original databases are never modified. Run only after the MCP host is stopped.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "eye_Databa"
STATE = ROOT / "state"
BACKUP = ROOT / "archive" / "migration-backup-20260901"


def copy_db(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    src = sqlite3.connect(str(source))
    dst = sqlite3.connect(str(target))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    return conn


def make_data_db(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS files(
      domain TEXT NOT NULL, source_id TEXT NOT NULL, agent TEXT,
      relative_path TEXT NOT NULL, physical_path TEXT NOT NULL,
      size INTEGER NOT NULL, modified_ns INTEGER NOT NULL, sha256 TEXT,
      hash_status TEXT NOT NULL, error TEXT, first_seen_at TEXT NOT NULL,
      last_seen_at TEXT NOT NULL, last_event TEXT NOT NULL,
      PRIMARY KEY(domain, source_id, relative_path));
    CREATE TABLE IF NOT EXISTS changes(
      id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL,
      source_id TEXT NOT NULL, agent TEXT, relative_path TEXT NOT NULL,
      event TEXT NOT NULL, old_sha256 TEXT, new_sha256 TEXT,
      observed_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    return conn


def configured_domains() -> tuple[set[str], set[str], set[str]]:
    """Return current EYES workspace, memory-source, and skills identifiers."""
    def read(name: str) -> dict:
        return json.loads((ROOT / name).read_text(encoding="utf-8-sig"))
    workspace = {str(item["id"]) for item in read("eye_workspace.json")["roots"]}
    memory = {str(item["id"]) for item in read("eye_memory.json")["root"]["sources"]}
    skills = {str(read("eye_skills.json")["root"]["id"])}
    return workspace, memory, skills


def migrate() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    sources = {
        "workspace": BACKUP / "workspace.db",
        "memory": BACKUP / "agent_data_index.db",
        "skills": BACKUP / "semantic.db",
    }
    for source in sources.values():
        if not source.is_file():
            raise FileNotFoundError(source)

    workspace_ids, memory_ids, skills_ids = configured_domains()

    # Workspace query keeps the existing files schema, but only current EYES
    # workspace roots are copied. Legacy/memory/skills roots are not mixed in.
    workspace_query = OUT / "eye_workspace_query_01.db"
    old_workspace = sqlite3.connect(str(sources["workspace"]))
    old_workspace.row_factory = sqlite3.Row
    workspace_rows = old_workspace.execute(
        "SELECT root,path,physical_path,size,modified_ns,sha256,hash_status,error,first_seen_at,last_seen_at,last_seen_run,content FROM files WHERE root IN (%s)"
        % ",".join("?" for _ in workspace_ids), sorted(workspace_ids)
    ).fetchall()
    old_workspace.close()
    workspace_conn = connect(workspace_query)
    workspace_conn.executescript("""
        CREATE TABLE IF NOT EXISTS files(
          root TEXT NOT NULL,path TEXT NOT NULL,physical_path TEXT NOT NULL,
          size INTEGER NOT NULL,modified_ns INTEGER NOT NULL,sha256 TEXT,
          content TEXT,hash_status TEXT NOT NULL,error TEXT,
          first_seen_at TEXT NOT NULL,last_seen_at TEXT NOT NULL,last_seen_run TEXT NOT NULL,
          PRIMARY KEY(root,path));
    """)
    workspace_conn.executemany(
        """INSERT OR REPLACE INTO files(
             root,path,physical_path,size,modified_ns,sha256,content,
             hash_status,error,first_seen_at,last_seen_at,last_seen_run
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (row["root"], row["path"], row["physical_path"], row["size"],
             row["modified_ns"], row["sha256"], row["content"],
             row["hash_status"], row["error"], row["first_seen_at"],
             row["last_seen_at"], row["last_seen_run"])
            for row in workspace_rows
        ],
    )
    workspace_conn.commit(); workspace_conn.close()

    # Split the existing shared semantic index by namespace.
    old_semantic = sqlite3.connect(str(sources["skills"]))
    memory_query = connect(OUT / "eye_memory_query_01.db")
    skills_query = connect(OUT / "eye_skills_query_01.db")
    try:
        for conn in (memory_query, skills_query):
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS idf(feature TEXT PRIMARY KEY, idf REAL);
            CREATE TABLE IF NOT EXISTS embeddings(
              namespace TEXT NOT NULL, conversation_id TEXT NOT NULL,
              node_id TEXT NOT NULL, source_path TEXT, title TEXT,
              role TEXT, content_type TEXT, content_text TEXT,
              create_time_iso TEXT, is_reasoning INTEGER DEFAULT 0,
              source_shard TEXT, content_hash TEXT, vec BLOB NOT NULL,
              PRIMARY KEY(namespace, conversation_id, node_id));
            CREATE TABLE IF NOT EXISTS skill_files(
              namespace TEXT NOT NULL DEFAULT 'skills',
              rel_path TEXT NOT NULL,
              size INTEGER NOT NULL,
              mtime_ns INTEGER NOT NULL,
              sha256 TEXT NOT NULL,
              indexed_at TEXT NOT NULL,
              PRIMARY KEY(namespace, rel_path));
            """)
        rows = old_semantic.execute("SELECT key,value FROM meta").fetchall()
        memory_query.executemany("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", rows)
        memory_query.executemany("INSERT OR REPLACE INTO idf(feature,idf) VALUES(?,?)", old_semantic.execute("SELECT feature,idf FROM idf").fetchall())
        columns = [r[1] for r in old_semantic.execute("PRAGMA table_info(embeddings)")]
        fields = ",".join(columns)
        data = old_semantic.execute(f"SELECT {fields} FROM embeddings WHERE namespace='memory'").fetchall()
        if data:
            marks = ",".join("?" for _ in columns)
            memory_query.executemany(f"INSERT OR REPLACE INTO embeddings({fields}) VALUES({marks})", data)
        skill_rows = old_semantic.execute("SELECT namespace,rel_path,size,mtime_ns,sha256,indexed_at FROM skill_files WHERE namespace='skills'").fetchall()
        if skill_rows:
            skills_query.executemany("INSERT OR REPLACE INTO skill_files(namespace,rel_path,size,mtime_ns,sha256,indexed_at) VALUES(?,?,?,?,?,?)", skill_rows)
        memory_query.commit(); skills_query.commit()
    finally:
        old_semantic.close(); memory_query.close(); skills_query.close()

    # Build domain data ledgers from existing metadata indexes.
    now = datetime.now(timezone.utc).isoformat()
    for domain in ("workspace", "memory", "skills"):
        conn = make_data_db(OUT / f"eye_{domain}_data_01.db")
        try:
            if domain == "workspace":
                old = sqlite3.connect(str(sources[domain])); old.row_factory = sqlite3.Row
                rows = old.execute("SELECT root,path,physical_path,size,modified_ns,sha256,hash_status,error,first_seen_at,last_seen_at,content FROM files WHERE root IN (%s)" % ",".join("?" for _ in workspace_ids), sorted(workspace_ids)).fetchall()
                conn.executemany("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                    (domain, r[0], None, r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], r[9], "migrated") for r in rows])
                old.close()
            elif domain == "memory":
                old = sqlite3.connect(str(sources[domain])); old.row_factory = sqlite3.Row
                rows = old.execute("SELECT agent,source_id,rel_path,size,mtime_ns,sha256,hashed_at FROM agent_files WHERE source_id IN (%s)" % ",".join("?" for _ in memory_ids), sorted(memory_ids)).fetchall()
                conn.executemany("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                    (domain, r[1], r[0], r[2], r[2], r[3], r[4], r[5], "hashed", None, r[6], r[6], "migrated") for r in rows])
                old.close()
            else:
                old = sqlite3.connect(str(sources[domain])); old.row_factory = sqlite3.Row
                rows = old.execute("SELECT rel_path,size,mtime_ns,sha256,indexed_at FROM skill_files WHERE namespace='skills'").fetchall()
                conn.executemany("INSERT OR REPLACE INTO files VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", [
                    (domain, "skills", None, r[0], r[0], r[1], r[2], r[3], "hashed", None, r[4], r[4], "migrated") for r in rows])
                old.close()
            conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('migrated_at',?)", (now,))
            conn.commit()
        finally:
            conn.close()

    manifest = {
        "created_at": now,
        "source_databases": {k: str(v) for k, v in sources.items()},
        "output_databases": [str(p) for p in sorted(OUT.glob("*.db"))],
        "originals_preserved": True,
        "workspace_source_rows": len(workspace_rows),
        "legacy_workspace_rows_preserved_in_original": True,
        "memory_source_ids": sorted(memory_ids),
        "skills_source_ids": sorted(skills_ids),
    }
    (OUT / "migration_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(migrate(), indent=2))