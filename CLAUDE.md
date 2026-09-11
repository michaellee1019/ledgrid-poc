# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

## SSH and SCP

**ALWAYS use the dedicated `.gpt-key`; NEVER invoke bare `ssh` or `scp`.** Bare remote commands may consult the human-managed SSH agent, request signing or a passphrase, or block waiting for human interaction. Agent automation must never depend on that interaction.

The canonical key is `/Users/rtimmons/Projects/ledgrid-poc/.gpt-key`. It is intentionally ignored and may not exist inside temporary Git worktrees, so use this absolute saved-project path for every remote command:

```bash
LEDGRID_SSH_KEY=/Users/rtimmons/Projects/ledgrid-poc/.gpt-key
ssh -i "$LEDGRID_SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 user@host -- command
scp -i "$LEDGRID_SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes source user@host:path
```

For recipes or tools that accept an SSH key, explicitly pass the same key, for example `SSH_KEY="$LEDGRID_SSH_KEY" just deploy`. If `.gpt-key` is absent or unreadable, stop and report the blocker; do not fall back to default identities, the SSH agent, password prompts, or a newly generated key.

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

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. `.beads/interactions.jsonl` is a separate append-only Beads audit log intended for Git versioning after JSONL integrity checks, never a substitute for Dolt sync or backup. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

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

### Small-fix fast path

Use the smallest delivery process justified by uncertainty and consequence. This fast path also applies to ordinary scoped requests outside autopilot and takes precedence over the default dispatch, steward, and validation cadence below. It does not expand commit, push, deployment, hardware, or product authority.

A small fix has understood behavior and a localized cause, one ownership area, and focused acceptance. File count alone is not a risk measure. Shared Scene/schema or compositor/protocol changes, persistence or data-integrity changes, risky live-state transitions, new simulation behavior, and hardware work retain the stronger gates below.

- The coordinator implements small fixes directly. Do not spawn a steward or implementation worker merely to hand off an already understood correction. Delegate only a concrete independent task with useful concurrent work; changing to a cheaper model alone does not justify a handoff.
- Reconcile relevant claims, dirty work, and the current base once. If an accepted leaf is actionable, refresh its base and readiness locally; stale wave labels or an old handoff date alone do not require a portfolio review. Do not scan the whole backlog for a scoped user request.
- Use the current suitable checkout when ownership is exclusive and no pre-integration review gate applies. Otherwise use one implementation branch/worktree. Independent acceptance still uses a separate agent and isolated worktree. Do not create extra implementation lanes for one fix.
- For a behavior correction, add or use a focused regression that demonstrates the old failure and corrected behavior, then run changed-language syntax/lint, relevant adjacent tests, and `git diff --check`. Documentation-only edits need consistency/link checks, not application tests.
- Browser validation is triggered by changed UI wiring, browser-only execution, asset loading, or a defect that production render tests cannot establish. A renderer's visibility in Composer alone does not trigger browser setup. For browser changes, exercise only the affected path; validate live acknowledgements only when publication or acknowledgement behavior changed. Rebuild shipped generated assets when their inputs change.
- Benchmark when work per frame, allocations, source cadence, simulation complexity, or a performance acceptance criterion changes. A cache correctness fix with unchanged work/cadence normally needs cache/changed-frame assertions, not a timing report. Contact sheets, full palette/geometry matrices, and long simulations require a corresponding visual family or behavioral change.
- Retain one focused independent review for visible behavior, including small renderer fixes; routine documentation/mechanical edits can be coordinator-reviewed. A small-fix reviewer checks the diff and decisive regression, not a new evidence program. Use the model table's small-fix effort; high-consequence gates are unchanged.
- Reuse successful checks tied to the exact candidate commit and relevant environment. Each role does not need to rerun the entire package. Rerun affected checks after source changes, conflict resolution, a changed integration base, environment differences, or a concrete concern; a no-op rebase is not itself a reason.
- Keep one concise Beads handoff with candidate SHA, checks, acceptance, and any blocker. Prefer a persistent test over a temporary evidence script. Do not repeatedly rewrite epic notes or re-inspect unchanged state while another agent runs; prepare independent work or wait. If setup or coordination overtakes the fix, simplify the approach within the required gates.

For an Emoji-style pulse/cache correction, the expected path is direct implementation, a resolved-Scene source/final regression, affected asset refresh, one focused independent review, and local integration when authorized. A portfolio steward, implementation subagent, new benchmark report, and live-acknowledgement exercise are unnecessary.

### Lean startup

Run `bd prime`, then reconcile only the state that can cause duplicate or lost work:

```bash
bd list --status=in_progress
bd merge-slot check
bd worktree list
git worktree list --porcelain
```

Inspect the live agent tree too. Repair mismatches before claiming anything: integrate a completed handoff, preserve dirty work, or return an orphaned claim to `open` with a short interruption note. Do not create a planning phase merely to restate the backlog.

Use the integration branch recorded by the latest accepted Beads handoff and verify it against Git. The historical selective-reconstruction line is already incorporated into the current `update-animation-pipeline` integration branch; do not recreate its deleted branch or reconcile old prose on every start. Never merge a donor wholesale into another line or infer permission to merge to `main`. A sane, minimal eventual history onto `main` matters, but is secondary to reaching the right product and a convincing end-to-end demo. Do not stop visible product work merely to inventory, rewrite history, prune broadly, or perfect a future squash.

Use `bd ready --json` and select unassigned descendants of `ledgrid-poc-ib7`. Rank the shortest dependency path to the current Live-First demo first: user-visible behavior, shared contract unblockers, safety and operability, then complexity deletion. Defer broad renderer migration, inventory, verification-only work, documentation programs, and merge-history cleanup until the demo boundary or an actual dependency requires them. A leaf needs actionable acceptance, an identifiable ownership/conflict area, and focused validation; do not block delivery to perfect estimates, prose, or execution cards.

### Breadth-first demo posture

Optimize first for a compelling, varied catalog that is fun to explore: visibly distinct Animations and Widgets, intuitive component-local controls, useful defaults, and a short select-to-preview loop. Prefer several thin, complete catalog slices over taking one component or state path to production polish. Once a component can be selected, understood, previewed, and exercised through the accepted demo path, move to the next distinct catalog contribution.

Deliberately incur reversible technical debt when it buys catalog breadth or faster feedback. During implementation, review, or debugging, turn polish findings and state-management edge cases into linked follow-up Beads and continue unless they threaten safety, corrupt user work, invalidate the shared Scene v2 contract, or break the current browser/live demo. Perfect cache behavior, exhaustive recovery, speculative abstractions, full compatibility, and exhaustive test matrices are normally backlog work rather than blockers. Do not silently discard a finding: record its reproduction, impact, and suggested boundary in Beads.

Keep feedback fast. Use focused contract tests and the small-fix validation triggers above. A new browser feature normally needs one short smoke covering its selection, control response and Preview; include live acknowledgement only when that boundary changes. Favor rapid visual confirmation over expanding a verification program. Batch physical-wall checks at useful demo checkpoints and only when the user has separately authorized wall or deployment operations; the `start` contract alone still does not authorize them.

### Model selection

Choose the model for the Bead's uncertainty, consequence of error, and ownership area. These are project defaults, informed by the [OpenAI model comparison](https://developers.openai.com/api/docs/models/compare) and the models exposed by the current Codex runtime; they are not measured project benchmarks.

| Work | Default model | Reasoning effort |
| --- | --- | --- |
| Coordinator: cross-cutting decisions, dispatch, integration, and escalation | `gpt-6-astra` | `high` |
| Portfolio steward: bounded queue, dependency, and wave maintenance | `gpt-5.6-sol` | `high` |
| Bounded implementation with a clear existing pattern, when delegation is useful | `gpt-5.6-terra` | `medium`; `low` for straightforward corrections |
| Catalog-wide source-evidence collection | `gpt-5.6-terra` | `high` |
| Complex implementation or investigation across components, simulation behavior, or live state | `gpt-5.6-sol` | `high` |
| Hardest implementation/debugging, architectural ambiguity, or repeatedly failed reasoning | `gpt-6-astra` | `high`; `xhigh` for unresolved deep reasoning |
| Focused independent review of a small visible correction | `gpt-5.6-sol` | `medium` |
| Independent acceptance of a new user-visible feature or modernization evidence | `gpt-5.6-sol` | `high` |
| Independent review of shared Scene/schema, compositor/protocol, safety/data integrity, or risky live-state changes | `gpt-6-astra` | `high` |
| Mechanical edits, narrow lookups, or straightforward check execution with objective results | `gpt-5.6-luna` | `low` or `medium` |

Use the least expensive model likely to complete the accepted task correctly. Start complex work at the appropriate tier; do not require a cheaper model to fail first. Escalate Terra to Sol, or Sol to Astra, when a focused attempt exposes unresolved reasoning or cross-cutting uncertainty. Escalate the steward to Astra when dependency/product constraints cannot be reconciled from accepted decisions. Repair missing context, tooling, and environment problems directly; changing models does not fix them. Preserve evidence and the same Bead when handing off, and end the previous worker's ownership before replacement.

Reserve `xhigh` for a concrete reasoning need; `max`/`ultra` are not routine defaults. Luna does not own product decisions, shared-contract design, or independent acceptance. Review independence means a separate agent and isolated worktree, even when author and reviewer use the same model. Do not add agents solely to occupy slots; handle small scoped requests directly.

For delegated work, pass the selected model and supported effort explicitly. When using `spawn_agent` with a model override, use `fork_turns="none"` or a bounded positive turn count and include the Bead, accepted contract, base/worktree, ownership, and validation requirements. Full-history forks inherit the parent model and cannot take overrides. Respect explicit user model choices and the current tool's available models/efforts; if a required review tier is unavailable, report that limit rather than silently weakening the gate. This policy does not change the running coordinator's model or authorize a new task.

### Portfolio steward

Use a short-lived portfolio steward when the coordinator cannot resolve a real queue, dependency, ownership, or priority ambiguity with a bounded inspection, or when planning several independent workstreams would save time. At a batch boundary of up to 15 selected Beads, check whether stewardship is needed; do not spawn one automatically. Missing/stale wave metadata alone is insufficient when the next accepted leaf is clear. Use at most one steward at a time, selected by the model policy above.

The steward reads the binding Scene v2 decision, epic, recent handoffs, ready/blocked queues, and branch progress. It may prepare or repair at most 15 bounded executable Beads, partition them into dependency-ordered non-conflicting waves, clarify acceptance from accepted decisions, and retire stale coordination work. It normally changes Beads only, leaves a short epic note, and exits before implementation dispatch. It must not inflate the backlog, rewrite product intent, implement code, authorize pushes/hardware, or claim later waves.

The steward should shape breadth-first waves across distinct visual families and control patterns. Do not serialize the portfolio behind polish for the most mature component. Keep follow-up debt discoverable but off the runnable demo wave unless it crosses the blocker threshold above.

Only the current runnable wave receives `worktree-ready` and may be claimed. Later selected Beads remain open and unclaimed until their dependencies integrate. Represent an unresolved product or authority choice as a separate Bead carrying the exact `human` label, block dependents on it, and leave it discoverable through `bd human list`.

### Implementation workers

Use up to two concurrent implementation workers so the coordinator retains one slot and a steward or reviewer can use the fourth. Select each worker's model and reasoning effort using the model policy above; worker count is independent of model tier.

Each worker gets one claimed Bead, one local `codex/` branch, one isolated worktree, the current integration base, and an explicit ownership area. Never run two workers in the same conflict domain or let two workers regenerate the same shared artifact. During source completion the coordinator owns `update-animation-pipeline`; reconstruction uses its recorded integration branch and never merges the donor wholesale.

A worker implements the smallest complete product increment, runs baseline validation, makes one logical commit, and records a concise Beads handoff with tip SHA, changed paths, checks, and any generated output. It adds `merge-ready` but does not merge or close its own implementation Bead.

Baseline validation follows the small-fix triggers: changed-language syntax or lint, focused regression/adjacent tests, and `git diff --check`. Use a browser smoke only when the affected boundary requires browser evidence. Full browser matrices, aggregate preflight, firmware matrices, and soak tests belong at release or hardware boundaries, not ordinary leaves.

### Risk-tiered acceptance

Shared schemas, compositor or protocol work, risky live-state changes, and user-visible acceptance require an independent reviewer selected by the model policy above: Astra for shared contracts and high-consequence changes, Sol for ordinary user-visible acceptance. The reviewer works from an isolated worktree, checks the accepted Bead rather than redesigning it, records actionable findings or approval, and does not merge. The coordinator may review routine bounded leaves that do not meet those independent-review triggers. A review is a gate on integration, not a separate long-running implementation lane. For small visible corrections, use the focused review exception above: inspect the exact candidate and reproduce the decisive regression, reusing other trustworthy same-candidate checks. Do not inherit the catalog-modernization evidence checklist solely because a fix belongs to an Animation.

Reviewers must distinguish demo blockers from follow-up debt. Block integration only for a concrete failure of the accepted user-visible path, safety or data-integrity risk, a broken shared contract, or a regression likely to derail the next breadth wave. Record lower-impact correctness, perfect-state-management, maintainability, and rare edge-case findings as follow-up Beads, then approve the bounded demo increment when its primary path is convincing.

### Animation modernization review discipline

Use a review-before-implementation pass for catalog-wide animation modernization. The finite `ledgrid-poc-ib7.82` program is closed; its accepted dispositions remain evidence for follow-ups. A localized correction to an already reviewed component uses the small-fix path, not a repeat family audit. An evidence worker records source-read-only evidence for one non-overlapping family, and an independent reviewer reproduces the important claims. Select both models by the policy above; routine evidence starts with Terra and Sol acceptance, while complex simulation/timing analysis and shared-contract risks use the higher tiers. Create implementation children only from accepted `implement` dispositions or accepted product blockers; keep `retain-current`, `defer`, and `not-applicable` findings documented without turning them into speculative work.

For every component, record exactly one disposition for semantic palette, premultiplied RGBA/background composition, installation geometry, and direct interaction. Evidence must use a real resolved Scene at every sample through the production render path, including Scene palette and pace. Direct `generate_frame` calls, constructor defaults, or a context installed once at the wrong elapsed time do not prove Scene timing or palette behavior. Preserve fixed 33x138 fingerprints, semantic state and RNG digests, cache/source-tick behavior, focused tests, and desktop mean/p95/p99/max plus changed-frame ratio; never present desktop timing as Raspberry Pi evidence.

Absence of an interaction handler proves only current absence, not product-level `not-applicable`. Exercise the actual Composer event name, normally `primary`, and assess the animation's natural semantic boundary. An accepted input queues only validated events, consumes them once at the next source tick, and has exact no-input frame, logical-state, and RNG parity. If that story is not convincing, choose `defer` or give a visual/product rationale for `not-applicable`.

Geometry remains provider-owned and non-authorable. Pick one precise role per accepted renderer—such as exact-core collision, clearance-only planning, exact-edge light, or presentation-only refraction—and state what a live geometry revision may recompute. Disabled and enabled-zero paths must be exact frame/state/RNG no-ops; live revisions preserve tick, RNG, and persistent simulation state. Do not duplicate the global final-optics pass inside a renderer: story-specific geometry and installation-wide shadow/refraction are separate concerns.

Audit presets at three distinct layers: raw authored payloads, normalized component-local parameters, and visible names/descriptions. A raw legacy field may be intentionally stripped because Scene owns palette/background/brightness/pace or installation owns calibration; a collapsed normalized payload or false visible promise is product drift. Scope only the reviewed family and distinguish its failures from unrelated broad-suite noise.

When acceptance requests changes, append corrected evidence instead of overwriting the original report, keep the source worktree clean, and rerun the same independent gate. On `stop`, an approval comment and `acceptance-approved` label recorded before interruption may be reconciled and closed after verification. Preserve dirty or unmerged worktrees; clean, source-read-only review worktrees with no unique commits may be removed after their Beads are closed.

### Continuous integration loop

Wait for the first worker to finish, review the handoff, obtain required independent acceptance, and acquire the Beads merge slot only for integration. Rebase onto the recorded current integration tip, validate affected changes if the prospective tip differs from the tested candidate, fast-forward merge, record the integrated SHA, close the Bead, release the slot, and remove only the clean merged worktree. Reuse exact-candidate checks after a no-op rebase. Unlock and claim the next dependency wave only after its prerequisites integrate, then refill available implementation capacity.

Continue until the user says `stop`, no meaningful local work can proceed, or a real permission/product decision is required. On stop, interrupt workers, wait for the live tree to empty, release the merge slot, reconcile every `in_progress` Bead, and preserve dirty or unmerged worktrees. Never stage `.beads/**`, traces, screenshots, or local run state—except `.beads/interactions.jsonl`: validate its append-only JSONL records and commit that audit log when it changes. Never treat that commit as a substitute for `bd dolt push`/pull or a Dolt backup.
