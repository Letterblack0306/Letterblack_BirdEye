"""Non-duplicating inventory index for the C:\\MCP Local tree."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(r"C:\MCP Local")
DB = Path(r"C:\MCP Local\Letterblack_BirdEye\eye_Databa\eye_mcp_local_inventory_01.db")
EXCLUDED = {".git", ".pytest_cache", "__pycache__", "archive", "eye_Databa", "state"}
MANAGED = {
    "Chat_Dataexported": "memory",
    "GPT-Knowledge": "knowledge",
    "Letterblack_BirdEye": "birdeye",
    "Memory": "memory",
    "Skills": "skills",
    "servers": "mcp-servers",
    "workspace": "workspace",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def features(path: Path) -> tuple[str, str, int | None, int | None, str]:
    ext = path.suffix.lower()
    kind = {".py": "python", ".json": "json", ".md": "markdown", ".ps1": "powershell", ".log": "log"}.get(ext, ext.lstrip(".") or "file")
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        terms = sorted(set(re.findall(r"[A-Za-z_][A-Za-z0-9_/-]{2,}", text.lower())))
        return ext, kind, text.count("\n") + (1 if text else 0), len(terms), json.dumps(terms[:500], ensure_ascii=False)
    except (OSError, UnicodeError):
        return ext, kind, None, None, ""


def build() -> dict:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS tree(
      relative_path TEXT PRIMARY KEY, absolute_path TEXT NOT NULL,
      node_type TEXT NOT NULL, managed_domain TEXT, managed_root TEXT,
      size INTEGER, modified_ns INTEGER, sha256 TEXT, extension TEXT,
      file_type TEXT, line_count INTEGER, token_count INTEGER,
      feature_terms TEXT, indexed_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_tree_sha256 ON tree(sha256);
    CREATE INDEX IF NOT EXISTS idx_tree_domain ON tree(managed_domain);
    """)
    conn.execute("DELETE FROM tree")
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    managed_refs = 0
    for path in sorted(ROOT.iterdir(), key=lambda p: p.name.lower()):
        rel = path.name
        if path.name in EXCLUDED:
            continue
        if path.is_dir() and path.name in MANAGED:
            conn.execute("INSERT INTO tree VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rel, str(path), "managed_root", MANAGED[path.name], str(path), None, None, None, None, None, None, None, "", now))
            managed_refs += 1
            continue
        if path.is_dir():
            conn.execute("INSERT INTO tree VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rel, str(path), "directory", None, None, None, None, None, None, None, None, None, "", now))
            count += 1
            for child in path.rglob("*"):
                if any(part in EXCLUDED for part in child.relative_to(ROOT).parts):
                    continue
                if child.is_dir():
                    conn.execute("INSERT OR REPLACE INTO tree VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (child.relative_to(ROOT).as_posix(), str(child), "directory", None, None, None, None, None, None, None, None, None, "", now))
                    count += 1
                elif child.is_file():
                    st = child.stat(); ext, kind, lines, tokens, terms = features(child)
                    conn.execute("INSERT OR REPLACE INTO tree VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (child.relative_to(ROOT).as_posix(), str(child), "file", None, None, st.st_size, st.st_mtime_ns, sha256(child), ext, kind, lines, tokens, terms, now))
                    count += 1
        elif path.is_file():
            st = path.stat(); ext, kind, lines, tokens, terms = features(path)
            conn.execute("INSERT OR REPLACE INTO tree VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (rel, str(path), "file", None, None, st.st_size, st.st_mtime_ns, sha256(path), ext, kind, lines, tokens, terms, now))
            count += 1
    conn.execute("INSERT OR REPLACE INTO tree VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (".", str(ROOT), "root", None, None, None, None, None, None, None, None, None, "", now))
    conn.commit(); conn.close()
    return {"ok": True, "database": str(DB), "indexed_nodes": count + managed_refs + 1, "managed_root_references": managed_refs, "excluded": sorted(EXCLUDED)}


if __name__ == "__main__":
    print(json.dumps(build(), indent=2))