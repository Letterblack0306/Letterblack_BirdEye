"""Durable EYES hash/index storage.

EYES keeps one append-safe SQLite database family per domain. Source files are
never copied or moved. Each record stores identity metadata and SHA-256 only;
the source path remains canonical. Database rotation is automatic when the
active database reaches ``EYE_DATABASE_MAX_BYTES`` (default: 1 GiB).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eye import load_config


EYE_DATA_DIR = Path(__file__).resolve().parent / "eye_Databa"
DEFAULT_MAX_BYTES = 1024 * 1024 * 1024
_DOMAINS = ("workspace", "memory", "skills")


def _max_database_bytes() -> int:
    raw = os.environ.get("EYE_DATABASE_MAX_BYTES", str(DEFAULT_MAX_BYTES))
    try:
        return max(1024 * 1024, int(raw))
    except ValueError:
        return DEFAULT_MAX_BYTES


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _database_path(domain: str, number: int) -> Path:
    return EYE_DATA_DIR / f"eye_{domain}_data_{number:02d}.db"


def _next_database(domain: str) -> tuple[Path, int]:
    EYE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    numbers = []
    for path in EYE_DATA_DIR.glob(f"eye_{domain}_data_*.db"):
        try:
            numbers.append(int(path.stem.rsplit("_", 1)[1]))
        except (ValueError, IndexError):
            continue
    number = max(numbers, default=0)
    if number == 0:
        number = 1
    path = _database_path(domain, number)
    if path.exists() and path.stat().st_size >= _max_database_bytes():
        number += 1
        path = _database_path(domain, number)
    return path, number


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS files (
            domain TEXT NOT NULL,
            source_id TEXT NOT NULL,
            agent TEXT,
            relative_path TEXT NOT NULL,
            physical_path TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            sha256 TEXT,
            hash_status TEXT NOT NULL,
            error TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_event TEXT NOT NULL,
            extension TEXT,
            file_type TEXT,
            line_count INTEGER,
            token_count INTEGER,
            feature_terms TEXT,
            last_generation INTEGER,
            PRIMARY KEY(domain, source_id, relative_path)
        );
        CREATE TABLE IF NOT EXISTS changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            generation INTEGER,
            domain TEXT NOT NULL,
            source_id TEXT NOT NULL,
            agent TEXT,
            relative_path TEXT NOT NULL,
            event TEXT NOT NULL,
            old_sha256 TEXT,
            new_sha256 TEXT,
            observed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(files)")}
    for name, definition in (
        ("agent", "TEXT"),
        ("physical_path", "TEXT"),
        ("size", "INTEGER"),
        ("modified_ns", "INTEGER"),
        ("sha256", "TEXT"),
        ("hash_status", "TEXT"),
        ("error", "TEXT"),
        ("first_seen_at", "TEXT"),
        ("last_seen_at", "TEXT"),
        ("last_event", "TEXT"),
        ("extension", "TEXT"),
        ("file_type", "TEXT"),
        ("line_count", "INTEGER"),
        ("token_count", "INTEGER"),
        ("feature_terms", "TEXT"),
        ("last_generation", "INTEGER"),
    ):
        if name not in columns:
            conn.execute(f"ALTER TABLE files ADD COLUMN {name} {definition}")
    change_columns = {row[1] for row in conn.execute("PRAGMA table_info(changes)")}
    for name, definition in (("generation", "INTEGER"), ("agent", "TEXT")):
        if name not in change_columns:
            conn.execute(f"ALTER TABLE changes ADD COLUMN {name} {definition}")
    conn.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('canonical_generation','0')"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_changes_generation ON changes(generation) WHERE generation IS NOT NULL"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_files_path ON files(relative_path)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_changes_time ON changes(observed_at)")
    conn.commit()
    return conn


def _source_for(path: Path) -> tuple[str, str, str | None, Path] | None:
    """Resolve a physical path to (domain, source_id, agent, source_root)."""
    config = load_config()
    domains = {
        "workspace": config["roots"],
        "memory": [root for root in config["roots"] if root.get("id") == "memory"],
        "skills": [root for root in config["roots"] if root.get("id") == "skills"],
    }
    resolved = path.resolve()
    for root in domains["workspace"]:
        if root.get("id") in {"memory", "skills"} or root.get("class") == "memory":
            continue
        root_path = Path(str(root.get("path", ""))).resolve()
        try:
            resolved.relative_to(root_path)
        except ValueError:
            continue
        return "workspace", str(root.get("id")), root.get("agent"), root_path
    memory = domains["memory"]
    if memory:
        for source in memory[0].get("sources", []):
            source_path = Path(str(source.get("path", ""))).resolve()
            try:
                resolved.relative_to(source_path)
            except ValueError:
                continue
            return "memory", str(source.get("id")), source.get("agent"), source_path
    for root in domains["skills"]:
        root_path = Path(str(root.get("path", ""))).resolve()
        try:
            resolved.relative_to(root_path)
        except ValueError:
            continue
        return "skills", str(root.get("id")), root.get("agent"), root_path
    return None


def _hash_file(path: Path, max_bytes: int = 5_000_000) -> tuple[str | None, str, str | None]:
    try:
        size = path.stat().st_size
        if size > max_bytes:
            return None, "too_large", None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest(), "hashed", None
    except (OSError, PermissionError) as exc:
        return None, "unreadable", f"{type(exc).__name__}: {exc}"


def _file_features(path: Path, max_bytes: int = 5_000_000) -> tuple[str, str, int | None, int | None, str]:
    extension = path.suffix.lower()
    file_type = {
        ".py": "python", ".json": "json", ".md": "markdown", ".ps1": "powershell",
        ".js": "javascript", ".ts": "typescript", ".tsx": "typescript-react",
        ".jsx": "javascript-react", ".yaml": "yaml", ".yml": "yaml",
    }.get(extension, extension.lstrip(".") or "file")
    try:
        if path.stat().st_size > max_bytes:
            return extension, file_type, None, None, ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        terms = sorted({term.lower() for term in __import__("re").findall(r"[A-Za-z_][A-Za-z0-9_/-]{2,}", text)})
        return extension, file_type, text.count("\n") + (1 if text else 0), len(terms), json.dumps(terms[:500], ensure_ascii=False)
    except (OSError, UnicodeError):
        return extension, file_type, None, None, ""


def record_file_event(path: str | Path, event: str = "modified", max_bytes: int = 5_000_000) -> dict[str, Any]:
    """Persist one EYES file event. Missing files are recorded as deletions."""
    physical = Path(path).resolve()
    source = _source_for(physical)
    if source is None:
        return {"ok": False, "action": "ignored", "path": str(physical), "reason": "unconfigured"}
    domain, source_id, agent, source_root = source
    relative = physical.relative_to(source_root).as_posix()
    db_path, number = _next_database(domain)
    conn = _connect(db_path)
    try:
        previous = conn.execute(
            "SELECT sha256 FROM files WHERE domain=? AND source_id=? AND relative_path=?",
            (domain, source_id, relative),
        ).fetchone()
        old_sha = previous[0] if previous else None
        now = _now()
        mutation = event == "deleted" or not physical.is_file()
        if not mutation:
            stat = physical.stat()
            new_sha, status, error = _hash_file(physical, max_bytes)
            mutation = old_sha != new_sha or previous is None
        else:
            stat = None
            new_sha, status, error = None, "deleted", None

        generation = None
        if mutation:
            conn.execute("BEGIN IMMEDIATE")
            generation = int(
                conn.execute(
                    "SELECT value FROM meta WHERE key='canonical_generation'"
                ).fetchone()[0]
            ) + 1
            conn.execute(
                "UPDATE meta SET value=? WHERE key='canonical_generation'",
                (str(generation),),
            )
        if event == "deleted" or not physical.is_file():
            conn.execute(
                "DELETE FROM files WHERE domain=? AND source_id=? AND relative_path=?",
                (domain, source_id, relative),
            )
            conn.execute(
                "INSERT INTO changes(generation,domain,source_id,agent,relative_path,event,old_sha256,new_sha256,observed_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (generation, domain, source_id, agent, relative, "deleted", old_sha, None, now),
            )
            conn.commit()
            return {"ok": True, "action": "deleted", "domain": domain, "source_id": source_id, "path": relative, "database": str(db_path)}
        extension, file_type, line_count, token_count, feature_terms = _file_features(physical, max_bytes)
        values = (
            domain, source_id, agent, relative, str(physical), stat.st_size,
            stat.st_mtime_ns, new_sha, status, error, now, now, event,
            extension, file_type, line_count, token_count, feature_terms, generation,
        )
        columns = (
            "domain", "source_id", "agent", "relative_path", "physical_path",
            "size", "modified_ns", "sha256", "hash_status", "error",
            "first_seen_at", "last_seen_at", "last_event", "extension",
            "file_type", "line_count", "token_count", "feature_terms", "last_generation",
        )
        placeholders = ",".join("?" for _ in values)
        assignments = ",".join(
            f"{column}=excluded.{column}"
            for column in columns
            if column not in {"domain", "source_id", "relative_path"}
        )
        conn.execute(
            f"INSERT INTO files({','.join(columns)}) VALUES({placeholders}) "
            f"ON CONFLICT(domain,source_id,relative_path) DO UPDATE SET {assignments}",
            values,
        )
        if mutation:
            conn.execute(
                "INSERT INTO changes(generation,domain,source_id,agent,relative_path,event,old_sha256,new_sha256,observed_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (generation, domain, source_id, agent, relative, event, old_sha, new_sha, now),
            )
        conn.commit()
        return {"ok": True, "action": "indexed", "domain": domain, "source_id": source_id, "path": relative, "hash_status": status, "sha256": new_sha, "database": str(db_path), "database_number": number}
    finally:
        conn.close()


def sync_all(max_bytes: int = 5_000_000, roots: set[str] | None = None) -> dict[str, Any]:
    """Reconcile configured files into the three EYES database families.

    The filesystem walk is authoritative for the current source root.  Files
    present in the canonical ledger but absent from that walk are recorded as
    deletions so the normal change journal/projection path removes them.
    """
    config = load_config()
    counts = {domain: 0 for domain in _DOMAINS}
    for root in config["roots"]:
        domain = "memory" if root.get("id") == "memory" else "skills" if root.get("id") == "skills" else "workspace"
        if roots is not None and (domain != "workspace" or str(root.get("id")) not in roots):
            continue
        candidates = []
        if domain == "memory":
            candidates = [(str(s.get("path")), str(s.get("id"))) for s in root.get("sources", [])]
        else:
            candidates = [(str(root.get("path")), str(root.get("id")))]
        for source_path, _source_id in candidates:
            source_root = Path(source_path)
            if not source_root.is_dir():
                continue
            seen: set[str] = set()
            for path in source_root.rglob("*"):
                if path.is_file():
                    seen.add(path.resolve().relative_to(source_root.resolve()).as_posix())
                    result = record_file_event(path, "initial", max_bytes)
                    if result.get("ok") and result.get("action") == "indexed":
                        counts[domain] += 1
            db_path, _number = _next_database(domain)
            conn = _connect(db_path)
            try:
                indexed = conn.execute(
                    "SELECT relative_path, physical_path FROM files "
                    "WHERE domain=? AND source_id=?",
                    (domain, _source_id),
                ).fetchall()
            finally:
                conn.close()
            for row in indexed:
                relative = str(row["relative_path"]).replace("\\", "/")
                if relative not in seen:
                    result = record_file_event(Path(row["physical_path"]), "deleted", max_bytes)
                    if result.get("ok") and result.get("action") == "deleted":
                        counts[domain] += 1
    return {"ok": True, "indexed": counts, "database_dir": str(EYE_DATA_DIR)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist EYES hashes and file indexes.")
    parser.add_argument("command", choices=("status", "sync", "record"))
    parser.add_argument("path", nargs="?")
    parser.add_argument("--event", default="modified")
    args = parser.parse_args()
    if args.command == "status":
        files = sorted(EYE_DATA_DIR.glob("eye_*_data_*.db")) if EYE_DATA_DIR.is_dir() else []
        print(json.dumps({"ok": True, "database_dir": str(EYE_DATA_DIR), "databases": [str(p) for p in files]}, indent=2))
        return 0
    if args.command == "sync":
        print(json.dumps(sync_all(), indent=2))
        return 0
    if not args.path:
        parser.error("record requires a file path")
    print(json.dumps(record_file_event(args.path, args.event), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())