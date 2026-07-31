from __future__ import annotations

import argparse
import fnmatch
import hashlib
import heapq
import json
import re
import sqlite3
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml
except ImportError:  # pragma: no cover - fallback is exercised only without PyYAML.
    yaml = None

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / "state"
CONFIG_PATH = ROOT / "config.json"
GOVERNANCE_PATH = ROOT / "governance.json"
DATABASE_PATH = STATE_DIR / "workspace.db"
PROGRESS_PATH = STATE_DIR / "trace_progress.json"
SUMMARY_PATH = STATE_DIR / "workspace_trace.json"
LAST_SEARCH_PATH = STATE_DIR / "last_search.json"

STATE_FILE_SCHEMA_VERSION = 1

_AUTHORIZED_TRACE_TOP_FIELDS: frozenset[str] = frozenset({
    "run_id",
    "status",
    "started_at",
    "completed_at",
    "elapsed_seconds",
    "mode",
    "database",
    "knowledge_roots",
    "statistics",
    "schema_version",
})

_AUTHORIZED_TRACE_STATISTICS_FIELDS: frozenset[str] = frozenset({
    "files_seen_this_run",
    "files_hashed_this_run",
    "files_cached_this_run",
    "files_large_this_run",
    "files_unreadable_this_run",
    "metadata_errors_this_run",
    "bytes_seen_this_run",
    "bytes_hashed_this_run",
    "files_per_second",
    "indexed_file_count",
    "indexed_total_bytes",
    "indexed_hashed_records",
    "reconciled",
    "reconciliation_blocked_reason",
})

_AUTHORIZED_PROGRESS_FIELDS: frozenset[str] = frozenset({
    "run_id",
    "status",
    "updated_at",
    "started_at",
    "elapsed_seconds",
    "current_root",
    "current_file",
    "files_seen",
    "files_hashed",
    "files_cached",
    "files_large",
    "files_unreadable",
    "metadata_errors",
    "bytes_seen",
    "bytes_hashed",
    "files_per_second",
    "database",
    "reconciled",
    "reconciliation_blocked_reason",
    "schema_version",
})


class GovernanceError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Missing required file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"Top-level JSON must be an object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def human_bytes(value: int | float) -> str:
    number = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(number) < 1024 or unit == "PB":
            return f"{int(number)} {unit}" if unit == "B" else f"{number:.2f} {unit}"
        number /= 1024
    return f"{number:.2f} PB"


def human_duration(seconds: float) -> str:
    remaining = max(0, int(seconds))
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, remaining = divmod(remaining, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours:02d}h")
    if minutes or hours or days:
        parts.append(f"{minutes:02d}m")
    parts.append(f"{remaining:02d}s")
    return " ".join(parts)


def shorten(value: str, width: int = 100) -> str:
    if len(value) <= width:
        return value
    usable = width - 3
    left = usable // 2
    return value[:left] + "..." + value[-(usable - left):]


def safe_root_name(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-.")
    if not normalized:
        raise GovernanceError("Knowledge root name cannot be empty")
    return normalized.lower()


_REFERENCE_METADATA_FIELDS = (
    "id",
    "record_type",
    "source_class",
    "authority_level",
    "verification_status",
    "workspace_scope",
    "execution_status",
    "guard_binding",
)


def reference_metadata(path: Path, content: str) -> tuple[str, dict[str, Any]]:
    """Return safe, structured metadata for a bundled YAML reference mapping."""
    if path.suffix.lower() not in {".yaml", ".yml"}:
        return "not_applicable", {}
    if yaml is None:
        parsed = _fallback_yaml_mapping(content)
    else:
        try:
            parsed = yaml.safe_load(content)
        except yaml.YAMLError:
            return "invalid", {}
    if not isinstance(parsed, dict):
        return "invalid", {}
    return "parsed", {
        field: parsed[field]
        for field in _REFERENCE_METADATA_FIELDS
        if field in parsed
    }


def _fallback_yaml_mapping(content: str) -> dict[str, Any] | None:
    """Parse the small, scalar-and-block mapping subset used by bundled records.

    This is deliberately non-general: it never constructs Python objects or
    interprets YAML tags, and exists only when PyYAML is unavailable.
    """
    lines = [line.rstrip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    result: dict[str, Any] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.startswith((" ", "-")) or ":" not in line:
            return None
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        if not key:
            return None
        if value:
            scalar = _yaml_scalar(value)
            if scalar is _INVALID_YAML_SCALAR:
                return None
            result[key] = scalar
            index += 1
            continue
        index += 1
        nested: dict[str, Any] = {}
        values: list[Any] = []
        while index < len(lines) and lines[index].startswith("  "):
            child = lines[index].strip()
            if child.startswith("- "):
                scalar = _yaml_scalar(child[2:].strip())
                if scalar is _INVALID_YAML_SCALAR:
                    return None
                values.append(scalar)
            elif ":" in child:
                child_key, child_value = child.split(":", 1)
                scalar = _yaml_scalar(child_value.strip())
                if not child_key.strip() or scalar is _INVALID_YAML_SCALAR:
                    return None
                nested[child_key.strip()] = scalar
            else:
                return None
            index += 1
        result[key] = values if values else nested
    return result


_INVALID_YAML_SCALAR = object()


def _yaml_scalar(value: str) -> Any:
    if value.startswith(("[", "{")) and not value.endswith(("]", "}")):
        return _INVALID_YAML_SCALAR
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "Null", "NULL", "~"}:
        return None
    if value[:1] in {"'", '"'}:
        if len(value) < 2 or value[-1] != value[0]:
            return _INVALID_YAML_SCALAR
        return value[1:-1]
    return value


@dataclass(frozen=True)
class KnowledgeRoot:
    name: str
    path: Path
    root_class: str


@dataclass(frozen=True)
class Context:
    config: dict[str, Any]
    governance: dict[str, Any]
    roots: tuple[KnowledgeRoot, ...]

    @classmethod
    def load(cls) -> "Context":
        config = load_json(CONFIG_PATH)
        governance = load_json(GOVERNANCE_PATH)
        raw_roots = config.get("knowledge_roots")
        if not isinstance(raw_roots, list) or not raw_roots:
            raise GovernanceError("config.json requires a non-empty knowledge_roots list")

        roots: list[KnowledgeRoot] = []
        names: set[str] = set()
        paths: set[str] = set()

        for item in raw_roots:
            if not isinstance(item, dict):
                raise GovernanceError("Each knowledge_roots entry must be an object")
            name = safe_root_name(str(item.get("name", "")))
            if name == "lbe-reference":
                raise GovernanceError(
                    "Knowledge root name 'lbe-reference' is reserved for bundled references"
                )
            raw_path = str(item.get("path", "")).strip()
            if not raw_path:
                raise GovernanceError(f"Knowledge root '{name}' has no path")
            path = Path(raw_path).expanduser().resolve()
            if name in names:
                raise GovernanceError(f"Duplicate knowledge root name: {name}")
            key = str(path).casefold()
            if key in paths:
                raise GovernanceError(f"Duplicate knowledge root path: {path}")
            if not path.exists() or not path.is_dir():
                raise FileNotFoundError(f"Knowledge root does not exist: {path}")
            roots.append(KnowledgeRoot(name, path, "workspace"))
            names.add(name)
            paths.add(key)

        reference_path = (ROOT / "examples" / "reference").resolve()
        if reference_path.is_dir():
            reference_name = "lbe-reference"
            reference_key = str(reference_path).casefold()
            if reference_name in names:
                raise GovernanceError(
                    "Knowledge root name 'lbe-reference' is reserved for bundled references"
                )
            if reference_key in paths:
                raise GovernanceError(f"Duplicate knowledge root path: {reference_path}")
            roots.append(KnowledgeRoot(reference_name, reference_path, "reference"))
            names.add(reference_name)
            paths.add(reference_key)

        for index, left in enumerate(roots):
            for right in roots[index + 1:]:
                try:
                    left.path.relative_to(right.path)
                except ValueError:
                    pass
                else:
                    raise GovernanceError(f"Overlapping roots: {left.path} is inside {right.path}")
                try:
                    right.path.relative_to(left.path)
                except ValueError:
                    pass
                else:
                    raise GovernanceError(f"Overlapping roots: {right.path} is inside {left.path}")

        return cls(config, governance, tuple(roots))


def matches_any(path_text: str, patterns: list[str]) -> bool:
    normalized = path_text.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, str(pattern)) for pattern in patterns)


def path_allowed(path_text: str, allowed: list[str]) -> bool:
    normalized = path_text.replace("\\", "/").strip("/")
    for raw in allowed:
        entry = str(raw).replace("\\", "/").strip("/")
        if entry in {"", ".", "*", "**"}:
            return True
        if normalized == entry or normalized.startswith(entry + "/"):
            return True
    return False


def virtual_path(root: KnowledgeRoot, path: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.path).as_posix()
    except (ValueError, OSError) as exc:
        raise GovernanceError(f"Path escapes knowledge root {root.name}: {path}") from exc
    return root.name if relative == "." else f"{root.name}/{relative}"


def split_virtual_path(ctx: Context, value: str) -> tuple[KnowledgeRoot, Path]:
    normalized = value.replace("\\", "/").strip("/")
    root_name, separator, relative = normalized.partition("/")
    if not normalized or not separator:
        raise GovernanceError("Path must begin with a root name and '/'")
    root = next((item for item in ctx.roots if item.name == root_name.lower()), None)
    if root is None:
        available = ", ".join(item.name for item in ctx.roots)
        raise GovernanceError(f"Path must begin with a root name ({available})")
    candidate = (root.path / relative).resolve()
    try:
        candidate.relative_to(root.path)
    except ValueError as exc:
        raise GovernanceError("Path escapes configured knowledge root") from exc
    return root, candidate


def iter_files(ctx: Context, root: KnowledgeRoot) -> Iterable[tuple[Path, str]]:
    forbidden = list(ctx.governance.get("forbidden_globs", []))
    allowed = list(ctx.governance.get("allowed_read_paths", ["."]))
    try:
        for path in root.path.rglob("*"):
            try:
                if not path.is_file():
                    continue
                virtual = virtual_path(root, path)
            except (OSError, PermissionError, GovernanceError):
                continue
            relative = virtual.split("/", 1)[1] if "/" in virtual else "."
            if matches_any(virtual, forbidden) or matches_any(relative, forbidden):
                continue
            if not path_allowed(relative, allowed):
                continue
            yield path, virtual
    except (OSError, PermissionError):
        return


def open_database() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=60)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA temp_store=FILE")
    connection.execute("PRAGMA cache_size=-32768")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            resume_requested INTEGER NOT NULL DEFAULT 0,
            files_seen INTEGER NOT NULL DEFAULT 0,
            files_hashed INTEGER NOT NULL DEFAULT 0,
            files_cached INTEGER NOT NULL DEFAULT 0,
            files_large INTEGER NOT NULL DEFAULT 0,
            files_unreadable INTEGER NOT NULL DEFAULT 0,
            metadata_errors INTEGER NOT NULL DEFAULT 0,
            bytes_seen INTEGER NOT NULL DEFAULT 0,
            bytes_hashed INTEGER NOT NULL DEFAULT 0,
            current_root TEXT,
            current_file TEXT,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS files (
            root TEXT NOT NULL,
            path TEXT NOT NULL,
            physical_path TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            sha256 TEXT,
            hash_status TEXT NOT NULL,
            error TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_seen_run TEXT NOT NULL,
            PRIMARY KEY(root, path)
        );

        CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
        CREATE INDEX IF NOT EXISTS idx_files_run ON files(last_seen_run);
        CREATE INDEX IF NOT EXISTS idx_files_root ON files(root);
        CREATE INDEX IF NOT EXISTS idx_files_path ON files(path);
        """
    )
    connection.commit()
    return connection


@dataclass
class TraceStats:
    started: float
    started_at: str
    current_root: str = ""
    current_file: str = ""
    files_seen: int = 0
    files_hashed: int = 0
    files_cached: int = 0
    files_large: int = 0
    files_unreadable: int = 0
    metadata_errors: int = 0
    bytes_seen: int = 0
    bytes_hashed: int = 0

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def rate(self) -> float:
        elapsed = self.elapsed()
        return self.files_seen / elapsed if elapsed else 0.0


def activity_bar(value: int, width: int = 28) -> str:
    chars = ["-"] * width
    chars[value % width] = "#"
    return "[" + "".join(chars) + "]"


def print_progress(stats: TraceStats, newline: bool = False) -> None:
    line1 = (
        f"{activity_bar(stats.files_seen)} Files: {stats.files_seen:,} | "
        f"Hashed: {stats.files_hashed:,} | Cache: {stats.files_cached:,} | "
        f"Large: {stats.files_large:,} | Unreadable: {stats.files_unreadable:,}"
    )
    line2 = (
        f"Root: {stats.current_root or '-'} | Data: {human_bytes(stats.bytes_seen)} | "
        f"Hashed data: {human_bytes(stats.bytes_hashed)} | "
        f"Elapsed: {human_duration(stats.elapsed())} | Rate: {stats.rate():.2f} files/s"
    )
    line3 = f"File: {shorten(stats.current_file or '-')}"
    if sys.stdout.isatty():
        print("\r\033[2K" + line1 + "\n\033[2K" + line2 + "\n\033[2K" + line3, end="", flush=True)
        if newline:
            print()
        else:
            print("\033[2A", end="", flush=True)
    else:
        print(line1)
        print(line2)
        print(line3)


def progress_payload(
    run_id: str,
    stats: TraceStats,
    status: str,
    reconciled: bool | None = None,
    reconciliation_blocked_reason: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "updated_at": utc_now(),
        "started_at": stats.started_at,
        "elapsed_seconds": round(stats.elapsed(), 3),
        "current_root": stats.current_root,
        "current_file": stats.current_file,
        "files_seen": stats.files_seen,
        "files_hashed": stats.files_hashed,
        "files_cached": stats.files_cached,
        "files_large": stats.files_large,
        "files_unreadable": stats.files_unreadable,
        "metadata_errors": stats.metadata_errors,
        "bytes_seen": stats.bytes_seen,
        "bytes_hashed": stats.bytes_hashed,
        "files_per_second": round(stats.rate(), 3),
        "database": str(DATABASE_PATH),
        "schema_version": STATE_FILE_SCHEMA_VERSION,
    }
    if reconciled is not None:
        payload["reconciled"] = reconciled
    if reconciliation_blocked_reason is not None:
        payload["reconciliation_blocked_reason"] = reconciliation_blocked_reason
    return payload


def _reconciliation_blocked_reason(stats: TraceStats) -> str | None:
    expected = (
        stats.files_hashed
        + stats.files_cached
        + stats.files_large
        + stats.files_unreadable
    )
    if expected != stats.files_seen:
        return (
            f"files_seen ({stats.files_seen}) does not equal "
            f"hashed ({stats.files_hashed}) + cached ({stats.files_cached}) + "
            f"large ({stats.files_large}) + unreadable ({stats.files_unreadable})"
        )
    return None


def update_run(
    connection: sqlite3.Connection,
    run_id: str,
    stats: TraceStats,
    status: str,
    completed_at: str | None = None,
    error: str | None = None,
) -> None:
    connection.execute(
        """
        UPDATE runs SET
            completed_at=?, status=?, files_seen=?, files_hashed=?,
            files_cached=?, files_large=?, files_unreadable=?,
            metadata_errors=?, bytes_seen=?, bytes_hashed=?,
            current_root=?, current_file=?, error=?
        WHERE run_id=?
        """,
        (
            completed_at, status, stats.files_seen, stats.files_hashed,
            stats.files_cached, stats.files_large, stats.files_unreadable,
            stats.metadata_errors, stats.bytes_seen, stats.bytes_hashed,
            stats.current_root, stats.current_file, error, run_id,
        ),
    )


def trace_workspace(
    ctx: Context,
    *,
    resume: bool = False,
    progress_every: int | None = None,
    checkpoint_every: int | None = None,
) -> dict[str, Any]:
    max_bytes = int(ctx.config.get("max_file_bytes", 5_000_000))
    progress_every = max(1, int(progress_every or ctx.config.get("progress_every_files", 250)))
    checkpoint_every = max(1, int(checkpoint_every or ctx.config.get("checkpoint_every_files", 1000)))
    run_id = uuid.uuid4().hex
    stats = TraceStats(time.monotonic(), utc_now())
    connection = open_database()
    connection.execute(
        "INSERT INTO runs(run_id, started_at, status, resume_requested) VALUES(?, ?, 'running', ?)",
        (run_id, stats.started_at, int(resume)),
    )
    connection.commit()
    write_json(PROGRESS_PATH, progress_payload(run_id, stats, "starting"))

    try:
        for root in ctx.roots:
            stats.current_root = root.name
            for path, virtual in iter_files(ctx, root):
                stats.current_file = virtual
                try:
                    stat = path.stat()
                except (OSError, PermissionError):
                    stats.metadata_errors += 1
                    continue

                size = int(stat.st_size)
                modified_ns = int(stat.st_mtime_ns)
                stats.files_seen += 1
                stats.bytes_seen += size

                existing = connection.execute(
                    "SELECT size, modified_ns, sha256, hash_status FROM files WHERE root=? AND path=?",
                    (root.name, virtual),
                ).fetchone()

                file_hash: str | None = None
                status = "unhashed"
                error: str | None = None

                if size > max_bytes:
                    status = "too_large"
                    stats.files_large += 1
                elif (
                    existing is not None
                    and int(existing["size"]) == size
                    and int(existing["modified_ns"]) == modified_ns
                    and existing["sha256"]
                ):
                    file_hash = str(existing["sha256"])
                    status = "cached"
                    stats.files_cached += 1
                else:
                    try:
                        file_hash = sha256_file(path)
                        status = "hashed"
                        stats.files_hashed += 1
                        stats.bytes_hashed += size
                    except (OSError, PermissionError) as exc:
                        status = "unreadable"
                        error = f"{type(exc).__name__}: {exc}"
                        stats.files_unreadable += 1

                now = utc_now()
                connection.execute(
                    """
                    INSERT INTO files(
                        root,path,physical_path,size,modified_ns,sha256,hash_status,error,
                        first_seen_at,last_seen_at,last_seen_run
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(root,path) DO UPDATE SET
                        physical_path=excluded.physical_path,
                        size=excluded.size,
                        modified_ns=excluded.modified_ns,
                        sha256=excluded.sha256,
                        hash_status=excluded.hash_status,
                        error=excluded.error,
                        last_seen_at=excluded.last_seen_at,
                        last_seen_run=excluded.last_seen_run
                    """,
                    (
                        root.name, virtual, str(path), size, modified_ns, file_hash,
                        status, error, now, now, run_id,
                    ),
                )

                if stats.files_seen % progress_every == 0:
                    print_progress(stats)
                    update_run(connection, run_id, stats, "running")
                    write_json(PROGRESS_PATH, progress_payload(run_id, stats, "running"))

                if stats.files_seen % checkpoint_every == 0:
                    update_run(connection, run_id, stats, "checkpoint_saved")
                    connection.commit()
                    write_json(PROGRESS_PATH, progress_payload(run_id, stats, "checkpoint_saved"))

            print_progress(stats, newline=True)
            update_run(connection, run_id, stats, f"root_completed:{root.name}")
            connection.commit()

        root_names = [root.name for root in ctx.roots]
        placeholders = ",".join("?" for _ in root_names)
        connection.execute(
            f"DELETE FROM files WHERE root IN ({placeholders}) AND last_seen_run<>?",
            (*root_names, run_id),
        )
        completed_at = utc_now()
        connection.commit()

        totals = connection.execute(
            """
            SELECT COUNT(*) AS count, COALESCE(SUM(size),0) AS bytes,
                   SUM(CASE WHEN sha256 IS NOT NULL THEN 1 ELSE 0 END) AS hashed
            FROM files
            """
        ).fetchone()

        reconciliation_blocked_reason = _reconciliation_blocked_reason(stats)
        reconciled = reconciliation_blocked_reason is None
        final_status = "completed" if reconciled else "finished_with_gaps"

        summary = {
            "run_id": run_id,
            "status": final_status,
            "started_at": stats.started_at,
            "completed_at": completed_at,
            "elapsed_seconds": round(stats.elapsed(), 3),
            "mode": "sqlite-read-only-knowledge-index",
            "database": str(DATABASE_PATH),
            "schema_version": STATE_FILE_SCHEMA_VERSION,
            "knowledge_roots": [
                {"name": root.name, "path": str(root.path), "root_class": root.root_class}
                for root in ctx.roots
            ],
            "statistics": {
                "files_seen_this_run": stats.files_seen,
                "files_hashed_this_run": stats.files_hashed,
                "files_cached_this_run": stats.files_cached,
                "files_large_this_run": stats.files_large,
                "files_unreadable_this_run": stats.files_unreadable,
                "metadata_errors_this_run": stats.metadata_errors,
                "bytes_seen_this_run": stats.bytes_seen,
                "bytes_hashed_this_run": stats.bytes_hashed,
                "files_per_second": round(stats.rate(), 3),
                "indexed_file_count": int(totals["count"]),
                "indexed_total_bytes": int(totals["bytes"]),
                "indexed_hashed_records": int(totals["hashed"] or 0),
                "reconciled": reconciled,
                "reconciliation_blocked_reason": reconciliation_blocked_reason,
            },
        }
        write_json(SUMMARY_PATH, summary)
        update_run(connection, run_id, stats, final_status, completed_at)
        write_json(PROGRESS_PATH, progress_payload(run_id, stats, final_status, reconciled, reconciliation_blocked_reason))
        print_progress(stats, newline=True)
        print("\nTrace completed successfully.")
        print(f"Database: {DATABASE_PATH}")
        print(f"Files: {stats.files_seen:,}")
        print(f"Data: {human_bytes(stats.bytes_seen)}")
        print(f"Elapsed: {human_duration(stats.elapsed())}")
        return summary

    except KeyboardInterrupt:
        update_run(connection, run_id, stats, "interrupted", utc_now(), "KeyboardInterrupt")
        connection.commit()
        write_json(PROGRESS_PATH, progress_payload(run_id, stats, "interrupted"))
        print_progress(stats, newline=True)
        print("\nTrace interrupted safely.")
        print("Resume with: python .\\agent.py trace --resume")
        raise
    except Exception as exc:
        update_run(connection, run_id, stats, "failed", utc_now(), f"{type(exc).__name__}: {exc}")
        connection.commit()
        write_json(PROGRESS_PATH, progress_payload(run_id, stats, "failed"))
        raise
    finally:
        connection.close()


def inspect_file(ctx: Context, value: str) -> dict[str, Any]:
    root, path = split_virtual_path(ctx, value)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"File does not exist: {value}")
    virtual = virtual_path(root, path)
    relative = virtual.split("/", 1)[1]
    forbidden = list(ctx.governance.get("forbidden_globs", []))
    if matches_any(virtual, forbidden) or matches_any(relative, forbidden):
        raise GovernanceError(f"Read blocked by forbidden pattern: {virtual}")
    if not path_allowed(relative, list(ctx.governance.get("allowed_read_paths", ["."]))):
        raise GovernanceError(f"Read path not allowlisted: {virtual}")
    max_bytes = int(ctx.config.get("max_file_bytes", 5_000_000))
    data = path.read_bytes()
    if len(data) > max_bytes:
        raise GovernanceError(f"File exceeds max_file_bytes: {virtual}")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise GovernanceError(f"Only UTF-8 text files are supported: {virtual}") from exc
    return {
        "root": root.name,
        "path": virtual,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "content": content,
    }


def _classify_source(path: str) -> tuple[str, int]:
    """Return (classification, penalty_score) for retrieval-time noise deprioritization.

    Penalty is subtracted from the match score so authoritative source files
    outrank generated artifacts and dependency metadata.  Hidden configuration
    files (``.gitignore``, ``.github/``, ``.vscode/``, ``.npmrc``, etc.) are
    NOT penalized — only VCS internals are."""
    normalized = "/" + path.lower().replace("\\", "/").strip("/") + "/"

    # VCS internals — preserve .gitignore, .github/, .gitattributes
    if "/.git/" in normalized:
        return ("vcs_internal", 800)

    # Vendor / dependency trees
    if "/node_modules/" in normalized:
        return ("vendor", 700)

    # Package lockfiles
    for lock in ("/package-lock.json", "/yarn.lock", "/poetry.lock", "/pnpm-lock.yaml"):
        if lock in normalized or normalized.endswith(lock.strip("/") + "/"):
            return ("lockfile", 650)

    # Build / dist / generated output
    for noise_dir in ("/dist/", "/build/", "/out/", "/.next/", "/.nuxt/"):
        if noise_dir in normalized:
            return ("generated", 500)

    # Release proofs and internal state
    if "/release/" in normalized or "/release-public/" in normalized or "/release-exec/" in normalized:
        return ("release_proof", 550)

    # Archives
    for ext in (".zip", ".tar.gz", ".7z", ".rar", ".zxp"):
        if normalized.rstrip("/").endswith(ext):
            return ("archive", 700)

    # Backups
    if "/backup/" in normalized or "/backups/" in normalized or ".bak" in normalized:
        return ("backup", 700)

    # Temporary worktrees
    if "/worktree/" in normalized or "/_worktree/" in normalized:
        return ("worktree", 600)

    return ("source", 0)


def search_workspace(
    ctx: Context,
    query: str,
    *,
    max_results: int = 50,
    extensions: list[str] | None = None,
    roots: list[str] | None = None,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        raise GovernanceError("Search query cannot be empty")
    max_results = max(1, min(int(max_results), 200))
    default_extensions = [
        ".js",".jsx",".mjs",".cjs",".ts",".tsx",".py",".ps1",".json",".jsonl",
        ".md",".html",".htm",".xml",".css",".txt",".yml",".yaml",".toml",
        ".ini",".bat",".cmd",".sh",
    ]
    allowed_extensions = {
        item.lower() if item.startswith(".") else "." + item.lower()
        for item in (extensions or default_extensions)
    }
    selected_roots = {safe_root_name(item) for item in roots} if roots else None
    known_roots = {root.name for root in ctx.roots}
    root_by_name = {root.name: root for root in ctx.roots}
    if selected_roots and selected_roots - known_roots:
        raise GovernanceError(f"Unknown roots: {sorted(selected_roots - known_roots)}")

    stop_words = {
        "a","an","and","are","as","at","be","by","for","from","has","have",
        "in","is","it","of","on","or","return","returns","that","the","this",
        "to","was","were","with","after","before",
    }
    query_lower = query.lower()
    raw_terms = re.findall(r"[a-z0-9_.$/-]+", query_lower)
    terms = [term for term in raw_terms if term not in stop_words and len(term) > 2] or raw_terms
    required = 1 if len(terms) == 1 else 2
    max_bytes = int(ctx.config.get("max_file_bytes", 5_000_000))
    started = time.monotonic()
    connection = None
    try:
        connection = open_database()
        scanned = skipped = serial = 0
        candidates: list[tuple[int, int, dict[str, Any]]] = []

        sql = "SELECT root,path,physical_path,size,sha256 FROM files"
        parameters: list[Any] = []
        if selected_roots:
            marks = ",".join("?" for _ in selected_roots)
            sql += f" WHERE root IN ({marks})"
            parameters.extend(sorted(selected_roots))

        count_sql = "SELECT COUNT(*) FROM files"
        count_params: list[Any] = []
        if selected_roots:
            marks = ",".join("?" for _ in selected_roots)
            count_sql += f" WHERE root IN ({marks})"
            count_params.extend(sorted(selected_roots))
        files_considered = connection.execute(count_sql, count_params).fetchone()[0]

        for row in connection.execute(sql, parameters):
            virtual = str(row["path"])
            physical = Path(str(row["physical_path"]))
            size = int(row["size"])
            if physical.suffix.lower() not in allowed_extensions:
                continue
            scanned += 1
            virtual_lower = virtual.lower()
            filename_matches = sum(term in virtual_lower for term in terms)
            exact_path = query_lower in virtual_lower
            content = ""
            content_lower = ""
            if size <= max_bytes:
                try:
                    content = physical.read_text(encoding="utf-8", errors="ignore")
                    content_lower = content.lower()
                except (OSError, PermissionError):
                    skipped += 1
                    continue
            exact_content = bool(content_lower) and query_lower in content_lower
            content_matches = sum(term in content_lower for term in terms)
            matched = max(filename_matches, content_matches)
            if not exact_path and not exact_content and matched < required:
                continue

            score = (500 if exact_path else 0) + (400 if exact_content else 0)
            score += filename_matches * 60 + content_matches * 35
            if terms and matched == len(terms):
                score += 120
            for marker in ("/src/","/source/","/client/","/host/","/jsx/","/scripts/","/tests/","/tools/"):
                if marker in virtual_lower:
                    score += 12
            for marker in ("/dist/","/build/","/vendor/","/backup","/archive/","/old/","/node_modules/","/.cep-dev/"):
                if marker in virtual_lower:
                    score -= 25

            snippet = ""
            line_number = None
            if content:
                lines = content.splitlines()
                best_index = 0
                best_score = -1
                for index, line in enumerate(lines):
                    lower = line.lower()
                    line_score = (200 if query_lower in lower else 0) + sum(
                        20 for term in terms if term in lower
                    )
                    if line_score > best_score:
                        best_score = line_score
                        best_index = index
                snippet = "\n".join(lines[max(0,best_index-2):min(len(lines),best_index+3)])[:1200]
                line_number = best_index + 1

            root = root_by_name.get(str(row["root"]))
            metadata_parse_status = "not_applicable"
            gallery_metadata: dict[str, Any] = {}

            # Source classification for noise deprioritization.
            classification, penalty = _classify_source(virtual)
            if root is not None and root.root_class == "reference":
                metadata_parse_status, gallery_metadata = reference_metadata(physical, content)
                # Bundled YAML support files are indexed as raw evidence, but only
                # gallery records participate in reference-pattern retrieval.
                if metadata_parse_status == "parsed" and not gallery_metadata.get("record_type"):
                    continue
                declared_source_class = gallery_metadata.get("source_class")
                if isinstance(declared_source_class, str) and declared_source_class.strip():
                    classification = declared_source_class

            # Compute penalized score once; used for display, heap, and ordering
            penalized_score = int(score) - penalty

            item = {
                "root": str(row["root"]),
                "path": virtual,
                "score": penalized_score,
                "size": size,
                "line": line_number,
                "snippet": snippet,
                "sha256": row["sha256"],
                "matched_terms": matched,
                "exact_phrase": exact_path or exact_content,
                "source_class": classification,
                "root_class": root.root_class if root is not None else "workspace",
                "metadata_parse_status": metadata_parse_status,
                "gallery_metadata": gallery_metadata,
            }
            serial += 1
            entry = (penalized_score, serial, item)
            limit = max_results * 4
            if len(candidates) < limit:
                heapq.heappush(candidates, entry)
            elif penalized_score > candidates[0][0]:
                heapq.heapreplace(candidates, entry)

        ordered = [
            entry[2] for entry in sorted(
                candidates, key=lambda item: (-item[0], item[2]["path"].lower())
            )
        ]
        results: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        for item in ordered:
            file_hash = item.get("sha256")
            if file_hash and file_hash in seen_hashes:
                continue
            results.append(item)
            if file_hash:
                seen_hashes.add(str(file_hash))
            if len(results) >= max_results:
                break

        if results:
            outcome = "matches_found"
            message = f"Found {len(results)} matching files."
        elif scanned == 0:
            outcome = "scope_empty"
            message = "No files matched the requested scope."
        else:
            outcome = "no_matches"
            message = "No content matched the query."

        # Compute classification statistics for transparency
        class_counts: dict[str, int] = {}
        for candidate in ordered:
            cls = candidate.get("source_class", "source")
            class_counts[cls] = class_counts.get(cls, 0) + 1

        output = {
            "query": query,
            "search_completed": True,
            "outcome": outcome,
            "message": message,
            "result_count": len(results),
            "results": results,
            "searched_roots": sorted(selected_roots) if selected_roots else sorted(known_roots),
            "meaningful_terms": terms,
            "minimum_required_matches": required,
            "scanned_files": scanned,
            "skipped_unreadable_files": skipped,
            "duplicate_hashes_collapsed": len(ordered) - len(results),
            "source_classifications": class_counts,
            "applied_filters": {
                "roots": sorted(selected_roots) if selected_roots else sorted(known_roots),
                "extensions": sorted(allowed_extensions),
                "max_results": max_results,
            },
            "scope": {
                "roots_requested": sorted(selected_roots) if selected_roots else None,
                "roots_searched": sorted(selected_roots) if selected_roots else sorted(known_roots),
                "extensions": sorted(allowed_extensions),
                "files_considered": files_considered,
            },
            "execution": {
                "files_searched": scanned,
                "files_unreadable": skipped,
            },
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
        write_json(LAST_SEARCH_PATH, output)
        return output
    except (sqlite3.Error, OSError, IOError) as exc:
        return {
            "query": query,
            "search_completed": False,
            "outcome": "search_failed",
            "message": "Search could not be completed.",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    finally:
        if connection is not None:
            connection.close()


def database_status() -> dict[str, Any]:
    connection = open_database()
    try:
        totals = connection.execute(
            """
            SELECT COUNT(*) AS file_count, COALESCE(SUM(size),0) AS total_bytes,
                   SUM(CASE WHEN sha256 IS NOT NULL THEN 1 ELSE 0 END) AS hashed_count,
                   SUM(CASE WHEN hash_status='too_large' THEN 1 ELSE 0 END) AS large_count,
                   SUM(CASE WHEN hash_status='unreadable' THEN 1 ELSE 0 END) AS unreadable_count
            FROM files
            """
        ).fetchone()
        last_run = connection.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return {
            "database": str(DATABASE_PATH),
            "file_count": int(totals["file_count"]),
            "total_bytes": int(totals["total_bytes"]),
            "total_human": human_bytes(int(totals["total_bytes"])),
            "hashed_count": int(totals["hashed_count"] or 0),
            "large_count": int(totals["large_count"] or 0),
            "unreadable_count": int(totals["unreadable_count"] or 0),
            "last_run": dict(last_run) if last_run else None,
        }
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="SQLite-backed read-only CEP/LBE knowledge tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("roots")
    p.set_defaults(func=lambda _: print(json.dumps(
        {"knowledge_roots": [
            {"name": r.name, "path": str(r.path), "root_class": r.root_class}
            for r in Context.load().roots
        ]},
        indent=2,
    )))

    p = sub.add_parser("trace")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--progress-every", type=int)
    p.add_argument("--checkpoint-every", type=int)
    p.set_defaults(func=lambda a: trace_workspace(
        Context.load(),
        resume=a.resume,
        progress_every=a.progress_every,
        checkpoint_every=a.checkpoint_every,
    ))

    p = sub.add_parser("status")
    p.set_defaults(func=lambda _: print(json.dumps(database_status(), indent=2)))

    p = sub.add_parser("inspect")
    p.add_argument("path")
    p.set_defaults(func=lambda a: print(json.dumps(
        inspect_file(Context.load(), a.path), indent=2, ensure_ascii=False
    )))

    p = sub.add_parser("search")
    p.add_argument("query")
    p.add_argument("--max-results", type=int, default=50)
    p.add_argument("--extensions")
    p.add_argument("--roots")
    def run_search(a: argparse.Namespace) -> None:
        extensions = [x.strip() for x in a.extensions.split(",") if x.strip()] if a.extensions else None
        roots = [x.strip() for x in a.roots.split(",") if x.strip()] if a.roots else None
        print(json.dumps(search_workspace(
            Context.load(), a.query, max_results=a.max_results,
            extensions=extensions, roots=roots,
        ), indent=2, ensure_ascii=False))
    p.set_defaults(func=run_search)

    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
