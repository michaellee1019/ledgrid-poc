# Current Composer acceptance criteria

## Scope

Composer is the sole browser product. `GET /` redirects to `GET /composer`; the
route, catalog, draft, local rendering, Check, and guarded activation contracts
are documented in [the web route inventory](../web/README.md) and
[the animation-system contract](ANIMATION_SYSTEM.md).

Opening Composer is private and non-mutating. It loads the bundled catalog and
renders locally before an operator deliberately requests a wall workflow.

## Demo delivery posture

The current product phase optimizes for breadth and fast visual learning. The
demo should offer a varied catalog of visibly different components with fun,
intuitive controls, strong defaults, and a short path from selection to browser
Preview. Several thin, complete catalog entries are more valuable now than one
entry with exhaustive state handling or production polish.

Reversible technical debt is acceptable when it accelerates that breadth.
Polish issues, rare state-management edges, speculative abstractions, and
exhaustive compatibility or recovery work belong in Beads unless they threaten
safety, corrupt authored work, violate the shared Scene v2 contract, or break
the current demo path. Findings are recorded rather than ignored, but they do
not automatically block the next catalog slice.

Every visible slice should get quick browser feedback: select it, adjust its
main controls, Preview it, and confirm the relevant live acknowledgement with a
focused smoke. Physical-wall feedback should be batched at useful demo
checkpoints and performed only under separate wall/deployment authorization.

## Product contract

| ID | Priority | Current requirement | Evidence boundary |
| --- | --- | --- | --- |
| COMP-01 | P0 | Root navigation reaches Composer and no second browser product is documented or served. | Route and terminology inventory test. |
| COMP-02 | P0 | Catalog selection, parameter edits, import/export, previews, and local Check remain drafts until a deliberate guarded activation. | Composer/API contract tests. |
| COMP-03 | P0 | A guarded activation is tied to the exact document, provider/runtime identities, and Check result. Accepted work is Pending until a correlated observation reports its terminal state. | Scene activation contract tests and controller evidence. |
| COMP-04 | P0 | Preview is an authored simulation, never receiver framebuffer readback or proof of physical output. | Composer copy and preview provenance contract. |
| COMP-05 | P1 | Provider, role, readiness, and preview capability are distinct; catalog visibility never authorizes activation. | Generated catalog and API contract tests. |
| COMP-06 | P1 | Composer works at supported narrow widths with keyboard-operable controls and clear state text. | Focused browser evidence when a browser-bound change requires it; physical iPhone and VoiceOver evidence remain separately owned safeguards. |
| COMP-07 | P1 | Wall settings, stop, observation, rollback, and diagnostics remain server-gated and report observed state rather than optimistic local state. | API/controller contracts and deployment evidence. |
| COMP-08 | P1 | The demo catalog favors visibly distinct choices, intuitive component-local controls, useful defaults, and rapid Preview feedback over exhaustive polish of a single component. | Focused component contracts and one short browser smoke per visible slice; separately authorized live-wall checks at demo checkpoints. |

## Terms with one meaning

- **Draft** is private authored state.
- **Preview** is local simulation and cannot claim physical output.
- **Check** evaluates an exact draft and becomes stale when its identity changes.
- **Pending** is an accepted activation awaiting correlated observation.
- **Active**, cancelled, failed, and rolled back are observed terminal states.

## Source-phase validation

This document's source contract is validated by documentation links, the
Composer route inventory, and the removed-term scan in
`tests/unit/test_current_ux_contracts.py`. Regenerate browser assets only when
their inputs change. A browser run is not a routine documentation gate.

The retained physical-device evidence tooling under
`tools/browser_qualification/` is a post-squash safeguard, not a competing
browser product or a full-matrix command for ordinary source changes.
