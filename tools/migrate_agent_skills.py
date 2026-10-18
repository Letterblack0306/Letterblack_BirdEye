"""Validate and minimally normalize portable Agent Skills packages without touching bodies.

Dry-run by default. --apply backs up original bytes outside the curated index,
preserves package paths and Markdown bodies, and validates each replacement.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mcp_server


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def propose(path: Path, root: Path) -> bytes | None:
    raw = path.read_bytes()
    rel = path.relative_to(root).as_posix()
    text = raw.decode("utf-8-sig")
    if mcp_server._agent_skill_metadata(rel, text) is not None:
        return None
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{rel}: missing YAML frontmatter")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise ValueError(f"{rel}: unterminated YAML frontmatter")
    header = "".join(lines[1:end])
    try:
        meta = yaml.safe_load(header)
    except yaml.YAMLError:
        # Only fix a single-line unquoted description; do not guess at more
        # complicated malformed documents.
        rows = header.splitlines(keepends=True)
        choices = [i for i, line in enumerate(rows) if line.startswith("description: ")]
        if len(choices) != 1:
            raise
        i = choices[0]
        text_value = rows[i].split(":", 1)[1].strip()
        rows[i] = "description: " + json.dumps(text_value, ensure_ascii=False) + "\n"
        meta = yaml.safe_load("".join(rows))
    if not isinstance(meta, dict):
        raise ValueError(f"{rel}: metadata is not a mapping")
    original_name = meta.get("name")
    description = meta.get("description")
    if not isinstance(original_name, str) or not isinstance(description, str) or not description:
        raise ValueError(f"{rel}: missing name/description")
    new_name = path.parent.name
    if original_name != new_name:
        md = meta.setdefault("metadata", {})
        if md is None:
            md = {}
        if not isinstance(md, dict):
            raise ValueError(f"{rel}: metadata is not a mapping")
        md.setdefault("birdeye-legacy-name", original_name)
        meta["metadata"] = md
        meta["name"] = new_name
    if len(description) > 1024:
        md = meta.setdefault("metadata", {})
        if md is None:
            md = {}
        if not isinstance(md, dict):
            raise ValueError(f"{rel}: metadata is not a mapping")
        md.setdefault("birdeye-original-description", description)
        meta["metadata"] = md
        prefix = description[:1000]
        meta["description"] = prefix.rsplit(" ", 1)[0] or prefix
    metadata = meta.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            raise ValueError(f"{rel}: metadata is not a mapping")
        meta["metadata"] = {
            str(k): value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            for k, value in metadata.items()
        }
    nl = "\r\n" if b"\r\n" in raw else "\n"
    front = "---\n" + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000) + "---\n"
    front = front.replace("\n", nl)
    body = "".join(lines[end + 1:])
    replacement = (b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b"") + (front + body).encode("utf-8")
    if mcp_server._agent_skill_metadata(rel, replacement.decode("utf-8-sig")) is None:
        raise ValueError(f"{rel}: output does not satisfy portable Agent Skills metadata")
    if replacement == raw:
        raise ValueError(f"{rel}: no effective normalization")
    return replacement


def run() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=mcp_server.SKILLS_ROOT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    proposed = []
    errors = []
    skills = sorted(root.rglob("SKILL.md"))
    for file in skills:
        try:
            new = propose(file, root)
        except (ValueError, UnicodeError, yaml.YAMLError) as exc:
            errors.append(f"{file.relative_to(root).as_posix()}: {exc}")
            continue
        if new is not None:
            old = file.read_bytes()
            proposed.append((file, old, new))
    report = {
        "format": "agent-skills",
        "root": str(root),
        "packages": len(skills),
        "planned": len(proposed),
        "invalid_unmigratable": errors,
        "paths": [str(p.relative_to(root).as_posix()) for p, _, _ in proposed],
        "applied": False,
    }
    if args.apply:
        if errors:
            raise RuntimeError("refusing partial migration: " + "; ".join(errors))
        backup_root = Path(__file__).resolve().parents[1] / "state" / "skill_migrations"
        backup = backup_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup.mkdir(parents=True, exist_ok=False)
        touched = []
        try:
            for file, old, new in proposed:
                rel = file.relative_to(root)
                prev = backup / rel
                prev.parent.mkdir(parents=True, exist_ok=True)
                prev.write_bytes(old)
                if file.read_bytes() != old:
                    raise RuntimeError(f"concurrent modification: {rel}")
                fd, tempname = tempfile.mkstemp(prefix=".skill-", suffix=".tmp", dir=str(file.parent))
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(new)
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(tempname, file)
                finally:
                    if os.path.exists(tempname):
                        os.unlink(tempname)
                touched.append((file, old, new))
                if file.read_bytes() != new:
                    raise RuntimeError(f"postwrite mismatch: {rel}")
        except Exception:
            for file, old, new in reversed(touched):
                if file.read_bytes() == new:
                    file.write_bytes(old)
            raise
        manifest = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "entries": [{"path": p.relative_to(root).as_posix(), "before_sha256": sha(old),
                         "after_sha256": sha(new), "before_bytes": len(old),
                         "after_bytes": len(new)} for p, old, new in proposed],
        }
        (backup / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        report.update({"applied": True, "backup_manifest": str(backup / "manifest.json")})
    print(json.dumps(report, indent=2, ensure_ascii=True))


if __name__ == "__main__":
    run()
