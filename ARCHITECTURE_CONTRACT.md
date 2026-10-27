# BirdEye MCP — Oracle-Only Architecture Contract
Approved by user: 2026-10-26 (Asia/Dubai).
Status: Architecture approved. Implementation and operational acceptance are separate.

## Authority
Oracle Cloud hosts ONE canonical BirdEye MCP service and its central coordination, memory, persistent searchable index, configuration, governance, and execution evidence. Main PC (LETTERBLACK) and Mini PC (LetterBlack_Min) are remote capability/data sources; neither is a second canonical BirdEye server.

## Connections and execution
ChatGPT, Codex, Cline, and other authorized MCP clients connect to Oracle's MCP endpoint. Oracle uses existing WireGuard, SSH, and machine coordinator to reach either registered Windows PC. Do not introduce new local adapters or duplicate MCP services without a proven necessity and explicit approval.
Host-specific workspace paths name locations for remote operations; they do not identify where BirdEye MCP is installed.
Request flow: client -> Oracle MCP -> choose machine and authorize -> secured remote execution -> output/effects/receipts to Oracle and client.

## Data and offline operation
Oracle continues to serve MCP and search previously ingested records when both PCs are offline. Offline hosts cannot fulfill live execution and must report unavailable.
Existing Windows capabilities may discover changes in explicitly configured roots; Oracle retains the authoritative index and machine-qualified provenance. Favor incremental updates and durable acknowledgment/reconciliation rather than full copies or parallel indexing authorities.

## Preservation and change control
Preserve all existing local files, running services, credentials, and uncommitted changes. No restarts, removals, database migrations, or further configuration modifications are authorized merely by publishing this contract.
A prior Oracle machine-registry change from C:\BirdEye to G:\Dev\MCP\BirdEye for Main does NOT establish a required local BirdEye installation. Validate the workspace purpose and real path before further edits.
Architecture deviations require discussion and approval BEFORE action.

## Acceptance criteria
1. Oracle's authenticated MCP tools, retained memory and search work with BOTH Windows PCs disconnected.
2. Main and Mini each reconnect and report correct machine identity and registered workspace roots.
3. Authorized commands targeting each PC actually execute and generate verified machine-specific effects and receipts.
4. Unauthorized commands and unavailable/offline targets are correctly denied.
5. Configured file create/change/delete operations become discoverable in Oracle's searchable index with host provenance, including after reconnect; no lost changes or unnecessary full indexing.
6. There is only one canonical BirdEye MCP authority.

## Known baseline
Oracle birdeye-mcp.service and birdeye-cloud-control.service were active at the last direct check. Main PC remote execution, full combined indexing, Mini memory projection parity, and dual-PC-offline independence remain unverified.