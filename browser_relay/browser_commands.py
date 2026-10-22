"""Durable general-browser commands on the existing BirdEye relay."""
import hashlib, json, secrets, sqlite3, threading, time
from pathlib import Path
ACTIONS = ["tabs","open","navigate","snapshot","click","type","press","select","scroll","screenshot","back","forward","reload","close"]
class BrowserCommands:
    def __init__(self, path=None):
        self.path = Path(path or Path(__file__).parent.parent / "state" / "browser-commands.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.client = None
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY, body TEXT NOT NULL, status TEXT NOT NULL, result TEXT, claim TEXT, created REAL NOT NULL)")

    def recover(self):
        with self.lock,self.db() as db:
            db.execute("UPDATE commands SET status='uncertain' WHERE status='dispatched'")
    def db(self):
        db=sqlite3.connect(self.path, timeout=5); db.row_factory=sqlite3.Row
        return db
    def hello(self,p):
        with self.lock:
            self.client={"version":str(p.get("version","")), "extension_id":str(p.get("extension_id","")), "seen":time.time()}
            return {"ok":True,"client":self.client,"actions":ACTIONS}
    def enqueue(self,p):
        action=p.get("action")
        if action not in ACTIONS: raise ValueError("BROWSER_ACTION_UNSUPPORTED")
        request_id=str(p.get("request_id","")).strip()
        if not request_id or len(request_id)>160: raise ValueError("request_id is required (max 160)")
        if action not in ("tabs","open") and (not str(p.get("tab_id","")).isdigit() or not p.get("expected_url")):
            raise ValueError("Exact tab_id and expected_url required")
        if action in ("open","navigate") and not str(p.get("url","")).startswith(("http://","https://")):
            raise ValueError("HTTP/HTTPS URL required")
        clean={k:v for k,v in p.items() if k!="wait_seconds"}
        body=json.dumps(clean,sort_keys=True,separators=(",",":"))
        with self.lock,self.db() as db:
            old=db.execute("SELECT * FROM commands WHERE id=?",(request_id,)).fetchone()
            if old:
                if old["body"]!=body: raise ValueError("REQUEST_ID_CONFLICT")
                return self.encode(old)
            db.execute("INSERT INTO commands VALUES(?,?,'queued',NULL,NULL,?)",(request_id,body,time.time()))
            return self.encode(db.execute("SELECT * FROM commands WHERE id=?",(request_id,)).fetchone())
    def encode(self,row):
        return {"request_id":row["id"],"status":row["status"],"request":json.loads(row["body"]),"result":json.loads(row["result"]) if row["result"] else None,"created_at":row["created"],"receipt_sha256":hashlib.sha256((row["body"]+(row["result"] or "")).encode()).hexdigest()}
    def get(self,request_id):
        with self.lock,self.db() as db:
            row=db.execute("SELECT * FROM commands WHERE id=?",(request_id,)).fetchone()
            return self.encode(row) if row else None
    def claim(self):
        with self.lock,self.db() as db:
            if db.execute("SELECT 1 FROM commands WHERE status='dispatched'").fetchone(): return None
            row=db.execute("SELECT * FROM commands WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if not row:return None
            token=secrets.token_hex(24)
            db.execute("UPDATE commands SET status='dispatched',claim=? WHERE id=?",(token,row["id"]))
            return {"request_id":row["id"],"claim":token,"request":json.loads(row["body"])}
    def complete(self,p):
        with self.lock,self.db() as db:
            row=db.execute("SELECT * FROM commands WHERE id=?",(p.get("request_id"),)).fetchone()
            if not row or not secrets.compare_digest(str(row["claim"] or ""),str(p.get("claim",""))):raise ValueError("CLAIM_MISMATCH")
            result=json.dumps(p.get("result") or {},sort_keys=True)
            if row["status"]=="complete":
                if result!=row["result"]:raise ValueError("COMPLETION_CONFLICT")
            elif row["status"] in ("dispatched","uncertain"):
                db.execute("UPDATE commands SET status='complete',result=? WHERE id=?",(result,row["id"]))
            else:raise ValueError("COMMAND_NOT_DISPATCHED")
            out=self.encode(db.execute("SELECT * FROM commands WHERE id=?",(row["id"],)).fetchone())
            out["receipt_sha256"]=hashlib.sha256((row["body"]+result).encode()).hexdigest()
            return out
