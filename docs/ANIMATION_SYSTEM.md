# Animation plugins

Animations are allowlisted, self-contained Python packages discovered by the
animation manager. The web UI reads the same registry for names, descriptions,
parameters, presets, previews, and reload operations.

## Installed geometry and receiver topology

The finalized wall is 33×138. Five logical receivers own widths
`(8,8,8,8,1)`; their physical left-to-right order is `(0,1,2,3,4)` and native
global offsets by logical ID are `(0,8,16,24,32)`. Runtime, preview, native
build, profile, persistence, and acceptance code must consume explicit topology
rather than infer `logical_id * 8` or assume every receiver owns eight semantic
strips. Historical 32×138 fixtures and measurements remain useful only at the
geometry they name.

Transport route, physical order, host strip direction, native strip direction,
logical width, and physical output-lane mask are independent configuration
domains. The one-strip fifth receiver is therefore not represented as an
eight-strip component merely because its ESP32 output driver has eight lane
positions.

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
role. Stateful animations and legacy Clock remain catalog-visible `full_scene`
compatibility components with an explicit non-composable diagnostic. Composer
is their only browser surface; catalog visibility never supplies an executable
browser route.

Root `presets/animations/<plugin_id>/` is a user-writable runtime overlay.
Do not place curated source presets there.

### Receiver-native source packages (Phase 3D)

A repository-built receiver background is a peer in the same component catalog,
not a Python plugin. Its package has no `__init__.py`, is never placed in the
Python manager allowlist, and cannot be started, reloaded, or preview-constructed
through `AnimationBase`:

```text
animation/plugins/<plugin_id>/
├── manifest.json
├── native/
│   └── background.cpp
└── presets/              # optional curated presets
```

The repository manifest is strict and complete. It uses descriptor version 1,
the reserved ABI-v2 entrypoint, one fixed-FPS receiver-native background, a
manifest-owned parameter schema, a host-build preview declaration, and the exact
tracked source path:

```json
{
  "manifest_version": 1,
  "plugin_id": "example_native",
  "name": "Example Native",
  "description": "Analytic receiver-rendered background",
  "icon": "🌌",
  "gallery": "show",
  "provider": "receiver_native",
  "role": "background",
  "entrypoint": "ledgrid.native-background-abi:2",
  "cadence": {"mode": "fixed_fps", "preferred_fps": 60},
  "parameter_schema": {
    "speed": {
      "type": "float",
      "min": 0.1,
      "max": 4.0,
      "default": 1.0,
      "description": "Motion multiplier"
    }
  },
  "vibe": {
    "color_policy": "semantic",
    "timing_adapter": "scaled_context",
    "capabilities": ["palette_roles", "tempo", "luminance"],
    "semantic_roles": ["background_low", "background_mid", "background_high"]
  },
  "installation_profile_requirements": [],
  "preview": {
    "kind": "native_host_build",
    "capture_seconds": [0, 0.5, 1, 2],
    "simulation_fps": 60,
    "framebuffer_readback": false
  },
  "build": {
    "artifact_kind": "receiver_native_module",
    "bundle_schema": "ledgrid.native-background-bundle",
    "bundle_version": 1,
    "abi_schema": "ledgrid.native-background-abi",
    "abi_version": 2,
    "target": "esp32-s3",
    "source": "native/background.cpp"
  }
}
```

Every parameter definition requires a non-empty description and default.
Numeric definitions require finite bounds; integers fit signed 32-bit values;
option-backed strings have unique bounded options. Scene-global state such as
vibe and plant modifiers is not a component parameter. Descriptor `defaults`
are derived from the schema, so repository manifests cannot carry a second
drift-prone defaults object. Unknown fields, path traversal, wrong
ABI/target/cadence, or malformed defaults fail descriptor discovery. Runtime
app releases deliberately omit native source bytes, so source existence,
regular-file/symlink safety, package confinement, and Git tracking are enforced
by the explicit native builder before either toolchain runs.

Curated native presets use the ordinary plugin-owned preset envelope:

```json
{
  "version": 2,
  "preset_id": "quiet",
  "name": "Quiet",
  "animation": "example_native",
  "category": "Ambient",
  "description": "A dim, slow native background.",
  "tags": ["ambient", "native"],
  "created_at": "2026-08-21T00:00:00Z",
  "updated_at": "2026-08-21T00:00:00Z",
  "params": {"speed": 0.65}
}
```

Preset discovery resolves the component package directory independently from a
Python implementation. The component and preset APIs expose the descriptor and
curated presets; interactive rendering happens locally in Composer. Python
animation APIs continue to return “not found” for the native ID.

Phase 3D established deterministic planning, host/target build, validation,
preview, and atomic publication. Use `just native-plan <plugin_id>`,
`just native-build <plugin_id>`, and `just native-publish <plugin_id-or-bundle>`;
none of those operations changes display ownership or flashes firmware. Phase 4
adds explicit `just native-install` plus managed scene selection, persistence,
and recovery. Activation is authorized only through Composer Check and guarded
activation. The retired `native-start` and `native-run` compatibility commands
fail before target access or partial build/publication/install work. Runtime
selection remains gated by the target-owned schema-v3 rollout; feature-off
production continues to reject it and stream complete Python frames. A native
component may remain catalog-visible and previewable while runtime selection is
gated.

Install and activation operate on one exact managed bundle/payload binding across
all five receivers. A callback/watchdog failure records the payload quarantine and
falls back without automatic retry. An operator must clear that exact quarantine
through the digest-bound API before reinstalling; the separate receiver-native
recovery action proves complete host takeover and restores the scene's recorded
Python fallback before manager state is cleared.

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
scene at the persistence boundary. Compatibility data never restores a browser
route or a second browser product; Composer owns catalog, draft, Check, and
guarded activation workflows.

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
with `"capabilities": []` is an exact presentation no-op. The compatibility
schema still accepts the following legacy shape, although no shipped plugin now
uses a palette mapping:

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

The generated catalog is the inventory authority; do not copy its component
counts into product documentation. The grade holdouts retain authored
full-spectrum, lineage, seasonal, or game-state colors whose replacement with a
small global role set would make their controls and presets dishonest. They
claim no vibe capability and remain presentation no-ops until a future
renderer-specific semantic design is proven.

Composer presents these descriptors in one provider-qualified catalog.
Provider, role, scene compatibility, and preview provenance are separate facts:
a receiver-native source may be visible as **Catalog / build only** without
becoming scene-selectable. Host-build previews are explicitly labeled as host
simulation rather than current wall output or receiver framebuffer readback.
ID-only preset/preview decoration fails closed when two providers declare the
same plugin ID. The historical 32 x 138 comparison is checked in as
[`phase2d-semantic-vibe-contact-sheet.png`](phase2d-semantic-vibe-contact-sheet.png).
It records the geometry on which that Phase 2D evidence ran and is not a current
33 x 138 physical-wall acceptance artifact.

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
the library filesystem. The selected global 33x138 geometry is immutable and is
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
33 x 138 geometry. Its standard gate includes the three accepted background +
clock scenes and reports p50/p95/p99/max separately; desktop results are portable
evidence, not Raspberry Pi or physical-wall timing evidence.

## Browser preset composer

`/composer` is an installable, private authoring surface. It loads a
provider-qualified catalog and full preset parameter envelopes once, then keeps
draft editing, rendering, checking, comparison, autosave, and JSON export in the
browser. Opening the composer does not read live wall status, start an animation,
or call the server preview endpoints.

Browser execution is capability-gated rather than inferred from catalog
visibility:

- verified Host Python components run unchanged in a Web Worker through a pinned
  Pyodide/CPython WebAssembly runtime and a deterministic repository source
  bundle; the same checked-in foliage and seven-globe calibration maps are
  bundled so plant-aware curated presets do not silently lose their geometry;
- verified receiver-native components use a separate Emscripten build of the
  same repository C++ source and ABI-v2 callbacks used by the host preview;
- components without a verified adapter state explicitly that interactive
  browser rendering is unavailable; no server-rendered fallback asset is
  published.

Both workers return canonical strip-major RGB bytes. The UI alone maps logical
LED zero to the bottom of the 33 x 138 display. Browser previews remain authored
simulations: they are never presented as receiver framebuffer readback or proof
of physical output.

The checker samples frames locally and reports schema errors, motion, luminance,
channel clipping, temporal deltas, estimated current, and browser render-time
percentiles. These measurements characterize the current browser and do not
replace the host rendering gate, Raspberry Pi measurements, receiver timing, or
photographed wall acceptance.

## Runtime boundaries

The controller process owns plugin instances and hardware presentation. The web
process writes commands through `ipc/control_channel.py` and reads status and
preview frames from the same channel. Hot reload is suitable for local plugin
iteration, but production changes should go through the normal deploy and
acceptance flow.

## Receiver boundary and gated Phase 4 evolution

This document describes the Python host-rendered plugin, catalog, scene, and
receiver-hybrid product contracts. Phase 3A of the
[unified roadmap](plan-revamped-animation-pipeline.md) provides explicit
receiver ownership, status v3, a staged host-authoritative presentation context,
and a statically linked rainbow. Phase 3B adds negotiated status v4, bounded
premultiplied-RGBA foreground state, sparse exact-roster host orchestration,
leases, scheduled commit, fixed-point receiver composition, manager lifecycle,
desired-scene persistence, and complete host RGB takeover.

The absent or disabled schema-v3 rollout config selects feature-off production
firmware and the Python full-frame path. Explicit local and managed-native
canaries both require the strict all-readable policy; native execution additionally
requires `native_modules_enabled=true`. Ordinary `just deploy` reconciles the
selected baseline firmware and target-owned libraries but never installs or
activates a receiver-native package. Dynamic native playback remains default-off
until the roadmap's H0-H4 physical gates are accepted.

The retired 2026-08-14 four-receiver installation used the named
`degraded_spi1_01_readable` policy and `(0,1,3,2)` physical order. Its status,
camera, and 32 x 138 payload measurements are historical evidence only. The
schema-v3 migrator recognizes exactly that legacy payload as a safe feature-off
migration input; it is not a selectable current operating mode or release gate.

The finalized five-receiver topology keeps four independent coordinate facts:
fixed SPI route/logical identity, physical lane permutation, host-frame/sparse
strip direction, and receiver-native procedural direction. The target-owned
config is authoritative and carries physical order `(0,1,2,3,4)`, widths
`(8,8,8,8,1)`, offsets `(0,8,16,24,32)`, output masks
`(255,255,255,255,255)`, host reversal map
`(false,false,false,false,false)`, and independently retained native reversal map
`(false,false,true,true,false)`. The fifth
mask broadcasts one semantic strip across the dedicated receiver's outputs;
the semantic wall remains 33 strips wide.
Host transforms must not be reused as proof of
native orientation: verify boundary-crossing foreground and a direction-marked
native phase field separately. See [Hardware](HARDWARE.md#installed-lane-and-strip-orientation)
for the exact installed mapping and diagnostic sequence.

The follow-on Phase 3B0 slice adds capability-gated sparse batch command `0x35`.
Its 28-byte fixed header carries the session, generation, and logical span
count; each sorted span adds `start:u16`, `count:u16`, and premultiplied RGBA.
`OVERLAY_BEGIN.expected_patches` and status-v4 `accepted_patches` count spans,
not packets. One CRC and one queued-response command/result proof therefore
cover all spans in a batch, while receivers without capability bit `1<<5`
continue using the original `0x31` packets. The exact wire layout, 4,096-byte
capacity rule, retry semantics, and malformed vectors are frozen in
[ANIMATION_PIPELINE_CONTRACT_V1.md](ANIMATION_PIPELINE_CONTRACT_V1.md).
On the historical frozen 32×138 clock trace this changes the representative patch
payload
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

The `native-animations` branch remains an organ donor for receiver work. Reuse
its ABI/build validation, host preview harness, ESP-IDF loader baseline,
content-addressed cache, typed parameters, status, and quarantine ideas, but port
every historical four-board failure case into the finalized heterogeneous
exact-five transaction contract. Do not carry forward its separate native
catalog,
signed package envelope, frame-track backend, separate gallery, or exclusive
Python-versus-firmware lifecycle. The roadmap's donor table is authoritative for
commit/path mapping and replacement contracts.
