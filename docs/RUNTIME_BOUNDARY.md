# LBE runtime boundary

See `ARCHITECTURE_AND_STATUS.md` for the full architecture, live status, and the
change-control rules that govern edits in this repo.

`workspace_bridge.run_command` is the execution authority for workspace commands.
The model may propose an argv array, but the bridge resolves the configured
workspace, applies the command policy (including global/unrestricted mode configured in `config.json`), and executes with an explicit cwd and
`shell=False`.

Read-only commands can execute without an external context provider. A command
classified as mutating (for example `git add`, `git commit`, `npm install`, or
`pip install`) must carry both:

```json
{
  "capability": "workspace.mutate",
  "context_evidence": {"workspace": "demo"}
}
```

The response includes an `authority` decision and a `reconciliation` record.
Execution evidence captures stdout, stderr, exit status, timing, and repository
state before and after the command. BirdEye/GPT-Knowledge can provide context
evidence, but they do not become execution authority.

Failures are classified as `timeout`, `process_failure`, or
`execution_failure`. The bridge records failures by resolved workspace, intent,
and failure class. Two consecutive failures of the same class open a durable,
intent-scoped circuit. A successful execution clears only that intent's state;
other intents remain available.
