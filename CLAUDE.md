# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->

## Beads Autopilot

These rules apply to the `ledgrid-poc-ib7` Live-First Composer initiative. They replace the retired worktree-train and source-parity sequencing with a breadth-first demo-delivery loop.

### The `start` contract

When the user's request is simply `start`, treat it as explicit authorization to run Beads autopilot for the current session. `resume` and `keep going` have the same meaning when the initiative is already clear.

Autopilot may reconcile Beads, create local branches and worktrees, claim work, spawn subagents, make local commits, integrate completed work, close Beads, and continue to the next ready item without asking between ordinary local steps. It does not authorize git or Dolt pushes, a squash or merge to `main`, physical-wall or receiver operations, deployment, or a product decision outside the accepted epic.

### Lean startup

Run `bd prime`, then reconcile only the state that can cause duplicate or lost work:

```bash
bd list --status=in_progress
bd merge-slot check
bd worktree list
git worktree list --porcelain
```

Inspect the live agent tree too. Repair mismatches before claiming anything: integrate a completed handoff, preserve dirty work, or return an orphaned claim to `open` with a short interruption note. Do not create a planning phase merely to restate the backlog.

The integration base is the recorded `codex/ib7-selective-reconstruction` branch and its registered clean worktree. Treat `update-animation-pipeline` as a donor and never merge it wholesale. A sane, minimal eventual history onto `main` matters, but is secondary to reaching the right product and a convincing end-to-end demo. Do not stop visible product work merely to inventory, rewrite history, prune broadly, or perfect a future squash.

Use `bd ready --json` and select unassigned descendants of `ledgrid-poc-ib7`. Rank the shortest dependency path to the current Live-First demo first: user-visible behavior, shared contract unblockers, safety and operability, then complexity deletion. Defer broad renderer migration, inventory, verification-only work, documentation programs, and merge-history cleanup until the demo boundary or an actual dependency requires them. A leaf needs actionable acceptance, an identifiable ownership/conflict area, and focused validation; do not block delivery to perfect estimates, prose, or execution cards.

### Breadth-first demo posture

Optimize first for a compelling, varied catalog that is fun to explore: visibly distinct Animations and Widgets, intuitive component-local controls, useful defaults, and a short select-to-preview loop. Prefer several thin, complete catalog slices over taking one component or state path to production polish. Once a component can be selected, understood, previewed, and exercised through the accepted demo path, move to the next distinct catalog contribution.

Deliberately incur reversible technical debt when it buys catalog breadth or faster feedback. During implementation, review, or debugging, turn polish findings and state-management edge cases into linked follow-up Beads and continue unless they threaten safety, corrupt user work, invalidate the shared Scene v2 contract, or break the current browser/live demo. Perfect cache behavior, exhaustive recovery, speculative abstractions, full compatibility, and exhaustive test matrices are normally backlog work rather than blockers. Do not silently discard a finding: record its reproduction, impact, and suggested boundary in Beads.

Keep feedback fast. A user-visible leaf should normally get focused contract tests and one short browser smoke covering selection, intuitive control response, Preview, and the relevant live acknowledgement. Favor rapid visual confirmation over expanding a verification program. Batch physical-wall checks at useful demo checkpoints and only when the user has separately authorized wall or deployment operations; the `start` contract alone still does not authorize them.

### Sol portfolio steward

Start a short-lived portfolio steward before dispatch when the current wave is missing or stale, after each batch of up to 15 selected Beads, or earlier when no product leaf is ready, an item stalls or reopens twice, priorities/dependencies disagree, or Beads/worktree/branch reality drifts. Use at most one steward at a time with `gpt-5.6-sol` and reasoning effort `high`.

The steward reads the binding Scene v2 decision, epic, recent handoffs, ready/blocked queues, and branch progress. It may prepare or repair at most 15 bounded executable Beads, partition them into dependency-ordered non-conflicting waves, clarify acceptance from accepted decisions, and retire stale coordination work. It normally changes Beads only, leaves a short epic note, and exits before implementation dispatch. It must not inflate the backlog, rewrite product intent, implement code, authorize pushes/hardware, or claim later waves.

The steward should shape breadth-first waves across distinct visual families and control patterns. Do not serialize the portfolio behind polish for the most mature component. Keep follow-up debt discoverable but off the runnable demo wave unless it crosses the blocker threshold above.

Only the current runnable wave receives `worktree-ready` and may be claimed. Later selected Beads remain open and unclaimed until their dependencies integrate. Represent an unresolved product or authority choice as a separate Bead carrying the exact `human` label, block dependents on it, and leave it discoverable through `bd human list`.

### Terra implementation workers

Use up to two concurrent implementation workers so the coordinator retains one slot and a steward can use the fourth. Spawn each worker with model `gpt-5.6-terra` and reasoning effort `high`.

Each worker gets one claimed Bead, one local `codex/` branch, one isolated worktree, the current integration base, and an explicit ownership area. Never run two workers in the same conflict domain or let two workers regenerate the same shared artifact. During source completion the coordinator owns `update-animation-pipeline`; reconstruction uses its recorded integration branch and never merges the donor wholesale.

A worker implements the smallest complete product increment, runs baseline validation, makes one logical commit, and records a concise Beads handoff with tip SHA, changed paths, checks, and any generated output. It adds `merge-ready` but does not merge or close its own implementation Bead.

Baseline validation is changed-language syntax or lint, focused tests, and `git diff --check`. Add one adjacent regression only when a shared contract changed. Use one browser smoke only for a user-visible browser boundary. Full browser matrices, aggregate preflight, firmware matrices, and soak tests belong at release or hardware boundaries, not ordinary leaves.

### Risk-tiered acceptance

Shared schemas, compositor or protocol work, risky live-state changes, and user-visible acceptance require an independent reviewer using `gpt-5.6-sol` with reasoning effort `high`. The reviewer works from an isolated worktree, checks the accepted Bead rather than redesigning it, records actionable findings or approval, and does not merge. The coordinator may review routine bounded leaves. A review is a gate on integration, not a separate long-running implementation lane.

Reviewers must distinguish demo blockers from follow-up debt. Block integration only for a concrete failure of the accepted user-visible path, safety or data-integrity risk, a broken shared contract, or a regression likely to derail the next breadth wave. Record lower-impact correctness, perfect-state-management, maintainability, and rare edge-case findings as follow-up Beads, then approve the bounded demo increment when its primary path is convincing.

### Animation modernization review discipline

Use a review-before-implementation pass for catalog-wide animation modernization, including `ledgrid-poc-ib7.82`. A Terra worker records source-read-only evidence for one non-overlapping family, and a Sol reviewer independently reproduces the important claims. Create implementation children only from accepted `implement` dispositions or accepted product blockers; keep `retain-current`, `defer`, and `not-applicable` findings documented without turning them into speculative work.

For every component, record exactly one disposition for semantic palette, premultiplied RGBA/background composition, installation geometry, and direct interaction. Evidence must use a real resolved Scene at every sample through the production render path, including Scene palette and pace. Direct `generate_frame` calls, constructor defaults, or a context installed once at the wrong elapsed time do not prove Scene timing or palette behavior. Preserve fixed 33x138 fingerprints, semantic state and RNG digests, cache/source-tick behavior, focused tests, and desktop mean/p95/p99/max plus changed-frame ratio; never present desktop timing as Raspberry Pi evidence.

Absence of an interaction handler proves only current absence, not product-level `not-applicable`. Exercise the actual Composer event name, normally `primary`, and assess the animation's natural semantic boundary. An accepted input queues only validated events, consumes them once at the next source tick, and has exact no-input frame, logical-state, and RNG parity. If that story is not convincing, choose `defer` or give a visual/product rationale for `not-applicable`.

Geometry remains provider-owned and non-authorable. Pick one precise role per accepted renderer—such as exact-core collision, clearance-only planning, exact-edge light, or presentation-only refraction—and state what a live geometry revision may recompute. Disabled and enabled-zero paths must be exact frame/state/RNG no-ops; live revisions preserve tick, RNG, and persistent simulation state. Do not duplicate the global final-optics pass inside a renderer: story-specific geometry and installation-wide shadow/refraction are separate concerns.

Audit presets at three distinct layers: raw authored payloads, normalized component-local parameters, and visible names/descriptions. A raw legacy field may be intentionally stripped because Scene owns palette/background/brightness/pace or installation owns calibration; a collapsed normalized payload or false visible promise is product drift. Scope only the reviewed family and distinguish its failures from unrelated broad-suite noise.

When acceptance requests changes, append corrected evidence instead of overwriting the original report, keep the source worktree clean, and rerun the same independent gate. On `stop`, an approval comment and `acceptance-approved` label recorded before interruption may be reconciled and closed after verification. Preserve dirty or unmerged worktrees; clean, source-read-only review worktrees with no unique commits may be removed after their Beads are closed.

### Continuous integration loop

Wait for the first worker to finish, review the handoff, obtain required independent acceptance, and acquire the Beads merge slot only for integration. Rebase onto the current selective-reconstruction tip, rerun the focused checks on the prospective tip, fast-forward merge, record the integrated SHA, close the Bead, release the slot, and remove only the clean merged worktree. Unlock and claim the next dependency wave only after its prerequisites integrate, then refill available Terra capacity.

Continue until the user says `stop`, no meaningful local work can proceed, or a real permission/product decision is required. On stop, interrupt workers, wait for the live tree to empty, release the merge slot, reconcile every `in_progress` Bead, and preserve dirty or unmerged worktrees. Never stage `.beads/**`, traces, screenshots, or local run state.
