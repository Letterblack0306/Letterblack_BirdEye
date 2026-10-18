"""Controlled BirdEye HTTP refresh after verified source changes.

--verify checks only. --apply schedules a detached worker after the invoking
MCP request returns. Worker refuses to terminate a changed or foreign listener.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
PORT = 8766
REPORT = ROOT / "state" / "skill_migrations" / "live_reload.json"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def listener() -> psutil.Process:
    found = [
        c.pid for c in psutil.net_connections(kind="tcp")
        if c.status == psutil.CONN_LISTEN and c.laddr.ip == "127.0.0.1"
        and c.laddr.port == PORT and c.pid is not None
    ]
    if len(found) != 1:
        raise RuntimeError(f"canonical port has {len(found)} listeners; refusing restart")
    proc = psutil.Process(found[0])
    cmd = proc.cmdline()
    if len(cmd) < 2 or Path(cmd[1]).name.lower() != "http_mcp_server.py":
        raise RuntimeError("canonical listener is not the expected BirdEye HTTP service")
    if Path(proc.cwd()).resolve() != ROOT:
        raise RuntimeError("canonical listener working directory differs from tested source")
    if Path(proc.exe()).name.lower() != "python.exe":
        raise RuntimeError("canonical listener is not Python")
    return proc


def health() -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=4) as response:
        result = json.load(response)
    if result.get("server") != "letterblack-birdeye" or result.get("ok") is not True:
        raise RuntimeError("canonical endpoint identity or health mismatch")
    if result.get("auth") != "loopback-only":
        raise RuntimeError("bearer auth changes require a separate explicit deployment procedure")
    return result


def atomic_report(payload: dict) -> None:
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    tmp = REPORT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, REPORT)


def worker(pid: int, expected_bird: str, expected_http: str) -> None:
    report = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "previous_pid": pid, "state": "running", "ok": False,
    }
    try:
        time.sleep(4.0)
        if digest(ROOT / "mcp_server.py") != expected_bird:
            raise RuntimeError("mcp_server.py changed after preflight; refusing restart")
        if digest(ROOT / "http_mcp_server.py") != expected_http:
            raise RuntimeError("http_mcp_server.py changed after preflight; refusing restart")
        existing = listener()
        if existing.pid != pid:
            raise RuntimeError("listener PID changed; refusing restart")
        h = health()
        env = existing.environ()
        binary = existing.exe()
        cwd = existing.cwd()
        report["before_health"] = h
        existing.terminate()
        existing.wait(timeout=12)
        log_path = ROOT / "state" / "skill_migrations" / "live_reload_server.log"
        with log_path.open("ab") as log:
            child = subprocess.Popen(
                [binary, "http_mcp_server.py"], cwd=cwd, env=env,
                stdout=log, stderr=log,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                              | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        report["new_pid"] = child.pid
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"replacement process exited with code {child.returncode}")
            try:
                h = health()
                actual = listener()
                if actual.pid == child.pid:
                    report["after_health"] = h
                    break
            except (OSError, RuntimeError):
                pass
            time.sleep(0.4)
        else:
            raise RuntimeError("replacement HTTP server did not become healthy")
        sys.path.insert(0, str(ROOT / "tools"))
        from smoke_agent_skills_http import exercise
        report["mcp_acceptance"] = asyncio.run(exercise(f"http://127.0.0.1:{PORT}/mcp"))
        report["state"] = "completed"
        report["ok"] = True
    except Exception as exc:
        report["state"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    atomic_report(report)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--worker", action="store_true")
    p.add_argument("--pid", type=int, default=0)
    p.add_argument("--bird-hash", default="")
    p.add_argument("--http-hash", default="")
    args = p.parse_args()
    if args.worker:
        worker(args.pid, args.bird_hash, args.http_hash)
        return
    current = listener()
    observed = health()
    summary = {
        "current_pid": current.pid, "server": observed["server"],
        "auth": observed["auth"],
        "source_hash": digest(ROOT / "mcp_server.py"),
        "http_hash": digest(ROOT / "http_mcp_server.py"),
        "action": "scheduled" if args.apply else "dry_run",
        "report": str(REPORT),
    }
    if args.apply:
        log = ROOT / "state" / "skill_migrations" / "reload_worker.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("ab") as stream:
            child = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), "--worker",
                 "--pid", str(current.pid), "--bird-hash", summary["source_hash"],
                 "--http-hash", summary["http_hash"]],
                cwd=str(ROOT), stdout=stream, stderr=stream,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                              | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
        summary["worker_pid"] = child.pid
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
