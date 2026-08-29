# Deprecated: LED Grid Wall HAT hardware iteration plan

This plan was retired on 2026-08-29. Beads is the authoritative source for the
hardware design scope, status, dependencies, and handoff context.

The work is tracked by epic `ledgrid-poc-29i` (Revise LED Grid Wall HAT for
reliable dual-bus SPI). Use:

```bash
bd show ledgrid-poc-29i
bd list --parent ledgrid-poc-29i
```

The epic and every child bead are deliberately deferred. They must not appear
in `bd ready` or be treated as actionable until explicitly reactivated.
Reactivation would authorize design-file work only; it would not authorize
connecting to, querying, powering, flashing, or otherwise operating live
hardware.

The former detailed requirements remain available through Git history for this
path.
