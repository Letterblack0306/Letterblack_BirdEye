from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any


class ActivityEvidenceCollector:
    """Collect bounded, read-only activity evidence from local agent runtimes.

    This evidence is observational only. It must never affect BirdEye mutation
    authority, validation verdicts, or completion truth.
    """

    AUTHORITY = "NON_CANONICAL_ACTIVITY_EVIDENCE"
    EVIDENCE_LEVEL = "OBSERVED_ACTIVITY_LOG"

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config if isinstance(config, dict) else {}
        self.enabled = bool(self.config.get("enabled", False))
        self.max_events = max(1, min(500, int(self.config.get("maxEvents", 80))))
        self.max_files = max(1, min(100, int(self.config.get("maxFilesPerSource", 12))))
        self.max_chars = max(120, min(4000, int(self.config.get("maxCharsPerEvent", 1200))))
        self.max_file_bytes = max(64 * 1024, min(8 * 1024 * 1024, int(self.config.get("maxFileBytes", 2 * 1024 * 1024))))
        self.sources = self.config.get("sources", {}) if isinstance(self.config.get("sources", {}), dict) else {}

    def collect(self, workspace_root: Path) -> dict[str, Any]:
        observed_at = datetime.now().astimezone().isoformat()
        if not self.enabled:
            return {
                "schemaVersion": 1,
                "enabled": False,
                "authority": self.AUTHORITY,
                "evidenceLevel": self.EVIDENCE_LEVEL,
                "readOnly": True,
                "observedAt": observed_at,
                "sources": [],
                "events": [],
            }

        workspace = str(workspace_root.resolve())
        source_results: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []

        codex_cfg = self.sources.get("codex") if isinstance(self.sources.get("codex"), dict) else None
        if codex_cfg is not None:
            result, found = self._collect_codex(workspace, codex_cfg)
            source_results.append(result)
            events.extend(found)

        antigravity_cfg = self.sources.get("antigravity") if isinstance(self.sources.get("antigravity"), dict) else None
        if antigravity_cfg is not None:
            result, found = self._collect_antigravity(workspace, antigravity_cfg)
            source_results.append(result)
            events.extend(found)

        events.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        events = events[: self.max_events]

        return {
            "schemaVersion": 1,
            "enabled": True,
            "authority": self.AUTHORITY,
            "evidenceLevel": self.EVIDENCE_LEVEL,
            "readOnly": True,
            "observedAt": observed_at,
            "sources": source_results,
            "events": events,
        }

    def _expand_root(self, value: Any) -> Path | None:
        text = str(value or "").strip()
        if not text:
            return None
        return Path(os.path.expandvars(text)).expanduser().resolve()

    def _recent_files(self, root: Path, pattern: str) -> list[Path]:
        try:
            files = [item for item in root.glob(pattern) if item.is_file()]
        except (OSError, ValueError):
            return []
        files.sort(key=lambda item: item.stat().st_mtime_ns if item.exists() else 0, reverse=True)
        return files[: self.max_files]

    def _read_bounded(self, path: Path) -> str:
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                if size <= self.max_file_bytes:
                    data = handle.read()
                else:
                    head_size = min(256 * 1024, self.max_file_bytes // 4)
                    tail_size = self.max_file_bytes - head_size
                    head = handle.read(head_size)
                    handle.seek(max(0, size - tail_size))
                    tail = handle.read(tail_size)
                    data = head + b"\n<BIRDEYE_TRUNCATED>\n" + tail
            return data.decode("utf-8", errors="replace")
        except OSError:
            return ""

    @staticmethod
    def _workspace_matches(text: str, workspace: str) -> bool:
        if not text or not workspace:
            return False
        normalized_text = text.replace("/", "\\").casefold()
        normalized_workspace = workspace.replace("/", "\\").casefold()
        return normalized_workspace in normalized_text

    def _source_status(self, source: str, root: Path | None, files: list[Path], matched: int) -> dict[str, Any]:
        return {
            "source": source,
            "configured": root is not None,
            "available": bool(root and root.is_dir()),
            "root": f"<{source}-runtime-root>" if root else None,
            "filesInspected": len(files),
            "workspaceMatchedFiles": matched,
        }

    def _collect_codex(self, workspace: str, config: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        root = self._expand_root(config.get("root"))
        if root is None or not root.is_dir():
            return self._source_status("codex", root, [], 0), []

        patterns = config.get("patterns", ["sessions/**/*.jsonl", "history.jsonl"])
        if not isinstance(patterns, list):
            patterns = ["sessions/**/*.jsonl", "history.jsonl"]
        files: list[Path] = []
        seen: set[Path] = set()
        for pattern in patterns:
            for item in self._recent_files(root, str(pattern)):
                if item not in seen:
                    files.append(item)
                    seen.add(item)
        files.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
        files = files[: self.max_files]

        events: list[dict[str, Any]] = []
        matched = 0
        for file_path in files:
            text = self._read_bounded(file_path)
            if not self._workspace_matches(text, workspace):
                continue
            matched += 1
            relative = file_path.relative_to(root).as_posix()
            for line_number, line in enumerate(text.splitlines(), 1):
                event = self._codex_event(line, relative, line_number)
                if event is not None:
                    events.append(event)
        return self._source_status("codex", root, files, matched), events

    def _codex_event(self, line: str, relative_path: str, line_number: int) -> dict[str, Any] | None:
        stripped = line.strip()
        if not stripped or stripped == "<BIRDEYE_TRUNCATED>":
            return None
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if not isinstance(record, dict):
            return None
        raw = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        lowered = raw.casefold()
        event_type = "RUNTIME_EVENT"
        if any(token in lowered for token in ('"role":"user"', '"type":"user"', 'user_message')):
            event_type = "USER_MESSAGE"
        elif any(token in lowered for token in ('tool_call', 'function_call', 'command_execution', 'shell_command')):
            event_type = "TOOL_REQUEST"
        elif any(token in lowered for token in ('tool_result', 'function_result', 'command_output')):
            event_type = "TOOL_RESULT"
        elif any(token in lowered for token in ('"role":"assistant"', 'assistant_message')):
            event_type = "AGENT_MESSAGE"
        elif not any(token in lowered for token in ('message', 'tool', 'command', 'event', 'response', 'request')):
            return None

        timestamp = record.get("timestamp") or record.get("ts") or record.get("time") or record.get("created_at")
        return {
            "source": "codex",
            "eventType": event_type,
            "timestamp": str(timestamp) if timestamp is not None else None,
            "summary": self._sanitize(self._extract_summary(record)),
            "sourcePath": relative_path,
            "sourceLine": line_number,
        }

    def _collect_antigravity(self, workspace: str, config: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        root = self._expand_root(config.get("root"))
        if root is None or not root.is_dir():
            return self._source_status("antigravity", root, [], 0), []
        pattern = str(config.get("pattern", "log/cli-*.log"))
        files = self._recent_files(root, pattern)
        events: list[dict[str, Any]] = []
        matched = 0
        for file_path in files:
            text = self._read_bounded(file_path)
            if not self._workspace_matches(text, workspace):
                continue
            matched += 1
            relative = file_path.relative_to(root).as_posix()
            for line_number, line in enumerate(text.splitlines(), 1):
                event = self._antigravity_event(line, relative, line_number, file_path.name)
                if event is not None:
                    events.append(event)
        return self._source_status("antigravity", root, files, matched), events

    def _antigravity_event(self, line: str, relative_path: str, line_number: int, file_name: str) -> dict[str, Any] | None:
        event_type: str | None = None
        summary: str | None = None
        conversation_id: str | None = None

        if "HandleUserInput called with text:" in line:
            event_type = "USER_MESSAGE"
            summary = line.split("HandleUserInput called with text:", 1)[1].strip()
        elif "Created conversation " in line:
            event_type = "CONVERSATION_CREATED"
            summary = line.split("Created conversation ", 1)[1].strip()
            conversation_id = summary.split()[0] if summary else None
        elif "Surfacing tool confirmation:" in line:
            event_type = "TOOL_REQUEST"
            summary = line.split("Surfacing tool confirmation:", 1)[1].strip()
        elif "Responding to tool confirmation:" in line:
            event_type = "TOOL_APPROVAL"
            summary = line.split("Responding to tool confirmation:", 1)[1].strip()
            match = re.search(r"convID=([^,\s]+)", summary)
            conversation_id = match.group(1) if match else None
        elif "Tool confirmation for conversation " in line:
            event_type = "TOOL_APPROVAL"
            summary = line.split("Tool confirmation for conversation ", 1)[1].strip()
            conversation_id = summary.split()[0] if summary else None
        elif "Forwarding user message to conversation " in line:
            event_type = "RUNTIME_EVENT"
            summary = line.split("Forwarding user message to conversation ", 1)[1].strip()
            conversation_id = summary.split()[0] if summary else None
        elif "Starting new conversation" in line or "Starting conversation update stream for " in line:
            event_type = "RUNTIME_EVENT"
            summary = line.strip()
        else:
            return None

        return {
            "source": "antigravity",
            "eventType": event_type,
            "timestamp": self._antigravity_timestamp(line, file_name),
            "conversationId": conversation_id,
            "summary": self._sanitize(summary or ""),
            "sourcePath": relative_path,
            "sourceLine": line_number,
        }

    @staticmethod
    def _antigravity_timestamp(line: str, file_name: str) -> str | None:
        file_match = re.search(r"cli-(\d{4})(\d{2})(\d{2})_", file_name)
        line_match = re.search(r"\b[IEWF](\d{2})(\d{2})\s+(\d{2}:\d{2}:\d{2}(?:\.\d+)?)", line)
        if not file_match or not line_match:
            return None
        year = file_match.group(1)
        return f"{year}-{line_match.group(1)}-{line_match.group(2)}T{line_match.group(3)}"

    def _extract_summary(self, record: dict[str, Any]) -> str:
        preferred = ("text", "message", "content", "input", "output", "command", "name", "type")
        pieces: list[str] = []
        for key in preferred:
            if key not in record:
                continue
            value = record[key]
            if isinstance(value, str):
                pieces.append(value)
            elif isinstance(value, (dict, list)):
                pieces.append(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            else:
                pieces.append(str(value))
            if sum(len(part) for part in pieces) >= self.max_chars:
                break
        if not pieces:
            pieces.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        return " | ".join(pieces)

    def _sanitize(self, value: str) -> str:
        text = str(value or "")
        text = re.sub(r"(?i)(authorization:\s*bearer\s+)[^\s\"']+", r"\1<redacted>", text)
        text = re.sub(r"(?i)(api[_-]?key|access[_-]?token|refresh[_-]?token|password)([\"'=:\s]+)[^\s,}\"]+", r"\1\2<redacted>", text)
        text = text.replace("\x00", "")
        if len(text) > self.max_chars:
            return text[: self.max_chars - 3] + "..."
        return text
