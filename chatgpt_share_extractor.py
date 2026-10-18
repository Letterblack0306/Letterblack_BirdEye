from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

_SHARE_ID = re.compile(r"^/share/([0-9a-fA-F-]{36})/?$")
_STREAM = re.compile(r'window\.__reactRouterContext\.streamController\.enqueue\((".*?")\);', re.S)


def extract_share(url: str, max_messages: int = 100, save: bool = False) -> dict:
    """Extract public shared ChatGPT messages without external browser/profile access."""
    parsed = urlparse(str(url).strip())
    match = _SHARE_ID.fullmatch(parsed.path)
    if parsed.scheme != "https" or parsed.netloc.lower() != "chatgpt.com" or not match or parsed.query or parsed.fragment:
        return {"ok": False, "error": "INVALID_SHARE_URL", "message": "Expected https://chatgpt.com/share/<uuid>."}
    if not isinstance(max_messages, int) or not 1 <= max_messages <= 2000:
        return {"ok": False, "error": "INVALID_LIMIT", "message": "max_messages must be 1..2000."}
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; BirdEyeSharedConversation/1.0)"})
        with urllib.request.urlopen(req, timeout=20) as response:
            page = response.read(12_000_001)
        if len(page) > 12_000_000:
            return {"ok": False, "error": "PAGE_TOO_LARGE"}
        html = page.decode("utf-8", "replace")
        matches = list(_STREAM.finditer(html))
        if not matches:
            return {"ok": False, "error": "NO_PAGE_PAYLOAD", "message": "No public conversation hydration stream found."}
        serialized = json.loads(max(matches, key=lambda m: len(m.group(1))).group(1))
        pool = json.loads(serialized)
        mapping = next((pool[i + 1] for i, item in enumerate(pool[:-1]) if item == "mapping" and isinstance(pool[i + 1], dict)), None)
        if not isinstance(mapping, dict):
            return {"ok": False, "error": "NO_CONVERSATION_MAPPING"}
        def obj(value):
            if not isinstance(value, dict):
                return {}
            result = {}
            for k, idx in value.items():
                if not (isinstance(k, str) and k.startswith("_") and k[1:].isdigit()):
                    continue
                key_pos = int(k[1:])
                if key_pos >= len(pool):
                    continue
                result[str(pool[key_pos])] = pool[idx] if isinstance(idx, int) and 0 <= idx < len(pool) else None
            return result
        messages = []
        for order, idx in enumerate(mapping.values()):
            if not isinstance(idx, int) or idx < 0 or idx >= len(pool):
                continue
            node = obj(pool[idx])
            msg = obj(node.get("message"))
            author = obj(msg.get("author"))
            role = author.get("role")
            if role not in ("user", "assistant"):
                continue
            content = obj(msg.get("content"))
            parts = content.get("parts")
            if not isinstance(parts, list):
                continue
            chunks = [pool[p] for p in parts if isinstance(p, int) and 0 <= p < len(pool) and isinstance(pool[p], str)]
            body = "\n".join(chunks).strip()
            if not body:
                continue
            messages.append({
                "role": role, "text": body, "message_id": msg.get("id"),
                "created_at": msg.get("create_time"), "_order": order,
            })
        messages.sort(key=lambda m: (float(m["created_at"]) if isinstance(m["created_at"], (float,int)) else 0, m["_order"]))
        for m in messages:
            m.pop("_order", None)
        result = {
            "ok": True, "source_url": url, "source_type": "public_shared_chatgpt",
            "total_messages": len(messages), "returned_messages": min(max_messages, len(messages)),
            "truncated": len(messages) > max_messages,
            "messages": messages[-max_messages:],
            "authority": "external_historical_conversation_not_live_workspace_proof",
        }
        if save:
            destination = Path(__file__).resolve().parent / "state" / "shared_conversations"
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / (match.group(1).lower() + ".json")
            import os
            import tempfile
            handle, tmp = tempfile.mkstemp(prefix="share-", suffix=".tmp", dir=destination)
            try:
                with os.fdopen(handle, "w", encoding="utf-8") as file:
                    json.dump({"source_url": url, "total_messages": len(messages), "messages": messages}, file, ensure_ascii=False, indent=2)
                os.replace(tmp, target)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            result["saved_path"] = str(target)
        return result
    except (OSError, ValueError, TypeError, json.JSONDecodeError, StopIteration) as exc:
        return {"ok": False, "error": "EXTRACTION_FAILED", "message": str(exc)[:600]}
