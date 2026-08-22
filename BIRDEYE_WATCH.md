# BirdEye workspace watch

`birdeye watch` is represented by the repository launcher `birdeye-watch.ps1`.
It derives the project from the current working directory, preferring the
nearest Git repository root. No project path is hardcoded.

## One-time receipt

From any project workspace:

```powershell
& "C:\path\to\Letterblack_BirdEye\birdeye-watch.ps1" -Once
```

## Continuous watch

```powershell
& "C:\path\to\Letterblack_BirdEye\birdeye-watch.ps1"
```

The default interval is 300 seconds. Override it without changing source:

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

The watcher does not copy project source files and does not modify the project
repository.
