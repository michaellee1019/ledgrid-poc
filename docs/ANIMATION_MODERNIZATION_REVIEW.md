# Animation Modernization Review

This document captures the evidence method and the current handoff for the Scene v2 catalog review. Beads remains the authoritative source for status, dependencies, findings, and implementation acceptance.

## Current handoff

The finite `ledgrid-poc-ib7.82` review and accepted product modernization program closed on 2026-09-11 after the Emoji cache correction integrated at `197ce0a`. It covers 39 retained Animation IDs and four shipped starter Looks; `pixel_chase` remains outside that surface. Existing follow-up debt and hardware authority gates remain tracked in Beads.

For current status and the integration base, use `bd show ledgrid-poc-ib7.82 --json` and the latest accepted epic handoff. Do not reconstruct a runnable wave from this historical evidence guide. Local corrections to already reviewed components use [AGENTS.md — Small-fix fast path](../AGENTS.md#small-fix-fast-path); the full evidence contract below applies to a family review or a change affecting those axes, not every follow-up fix.

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
