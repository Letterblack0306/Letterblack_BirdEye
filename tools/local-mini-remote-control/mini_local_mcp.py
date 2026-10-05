#!/usr/bin/env python
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer as _Server
except Exception:
    from mcp.server.fastmcp import FastMCP as _Server

SERVER_NAME = "letterblack-local-mini"
DEFAULT_ROOT = Path(__file__).resolve().parent
mcp = _Server(SERVER_NAME)


def _result(ok: bool, **kwargs: Any) -> dict[str, Any]:
    return {"ok": ok, **kwargs}


def _roots() -> list[Path]:
    raw = os.environ.get("MINI_MCP_ROOTS", str(DEFAULT_ROOT)).strip()

    if raw == "*":
        roots: list[Path] = []
        if os.name == "nt":
            import string
            for letter in string.ascii_uppercase:
                p = Path(letter + ":\\")
                try:
                    if p.exists():
                        roots.append(p.resolve())
                except Exception:
                    pass
        else:
            roots.append(Path("/"))
        return roots

    roots: list[Path] = []
    for item in raw.split(";"):
        item = item.strip()
        if not item:
            continue
        roots.append(Path(item).expanduser().resolve())
    if not roots:
        roots.append(DEFAULT_ROOT)
    return roots


def _allowed(path: str | os.PathLike[str]) -> Path:
    p = Path(path).expanduser().resolve()
    roots = _roots()
    for root in roots:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    raise PermissionError(
        f"Path is outside MINI_MCP_ROOTS: {p}. Allowed roots: "
        + ", ".join(str(r) for r in roots)
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


@mcp.tool()
def health() -> dict[str, Any]:
    """Return runtime identity and the effective allowed roots."""
    return _result(
        True,
        server=SERVER_NAME,
        pid=os.getpid(),
        python=sys.executable,
        python_version=sys.version.split()[0],
        cwd=os.getcwd(),
        roots=[str(r) for r in _roots()],
    )


@mcp.tool()
def system_info() -> dict[str, Any]:
    """Return local OS/process identity without changing state."""
    import platform
    elevated = False
    if os.name == "nt":
        try:
            import ctypes
            elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            elevated = False
    return _result(
        True,
        platform=platform.platform(),
        hostname=platform.node(),
        user=os.environ.get("USERNAME") or os.environ.get("USER"),
        elevated=elevated,
        executable=sys.executable,
        cwd=os.getcwd(),
        roots=[str(r) for r in _roots()],
    )


@mcp.tool()
def list_drives() -> dict[str, Any]:
    """Return the filesystem roots authorized for this MCP process."""
    return _result(True, drives=[str(r) for r in _roots()])


@mcp.tool()
def list_dir(path: str, depth: int = 1) -> dict[str, Any]:
    """List files/directories under an allowed path."""
    base = _allowed(path)
    if not base.is_dir():
        return _result(False, error=f"Not a directory: {base}")

    depth = max(1, min(int(depth), 5))
    rows: list[dict[str, Any]] = []

    def walk(cur: Path, level: int) -> None:
        if level > depth:
            return
        try:
            children = sorted(cur.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except Exception as exc:
            rows.append({"path": str(cur), "error": str(exc)})
            return
        for child in children[:500]:
            try:
                rows.append({
                    "path": str(child),
                    "type": "dir" if child.is_dir() else "file",
                    "bytes": None if child.is_dir() else child.stat().st_size,
                })
            except Exception as exc:
                rows.append({"path": str(child), "error": str(exc)})
            if child.is_dir():
                walk(child, level + 1)

    walk(base, 1)
    return _result(True, root=str(base), entries=rows)


@mcp.tool()
def read_text(path: str, max_chars: int = 200_000) -> dict[str, Any]:
    """Read text from an allowed file."""
    p = _allowed(path)
    if not p.is_file():
        return _result(False, error=f"Not a file: {p}")
    max_chars = max(1, min(int(max_chars), 2_000_000))
    data = p.read_text(encoding="utf-8", errors="replace")
    return _result(True, path=str(p), text=data[:max_chars], truncated=len(data) > max_chars)


@mcp.tool()
def stat_path(path: str) -> dict[str, Any]:
    """Return metadata for an allowed path."""
    p = _allowed(path)
    if not p.exists():
        return _result(False, error=f"Path does not exist: {p}")
    st = p.stat()
    return _result(
        True,
        path=str(p),
        type="dir" if p.is_dir() else "file",
        bytes=None if p.is_dir() else st.st_size,
        mtime_ns=st.st_mtime_ns,
    )


@mcp.tool()
def file_hash(path: str, algorithm: str = "sha256") -> dict[str, Any]:
    """Return SHA-256 evidence for an allowed file."""
    p = _allowed(path)
    if not p.is_file():
        return _result(False, error=f"Not a file: {p}")
    if algorithm.lower() != "sha256":
        return _result(False, error="Only sha256 is supported")
    return _result(
        True,
        path=str(p),
        algorithm="sha256",
        sha256=_sha256(p),
        bytes=p.stat().st_size,
    )


@mcp.tool()
def write_text(path: str, text: str, overwrite: bool = False) -> dict[str, Any]:
    """Write UTF-8 text under an allowed root."""
    p = _allowed(path)
    if p.exists() and not overwrite:
        return _result(False, error=f"File exists; set overwrite=true: {p}")
    p.parent.mkdir(parents=True, exist_ok=True)
    # newline="" disables the platform newline translation that would otherwise
    # rewrite "\n" as "\r\n" on Windows, so file_hash matches the caller's bytes.
    with p.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return _result(True, path=str(p), bytes=p.stat().st_size, sha256=_sha256(p))


@mcp.tool()
def mkdir(path: str, parents: bool = True, exist_ok: bool = True) -> dict[str, Any]:
    """Create a directory under an allowed root."""
    p = _allowed(path)
    p.mkdir(parents=bool(parents), exist_ok=bool(exist_ok))
    return _result(True, path=str(p))


@mcp.tool()
def copy_file(src: str, dst: str, overwrite: bool = False) -> dict[str, Any]:
    """Copy a file between allowed paths."""
    source = _allowed(src)
    target = _allowed(dst)
    if not source.is_file():
        return _result(False, error=f"Source is not a file: {source}")
    if target.exists() and not overwrite:
        return _result(False, error=f"Target exists; set overwrite=true: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return _result(
        True,
        src=str(source),
        dst=str(target),
        bytes=target.stat().st_size,
        sha256=_sha256(target),
    )


@mcp.tool()
def move_file(src: str, dst: str, overwrite: bool = False) -> dict[str, Any]:
    """Move a file or directory between allowed paths."""
    source = _allowed(src)
    target = _allowed(dst)
    if not source.exists():
        return _result(False, error=f"Source does not exist: {source}")
    if target.exists():
        if not overwrite:
            return _result(False, error=f"Target exists; set overwrite=true: {target}")
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    moved = Path(shutil.move(str(source), str(target)))
    return _result(True, src=str(source), dst=str(moved))


@mcp.tool()
def delete_file(path: str) -> dict[str, Any]:
    """Delete one allowed file and return its pre-delete SHA-256."""
    p = _allowed(path)
    if not p.is_file():
        return _result(False, error=f"Not a file: {p}")
    before = _sha256(p)
    size = p.stat().st_size
    p.unlink()
    return _result(True, path=str(p), bytes_deleted=size, sha256_before=before)


@mcp.tool()
def run_process(
    executable: str,
    args: list[str] | None = None,
    cwd: str | None = None,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    """Run one argv-array process with shell=False. cwd, when set, must be allowed."""
    args = args or []
    workdir = str(_allowed(cwd)) if cwd else None

    exe = executable
    if os.path.isabs(exe):
        if not Path(exe).is_file():
            return _result(False, error=f"Executable not found: {exe}")
    else:
        from shutil import which
        resolved = which(exe)
        if not resolved:
            return _result(False, error=f"Executable not found in PATH: {exe}")
        exe = resolved

    timeout_seconds = max(1, min(int(timeout_seconds), 600))
    try:
        cp = subprocess.run(
            [exe, *args],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
            errors="replace",
        )
        return _result(
            cp.returncode == 0,
            executable=exe,
            args=args,
            cwd=workdir,
            exit_code=cp.returncode,
            stdout=cp.stdout[-200_000:],
            stderr=cp.stderr[-200_000:],
        )
    except subprocess.TimeoutExpired as exc:
        return _result(
            False,
            error=f"Timeout after {timeout_seconds}s",
            stdout=(exc.stdout or "")[-50_000:] if isinstance(exc.stdout, str) else "",
            stderr=(exc.stderr or "")[-50_000:] if isinstance(exc.stderr, str) else "",
        )
    except Exception as exc:
        return _result(False, error=repr(exc))


if __name__ == "__main__":
    print(f"[{SERVER_NAME}] starting stdio MCP on {sys.executable}", file=sys.stderr)
    mcp.run(transport="stdio")
