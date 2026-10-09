# Models, delegation, and review

Choose by uncertainty, consequence of error, and verification strength—not role title, file count, or visibility alone. These are project defaults informed by [OpenAI model selection](https://developers.openai.com/api/docs/guides/model-selection), not measured project benchmarks. Honor explicit user choices and the models/efforts actually exposed by the current tool.

## Model and thinking defaults

| Work | Model | Effort |
| --- | --- | --- |
| Mechanical lookup, extraction, or objective check execution | Available Luna (currently `gpt-5.6-luna`) | `low`; `medium` if interpretation is needed |
| Bounded implementation; routine coordination or queue reconciliation | `gpt-6.1-sol` | `medium`; `low` for an obvious correction |
| Cross-component debugging, simulation behavior, uncertain integration | `gpt-6.1-sol` | `high` |
| Difficult architectural decisions, conflicting evidence, unresolved complex failure | `gpt-6-astra` | `high` |
| Optional focused review resolving a named uncertainty | `gpt-6.1-sol` | `medium`; `high` for cross-component reasoning |
| Required independent protocol/shared-contract, persistence/integrity, safety, or risky live-state review | `gpt-6-astra` | `high` |

Coordinator is an ownership role, not an automatic requirement for Astra/high on every action. Keep the current session model; this policy does not switch it or justify creating a new agent just to run a cheaper model. Use the table for selectable delegated work or future session recommendations. A steward is optional and normally Sol/medium; use high only for actual dependency ambiguity.

Reserve `xhigh` for a named unresolved reasoning problem. `max`/`ultra` are not routine defaults. Higher effort is not a substitute for missing context, a reproducible failure, or decisive checks. Start difficult/high-consequence work at the appropriate tier without requiring a cheaper model to fail first. Escalate after a focused attempt exposes reasoning uncertainty; fix tooling/context failures directly. Retain the same Bead and evidence, and end the previous worker's ownership before replacement.

If Sol 6.1 is unavailable, use an exposed Sol at the corresponding supported effort. If Luna is unavailable, keep a tiny task local or use Sol/low for a useful independent chunk. Do not request unavailable model IDs. Luna does not own product decisions, shared-contract design, or independent acceptance. If required Astra review is unavailable, report that limitation; do not silently weaken the gate.

## When delegation earns its cost

Delegate only a bounded independent task that provides useful parallel progress, isolates a substantial investigation, or supplies required independent review. Handle a small understood correction directly. Never spawn agents merely to occupy slots, assign ceremonial roles, or move already-understood work to a cheaper model.

Pass the selected model and effort explicitly with `fork_turns="none"` or bounded context; full-history forks inherit the parent and cannot override them. Supply the Bead, relevant constraints, candidate/base, ownership and allowed mutations, acceptance, focused checks, and expected result. Implementation workers use isolated worktrees; read-only lookups do not need one. Do not duplicate the worker's investigation while waiting. Use completion notifications/bounded waits instead of polling unchanged state. Follow the autopilot reference's two-worker ceiling when that mode is enabled.

## Review proportional to risk

| Risk and uncertainty | Scrutiny |
| --- | --- |
| Mechanical/documentation or localized reversible UI/renderer correction with decisive evidence | Coordinator checks the diff and affected behavior; no independent reviewer by default |
| Reversible behavior with a specific unresolved uncertainty | Coordinator may request one focused Sol review of that uncertainty; visibility alone is not a trigger |
| Protocol/shared data contract, persistence/data integrity, safety, or risky live-state transition | One independent Astra reviewer in an isolated worktree before integration or deployment |

Classify the actual changed behavior: touching a protocol file without changing its contract does not by itself create a protocol review; a tiny edit that changes receipt truthfulness does. Required review cannot be avoided by splitting a risky change into small files or tasks.

Give reviewers the exact candidate diff, accepted contract, and existing check results. Review the failure modes at the changed boundary and reproduce the decisive integrity regression when required. Reuse valid exact-candidate evidence; do not repeat whole suites, browser matrices, wall sweeps, or long runs by role. Strong author models do not waive review independence.

Finish after the accepted contract is satisfied, decisive checks pass, and concrete blocking findings are resolved. Report optional debt separately. A second reviewer or broader validation needs a distinct unresolved risk; no routine reviewer chains or reviews of reviews. Re-review fixes only at the affected boundary unless they change the wider risk. Record model/effort and the reason for nonroutine escalation in the existing handoff, not a new tracking artifact.
