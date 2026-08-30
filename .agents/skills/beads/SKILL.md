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

Implementation workers:

- Use at most two concurrently.
- Spawn `gpt-5.6-terra` with reasoning effort `high`.
- Give each worker one claimed Bead, one isolated worktree and `codex/` branch, a current base SHA, and non-overlapping ownership.
- Require the smallest complete product increment, baseline checks, one logical commit, and a concise Beads handoff. The worker marks `merge-ready`; the coordinator integrates and closes.

Portfolio steward:

- Use at most one, with `gpt-5.6-sol` and reasoning effort `xhigh`.
- Invoke after three integrated product Beads, when no P0/P1 product leaf is ready, after a repeated stall, when queue/worktree/claim state disagrees, when a conflict domain is starved, or when the next step is ambiguous.
- Keep the review bounded and normally Beads-only. Repair priorities and dependencies, retire stale coordination/test work, clarify from existing decisions, and prepare only the next one to three leaves.
- Leave a short epic comment covering big-picture progress, next critical path, and stalled, starved, or orphaned work. Ask the user only if resolution changes product intent or authority.

## Delivery Loop

Claim immediately before launch. Never overlap exact conflict domains or shared generated-output ownership. The coordinator keeps the integration branch and merge slot.

For ordinary leaves, validation is changed-language syntax or lint, focused tests, and `git diff --check`. Add one adjacent regression only for a shared contract and one browser smoke only at a user-visible browser boundary. Reserve broad suites for release or hardware boundaries.

When a worker finishes: inspect its handoff, acquire the merge slot, rebase onto the current integration tip, rerun focused checks, fast-forward merge, record the integrated SHA, close the Bead, release the slot, clean the merged worktree, and refill the free Terra slot. Continue until stopped, genuinely blocked, or new authority is required.

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
- Never stage `.beads/**`, traces, screenshots, or local run state.
- Local worker commits and coordinator integration are authorized only under the repository's `start` contract; pushes and hardware remain separately authorized.
