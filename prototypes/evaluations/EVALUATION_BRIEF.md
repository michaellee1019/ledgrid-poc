# Wall UX design-tournament brief

This evaluation is about task success and trustworthy control of a physical
installation, not visual preference in isolation. Review every candidate from
the assigned persona. Do not modify candidate files.

## Ground truth

- The wall is a 32 × 138, 4,416-LED living plant installation.
- The library contains 52 components and roughly 292 presets across ambient,
  clocks/information, games, GIF/pixel art, calibration, diagnostics, and
  developer content.
- Component identity is provider-qualified. Host Python and receiver-native
  content can share names but not behavior, availability, or preview provenance.
- A preview is isolated work. It must never be confused with physical live
  output. A receiver-native preview may be a host simulation, not framebuffer
  readback.
- A scene has one background and an optional clock overlay. It must support
  validation, known Python fallback, placement/opacity/stale policy, layout-only
  saving, and deliberate application.
- Global Vibe, plant material, brightness, FPS, and operator speed are not preset
  or scene-owned state.
- Some catalog entries are visible but unavailable, build-only, quarantined, or
  developer-only.
- Receiver evidence has expected-degraded states that are not equivalent to a
  generic failure.

## Candidate set

Evaluate every completed candidate listed by the coordinator. Treat fixture data
as representative and test the interaction model it demonstrates. Do not penalize
a cold concept merely for not being backend-connected; backend fit is a separate
criterion.

## Evidence to record for every adventure

1. Starting assumptions: what did the persona expect to do first?
2. First click/tap and the reason it seemed right.
3. Task path, including wrong turns and recovery.
4. Whether full names, provider, role, availability, and preview/live state stayed
   legible without hover or guesswork.
5. The exact moment the persona believed the physical wall would change.
6. Confidence at completion on a 1–5 scale.
7. A task result: success, partial, or failure.
8. One pattern worth preserving and one concrete friction to fix.

## Shared safety adventures

- From a cold start, explain what is physically live now and stop it safely.
- Find a calming preset among hundreds, audition it, compare alternatives, and
  deliberately take one live.
- Encounter receiver-native and unavailable content; explain what can be
  previewed, what can be performed, and why.
- Compose a background plus clock overlay, notice drift or dirty state, validate,
  save only the scene layout, and decide whether to perform it.
- Change Vibe and plant material without accidentally treating them as part of a
  preset or saved scene.
- Diagnose a receiver whose return telemetry is intentionally limited but whose
  playback agreement is still usable.
- Repeat the persona's highest-frequency task at phone width.

## Scoring

Score each category from 1 (unsafe/unusable) to 5 (clear and resilient), and cite
observable evidence.

| Category | Weight |
| --- | ---: |
| Seconds to orient and reach the common task | 15 |
| Preview/live separation and consequential-action safety | 20 |
| Catalog scale, complete typography, and comparison | 15 |
| Scene/global-state mental model | 15 |
| Recovery, disabled/unavailable states, and honest provenance | 10 |
| Phone and touch operation | 10 |
| Technical fit to the existing backend contracts | 10 |
| Accessibility and keyboard legibility | 5 |

The weighted score supports—not replaces—the written evidence. End with a ranked
list, then a cross-candidate kit of parts: patterns to keep regardless of which
overall paradigm wins.
