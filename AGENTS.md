# Agent instructions

Deliver useful changes for the installed five-receiver 33x138 wall. Prefer small, verified improvements and fast feedback. Current user instructions override older project decisions and historical handoffs.

## Task tracking and authority

Use Beads for durable tasks, blockers, handoffs, and memory. Reuse hook-loaded context; run `bd prime` only when it is missing or stale. Use the [Beads skill](.agents/skills/beads/SKILL.md) for issue operations. Do not maintain parallel Markdown TODO or memory registries.

Beads is **local-only** by user decision. The Dolt database is authoritative; `.beads/issues.jsonl` is a passive export. Do not publish issues or memories, run Dolt remote sync, or enable automatic pushes. Preserve local backups. A configured remote is not authorization to use it.

Ordinary requests authorize the requested local work, not Git commits, pushes, or a merge to `main`. `start` explicitly enables session autopilot; `resume` and `keep going` continue it when the initiative is clear. Autopilot permits local branches/worktrees, useful delegation, local commits, integration, and continued work on accepted Beads. It does not authorize Git/Dolt pushes, a main merge, or new product scope. Read the [autopilot procedure](.agents/skills/beads/references/autopilot.md) only for that mode.

## Current product contract

The October 9 simplification decision in `ledgrid-poc-ve0` supersedes older native-execution, transactional rollback, and exact displayed-receipt requirements.

- Serve the installed five-receiver 33x138 wall. Preserve phone/desktop Composer, browser rendering, Widgets, Looks, playlists, manual takeover, controls, calibration and Home Assistant integration. Keep all game animations; retire World Flags and receiver-native-only entries. Saved references to removed components must remain recoverable and report unsupported components rather than silently substitute.
- Render full RGB frames on the host. Receivers handle transport and LED output; do not rebuild receiver-native modules, sparse overlays, profile libraries, distributed activation transactions, or qualification machinery.
- Show requested scene, controller playback, receiver connectivity and errors separately. Controller playback is not proof of exact all-five physical display. Preserve command ordering/correlation, CRC/FEC, physical mapping, bounded waits and brightness controls.
- This hobby installation accepts interrupted playback and manual recovery. Deployment is stop/copy/build/flash/start/readiness, with no immutable releases or automatic rollback guarantees. Keep personal data outside replaceable application files: calibration, Looks, playlists, custom presets and settings. Back up migration inputs and preserve current brightness, including zero. No need to restore active playback after a failed operation.
- One wall configuration and online-only Composer are supported. Browser rendering remains; offline editing and cache migration do not. Preserve inexpensive file-write integrity and input validation.
- Receiver 3's known CRC/FEC increments, occasional invalid protected status and reduced output rate remain accepted degradation. Report them truthfully; do not initiate physical investigation or block a demo on these alone.
- Keep 150 FPS as an installed-output goal and prepared switches within five seconds. Visible stutter, failed playback, false status and data loss require correction. Desktop timing is not installed-wall evidence.
- The approved simplification includes coordinated host/receiver deployments, reflashes and maintenance downtime after focused validation and required independent review. Capture current personal data/settings first. No rewiring, data destruction, Git/Dolt push or main merge is authorized.
- Remove compatibility after checking current consumers. A legacy name alone does not prove obsolescence. Report maintained implementation, tests, and generated-artifact savings separately; line-count targets never justify broken retained features.

## Small-fix fast path

Inspect the relevant issue, claim/ownership, current base, and dirty work once. Handle understood corrections directly in a suitable exclusive checkout. Use a worktree for concurrent ownership or a required pre-integration review; do not create agents, worktrees, waves, or a portfolio review merely to process one fix.

Use one Bead for the requested increment and one concise handoff: result, changed paths, candidate/base when relevant, checks, and remaining blocker. Do not create separate records for routine research, review events, or each check. Open follow-ups only for a reproducible supported-path failure, concrete integrity risk, measured relevant performance shortfall, or an explicit request. Record lesser debt with its impact and reopening trigger in the current handoff. Keep roughly 10–15 actionable product leaves at most, not as a quota; preserve evidence and active owners.

## Validation and review

Choose model and thinking effort by uncertainty and consequence, not a fixed expensive coordinator role. Routine delegated work defaults to Sol/medium, objective mechanical work to Luna/low, and difficult reasoning or required independent review to Astra/high. Delegate only for useful independent progress or required scrutiny; stop reviewing once concrete risks are resolved. The [model and review policy](.agents/skills/beads/references/models.md) defines escalation and runtime fallbacks.

- Behavior corrections: a focused regression demonstrating the failure and fix, changed-language syntax/lint, relevant adjacent checks, and `git diff --check`. Documentation-only changes need consistency/link checks, not application tests.
- Browser smoke: only for changed UI wiring, browser execution, asset loading, or behavior production-render tests cannot establish. Exercise the affected path; include live acknowledgement only if that boundary changed. Rebuild shipped generated assets when their inputs change.
- Benchmark: when per-frame work, allocations, cadence, simulation complexity, or an explicit performance criterion changes. Cache correctness with unchanged work normally needs changed-frame/cache assertions. Use the animation skill for simulation and render-specific evidence.
- Firmware: build the installed target when firmware changes. Use the focused current command/playback tests for activation and reset behavior. Run the current aggregate gate once per relevant integration/deployment batch. Full catalog sweeps, broad historical test repair, and long playlist runs need a scoped reason.
- Reuse exact-candidate evidence unless relevant source, base, environment, or a concrete concern changes. A no-op rebase or a new reviewer is not a reason to rerun everything.
- Independent Astra review in an isolated worktree is required for protocol/shared data contracts, data integrity/persistence, safety, or risky live-state transitions. Ordinary reversible UI/renderer changes use coordinator review. Optional review must resolve a concrete uncertainty; visibility alone is not a trigger. See [model selection](.agents/skills/beads/references/models.md) only when delegating.
- Block acceptance for a concrete failure of the accepted path, integrity/safety risk, broken shared contract, or failed explicit Bead criterion. Do not turn optional debt into an automatic follow-up program.

## Shell and remote access

Use non-interactive file operations (`cp -f`, `mv -f`, `rm -f`, recursive flags only when needed). Use `apt-get -y` and `HOMEBREW_NO_AUTO_UPDATE=1` for applicable package operations.

Always use the dedicated key for SSH/SCP; never fall back to default identities, the human SSH agent, passwords, or a generated key. If it is missing or unreadable, report the blocker.

```bash
LEDGRID_SSH_KEY=/Users/rtimmons/Projects/ledgrid-poc/.gpt-key
ssh -i "$LEDGRID_SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 user@host -- command
scp -i "$LEDGRID_SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes source user@host:path
```

Pass the same key to recipes, for example `SSH_KEY="$LEDGRID_SSH_KEY" just deploy`.

## Handoff and instruction maintenance

Close completed Beads with acceptance evidence; distinguish completed, superseded (named successor), and not-planned (retired obligation). Preserve unfinished work and report changed files, validation, and any genuine blocker. Check Git status. Commit only with existing authority; no ceremonial commit per review or audit event.

Do not stage database files, exports, backups, traces, screenshots, or run state. `.beads/interactions.jsonl` is the existing versioned append-only audit-log exception: validate JSON and unique IDs, then batch it into an authorized implementation/session commit. It is neither issue sync nor backup; local-only issue/memory policy still applies. Repository instruction/config files are source, not tracker data.

Keep this file authoritative. `CLAUDE.md` imports it; do not maintain a second policy copy. Put conditional workflows in skill references, historical evidence in Beads, and only recurring decision-changing guidance here. Preserve native Beads lifecycle hooks; avoid duplicate setup blocks and routine doctor/setup runs without a concrete problem.

<!-- BEGIN BEADS CODEX SETUP: generated by bd setup codex -->
## Beads Issue Tracker

Use Beads (`bd`) for durable task tracking in repositories that include it. Use the `beads` skill at `.agents/skills/beads/SKILL.md` (project install) or `~/.agents/skills/beads/SKILL.md` (global install) for Beads workflow guidance, then use the `bd` CLI for issue operations.

### Quick Reference

```bash
bd ready                # Find available work
bd show <id>            # View issue details
bd update <id> --claim  # Claim work
bd close <id>           # Complete work
bd prime                # Refresh Beads context
```

### Rules

- Use `bd` for all task tracking; do not create markdown TODO lists.
- Run `bd prime` when Beads context is missing or stale. Codex 0.129.0+ can load Beads context automatically through native hooks; use `/hooks` to inspect or toggle them.
- Keep persistent project memory in Beads via `bd remember`; do not create ad hoc memory files.

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.
<!-- END BEADS CODEX SETUP -->
