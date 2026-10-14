# Governance and Rule Discovery — Agent Routing

**Purpose:** Let agents discover the correct current rule, its enforcing owner, applicable scope, required evidence and legitimate resolution path. This is a navigation document, **not a new gate, replacement policy, or permanent prohibition**. Read [Repository Guide](REPOSITORY_GUIDE.md) for BirdEye ownership and change discipline.

## Where rules actually live

| Source | Start with | What it establishes | What it does not establish |
| --- | --- | --- | --- |
| Active local workspace | `AGENTS.md`, nested instructions, active task/issue definitions, installed hooks/configuration and relevant source | Rules applicable to **this** operation and revision | Applicability to other workspaces |
| BirdEye | [Repository Guide](REPOSITORY_GUIDE.md), `governance.json`, `config.json`, current source, `workspace_identity`, `revision_status`, `workspace_run` receipts | Configured roots, execution observations and actual effect | A reasoner's intent or permission solely from tool availability |
| [GPT-Knowledge](https://github.com/Letterblack0306/GPT-Knowledge) | [Start Here](https://github.com/Letterblack0306/GPT-Knowledge/blob/main/000_START_HERE.md), [Knowledge Router](https://github.com/Letterblack0306/GPT-Knowledge/blob/main/INDEX.md), [machine router](https://github.com/Letterblack0306/GPT-Knowledge/blob/main/knowledge-index.json), [Failure Patterns](https://github.com/Letterblack0306/GPT-Knowledge/blob/main/project-engineering/gpt-agent-failure-patterns-and-correction.md) | Evidence methods, decisions and recoverable-blocker diagnostics | Live project authority or execution proof |
| [LB Guards Rules](https://github.com/Letterblack0306/LB_Guards_Rules) | [README](https://github.com/Letterblack0306/LB_Guards_Rules/blob/main/README.md), [Guard Gallery](https://github.com/Letterblack0306/LB_Guards_Rules/blob/main/GUARD_GALLERY.md), [registry](https://github.com/Letterblack0306/LB_Guards_Rules/blob/main/src/guards/index.js), [default policy](https://github.com/Letterblack0306/LB_Guards_Rules/blob/main/.workspace-trust/policy.json) | Available implementations, policy defaults and documented remediation | That a guard is installed, enabled or invoked in the target workspace |
| [LetterBlack LBE Core](https://github.com/Letterblack0306/LetterBlack-LBE-Core) | [Root agent scope](https://github.com/Letterblack0306/LetterBlack-LBE-Core/blob/main/AGENTS.md), [active task](https://github.com/Letterblack0306/LetterBlack-LBE-Core/blob/main/tasks/ACTIVE_TASK.json), [nested agent rules](https://github.com/Letterblack0306/LetterBlack-LBE-Core/blob/main/LBE_Core_Engine/AGENTS.md), [active issue ledger](https://github.com/Letterblack0306/LetterBlack-LBE-Core/blob/main/LBE_Core_Engine/.governance/ISSUE_LEDGER.json), [governance index](https://github.com/Letterblack0306/LetterBlack-LBE-Core/blob/main/LBE_Core_Engine/docs/governance/DOC_INDEX.json), [validation gates](https://github.com/Letterblack0306/LetterBlack-LBE-Core/blob/main/LBE_Core_Engine/VALIDATION_GATES.md) | Task, issue, validation and release constraints for that repository | Automatic permission to change its task/issue scope |
| [Evidence & Governance Auditor](https://chatgpt.com/plugins/plugins_6acbc591b0608191b2cede6f8c8f4bbf) | Installed skill and its current release | Selective investigation/correction method | A higher-priority authority than the active policy |

These are **navigation references**; always inspect current commits, local state, and actual invocations. Remote GitHub changes may not yet exist on the machine's local GPT-K checkout or in BirdEye's SQLite index.

## Fast agent discovery procedure

1. **Resolve target:** determine requested operation, real machine, process, workspace, branch/HEAD, dirty files, and relevant current policy owner. Use BirdEye `workspace_identity`, `revision_status`, `birdeye_roots`, `knowledge_route`, `knowledge_read`, `birdeye_inspect`, and `birdeye_search` when available. Search results can be stale; a failed search or locked index does not prove a document is absent.
2. **Find the *applicable* rule:** inspect the nearest active `AGENTS.md`/task/issue authority and the installed execution policy. For LBE, distinguish root active-task scope from nested active-issue scope. Do not combine different worktrees, permissions, commits, or documents into a fictitious single policy.
3. **Classify the condition:**
   - **Security/authorization:** identity, credentials, DENY, protected operation or target — enforce until legitimately authorized.
   - **Scope/task:** permitted files, issue registration, task mode — satisfy via the project's approved change path.
   - **Development validation:** lint, test, import, registry, code or evidence check — repair within authorized scope, then re-run.
   - **Release gate:** version, signing, HEAD-bound proof, artifacts, deployment or publishing — enforce at its designated stage.
   - **Advisory/documentation:** historical notes, plans, best practices and information gaps — guide decisions, but do not invent execution DENY.
4. **Find the remediation:** identify the exact denying command/check, owner, source, required evidence, approved repair command/process, and who can authorize any needed scope change. Consult the relevant source directly; do not invent a universal 'fix all blockers' command.
5. **Continue:** fulfil prerequisites already authorized, re-evaluate the same gate, resume the original user objective, observe the real execution effect and correlated receipt. Do not request repeated permission where scope already allows the next step.
6. **Escalate only the boundary:** when further progress needs new approval, credentials or a prohibited scope, preserve ongoing work and report that exact condition. Do **not** override DENY, edit guards to force PASS, forge proof or self-approve expanded rights. An unresolved blocked operation is not necessarily a permanently abandoned objective.

## Truth labels and receipts

Report `PROVEN`, `IMPLEMENTED`, `DOCUMENTED`, `INFERRED`, `UNVERIFIED`, `STALE`, or a *specific* `BLOCKED` condition. Where diagnosing a failed attempt, distinguish tool/transport failure, index failure, stale data, configuration, policy denial and observed target behavior.

Required claim chain when applicable: **request → trusted identity → authorization → execution → actual effect → correlated receipt**. A passing test, DENY-path result, registered tool or new document cannot by itself prove an ALLOW-path behavior works.

## BirdEye-specific maintenance notes

- Before changing BirdEye itself, follow the [Repository Guide](REPOSITORY_GUIDE.md) and inspect **live** `governance.json`; do not assume GitHub write permissions override machine-side policy.
- Preserve all unrelated local changes. Prefer canonical remote documentation edits and reconcile them safely with a dirty checkout rather than replacing modified source or blindly pulling.
- The current LBE [Issue #16](https://github.com/Letterblack0306/LetterBlack-LBE-Core/issues/16) records proposed documentation alignment; an open issue is **not** proof the instructions have changed.
- This index does not assert all rules are simultaneously enforced. Enumerate actual active rules from the resolved policy and invoking path when a total or comprehensive audit is required.
