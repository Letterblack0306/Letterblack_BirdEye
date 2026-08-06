# BirdEye Local Workspace Request Bridge

This bridge lets a remote collaborator request bounded, read-only diagnostics from a locally indexed workspace without exposing the local MCP server or opening inbound ports.

## Flow

```text
GitHub request JSON
→ local BirdEye outbound poller
→ workspace and request validation
→ Git/index/validation evidence
→ redaction
→ GitHub response JSON
```

## Security model

- outbound HTTPS only;
- GitHub token comes from `BIRDEYE_GITHUB_TOKEN` or the configured environment variable;
- only configured workspace IDs are accepted;
- absolute workspace paths are replaced in responses;
- no request-provided shell command is executed;
- validation commands are defined only in the local config;
- mutation requests are rejected;
- expired requests are rejected;
- an existing response makes a request idempotent;
- output is bounded before publishing.

The first implementation is deliberately read-only. It does not pull, merge, checkout, reset, edit, delete, or run arbitrary request text.

## Repository layout

```text
requests/pending/<request-id>.json
responses/<machine-id>/<request-id>/result.json
```

Use a machine-specific runtime branch, for example `runtime/dev-main`. Create that branch in GitHub before starting the poller.

## Request schema

```json
{
  "schemaVersion": 1,
  "requestId": "req-20260807-001",
  "createdAt": "2026-08-07T20:00:00Z",
  "expiresAt": "2026-08-07T20:15:00Z",
  "workspaceId": "access-browser-agent",
  "operation": "workspace_diagnosis",
  "scope": {
    "validationProfile": "default"
  },
  "mutationAllowed": false
}
```

Supported operations:

- `workspace_status`
- `workspace_diagnosis`
- `git_compare`
- `run_validation_profile`
- `refresh_index`

`refresh_index` is currently an inspection-compatible operation name. It does not execute an index mutation in this first version.

## Local setup

1. Copy `bridge/bridge.config.example.json` to `bridge.config.json` outside version control or keep it ignored locally.
2. Edit machine ID, runtime branch, workspace mappings, database locations, and local validation profiles.
3. Create a fine-grained GitHub token restricted to `Letterblack_BirdEye` contents read/write.
4. Set the token for the current PowerShell session:

```powershell
$env:BIRDEYE_GITHUB_TOKEN = "<token>"
```

5. Test one poll:

```powershell
python .\bridge\birdeye_request_bridge.py once --config .\bridge.config.json
```

6. Run continuously:

```powershell
python .\bridge\birdeye_request_bridge.py run --config .\bridge.config.json
```

## Windows Task Scheduler

Create a task that starts at user logon with:

```text
Program: python
Arguments: <BirdEye-root>\bridge\birdeye_request_bridge.py run --config <private-config-path>
Start in: <BirdEye-root>
```

Use the logged-in user account and do not embed the GitHub token in task arguments. Store it in the user environment or a later credential-vault integration.

## Verdict rules

- `FAIL`: a configured validation profile fails or request processing fails.
- `REVIEW`: workspace is dirty or the BirdEye database is unavailable.
- `PASS`: configured evidence is available, workspace is clean, and requested validation passes when applicable.

A PASS is bounded to the evidence requested and collected. It is not a universal certification of the workspace.
