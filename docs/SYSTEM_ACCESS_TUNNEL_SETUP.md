# Opt-in system access tunnel

## Purpose

BirdEye normally exposes evidence, indexing, workspace identity, and governed workspace-scoped execution. System-wide command execution is a separate, user-controlled capability and MUST remain closed unless the local user explicitly opens it.

This setup is intended for temporary application testing, repair, diagnostics, or other local work that cannot be completed inside a configured workspace.

## Required command surface

The local installer/agent MUST provide this user-facing CLI contract:

```text
birdeye access open
birdeye access status
birdeye access close
```

Running `birdeye` without arguments MAY show an interactive control, but it MUST NOT silently open system access.

## Fail-closed state

System access MUST be CLOSED when:

- it has never been explicitly opened;
- the machine or service restarts;
- the lease expires;
- the user runs `birdeye access close`;
- the access-state file is missing, malformed, expired, or cannot be validated.

No agent, MCP request, startup hook, scheduled task, or child process may open the tunnel on the user's behalf.

## Local setup contract

This repository branch documents the setup contract; it does not claim that a machine has already installed the launcher.

An installation agent should:

1. Install a `birdeye` launcher on the user's local PATH.
2. Store access state under BirdEye's local state directory, not in Git-tracked configuration.
3. On `birdeye access open`, require an interactive local terminal and create a short-lived local lease containing a random session identifier, opening time, expiry/inactivity policy, and owner process/user information.
4. Keep the endpoint local-machine only. Do not bind the command capability to a LAN/public interface.
5. Expose system-wide execution only while that lease is valid.
6. On `birdeye access close`, revoke the lease immediately and terminate any tunnel-owned helper process.
7. Never persist OPEN across reboot/restart.
8. Journal each execution with actor/caller, working directory, argv, start/end time, exit code, timeout, and available execution evidence. Do not journal secret values.
9. Keep normal BirdEye evidence/search capabilities available independently of the system-access lease.

## Execution boundary

Opening the tunnel enables transport; it does not remove execution controls.

The system-wide executor SHOULD accept argv arrays and use `shell=False` by default. PowerShell/cmd are available only because the user explicitly opened system access; their invocation still needs to be represented as explicit argv and recorded.

The executor MUST NOT:

- silently elevate to Administrator;
- bypass Windows UAC;
- expose a remote unauthenticated shell;
- treat an agent request as equivalent to the user's `access open`;
- persist credentials, tokens, or secrets in receipts;
- modify the repository merely to represent the runtime OPEN/CLOSED state.

If elevation is required for a particular command, that is a separate explicit local-user approval event.

## Relationship to existing workspace execution

Existing `workspace_run` and `workspace_run_sequence` remain the default execution path. Their workspace confinement and command policy are not weakened by this feature.

Use system access only when the requested operation genuinely needs to cross a configured workspace boundary, launch/test another local application, inspect machine-level state, or invoke an explicitly requested shell command.

## Agent decision rule

Before execution:

```text
Can workspace_run satisfy the operation?
  YES -> use workspace_run / workspace_run_sequence
  NO  -> query system-access status
          CLOSED -> report that the user must run: birdeye access open
          OPEN   -> use the system-access executor and preserve its receipt
```

Agents MUST NOT ask another agent/process to open the tunnel as a workaround.

After the requested system-wide work is complete, the agent should tell the user that system access is still open if the lease remains active. The agent MUST NOT close a user-opened tunnel unless the user requested closure or the lease policy expires it.

## Acceptance evidence

Do not call this feature installed or working until local runtime evidence proves all of the following:

- `birdeye access status` reports CLOSED before opening;
- execution through the system-wide surface is denied while CLOSED;
- `birdeye access open` from an interactive local terminal produces OPEN;
- an allowed PowerShell diagnostic executes while OPEN and produces a receipt;
- the same system-wide execution is denied after `birdeye access close`;
- restart/reboot does not restore OPEN;
- the endpoint is local-only;
- malformed/expired state fails closed;
- existing workspace-scoped execution still behaves according to its existing policy.

Static tests or successful installation alone do not prove the runtime capability.

## Suggested defaults

A local implementation may use a 30-minute inactivity lease, refreshed only by successful authenticated use of the system-access surface. The user can always close it earlier with:

```text
birdeye access close
```

The inactivity duration is configuration, not authority: expiry must always transition to CLOSED.
