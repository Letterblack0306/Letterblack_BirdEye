from __future__ import annotations
import hashlib, io, os, re, time, uuid
from pathlib import Path
from PIL import Image, ImageChops

MAX_FRAMES = 32
MAX_AGE = 3600
REFERENCE_RE = re.compile(r"^[a-f0-9]{64}$")

def _encoded(image):
    output=io.BytesIO()
    image.save(output,format="PNG",optimize=True)
    return output.getvalue()

def _pixel_digest(image):
    return hashlib.sha256(str((image.size,image.mode)).encode()+image.tobytes()).hexdigest()

def _prune(store):
    files=sorted(store.glob("*.png"),key=lambda p:p.stat().st_mtime,reverse=True)
    now=time.time()
    for i,p in enumerate(files):
        try:
            if i >= MAX_FRAMES or now-p.stat().st_mtime>MAX_AGE:
                p.unlink(missing_ok=True)
        except OSError: pass

def adaptive_frame(image_bytes, *, cache_root, client_id, reference_id=None, force_full=False):
    if not isinstance(client_id,str) or not 1<=len(client_id)<=128 or not re.fullmatch(r"[a-zA-Z0-9_.:-]+",client_id):
        raise ValueError("client_id must be a stable 1-128 character identifier")
    if reference_id is not None and (not isinstance(reference_id,str) or not REFERENCE_RE.fullmatch(reference_id)):
        raise ValueError("reference_id must be a SHA-256 hex digest")
    if type(force_full) is not bool: raise ValueError("force_full must be boolean")
    frame=Image.open(io.BytesIO(image_bytes)).convert("RGB")
    frame.load()
    pixel_hash=_pixel_digest(frame)
    scope=hashlib.sha256(client_id.encode("utf-8")).hexdigest()[:24]
    store=Path(cache_root)/scope
    store.mkdir(parents=True,exist_ok=True)
    target=store/(pixel_hash+".png")
    # Stored frame names are content-addressed by decoded pixels, not PNG encoding.
    if not target.exists():
        tmp=store/(uuid.uuid4().hex+".tmp")
        try:
            with tmp.open("xb") as f: f.write(_encoded(frame))
            os.replace(tmp,target)
        finally: tmp.unlink(missing_ok=True)
    os.utime(target,None)
    base={"ok":True,"frame_id":pixel_hash,"reference_id":reference_id,"width":frame.width,"height":frame.height,"client_id":client_id}
    previous=store/(reference_id+".png") if reference_id else None
    if not force_full and previous and previous.exists():
        old=Image.open(previous).convert("RGB")
        old.load()
        if old.size==frame.size:
            if reference_id==pixel_hash:
                base.update({"delivery":"unchanged","changed":False,"region":None,"_image_bytes":None})
                _prune(store)
                return base
            diff=ImageChops.difference(old,frame).getbbox()
            if diff is None:
                base.update({"delivery":"unchanged","changed":False,"region":None,"_image_bytes":None})
                _prune(store)
                return base
            left,top,right,bottom=diff
            # Tiny pixel specks are not promoted to a crop with inadequate context.
            margin=24
            left=max(0,left-margin); top=max(0,top-margin)
            right=min(frame.width,right+margin); bottom=min(frame.height,bottom+margin)
            area=(right-left)*(bottom-top)
            if area < 0.40*frame.width*frame.height:
                region=frame.crop((left,top,right,bottom))
                base.update({"delivery":"region","changed":True,"region":{"x":left,"y":top,"width":right-left,"height":bottom-top},"_image_bytes":_encoded(region)})
                _prune(store)
                return base
    base.update({"delivery":"full","changed":True,"region":{"x":0,"y":0,"width":frame.width,"height":frame.height},"_image_bytes":_encoded(frame)})
    _prune(store)
    return base
