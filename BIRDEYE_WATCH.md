# BirdEye unified root watch

`birdeye watch` is represented by the repository launcher `birdeye-watch.ps1`.
The BirdEye stdio MCP server also starts this incremental watcher on demand
when an MCP service session opens and stops it when the service session closes.
The watcher is client-neutral: Cline, Codex, ChatGPT-connected runtimes,
local agents, and future MCP clients all consume the same BirdEye capability
surface. A per-state-root lease prevents concurrent BirdEye processes from
creating competing watchers for the same `workspace.db`.
The canonical inventory is the explicit root registry in `config.json`. The
legacy workspace watcher derives a compact Git receipt from the current working
directory; the unified index watcher uses the configured roots and the shared
SQLite `files` table.

The registry distinguishes:

```text
registered   BirdEye knows the path
enabled      the root participates operationally
index        files are enumerated and indexed
hash         content-hash policy, currently sha256
authority    current or historical semantic boundary
```

Historical agent/runtime roots use `root_class=memory` and
`authority=historical`. They are indexed and hashed like other active roots,
but their contents never establish current workspace truth.

All historical inputs are represented by one `memory` root. Its declarative
`sources` list contains the paths for agent runtime logs, agent session logs,
and GPT/ChatGPT history. BirdEye exposes them under virtual paths such as
`memory/sources/cline/...` and `memory/sources/chatgpt/...`; do not create a
separate MCP server or source-specific Python file for each provider.

## One-time receipt

From any project workspace:

```powershell
& "C:\path\to\Letterblack_BirdEye\birdeye-watch.ps1" -Once
```

## Manual continuous watch

```powershell
& "C:\path\to\Letterblack_BirdEye\birdeye-watch.ps1"
```

The standalone watcher is optional when BirdEye MCP is running. Its default
interval is 300 seconds. Override it without changing source:

```powershell
& "C:\path\to\Letterblack_BirdEye\birdeye-watch.ps1" -IntervalSeconds 60
```

## State location

The default state root is `state` relative to the current process. For a
central BirdEye state directory, set either `-StateRoot` or the
`BIRDEYE_STATE_ROOT` environment variable.

Example:

```powershell
$env:BIRDEYE_STATE_ROOT = "C:\MCP Local\Letterblack_BirdEye\state"
& "C:\path\to\Letterblack_BirdEye\birdeye-watch.ps1"
```

## Receipt behavior

Each workspace receives a stable project ID derived from its resolved path.
BirdEye writes:

```text
state/projects/<project-id>/latest.json
state/projects/<project-id>/history.jsonl
```

The receipt contains the workspace root, origin URL, branch, Git HEAD, changed
paths, working-tree diff summary, staged diff summary, timestamp, and a SHA-256
over the stable project state. When that SHA is unchanged, no new history entry
is written.

The unified index additionally records per-file path, size, modification time,
SHA-256, hash status, root policy, and per-root run completion. New or changed
files are hashed; unchanged files reuse their prior hash. A root is not
`CURRENT` unless a completed root run proves it.

The watcher does not copy project source files and does not modify the project
repository.
