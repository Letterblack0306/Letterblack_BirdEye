from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_ALLOWED_STATUS = {"waiting", "executing", "stopped"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LoopRegistry:
    """Persistent registry for ChatGPT loop endpoints.

    This registry owns only loop/thread routing and runtime cursor state.
    BirdEye's indexed workspace metadata and SHA-256 records remain authoritative
    in their existing stores and are intentionally not duplicated here.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _empty(self) -> dict[str, Any]:
        return {"version": 1, "loops": {}}

    def _read(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return self._empty()
        if not isinstance(data, dict) or not isinstance(data.get("loops"), dict):
            return self._empty()
        data.setdefault("version", 1)
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _validate_thread_url(thread_url: str) -> str:
        value = str(thread_url or "").strip()
        if not value:
            raise ValueError("thread_url is required")
        if not (value.startswith("https://chatgpt.com/") or value.startswith("https://chat.openai.com/")):
            raise ValueError("thread_url must be an exact ChatGPT conversation URL")
        return value

    @staticmethod
    def _validate_status(status: str | None) -> str:
        value = str(status or "waiting").strip().lower()
        if value not in _ALLOWED_STATUS:
            raise ValueError(f"status must be one of: {', '.join(sorted(_ALLOWED_STATUS))}")
        return value

    def register(
        self,
        *,
        loop_id: str | None,
        thread_url: str,
        browser_profile: str | None = None,
        cdp_endpoint: str | None = None,
        page_id: str | None = None,
        workspace: str | None = None,
        status: str = "waiting",
    ) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            now = _utc_now()
            loop_id = str(loop_id or "").strip() or f"loop_{uuid.uuid4().hex[:12]}"
            entry = {
                "loop_id": loop_id,
                "thread_url": self._validate_thread_url(thread_url),
                "browser_profile": browser_profile or None,
                "cdp_endpoint": cdp_endpoint or None,
                "page_id": page_id or None,
                "workspace": workspace or None,
                "status": self._validate_status(status),
                "last_message_hash": None,
                "last_action_id": None,
                "created_at": data["loops"].get(loop_id, {}).get("created_at", now),
                "updated_at": now,
            }
            data["loops"][loop_id] = entry
            self._write(data)
            return {"ok": True, "loop": entry, "path": str(self.path)}

    def get(self, loop_id: str) -> dict[str, Any]:
        with self._lock:
            key = str(loop_id or "").strip()
            entry = self._read()["loops"].get(key)
            if entry is None:
                return {"ok": False, "error": "LOOP_NOT_FOUND", "loop_id": key}
            return {"ok": True, "loop": entry, "path": str(self.path)}

    def list(self, *, status: str | None = None) -> dict[str, Any]:
        with self._lock:
            data = self._read()
            wanted = self._validate_status(status) if status else None
            loops = list(data["loops"].values())
            if wanted:
                loops = [entry for entry in loops if entry.get("status") == wanted]
            loops.sort(key=lambda entry: (entry.get("updated_at", ""), entry.get("loop_id", "")))
            return {
                "ok": True,
                "count": len(loops),
                "loops": loops,
                "path": str(self.path),
            }

    def update(self, loop_id: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            key = str(loop_id or "").strip()
            data = self._read()
            entry = data["loops"].get(key)
            if entry is None:
                return {"ok": False, "error": "LOOP_NOT_FOUND", "loop_id": key}

            if changes.get("thread_url") is not None:
                entry["thread_url"] = self._validate_thread_url(changes["thread_url"])
            if changes.get("status") is not None:
                entry["status"] = self._validate_status(changes["status"])

            for field in (
                "browser_profile",
                "cdp_endpoint",
                "page_id",
                "workspace",
                "last_message_hash",
                "last_action_id",
            ):
                if changes.get(field) is not None:
                    entry[field] = str(changes[field])
            entry["updated_at"] = _utc_now()
            data["loops"][key] = entry
            self._write(data)
            return {"ok": True, "loop": entry, "path": str(self.path)}

    def remove(self, loop_id: str) -> dict[str, Any]:
        with self._lock:
            key = str(loop_id or "").strip()
            data = self._read()
            if key not in data["loops"]:
                return {"ok": False, "error": "LOOP_NOT_FOUND", "loop_id": key}
            removed = data["loops"].pop(key)
            self._write(data)
            return {"ok": True, "removed": removed, "path": str(self.path)}
