---
name: beads
description: Track project work with bd; run ledgrid autopilot for start, resume, keep going, or stop.
---

# Beads

Follow [AGENTS.md](../../../AGENTS.md) for product scope, authority, validation, and review. Use Beads as durable task state; ordinary scoped work does not activate autopilot.

## Scoped work

Reuse context already loaded by hooks. Run `bd prime` only if context is missing or stale; if empty, check `bd where`. Inspect the relevant issue and existing claims before creating or claiming work. Use one issue for one meaningful increment, not for each tool call or review step.

```bash
bd show <id>
bd update <id> --claim
# After focused validation:
bd close <id> --reason="Completed: result and acceptance evidence"
```

If no matching issue exists, use `bd create --title="..." --description="Problem and bounded correction" --acceptance="Decisive check" --type=task --priority=2`. Use `bd ready --json` when selecting new work, not on every scoped follow-up. Use `bd update` instead of interactive `bd edit`; consult command help for unfamiliar flags.

Record one concise handoff with result, paths, checks, candidate/base if relevant, and any blocker. Open follow-ups only under the root admission rule. Keep reusable current decisions in `bd remember --key <key> "..."`; keep historical execution detail in issue notes. Update a current fact in place rather than adding competing “latest” memories.

## Autopilot and delegation

For `start`, or continuation of an established initiative, read [autopilot.md](references/autopilot.md). For `stop`, use its stop procedure. Read [models.md](references/models.md) only when delegation is useful or independent review is required. Small fixes stay with the coordinator; ordinary visible changes do not require independent review.

## Data and closeout

The local Dolt database is authoritative. No remote sync or publication of tracker data/memories is authorized; preserve local backups. JSONL issue exports are not a sync mechanism. Follow root policy for the audit-log exception and Git authority.

Before ending implementation work, record/close completed work, run relevant checks, inspect Git status, and report the result. Do not infer commit/push authority from a generic Beads checklist. Keep native lifecycle hooks and use `bd prime --help` for supported context customization; do not reinstall setup on routine sessions.
