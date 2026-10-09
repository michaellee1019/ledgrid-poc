# Beads autopilot

Read only for an explicitly enabled autopilot session. [AGENTS.md](../../../../AGENTS.md) supplies the authority and product boundaries.

## Start and select

Reuse loaded prime context. Reconcile state that could cause duplicate or lost work once:

```bash
bd list --status=in_progress
bd merge-slot check
bd worktree list
git worktree list --porcelain
```

Inspect the live agent tree. Preserve dirty/unmerged work and active owners. Verify a completed handoff before integrating it; release an orphaned claim only after establishing that no worker owns it, with an interruption note.

Verify the integration branch from the latest accepted handoff against Git (currently `update-animation-pipeline`). Do not recreate historical reconstruction branches or merge a donor wholesale. Select unassigned, ready descendants of `ledgrid-poc-ib7` using `bd ready --json`: concrete safety/integrity failures, phone-demo reliability/responsiveness, requested current-only simplification, then measured relevant bottlenecks. No dispatch depends on stale wave labels or perfect execution cards.

## Work and delegate when useful

Implement localized work directly. A portfolio steward is optional only for a real queue/dependency/ownership ambiguity unresolved by bounded inspection, or useful planning of independent streams. It changes Beads only, preserves accepted intent, and exits after resolving that ambiguity; do not manufacture waves or fill a quota.

Use [models.md](models.md) for useful delegation. At most two implementation workers run concurrently, reserving capacity for the coordinator and one reviewer/steward. Each worker gets one claimed Bead, an isolated `codex/` branch/worktree, current base, non-overlapping ownership, acceptance, and focused validation. Do not let workers regenerate the same shared artifact.

Under autopilot's local commit authority, a worker makes one logical commit and records SHA, paths, checks, and generated output; marks `merge-ready`; and leaves integration/closure to the coordinator. Required independent review follows root risk triggers and uses a separate isolated worktree. Reuse trustworthy exact-candidate evidence.

## Integrate and continue

Review the handoff and required acceptance before acquiring the merge slot. Rebase on the current integration tip, rerun affected checks only if the candidate or relevant environment changed, then fast-forward integrate. Record the integrated SHA, close the issue, and release the slot. Remove only clean, merged worktrees without unique evidence. Preserve historical worktrees until their contents are checked.

Continue ready accepted work until stopped, no meaningful local work can proceed, or a genuine product/authority decision is needed. No push or main merge is implied. Batch audit-log changes into authorized commits.

## Stop

Interrupt workers and reconcile their final status, claims, and handoffs. Release the merge slot owned by this session; preserve dirty/unmerged worktrees. Record unfinished work and its next step without creating speculative follow-ups. Do not close unfinished issues or disturb unrelated owners.
