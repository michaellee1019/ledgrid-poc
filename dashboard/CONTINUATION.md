# Dashboard animation continuation

## Why this branch exists

This branch preserves an extensible information-dashboard experiment for the LED
wall. Its useful distinction from both the web control dashboard and the shipped
`clock` animation is the widget/scene direction: a clock is the first widget, with
weather, transit, cached external data, and responsive composition intended to
follow. It is not a replacement for the browser control UI.

The historical architecture and phased design remain in [`plan.md`](plan.md).

## Implemented MVP

- A top-level `dashboard/` package separates layout, scene, rendering, bitmap
  text, fonts, and widgets.
- `ClockWidget` supports 12/24-hour display, optional seconds, two bitmap font
  sizes, compact fitting on the installed 32-column wall, clipping, centering,
  color, and safe margins.
- `DashboardScene` owns widget state and composition; `FrameBuffer` owns logical
  `(strip, led)` drawing and flat-frame conversion.
- `dashboard.plugin.DashboardAnimationPlugin` exposes the legacy
  `AnimationBase` lifecycle and once-per-second clock updates.
- `animation/dashboard.py` is retained as a legacy compatibility shim.
- `tests/unit/test_dashboard_clock.py` covers the narrow-wall fitting behavior.

After merging current `main`, the obsolete flat discovery entrypoint
`animation/plugins/dashboard.py` was intentionally removed. The current loader
assigned it the module name `dashboard`, shadowing the real top-level package and
breaking repository-wide plugin discovery. Therefore this code is preserved but
is deliberately **not registered as a shipped animation** yet.

## Current architecture drift

Do not restore the old central allow-list or flat plugin file. Current shipped
animations are colocated packages:

```text
animation/plugins/dashboard/
  __init__.py
  manifest.json
  tests/
  presets/
```

The package and manifest are the discovery contract; `AnimationManager` derives
its allow-list from them. Use `animation/plugins/clock/` as the nearest example.

The runtime also needs a focused port to current rendering contracts:

- Return a C-contiguous `numpy.uint8` array shaped `(total_leds, 3)`, normally
  obtained from `next_frame_buffer()`, instead of allocating a Python list of RGB
  tuples through `FrameBuffer.to_frame()` on every render.
- Isolate wall-clock acquisition behind an overridable method for deterministic
  tests. Cache the last rendered semantic second/minute and return
  `rendered_frame(cached_frame, changed=False)` between meaningful updates.
- Apply brightness with the base array helpers before flattening/transposing.
- Declare exact `PLANT_MODIFIER_SUPPORT`. Use the shared plant geometry/cache and
  current `plant_modifiers` state; do not add new behavior behind legacy
  `plant_aware`. Informational widgets should choose safe placement using
  clearance geometry, with deterministic least-overlap fallback when necessary.
- Move plugin-focused tests beside the package and add a valid manifest icon and
  preview metadata. Keep reusable dashboard-domain modules top-level only if the
  package imports them without creating a discovery-name collision.

## Not production-ready

- The merged branch does not expose `dashboard` in animation discovery or the
  browser gallery.
- The plugin still allocates list-based frames and does not use `RenderedFrame`
  unchanged-frame signaling.
- No plant-aware placement, modifier support declaration, curated presets,
  plugin manifest, preview metadata, or plugin-package tests exist.
- The clock reads local process time directly. Timezone/offset behavior is not
  defined, and rollover behavior is not deterministically tested.
- Only clock fitting is tested. Text clipping, frame orientation, brightness,
  live parameter updates, scene layout, and 32-by-138 render output are not.
- Weather/transit providers, caching, scheduling, retries, stale-data display,
  and configuration are design only.
- No visual contact sheet, realistic render benchmark, Raspberry Pi measurement,
  or photographed wall acceptance has been performed.
- `main` now has a richer shipped `clock` animation. Avoid duplicating its mature
  faces/backgrounds; decide whether the dashboard composes or shares its text/time
  primitives before expanding the clock MVP.

## Exact next steps

1. Create `animation/plugins/dashboard/` with one concrete class in
   `__init__.py`, a valid `manifest.json`, and colocated `tests/`; leave
   `AnimationManager` and `AnimationPluginLoader` unchanged.
2. Port `FrameBuffer`/plugin output to reusable NumPy storage and return
   `RenderedFrame(changed=False)` within an unchanged semantic tick. Add a fixed
   clock source and tests for second/minute rollover and live parameter updates.
3. Add plugin discovery, manifest, canonical shape/dtype/contiguity, 32-by-138
   orientation, brightness, and cached-frame tests. Remove `animation/dashboard.py`
   only after imports and presets no longer need the legacy shim.
4. Define dashboard plant semantics. At minimum, place clock text outside shared
   clearance geometry with bounded least-overlap fallback; test modifier-off and
   zero-strength parity plus a modifier-on frame after a semantic tick.
5. Add one practical curated preset and preview metadata, render/inspect a real
   aspect-ratio preview, then benchmark default and worst supported settings.
6. Only after the clock slice passes should provider/cache/scheduler work begin.
   Keep all network I/O outside `generate_frame()` and specify timezone, timeout,
   retry, and stale-data behavior in tests.

## Validation commands

Current preserved MVP:

```bash
uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' \
  python -m unittest tests.unit.test_dashboard_clock -v
```

After restoring manifest-backed discovery:

```bash
uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' \
  python -m unittest animation.plugins.dashboard.tests.test_dashboard -v

uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' \
  python -m unittest animation.core.tests.test_plugin_registry \
  tests.unit.test_curated_animation_presets -v

uv run --with numpy --with pillow tools/benchmarks/animation_render.py \
  --plugin dashboard --frames 100 --check --max-p95-ms 4.0 --json

uv run --with numpy --with pillow --with flask --with 'werkzeug>=2.0.0' \
  --with opencv-python-headless python -m unittest discover -s tests -p 'test_*.py'
```

Desktop benchmark results demonstrate relative render cost only; they are not
Raspberry Pi performance evidence. Hardware or live-wall validation is outside
this continuation merge.
