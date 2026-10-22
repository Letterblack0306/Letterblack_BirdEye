"""Registration adapter: existing BirdEye owns execution and receipts."""
import base64,hashlib,json,sys
from pathlib import Path
from browser_tool import ACTIONS
SCHEMA={"name":"browser_action","description":"Control existing local Chrome through shared BirdEye extension relay. Discover tabs first. Bind to exact tab_id and expected_url. Reconcile request_id after uncertain effects.","inputSchema":{"type":"object","properties":{
"action":{"type":"string","enum":ACTIONS},"request_id":{"type":"string"},"tab_id":{"type":"string"},"expected_url":{"type":"string"},"url":{"type":"string"},"ref":{"type":"string"},"selector":{"type":"string"},"text":{"type":"string"},"value":{"type":"string"},"key":{"type":"string"},"append":{"type":"boolean"},"active":{"type":"boolean"},"x":{"type":"number"},"y":{"type":"number"},"frame_id":{"type":"integer"},"wait_seconds":{"type":"number","minimum":0,"maximum":25}},"required":["action"],"additionalProperties":False}}
def invoke(params,bird):
    p=dict(params)
    if p.get("action")=="status" and not p.get("request_id"):raise ValueError("status requires request_id")
    receipt=bird.run_command(bird.RunRequest.from_mapping({"workspace":str(bird.BIRDEYE_DIR),"argv":[sys.executable,str(bird.BIRDEYE_DIR/"browser_tool.py"),"--json",json.dumps(p)],"timeout_seconds":35,"intent":"browser-"+str(p.get("action")),"request_id":p.get("request_id")}),bird.CONFIG_PATH)
    if not receipt.get("ok"):return receipt
    out=json.loads(receipt.get("stdout") or "{}")
    out["execution_receipt_path"]=receipt.get("execution_history",{}).get("receipt_path")
    out["authority"]=receipt.get("authority")
    image=out.get("command",{}).get("result") or {}
    if image.get("image_path"):
        raw=Path(image["image_path"]).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=image.get("image_sha256"):raise ValueError("BROWSER_CAPTURE_HASH_MISMATCH")
        out["_image_base64"]=base64.b64encode(raw).decode()
    return out
