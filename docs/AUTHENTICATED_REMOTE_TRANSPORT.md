# Authenticated Remote Transport

Status: IMPLEMENTED IN SOURCE / RUNTIME UNVERIFIED

BirdEye already has a local access lease in `state/access_tunnel.json` and a stdio MCP server in `loop_mcp_server.py --stdio`. The missing owner was the transport between an authenticated remote client and that stdio server.

This branch adds `remote_transport.py` as that bounded owner.

## Security boundary

- Binds to `127.0.0.1` by default. It is **not** exposed publicly by itself.
- Requires `BIRDEYE_REMOTE_TOKEN` for every client session.
- Uses the existing `access_tunnel.json` lease as an authorization gate.
- Rejects connections while the lease is closed or expired.
- Spawns a fresh `loop_mcp_server.py --stdio` process per authenticated client and bridges newline-delimited MCP JSON-RPC to its stdin/stdout.
- Terminates the child MCP process when the lease closes or expires.

The transport does not create a reverse tunnel, firewall rule, public listener, VPN, or external relay. A separate authenticated/reverse transport may connect to this loopback endpoint later if remote Internet access is required.

## Run locally

PowerShell:

```powershell
$env:BIRDEYE_REMOTE_TOKEN = '<strong-random-token>'
py remote_transport.py --host 127.0.0.1 --port 8765
```

Keep the token outside the repository and logs.

## Client protocol

The client first sends one newline-terminated JSON object:

```json
{"token":"<same-token>"}
```

Expected response when the lease is OPEN and the token is valid:

```json
{"ok":true,"transport":"birdeye-mcp-stdio"}
```

After that handshake, send ordinary newline-delimited MCP JSON-RPC:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
```

When the lease is CLOSED, the connection must return:

```json
{"ok":false,"error":"access_closed"}
```

Invalid tokens return:

```json
{"ok":false,"error":"unauthorized"}
```

## Acceptance boundary

The implementation is not RUNTIME_PROVEN until the local machine demonstrates:

```text
CLOSED
  -> client connection denied

OPEN
  -> authenticated handshake succeeds
  -> MCP initialize returns serverInfo
  -> tools/list returns BirdEye tools
  -> harmless tools/call succeeds
  -> correlated BirdEye execution/evidence receipt is observed where applicable

CLOSE
  -> active transport is revoked
  -> new client connection denied
```

Only after the local loopback bridge is proven should an Internet/reverse transport be added. Do not claim external ChatGPT/Cline reachability from this source change alone.
