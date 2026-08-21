# Animation plugins

Animations are allowlisted, self-contained Python packages discovered by the
animation manager. The web UI reads the same registry for names, descriptions,
parameters, presets, previews, and reload operations.

## Package contract

Each built-in animation owns one directory:

```text
animation/plugins/<plugin_id>/
├── __init__.py       # AnimationBase subclass and plugin-specific code
├── manifest.json     # stable registry metadata
├── presets/          # curated JSON presets
├── tests/            # focused unit and behavior tests
└── assets/           # optional files used only by this plugin
```

Only `__init__.py` and `manifest.json` are required. Framework and lifecycle
contracts live in `animation/core/` with tests under `animation/core/tests/`.
Reusable rendering or simulation primitives used by multiple plugins belong in
`animation/libraries/` with tests under `animation/libraries/tests/`.

The package directory and manifest `plugin_id` must agree, and the manifest's
`class` must name the package's one concrete animation class. `icon` is required;
`gallery` is either `show` or `test`. Built-in packages are discovered in sorted
`plugin_id` order. Flat `.py` plugins remain supported only for explicitly
configured external plugin directories.

Existing manifests without component fields retain the Python-background
compatibility default. Newly authored components declare `provider`, `role`,
`entrypoint`, and `cadence` together and set `manifest_version` to `1`. The
current host loader accepts Python
`background`, `overlay`, and compatibility `full_scene` roles and fails closed
when only part of that metadata is present. `clock_overlay` is the reference
explicit overlay; the existing `clock` remains the preset-compatible full scene.

Discovery first scans manifest JSON into versioned descriptors without importing
plugin implementations. The Python adapter then binds the allowlisted class,
classifies stateful implementations, and adds the validated parameter schema and
actual no-config defaults. The unified catalog is filterable by provider and
role. Painter, stateful animations, and legacy Clock remain catalog-visible
`full_scene` compatibility components with an explicit non-composable diagnostic.

Root `presets/animations/<plugin_id>/` is a user-writable runtime overlay.
Do not place curated source presets there.

## Minimal plugin

`animation/plugins/example/__init__.py`:

```python
import numpy as np

from animation.core.base import AnimationBase


class ExampleAnimation(AnimationBase):
    ANIMATION_NAME = "Example"
    ANIMATION_DESCRIPTION = "A static red frame."
    ANIMATION_AUTHOR = "LED Grid"
    ANIMATION_VERSION = "1.0"

    def generate_frame(self, time_elapsed: float, frame_count: int) -> np.ndarray:
        frame = self.next_frame_buffer(clear=False)
        frame[:] = (255, 0, 0)
        return frame
```

`animation/plugins/example/manifest.json`:

```json
{
  "plugin_id": "example",
  "class": "ExampleAnimation",
  "icon": "💡",
  "gallery": "show"
}
```

Checked-in manifests are the built-in allowlist. A directory without a valid
manifest is not loaded or exposed by the web API.

## Frame contract

`generate_frame(time_elapsed, frame_count)` returns either:

- a C-contiguous `numpy.uint8` array shaped `(controller.total_leds, 3)`; or
- `RenderedFrame(pixels, changed, dirty_ranges)` with presentation hints.

An explicit overlay instead returns `OverlayFrame`: a C-contiguous `uint8`
array shaped `(controller.total_leds, 4)` in premultiplied RGBA8, plus a
monotonic content revision and the same change/dirty hints. RGB channels may
not exceed alpha. Alpha zero is transparent; RGB zero with alpha 255 is opaque
black. The legacy single-animation start, list, and preview paths reject
overlays rather than interpreting RGBA as RGB.

Use `next_frame_buffer()` instead of allocating a fresh full-wall array on each
frame. Source-rate or event-driven plugins should return `changed=False` while
their image is unchanged. `dirty_ranges` may identify canonical flat-index
ranges for a controller that supports partial transfer.

Simulation state belongs to the plugin instance. Use elapsed time or a bounded
fixed timestep for motion; do not make behavior depend on web request timing.
Plugins must not call SPI or the web layer directly.

## Fixed host scenes

The host scene product supports exactly one Python RGB background plus the fixed
`clock_overlay` slot. `SceneState` version 1 persists the component provider,
resolved authored-parameter snapshot, overrides, optional component-preset
identity, placement, opacity, stale policy, and a known Python fallback. Vibe,
plant modifiers, master brightness, and operator tempo remain independent
top-level display state and are never captured by a scene preset.

The manager owns each component's lifecycle, elapsed time, frame counters,
targeted interactions, cached frames, placement, opacity, and the compositor.
Removing, disabling, moving, replacing, or updating the overlay does not restart
the background. Complete scene replacement, targeted component updates, status,
and preview use the same provider, role, composability, and parameter validation.
Existing single-animation starts translate to a background-only compatibility
scene; legacy APIs, presets, painter, and full-scene stateful animations retain
their existing product paths.

Composition uses canonical strip-major coordinates, integer strip/LED
translation with clipping, and fixed-point premultiplied source-over. The
compositor never mutates component buffers and rotates two RGB outputs so frame
N remains stable while frame N+1 is generated. Previous and new alpha coverage
are unioned for movement and clearing; unknown dirty coverage falls back to a
complete frame. The result is flattened to the existing authoritative RGB
transport, so firmware and receiver protocol behavior do not change.

Rendering order is component semantics, component-local palette/grade,
composition, universal plant optics once, then vibe luminance once. Receiver
master brightness remains last. `get_scene_preview()` uses the same composition
and presentation order without hardware I/O and accepts an isolated vibe,
plant-modifier state, and source time without mutating the live scene. The
shipped acceptance scenes pair `clock_overlay` with Gradient, Aurora Curtains,
and Sparkle.

The versioned web/IPC surface exposes the unified catalog, current scene,
validation, start/stop, targeted updates, preview, component presets, and scene
presets. Before-deploy and restart persistence use versioned desired display
state while still migrating legacy animation snapshots. Unsupported scene schema
or provider state selects the recorded Python fallback only after the complete
desired state has been validated.

## Parameters

Extend the base schema and read applied values from `self.params`:

```python
def get_parameter_schema(self):
    schema = super().get_parameter_schema()
    schema["density"] = {
        "type": "float",
        "min": 0.0,
        "max": 1.0,
        "default": 0.25,
        "description": "Fraction of active pixels",
    }
    return schema
```

Defaults must render a useful, bounded scene without network, sensor, or user
input. Keep parameter names stable because saved runtime presets refer to them.

Numeric controls may also declare named values with an optional `presets`
mapping. The names are presentation metadata: the selected value sent to the
animation and stored in an animation preset remains numeric, and operators can
still choose any value allowed by the control's range.

```python
schema["background_speed"] = {
    "type": "float",
    "min": 0.0,
    "max": 3.0,
    "default": 1.0,
    "presets": {"frozen": 0.0, "normal": 1.0, "lively": 2.0},
    "description": "Backdrop motion speed",
}
```

## Top-level vibe context

The manager owns one versioned vibe independently of the running animation and
selected preset. The stable IDs are `neutral`, `quiet`, `cozy`, `vivid`, and
`celebration`; the central profile registry resolves each ID to immutable
palette roles, tempo, luminance, and a deterministic profile digest. Vibe
selection survives controller restart and deploy-state restoration, is exposed
in status, and is passed explicitly to both plain and parameterized previews.
Changing it does not mutate authored parameters or dirty preset identity.

Every shipped plugin declares a `semantic`, `grade`, or `preserve` color policy
in its strict `vibe` manifest object. Capabilities remain the opt-in boundary for
actual palette, tempo, or luminance behavior, so a classification-only manifest
with `"capabilities": []` is an exact presentation no-op. For example:

```json
{
  "vibe": {
    "color_policy": "grade",
    "timing_adapter": "scaled_context",
    "capabilities": ["palette_roles", "tempo", "luminance"],
    "legacy_parameter_mappings": {
      "palette": {
        "quiet": "mono",
        "cozy": "ruby",
        "vivid": "candy",
        "celebration": "solar"
      }
    }
  }
}
```

Capabilities are `palette_roles`, `tempo`, and `luminance`. Color policy is
`semantic`, `grade`, or `preserve`: semantic plugins consume palette roles,
grade plugins accept the bounded framework grade, and preserve plugins retain
their source colors. Framework luminance is applied once after plant optics.
Legacy parameter mappings are ephemeral render inputs and may target only
declared schema parameters; they never rewrite an authored preset. `neutral`
must not declare a mapping and remains byte-compatible.

Timing is explicit. `legacy_speed_param` receives unscaled elapsed time and an
effective `speed`; `scaled_context` receives continuously scaled elapsed time
and a unit speed; `wall_clock` receives unscaled elapsed time and ignores vibe
tempo. Authored speed, vibe tempo, and the operator tempo multiplier are kept
separate and applied once. Managed plugins can inspect the immutable
`presentation_context`, `authored_params`, and per-render `effective_params`.
`on_presentation_context_changed(old, new)` may invalidate presentation caches
but must not reset, advance, reseed, or otherwise mutate semantic state.

## Composable plant modifiers

The manager owns one validated, versioned `plant_modifiers` state with active
modifier IDs and normalized strengths. It applies that state live and to every
future animation start and preview. Plugins declare supported semantics through
`PLANT_MODIFIER_SUPPORT`, then use `plant_modifier_enabled()` and
`plant_modifier_strength()`; unsupported active IDs are observable no-ops.

The fourteen stable IDs are `illuminate`, `shadow`, `refract`, `hue_shift`,
`liquid_glass`, `attractor`, `repulsor`, `slow_zone`, `obstacle`, `portal`,
`bumper`, `hazard`, `habitat`, and `emitter`. Hue Shift and Liquid Glass are
universal presentation modifiers applied by the framework after plugin rendering;
all other modifiers require explicit plugin support. At most one field modifier
and one surface modifier may be active.
The old `plant_aware` boolean remains only as a compatibility input and migrates
to Illuminate plus Obstacle; newly persisted global state uses the composable
shape.

The base schema also supplies `plant_clearance`, `plant_mask_path`, and
`plant_globe_mask_path`. Plugins load calibrated masks lazily through
`get_plant_masks()`, including per-layer edges, distance/normal fields, and the
seven ordered globe-region masks.

The manager may instead select a content-addressed installation profile from
the target-owned `installation_profile_library/`. A nonzero saved digest is
strictly resolved before controller construction or live selection; the
64-character all-zero digest keeps the legacy JSON mask path and does not touch
the library filesystem. The selected global 32x138 geometry is immutable and is
shared by reference through live, composed, receiver-foreground, and preview
presentation contexts. Status exposes the selected digest, revision, compact
view metadata, and the independently named topology fields. The ordinary
`get_plant_masks(radius)` override remains supported: it caches one immutable
clearance layer derived from the profile distance field and shares all other
managed arrays.

A live profile change is presentation-only. `AnimationBase` invalidates its
mask and framework-optics caches, and a plugin that projects or plans from
`get_plant_masks()` must refresh that derived cache from
`on_presentation_context_changed(old, new)` when installation-profile identity
changes. That hook may invalidate future routing/layout work, but must preserve
the plugin instance, authored parameters, current semantic state, elapsed-time
state, and RNG stream. Host profile selection remains independent of receiver
profile activation.

The default-off Phase 3C receiver path can stage and activate the same decoded
profile view without changing display ownership. A committed local-background
context preserves all fourteen resolved Q8.8 modifier strengths. When and only
when local-background ownership, a valid active profile, and nonzero
`hue_shift` coincide, firmware applies the generated signed-Q14 hue matrix to
exact foliage/globe pixels after sparse foreground composition. Profile
activation or restore invalidates one such in-flight presentation but preserves
the renderer, context, foreground generation, semantic time, and cadence.
Zero strength, no profile, the feature-off production build, and complete host
frames remain byte-exact compatibility paths. A separate allocation-free
18-class native geometry canary distinguishes empty, clearance, foliage,
regions 1-7, and obstacle edges without mutating the decoded profile; it is a
portable/read-only diagnostic and is not exposed as a wall scene.

Keep foliage, globes, their union, and clearance-expanded obstacles semantically
separate. Interactive simulations can use them for collision and routing;
visual effects can use them as masks or accent layers. An empty active state
must restore byte-identical pixels, semantic evolution, and RNG consumption.

Shared mask geometry belongs in `animation/libraries/`, not in individual
plugins.

## Presets and assets

Curated presets live at `animation/plugins/<plugin_id>/presets/*.json` and are
versioned with the code they configure. The registry merges them with runtime
presets from `presets/animations/<plugin_id>/`, with runtime files remaining
ignored until intentionally promoted into the plugin package.

An asset used by one plugin belongs in that plugin's `assets/` directory. An
asset used by several plugins may live under root `assets/` with a documented
owner and format.

## Tests and acceptance

Focused tests live beside the plugin. They should cover:

- manifest discovery and import;
- frame shape, dtype, contiguity, and bounds;
- deterministic state transitions for seeded simulations;
- meaningful parameter extremes;
- ordinary and plant-aware behavior when applicable;
- every curated preset loading and rendering successfully.

Overlay and scene changes additionally require golden fixed-point blend and
rounding vectors, previous/new dirty coverage, placement/clipping, cache and
lifecycle tests, preview/live equivalence, legacy-boundary rejection, and
installed-geometry scene benchmarks with changed and transport-volume metrics.

Run the repository checks before exposing a plugin:

```bash
just test
just test-rendering
```

The rendering benchmark is the authoritative performance gate for the installed
32 x 138 geometry. Its standard gate includes the three accepted background +
clock scenes and reports p50/p95/p99/max separately; desktop results are portable
evidence, not Raspberry Pi or physical-wall timing evidence.

## Runtime boundaries

The controller process owns plugin instances and hardware presentation. The web
process writes commands through `ipc/control_channel.py` and reads status and
preview frames from the same channel. Hot reload is suitable for local plugin
iteration, but production changes should go through the normal deploy and
acceptance flow.

## Receiver boundary and planned evolution

This document describes the Python host-rendered plugin, catalog, scene, and
receiver-hybrid product contracts. Phase 3A of the
[unified roadmap](plan-revamped-animation-pipeline.md) provides explicit
receiver ownership, status v3, a staged host-authoritative presentation context,
and a statically linked rainbow. Phase 3B adds negotiated status v4, bounded
premultiplied-RGBA foreground state, sparse four-board host orchestration,
leases, scheduled commit, fixed-point receiver composition, manager lifecycle,
desired-scene persistence, and complete host RGB takeover.

The default/absent rollout config still selects the feature-off production
firmware and Python full-frame path. The installed wall currently opts into the
named feature-on canary image through the explicit
`degraded_spi1_01_readable` target-owned policy. Readable logical receivers 0/1
require exact v4 acknowledgements; write-only 2/3 receive paced serialized
commands and remain acknowledgement-, integrity-, and timing-unverified despite
camera-visible output. Runtime status must therefore remain
`operational=true`, `degraded=true`,
`telemetry_complete=false`, `healthy=false`, and
`release_acceptance=false`. This explicit product mode does not close the strict
all-four hardware gates.

The installed hybrid topology has four independent coordinate facts: fixed SPI
route/logical identity, physical lane permutation, host-frame/sparse strip
direction, and receiver-native procedural direction. The target-owned config is
authoritative. As camera-verified on 2026-08-14 it records physical order
`(0,1,3,2)` plus host and native reversal maps
`(false,false,true,true)`. Host transforms must not be reused as proof of native
orientation: verify boundary-crossing foreground and a signed native phase field
separately. See [Hardware](HARDWARE.md#installed-lane-and-strip-orientation) for
the exact installed mapping and diagnostic sequence.

The follow-on Phase 3B0 slice adds capability-gated sparse batch command `0x35`.
Its 28-byte fixed header carries the session, generation, and logical span
count; each sorted span adds `start:u16`, `count:u16`, and premultiplied RGBA.
`OVERLAY_BEGIN.expected_patches` and status-v4 `accepted_patches` count spans,
not packets. One CRC and one queued-response command/result proof therefore
cover all spans in a batch, while receivers without capability bit `1<<5`
continue using the original `0x31` packets. The exact wire layout, 4,096-byte
capacity rule, retry semantics, and malformed vectors are frozen in
[ANIMATION_PIPELINE_CONTRACT_V1.md](ANIMATION_PIPELINE_CONTRACT_V1.md).
On the frozen 32×138 clock trace this changes the representative patch payload
from 1,812 to 952 bytes (7.1795% of a 13,260-byte wall frame) and the complete
60-second SPI trace from 6,413,426 to 2,468,816 clocked bytes (94.8282% saved
versus 47,736,000 dense bytes). The accounting also reports 4,937,632 aggregate
MOSI+MISO endpoint bytes; SPI responses share command/query clocks rather than
adding a second transfer.

Receiver playback is not an `AnimationBase`, does not weaken the Python manifest
allowlist, and does not introduce another catalog. The Pi continues to resolve
vibe and plant-modifier presentation state and transmits exact fixed-point
values and digests. Phase 3A/3B0 does not perform local profile lookup or
receive calibrated plant geometry; the separate default-off Phase 3C profile
transaction stages a Pi-authoritative, content-addressed profile for
receiver-safe optics.

The `native-animations` branch remains an organ donor for the roadmap's later
receiver phases. Reuse its ABI/build validation, host preview harness, ESP-IDF
loader baseline, content-addressed cache, typed parameters, status, quarantine,
and four-board failure tests. Do not carry forward its separate native catalog,
signed package envelope, frame-track backend, separate gallery, or exclusive
Python-versus-firmware lifecycle. The roadmap's donor table is authoritative for
commit/path mapping and replacement contracts.
