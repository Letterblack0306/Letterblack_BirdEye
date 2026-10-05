# Local Mini Remote Control Package

Purpose: expose governed local filesystem write/copy/hash and process execution to the existing ChatGPT remote MCP bridge without relying on a local agent to invent implementation details.

## Target runtime

Default deployment target:

`C:\MCP Local\Local_Mini_MCP`

Existing tunnel credentials are preserved. The installer backs up the files it replaces before changing anything.

## Capabilities

Read-only:
- health
- system_info
- list_drives
- list_dir
- read_text
- stat_path
- file_hash

Write:
- write_text
- copy_file
- move_file
- delete_file
- mkdir

Execution:
- run_process (shell=false)

All filesystem paths are constrained by MINI_MCP_ROOTS and Windows ACL/UAC still applies.

## Install

From a clone/pull of this repository:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-mini-remote-control\install.ps1
```

Defaults:
- target: C:\MCP Local\Local_Mini_MCP
- write enabled: true
- exec enabled: true
- bridge restart: true

The installer:
1. creates a timestamped backup,
2. deploys the authoritative server + bridge,
3. calculates the deployed server SHA-256,
4. writes bridge_config.json with that hash,
5. restarts only the local bridge,
6. runs verification.

## Verify

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-mini-remote-control\verify.ps1
```

Required PASS conditions:
- mini_local_mcp.py compiles,
- local_bridge.py compiles,
- configured server hash equals deployed file hash,
- write_enabled=true,
- exec_enabled=true,
- expected tools are declared.

After the remote connector refreshes, tools/list should expose write_text, copy_file, file_hash and run_process.

## Rollback

The install command prints the backup directory. Restore with:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\local-mini-remote-control\rollback.ps1 -BackupDir "<printed backup path>"
```

## Safety boundary

The HTTP bridge binds only to loopback and requires LOCAL_MINI_BRIDGE_TOKEN. Do not bind it directly to a non-loopback interface. Use the existing authenticated tunnel.
