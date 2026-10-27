from __future__ import annotations
import base64, hashlib, os, tempfile, uuid
from pathlib import Path

MAX_BYTES = 32 * 1024 * 1024
CREATIVE_IMAGE_ROOT = Path(r"C:\MCP Local\Creative_Workspace\Images")

def deliver(params):
    requested = params.get("destination")
    if requested:
        destination = Path(requested).expanduser().resolve()
    else:
        extension = params.get("format", "png").lower().lstrip(".")
        if extension not in ("png", "jpg", "jpeg", "webp"):
            raise ValueError("Unsupported image format")
        destination = CREATIVE_IMAGE_ROOT / ("image-" + uuid.uuid4().hex + "." + extension)
    suffix = destination.suffix.lower()
    if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
        raise ValueError("Unsupported image extension")
    keys = [k for k in ("source_path", "base64_data") if params.get(k)]
    if len(keys) != 1:
        raise ValueError("Supply exactly one source_path or base64_data")
    if keys[0] == "source_path":
        source = Path(params["source_path"]).expanduser().resolve()
        if source.stat().st_size > MAX_BYTES:
            raise ValueError("Source exceeds 32 MB")
        payload = source.read_bytes()
    else:
        payload = base64.b64decode(params["base64_data"], validate=True)
    if not payload or len(payload) > MAX_BYTES:
        raise ValueError("Invalid image size")
    if suffix == ".png" and not payload.startswith(bytes.fromhex("89504e470d0a1a0a")):
        raise ValueError("Invalid PNG signature")
    if suffix in (".jpg", ".jpeg") and not payload.startswith(bytes.fromhex("ffd8ff")):
        raise ValueError("Invalid JPEG signature")
    if suffix == ".webp" and not (payload.startswith(b"RIFF") and payload[8:12] == b"WEBP"):
        raise ValueError("Invalid WebP signature")
    digest = hashlib.sha256(payload).hexdigest()
    if params.get("sha256") and digest.lower() != params["sha256"].lower():
        raise ValueError("SHA-256 mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".image-delivery-", dir=str(destination.parent))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    actual = hashlib.sha256(destination.read_bytes()).hexdigest()
    if actual != digest:
        raise IOError("Verification failed")
    return {"ok": True, "path": str(destination), "bytes": len(payload), "sha256": actual}
