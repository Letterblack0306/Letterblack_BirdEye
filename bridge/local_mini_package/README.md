# BirdEye Local Mini MCP v2 package

This directory is a pullable side-by-side upgrade package for the local runtime currently installed under C:\\MCP Local\\Local_Mini_MCP.

It exists so a local agent does not have to redesign or rewrite the bridge from chat instructions.

## Capabilities

Read/inspection:
- stat_path
- hash_file
- read_lines
- find_files
- search_text
- existing health/system/list/read operations

Mutation:
- write_text uses atomic replacement and supports expected_sha256
- patch_text requires exactly one expected-old-text match and optionally an expected SHA-256
- writes return before/after SHA-256 evidence
- read roots and write roots are separate

Execution:
- run_process always uses shell=False
- executable allow-list
- command SHA-256, PID, duration, exit code, stdout and stderr
- process_start/process_status/process_output/process_stop for long-running jobs

Transport/policy:
- bearer authentication
- loopback-only listener
- server file hash pinning
- remote tool allow-list
- independent write and execution gates
- metadata-only JSONL bridge audit

## Deliberate non-goals

This package does not expose an unrestricted raw shell, delete/move/reset/format operations, registry or boot mutation, credential handling, or automatic replacement of the current known-good runtime.

## Pull and install

From the local BirdEye repository:

    cd C:\\MCP Local\\Letterblack_BirdEye
    git fetch origin
    git checkout feat/local-mini-read-write-exec-package
    powershell -ExecutionPolicy Bypass -File .\\bridge\\local_mini_package\\apply-local.ps1
    powershell -ExecutionPolicy Bypass -File .\\bridge\\local_mini_package\\verify-local.ps1

The default install keeps write_enabled=false and exec_enabled=false.

To install the v2 config with both gates enabled:

    powershell -ExecutionPolicy Bypass -File .\\bridge\\local_mini_package\\apply-local.ps1 -EnableWrite -EnableExec
    powershell -ExecutionPolicy Bypass -File .\\bridge\\local_mini_package\\verify-local.ps1

The installer does not switch or stop the existing runtime. It installs under C:\\MCP Local\\Local_Mini_MCP\\v2 and records INSTALL_RECEIPT.json plus a timestamped backup.

## Required live acceptance before switching

The local agent must prove all of these, in order:

1. Current runtime and config backup exists.
2. v2 server SHA-256 equals expected_server_sha256 in bridge_config.json.
3. Python compilation passes.
4. direct health import probe passes.
5. bridge starts on loopback only.
6. unauthenticated request is rejected.
7. read tools are advertised.
8. write tools are absent while write_enabled=false.
9. run_process/process_start/process_stop are absent while exec_enabled=false.
10. gates are enabled deliberately, not implicitly.
11. disposable write_text succeeds and returns before/after evidence.
12. a stale expected_sha256 returns WRITE_CONFLICT without modifying the file.
13. patch_text changes one expected occurrence and returns before/after hashes.
14. run_process executes a harmless command and returns command SHA-256 plus exit code.
15. long-running process start/status/output/stop works.
16. existing known-good runtime remains recoverable.
17. only then is the launcher/service switched to v2.

## Switching the runtime

After acceptance, point the launcher/service at v2\\local_bridge_v2.py and set:

    LOCAL_MINI_BRIDGE_CONFIG=C:\\MCP Local\\Local_Mini_MCP\\v2\\bridge_config.json
    LOCAL_MINI_BRIDGE_TOKEN=<existing bridge token>

Do not delete the old runtime or the timestamped backup until live acceptance is complete.

## Rollback

Rollback is a launcher/service pointer change back to the previous bridge. The installer never deletes the old runtime.
