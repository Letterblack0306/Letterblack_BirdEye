# BirdEye local browser tool

The existing BirdEye MCP server now registers `browser_action`. It controls the current Chrome profile through the existing BirdEye Browser Relay extension. Browser Agent's CDP-only runtime was not copied into a second service or a separate browser profile.

## Calling the tool

Discover `browser_action` from the live MCP catalog. For clients whose catalog has not refreshed, the same installed tool is callable through BirdEye workspace_run in the discovered BirdEye directory:

```json
["python","browser_tool.py","--json","{\"action\":\"tabs\",\"request_id\":\"unique-operation-id\"}"]
```

Supported actions: tabs, open, navigate, snapshot, click, type, press, select, scroll, screenshot, back, forward, reload, close, status.

Read the live schema in browser_mcp.py. Target operations require an exact tab_id and expected_url. Snapshot returns visible text and refs; click/type/select/press accept a ref or a unique selector. Keyboard events are synthetic and explicitly report trusted=false. Protected browser pages and file inputs remain restricted; embedded frames need task-specific verification.

Each intended operation has a stable request_id. Check status after a timeout. Identical IDs/payloads return the existing command; conflicting payloads fail. A dispatched operation is not automatically replayed after relay restart. The caller must reconcile uncertain effects. No universal exactly-once effect guarantee is claimed across a browser crash between effect and persistence.

## Existing runtime owners

- mcp_server.py: native tool registration.
- browser_mcp.py: adapter through BirdEye run_command authority and receipts.
- browser_tool.py: local JSON command client and PNG integrity metadata.
- browser_relay/server.py: existing loopback relay, extended general-browser routes.
- browser_relay/browser_commands.py: server-owned durable command journal.
- browser_relay/extension/browser_tools.js: browser operations and result reconciliation.
- browser_relay/extension/browser_heartbeat.js: extension-worker wake-up.
- browser_relay/extension/service_worker.js: existing legacy conversation relay plus browser module.
- browser_relay/extension/manifest.json: installed extension permissions, version 0.2.4.
- browser_relay/install_background_relay.ps1: existing task installer, synchronized for pythonw and battery operation.

The existing scheduled task is BirdEye Browser Relay. It runs hidden with battery start/stop restrictions disabled. The native MCP owner remains BirdEye HTTP MCP; its catalog was refreshed and actual tools/list and tools/call were exercised. No second permanent MCP, browser, agent or execution authority was introduced.

General browser POST routes reject non-extension web origins and require JSON. Bind remains loopback. Existing legacy ChatGPT read/reply routes are preserved.

## Acceptance on 2026-10-21

Observed in actual Chrome and through native Streamable HTTP MCP, including:
- tool discovery, existing tabs and same-profile tab creation;
- title/H1/paragraph/link reads on a temporary local fixture;
- verified text input, click effect, identical-ID click without duplicate effect;
- select, synthetic key handler, open shadow-root ref click, scroll and zero-scroll;
- navigation, back, forward, reload and tab close with confirmed absence;
- PNG capture, inspected pixels, native MCP image block and SHA-256 equality;
- target identity denial, missing selector error and independent continuation;
- relay restart with persisted receipts and same-tab reattachment;
- public website title/text/link read via native MCP;
- 39 existing HTTP MCP / workspace execution regression tests passed.

Representative evidence:
- state/executions/bird-20261021-102734-4ba1331d/receipt.json: input effect.
- state/executions/bird-20261021-102751-f6f94168/receipt.json: inspected capture.
- state/executions/bird-20261021-102832-23331dd2/receipt.json: history effect.
- state/executions/bird-20261021-103029-c808cccb/receipt.json: missing selector.
- state/executions/bird-20261021-103152-fee59599/receipt.json: restart reattachment.
- state/executions/bird-20261021-103533-3b8a4305/receipt.json: public-page read.
- state/executions/bird-20261021-103653-569caeeb/receipt.json: native MCP close.
- state/executions/bird-20261021-103802-966cbcdf/receipt.json: native PNG image delivery.
- state/executions/bird-20261021-104022-b9121311/receipt.json: final installed zero-scroll.

Source changes are uncommitted; unrelated pre-existing work is preserved. Acceptance hashes and current runtime identity are recorded in state/browser-tools-acceptance.json.


## Agent visual feedback (extension 0.3.0)

Agent-controlled web tabs now receive an isolated activity overlay from agent_visuals.js. The existing browser tool path injects it only into controlled tabs. It provides a dark activity panel, an agent-labelled glowing pointer, eased movement to actual element bounds, target outlines, click ripples, and active/done/error/idle states. It does not move the user's physical mouse.

Snapshot scans animate over up to three visible discovered controls. Tool snapshots exclude the overlay; page content and element refs remain usable. Click/type/select/key actions animate to the actual resolved target before execution. Animation is bounded (300–650 ms per movement) and respects prefers-reduced-motion. Alt+Shift+B toggles the overlay on the current page.

Visual feedback is not an authority decision or agent reasoning. A visual feedback failure does not replace the underlying tool result. agent_visual metadata reports the real overlay phase, movement count, animation frames and recent visual events, without claiming that a dispatched action proves the user's entire task completed.

Acceptance: real Chrome frames inspected across a moving probe; click pulse and target outline inspected; typed value and option selection verified; overlay excluded from returned text/refs; error and recovery states exercised; native MCP PNG delivery verified. One timed-out native read was reconciled as completed in the server journal without replay. Representative receipts: bird-20261021-122657-42c9417f (actual click); bird-20261021-123036-9cd11ae9 (reconciled native visual probe); bird-20261021-123038-9dde0309 (native overlay image).


### Non-disruptive focus behavior (extension 0.3.2)

Read probes and new tabs preserve the selected tab by default. Pass active=true only when foreground operation is intended. Background snapshots do not add the visual overlay. Screenshots temporarily select the target within its window, do not focus the OS window, and restore the prior tab unless the user has selected another tab in the meantime. Snapshot element references use an isolated-world Map instead of attributes on application controls. Legacy read/diagnose requests no longer activate ChatGPT; explicit reply operations retain their existing behavior.

Live acceptance: state/browser-focus-acceptance.json. A background ChatGPT read preserved the conversation URL and active tab; an instrumented fixture showed zero control attribute mutations/focus events. Screenshot restoration and background-by-default open passed. The exact ChatGPT conversation-loading error was not reproduced, so its root cause remains unproven.

## Agent discovery and failure classification
Before reporting browser access unavailable, discover browser_action through the live BirdEye MCP tools/list and refresh stale connector catalogs. Prefer native browser_action. Verify relay /health and current browser_client.seen heartbeat. Distinguish external tool-execution rejection, missing connector exposure, relay HTTP failure, extension disconnect, and browser command failure. Never infer a local denial from an external rejection. Preserve exact request_id for status reconciliation; do not blindly replay writes. Require tab_id and expected_url for targeted operations and verify returned page evidence. Do not create another relay or disable browser origin checks to solve discovery problems.
