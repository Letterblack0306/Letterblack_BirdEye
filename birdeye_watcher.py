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
import sys
import time
import uuid
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

import agent
from agent import Context, matches_any, path_allowed


def _virtual(root, relative: Path) -> str:
    posix = relative.as_posix()
    return root.name if posix == "." else f"{root.name}/{posix}"


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
        print(f"[watcher] {result.get('action', 'skip')} {virtual}", flush=True)

    def _remove(self, src_path: str) -> None:
        root, relative = self._resolve(src_path)
        if root is None or relative is None:
            return
        virtual = _virtual(root, relative)
        result = agent.index_file_event(
            self.ctx, root, Path(src_path), virtual, deleted=True, run_id=self.run_id
        )
        print(f"[watcher] deleted {virtual}", flush=True)

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
        print(f"[watcher] no roots selected. Available: {', '.join(r.name for r in ctx.roots)}", flush=True)
        return 1

    run_id = f"watcher:{uuid.uuid4().hex}"
    handler = _IndexHandler(ctx, run_id)
    observer = Observer()

    for root in roots:
        observer.schedule(handler, str(root.path), recursive=True)
        print(f"[watcher] watching {root.name} ({root.path})", flush=True)

    observer.daemon = True
    observer.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[watcher] stopping", flush=True)
        observer.stop()
    observer.join()
    return 0


if __name__ == "__main__":
    sys.exit(main())
