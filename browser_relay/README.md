# BirdEye Browser Relay

Local browser transport for BirdEye loops that does not use Chrome remote debugging or CDP.

## Architecture

ChatGPT tab
-> Chrome extension
-> localhost HTTP relay
-> BirdEye loop

BirdEye remains responsible for loop state and local execution. Existing BirdEye workspace indexing and SHA-256 records remain authoritative for files.

## Install

From the BirdEye repository:

    powershell -ExecutionPolicy Bypass -File .\browser_relay\install_browser_relay.ps1

The relay listens on:

    http://127.0.0.1:7726

Then open chrome://extensions, enable Developer mode, choose Load unpacked, and select:

    browser_relay\extension

No Chrome remote-debugging port is enabled.

## API

GET  /health
GET  /tabs
POST /tabs/register
POST /tabs/unregister
POST /commands/next
POST /commands/complete
POST /read
POST /reply

Commands are bound to a specific registered Chrome tab ID.

## Boundary

This is the browser transport layer, not the complete autonomous command loop. The loop controller can use it to read a specific ChatGPT tab and post a result back to that same tab.

DOM selectors are deliberately explicit. If the ChatGPT UI changes, the relay reports an error rather than claiming success.


## Managed component reuse preflight (LET-80)

Component creation is not the same operation as modifying or deploying an existing component. The BirdEye MCP source includes a preflight for recognized browser-relay changes in workspace_run and every workspace_run_sequence step. It resolves known components from existing release-manifest.json files; it does not create another registry. Explicit creation of the existing birdeye-browser-relay is denied; unavailable inventory fails closed; an existing modification requires matching release-manifest hash and normal BirdEye execution authorization.

The gateway reads release manifests from the trusted BIRDEYE_RELEASES_ROOT environment variable, if configured, otherwise a releases directory adjacent to its installed parent. Verify that root on the actual running server. A model-supplied manifest path is not treated as authority.

Boundary: this is a bounded preflight, not an OS sandbox against arbitrary administrator shell commands. Opaque scripts can conceal effects. Full system-wide enforcement also requires mandatory task/session authorization, machine-qualified effect checks, and client schema compatibility. Do not mark LET-80 accepted solely on these tests. Production rollout must prove Main/Mini ALLOW, DENY, restart and rollback.
