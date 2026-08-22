# Lumen Path: an activity-first control paradigm

## The bet

The wall is not a folder of 292 things. It is a room-scale material that helps a group move from one human state to another.

Lumen Path makes the primary object a **journey**: a bounded session with an immediate purpose, a beginning, one or more turns, and an ending condition. “Breathing Moss · Tidepool” is implementation material. “Calm the room over 45 minutes” is the operator’s intent.

This changes the product’s center of gravity:

| Catalog-shaped control | Lumen Path |
| --- | --- |
| Choose an animation | Choose what the room should become |
| Apply a preset | Rehearse a moment, then take it live |
| One indefinite playback state | A timed arc with authored turns and an ending |
| Categories and folders | Human purposes and qualities |
| Preview thumbnail | Isolated rehearsal on the 32 × 138 physical shape |
| Settings page | Independent atmosphere and material behaviors |
| Error console | “Can I safely host/play/paint?” wall care |

## Core nouns

- **Purpose** — calm, host, play, paint, diagnose, or author an arc. This is the entry point.
- **Journey** — a session that changes the room over time. A journey can begin now or at a scheduled time.
- **Turn** — one phase of a journey. “Exhale,” “Gather inward,” and “Land softly” are turns.
- **Moment** — the actual scene within a turn: one background and an optional clock overlay.
- **Recipe** — a provider-qualified component plus preset and overrides. Recipes are ingredients, not top-level navigation.
- **Rehearsal** — an isolated, tall preview. Scrubbing, comparing, gestures, vibe, and plant changes in rehearsal never mutate live state.
- **Atmosphere** — global Vibe and plant material semantics. These are independent of a recipe or scene.
- **Field guide** — the complete, untruncated set of implementation possibilities, reached when a person wants to choose ingredients directly.
- **Wall care** — health translated into safe activities, with developer detail one layer lower.

## Core walkthrough

1. At **Trailhead**, answer “What should the room become?” Choose **Calm the room**.
2. Lumen Path proposes a 45-minute journey: **Exhale → Gather inward → Land softly**.
3. Scrub the whole journey or select a turn. The private rehearsal uses the wall’s true tall aspect and repeatedly says the physical wall is unaffected.
4. Tune a moment in **Scene Studio**. The fixed contract is visible: one background, optional clock overlay, opacity/placement/stale behavior, and a known Python fallback. Receiver-native content is provider-qualified and its preview says it is a host simulation, not framebuffer readback.
5. Save the journey or layout without applying it. Saved/draft/live state remain distinct.
6. Choose **Take journey live**. Review “on wall now” against “proposed,” acknowledge the intended physical wall, then press and hold for one second. Releasing cancels.
7. The live ribbon now identifies the physical content and says whether it matches saved state. Stop/resume, power, and brightness remain close at hand.

An alternate walkthrough starts at **Author an arc** and schedules guest arrival, shared energy, and afterglow. It uses the same turns/rehearsal/confirmation grammar.

## How browsing 292 items works

The field guide is intentionally secondary but complete. It renders all 292 preset possibilities across 52 components with no “load more” and reports “292 of 292.” Search and purpose filters narrow the view without truncation. Readiness is never hidden:

- **Ready** can be rehearsed, compared, and reviewed for live use.
- **Build required** remains discoverable and can be compared, but cannot go live.
- **Unavailable** stays visible with its state.
- **Quarantined** stays visible and cannot be activated.

Every card carries provider and role identity. A three-up rehearsal deck keeps the physical tall shape useful for comparison. The receiver-native preview truth is repeated in that deck.

## State and safety model

There are four visually separate states:

1. **Live** — what the physical wall is believed to be presenting.
2. **Saved** — a reusable journey or scene layout.
3. **Draft** — edits in the current private rehearsal.
4. **Drift** — whether live differs from the selected saved state.

No preview action changes live. “Save journey only” and “Save layout only” are explicit. Content takes live through a review plus hold gesture. Global output/atmosphere controls use their own explicit Apply button. Point/hole gestures use an inline stage-then-confirm step.

This prototype is honest about the contract’s observability limits. For receiver-native backgrounds it says “host simulation · not framebuffer readback.” In Wall Care, write-only receivers are “expected degraded,” not falsely healthy and not falsely failed. Outbound transfer is not described as acknowledgement or release acceptance.

## Independent atmosphere

Vibe is a global grade with the stable choices Neutral, Quiet, Cozy, Vivid, and Celebration. Plant behavior is a separate material model grouped by light, field, and surface semantics. The prototype includes shadow/illuminate/refract, attract/slow zone, and obstacle/portal/habitat examples. It explains that unsupported modifiers remain visible and are never silently applied.

Global brightness, target FPS, and motion speed live in Room Tuning. They do not masquerade as per-recipe overrides. Scene Studio’s speed/palette controls are explicitly scoped to the scene.

## Phone behavior

On narrow screens, the paradigm does not collapse into a tiny desktop dashboard:

- The live physical-state ribbon remains at the top.
- Purpose cards become a single thumb-friendly trail.
- The current wall becomes a compact vertical specimen.
- Journey rehearsal moves before the turn list, so safety and outcome stay visible.
- Primary navigation becomes four bottom destinations; Scene Studio remains contextual.
- Modals become bottom sheets and comparison becomes a vertical stack.
- The field guide still renders every match and never substitutes pagination.

## Secondary, intentionally lower-fidelity branches

The prototype demonstrates the handoff for point/hole/D-pad interaction, painter/emoji, semantic mask inspection, performance traces, provider packages, and calibration. It does not pretend these are complete editors. Their role here is to prove they fit the activity model:

- play and paint are sessions entered from a purpose;
- gestures begin in rehearsal and require a live send action;
- masks are semantic plant geometry, not a decoration toggle;
- diagnostics begin with activity readiness, then disclose technical evidence;
- developer tools sit below Wall Care instead of competing with room goals.

## Product and implementation boundary

This is a dependency-free, offline concept prototype. It makes no API calls and cannot mutate the backend or hardware. The interaction model aligns with the backend’s isolated preview, scene validation/application, global Vibe, plant modifier, output tuning, interaction, painter, and health concepts, but the prototype uses deterministic in-browser simulation only.
