#!/usr/bin/env python
"""Letterblack Local Mini MCP v2.

Bounded local inspection, text mutation and process execution for agent clients.
Filesystem reads and writes are separately rooted. Mutations are atomic and
return before/after SHA-256 evidence. Process execution never uses a shell.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer as _Server
except Exception:
    from mcp.server.fastmcp import FastMCP as _Server

SERVER_NAME = "letterblack-local-mini-v2"
mcp = _Server(SERVER_NAME)
_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()
_MAX_OUTPUT = 200_000


def _split_roots(name: str, fallback: str = "") -> list[Path]:
    raw = os.environ.get(name, fallback).strip()
    if raw == "*":
        roots: list[Path] = []
        if os.name == "nt":
            import string
            for letter in string.ascii_uppercase:
                p = Path(letter + ":\\")
                try:
                    if p.exists():
                        roots.append(p.resolve())
                except OSError:
                    pass
        else:
            roots.append(Path("/"))
        return roots
    roots = []
    for item in raw.split(";"):
        if item.strip():
            try:
                roots.append(Path(item.strip()).expanduser().resolve())
            except OSError:
                pass
    return roots


def _read_roots() -> list[Path]:
    return _split_roots("MINI_MCP_READ_ROOTS", os.environ.get("MINI_MCP_ROOTS", "*"))


def _write_roots() -> list[Path]:
    return _split_roots("MINI_MCP_WRITE_ROOTS", "")


def _inside(path: Path, roots: list[Path]) -> Path:
    p = path.expanduser().resolve()
    for root in roots:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    raise PermissionError(f"Path outside allowed roots: {p}")


def _read_allowed(path: str | os.PathLike[str]) -> Path:
    return _inside(Path(path), _read_roots())


def _write_allowed(path: str | os.PathLike[str]) -> Path:
    roots = _write_roots()
    if not roots:
        raise PermissionError("No MINI_MCP_WRITE_ROOTS configured")
    return _inside(Path(path), roots)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_state(path: Path) -> dict[str, Any] | None:
    if not path.exists() or not path.is_file():
        return None
    st = path.stat()
    return {"sha256": _sha256_file(path), "bytes": st.st_size, "mtime_ns": st.st_mtime_ns}


def _result(ok: bool, **kwargs: Any) -> dict[str, Any]:
    return {"ok": ok, **kwargs}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.birdeye-", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _canonical_command_hash(executable: str, args: list[str], cwd: str | None) -> str:
    payload = json.dumps(
        {"executable": executable, "args": args, "cwd": cwd},
        sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _resolve_executable(executable: str) -> str:
    if os.path.isabs(executable):
        p = Path(executable)
        if not p.is_file():
            raise FileNotFoundError(f"Executable not found: {executable}")
        resolved = str(p.resolve())
    else:
        found = shutil.which(executable)
        if not found:
            raise FileNotFoundError(f"Executable not found in PATH: {executable}")
        resolved = str(Path(found).resolve())
    allow_raw = os.environ.get("MINI_MCP_EXEC_ALLOW", "").strip()
    if allow_raw:
        allowed = {x.strip().lower() for x in allow_raw.split(";") if x.strip()}
        candidates = {resolved.lower(), Path(resolved).name.lower(), executable.lower()}
        if candidates.isdisjoint(allowed):
            raise PermissionError(f"Executable not allowed: {resolved}")
    return resolved


@mcp.tool()
def health() -> dict[str, Any]:
    return _result(
        True,
        server=SERVER_NAME,
        pid=os.getpid(),
        python=sys.executable,
        python_version=sys.version.split()[0],
        cwd=os.getcwd(),
        read_roots=[str(x) for x in _read_roots()],
        write_roots=[str(x) for x in _write_roots()],
        exec_allow=[x for x in os.environ.get("MINI_MCP_EXEC_ALLOW", "").split(";") if x],
    )


@mcp.tool()
def system_info() -> dict[str, Any]:
    import platform
    elevated = False
    if os.name == "nt":
        try:
            import ctypes
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            pass
    return _result(
        True,
        platform=platform.platform(),
        hostname=platform.node(),
        user=os.environ.get("USERNAME") or os.environ.get("USER"),
        elevated=elevated,
        executable=sys.executable,
        cwd=os.getcwd(),
    )


@mcp.tool()
def list_drives() -> dict[str, Any]:
    return _result(True, drives=[str(x) for x in _read_roots()])


@mcp.tool()
def list_dir(path: str, depth: int = 1, max_entries: int = 1000) -> dict[str, Any]:
    base = _read_allowed(path)
    if not base.is_dir():
        return _result(False, error=f"Not a directory: {base}")
    depth = max(1, min(int(depth), 8))
    max_entries = max(1, min(int(max_entries), 10_000))
    rows: list[dict[str, Any]] = []

    def walk(cur: Path, level: int) -> None:
        if level > depth or len(rows) >= max_entries:
            return
        try:
            children = sorted(cur.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError as exc:
            rows.append({"path": str(cur), "error": str(exc)})
            return
        for child in children:
            if len(rows) >= max_entries:
                break
            try:
                st = child.stat()
                rows.append({
                    "path": str(child),
                    "type": "dir" if child.is_dir() else "file",
                    "bytes": None if child.is_dir() else st.st_size,
                    "mtime_ns": st.st_mtime_ns,
                })
            except OSError as exc:
                rows.append({"path": str(child), "error": str(exc)})
            if child.is_dir():
                walk(child, level + 1)

    walk(base, 1)
    return _result(True, root=str(base), entries=rows, truncated=len(rows) >= max_entries)


@mcp.tool()
def stat_path(path: str) -> dict[str, Any]:
    p = _read_allowed(path)
    if not p.exists():
        return _result(False, error=f"Path does not exist: {p}")
    st = p.stat()
    return _result(
        True,
        path=str(p),
        type="dir" if p.is_dir() else "file",
        bytes=None if p.is_dir() else st.st_size,
        mtime_ns=st.st_mtime_ns,
        ctime_ns=st.st_ctime_ns,
        readonly=not os.access(p, os.W_OK),
    )


@mcp.tool()
def hash_file(path: str) -> dict[str, Any]:
    p = _read_allowed(path)
    if not p.is_file():
        return _result(False, error=f"Not a file: {p}")
    return _result(True, path=str(p), algorithm="sha256", sha256=_sha256_file(p), bytes=p.stat().st_size)


@mcp.tool()
def read_text(path: str, max_chars: int = 200_000) -> dict[str, Any]:
    p = _read_allowed(path)
    if not p.is_file():
        return _result(False, error=f"Not a file: {p}")
    max_chars = max(1, min(int(max_chars), 2_000_000))
    data = p.read_text(encoding="utf-8", errors="replace")
    return _result(True, path=str(p), text=data[:max_chars], truncated=len(data) > max_chars)


@mcp.tool()
def read_lines(path: str, start_line: int = 1, end_line: int = 200) -> dict[str, Any]:
    p = _read_allowed(path)
    if not p.is_file():
        return _result(False, error=f"Not a file: {p}")
    start = max(1, int(start_line))
    end = max(start, min(int(end_line), start + 5000))
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[start - 1:end]
    return _result(True, path=str(p), start_line=start, end_line=start + len(selected) - 1, lines=selected)


@mcp.tool()
def find_files(root: str, pattern: str = "*", max_results: int = 500) -> dict[str, Any]:
    base = _read_allowed(root)
    if not base.is_dir():
        return _result(False, error=f"Not a directory: {base}")
    limit = max(1, min(int(max_results), 5000))
    matches = []
    for cur, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        for name in files:
            if fnmatch.fnmatch(name, pattern):
                matches.append(str(Path(cur) / name))
                if len(matches) >= limit:
                    return _result(True, root=str(base), matches=matches, truncated=True)
    return _result(True, root=str(base), matches=matches, truncated=False)


@mcp.tool()
def search_text(
    root: str,
    query: str,
    glob: str = "*",
    case_sensitive: bool = False,
    max_results: int = 200,
    max_file_bytes: int = 2_000_000,
) -> dict[str, Any]:
    base = _read_allowed(root)
    if not base.is_dir():
        return _result(False, error=f"Not a directory: {base}")
    if not query:
        return _result(False, error="query must not be empty")
    limit = max(1, min(int(max_results), 2000))
    needle = query if case_sensitive else query.lower()
    matches: list[dict[str, Any]] = []
    files_scanned = 0
    for cur, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__"}]
        for name in files:
            if not fnmatch.fnmatch(name, glob):
                continue
            p = Path(cur) / name
            try:
                if p.stat().st_size > max_file_bytes:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            files_scanned += 1
            for number, line in enumerate(text.splitlines(), 1):
                hay = line if case_sensitive else line.lower()
                if needle in hay:
                    matches.append({"path": str(p), "line": number, "text": line[:2000]})
                    if len(matches) >= limit:
                        return _result(True, root=str(base), matches=matches, files_scanned=files_scanned, truncated=True)
    return _result(True, root=str(base), matches=matches, files_scanned=files_scanned, truncated=False)


@mcp.tool()
def write_text(
    path: str,
    text: str,
    overwrite: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    p = _write_allowed(path)
    before = _file_state(p)
    if p.exists() and not overwrite:
        return _result(False, error=f"File exists; set overwrite=true: {p}")
    if expected_sha256 is not None:
        actual = before["sha256"] if before else None
        if actual != expected_sha256:
            return _result(False, error="WRITE_CONFLICT", expected_sha256=expected_sha256, actual_sha256=actual)
    _atomic_write(p, text)
    return _result(True, operation="write_text", path=str(p), before=before, after=_file_state(p), changed=True)


@mcp.tool()
def patch_text(
    path: str,
    expected_old_text: str,
    replacement_text: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    p = _write_allowed(path)
    if not p.is_file():
        return _result(False, error=f"Not a file: {p}")
    before = _file_state(p)
    if expected_sha256 is not None and before and before["sha256"] != expected_sha256:
        return _result(False, error="WRITE_CONFLICT", expected_sha256=expected_sha256, actual_sha256=before["sha256"])
    data = p.read_text(encoding="utf-8", errors="strict")
    count = data.count(expected_old_text)
    if count == 0:
        return _result(False, error="EXPECTED_TEXT_NOT_FOUND")
    if count != 1:
        return _result(False, error="EXPECTED_TEXT_NOT_UNIQUE", occurrences=count)
    _atomic_write(p, data.replace(expected_old_text, replacement_text, 1))
    return _result(True, operation="patch_text", path=str(p), before=before, after=_file_state(p), changed=True)


def _spawn(executable: str, args: list[str], cwd: str | None, stdout_target, stderr_target):
    resolved = _resolve_executable(executable)
    workdir = str(_read_allowed(cwd)) if cwd else None
    proc = subprocess.Popen(
        [resolved, *args],
        cwd=workdir,
        stdin=subprocess.DEVNULL,
        stdout=stdout_target,
        stderr=stderr_target,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    return proc, resolved, workdir


@mcp.tool()
def run_process(
    executable: str,
    args: list[str] | None = None,
    cwd: str | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    args = args or []
    timeout = max(1, min(int(timeout_seconds), 600))
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        proc, resolved, workdir = _spawn(executable, args, cwd, subprocess.PIPE, subprocess.PIPE)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timed_out = True
        return _result(
            proc.returncode == 0 and not timed_out,
            executable=resolved,
            args=args,
            cwd=workdir,
            pid=proc.pid,
            exit_code=proc.returncode,
            timed_out=timed_out,
            duration_ms=int((time.monotonic() - started) * 1000),
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            command_sha256=_canonical_command_hash(resolved, args, workdir),
            stdout=(stdout or "")[-_MAX_OUTPUT:],
            stderr=(stderr or "")[-_MAX_OUTPUT:],
        )
    except Exception as exc:
        return _result(False, error=repr(exc), duration_ms=int((time.monotonic() - started) * 1000))


@mcp.tool()
def process_start(executable: str, args: list[str] | None = None, cwd: str | None = None) -> dict[str, Any]:
    args = args or []
    try:
        job_id = f"bird-job-{uuid.uuid4().hex[:12]}"
        job_dir = Path(tempfile.gettempdir()) / "birdeye-local-mini-jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=False)
        out_path, err_path = job_dir / "stdout.log", job_dir / "stderr.log"
        out = out_path.open("w", encoding="utf-8")
        err = err_path.open("w", encoding="utf-8")
        proc, resolved, workdir = _spawn(executable, args, cwd, out, err)
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "proc": proc, "stdout_handle": out, "stderr_handle": err,
                "stdout_path": out_path, "stderr_path": err_path,
                "executable": resolved, "args": args, "cwd": workdir,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "command_sha256": _canonical_command_hash(resolved, args, workdir),
            }
        return _result(True, job_id=job_id, pid=proc.pid, executable=resolved, args=args, cwd=workdir)
    except Exception as exc:
        return _result(False, error=repr(exc))


def _job(job_id: str) -> dict[str, Any]:
    with _JOBS_LOCK:
        item = _JOBS.get(job_id)
    if not item:
        raise KeyError("UNKNOWN_JOB")
    return item


@mcp.tool()
def process_status(job_id: str) -> dict[str, Any]:
    try:
        item = _job(job_id)
        proc = item["proc"]
        code = proc.poll()
        return _result(True, job_id=job_id, pid=proc.pid, running=code is None, exit_code=code, started_at=item["started_at"])
    except Exception as exc:
        return _result(False, error=str(exc))


@mcp.tool()
def process_output(job_id: str, max_chars: int = 100_000) -> dict[str, Any]:
    try:
        item = _job(job_id)
        for key in ("stdout_handle", "stderr_handle"):
            try:
                item[key].flush()
            except Exception:
                pass
        limit = max(1, min(int(max_chars), _MAX_OUTPUT))
        stdout = item["stdout_path"].read_text(encoding="utf-8", errors="replace")
        stderr = item["stderr_path"].read_text(encoding="utf-8", errors="replace")
        return _result(True, job_id=job_id, stdout=stdout[-limit:], stderr=stderr[-limit:], running=item["proc"].poll() is None)
    except Exception as exc:
        return _result(False, error=str(exc))


@mcp.tool()
def process_stop(job_id: str) -> dict[str, Any]:
    try:
        item = _job(job_id)
        proc = item["proc"]
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        return _result(True, job_id=job_id, exit_code=proc.returncode, stopped=True)
    except Exception as exc:
        return _result(False, error=str(exc))


if __name__ == "__main__":
    print(f"[{SERVER_NAME}] starting stdio MCP on {sys.executable}", file=sys.stderr)
    mcp.run(transport="stdio")
