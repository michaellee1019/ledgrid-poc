# Breakout branch continuation: Space Invaders

This branch was merged with `main` on 2026-08-08 to keep its one distinct,
recoverable idea without reviving obsolete application code. The preserved
source is [`space_invaders_legacy.py`](space_invaders_legacy.py). It is an exact
copy of the file introduced by commit `1b463bf` (Git blob
`fd74bd2bbc9bcc856aa762f5187e3192d1b7ef4a`) and is intentionally outside
`animation/plugins/`, so it is not discovered as a shipped animation.

## What remains valuable

The legacy source contains a complete autoplaying Space Invaders behavior
prototype:

- a marching, edge-reversing formation that descends and advances levels;
- an autonomous player that tracks invader columns and fires when aligned;
- independent player and invader bullets, destructible shields, collisions,
  explosions, game-over flashes, and automatic resets;
- geometry-derived formation, shield, and speed settings rather than a single
  fixed-size recording; and
- live controls for global, formation, player, and bullet speed, fire rates,
  and shield durability.

These simulation rules are the branch's useful payload. Preserve their visible
behavior during a port, then improve deterministic AI or presentation in
separate changes with focused tests.

## What is obsolete or superseded

- The branch's flat `conway_life.py` and its color-only follow-up commits are
  superseded by `main`'s colocated `animation/plugins/conway_life/` package,
  manifests, curated presets, loop handling, tests, and plant-aware behavior.
- The branch's manager allowlist edit is obsolete. Checked-in package manifests
  are now the manager's source of truth.
- The branch's dashboard template, slider, target-FPS, Justfile, and
  `tools/dev/iterate.sh` changes predate the extracted dashboard assets and the
  current deployment/development workflows. The merged tree keeps `main` for
  all of them.
- The legacy file is an archive and not a supported external plugin. Do not move
  it back to `animation/plugins/space_invaders.py` as a shortcut.

## Current architecture mismatches

The prototype returns a newly allocated Python list of RGB tuples. Current
plugins reuse NumPy `uint8` buffers and return canonical `(total_leds, 3)`
frames, optionally wrapped in `RenderedFrame`. The prototype also renders with
per-cell Python writes, applies brightness per pixel, uses an unseeded random
generator, and permits unbounded movement/bullet catch-up loops after a long
stall.

The installed layout is 32 strips by 138 LEDs. The prototype derives its size
from the controller but deliberately reserves strip 0 and vertically reverses
each strip itself. That choice needs an explicit visual-parity decision during
the port; current canvas-based game plugins perform one documented
canvas-to-strip-major conversion at the render boundary.

Shipped animations are colocated packages. A complete Space Invaders plugin
must own `__init__.py`, `manifest.json`, `tests/`, and curated `presets/` under
`animation/plugins/space_invaders/`. The current manager derives its allowlist
from those manifests, curated preset validation requires schema-valid plugin
parameters, and source-rate plugins should return cached frames with
`changed=False`. The prototype has no deterministic behavior tests, runtime
stats, render-rate cap, plant-mask semantics, preset metadata, or performance
measurements.

## Recommended port, in order

1. Create `animation/plugins/space_invaders/` with `__init__.py`, a manifest
   naming `SpaceInvadersAnimation` and icon `👾`, a `tests/` package, and a
   `presets/` directory. Let the manifest make the plugin discoverable; do not
   edit a central allowlist.
2. Port the semantic state and update methods from the archived source before
   changing the rules. Add a schema-backed `seed` and a dedicated seeded RNG.
   Cap elapsed-time deltas after stalls and cap or replace each catch-up loop so
   work per manager call stays bounded.
3. Render into a reusable `(height, width, 3)` NumPy canvas and copy it once into
   `next_frame_buffer()`. Follow `pinball` or `maze_chase` for the explicit
   bottom-to-top, strip-major mapping, `apply_brightness_array()`, a bounded
   `render_fps`, and `rendered_frame(..., changed=False)` cache reuse. Initially
   preserve the blank first-strip convention for visual parity; decide whether
   to reclaim it only after capturing a comparison frame.
4. Preserve live updates for the seven legacy controls and add runtime stats for
   level, invaders, bullets, shields, resets, and player hits. Ensure a live
   parameter update refreshes derived rates without resetting the match or
   consuming RNG.
5. Add focused tests for manifest discovery; canonical shape/type on 32×138
   and smaller layouts; seeded state parity; invader edge/drop behavior; player
   alignment and firing; player/invader/shield collisions; level progression;
   game-over reset; bounded stall recovery; cached-frame cadence; and live
   parameter updates. Add a longer deterministic simulation that reports levels
   cleared and resets, not only smoke frames.
6. Add at least three curated presets such as classic attract mode, fast swarm,
   and shieldless siege. Keep every value inside the plugin schema, include the
   repository's required preset metadata and `plant_aware: true`, confirm files
   are tracked, and render representative warmed frames at 32×138 for visual
   inspection.
7. Treat plant interaction as a separate follow-up. Framework visual modifiers
   can remain available without claiming semantic support. If gameplay becomes
   plant-aware, use shared exact obstacle geometry for collisions and clearance
   geometry for formation, shield, player, and spawn planning; cover modifier
   off/zero parity before shipping it.
8. Run the focused plugin test, curated-preset test, full Python suite, and
   `tools/benchmarks/animation_render.py` at the real manager cadence. Record
   mean, p95, p99, maximum, changed-frame ratio, and multi-seed gameplay results.

## Checks run for this preservation merge

- `uv run --with numpy --with pillow --with flask --with
  'werkzeug>=2.0.0' --with opencv-python-headless python -m unittest
  animation.core.tests.test_plugin_registry
  tests.unit.test_curated_animation_presets -v`: **15 tests passed**. This
  confirms package discovery, all shipped plugins on 32×138, and all current
  curated presets after excluding the archived flat source.
- A Python compile-only syntax check of `space_invaders_legacy.py`: **passed**.
- `git diff --check`: **passed**.
- The archived file's Git blob was compared with
  `1b463bf:animation/plugins/space_invaders.py`: **identical**.

Known gaps are intentional: the legacy Space Invaders class was not loaded or
rendered against current `AnimationBase`, no gameplay simulation or benchmark
was run for it, no preview/contact sheet was produced, and no hardware was
operated. Those checks belong to the package port described above.
