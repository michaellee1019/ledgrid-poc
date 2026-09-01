# Animation Modernization Review

This document captures the evidence method and the current handoff for the Scene v2 catalog review. Beads remains the authoritative source for status, dependencies, findings, and implementation acceptance.

## Current handoff

As of 2026-09-01, `ledgrid-poc-ib7.82` covers exactly 39 retained Animation IDs and the four shipped starter Look IDs at selective-reconstruction base `25c76925841a4223c560a94a89965362552de12d`. The older `pixel_chase` audit row is outside the retained catalog.

Family reviews `.82.1` through `.82.8` are independently accepted and closed. Their accepted findings have produced gated implementation children through `.82.6.2`; those children remain open, unassigned, and without `worktree-ready` until the final shipped-Looks review `.82.11` clears the breadth gate. No push, deployment, receiver, camera, or physical-wall action is authorized by this program.

The next fresh `Start` should:

1. Run the normal Beads reconciliation and confirm the selective-reconstruction worktree is still clean at the recorded tip and the merge slot is free.
2. Start one Sol/high portfolio steward for the just-accepted `.82.7` and `.82.8` findings. It should create only bounded children warranted by accepted findings, avoid duplicates, keep every implementation child blocked by `.82.11`, and unlock exactly `.82.9` and `.82.10` for read-only review.
3. Continue the same Terra evidence/Sol acceptance loop for `.82.9` foundational ambient Animations and `.82.10` narrative/pixel-art Animations.
4. Review `.82.11` shipped whole-scene Looks last, reconcile the complete coverage ledger, then decide which accepted implementation wave becomes runnable.

Use `bd show ledgrid-poc-ib7.82 --json` and the epic comments for the exact child inventory. Do not recreate already-scheduled work from prose summaries.

## Evidence contract

Every reviewed component receives one disposition per axis: `implement`, `retain-current`, `defer`, or `not-applicable` for semantic palette, RGBA/background composition, plant/foliage/globe interaction, and direct user interaction. A capability is not required merely because Scene v2 can support it.

Canonical visual evidence uses the production Scene path at 33x138. Resolve and render the Scene for every sample so palette, pace, wall-clock/scaled time, source cadence, and cache authority are genuine. Direct renderer calls are useful probes but cannot substitute for resolved-Scene fingerprints. Where palette is presentation-only, prove that alternate palettes change pixels while logical state, RNG, and source tick remain exact.

Proposed premultiplied alpha must compose against at least two actual receiver-native Background states. Intentional opacity should be demonstrated as a full alpha-255 plane that hides both backgrounds and whose dark field is part of the animation's story.

Installation geometry is immutable provider data, never preset or Scene authoring. Separate exact globe cores from clearance masks, and separate renderer-specific behavior from the one global final-optics stage. Each accepted role must define disabled/zero parity and live-revision continuity without resetting state, RNG, or tick.

Direct input must use the actual Composer event and the animation's semantic step. Validated events queue atomically, consume exactly once, and preserve exact no-input behavior. Empty current capability lists are evidence of absence, not a complete product decision.

Preset review distinguishes raw legacy fields, normalized component-local payloads, and user-visible copy. Preserve Scene authority over palette, Background, brightness, and pace, and installation authority over calibration. Record collapsed local intent or false descriptions as bounded product drift; do not attach unrelated aggregate-suite failures to the reviewed family.

## Session learnings already reflected in accepted findings

- True resolved-Scene timing caught mislabeled palette frames in Living Systems and pace-bypassing fingerprints in Procedural Sculptures.
- Product-level gesture review changed several blanket `not-applicable` decisions into bounded semantic-tick inputs or explicit deferrals.
- Narrow geometry roles prevented vague "plant aware" work from becoming generic mask coupling. Firefly, Cyclic Reef, Quasicrystal Bloom, and all strategic games remain deferred where the story is not yet strong.
- Preset drift must be counted precisely. Examples include collapsed Living Ecosystem payloads, unsupported Sculpture raw fields versus only seven false visible descriptions, and concrete copy drift rather than broad test-failure totals.
- Time/Weather Animations share a real semantic-palette contract gap while correctly retaining full-plane opacity and final optics exactly once.
- Strategic gameplay retains opaque presentation and current input boundaries; geometry remains deferred rather than forced.

Independent evidence and hashes live in the corresponding Beads comments and `/private/tmp/ledgrid-ib7-82-*-evidence` directories for this host session. Treat temporary paths as supporting evidence, not durable state; the Beads comments are the durable handoff.
