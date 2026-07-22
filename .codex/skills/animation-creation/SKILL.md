---
name: animation-creation
description: Create, improve, debug, or review real-time procedural animations, autonomous visual simulations, mask-aware physical installations, and pre-rendered animation packs, especially LED-grid Python plugins, curated presets, pixel-art loops, and GIF assets. Use for new animation plugins, clocks and other time/data-driven displays, visual-behavior changes, preset or asset families, coordinated agents or particles, gameplay/autoplay logic, calibrated obstacle or occlusion masks, animation parameters, frame pacing, deterministic simulation tests, render benchmarks, or Raspberry Pi CPU optimization.
---

# Animation Creation

Build animations as observable systems: clarify the intended motion or behavior, preserve the host render contract, validate the visual outcome through simulation, and measure performance at realistic hardware dimensions.

## Establish the outcome

Translate subjective requests into observable acceptance criteria before editing. Infer low-risk details from the repository and continue; ask only when a choice would materially change the result.

Identify:

- The visual story: what moves, interacts, accumulates, clears, loops, or resets.
- The behavioral goal: for example, “intelligently win” means coordinated decisions that produce measurable line clears and avoid premature game-over resets.
- The operating envelope: grid dimensions, entity-count range, target FPS, CPU, memory, and output hardware.
- The controls: useful parameters, defaults, limits, and whether changes apply live.
- The asset envelope when pre-rendering: native dimensions, aspect ratio, frame
  count, source FPS, palette/alpha needs, storage budget, and art provenance.
- The evidence needed: unit assertions, deterministic simulation results, frame-time percentiles, or physical observation.

Tell the user what constraint or discovery is shaping the implementation. Report benchmark numbers with the tested dimensions and machine; never imply desktop timings are Raspberry Pi measurements.

## Inspect before designing

Read the animation base class, plugin loader/registry, two nearby plugins, animation tests, and existing benchmark tooling. Check the worktree and preserve unrelated changes.

Determine the frame contract:

- Required buffer shape and data type.
- Logical-to-physical coordinate mapping.
- Brightness and color handling.
- Whether unchanged frames can reuse a cached buffer.
- Whether motion is driven by elapsed time, fixed ticks, or frame count.
- Parameter update and runtime-stat conventions.

For a preset-heavy request, also inspect preset discovery, schema validation,
dashboard metadata, and ignore rules. Establish the plugin schema before
parallelizing preset creation so every contributor targets one stable contract.

When working in `ledgrid-poc`, read [references/ledgrid-poc.md](references/ledgrid-poc.md).

## Design state separately from rendering

Keep these responsibilities distinct even if they remain in one class:

1. Advance simulation from a bounded time delta.
2. Make or refresh plans only when relevant state changes.
3. Resolve collisions, locks, clears, despawns, and resets.
4. Render the resulting state into a reused frame buffer.

Keep semantic simulation state separate from presentation state. Colors, cell
age, trails, interpolation, atmospheric backgrounds, and source-rate ticks may
explain or decorate evolution, but must not change the underlying rules unless
the user explicitly requests a new simulation. Verify this with paired runs
that use identical seeds and different visual settings, then compare their
logical states after multiple updates.

Use a dedicated seeded random generator when repeatable tests matter. Keep each entity’s mutable movement state independent, while sharing the minimum coordination state needed for intelligent group behavior.

Isolate wall-clock time, weather, sensor readings, and other external inputs
behind a small overridable method. Tests can then inject a fixed value without
mocking global modules, while production still reads the live source.

For multiple agents, particles, or game pieces:

- Order entities by expected resolution or lock time.
- Plan against a projected shared state and reserve each selected outcome before planning the next.
- Replan after material state changes, not every rendered frame.
- Keep physical movement collision-checked even when the destination was reserved.
- Bound look-ahead by the nearest few consequential entities; defer distant entities until they enter the horizon.
- Ensure steering speed scales with the display geometry so a valid plan is physically reachable.

## Design for physical masks and occlusion

Treat foliage, sculpture, bezels, seams, and other calibrated installation
features as semantic geometry, not merely a final black-pixel overlay. Preserve
distinct layers when they have different meanings—for example, soft foliage
occlusion versus solid globe obstacles—and let each animation choose behavior
appropriate to its visual or simulation model.

- Centralize mask loading, logical/flat coordinate conversion, overlap priority,
  clearance dilation, error reporting, and caching. Cache keys must include grid
  geometry, paths, and clearance radius so live changes cannot reuse stale masks.
- Expose obstacle, clearance, and safe-space views. Use exact obstacles for
  collisions and highlights; use dilated clearance for text, HUDs, destinations,
  spawn points, and other information that needs breathing room.
- Integrate at the semantic layer when possible: route agents around obstacles,
  exclude masked cells from cellular neighborhoods, relocate labels, reserve
  safe destinations, or bias placement toward visible areas. For effects that
  cannot route, minimize masked overlap and make the installation geometry
  legible with edges, halos, shadows, or intentional clipping.
- Define a deterministic fallback when no safe placement exists. Prefer the
  least-overlapping valid candidate over dropping useful content or entering an
  unbounded search.
- Keep the disabled path behaviorally and visually identical to the prior
  renderer. Guard simulation changes as well as paint changes behind the feature
  state, and test equality with the mode off.
- For normalized effects, treat an enabled strength of zero as an exact no-op
  unless the contract explicitly says otherwise. Test byte, logical-state, and
  RNG parity at both the canonical off state and zero strength.
- Gate geometry loading and behavior through the plugin's declared support, not
  merely through the presence of global state. Unsupported active effects must
  not fall through to a broad legacy mode or incur hidden geometry/render work.
- Separate exact contact geometry from clearance geometry. Exact cores own
  collisions, damage, illumination, and boundaries; dilated clearance owns
  planning, spawn, food, HUD, and routing pressure unless the animation clearly
  documents a different semantic model.
- Decide control authority explicitly. A global installation state should be
  applied by the manager to the active animation, every future start, generic
  live parameter updates, plain and parameterized previews, and any separate
  preview process. Presets may declare a preferred value, but must not silently
  override the operator's global selection. Validate at the manager/API boundary
  and persist canonical state through restarts or deployments.
- A live installation-state change invalidates relevant caches and future plans,
  but must not by itself reset the simulation, consume RNG, advance time, or
  trigger a semantic event. Recompute derived next-state data from the unchanged
  current state when rules change.
- If the operator control is global, show it beside other global controls and
  suppress duplicate per-animation controls. Still keep the parameter in plugin
  schemas so direct/headless construction remains valid and independently
  testable.
- Prefer one shared geometry/cache contract with animation-specific composition.
  A universal post-process is useful only as a last-resort fallback; it cannot
  preserve hidden information or create meaningful routing behavior.

## Detect and recover from loops deliberately

For autonomous simulations that can settle into cycles, monitor semantic state
at natural update boundaries rather than rendered frames.

- Fingerprint only the state that determines future evolution; exclude color,
  interpolation phase, background time, and other visual state.
- Pair a compact hash with a cheap invariant such as population, and retain a
  bounded dictionary plus eviction queue. If a false positive would be costly,
  confirm equality before taking a destructive action.
- Make recovery explicit: restart the authored seed, reseed a fresh world, or
  inject a bounded perturbation such as a glider storm. Reset loop history after
  recovery so the old cycle is not immediately rediscovered.
- Configure recovery by preset intent. Disable it for deliberate oscillators,
  guns, and loop installations; enable it for worlds expected to keep evolving.
- Test a known period-two cycle, disabled monitoring, each recovery policy, and
  strict history bounds.

For tiled deterministic installations, assign every active cell a region ID
and reject neighbors from other regions. Disable global wrapping in tiled mode,
reserve dead gutters when visual separation matters, and ensure background
rendering does not illuminate those gutters. Test expected population multiples
and a constructed cross-boundary neighbor case.

## Build visual families from orthogonal controls

When a request calls for many related options, prefer one renderer with a
small set of composable visual axes—such as face/geometry, background,
palette, motion, and information density—over many nearly identical plugins.

- Make each axis visibly consequential, not a synonym or palette-only variant.
- Keep option values explicit in the parameter schema so presets and the UI can validate them.
- Stabilize defaults, options, and bounds before delegating preset authoring.
- Partition delegated presets by user outcome (practical, atmospheric,
  experimental), assign non-overlapping filenames, and require schema-valid
  metadata.
- Include useful presets as well as showcase presets. For clocks, preserve
  legibility, 12/24-hour choice, seconds behavior, and documented timezone or
  offset semantics.
- Treat presets as shipped artifacts: ensure they are discoverable, tracked by
  version control, listed in the dashboard, and exercised by repository-wide
  preset tests.
- When changing a default across every preset, update deterministic preset/asset
  generators too. Regeneration-equality tests should compare generated payloads
  with committed JSON so the next asset refresh cannot silently undo the policy.
- Audit ignore behavior explicitly. A valid preset can remain invisible to
  `git status`; use the repository's ignore diagnostic and tracked-file listing
  before claiming that presets are check-in ready.

## Create pre-rendered pixel and GIF packs

Treat a pre-rendered pack as a tested animation family, not a folder of opaque
downloads.

- Inspect the player before sourcing or generating art: confirm asset discovery,
  frame timing, loop handling, orientation, coordinate flattening, fit/crop
  behavior, interpolation, transparency disposal, and whether all decoded frames
  remain resident in memory.
- Author at the physical canvas dimensions whenever practical. For pixel art,
  preserve hard edges with native-size output, integer scaling, or nearest-neighbor
  resampling; avoid silently feeding deliberate pixels through bilinear scaling.
- Design for the installed aspect ratio. On a very tall wall, use a native tall
  scene or repeat staggered motifs/bands across the height so `contain` does not
  create a tiny centerpiece and `cover` does not crop away the subject.
- Prefer a deterministic repository script for a large original pack. Keep motif,
  palette, timing, and preset metadata in one catalog so regeneration cannot drift.
  Distinguish AI concept art from shipped pixels and record whether assets are
  original, generated, licensed, or downloaded; do not ship ambiguous provenance.
- Keep loops LED-friendly: limited palettes, near-black backgrounds where
  appropriate, high-contrast silhouettes, modest frame counts, and timings slow
  enough to read from across the room. Measure both compressed disk size and
  decoded memory when the player preloads frames.
- Create one schema-valid preset per useful asset when users need one-click
  selection. Verify the preset names the exact file, uses pixel-safe fit settings,
  appears in discovery/UI, and is not accidentally excluded by ignore rules.
- Validate every file mechanically: expected dimensions, multiple frames, sensible
  per-frame durations, infinite-loop metadata when intended, and non-empty pixel
  deltas between frames. Then decode representative assets through the actual
  plugin, not only Pillow or an asset-preparation script.
- Build a contact sheet of first frames at the real aspect ratio and inspect a few
  complete loops. Check clipping, blur, blank frames, disposal artifacts, seam
  jumps, excessive repetition, low contrast, and whether every motif reads at
  target resolution.

## Protect the frame budget

Treat 200 FPS as a 5 ms total frame budget, not a 5 ms allowance for one plugin. Optimize the common frame first, then bound worst-case event spikes.

Prefer:

- Reused canonical buffers and cached geometry, palettes, masks, and static layers.
- NumPy/vectorized operations for dense pixels; compact Python loops for sparse entities.
- O(entity count) rendering and bounded planning independent of the configured maximum.
- Cached board metrics or spatial indexes instead of copying/rescanning the world for every candidate.
- O(piece cells) collision or landing calculations when column/surface data can replace full descent scans.
- Event-driven planning, incremental work, and capped spawn/simulation work per update.
- Schedule emitters, injections, damage ticks, and other semantic events at
  committed simulation boundaries rather than rendered frames. Manager FPS,
  cached-frame calls, and independently animated backgrounds must not multiply
  event rates.
- Cached unchanged frames with the host’s `changed=False` mechanism.
- End-to-end vectorization for dense layers, including brightness scaling,
  masks, and logical-to-physical layout conversion. A vectorized field followed
  by thousands of Python pixel writes can still dominate p95 latency.
- Shared allocation-free render primitives when two animations genuinely use
  the same phase-map, palette-lookup, masking, or cadence mechanism. Keep each
  animation's domain-specific composition and simulation separate.

Separate manager FPS from source FPS. A clock, sensor display, or slowly
changing ambient scene can render only when its semantic tick changes and
return the cached frame otherwise. Include every visual input in the cache key,
including live parameter changes; use a bounded source-rate tick for animated
backgrounds and a seconds/minutes key for static informational faces.

An independently animated background must be able to mark a frame changed
without advancing the simulation. Quantize it to its own bounded FPS, cache by
that tick, and verify both that sub-tick calls return `changed=False` and that
the next background tick changes pixels while logical state remains identical.

Avoid per-pixel Python object allocation, unbounded catch-up loops, planning all maximum-density entities together, and lowering FPS as the first response to inefficient code.

For large procedural-plugin sprints:

- Stabilize one family contract before parallel work: plugin-owned manifests,
  parameter axes, source cadence, preset tiers, test ownership, and non-overlapping
  helper modules. When discovery derives its allowlist from shipped manifests,
  new packages should not also edit a central registry.
- Analytic seeded phase fields are a strong fit for dense ambient atmospheres:
  they provide long, non-obvious evolution without mutable particle catch-up.
  Quantize presentation to source ticks and keep any semantic clock bounded.
- A fixed-step simulation's first call at a late elapsed time must not replay an
  unbounded backlog. Initialize at that time or cap catch-up, and advance through
  sequential semantic ticks in behavior tests and visual warmups.
- At a 200 Hz manager rate, a 30 Hz source should change about 15 percent of
  calls and a 40 Hz source about 20 percent. Report the observed changed ratio
  alongside latency so a fast benchmark cannot conceal an accidentally frozen
  animation or a missing cache.
- Keep logical geometry orientation explicit. Plant masks in `ledgrid-poc` are
  strip-major `(width, height)`, while image-style canvases are commonly
  `(height, width)`. Cache a named canvas-oriented view or transpose once at a
  documented boundary, then exercise modifier-on frames after a semantic tick.
- Apply brightness and other in-place transforms before flattening transposed or
  sliced arrays. Reshaping a non-contiguous view can return a copy and silently
  discard later in-place work.
- Presentation-only modifier branches must acquire the geometry they use rather
  than relying on another modifier branch to initialize a local variable.
  Guard geometry and RNG work on resolved strength greater than zero so active
  zero-strength modifiers retain exact parity cheaply.

Treat validated configuration as state, not work to repeat inside a hot loop.
Parse and canonicalize structured payloads at construction or the live-update
boundary, cache the resulting immutable value, and invalidate it only when a
relevant parameter changes. Collision, steering, particle, and per-pixel paths
should query that cached value rather than re-validating mappings. Add live
update tests so this optimization cannot make runtime controls stale.

If a visual parameter increases work, benchmark both its normal default and maximum supported value. Expose performance caps in runtime stats when that helps operators understand behavior.

## Validate behavior and visuals

Add focused tests that fail for the old behavior and describe the user’s goal rather than implementation trivia.

Cover as applicable:

- Plugin discovery and frame shape/type.
- Defaults, schema bounds, and live parameter updates.
- Independent movement state for concurrent entities.
- Shared planning produces complementary, non-duplicated outcomes.
- Collisions, line clears, resets, and manual overrides.
- High-density work remains bounded.
- Wide/tall hardware geometry does not break steering or placement.
- Render-rate caps return unchanged cached frames correctly.
- External inputs can be fixed deterministically and offsets/boundaries behave correctly.
- Every declared style/background option renders a visible canonical frame.
- Orthogonal options produce distinct frame fingerprints, not accidental duplicates.
- Every curated preset uses supported keys/options/bounds and renders successfully.
- Modifier-on curated presets render after at least one semantic update; a
  `t=0` smoke frame alone can miss orientation, emitter, and simulation-path
  failures.
- Known loops are detected from semantic state, recovery resets monitoring, and
  intentional loop presets remain untouched.
- Tiled simulations cannot exchange neighbors across region boundaries and dead
  gutters remain dead in both simulation and presentation.
- Pre-rendered assets have the expected dimensions, timing, loop metadata, visible
  frame deltas, provenance, and matching tracked presets.
- Mask-aware mode off reproduces the original frame and semantic evolution;
  mode on keeps critical information and entities out of clearance geometry,
  obeys obstacle routing/collision rules, and remains bounded when safe space is
  scarce or masks are missing/malformed.
- Global installation controls apply live, override preset/direct-start conflicts,
  survive saved-state round trips, propagate to every preview/update path, and
  have one visible authoritative UI control.

Run deterministic multi-seed simulations long enough for the requested emergent outcome. Report concrete results such as lines cleared, resets, surviving duration, or convergence—not “looks intelligent.” A unit test for one constructed board state and a longer simulation serve different purposes; use both when the behavior is emergent.

Benchmark with realistic dimensions, the actual manager call rate, source-rate
cadence, normal settings, and the worst supported density. Record mean, p95,
p99, maximum frame time, changed-frame ratio, and behavioral output. A passing
p95 can coexist with severe semantic-tick spikes, so report and investigate p99
and maximum event frames separately rather than treating the gate as the whole
performance story. Optimize and rerun if p95 approaches the frame budget or
event spikes are excessive.

For visual-only effects, render representative frames or short clips when tooling exists and inspect them. For a large preset family, build a labeled contact sheet covering every preset at the real aspect ratio; check legibility, clipping, blank outputs, unwanted repetition, and whether variants differ structurally. Do not substitute code review for visual verification.

Benchmark cached/default and animated/stress paths separately. A default clock
may look exceptionally cheap because repeated frames are correctly unchanged;
that number does not characterize aurora, particles, glow, maximum density, or
other continuously rendered variants.

When filtering test output through a pipeline, preserve the test runner's exit
status (for example with `pipefail`) or inspect the unfiltered result. A trailing
`tail`, `grep`, or formatter can exit successfully while the test process failed;
never report a suite as passing from the pipeline status alone.

## Communicate while iterating

Give short evidence-based updates at these moments:

- After discovering the current architecture or root cause.
- When a hardware or performance constraint changes the design.
- After a behavioral simulation exposes a gap between a good plan and executable motion.
- After final tests and benchmarks.

Lead the handoff with the outcome. Include changed files, behavior evidence, performance measurements with their environment, tests run, and any untouched unrelated worktree changes.
