"""BirdEye live incremental index watcher.

Watches configured knowledge roots with watchdog and keeps the single
SQLite index (state/workspace.db) current while the MCP server runs. It never
builds a second index or a second search implementation: every event is routed
to agent.index_file_event(), the same write path used by `trace`.

Run alongside the MCP server:
    python birdeye_watcher.py [--roots name1,name2]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import uuid
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import agent
from agent import Context, matches_any, path_allowed
from eye_database import record_file_event


class _WatcherLease:
    """Process-scoped exclusive lease for one BirdEye state/index."""

    def __init__(self, state_root: Path) -> None:
        self.path = state_root / "watcher.lock"
        self.handle = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.handle.close()
                self.handle = None
                return False
        else:
            import fcntl
            try:
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.handle.close()
                self.handle = None
                return False
        self.handle.write(f"pid={os.getpid()}\n".encode("ascii"))
        self.handle.flush()
        return True

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def _virtual(root, relative: Path) -> str:
    posix = relative.as_posix()
    return root.name if posix == "." else f"{root.name}/{posix}"


def _is_birdeye_database_artifact(path: Path) -> bool:
    """Return whether *path* is BirdEye's generated database state."""
    resolved = Path(path).resolve()
    name = resolved.name.lower()
    if not name.endswith((".db", ".db-wal", ".db-shm")):
        return False
    database_roots = (
        (agent.ROOT / "eye_Databa").resolve(),
        agent.STATE_DIR.resolve(),
    )
    return any(
        resolved == database_root or database_root in resolved.parents
        for database_root in database_roots
    )


def _governed(ctx: Context, root, relative: Path, virtual: str) -> bool:
    forbidden = list(ctx.governance.get("forbidden_globs", []))
    allowed = list(ctx.governance.get("allowed_read_paths", ["."]))
    relative_text = str(relative).replace("\\", "/")
    if matches_any(virtual, forbidden) or matches_any(relative_text, forbidden):
        return False
    return path_allowed(relative_text, allowed)


class _IndexHandler(FileSystemEventHandler):
    def __init__(self, ctx: Context, run_id: str) -> None:
        self.ctx = ctx
        self.run_id = run_id
        self.roots = ctx.roots

    def _resolve(self, path: str):
        resolved = Path(path).resolve()
        if _is_birdeye_database_artifact(resolved):
            return None, None
        for root in self.roots:
            try:
                relative = resolved.relative_to(root.path)
            except ValueError:
                if root.root_class != "memory":
                    continue
                for source in root.sources:
                    try:
                        source_relative = resolved.relative_to(source.path)
                    except ValueError:
                        continue
                    return root, Path("sources") / source.name / source_relative
                continue
            return root, relative
        return None, None

    def _index(self, src_path: str, dest_path: str | None = None) -> None:
        target = dest_path or src_path
        root, relative = self._resolve(target)
        if root is None or relative is None:
            return
        virtual = _virtual(root, relative)
        if not _governed(self.ctx, root, relative, virtual):
            return
        result = agent.index_file_event(
            self.ctx, root, Path(target), virtual, run_id=self.run_id
        )
        try:
            eye_result = record_file_event(target, "modified")
        except (OSError, RuntimeError, ValueError) as exc:
            eye_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(f"[watcher] {result.get('action', 'skip')} {virtual}", file=sys.stderr, flush=True)
        if eye_result.get("ok") is False:
            print(f"[eye] persistence failed for {virtual}: {eye_result.get('error', 'unknown error')}", file=sys.stderr, flush=True)
        self._update_domain_query(target, root, virtual, "modified")

    def _remove(self, src_path: str) -> None:
        root, relative = self._resolve(src_path)
        if root is None or relative is None:
            return
        virtual = _virtual(root, relative)
        result = agent.index_file_event(
            self.ctx, root, Path(src_path), virtual, deleted=True, run_id=self.run_id
        )
        try:
            eye_result = record_file_event(src_path, "deleted")
        except (OSError, RuntimeError, ValueError) as exc:
            eye_result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print(f"[watcher] deleted {virtual}", file=sys.stderr, flush=True)
        if eye_result.get("ok") is False:
            print(f"[eye] persistence failed for {virtual}: {eye_result.get('error', 'unknown error')}", file=sys.stderr, flush=True)
        self._update_domain_query(Path(src_path), root, virtual, "deleted")

    def _update_domain_query(self, path: Path, root, virtual: str, event: str) -> None:
        """Keep the EYES query projection current by replaying the canonical ledger."""
        try:
            if root.root_class == "memory":
                # Memory and skills vector stores are refreshed by their
                # canonical Memory/Skills indexers. Do not project heterogeneous
                # source formats into the workspace query schema here; the
                # canonical event is already persisted in eye_*_data_01.db.
                return
            domain = "skills" if root.name == "skills" else "workspace"
            from eye_query import project_pending_changes
            result = project_pending_changes(domain)
            if result.get("ok") is not True:
                print(f"[eye] query projection reported failure for {virtual}: {result.get('error', 'unknown')}", file=sys.stderr, flush=True)
        except (OSError, RuntimeError, ValueError, ImportError) as exc:
            print(f"[eye] query projection failed for {virtual}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)

    def on_created(self, event) -> None:
        if not event.is_directory:
            self._index(event.src_path)

    def on_modified(self, event) -> None:
        if not event.is_directory:
            self._index(event.src_path)

    def on_deleted(self, event) -> None:
        if not event.is_directory:
            self._remove(event.src_path)

    def on_moved(self, event) -> None:
        if event.is_directory:
            return
        self._remove(event.src_path)
        self._index(event.dest_path)


def start_watcher(ctx: Context, selected: set[str] | None = None):
    """Start the incremental watcher and return its observer handle.

    The MCP server uses this lifecycle hook so BirdEye starts watching only
    when the stdio server is launched, rather than requiring a separate
    permanently running process.
    """
    selected = selected or {root.name for root in ctx.roots}
    roots = [root for root in ctx.roots if root.name in selected and root.path.is_dir()]
    if not roots:
        raise ValueError(f"no roots selected. Available: {', '.join(r.name for r in ctx.roots)}")

    state_root = Path(os.environ.get("BIRDEYE_STATE_ROOT", str(agent.STATE_DIR))).resolve()
    lease = _WatcherLease(state_root)
    if not lease.acquire():
        return None

    run_id = f"watcher:{uuid.uuid4().hex}"
    handler = _IndexHandler(ctx, run_id)
    observer = Observer()
    for root in roots:
        observer.schedule(handler, str(root.path), recursive=True)
    observer.daemon = True
    observer.start()
    return observer, lease


def stop_watcher(observer) -> None:
    """Stop an observer previously returned by start_watcher."""
    if observer is None:
        return
    actual, lease = observer
    actual.stop()
    actual.join(timeout=5)
    lease.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BirdEye live incremental index watcher")
    parser.add_argument("--roots", help="comma-separated root names to watch (default: all)")
    args = parser.parse_args(argv)

    ctx = Context.load()
    selected = (
        {name.strip() for name in args.roots.split(",") if name.strip()}
        if args.roots
        else {root.name for root in ctx.roots}
    )
    roots = [root for root in ctx.roots if root.name in selected]
    if not roots:
        print(f"[watcher] no roots selected. Available: {', '.join(r.name for r in ctx.roots)}", file=sys.stderr, flush=True)
        return 1

    watcher = start_watcher(ctx, selected)
    if watcher is None:
        print("[watcher] another BirdEye watcher already owns this state/index", file=sys.stderr, flush=True)
        return 0
    observer, _lease = watcher
    for root in roots:
        print(f"[watcher] watching {root.name} ({root.path})", file=sys.stderr, flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[watcher] stopping", file=sys.stderr, flush=True)
        stop_watcher(observer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
