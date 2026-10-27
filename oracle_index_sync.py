"""Oracle-owned incremental ingestion into the existing EYES canonical ledger.
PCs run code from Oracle O drive and publish acknowledged local index batches there.
No PC index, local agent, snapshot upload, or second searchable database.
"""
from __future__ import annotations
import base64, fnmatch, hashlib, json, os, re, sqlite3, threading
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone

SHARED = Path("/home/ubuntu/birdeye-control/indexing")
CONFIG = SHARED / "sources.json"
STATE = SHARED / "checkpoints"
CACHE = SHARED / "content"
LOCK = threading.RLock()
MAX_BYTES = 5_000_000
DENY = [".git", "node_modules", ".venv", "__pycache__", ".pytest_cache",
        ".ssh", ".aws", ".env", ".env.*", "auth*.json", "*secret*", "*token*",
        "credentials*", "*api_key*", "*apikey*", "*api-key*", "*.pem", "*.key",
        "*.pfx", "*.p12", ".mcp-token", "*.incoming"]

def sources():
    doc=json.loads(CONFIG.read_text())
    return doc.get("sources", [])

def source(key):
    for r in sources():
        if r["key"] == key and r.get("enabled", True):
            return r
    raise ValueError("Unconfigured or disabled remote source")

def allowed(r, rel):
    p=PurePosixPath(rel)
    if r.get("file_source") and rel!=r["path"].replace("\\","/").rsplit("/",1)[-1]:return False
    if not rel or p.is_absolute() or ".." in p.parts or "\\" in rel or ":" in rel or "\0" in rel:
        return False
    patterns=DENY+r.get("exclusions", [])
    lowered=rel.casefold()
    if any(fnmatch.fnmatchcase(part.casefold(), pat.casefold()) for part in p.parts for pat in patterns if "/" not in pat):
        return False
    if any(fnmatch.fnmatchcase(lowered, pat.casefold()) for pat in patterns):
        return False
    return not any(lowered==x.casefold() or lowered.startswith(x.casefold().rstrip("/")+"/") for x in r.get("nested_exclusions", []))

def cache_root(r):
    return CACHE/r["machine"]/r["id"]

def _state(r):
    p=STATE/(r["key"]+".json")
    return json.loads(p.read_text()) if p.exists() else {"files":{}, "last_complete":None}

def _save(r, state):
    STATE.mkdir(parents=True, exist_ok=True)
    p=STATE/(r["key"]+".json");temp=p.with_suffix(".incoming")
    temp.write_text(json.dumps(state,separators=(",",":")));os.chmod(temp,0o664);os.replace(temp,p)

def _metadata(r, rel, item):
    from eye_database import _connect, _database_path, _now
    conn=_connect(_database_path("workspace",1))
    try:
        conn.execute("BEGIN IMMEDIATE")
        previous=conn.execute("SELECT size,modified_ns,hash_status FROM files WHERE domain='workspace' AND source_id=? AND relative_path=?", (r["key"],rel)).fetchone()
        status=item.get("status","too_large")
        if previous and (previous["size"],previous["modified_ns"],previous["hash_status"])==(item["size"],item["mtime"],status):
            conn.commit();return
        generation=int(conn.execute("SELECT value FROM meta WHERE key='canonical_generation'").fetchone()[0])+1
        conn.execute("UPDATE meta SET value=? WHERE key='canonical_generation'",(str(generation),))
        now=_now()
        conn.execute("""INSERT INTO files(domain,source_id,agent,relative_path,physical_path,size,modified_ns,sha256,hash_status,error,first_seen_at,last_seen_at,last_event,extension,file_type,last_generation)
          VALUES('workspace',?,?,?,?,?,?,NULL,?,?,?,?,'modified',?,?,?)
          ON CONFLICT(domain,source_id,relative_path) DO UPDATE SET physical_path=excluded.physical_path,size=excluded.size,modified_ns=excluded.modified_ns,sha256=NULL,hash_status=excluded.hash_status,error=excluded.error,last_seen_at=excluded.last_seen_at,last_generation=excluded.last_generation""",
          (r["key"],r.get("agent"),rel,"remote://"+r["machine"]+"/"+r["id"]+"/"+rel,item["size"],item["mtime"],status,item.get("error"),now,now,Path(rel).suffix.lower(),Path(rel).suffix.lstrip("."),generation))
        conn.execute("INSERT INTO changes(generation,domain,source_id,relative_path,event,observed_at) VALUES(?,'workspace',?,?,'modified',?)",(generation,r["key"],rel,now))
        conn.commit()
    finally:conn.close()


def _content_record(r,rel,item,target,raw):
    from eye_database import _connect,_database_path,_now
    digest=hashlib.sha256(raw).hexdigest()
    if digest!=item.get("sha256"):raise ValueError("Local indexing checksum mismatch")
    conn=_connect(_database_path("workspace",1))
    try:
        conn.execute("BEGIN IMMEDIATE")
        old=conn.execute("SELECT * FROM files WHERE domain='workspace' AND source_id=? AND relative_path=?",(r["key"],rel)).fetchone()
        if old and (old["sha256"],old["size"],old["modified_ns"],old["hash_status"])==(digest,item["size"],item["mtime"],"hashed"):
            conn.commit();return
        generation=int(conn.execute("SELECT value FROM meta WHERE key='canonical_generation'").fetchone()[0])+1
        conn.execute("UPDATE meta SET value=? WHERE key='canonical_generation'",(str(generation),))
        now=_now()
        values={"domain":"workspace","source_id":r["key"],"agent":r.get("agent"),"relative_path":rel,
                "physical_path":str(target),"size":item["size"],"modified_ns":item["mtime"],"sha256":digest,
                "hash_status":"hashed","error":None,"first_seen_at":old["first_seen_at"] if old else now,
                "last_seen_at":now,"last_event":"modified","extension":item.get("extension",Path(rel).suffix.lower()),
                "file_type":item.get("extension",Path(rel).suffix).lstrip("."),"line_count":item.get("line_count"),
                "token_count":item.get("token_count"),"feature_terms":item.get("feature_terms","[]"),"last_generation":generation}
        columns=list(values)
        assignments=",".join(c+"=excluded."+c for c in columns if c not in {"domain","source_id","relative_path"})
        conn.execute("INSERT INTO files("+",".join(columns)+") VALUES("+",".join("?" for _ in columns)+") ON CONFLICT(domain,source_id,relative_path) DO UPDATE SET "+assignments,tuple(values.values()))
        conn.execute("INSERT INTO changes(generation,domain,source_id,relative_path,event,old_sha256,new_sha256,observed_at) VALUES(?,'workspace',?,?,'modified',?,?,?)",(generation,r["key"],rel,old["sha256"] if old else None,digest,now))
        conn.commit()
    finally:conn.close()

def invoke(params):
    from eye_database import record_file_event
    from eye_query import project_pending_changes
    op=params.get("operation");r=source(params.get("source"))
    with LOCK:
        state=_state(r)
        if op=="inspect":
            rel=params.get("path","")
            if not allowed(r,rel):raise ValueError("Excluded or escaping path")
            p=cache_root(r)/rel
            if not p.is_file():return {"ok":False,"error":"content_not_cached","source":r["key"],"path":rel}
            lines=p.read_text(encoding="utf-8",errors="replace").splitlines()
            start=params.get("start_line") or 1;end=params.get("end_line") or len(lines)
            return {"ok":True,"path":r["key"]+"/"+rel,"machine":r["machine"],"content":"\n".join(lines[max(0,start-1):end]),"freshness":"last_acknowledged_remote_delta"}
        if op=="manifest":
            return {"ok":True,"files":state["files"],"last_complete":state.get("last_complete")}
        if op=="apply":
            items=params.get("items",[])
            if not isinstance(items,list) or len(items)>100:raise ValueError("Invalid delta batch")
            # Validate the entire batch before any writes.
            decoded=[]
            for item in items:
                rel=item["path"]
                if not allowed(r,rel):raise ValueError("Excluded or escaping source path")
                if type(item["size"]) is not int or item["size"]<0:raise ValueError("Invalid size")
                raw=None
                if "data" in item:
                    raw=base64.b64decode(item["data"],validate=True)
                    if len(raw)>MAX_BYTES or len(raw)!=item["size"]:raise ValueError("Invalid content length")
                decoded.append((item,raw))
            count=0
            for item,raw in decoded:
                rel=item["path"];target=cache_root(r)/rel
                if raw is not None:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    if not target.resolve().is_relative_to(cache_root(r).resolve()):raise ValueError("Cache escape")
                    tmp=target.with_name(target.name+".incoming")
                    tmp.write_bytes(raw);os.replace(tmp,target)
                    os.utime(target,ns=(item["mtime"],item["mtime"]))
                    _content_record(r,rel,item,target,raw)
                else:
                    target.unlink(missing_ok=True)
                    _metadata(r,rel,item)
                state["files"][rel]=[item["size"],item["mtime"],item.get("ctime",0)]
                count+=1
            replay=project_pending_changes("workspace")
            _save(r,state)
            return {"ok":True,"acknowledged":count,"generation":replay.get("applied_generation")}
        if op=="finalize":
            # Missing/offline/incomplete walks NEVER imply deletion.
            if params.get("complete") is not True:raise ValueError("A complete inventory is required")
            seen=params.get("seen")
            if not isinstance(seen,list) or any(not allowed(r,x) for x in seen):raise ValueError("Invalid inventory")
            deleted=0
            for rel in set(state["files"])-set(seen):
                target=cache_root(r)/rel
                target.unlink(missing_ok=True)
                result=record_file_event(target,"deleted",MAX_BYTES)
                if not result.get("ok"):raise RuntimeError(str(result))
                state["files"].pop(rel);deleted+=1
            state["last_complete"]=datetime.now(timezone.utc).isoformat()
            _save(r,state)
            replay=project_pending_changes("workspace")
            return {"ok":True,"deleted":deleted,"files":len(state["files"]),"generation":replay.get("applied_generation")}
        raise ValueError("Unknown ingestion operation")


_CONSUMER_STARTED=False
_START_LOCK=threading.Lock()

def _ack(path,result):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(".incoming")
    temp.write_text(json.dumps(result,separators=(",",":")));os.chmod(temp,0o664);os.replace(temp,path)

def consume_pending():
    processed=0
    for ready in sorted((SHARED/"results").glob("*.ready"),key=lambda p:p.stat().st_mtime):
        ack=SHARED/"acks"/(ready.stem+".json")
        try:
            if ready.stat().st_size>16_000_000:raise ValueError("Batch exceeds bound")
            envelope=json.loads(ready.read_text())
            raw=json.dumps(envelope["payload"],separators=(",",":"),ensure_ascii=True).encode()
            if len(raw)!=envelope["bytes"] or hashlib.sha256(raw).hexdigest()!=envelope["sha256"]:
                raise ValueError("Incomplete or corrupted O-drive batch")
            result=invoke(envelope["payload"])
            result["batch_sha256"]=envelope["sha256"]
            _ack(ack,result)
            ready.unlink();processed+=1
        except Exception as exc:
            _ack(ack,{"ok":False,"acknowledged":False,"error":type(exc).__name__+":"+str(exc)[:300],"retry":"next local reconciliation"})
            # Unaccepted source bytes are reconstructable; retain a bounded
            # error receipt rather than treating a partial batch as complete.
            failed=SHARED/"incomplete"/(ready.stem+".json")
            _ack(failed,{"error":type(exc).__name__,"batch":ready.name,"acknowledged":False})
            ready.unlink(missing_ok=True)
    return processed

def on_mcp_connected():
    if os.environ.get("BIRDEYE_CLOUD_PROFILE") != "1":
        return False
    global _CONSUMER_STARTED
    with _START_LOCK:
        if _CONSUMER_STARTED:return False
        _CONSUMER_STARTED=True
        def worker():
            import time
            while True:
                try:consume_pending()
                except Exception as exc:
                    import sys
                    print("[Oracle O-drive ingest]",type(exc).__name__,str(exc)[:200],file=sys.stderr,flush=True)
                time.sleep(1)
        threading.Thread(target=worker,name="oracle-o-drive-ingest",daemon=True).start()
        return True
