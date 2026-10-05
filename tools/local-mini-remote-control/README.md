# Local Mini Remote Control Package

This package deploys and verifies the Local Mini MCP bridge without modifying the active BirdEye checkout.

## Security boundary

The default filesystem scope is only the deployment target:

    C:\MCP Local\Local_Mini_MCP

To authorize additional roots:

    powershell -ExecutionPolicy Bypass -File .\tools\local-mini-remote-control\install.ps1 -Roots 'C:\MCP Local\Local_Mini_MCP;D:\2026\SAM_GINIE'

All mounted drives require an explicit opt-in:

    powershell -ExecutionPolicy Bypass -File .\tools\local-mini-remote-control\install.ps1 -Roots '*' -AllowAllRoots

Windows ACL/UAC still applies. The HTTP bridge binds only to loopback and requires LOCAL_MINI_BRIDGE_TOKEN.

## What is deployed

Only these runtime owners are replaced:

- mini_local_mcp.py
- remote_bridge/local_bridge.py
- remote_bridge/bridge_config.json

Existing tunnel scripts and credentials are preserved:

- remote_bridge/connect_tunnel.ps1
- remote_bridge/tunnel_switch.ps1
- remote_bridge/start_bridge.ps1
- remote_bridge/tunnel_credentials.env

Connector rediscovery is handled by the package helper refresh_connector.ps1. It restarts the existing tunnel profile without rewriting those files.

## Capabilities

Read: health, system_info, list_drives, list_dir, read_text, stat_path, file_hash.

Write: write_text, mkdir, copy_file, move_file, delete_file.

Execution: run_process with shell=False.

bridge_config.json is the sole authority for write/exec enablement. Stale LOCAL_MINI_REMOTE_WRITE and LOCAL_MINI_REMOTE_EXEC environment values are ignored by the bridge.

## Install

    powershell -ExecutionPolicy Bypass -File .\tools\local-mini-remote-control\install.ps1

The installer:

1. discovers a Python interpreter that can import mcp, uvicorn, and starlette;
2. creates a timestamped backup manifest;
3. deploys only the three runtime-owner files;
4. computes and records the server SHA-256;
5. starts the bridge;
6. performs authenticated health validation;
7. runs a real MCP behavior smoke test through the HTTP bridge;
8. restarts the existing tunnel profile to force tool rediscovery.

Use -PythonExe <path> only when automatic discovery is not appropriate.

## Verification

    powershell -ExecutionPolicy Bypass -File .\tools\local-mini-remote-control\verify.ps1

A full PASS requires:

- both Python files compile;
- deployed SHA-256 matches config;
- authenticated /health succeeds;
- write and exec are enabled;
- HTTP MCP tools/list contains all 13 expected tools;
- write_text works;
- file_hash returns the expected SHA-256;
- run_process actually executes a Python process;
- the smoke artifact is deleted.

## Rollback

    powershell -ExecutionPolicy Bypass -File .\tools\local-mini-remote-control\rollback.ps1 -BackupDir '<BACKUP_DIR printed by install>'

Rollback uses the recorded backup manifest, restores or removes every file the installer changed, restarts the restored bridge, and refreshes the existing tunnel profile.

## Evidence boundary

A package commit is not runtime proof. Runtime acceptance requires installer/verify output plus the refreshed ChatGPT connector exposing the expanded tool list.
