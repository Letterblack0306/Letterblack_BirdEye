"""Single BirdEye-owned Linear integration. No per-agent processes or credentials."""
from __future__ import annotations
import json
import os
import urllib.request
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = Path.home() / "AppData" / "Local" / "BirdEye" / "connections" / "linear.dpapi"
API = "https://api.linear.app/graphql"
POLICY = STORE.with_name("linear-policy.json")
NAMES = ("linear_status", "linear_search_issues", "linear_get_issue", "linear_list_projects", "linear_create_issue", "linear_update_issue")
SCHEMAS = [
    {"name":"linear_status","description":"BirdEye shared Linear connection status; does not reveal credential.","inputSchema":{"type":"object","properties":{},"additionalProperties":False}},
    {"name":"linear_search_issues","description":"Search issues through the shared BirdEye Linear connection. Read-only.","inputSchema":{"type":"object","properties":{"query":{"type":"string","minLength":1,"maxLength":200},"limit":{"type":"integer","minimum":1,"maximum":25}},"required":["query"],"additionalProperties":False}},
    {"name":"linear_get_issue","description":"Read one issue by its Linear identifier (e.g. LET-41) via BirdEye.","inputSchema":{"type":"object","properties":{"id":{"type":"string","minLength":1,"maxLength":80}},"required":["id"],"additionalProperties":False}},
    {"name":"linear_list_projects","description":"List projects through the BirdEye-owned Linear connection.","inputSchema":{"type":"object","properties":{"limit":{"type":"integer","minimum":1,"maximum":25}},"additionalProperties":False}},
    {"name":"linear_create_issue","description":"Create issue in the configured Linear team. Disabled until gateway owner explicitly enables writes.","inputSchema":{"type":"object","properties":{"title":{"type":"string","minLength":1,"maxLength":250},"description":{"type":"string","maxLength":15000}},"required":["title"],"additionalProperties":False}},
    {"name":"linear_update_issue","description":"Update an issue owned by the configured Linear team. Disabled until gateway owner explicitly enables writes.","inputSchema":{"type":"object","properties":{"id":{"type":"string","minLength":1,"maxLength":80},"title":{"type":"string","minLength":1,"maxLength":250},"description":{"type":"string","maxLength":15000}},"required":["id"],"additionalProperties":False}},
]

def credential_present() -> bool:
    return STORE.is_file()

def _key() -> str | None:
    if not STORE.is_file():
        return None
    if os.name != "nt":
        return None
    try:
        import win32crypt
        raw = win32crypt.CryptUnprotectData(STORE.read_bytes(), None, None, None, 0)[1]
        return raw.decode("utf-8").strip() or None
    except (OSError, ValueError, ImportError):
        return None

def save_key_interactive() -> None:
    """Run manually once in the gateway owner's Windows session, never over MCP."""
    import getpass
    if os.name != "nt":
        raise RuntimeError("Windows DPAPI storage required")
    import win32crypt
    secret = getpass.getpass("Linear API key (hidden): ").strip()
    if not secret:
        raise ValueError("No key entered")
    if "\n" in secret or "\r" in secret:
        raise ValueError("Malformed key")
    STORE.parent.mkdir(parents=True, exist_ok=True)
    blob = win32crypt.CryptProtectData(secret.encode("utf-8"), "BirdEye Linear integration", None, None, None, 0)
    STORE.write_bytes(blob)
    try:
        os.chmod(STORE, 0o600)
    except OSError:
        pass
    print("BirdEye Linear credential stored with Windows DPAPI for this account.")

def _write_policy() -> dict:
    try:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        team_id = policy.get("team_id")
        if (policy.get("allow_write") is True and isinstance(team_id, str)
                and len(team_id) == 36 and team_id.count("-") == 4):
            return {"team_id":team_id}
    except (OSError, ValueError, TypeError):
        pass
    return {}

def _team_of_issue(issue_id: str) -> str | None:
    result = _request("query($id:String!){issue(id:$id){team{id}}}", {"id":issue_id})
    if not result.get("ok"):
        return None
    return ((result.get("data") or {}).get("issue") or {}).get("team",{}).get("id")

def _request(query: str, variables: dict) -> dict:
    token = _key()
    if not token:
        return {"ok":False,"error":"LINEAR_NOT_CONNECTED","message":"Configure Linear once in the BirdEye gateway owner's Windows account. No per-agent authentication is needed."}
    req = urllib.request.Request(API, json.dumps({"query":query,"variables":variables}).encode("utf-8"),
        {"Content-Type":"application/json","Authorization":token, "User-Agent":"BirdEye-Linear/1"},method="POST")
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            obj = json.loads(response.read(1000000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"ok":False,"error":"LINEAR_HTTP_ERROR","status":exc.code}
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return {"ok":False,"error":"LINEAR_UNAVAILABLE"}
    if obj.get("errors"):
        return {"ok":False,"error":"LINEAR_GRAPHQL_ERROR","messages":[str(x.get("message","GraphQL error"))[:160] for x in obj["errors"][:3]]}
    return {"ok":True,"data":obj.get("data",{})}

def invoke(name: str, params: dict) -> dict:
    if name not in NAMES:
        return {"ok":False,"error":"UNKNOWN_LINEAR_TOOL"}
    if name == "linear_status":
        return {"ok":True,"provider":"linear","connection_scope":"BirdEye gateway owner","credential_stored":credential_present(),
                "credential_usable":bool(_key()),"write_enabled":bool(_write_policy()),"authentication":"Windows DPAPI"}
    if name in ("linear_create_issue","linear_update_issue"):
        policy = _write_policy()
        if not policy:
            return {"ok":False,"error":"LINEAR_WRITE_DISABLED","message":"Gateway owner must enable writes for an explicit Linear team."}
        if name == "linear_create_issue":
            title = params.get("title")
            desc = params.get("description","")
            if not isinstance(title,str) or not title.strip() or len(title)>250 or not isinstance(desc,str) or len(desc)>15000:
                return {"ok":False,"error":"INVALID_INPUT"}
            return _request("mutation($input:IssueCreateInput!){issueCreate(input:$input){success issue{id identifier title url}}}",
                            {"input":{"teamId":policy["team_id"],"title":title.strip(),"description":desc}})
        issue = params.get("id")
        changes={key:params[key] for key in ("title","description") if key in params}
        if (not isinstance(issue,str) or not issue.strip() or len(issue)>80 or not changes or
            any(not isinstance(value,str) or len(value)>(250 if key=="title" else 15000) or (key=="title" and not value.strip())
                for key,value in changes.items())):
            return {"ok":False,"error":"INVALID_INPUT"}
        if _team_of_issue(issue)!=policy["team_id"]:
            return {"ok":False,"error":"LINEAR_TEAM_DENIED"}
        return _request("mutation($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id identifier title url}}}",
                        {"id":issue,"input":changes})
    if name == "linear_search_issues":
        raw_term = params.get("query")
        term = raw_term.strip() if isinstance(raw_term,str) else ""
        if not term or len(term)>200:
            return {"ok":False,"error":"INVALID_QUERY"}
        try:
            limit = int(params.get("limit",10))
        except (TypeError, ValueError, OverflowError):
            return {"ok":False,"error":"INVALID_LIMIT"}
        if not 1 <= limit <= 25:
            return {"ok":False,"error":"INVALID_LIMIT"}
        return _request("""query($term: String!, $limit: Int!) { issueSearch(query:$term, first:$limit) { nodes { id identifier title url description } } }""",{"term":term,"limit":limit})
    if name == "linear_get_issue":
        raw_id = params.get("id")
        ident = raw_id.strip() if isinstance(raw_id,str) else ""
        if not ident or len(ident)>80:
            return {"ok":False,"error":"INVALID_ISSUE_ID"}
        return _request("""query($id:String!) { issue(id:$id) { id identifier title description url state {name} assignee {name} } }""",{"id":ident})
    if name == "linear_list_projects":
        try:
            limit = int(params.get("limit",10))
        except (TypeError, ValueError, OverflowError):
            return {"ok":False,"error":"INVALID_LIMIT"}
        if not 1 <= limit <= 25:
            return {"ok":False,"error":"INVALID_LIMIT"}
        return _request("""query($limit:Int!) { projects(first:$limit) { nodes { id name url description } } }""",{"limit":limit})
    return {"ok":False,"error":"UNKNOWN_LINEAR_TOOL"}

if __name__ == "__main__":
    save_key_interactive()
