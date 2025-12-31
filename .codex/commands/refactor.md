---
name: refactor
description: Start or continue the LED grid refactor plan.
---

# /refactor

Use this command to start or resume the refactor work.

When invoked:
1. Read `refactor.md`.
2. Summarize current status, phase, and top checklist items.
3. Review "TODO Workflow & Registry" and triage Inbox items.
4. Propose 1-3 next tasks aligned with the roadmap and TODOs.
5. Ask for confirmation before making edits.
6. After changes, update `refactor.md`: checklist, TODO status, session notes,
   decisions, and open questions.

If the user provides new TODOs in the command, append them to the TODO Inbox using
this format:

```
- [ ] TODO-YYYYMMDD-##: Short title
  - Phase: 1|2|3|4|5|6|Unassigned
  - Priority: P0|P1|P2
  - Acceptance: Clear outcome, test, or validation
  - Notes: Optional context
```
