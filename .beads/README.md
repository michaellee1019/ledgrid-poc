# Local Beads tracker

Project policy lives in [AGENTS.md](../AGENTS.md); the [Beads skill](../.agents/skills/beads/SKILL.md) describes scoped work and routes to autopilot only when enabled.

```bash
bd ready
bd show <id>
bd update <id> --claim
bd close <id> --reason="Completed: result and evidence"
```

Issues and memories stay in the local Dolt database. Preserve local backups; do not publish tracker data, enable automatic pushes, or use remote sync without explicit approval of the destination. Git code pushes and Dolt issue sync are separate operations. `.beads/issues.jsonl` is a passive export; `.beads/interactions.jsonl` is the separately versioned audit log governed by root policy.

`PRIME.md` customizes startup context through the installed `bd prime` interface. Native Codex/Claude lifecycle hooks load that context; avoid duplicating their command reference in instruction files. Current reusable facts belong in `bd remember`; historical execution details belong in issue notes.

The project Beads skill is intentionally customized. `bd setup codex --check` can report it as stale because it differs from the stock template. Compare relevant upstream changes before updating; do not overwrite project policy just to clear that comparison. Keep the native hooks and generated Codex pointer block intact.

For unfamiliar operations, use `bd <command> --help`. Upstream integration guidance: [Beads IDE setup](https://github.com/gastownhall/beads/blob/main/docs/getting-started/ide-setup.md).
