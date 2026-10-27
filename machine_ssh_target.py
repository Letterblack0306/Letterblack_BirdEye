"""Machine-qualified execution through an existing BirdEye MCP via trusted LAN SSH.

This is a transport adapter, not another executor or policy engine. The remote
BirdEye process remains the sole workspace execution and receipt authority.
Only configured SSH targets can route; failures never fall back to local work.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Any

_TOOLS = frozenset({
    "workspace_identity", "revision_status", "workspace_run",
    "workspace_run_sequence", "workspace_command_history",
})
_MARKER = "BIRDEYE_SSH_MCP_RESULT_V1:"
_MAX_STDOUT = 12_000_000
_MAX_STDERR = 600


def _error(kind: str, detail: str, machine: str) -> dict[str, Any]:
    return {"ok": False, "error": kind, "message": detail[:600],
            "execution_target": {"machine": machine, "transport": "lan-ssh",
                                 "verified": False}}


def _route(workspace: Any, config_path: Path):
    if not isinstance(workspace, str) or not workspace:
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    for target in config.get("ssh_targets", []):
        if not isinstance(target, dict):
            continue
        alias = target.get("id")
        if not isinstance(alias, str) or not alias:
            continue
        if workspace == alias:
            return target, target.get("default_workspace")
        prefix = alias + "::"
        if workspace.startswith(prefix):
            return target, workspace[len(prefix):]
    return None


def maybe_invoke(tool: str, params: dict[str, Any], config_path: Path) -> dict[str, Any] | None:
    """Return None only for an unrecognized/local request.

    For a matched remote alias, every transport failure returns a tagged error
    instead of ever attempting to execute the argv on the Mini PC.
    """
    if tool not in _TOOLS or not isinstance(params, dict):
        return None
    match = _route(params.get("workspace"), config_path)
    if match is None:
        return None
    target, target_workspace = match
    alias = target["id"]
    expected_host = str(target.get("expected_hostname", "")).strip()
    address = str(target.get("host", "")).strip()
    username = str(target.get("user", "")).strip()
    python_root = str(target.get("birdeye_root", "")).strip()
    identity = Path(str(target.get("identity_file", "")))
    port = target.get("port", 22)
    if (not expected_host or not address or not username or not python_root or
            not identity.is_file() or type(port) is not int or not 1 <= port <= 65535):
        return _error("SSH_TARGET_CONFIGURATION", "SSH target is not fully configured", alias)
    # Explicit main-host aliases do not grant any more authority than a direct
    # call to the main BirdEye. The target workspace must be a Windows absolute
    # path; command argv and all other parameters stay unchanged.
    if (not isinstance(target_workspace, str) or len(target_workspace) < 3 or
            target_workspace[1:3] != ":\\" or "\x00" in target_workspace):
        return _error("SSH_TARGET_WORKSPACE", "An absolute Windows workspace path is required", alias)
    remote_params = dict(params)
    remote_params["workspace"] = target_workspace

    # All command parameters travel inside the SSH stdin script, not shell
    # arguments. The remote process authenticates its hostname BEFORE invoke.
    request_json = json.dumps({"tool": tool, "params": remote_params,
                               "expected": expected_host, "root": python_root}, ensure_ascii=True)
    program = (
        "import json,os,socket,sys\n"
        "request=json.loads(" + repr(request_json) + ")\n"
        "observed=socket.gethostname()\n"
        "if observed.casefold()!=request['expected'].casefold():\n"
        "    result={'ok':False,'error':'SSH_HOST_IDENTITY_MISMATCH',"
        "'message':'Connected SSH host did not match the configured machine identity'}\n"
        "else:\n"
        "    sys.path.insert(0,request['root'])\n"
        "    os.chdir(request['root'])\n"
        "    try:\n"
        "        import mcp_server as bird\n"
        "        result=bird.invoke(request['tool'],request['params'])\n"
        "    except Exception as exc:\n"
        "        result={'ok':False,'error':type(exc).__name__,"
        "'message':str(exc)[:500]}\n"
        "print(" + repr(_MARKER) + "+json.dumps({'hostname':observed,'result':result},"
        "ensure_ascii=True,default=str))\n"
    )
    ssh = [
        "ssh", "-T", "-i", str(identity),
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=7",
        "-o", "ConnectionAttempts=1", "-o", "NumberOfPasswordPrompts=0",
        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
        "-o", "LogLevel=ERROR", "-p", str(port),
        username + "@" + address, "python -",
    ]
    try:
        requested = int(params.get("timeout_seconds") or 120)
    except (TypeError, ValueError):
        requested = 120
    timeout = min(340, max(20, requested + 30))
    try:
        completed = subprocess.run(
            ssh, input=program, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _error("SSH_TRANSPORT_UNAVAILABLE", type(exc).__name__, alias)
    if completed.returncode:
        message = completed.stderr.strip()[-_MAX_STDERR:] or ("SSH exit status " + str(completed.returncode))
        return _error("SSH_TRANSPORT_UNAVAILABLE", message, alias)
    if len(completed.stdout) > _MAX_STDOUT:
        return _error("SSH_RESPONSE_TOO_LARGE", "Remote output exceeds bounded transport response", alias)
    try:
        matches = [line[len(_MARKER):] for line in completed.stdout.splitlines()
                   if line.startswith(_MARKER)]
        if not matches:
            raise ValueError("Missing authenticated remote BirdEye result")
        envelope = json.loads(matches[-1])
        remote_host = str(envelope["hostname"])
        if remote_host.casefold() != expected_host.casefold():
            return _error("SSH_HOST_IDENTITY_MISMATCH", "Remote machine identity differed", alias)
        result = envelope["result"]
        if not isinstance(result, dict):
            raise ValueError("Unexpected remote tool result")
    except (ValueError, KeyError, TypeError) as exc:
        return _error("SSH_REMOTE_PROTOCOL_ERROR", str(exc), alias)
    result["execution_target"] = {
        "machine": alias,
        "hostname": remote_host,
        "host_address": address,
        "workspace": target_workspace,
        "transport": "lan-ssh",
        "verified": True,
        "authority": "remote-birdeye",
    }
    return result
