---
name: beads
description: Use for Beads task tracking and ledgrid-poc delivery orchestration. Trigger on Beads work, ready/blocked/claim/close requests, backlog recovery, or the single-word commands start, resume, keep going, and stop in this repository.
---

# Beads

Use Beads as the durable project state. Keep implementation, coordination, blockers, and handoffs there instead of markdown plans or ad hoc memory files.

## First Step

Run `bd prime` when session context is missing or stale. Reuse context already loaded in this session; do not repeat it for each scoped follow-up.

If it prints nothing, run `bd where`.

## Choose the Mode

- For an ordinary scoped request, use the compact CLI workflow below.
- When the user says `start`, run the repository's Beads Autopilot contract from `AGENTS.md`. `resume` and `keep going` continue it. `stop` quiesces it.

The `start` command is explicit authority for local Beads mutations, local worktrees and branches, model-directed subagents, local commits, serial integration, and continued dispatch during the session. It is not authority to push, squash to `main`, operate hardware, deploy destructively, or make a new product decision.

## Small fixes

Apply [AGENTS.md — Small-fix fast path](../../../AGENTS.md#small-fix-fast-path) before choosing agents or worktrees. The coordinator handles understood local corrections directly; a clear accepted leaf does not need a steward or separate implementation worker. Ordinary scoped requests use the compact workflow, without a full backlog/wave refresh. This changes process, not permissions.

Preserve independent acceptance for visible behavior and stronger review for shared contracts/live-state integrity. For a small visible fix, use one focused Sol/medium review of the exact candidate and decisive regression; routine documentation/mechanical edits can be coordinator-reviewed. Browser and benchmark work require the triggers in AGENTS.md. Record checks once against the candidate and reuse them unless relevant code, base, environment, or evidence changes.

## Autopilot Startup

Reconcile the four durable local planes plus the live agent tree:

```bash
bd list --status=in_progress
bd merge-slot check
bd worktree list
git worktree list --porcelain
```

Fix orphaned claims or worktrees before dispatch. Preserve dirty work. Use `bd ready --json`, then select the highest-priority unassigned product leaf under `ledgrid-poc-ib7`. Favor visible behavior, safety and operability, critical-path unblockers, and complexity deletion over validation, inventory, documentation, or coordination.

Do not require a perfect execution card. A dispatchable leaf needs clear enough acceptance, one ownership/conflict area, and focused validation. Refine only what the next worker actually needs.

## Model Roles

Use [AGENTS.md — Model selection](../../../AGENTS.md#model-selection) as the authoritative routing policy. The coordinator handles small fixes directly. When delegation adds value, route bounded implementation to Terra/medium (low for straightforward corrections), complex work to Sol/high, and the hardest reasoning to Astra/high. Luna/low or medium is only for mechanical work with objective results. Follow that policy's escalation, supported-effort, and context-handoff rules rather than hard-coding all workers to one model. Small scoped requests do not need delegation.

Implementation workers:

- Use at most two concurrently.
- Select the model for the Bead's complexity and consequence of error using the policy above.
- Give each worker one claimed Bead, one isolated worktree and `codex/` branch, a current base SHA, and non-overlapping ownership.
- Require the smallest complete product increment, baseline checks, one logical commit, and a concise Beads handoff. The worker marks `merge-ready`; the coordinator integrates and closes.

Portfolio steward:

- Use at most one; default to Sol/high and escalate to Astra/high for unresolved cross-cutting constraints.
- Use a steward for unresolved portfolio ambiguity or useful multi-workstream planning. At batch boundaries, assess the need; stale metadata alone does not trigger an agent.
- Keep the review bounded and normally Beads-only. Prepare or repair at most 15 leaves in dependency-ordered waves, clarify from accepted decisions, and leave a short epic comment. Only the current runnable wave receives `worktree-ready`; later waves remain unclaimed.

Independent acceptance:

- Use a separate reviewer in an isolated worktree before integration when AGENTS.md requires it. Use Sol/medium for focused small-fix review and Sol/high for new visible features or modernization evidence; use Astra/high for shared Scene/schema, compositor/protocol, safety/data integrity, and risky live-state changes.
- A stronger implementation model does not waive independent review. Apply the existing demo-blocker threshold and record lesser findings as follow-up Beads.
- Keep one coordinator, at most two implementation workers, and at most one steward or reviewer within the four-agent limit.

## Delivery Loop

Claim immediately before launch. Never overlap exact conflict domains or shared generated-output ownership. The coordinator keeps the integration branch and merge slot.

For ordinary code leaves, use changed-language syntax/lint, focused regression and relevant adjacent tests, and `git diff --check`. Documentation edits need consistency checks. Follow AGENTS.md for browser and benchmark triggers; do not turn a local correction into an evidence program. Reserve broad suites for release or cross-cutting risk.

When a worker finishes: inspect its handoff, obtain required independent acceptance, acquire the merge slot, rebase onto the recorded current integration tip, rerun affected checks only if the tested candidate or relevant environment changed, fast-forward merge, record the integrated SHA, close the Bead, release the slot, clean the merged worktree, and refill the free implementation slot. Continue until stopped, genuinely blocked, or new authority is required.

On `stop`, interrupt workers, wait for the live tree to empty, release the merge slot, reconcile every `in_progress` Bead, and preserve dirty or unmerged worktrees.

## Compact CLI Workflow

Find and inspect work:

```bash
bd ready
bd show <id>
```

Claim atomically:

```bash
bd update <id> --claim
```

Create follow-up work only when it must survive the session:

```bash
bd create "Short title" --description="Why this exists and what needs to be done" --type=task --priority=2
```

Close completed work with the result:

```bash
bd close <id> --reason="Completed"
```

## Rules

- Use `bd` for shared tasks, blockers, dependencies, handoffs, and persistent decisions.
- Do not create markdown TODO or memory files as project state.
- Do not use `bd edit`; it opens an interactive editor. Use `bd update` flags instead.
- Prefer `--json` when parsing `bd` output programmatically.
- Do not auto-close or mutate tasks unless the work is actually complete.
- Treat the Dolt database as issue state; use `bd dolt push`/pull for cross-machine sync. `.beads/issues.jsonl` is only an export.
- Do not stage `.beads/**`, traces, screenshots, or local run state, except `.beads/interactions.jsonl`. `bd audit` defines that file as an append-only audit log intended for Git versioning: validate each line as JSON and check IDs are unique before committing it. Its Git commit is not a Dolt sync or backup.
- Local worker commits and coordinator integration are authorized only under the repository's `start` contract; pushes and hardware remain separately authorized.
