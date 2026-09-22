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

1. **Record remaining work** - Create open beads only when they meet the installed-wall admission rule; record other findings as not planned with a reopening trigger
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

These rules apply to the `ledgrid-poc-ib7` Live-First Composer initiative. The current demo refocus takes precedence over older delivery requirements in skills, execution cards, and handoffs; prior programs remain historical evidence.

### Demo refocus (`ledgrid-poc-ib7.131`)

The user selected a reliable phone-controlled demo with playlists and three working native scenes. Reliability comes first; a few seconds per switch is acceptable. Do not add a one-second switching requirement or another visual family before this path works.

- Support one current host/firmware combination on the installed wall. Coordinated app/receiver reflashes and planned downtime necessary for this accepted work are authorized. Use a stopped maintenance window when simpler than rolling compatibility. Preserve operator Scenes, calibration, profiles, and recoverable settings; preserve brightness 0 unless the user changes it. This does not authorize physical rewiring, receiver-3 hardware investigation, Git/Dolt pushes, or a merge to `main`.
- Remove proven obsolete old-host/old-firmware negotiation, deployment bootstrap/migration paths, and their exclusive tests through the bounded compatibility Beads. Check current consumers first: a legacy-named helper used by current playlists, HA, or the current status layout is not obsolete merely because of its name. Preserve current CRC, identity, ordering, and truthful failure safeguards.
- Require independent review only for protocol/shared data contracts, data integrity/persistence, safety, or risky live-state transitions. Ordinary UI, renderer, and other reversible visible changes use coordinator review plus decisive checks. Visibility alone is not a review trigger. An optional reviewer must resolve a concrete uncertainty while useful work proceeds.
- Run one focused regression and relevant adjacent checks for a correction; add one affected browser smoke only when the browser boundary changes. Build the installed firmware target when firmware changes. Use the current aggregate gate once per relevant integration/deployment batch, not once per role or Bead; reuse exact-candidate evidence. Drop tests exclusive to removed compatibility paths after retaining unique current integrity assertions. Do not create a broad test-repair program.
- Keep 150 FPS as an installed-output goal, with smooth, reliable demos first. The user explicitly acknowledged Violet Solar Choir at 108.06 actual FPS. Record other measured limitations truthfully in `.90`; do not claim universal 150 FPS or infer acknowledgement of another case. A raw below-target number alone does not block this demo; visible stutter, failed playback, and false state still do.
- Judge progress by a working demonstrated journey, reliable switches, and measured switch latency. Report source-complete, deployed, and demo-accepted states separately. Count retirements separately from completed features. Keep only actionable leaves; 10-15 is a ceiling guide, not a quota. Batch audit-log changes into authorized implementation or session commits instead of creating a commit after every review event.

### UX and iteration priority (2026-09-23)

The user chose "Prioritize UX and fast iteration" when asked whether to accept the working host-rendered demo or continue requiring receiver-native execution. This supersedes the native-execution requirement in the older demo refocus and acceptance cards.

- Accept the verified `host_full_rgb` path for Aurora Curtains, Cellular Tapestry, and Circadian Window. Judge the demo by phone selection/editing, reliable playlists and manual takeover, truthful Active receipts, and prepared switches within five seconds. Report the execution mode accurately.
- Receiver-native execution is no longer a milestone blocker or an automatic next task. Preserve its implementation and evidence; revisit it only for a demonstrated UX/reliability benefit or an explicit request.
- Favor small visible improvements and fast feedback. Use the smallest affected regression and adjacent checks, one affected browser smoke when needed, and reuse applicable installed evidence. Do not rerun a five-minute playlist, full catalog sweep, or deployment solely to close a record or change priorities.
- Preserve exact all-five display proof, CRC/identity checks, rollback, operator settings, and accepted receiver-3 degradation. Keep the 150 FPS goal and measured limitations visible; this decision does not acknowledge new performance exceptions.

### Accepted receiver-3 degradation (2026-09-22)

The operator knows receiver 3 is flaky. Treat its CRC/FEC increments, occasional protected-status read corruption or empty responses, and reduced output rate as an accepted degraded condition during agent triage and demo acceptance. Record the measurements, but do not mark an otherwise verified animation or preset as failed, open a blocker, demand physical receiver-3 work, or recommend app/firmware rollback solely for these symptoms. If a transient status read interrupts a switch, a bounded new Check and complete all-five display proof may retry it; judge the final verified receipt and retain the earlier diagnostic in the evidence. Never claim a new Scene is Active without its intact displayed receipt. If fresh proof remains unavailable, report that activation as unverified and preserve the prior Scene; this is a verification limit, not a reason by itself to roll back the deployment.

For fast iteration, run the smallest affected regression and adjacent checks first; `just test-scene-fast` is the prepared local gate for the current activation/reset/receipt path. Do not run the full catalog wall sweep or broad historical suites for an ordinary fix. Use a few representative live switches for the affected boundary; reserve complete catalog qualification for a separately scoped batch, with incremental progress and an exact restoration record.

### Installed-wall backlog boundary (2026-09-11)

The accepted backlog reduction supersedes historical reconstruction, breadth-expansion, cleanup, qualification, and automatic-follow-up instructions wherever they conflict. The durable decision and disposition ledger are in `ledgrid-poc-ib7.111`; the only active product epic is `ledgrid-poc-ib7`.

- Preserve the current catalog, Widgets, Looks, controls, desktop/phone Composer, calibration, and working deployment path. Prioritize reliable use on the installed five-receiver 33x138 wall. New catalog expansion, another installation, and HAT redesign require an explicit user request.
- Target 150 FPS on the installed wall under the demo-first performance policy above. Do not substitute desktop timing, silently lower the target, hide receiver errors, or treat an intentional low animation source cadence as an output-throughput exemption. No agent may acknowledge a limitation for the user.
- Keep at most roughly 10-15 actionable leaves plus the product epic and merge-slot record; fewer is preferable when sufficient. Admit work only for a reproducible supported-path failure, concrete integrity risk, measured relevant performance shortfall, or explicitly requested feature or simplification. Each retained leaf needs a current problem, bounded correction, and decisive acceptance check.
- Squash-to-main, selective reconstruction, blanket compatibility deletion, wholesale deployment replacement, formal production certification, exhaustive documentation, speculative optimization, and the deferred parking lot are retired obligations. Closed history and design artifacts remain available; closure never authorizes deleting implementations, data, or hardware evidence.
- Record minor polish and speculative findings in the current Bead's handoff with impact and reopening trigger, or as a closed `not-planned` record when a separate record is useful. Do not automatically create open/deferred follow-ups. Reopen only when new evidence meets the admission rule or the user explicitly requests the work.
- Distinguish `completed` (acceptance evidence), `superseded` (named successor), and `not-planned` (cancelled obligation) in closure reasons. User-approved retirement does not claim implementation success. Remove obsolete blockers and dispatch labels without disturbing active owners, claims, or dirty work.
- Use the bounded current validation gate and exact-candidate evidence. Port unique integrity assertions when needed; do not repair every historical suite or reopen a family audit merely to eliminate stale tests. Apply independent review only to the current risk triggers above.

### The `start` contract

When the user's request is simply `start`, treat it as explicit authorization to run Beads autopilot for the current session. `resume` and `keep going` have the same meaning when the initiative is already clear.

Autopilot may reconcile Beads, create local branches and worktrees, claim work, spawn subagents, make local commits, integrate completed work, close Beads, and continue to the next ready item without asking between ordinary local steps. `start` alone does not grant hardware authority, but the explicit demo-refocus authorization above covers necessary coordinated deployments, receiver reflashes, and downtime for that work; do not ask again for the same authority. Git/Dolt pushes, a squash or merge to `main`, and product decisions outside the accepted epic remain separately authorized.

### Small-fix fast path

Use the smallest delivery process justified by uncertainty and consequence. This fast path also applies to ordinary scoped requests outside autopilot and takes precedence over the default dispatch, steward, and validation cadence below. It does not expand commit, push, deployment, hardware, or product authority.

A small fix has understood behavior and a localized cause, one ownership area, and focused acceptance. File count alone is not a risk measure. Protocol/shared data contracts, persistence or data-integrity changes, safety, and risky live-state transitions retain independent review. Simulation and hardware changes need the relevant behavior or installed-target evidence; neither automatically requires an exhaustive matrix.

- The coordinator implements small fixes directly. Do not spawn a steward or implementation worker merely to hand off an already understood correction. Delegate only a concrete independent task with useful concurrent work; changing to a cheaper model alone does not justify a handoff.
- Reconcile relevant claims, dirty work, and the current base once. If an accepted leaf is actionable, refresh its base and readiness locally; stale wave labels or an old handoff date alone do not require a portfolio review. Do not scan the whole backlog for a scoped user request.
- Use the current suitable checkout when ownership is exclusive and no pre-integration review gate applies. Otherwise use one implementation branch/worktree. Independent acceptance still uses a separate agent and isolated worktree. Do not create extra implementation lanes for one fix.
- For a behavior correction, add or use a focused regression that demonstrates the old failure and corrected behavior, then run changed-language syntax/lint, relevant adjacent tests, and `git diff --check`. Documentation-only edits need consistency/link checks, not application tests.
- Browser validation is triggered by changed UI wiring, browser-only execution, asset loading, or a defect that production render tests cannot establish. A renderer's visibility in Composer alone does not trigger browser setup. For browser changes, exercise only the affected path; validate live acknowledgements only when publication or acknowledgement behavior changed. Rebuild shipped generated assets when their inputs change.
- Benchmark when work per frame, allocations, source cadence, simulation complexity, or a performance acceptance criterion changes. A cache correctness fix with unchanged work/cadence normally needs cache/changed-frame assertions, not a timing report. Contact sheets, full palette/geometry matrices, and long simulations require a corresponding visual family or behavioral change.
- The coordinator reviews ordinary visible corrections, including small renderer fixes, using the diff and decisive regression. Independent review is required only for the risk triggers above. When useful, an optional small-fix reviewer checks the exact diff and regression without starting a new evidence program.
- Reuse successful checks tied to the exact candidate commit and relevant environment. Each role does not need to rerun the entire package. Rerun affected checks after source changes, conflict resolution, a changed integration base, environment differences, or a concrete concern; a no-op rebase is not itself a reason.
- Keep one concise Beads handoff with candidate SHA, checks, acceptance, and any blocker. Prefer a persistent test over a temporary evidence script. Do not repeatedly rewrite epic notes or re-inspect unchanged state while another agent runs; prepare independent work or wait. If setup or coordination overtakes the fix, simplify the approach within the required gates.

For an Emoji-style pulse/cache correction, the expected path is direct implementation, a resolved-Scene source/final regression, affected asset refresh, coordinator review, and local integration when authorized. A portfolio steward, implementation subagent, independent reviewer, new benchmark report, and live-acknowledgement exercise are unnecessary unless the actual change exposes a separate risk trigger.

### Lean startup

Run `bd prime`, then reconcile only the state that can cause duplicate or lost work:

```bash
bd list --status=in_progress
bd merge-slot check
bd worktree list
git worktree list --porcelain
```

Inspect the live agent tree too. Repair mismatches before claiming anything: integrate a completed handoff, preserve dirty work, or return an orphaned claim to `open` with a short interruption note. Do not create a planning phase merely to restate the backlog.

Use the integration branch recorded by the latest accepted Beads handoff and verify it against Git. The historical selective-reconstruction line is already incorporated into the current `update-animation-pipeline` integration branch; do not recreate its deleted branch or reconcile old prose on every start. Never merge a donor wholesale into another line or infer permission to merge to `main`. There is no standing squash or main-convergence obligation. Do not stop retained product work for inventory, history rewriting, broad pruning, or a future squash.

Use `bd ready --json` and select unassigned retained descendants of `ledgrid-poc-ib7`. Prioritize concrete safety/data-integrity failures, the selected phone-demo playback and responsiveness path, then requested current-only simplification and measured performance bottlenecks. Do not reactivate retired programs or select generic complexity deletion. Refresh the current base and ownership when dispatching; stale wave labels are not prerequisites. A leaf needs actionable acceptance, an identifiable ownership/conflict area, and focused validation; do not block delivery to perfect estimates, prose, or execution cards.

### Installed-wall delivery posture

Keep the varied catalog already delivered usable and responsive. Finish concrete retained failures before adding another visual family or expanding the product boundary. Preserve current controls and semantics; do not remove features to meet the backlog count.

Accept low-impact reversible debt when the supported path works. Record reproduction, impact, and a reopening trigger in the current handoff; apply the admission rule before creating an open follow-up. Perfect cache behavior, exhaustive recovery, speculative abstractions, blanket compatibility removal, and exhaustive test matrices are not standing obligations.

Keep feedback fast. Use focused contract tests and the small-fix validation triggers above. A changed browser feature normally needs one short smoke covering selection, control response, and Preview; include live acknowledgement when that boundary changes. Batch physical-wall checks under the applicable user authorization, including the demo-refocus authority above. Keep below-150-FPS measurements visible without turning broad FPS qualification into a demo prerequisite.

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
| Optional focused review of a small visible correction | `gpt-5.6-sol` | `medium` |
| Optional visible-feature review or explicitly requested modernization acceptance | `gpt-5.6-sol` | `high` |
| Independent review of protocol/shared data contracts, safety/data integrity, or risky live-state changes | `gpt-6-astra` | `high` |
| Mechanical edits, narrow lookups, or straightforward check execution with objective results | `gpt-5.6-luna` | `low` or `medium` |

Use the least expensive model likely to complete the accepted task correctly. Start complex work at the appropriate tier; do not require a cheaper model to fail first. Escalate Terra to Sol, or Sol to Astra, when a focused attempt exposes unresolved reasoning or cross-cutting uncertainty. Escalate the steward to Astra when dependency/product constraints cannot be reconciled from accepted decisions. Repair missing context, tooling, and environment problems directly; changing models does not fix them. Preserve evidence and the same Bead when handing off, and end the previous worker's ownership before replacement.

Reserve `xhigh` for a concrete reasoning need; `max`/`ultra` are not routine defaults. Luna does not own product decisions, shared-contract design, or independent acceptance. Review independence means a separate agent and isolated worktree, even when author and reviewer use the same model. Do not add agents solely to occupy slots; handle small scoped requests directly.

For delegated work, pass the selected model and supported effort explicitly. When using `spawn_agent` with a model override, use `fork_turns="none"` or a bounded positive turn count and include the Bead, accepted contract, base/worktree, ownership, and validation requirements. Full-history forks inherit the parent model and cannot take overrides. Respect explicit user model choices and the current tool's available models/efforts; if a required review tier is unavailable, report that limit rather than silently weakening the gate. This policy does not change the running coordinator's model or authorize a new task.

### Portfolio steward

Use a short-lived portfolio steward when the coordinator cannot resolve a real queue, dependency, ownership, or priority ambiguity with a bounded inspection, or when planning several independent workstreams would save time. At a batch boundary of up to 15 selected Beads, check whether stewardship is needed; do not spawn one automatically. Missing/stale wave metadata alone is insufficient when the next accepted leaf is clear. Use at most one steward at a time, selected by the model policy above.

The steward reads the binding Scene v2 decision, epic, recent handoffs, ready/blocked queues, and branch progress. It may prepare or repair at most 15 bounded executable Beads, partition them into dependency-ordered non-conflicting waves, clarify acceptance from accepted decisions, and retire stale coordination work. It normally changes Beads only, leaves a short epic note, and exits before implementation dispatch. It must not inflate the backlog, rewrite product intent, implement code, authorize pushes/hardware, or claim later waves.

The steward maintains the bounded retained queue under the installed-wall admission rule. Do not manufacture catalog waves or deferred polish work. Preserve closed evidence and link only actual dependencies; never cancel a concrete integrity defect merely to meet the target count.

Claim a retained leaf only after its actual prerequisites are satisfied and its base and ownership are refreshed. Historical wave labels are not required; add `worktree-ready` only for an immediate dispatch if useful. Represent a genuinely unresolved product or authority choice with the exact `human` label and an actual dependency; reuse existing applicable authorization instead of creating duplicate gates.

### Implementation workers

Use up to two concurrent implementation workers so the coordinator retains one slot and a steward or reviewer can use the fourth. Select each worker's model and reasoning effort using the model policy above; worker count is independent of model tier.

Each worker gets one claimed Bead, one local `codex/` branch, one isolated worktree, the current integration base, and an explicit ownership area. Never run two workers in the same conflict domain or let two workers regenerate the same shared artifact. During source completion the coordinator owns `update-animation-pipeline`; reconstruction uses its recorded integration branch and never merges the donor wholesale.

A worker implements the smallest complete product increment, runs baseline validation, makes one logical commit, and records a concise Beads handoff with tip SHA, changed paths, checks, and any generated output. It adds `merge-ready` but does not merge or close its own implementation Bead.

Baseline validation follows the small-fix triggers: changed-language syntax or lint, focused regression/adjacent tests, and `git diff --check`. Use a browser smoke only when the affected boundary requires browser evidence. Full browser matrices, aggregate preflight, firmware matrices, and soak tests belong at release or hardware boundaries, not ordinary leaves.

### Risk-tiered acceptance

Protocol/shared data contracts, data integrity/persistence, safety, and risky live-state transitions require independent Astra review. Ordinary visible changes use coordinator acceptance; request an optional Sol review only for a concrete uncertainty. A required reviewer works from an isolated worktree, checks the accepted Bead, reproduces the decisive integrity regression, reuses trustworthy exact-candidate evidence, and records actionable findings or approval without merging. Review is one bounded integration gate, not a second implementation or evidence program. Do not inherit catalog-modernization checks solely because a fix belongs to an Animation.

Reviewers must distinguish acceptance failures from optional debt. Block integration for a concrete failure of the accepted demo path, safety/data-integrity risk, a broken shared contract, or failure of an explicit performance criterion for that Bead. The 150 FPS goal alone is not a blanket demo gate. Record lesser findings in the handoff with a reopening trigger; create open follow-ups only under the admission rule. Approve the bounded increment when its accepted path is convincing.

### Animation modernization review discipline

Only when the user explicitly requests a new catalog-wide animation modernization program, use a review-before-implementation pass. The finite `ledgrid-poc-ib7.82` program is closed; its accepted dispositions remain evidence for follow-ups. A localized correction to an already reviewed component uses the small-fix path, not a repeat family audit. An evidence worker records source-read-only evidence for one non-overlapping family, and an independent reviewer reproduces the important claims. Select both models by the policy above; routine evidence starts with Terra and Sol acceptance, while complex simulation/timing analysis and shared-contract risks use the higher tiers. Create implementation children only from accepted `implement` dispositions or accepted product blockers; keep `retain-current`, `defer`, and `not-applicable` findings documented without turning them into speculative work.

For every component, record exactly one disposition for semantic palette, premultiplied RGBA/background composition, installation geometry, and direct interaction. Evidence must use a real resolved Scene at every sample through the production render path, including Scene palette and pace. Direct `generate_frame` calls, constructor defaults, or a context installed once at the wrong elapsed time do not prove Scene timing or palette behavior. Preserve fixed 33x138 fingerprints, semantic state and RNG digests, cache/source-tick behavior, focused tests, and desktop mean/p95/p99/max plus changed-frame ratio; never present desktop timing as Raspberry Pi evidence.

Absence of an interaction handler proves only current absence, not product-level `not-applicable`. Exercise the actual Composer event name, normally `primary`, and assess the animation's natural semantic boundary. An accepted input queues only validated events, consumes them once at the next source tick, and has exact no-input frame, logical-state, and RNG parity. If that story is not convincing, choose `defer` or give a visual/product rationale for `not-applicable`.

Geometry remains provider-owned and non-authorable. Pick one precise role per accepted renderer—such as exact-core collision, clearance-only planning, exact-edge light, or presentation-only refraction—and state what a live geometry revision may recompute. Disabled and enabled-zero paths must be exact frame/state/RNG no-ops; live revisions preserve tick, RNG, and persistent simulation state. Do not duplicate the global final-optics pass inside a renderer: story-specific geometry and installation-wide shadow/refraction are separate concerns.

Audit presets at three distinct layers: raw authored payloads, normalized component-local parameters, and visible names/descriptions. A raw legacy field may be intentionally stripped because Scene owns palette/background/brightness/pace or installation owns calibration; a collapsed normalized payload or false visible promise is product drift. Scope only the reviewed family and distinguish its failures from unrelated broad-suite noise.

When acceptance requests changes, append corrected evidence instead of overwriting the original report, keep the source worktree clean, and rerun the same independent gate. On `stop`, an approval comment and `acceptance-approved` label recorded before interruption may be reconciled and closed after verification. Preserve dirty or unmerged worktrees; clean, source-read-only review worktrees with no unique commits may be removed after their Beads are closed.

### Continuous integration loop

Wait for the first worker to finish, review the handoff, obtain required independent acceptance, and acquire the Beads merge slot only for integration. Rebase onto the recorded current integration tip, validate affected changes if the prospective tip differs from the tested candidate, fast-forward merge, record the integrated SHA, close the Bead, release the slot, and remove only the clean merged worktree. Reuse exact-candidate checks after a no-op rebase. Unlock and claim the next dependency wave only after its prerequisites integrate, then refill available implementation capacity.

Continue until the user says `stop`, no meaningful local work can proceed, or a real permission/product decision is required. On stop, interrupt workers, wait for the live tree to empty, release the merge slot, reconcile every `in_progress` Bead, and preserve dirty or unmerged worktrees. Never stage `.beads/**`, traces, screenshots, or local run state—except `.beads/interactions.jsonl`: validate its append-only JSONL records and commit that audit log when it changes. Never treat that commit as a substitute for `bd dolt push`/pull or a Dolt backup.
