from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ActivityEvidenceCollector:
    AUTHORITY = "NON_CANONICAL_ACTIVITY_EVIDENCE"
    EVIDENCE_LEVEL = "OBSERVED_ACTIVITY_LOG"

    def __init__(self, config: dict[str, Any] | None):
        config = config if isinstance(config, dict) else {}
        self.enabled = bool(config.get("enabled", False))
        self.max_events = max(1, min(200, int(config.get("maxEvents", 80))))
        self.max_files = max(1, min(50, int(config.get("maxFilesPerSource", 12))))
        self.sources = config.get("sources") if isinstance(config.get("sources"), dict) else {}

    @staticmethod
    def _root(value: str) -> Path:
        return Path(os.path.expandvars(os.path.expanduser(value))).resolve()

    def _recent(self, root: Path, patterns: list[str]) -> list[Path]:
        files: dict[str, Path] = {}
        if root.is_dir():
            for pattern in patterns:
                for path in root.glob(pattern):
                    if path.is_file():
                        files[str(path.resolve()).casefold()] = path.resolve()
        return sorted(files.values(), key=lambda p: p.stat().st_mtime, reverse=True)[:self.max_files]

    @staticmethod
    def _matches(text: str, workspace: Path) -> bool:
        target = str(workspace).replace("\\", "/").casefold()
        observed = text.replace("\\\\", "/").replace("\\", "/").casefold()
        while "//" in observed:
            observed = observed.replace("//", "/")
        return target in observed

    @staticmethod
    def _mtime(path: Path) -> str:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()

    def _codex(self, workspace: Path, cfg: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        root = self._root(str(cfg.get("root", r"%USERPROFILE%\.codex")))
        patterns = cfg.get("patterns") if isinstance(cfg.get("patterns"), list) else ["sessions/**/*.jsonl", "history.jsonl"]
        files = self._recent(root, [str(v) for v in patterns])
        events = []
        for path in files:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_no, line in enumerate(handle, 1):
                    raw = line.strip()
                    if not raw or not self._matches(raw, workspace):
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    timestamp = obj.get("timestamp") or obj.get("ts") or self._mtime(path)
                    event_type = obj.get("type") or obj.get("kind") or obj.get("role") or "CODEX_EVENT"
                    events.append({
                        "source": "codex",
                        "timestamp": str(timestamp),
                        "eventType": str(event_type).upper(),
                        "summary": raw[:1000],
                        "workspaceRoot": str(workspace),
                        "sourcePath": str(path),
                        "sourceLocator": {"line": line_no},
                    })
        return {"source": "codex", "available": root.is_dir(), "root": str(root), "filesConsidered": len(files), "eventsMatched": len(events)}, events

    def _antigravity(self, workspace: Path, cfg: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        root = self._root(str(cfg.get("root", r"%USERPROFILE%\.gemini\antigravity-cli")))
        files = self._recent(root, [str(cfg.get("pattern", "log/cli-*.log"))])
        markers = {
            "HandleUserInput called with text:": "USER_MESSAGE",
            "Created conversation ": "CONVERSATION_CREATED",
            "Surfacing tool confirmation:": "TOOL_CONFIRMATION_REQUESTED",
            "Responding to tool confirmation:": "TOOL_CONFIRMATION_RESPONSE",
            "Stream completed for": "AGENT_STREAM_COMPLETED",
            "agent executor error:": "AGENT_ERROR",
        }
        events = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            if not self._matches(text, workspace):
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                event_type = next((kind for marker, kind in markers.items() if marker.casefold() in line.casefold()), None)
                if event_type:
                    events.append({
                        "source": "antigravity",
                        "timestamp": self._mtime(path),
                        "eventType": event_type,
                        "summary": line[:1000],
                        "workspaceRoot": str(workspace),
                        "sourcePath": str(path),
                        "sourceLocator": {"line": line_no},
                    })
        return {"source": "antigravity", "available": root.is_dir(), "root": str(root), "filesConsidered": len(files), "eventsMatched": len(events)}, events

    def collect(self, workspace_root: Path) -> dict[str, Any]:
        result = {
            "schemaVersion": 1,
            "enabled": self.enabled,
            "authority": self.AUTHORITY,
            "evidenceLevel": self.EVIDENCE_LEVEL,
            "readOnly": True,
            "observedAt": datetime.now(timezone.utc).isoformat(),
            "sources": [],
            "events": [],
        }
        if not self.enabled:
            return result
        workspace = workspace_root.resolve()
        events = []
        for name, method in (("codex", self._codex), ("antigravity", self._antigravity)):
            cfg = self.sources.get(name)
            if isinstance(cfg, dict):
                try:
                    source, found = method(workspace, cfg)
                except OSError as exc:
                    source, found = {"source": name, "available": False, "error": str(exc)}, []
                result["sources"].append(source)
                events.extend(found)
        events.sort(key=lambda item: item["timestamp"], reverse=True)
        result["events"] = events[:self.max_events]
        return result
