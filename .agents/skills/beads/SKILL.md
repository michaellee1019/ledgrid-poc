---
name: beads
description: Use for Beads task tracking and ledgrid-poc delivery orchestration. Trigger on Beads work, ready/blocked/claim/close requests, backlog recovery, or the single-word commands start, resume, keep going, and stop in this repository.
---

# Beads

Use Beads as the durable project state. Keep implementation, coordination, blockers, and handoffs there instead of markdown plans or ad hoc memory files.

## First Step

Run:

```bash
bd prime
```

If it prints nothing, run `bd where`.

## Choose the Mode

- For an ordinary scoped request, use the compact CLI workflow below.
- When the user says `start`, run the repository's Beads Autopilot contract from `AGENTS.md`. `resume` and `keep going` continue it. `stop` quiesces it.

The `start` command is explicit authority for local Beads mutations, local worktrees and branches, model-directed subagents, local commits, serial integration, and continued dispatch during the session. It is not authority to push, squash to `main`, operate hardware, deploy destructively, or make a new product decision.

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

Use [AGENTS.md — Model selection](../../../AGENTS.md#model-selection) as the authoritative routing policy. The coordinator defaults to Astra/high; route bounded implementation to Terra/high, complex work to Sol/high, and the hardest reasoning to Astra/high. Luna/low or medium is only for mechanical work with objective results. Follow that policy's escalation, supported-effort, and context-handoff rules rather than hard-coding all workers to one model. Small scoped requests do not need delegation.

Implementation workers:

- Use at most two concurrently.
- Select the model for the Bead's complexity and consequence of error using the policy above.
- Give each worker one claimed Bead, one isolated worktree and `codex/` branch, a current base SHA, and non-overlapping ownership.
- Require the smallest complete product increment, baseline checks, one logical commit, and a concise Beads handoff. The worker marks `merge-ready`; the coordinator integrates and closes.

Portfolio steward:

- Use at most one; default to Sol/high and escalate to Astra/high for unresolved cross-cutting constraints.
- Follow AGENTS.md's steward cadence: before dispatch when the wave is missing/stale, after a batch of up to 15 selected Beads, or earlier on its stall, dependency, or state-drift triggers.
- Keep the review bounded and normally Beads-only. Prepare or repair at most 15 leaves in dependency-ordered waves, clarify from accepted decisions, and leave a short epic comment. Only the current runnable wave receives `worktree-ready`; later waves remain unclaimed.

Independent acceptance:

- Use a separate reviewer in an isolated worktree before integration when AGENTS.md requires it. Default to Sol/high for ordinary user-visible acceptance and bounded modernization evidence; use Astra/high for shared Scene/schema, compositor/protocol, safety/data integrity, and risky live-state changes.
- A stronger implementation model does not waive independent review. Apply the existing demo-blocker threshold and record lesser findings as follow-up Beads.
- Keep one coordinator, at most two implementation workers, and at most one steward or reviewer within the four-agent limit.

## Delivery Loop

Claim immediately before launch. Never overlap exact conflict domains or shared generated-output ownership. The coordinator keeps the integration branch and merge slot.

For ordinary leaves, validation is changed-language syntax or lint, focused tests, and `git diff --check`. Add one adjacent regression only for a shared contract and one browser smoke only at a user-visible browser boundary. Reserve broad suites for release or hardware boundaries.

When a worker finishes: inspect its handoff, obtain required independent acceptance, acquire the merge slot, rebase onto the current integration tip, rerun focused checks, fast-forward merge, record the integrated SHA, close the Bead, release the slot, clean the merged worktree, and refill the free implementation slot. Continue until stopped, genuinely blocked, or new authority is required.

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
