"""BirdEye local browser tool. Invoke via shared MCP browser_action or workspace_run."""
import argparse,json,sys,time,urllib.request,urllib.error,urllib.parse,uuid,base64,hashlib
from pathlib import Path
ACTIONS=["tabs","open","navigate","snapshot","click","type","press","select","scroll","screenshot","back","forward","reload","close","status"]
def call(params):
    base="http://127.0.0.1:7726"
    if params.get("action") not in ACTIONS: raise ValueError("BROWSER_ACTION_UNSUPPORTED")
    wait=max(0,min(25,float(params.pop("wait_seconds",15))))
    request_id=str(params.get("request_id") or uuid.uuid4())
    params["request_id"]=request_id
    def request(path,p=None):
        data=json.dumps(p).encode() if p is not None else None
        req=urllib.request.Request(base+path,data=data,headers={"Content-Type":"application/json"} if data else {})
        with urllib.request.urlopen(req,timeout=5) as r:return json.load(r)
    if params["action"]=="status":
        data=request("/browser/command?id="+urllib.parse.quote(request_id))
    else:
        data=request("/browser/enqueue",params)
    command=data.get("command")
    if not command:return data
    deadline=time.monotonic()+wait
    while command["status"] in ("queued","dispatched") and time.monotonic()<deadline:
        time.sleep(0.1)
        command=request("/browser/command?id="+urllib.parse.quote(request_id)).get("command",command)
    result=command.get("result")
    if result and result.get("image_base64"):
        raw=base64.b64decode(result.pop("image_base64"),validate=True)
        folder=Path(__file__).parent / "state" / "browser-captures"
        folder.mkdir(parents=True,exist_ok=True)
        dest=folder / (hashlib.sha256(request_id.encode()).hexdigest()+".png")
        dest.write_bytes(raw)
        result["image_path"]=str(dest)
        result["image_sha256"]=hashlib.sha256(raw).hexdigest()
    out={"ok":bool(result and result.get("ok")) if command["status"]=="complete" else False,"request_id":request_id,"status":command["status"],"command":command}
    if command["status"]!="complete":out["guidance"]="Reconcile this request_id with action=status. Do not resubmit a browser write."
    return out
if __name__=="__main__":
    ap=argparse.ArgumentParser();ap.add_argument("--json",required=True);args=ap.parse_args()
    try:print(json.dumps(call(json.loads(args.json)),ensure_ascii=True))
    except Exception as e:
        print(json.dumps({"ok":False,"error":type(e).__name__,"message":str(e),"guidance":"Check relay health and reconcile request_id before retrying a write."}));sys.exit(1)
