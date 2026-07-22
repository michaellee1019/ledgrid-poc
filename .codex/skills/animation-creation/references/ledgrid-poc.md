# `ledgrid-poc` animation reference

Read this reference only when working in the `ledgrid-poc` repository.

## Relevant paths

- `animation/core/base.py`: frame buffers, brightness, `RenderedFrame`, and base parameters.
- `animation/core/manager.py`: allowed plugins and preview controller.
- `animation/core/plant_awareness.py`: validated `PlantModifierState`, cached
  foliage/globe semantic geometry, clearance, edges, distance/normals, and named
  globe-region masks.
- `animation/plugins/`: concrete effects; `README.md` contains plugin rules.
- `presets/animations/<plugin>/`: curated disk-backed parameter sets.
- `tests/unit/`: focused plugin and frame-pipeline tests.
- `tools/benchmarks/animation_render.py`: headless latency/allocation benchmark.
- `tools/benchmarks/live_animation_sweep.py`: live plugin integrity sweep.
- `docs/ANIMATION_SYSTEM.md`: architecture and creation overview.
- `docs/RENDERING_PIPELINE_ACCEPTANCE.md`: rendering acceptance details.
- `Justfile`: canonical test, benchmark, live sweep, and deployment commands.

## Repository contracts

- Render into `next_frame_buffer()` rather than allocating a fresh frame.
- Return a canonical C-contiguous `(total_leds, 3)` `numpy.uint8` buffer or the repository’s `RenderedFrame` wrapper.
- Drive motion from `time_elapsed`; cap simulation delta after stalls.
- Use `rendered_frame(cached_frame, changed=False)` when source-rate throttling reuses a frame.
- Apply brightness through base helpers and preserve the repository’s strip/LED mapping.
- Avoid direct hardware calls from plugins.
- Add a plugin to the manager allowlist if discovery requires it.
- Add its dashboard icon/metadata when the web UI maintains a plugin map.
- Check `.gitignore`: curated preset directories may require explicit
  unignore rules before new JSON files appear in `git status`.
- Confirm check-in eligibility with `git check-ignore -v <preset>` and confirm
  the final curated set with `git ls-files 'presets/animations/*/*.json'`.
  Force-adding a chosen preset is acceptable when runtime presets are ignored by
  policy; once tracked, later edits remain visible normally.
- Keep preset parameters inside the plugin schema. The curated-preset test
  validates filenames/IDs, option values, numeric bounds, frame shape, and
  renderability across every shipped JSON file.
- Direct/headless construction defaults to an empty `plant_modifiers` state.
  The manager's global `PlantModifierState` is authoritative for managed starts,
  generic live updates, previews, and deployment persistence, and overrides
  conflicting preset values. The old `plant_aware` boolean is compatibility
  input only; do not add new behavior behind it.
- Declare exact support with `PLANT_MODIFIER_SUPPORT`; use
  `plant_modifier_enabled()` and `plant_modifier_strength()` so unsupported
  active modifiers remain no-ops. Obtain cached logical/flat foliage, globe,
  obstacle, clearance, safe, edge, distance/normal, and named globe-region views
  through the shared helper rather than re-reading calibration JSON or
  reimplementing coordinate mapping in each plugin.
- Keep exact cores and clearance distinct. Use exact geometry for contact and
  hazard semantics, clearance for planning/spawn/routing, and the stable
  `GLOBE_REGION_ORDER` for portal topology.
- Modifier-only live updates may invalidate caches and recompute derived plans,
  but must not reset semantic state, consume RNG, advance a tick, or emit an
  event. A supported modifier at strength zero and an unsupported modifier both
  require exact parity coverage.
- Treat foliage as soft/occluding and globes as solid landmarks unless an
  animation has a documented reason to reinterpret them. Route, place, or reserve
  against clearance geometry when possible; use intentional edge/highlight
  treatment when meaningful routing is impossible.
- `PlantMaskGeometry` arrays use canonical strip-major `(width, height)` layout.
  Image-style simulation canvases often use `(height, width)`; expose or cache an
  explicitly named transposed view and test modifier-on paths after a semantic
  tick instead of assuming NumPy boolean indexing will reveal the mismatch at
  construction time.
- A preset may temporarily retain `plant_aware: true` for the curated-preset
  compatibility contract while also carrying an explicit non-empty
  `plant_modifiers` recommendation. The explicit state wins over the legacy
  illuminate-plus-obstacle translation; do not implement new behavior behind
  the boolean.

## Preset-family workflow

For an animation with many presets:

1. Implement and smoke-render the plugin, registry entry, schema, and UI metadata.
2. Exercise the cross-product of declared geometry/background options on the
   deployed 32-by-138 layout before authoring presets.
3. If preset work is delegated, give contributors disjoint outcome categories
   and filenames, and prohibit plugin/schema edits after delegation begins.
4. Render all finished presets into a labeled contact sheet at the wall's true
   tall aspect ratio and inspect it. Warm fixed-step scenes through sequential
   source or semantic ticks before capture; a single late-time call is an invalid
   sample for simulations that correctly cap first-call catch-up.
5. Run `tests.unit.test_curated_animation_presets`, the focused plugin test,
   the full suite, and both default and animated/stress benchmarks.

When changing a parameter across the whole curated library, update every
deterministic preset generator (notably `scripts/generate_cute_gif_pack.py`) and
retain regeneration-equality coverage. Validate the explicit policy across all
curated JSON, then render every preset through its real plugin.

## Preset persistence and deployment

- Treat presets saved through the web UI as runtime data until explicitly
  curated. Fetch with ignore-existing semantics so local authored files are not
  overwritten, and exclude automatic snapshots such as `before-deploy.json`.
- Deploy curated presets from Git's tracked file list rather than rsyncing the
  entire runtime preset tree. This keeps manually saved controller presets local
  and prevents unrelated runtime JSON from becoming release artifacts.
- Support both Unix and ISO-8601 timestamps when runtime and curated presets are
  listed together; normalize only for sorting and preserve the stored value.
- After adding presets, validate both filesystem discovery and Git tracking.
  Passing schema/render tests does not prove that an ignored JSON file will be
  committed or deployed.

## Validation commands

Run the focused test first:

```bash
uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' python -m unittest tests.unit.test_<plugin> -v
```

Run all Python tests before handoff:

```bash
uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' --with opencv-python-headless python -m unittest discover -s tests -p 'test_*.py'
```

If output is filtered, enable shell `pipefail` or capture the Python process's
status directly. Do not trust the exit code from a final `tail`/`rg` process.

Run the standard rendering acceptance benchmark:

```bash
uv run --with numpy --with pillow tools/benchmarks/animation_render.py --frames 100 --check --max-p95-ms 4.0 --json
```

The benchmark’s 4 ms plugin p95 gate preserves headroom inside the 5 ms period at
200 FPS. For configuration-sensitive effects, also make a targeted benchmark
using the deployed 32-strip by 138-LED geometry, the actual 200 Hz manager call
cadence, maximum effect strength, and maximum supported entity count. Retain p99
and maximum semantic-event frames even when the p95 gate passes.

Use `just live-animation-sweep` only when the live controller is intentionally in scope. Do not deploy or operate physical hardware for a code-only request without the user requesting that external state change.

## Performance interpretation

Measure the default and stress configurations separately. A fast mean can hide expensive spawn, lock, clear, or replan frames, so retain p95, p99, and maximum timings. Desktop results demonstrate relative code cost and acceptance-gate compliance; they do not prove Raspberry Pi latency. Prefer an algorithm whose per-frame work is structurally bounded before considering a lower render rate.
