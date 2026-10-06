# BirdEye MCP + Workspace: Architecture and Current Status

Status: DOCUMENTED / live-verified 2026-10-06

Scope: `C:\MCP Local\Letterblack_BirdEye` (the "MCP"), the `birdeye` CLI shim, the
`tunnel-client` connector, and the configured workspace roots.

This document exists to stop well-meaning edits. Every runtime claim below is
marked with the evidence that supports it. Anything marked UNVERIFIED must not be
treated as working, and no change may be justified by a skill document, a comment,
a UI label, or a prior "it passed" statement.

---

## 1. Change-control rules (read before editing anything)

### 1.1 Evidence ladder

```
live runtime observation
  > current workspace source
  > current repo git state
  > this document / other project docs
  > historical conversation evidence
  > model inference
```

A lower tier never overrides a higher one.

### 1.2 Prohibited without explicit authorization

Do not perform any of the following because a task "seemed to imply it":

- Editing, deleting, or renaming any file under a configured root.
- Editing `config.json`, `governance.json`, or `birdeye_projection_config.json`.
- Restarting, stopping, or re-launching the tunnel daemon.
- Running `tunnel-client doctor` with the real health port while the tunnel is up.
- Running `birdeye access open` / `access close` to "reset" access.
- Re-indexing, rebuilding, or replaying an EYES domain.
- Writing to any path outside a root that `allowed_write_paths` covers.

Editing requires all three:

1. A stated user request that names the outcome.
2. A located, proven owner for that outcome (file + function), traced in current source.
3. A named falsifier that the current behavior is actually wrong.

Absent all three, the correct action is to report findings, not to edit.

### 1.3 Claim discipline

Never write `works`, `fixed`, `verified`, `done`, or `pass` without the exact
observable that caused that state. Required labels:

| Label | Meaning |
|---|---|
| PROVEN | Observed end-to-end on the live system |
| IMPLEMENTED | Present in source; runtime not observed |
| UNVERIFIED | Evidence insufficient to decide |
| BROKEN | A specific falsifier was observed |
| DOCUMENTED_ONLY | A doc claims it; effect not established |
| STALE | Evidence describes an older revision |

`IMPLEMENTED != PROVEN`. A green test is not runtime proof. Exit code 0 is not
proof the requested effect occurred.

---

## 2. Architecture

### 2.1 `birdeye` CLI shim

`birdeye` is **not** a standalone CLI. It is a two-line shim:

- `C:\Users\prave\.local\bin\birdeye.cmd` → forwards `%*` to `birdeye.py`
- `birdeye.py` → dispatches; anything that is not `access` goes to `mcp_server.py`

Consequences that look like bugs but are not:

- `birdeye --version` / `--help` fail. They are forwarded to `mcp_server.py`, a
  stdio MCP server using argparse, which rejects them with a usage error. There is
  no version flag.
- `workspace_identity` and `revision_status` return `GovernanceError: Multiple
  workspace roots are configured`. That is correct — they require an explicit
  workspace root name because this MCP serves many roots.

### 2.2 Access lease

`birdeye.py` implements exactly three subcommands under `access`:

| Subcommand | Effect |
|---|---|
| `open` | sets `open: true`, refreshes `last_activity` |
| `close` | sets `open: false` |
| `status` | prints the lease JSON |

- There is **no `start` subcommand.** `open` is the equivalent. Any other value
  prints `unknown access subcommand: <x>` and exits 1.
- `access --help` also fails; `--help` falls into the same else-branch. Bare
  `birdeye access` prints the valid list.
- Lease file: `state/access_tunnel.json`, overridable via `BIRDEYE_HOME`.

Caveat: `last_activity` is written **only** when `open`/`close` is called. Nothing
refreshes it on a timer and there is no `touch` subcommand. If a consumer treats
it as a sliding-expiry heartbeat, a long-open window will look stale despite
`open: true`. Renewing requires a fresh `birdeye access open`.

`remote_transport.py` gates connections on this lease. See
`AUTHENTICATED_REMOTE_TRANSPORT.md`.

### 2.3 Execution authority

Per `docs/RUNTIME_BOUNDARY.md`:

- `workspace_bridge.run_command` is the **execution authority**.
- The model may propose an argv array; the bridge resolves the workspace, applies
  the command policy, and executes with explicit cwd and `shell=False`.
- Mutating commands (`git add`, `git commit`, `npm install`, `pip install`) require
  both `capability: workspace.mutate` and `context_evidence`.
- BirdEye / GPT-Knowledge can supply context evidence. They are **not** execution
  authority.
### 2.4 Governance policy

`governance.json` is the enforcement surface. Actual current values:

- `allowed_read_paths`: `["."]`
- `allowed_write_paths`: **`[]` (empty — no writes authorized by policy)**
- `max_changed_files`: `0`
- `max_patch_bytes`: `0`
- `require_clean_base_hash`: `true`
- `store_only_verified_repairs`: `true`

`max_changed_files: 0` plus an empty `allowed_write_paths` is the single most
important fact here: **the policy as configured authorizes no mutation at all.**
Any write is outside policy and must not be attempted without an explicit user
decision to change governance first.

Allowed commands by default were a closed allowlist (git read verbs, `node --version`,
`npm.cmd` test/lint/check/build, `python` version/pytest/unittest).

As of 2026-10-06, `config.json` supports `"execution_policy": "unrestricted"` and `"allow_global_execution": true` (with `extra_allowed_executables`), enabling `powershell.exe`, `where.exe`, `realityscan.exe`, build executables, and custom scripts through `workspace_run` and `workspace_run_sequence`.

Dangerous system-wiping commands remain blocked (`diskpart`, `format`, `bcdedit`, `reg`, `shutdown`, `net stop/start`, `sc delete`, `git reset --hard`, `git clean -fd*`, `git push --force`).

Note: `powershell` is blocked **through the governed bridge**. That does not apply
to the user's own interactive shell, which is a separate, authorized path.

Forbidden globs cover VCS/build/cache directories, archives, binaries,
certificates/keys, `.env`, `credentials*`, `secrets*`, and the legacy workspace
databases.

### 2.5 Index roots

`config.json` `roots[]` defines every indexed root, each with `root_class`
(`workspace` / `knowledge` / `memory`), `hash: sha256`, a `git` flag, and
`exclusions` (`.git`, `node_modules`, `dist`, `build`, `coverage`).

**The filesystem-tool allowlist and the index roots are different surfaces.** A
path can be reachable by a file tool while absent from the index, and vice versa.
Do not treat "the tool rejected it" as proof that boundary enforcement works — that
must be proven against a path whose parent *is* in scope.
---

## 3. Current status (live, 2026-10-05)

### 3.1 EYES projections — PROVEN healthy

All three domains have **lag 0**, `journal_replayable: true`, `rebuildable: true`:

| Domain | Canonical gen | Applied gen | Lag | Owner |
|---|---|---|---|---|
| workspace | 555309 | 555309 | 0 | EYES query projector |
| memory | 1142 | 1142 | 0 | Memory deterministic vector projector |
| skills | 1377 | 1377 | 0 | EYES query projector |

`generation_lag_zero: true`. Legacy `workspace.db` is
`authority: compatibility-only`; `legacy_retirement_switch: false` (inactive).

Do **not** run `eyes_rebuild` — projections are already consistent and rebuilding
would discard a known-good state for no gain.

### 3.2 Index health — two roots PARTIAL (pre-existing)

| Root | Indexed | Hashed | Unresolved | Status |
|---|---|---|---|---|
| `sam-ginie` (`D:\2026\SAM_GINIE`) | 2845 | 2426 | **419** | PARTIAL |
| `agents-memory-tool-v6-integration` | 2800 | 2752 | **48** | PARTIAL |

All other roots report `CURRENT` with 0 unresolved.

`sam-ginie` additionally has `last_run_status: null` and null start/completion
timestamps — it has never completed a clean indexing run. Treat its index as
**incomplete**: search results from it may be silently missing files.

### 3.3 Connector tunnel — PROVEN ready

- Health: `GET http://127.0.0.1:8767/readyz` → `200 ready`
- PID `38008`, started `2026-10-05 18:04:31`, running
  `tunnel-client.exe run --profile local-mini --health.listen-addr 127.0.0.1:8767`

**Do not stop or restart this to "clean up".** `refresh_connector.ps1` restarts it
deliberately and only after doctor passes.

### 3.4 Repo state — DIRTY (preserve it)

Repo `C:\MCP Local\Letterblack_BirdEye`, HEAD `c692b78c8a2` ("fix(birdeye-projection):
remove false-completion and overstated-evidence defects").

There are **modified tracked files** and many **untracked** files:

```
M browser_relay/extension/content.js
M browser_relay/extension/service_worker.js
M browser_relay/server.py
M config.json
M eye_workspace.json
?? .agent/  ?? SKILL.md  ?? birdeye.py  ?? _own_part*.py  ?? (logs, images, more)
```

This dirt is **pre-existing work, not debris.** Do not run `git clean`, `git stash`,
`git checkout -- .`, or `git restore .`. All are blocked by policy for this reason.
Committing or discarding is a user decision.

There are also stray debug artifacts at repo root (`screen_*.png`, `cdp_probe*.mjs`,
`__err_test.log`, `__out_test.log`). They are untracked, not gitignored, and **must
not be deleted** — deleting is not a cleanup, it is an unverified mutation of
someone's investigation state.

### 3.5 In-flight task

`.agent/evidence/CURRENT_TASK.md` documents an active EYES workspace-projection
repair. Read it before touching projection code. It records its own allowed edit
paths; work outside that list is out of scope for that task.

---

## 4. Connector troubleshooting (evidence, not folklore)

`tunnel-client` is **not on PATH.** Binary:
`C:\Users\prave\AppData\Local\tunnel-client\bin\tunnel-client.exe`
(confirmed absent from both process and User PATH). Always invoke by full path.
Advice to run bare `tunnel-client ...` will fail.

Refresh workflow: `C:\Users\prave\tools\local-mini-remote-control\refresh_connector.ps1`

- Resolves `CONTROL_PLANE_API_KEY` from User env, then process env, then
  `remote_bridge\tunnel_credentials.env`. The credential file **does** contain it,
  so doctor passes even though the variable is unset in the shell. A doctor run that
  omits this fallback will falsely report `control_plane_api_key FAIL`.
- Before calling doctor it probes the health port; if held by the same profile it
  substitutes a throwaway loopback port. On a race it may briefly pass the real
  port and collide.

**Expected failure mode:** `tunnel-client doctor --profile local-mini` with no
`--health.listen-addr` override targets the YAML default `127.0.0.1:8767`, which the
running tunnel already owns → `health_listener FAIL`, exit 2. This is not a fault.
Use the script, or pass a free port explicitly.

Credentials must never be echoed. `MCP_EXTRA_HEADERS` carries a bearer token.

### 4.1 Known tunnel-client toolchain bug

On this machine, **both** `tunnel-client codex plugin install` and
`... codex plugin export` abort with:

```
tunnel-client binary is not executable: ...\tunnel-client.exe
```

while `doctor`, `--help`, and `codex diagnose` run fine against the same binary. The
exe is a normal 22 MB Windows binary (Attributes `Archive`, no Unix mode bits). This
is a POSIX-style exec-bit check applied on Windows — a bug in tunnel-client
`0.0.15+a390c16`, not an environment fault.

Consequence: **`codex plugin install` reports failure even when it succeeds** (it
copies the bundle before tripping the guard). Trust resulting state, not the exit
code; use `codex diagnose` to verify.

### 4.2 Codex plugin status

`tunnel-client codex diagnose` reports:

- Plugin installed: `true` at
  `C:\Users\prave\.codex\plugins\cache\debug\tunnel-mcp\local` (key
  `tunnel-mcp@debug`); doctor reports `codex_plugin PASS`
- Codex bridge: `State: ready`, app-server supported, assistant ready
- **`Binary hint file found: false`** — `.tunnel-client-bin` is absent, so the
  plugin router falls back to `Resolved tunnel-client source: current_process`. Works
  today, fragile across restarts. The CLI exposes no flag to write the hint.

---

## 5. Quick verification commands

Read-only. None mutate state.

```powershell
# tunnel readiness
Invoke-WebRequest http://127.0.0.1:8767/readyz -UseBasicParsing -TimeoutSec 3

# access lease (bare 'birdeye access' lists valid subcommands)
birdeye access status

# tunnel-client doctor — MUST use a free port while the tunnel is running
$l=[System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback,0)
$l.Start(); $p=([System.Net.IPEndPoint]$l.LocalEndpoint).Port; $l.Stop()
$env:CONTROL_PLANE_API_KEY = <from remote_bridge\tunnel_credentials.env>
& "$env:LOCALAPPDATA\tunnel-client\bin\tunnel-client.exe" doctor `
    --profile local-mini --health.listen-addr "127.0.0.1:$p"
```

Never print the API key. Never pass it as a literal on the command line — the
profile YAML references it as `env:CONTROL_PLANE_API_KEY` by design.