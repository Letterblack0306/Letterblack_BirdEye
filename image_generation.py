from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from image_delivery import deliver

OPENAI_MODELS = {"gpt-image-1", "gpt-image-1-mini", "gpt-image-1.5"}
OPENAI_SIZES = {"1024x1024", "1024x1536", "1536x1024"}
GEMINI_MODELS = {"gemini-2.5-flash-image", "gemini-3-pro-image-preview"}

def _request(url, payload, headers):
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.load(response), None
    except urllib.error.HTTPError as exc:
        return None, {"ok": False, "error": "Provider rejected request", "http_status": exc.code}
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, {"ok": False, "error": type(exc).__name__}

def generate(params):
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 12000:
        raise ValueError("prompt must contain 1-12000 characters")
    provider = params.get("provider", "auto")
    if provider not in ("auto", "openai", "gemini"):
        raise ValueError("Unsupported provider")
    available = [p for p, k in (("openai", "OPENAI_API_KEY"), ("gemini", "GEMINI_API_KEY")) if os.environ.get(k)]
    if provider == "auto":
        provider = "gemini" if "gemini" in available else ("openai" if "openai" in available else "")
    if not provider or provider not in available:
        return {"ok": False, "error": "Selected provider credentials unavailable"}
    model = params.get("model") or ("gpt-image-1" if provider == "openai" else "gemini-2.5-flash-image")
    size = params.get("size", "1024x1024")
    if provider == "openai":
        if model not in OPENAI_MODELS or size not in OPENAI_SIZES:
            raise ValueError("Unsupported model or size")
        response, error = _request("https://api.openai.com/v1/images/generations",
            {"model": model, "prompt": prompt, "size": size, "n": 1},
            {"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"], "Content-Type": "application/json"})
        if error: return {**error, "provider": provider}
        items = response.get("data") or []
        image = items[0].get("b64_json") if items else None
    else:
        if model not in GEMINI_MODELS:
            raise ValueError("Unsupported Gemini image model")
        response, error = _request(
            "https://generativelanguage.googleapis.com/v1beta/models/" + model + ":generateContent",
            {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE", "TEXT"]}},
            {"x-goog-api-key": os.environ["GEMINI_API_KEY"], "Content-Type": "application/json"})
        if error: return {**error, "provider": provider}
        parts = [p for c in response.get("candidates", []) for p in c.get("content", {}).get("parts", [])]
        images = [p.get("inlineData") or p.get("inline_data") for p in parts]
        image = next((p.get("data") for p in images if isinstance(p, dict) and p.get("mimeType", p.get("mime_type", "image/png")) == "image/png" and p.get("data")), None)
    if not isinstance(image, str) or not image:
        return {"ok": False, "error": "Provider returned no PNG bytes", "provider": provider}
    args = {"base64_data": image, "format": "png"}
    if params.get("destination"):
        args["destination"] = params["destination"]
    return {**deliver(args), "generated": True, "provider": provider, "model": model}
