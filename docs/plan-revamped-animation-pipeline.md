# Unified Delivery and Animation Pipeline

## Summary

Build the delivery foundation first, then replace the single-animation pipeline
with a small, explicit presentation model:

- Keep `just` as the operator interface, `uv` and PlatformIO as the build tools,
  systemd as the runtime supervisor, and shell/SSH/SPI helpers as stable leaf
  operations. Add only a thin, testable deployment coordinator.
- Make dependencies and firmware toolchains reproducible, record append-only
  deployment receipts, stage immutable app releases with app-only rollback, and
  automate provisioning only after those foundations pass physical acceptance.

- A **scene** contains one opaque background and zero or more transparent
  overlays. Version 1 supports one aggregate foreground plane and source-over
  alpha rather than an arbitrary render graph.
- A background or overlay is a repository-owned **component** with an explicit
  execution provider. Python, receiver-native, and future frame-track components
  share discovery, presets, previews, status, and lifecycle without pretending
  to use the same runtime.
- A top-level **vibe** remains independent of component and scene presets. It
  supplies a canonical vibe ID, semantic palette, tempo, and luminance context
  without mutating authored parameters or resetting simulation state.
- Receiver-native backgrounds render locally while the Pi sends only sparse
  foreground changes. A full host frame remains the universal takeover and
  rollback path.
- Foliage, globes, clearance, regions, and derived geometry are packaged as a
  versioned **installation profile**, not duplicated in animation packages.
- Native background source lives beside normal plugins under
  `animation/plugins/<plugin_id>/`. A deterministic workstation build produces
  a host preview and an unsigned, content-addressed ESP32-S3 payload.
- Unsigned native code is trusted repository code, not sandboxed user content.
  There is no arbitrary dashboard upload endpoint and no claim that SHA-256
  provides authenticity.
- Deliver the architecture incrementally. Prove vibe and composition on the Pi,
  then a statically linked firmware background with a sparse clock overlay, and
  only then introduce uploadable native modules.

The existing 32 x 138 wall, four 8 x 138 receivers, 160 Hz installed full-frame
target, and compiled startup rainbow remain the operating envelope and fallback.
Target 200 is retained only for output-rate saturation characterization. Existing
Python plugins and presets must continue to work throughout migration. Every
phase is independently useful, has an acceptance gate, and stops before the next
risk domain.

The installed SPI1 MISO fault limits evidence, not outbound presentation. MOSI,
chip select, host transfer accounting, and the complete-host-frame fallback still
work on all four receivers. Host UX, semantic-palette migration, portable sparse
foreground and geometry work, repository-native build/library work, strict
one-receiver canaries on readable SPI0, and an explicitly degraded visual
four-wall showcase may therefore proceed. The fault still blocks strict
acknowledgement-based all-board acceptance, transactional activation claims, and
production enablement of receiver-local features.

## Plan Authority and Resolved Decisions

- This is the sole forward-looking `docs/plan*` document. It consolidates the
  former build/deploy, uploadable-native-animation, and revamped-animation plans;
  their prior text remains available through Git history.
- The `native-animations` branch remains an implementation reference for the
  ABI, builder, loader, cache, status, quarantine, and four-board orchestration.
  Port audited pieces by responsibility; do not merge the branch wholesale.
- Native backgrounds are trusted, unsigned artifacts built only from tracked,
  allowlisted repository source. There is no arbitrary dashboard upload. If the
  trust boundary expands, require a sandbox or authenticity mechanism first.
- Receiver frame tracks, GIF/WebP packages, signing infrastructure, a separate
  native gallery, and exclusive receiver playback are deferred. The first
  receiver product is one native background plus a sparse Pi foreground.
- Deployment uses stable namespaced step IDs, opaque artifact receipt metadata,
  shared state outside application releases, and explicit build, install, and
  activate steps. It does not begin with a generic provider/action DSL.
- [RENDERING_PIPELINE_ACCEPTANCE.md](RENDERING_PIPELINE_ACCEPTANCE.md) remains
  the baseline for streamed-frame performance, receiver timing, rollback, and
  physical-wall qualification. New modes add gates; they do not weaken existing
  ones.
- [ANIMATION_SYSTEM.md](ANIMATION_SYSTEM.md) remains the current Python contract
  until each phase explicitly updates it.
- Implementation readiness and release readiness are separate gates. A later
  portable or host-product lane may begin once its software contracts are frozen,
  even while an earlier physical gate is unavailable. Every such feature remains
  default-off, preserves complete RGB takeover, and cannot be called accepted or
  production-ready until its own strict hardware gates pass.

### `native-animations` branch as an organ donor

The branch is a substantial, cross-domain prototype and remains the preferred
source for implementation patterns once work reaches Phase 3. It is not the
roadmap, a production baseline, or a branch to merge/cherry-pick wholesale. Main
and `native-animations` have diverged, and the prototype encodes product choices
that this plan has replaced.

Port by responsibility, together with the nearest tests:

| Unified phase | Donor commits and areas | Reuse | Redesign or omit |
| --- | --- | --- | --- |
| Phase 0 | `ebf0ede`; firmware hashing, readiness, provisioning, deploy failure tests | Input identity, capability/readiness gates, per-device evidence | Signing keys, signed capability gates, four key-specific builds |
| Phase 3A | `ac72df1`; ESP-IDF baseline, protocol/status, display mode, receiver control, startup fallback | Loader-capable baseline, explicit state scaffolding, status counters, typed parameters | Exclusive host-or-local ownership, first-frame takeover side effects, ignored cadence |
| Phase 3D | `c7e1464`; ABI header, target/host builds, ELF checks, previews, examples, benchmarks | Stable C ABI seam, global offsets/time, caller-owned output, dual builds, validation | ABI v1, split native catalog/source layout, signed package/index assumptions |
| Phase 4 | `ac72df1` and `c9432b5`; loader, upload/cache, library, drivers, four-board transactions | Ordered idempotent chunks, `.part` staging, hash verification, atomic visibility, disposable cache, failure injection | Signature envelope, single ambiguous digest, unpinned rollback asset, render-only watchdog coverage |
| Phase 4 product integration | `db0a361` and `a4a0805`; manager persistence/adoption and dashboard health | Transition/adoption cases, progress/status UX, API and persistence test ideas | Separate gallery, fake peer lifecycle, one exclusive provider/mode |

Useful branch paths include:

- `firmware/esp32/include/ledgrid/animation_abi.h`
- `firmware/esp32/include/ledgrid/display_mode.hpp`
- `firmware/esp32/src/esp_backends.cpp`
- `firmware/esp32/src/asset_upload.cpp`
- `firmware/esp32/src/receiver_control.cpp`
- `firmware/esp32/test/test_native/test_pipeline.cpp`
- `firmware_animations/native.py`, `manifest.py`, `library.py`, and `package.py`
- `drivers/spi_controller.py` and `drivers/multi_device.py`
- `tests/unit/test_firmware_host_protocol.py`
- `tests/unit/test_firmware_host_orchestration.py`
- `tests/unit/test_firmware_package_sdk.py`

Inspect them with `git show native-animations:<path>` or a narrow diff. Prefer
porting a small implementation lane plus its tests over copying a final branch
file, because main has continued changing the manager, deployment, firmware, and
plugin contracts.

The following branch behavior is evidence only and must not cross into the new
contract:

- the signed `.lga`/LGIX envelope, ECDSA verification, key provisioning, and
  `LEDGRID_ALLOW_UNSIGNED_DEVELOPMENT` path;
- receiver frame tracks and GIF/WebP packaging, which remain deferred;
- central native example catalog and separate on-device gallery;
- mutually exclusive Python versus firmware animation playback;
- partial RGB updates that take ownership of the sole working frame instead of
  representing an alpha foreground;
- one digest standing for both bundle metadata and executable bytes;
- cache eviction that protects only the active artifact, not the rollback and
  staged candidate;
- watchdog coverage limited to render rather than every module-controlled phase;
- reliance on `preferred_fps` validation without enforcing source cadence;
- any security or recovery guarantee for unsandboxed in-process machine code.

The branch's physical handoff is also not acceptance evidence. Its documented
SPI1 MISO/MOSI coupling fault must be repaired and the Phase H0 streamed baseline
must pass before all-wall receiver-native release. Until then, use branch code as
tested prototype material, not proof that the installed system is qualified.

## Problems and Success Criteria

### Problems to solve

1. **Single-owner rendering prevents composition.**
   `AnimationManager` owns one active animation and current frames have no alpha,
   coverage, layer identity, or blend semantics. A clock must currently own and
   render its own background instead of decorating another animation.

2. **Presets conflate content with atmosphere.**
   Curated presets repeatedly encode brightness, speed, mood, and palette.
   Changing the room from quiet to celebratory requires selecting or creating a
   different preset for every animation.

3. **The Pi sends authoritative complete frames.**
   Existing dirty ranges reduce transport for a mostly static final RGB frame,
   but they cannot express transparency over a background that continues to
   change on a receiver.

4. **Native playback has been modeled as a separate product.**
   The reference branch uses a separate source tree, catalog, gallery, package
   trust path, and mutually exclusive manager mode. That makes native content
   harder to author, preview, deploy, select, and restore than Python content.

5. **Plant geometry exists only as host-side evidence and NumPy state.**
   A receiver-native background cannot route around globes or participate in
   calibrated optical transforms without a compact, versioned geometry contract.

6. **Distributed receiver state can diverge.**
   Four independent ESP32s can hold different backgrounds, overlay generations,
   vibe revisions, geometry profiles, or clocks after a partial operation.

7. **The current global speed implementation mutates authored state.**
   Persistence must reverse that mutation, and the same approach would make vibe
   changes dirty presets and couple presentation changes to simulation lifecycle.

8. **Deployment behavior is noisy and source policy is implicit.**
   Operators cannot easily distinguish a clean release, a deliberate dirty-tree
   deployment, a no-op, or the exact phase that failed.

9. **Dependencies, firmware toolchains, and target environments are mutable.**
   Repeating a build or deployment can resolve different inputs, while runtime
   environments are updated in place and may retain stale packages.

10. **Deployment sequencing and health are difficult to test.**
    Shell currently carries both platform leaf operations and policy. There is no
    durable attempt receipt proving source identity, completed phases, fresh
    readiness, or rollback outcome.

11. **Application, provisioning, and firmware changes have different rollback
    properties.**
    Treating them as one transaction would over-promise whole-system atomicity
    and make receiver-native rollout harder to recover.

### End-state success criteria

- A user can put the clock over any supported Python background without creating
  a clock-specific copy of that background.
- The same clock can run over a receiver-native background while typical clock
  updates use less than 10 percent of the equivalent full-frame SPI payload.
- Selecting a vibe updates every component claiming support, survives restart
  and deployment, and does not change the selected component or scene preset.
- Presentation-only vibe changes leave seeded logical state and RNG consumption
  unchanged unless a component explicitly declares semantic vibe behavior.
- A tracked native background is discovered in the same catalog, uses the same
  preset and preview surfaces, and can be built, installed, activated, stopped,
  and restored without flashing baseline firmware.
- The Pi library is authoritative; receiver caches can be erased and rebuilt
  without reconstructing an artifact.
- The receiver reports enough state to prove that all four boards agree on the
  controller session, scene revision, background bundle/payload binding,
  foreground generation, resolved vibe and plant-modifier revisions, and
  installation profile.
- Pi disconnect leaves a healthy native background running. Time-sensitive
  overlays expire according to policy instead of freezing stale information.
- A module failure or operator kill switch returns to the compiled fallback or
  complete host frames without requiring a receiver reflash.
- Existing Python plugins, painter/manual output, previews, curated presets,
  deployment preservation, and streamed-frame acceptance continue to pass.
- `just deploy` accepts only a clean tree, `just deploy-dirty` is explicit, and
  `just deploy-plan` accounts for selected and excluded source before mutation.
- A clean checkout reproduces the intended Python and firmware toolchains; an
  unchanged deployment performs no dependency work, provisioning, reboot,
  release activation, or receiver flash.
- Every deployment attempt records an atomic redacted receipt and reports
  success only after fresh controller/system health for the desired release.
- An unhealthy application release automatically restores the prior healthy app
  without claiming that provisioning or firmware was rolled back.
- Native-background source iteration can build, publish, install, and activate
  its artifact without rebuilding the app environment, restarting systemd,
  rebooting the Pi, or flashing loader firmware.

## Core Decisions and Invariants

1. **Use a fixed stack, not an arbitrary graph.**
   Version 1 has one opaque base plus one aggregate transparent foreground plane.
   The Pi may combine several logical overlays into that plane. Add more planes
   or blend modes only after a concrete accepted use case requires them.

2. **Keep provider and role separate.**
   `provider` answers where and how code executes. `role` answers whether the
   component is a background, overlay, or compatibility full scene. A native
   background is a catalog peer but never a fake `AnimationBase`.

3. **Keep authored, presentation, and operator state separate.**
   Component parameters express authored behavior. Vibe expresses current
   atmosphere. Master brightness and tempo remain operator controls. Derived
   effective values are never written back into a preset.

4. **Black is a color, not transparency.**
   Foregrounds carry explicit alpha. Removing or moving a foreground clears
   alpha at its previous coverage; it never paints a remembered copy of the
   background.

5. **Use byte-exact premultiplied RGBA8 for version 1.**
   A shared integer source-over implementation and golden vectors define host,
   preview, and firmware behavior. Do not begin with alpha4 or a blend-mode zoo;
   memory is not the limiting constraint at 8 x 138.

6. **Schedule each component at its semantic cadence.**
   A 1 Hz clock does not become a 200 Hz renderer. A native background's declared
   cadence is enforced, and unchanged work reports `changed=False` or a next
   deadline. Manager call rate must not multiply semantic events.

7. **Use global coordinates and a common scene epoch.**
   Every receiver receives global dimensions, its strip offset, a scene epoch,
   and deterministic seed material. Strict v-sync remains deferred, but skew and
   drift are measured rather than hidden.

8. **The complete host frame is the universal kill switch.**
   Legacy full-frame commands retain a well-defined host takeover path. PING,
   status, brightness, configuration, asset transfer, and foreground traffic do
   not implicitly change background ownership.

9. **Keep the Pi authoritative.**
   Desired scene/vibe state, native artifacts, calibration artifacts, and the
   complete foreground snapshot live in managed Pi state. Receiver flash is a
   recoverable cache.

10. **Unsigned means trusted-local, not safe-to-upload.**
    Only source at tracked, allowlisted repository paths may enter the native
    build/install path. An explicit development command may build modified
    tracked source and records its exact working-tree digest; it never accepts an
    arbitrary or untracked executable input. Hashes detect corruption and identify
    content; they do not authorize code. If native content later becomes
    multi-user or externally supplied, stop and choose a sandbox or restore an
    authenticity mechanism before expanding scope.

11. **Preserve physical acceptance boundaries.**
    Host simulation and desktop timings are useful gates but never substitute
    for Raspberry Pi, one-receiver, four-receiver, and photographed wall evidence.

12. **Use the existing tools at their natural boundaries.**
    `just` remains the operator interface, `uv` and PlatformIO own reproducible
    build inputs, systemd owns runtime supervision, and rsync/SSH/SPI/flashing
    remain platform-specific leaf operations.

13. **Keep the deployment coordinator deliberately small.**
    It owns source policy, ordered steps, redaction, receipts, health, and
    recovery sequencing. It is not an artifact-provider framework, action DSL,
    deployment database, or shell rewrite.

14. **Keep rollback claims domain-specific.**
    App releases can switch atomically and automatically restore. Firmware
    partial failure requires explicit recovery. Receiver-background activation
    owns its own stage/verify/activate/compensate transaction.

15. **Measure before caching gates.**
    Never cache health, provisioning discovery, flashing, receiver readiness, or
    physical acceptance. Add a deterministic local gate cache only after receipts
    show that a complete-input gate repeatedly costs enough to matter.

## Core Model

### Component descriptor

Version 1 keeps one selectable component per plugin package. Do not introduce a
multi-component manifest until at least two real packages require it.

```text
ComponentDescriptor
  manifest_version
  plugin_id
  name, description, icon, gallery
  provider: python | receiver_native
  role: background | overlay | full_scene
  entrypoint
  parameter_schema and defaults
  preferred_fps or cadence contract
  vibe color policy
  vibe capabilities
  installation-profile requirements
  preview/build metadata
```

Compatibility defaults:

- Ordinary existing `AnimationBase` plugins are Python backgrounds.
- Plugins that write directly to hardware through `StatefulAnimationBase` are
  `full_scene` until converted; they cannot silently enter a composed scene.
- The current `clock` package remains a compatibility full scene. Version 1 adds
  a separate selectable `clock_overlay` package so the one-component-per-package
  rule remains true; both reuse time, layout, and glyph rendering through a
  shared clock-face helper.
- A missing provider/role in a version 1 Python manifest resolves through the
  compatibility adapter. Newly authored manifests must be explicit.

Native package layout:

```text
animation/plugins/<plugin_id>/
├── manifest.json
├── native/
│   └── background.cpp
├── presets/
├── tests/
└── assets/
```

Generated target binaries and previews do not live beside source unless already
treated as checked-in assets. Build outputs are content-addressed artifacts.

### Desired display state

Vibe remains independent of the scene even though one versioned aggregate is
used for persistence and reconciliation.

```text
DesiredDisplayState
  schema_version
  revision
  scene: SceneState
  vibe: VibeState
  plant_modifiers: PlantModifierState
  installation_profile_digest
  output: OutputState

SceneState
  revision
  background: ComponentRef
  overlays: ordered list[OverlayRef]
  known_python_fallback: ComponentRef

ComponentRef
  plugin_id
  provider
  preset_id when selected
  preset_fingerprint when selected
  parameter_overrides
  canonical resolved-parameter snapshot
  bundle_digest when receiver-native
  expected_payload_digest when receiver-native

OverlayRef
  slot_id
  component reference
  enabled
  opacity
  placement: global-logical translation and clip policy
  stale policy

VibeState
  schema_version
  revision
  id
  profile_version
  resolved_profile_digest

OutputState
  master_brightness
  operator_tempo_scale
  power
```

The initial migration maps the current animation and preset to a background-only
scene. It maps current global speed and brightness to operator state, preserves
the existing plant state, selects `neutral` vibe, and stores a schema version so
old snapshots cannot be confused with composed scenes.

Preset identity and active values are not mutually exclusive. Persistence keeps
the selected preset ID/fingerprint, live overrides, and the canonical resolved
snapshot. On restore, a matching preset plus overrides must reproduce the
snapshot. Preset drift uses the stored snapshot, preserves the selected ID as
diagnostic context, and marks the scene dirty rather than silently changing the
display.

### Runtime context and parameter precedence

Introduce an immutable runtime presentation context available to Python and
native components:

```text
AnimationRuntimeContext
  wall_time
  unscaled_elapsed
  scaled_elapsed
  frame_index
  scene_epoch
  global_width and height
  local_strip_offset and width
  vibe id, profile version, palette roles, and capability-resolved values
  installation profile view and plant-modifier state
```

The Python compatibility surface should preserve
`generate_frame(time_elapsed, frame_count)`. `AnimationBase` gains a separate
context update hook or property; ordinary parameter updates are not used for
vibe or installation changes.

Effective values follow these rules:

```text
effective time scale = authored speed × vibe tempo × operator tempo
effective luminance = authored component gain × vibe luminance × master brightness
```

- The framework owns vibe luminance and applies it exactly once after
  composition. Receiver hardware/master brightness remains the final output
  limit.
- Timing uses one of three explicit adapter modes so authored speed is never
  multiplied twice:
  - `legacy_speed_param` receives unscaled elapsed time and an ephemeral
    effective `speed` parameter containing authored × vibe × operator tempo;
  - `scaled_context` receives elapsed time already scaled by authored × vibe ×
    operator tempo and must not multiply authored speed again;
  - `wall_clock` receives wall/unscaled time and ignores tempo.
  Existing plugins default to `legacy_speed_param`; new context-native
  components declare their mode.
- Authored configuration remains immutable. A compatibility adapter may create
  an ephemeral effective parameter view, but it must not persist that view or
  send it through the ordinary live-parameter lifecycle.
- `on_presentation_context_changed(old, new)` may invalidate presentation
  caches. It must not reset semantic state, consume RNG, advance time, or emit a
  simulation event unless the component explicitly declares semantic vibe logic.

### Layer frame contracts

Keep the current opaque RGB contract for bases and add an explicit overlay
contract:

```text
BaseFrame
  pixels: contiguous uint8 (total_leds, 3)
  changed: bool
  dirty_ranges: optional canonical flat-index ranges

OverlayFrame
  pixels: contiguous premultiplied uint8 (total_leds, 4)
  changed: bool
  dirty_ranges: optional canonical flat-index ranges
  revision: uint64 within the controller session
```

Requirements:

- The canonical coordinate space is global logical strip-major `(strip, led)`
  before driver-specific channel encoding or wiring transforms. Flat index is
  `strip * leds_per_strip + led`; receiver-local index subtracts the receiver's
  global strip offset before applying the same formula. Canvas `(row, column)`,
  serpentine, and GRB transforms never appear on the layer/protocol boundary.
- RGB channels in an `OverlayFrame` are already premultiplied by alpha.
- Alpha zero represents no contribution even when RGB is black; alpha 255 is
  opaque.
- Validate premultiplied input with every RGB channel less than or equal to its
  alpha. Scene opacity scales RGB and alpha together using the shared integer
  rounding rule.
- The dirty set for movement or removal is the union of previous and new
  coverage. Ranges are sorted, non-overlapping, and half-open.
- A background change recomposites all active foreground coverage even when the
  overlay itself is unchanged.
- An overlay-only change can use the cached base immediately.
- If both inputs are unchanged, the compositor returns its cached output with
  `changed=False`.
- Composition writes into manager- or receiver-owned reusable buffers. It does
  not mutate component buffers that may be reused asynchronously.
- Logical overlays fold in declared scene order from bottom to top. Because
  fixed-point source-over is not associative after rounding, every fold rounds at
  the same specified point; two-overlay overlap and opacity vectors are golden
  fixtures even though firmware receives one aggregate plane.
- Version 1 placement is integer translation in global logical strip/LED space
  plus a deterministic clip policy. It does not scale or rotate layers.
- Version 1 uses only premultiplied source-over. The current clock's additive/max
  glow may be visually adapted; exact legacy glow is not a reason to add a
  second firmware blend mode before measurement.

### Presets and lifecycle

Keep three distinct selection artifacts:

- A **component preset** configures one component's authored parameters.
- A **scene preset** selects a background, ordered overlays, their component
  presets, placement, opacity, and stale policy.
- A **vibe profile** supplies the independent top-level presentation context.

A scene preset does not capture the active vibe by default. A future convenience
or automation may select a scene and vibe together, but it does so as two
explicit state changes rather than embedding vibe inside the scene artifact.

Each component has independent lifecycle, elapsed time, frame count, cadence,
parameter state, and interaction routing. Removing or changing an overlay does
not restart the base. Inputs target an explicit component or the topmost focused
interactive layer; they are never broadcast implicitly.

### Rendering order

Semantic geometry belongs inside the component that understands it; universal
presentation operations occur once after composition.

```text
1. Advance/render background semantics.
2. Advance/render overlay semantics.
3. Resolve component palette roles or component-local grade policy.
4. Composite premultiplied overlays over the opaque base.
5. Apply universal installation optics that are declared receiver-safe.
6. Apply vibe luminance once.
7. Apply master/safety brightness once.
8. Encode and display LEDs.
```

The host-only implementation performs steps 1 through 6 on the Pi and leaves
master receiver brightness in firmware. The hybrid implementation performs a
native base, foreground composition, receiver-safe optics, vibe luminance, and
master brightness on each receiver. Preview uses the same fixed-point blend and
ordering as its execution provider.

The current framework invokes universal plant optics through the active Python
animation before presentation. Phase 2B must relocate that invocation to the
manager-owned compositor output for composed scenes, while retaining byte-exact
background-only behavior. A component must never apply the same universal optic
before composition and then receive it again afterward.

### Expected repository ownership

Keep responsibilities in their existing domains rather than creating a parallel
native application:

- `animation/core/` owns descriptors, scene/vibe state, scheduling, composition,
  compatibility adapters, and preview orchestration.
- `animation/plugins/` owns Python and receiver-native component source,
  manifests, presets, focused tests, and component assets.
- `animation/libraries/` owns genuinely shared palette, clock-face, geometry,
  and fixed-point composition helpers.
- `drivers/spi_controller.py` owns exact single-receiver wire commands and status;
  `drivers/multi_device.py` owns global slicing and four-receiver coordination.
- `firmware/esp32/` owns display state, foreground buffers, installation-profile
  views, native execution, cache, quarantine, and receiver timing.
- `ipc/control_channel.py` and `web/` own managed commands, status presentation,
  unified catalog/scene UX, and previews; neither owns hardware or compiles
  target code.
- `tools/deployment/`, the `Justfile`, and future native build helpers own
  deterministic build/publish/install/activate steps and receipts.
- `tests/` and `tools/benchmarks/` own cross-domain golden fixtures,
  compatibility tests, performance evidence, and failure injection.

Exact new filenames are chosen during Phase 1. Do not put provider-specific
policy into the generic compositor or duplicate receiver orchestration in the
web process.

## Delivery Architecture

### Operator and source contract

- `just deploy` requires a clean tree.
- `just deploy-dirty` intentionally uses the current tracked-plus-safe-untracked
  manifest policy and records the base commit, diff digest, and included
  untracked paths.
- `just deploy-plan` is read-only and explains selected files, exclusions,
  deployment mode, gates, dependency work, provisioning, release activation,
  firmware work, receiver-artifact work, and expected restart/reboot behavior.
- Successful commands are quiet. A small local runner captures output in an
  ignored log, shows an erasable TTY status line, and prints a concise failure
  plus log path. `DEBUG=1` or an explicit verbose command streams normally.
- Preserve protected runtime presets, `run_state`, logs, environments,
  calibration, artifact libraries, and validated firmware images.

### Reproducible inputs

- Add `pyproject.toml` and `uv.lock` with runtime, test/development, and
  calibration groups. Local setup uses frozen synchronization rather than
  repeated inline dependency lists.
- Pin PlatformIO Core and the currently validated immutable pioarduino/ESP-IDF
  `55.03.39` platform release. PlatformIO continues to resolve and build firmware
  dependencies; changing this pin requires the normal firmware acceptance path.
- Export a fully pinned Pi runtime requirements lock. Build a fresh Pi virtual
  environment keyed by lock digest and Pi Python identity, smoke-test it, and
  reuse it only while that identity remains unchanged.
- Firmware identity includes all source, configuration, platform/toolchain,
  partition, and build-flag inputs. Prefer one common firmware image; keep stable
  logical-device identity/global offset in provisioned configuration and include
  that configuration identity in each receiver's desired-state receipt.

### Thin coordinator and receipts

Introduce only these generic deployment types:

```text
DeployContext
  source policy, target, mode, flags, paths, and redaction rules

Step
  stable namespaced ID, mutating flag, and callable operation

StepResult
  timing, outcome, log reference, and opaque artifact metadata

DeployReceipt
  attempt/source identity, completed steps, artifacts, health, and outcome
```

- Build steps procedurally. Retain rsync, SSH, SPI, flashing, and systemd helpers
  as leaf commands invoked with argument arrays and no `shell=True`.
- Initial step IDs include `source.validate`, `tests.run`, `app.stage`,
  `host.provision`, `receiver.firmware_build`, `receiver.firmware_flash`,
  `host.restart`, and `health.readiness`. Native-domain steps later add
  `receiver_background.build`, `.publish`, `.probe`, `.stage`, `.verify`, and
  `.activate` without generalizing them prematurely.
- Persist append-only, atomic JSON receipts locally and remotely for success,
  failure, and interruption. Record deployment ID, timestamps, target, mode,
  Git revision or dirty digest, dependency/toolchain/manifest digests, step
  timings, opaque artifact entries, health result, and final outcome.
- Artifact receipt entries retain the small shape
  `{kind, id, digest, version, target_id?}`. Domain-specific evidence may live in
  a referenced log or artifact; do not turn the receipt into a general database.
- Never serialize environment values. Redact private-key arguments, sensitive
  paths, and configured secret names from commands, receipts, and logs.
- Health requires systemd state, expected geometry and receiver topology,
  controller status newer than restart, stable fresh samples, and the desired
  release identity. Set the API deployment timestamp only after readiness passes.

### App releases and rollback

- Stage runtime application files and generated previews under
  `releases/<content-digest>` and point systemd at an atomic `current` symlink.
- Keep virtual environments, presets, `run_state`, logs, calibration, firmware,
  and receiver-artifact libraries outside release directories.
- Validate imports and static structure before activation, then preserve settings,
  switch `current`, restart, restore settings, and require fresh desired-release
  health.
- On failure, restore the previous symlink, restart it, verify prior health, and
  record both candidate failure and restoration.
- `just releases` and `just rollback [release-id]` operate only on the app lane.
  They never provision, reboot, build, flash firmware, or mutate receiver state.
- Once receiver-native playback exists, app rollback preflights compatibility and
  refuses an unsafe downgrade. A separate explicit scene-recovery operation must
  take complete host-frame ownership and persist a known Python fallback first;
  the app-only rollback command itself remains receiver-read-only.
- Do not add release garbage collection until retention policy is informed by
  real usage.

### Provisioning and firmware automation

- After receipts and app rollback are proven, compare desired and observed host
  packages, permissions, SPI boot settings, systemd definitions, environment,
  app release, and receiver firmware. Apply only differences.
- If provisioning requires reboot, persist the phase, reboot, wait for SSH,
  rediscover target state, and resume idempotently with a strict loop bound.
- Build the common receiver image before downtime and flash only receivers whose
  firmware identity differs, using stable provisioned paths. During migration,
  existing device-specific images remain supported until logical identity is
  externalized. Missing, duplicate, unexpected, or failed receivers fail the
  operation.
- On partial flash failure, do not activate the candidate app. Leave the service
  stopped, retain prior/candidate images and evidence, and require explicit
  firmware recovery. Do not claim automatic whole-system rollback.
- After firmware success, activate the staged host release and use app-only
  health restoration if that activation fails.

### Evidence-based gate caching

- Use receipts to collect unit, rendering, preview, deployment, firmware-test,
  and firmware-build timings across at least twenty normal attempts.
- Consider caching only a deterministic local gate that regularly costs at least
  five seconds, has a complete reviewable input set, has no hardware/external
  dependency, and materially improves the observed workflow.
- Its key includes selected source contents, dirty manifest, lockfile,
  interpreter/platform, toolchain, command arguments, and an explicit gate
  version. Corruption or missing state reruns the gate safely.
- `just test` always forces the complete gate. If no candidate meets the measured
  threshold, ship no gate cache.

## Vibe and Palette Contract

### Canonical vibe IDs

Use a small stable wire vocabulary. Display names may change, but IDs do not.
Finalize visual values during Phase 2A using these initial candidates:

- `neutral`: authored presentation with unity tempo and luminance.
- `quiet`: restrained motion, luminance, and chroma.
- `cozy`: warm palette roles and moderate motion.
- `vivid`: saturated, higher-contrast presentation and lively motion.
- `celebration`: broad accents and higher energy; semantic logic may opt in.

Unknown IDs are rejected at API boundaries. Persisted unknown profile versions
fall back visibly to `neutral` and publish a diagnostic rather than silently
inventing values.

### Profile fields

```text
VibeProfile
  id
  profile_version
  resolved_profile_digest
  tempo_scale
  luminance_scale
  chroma policy
  semantic role colors
  optional 256-entry color ramp
```

The Pi resolves the canonical profile into exact scalar values, RGB role values,
and optional ramp bytes, then hashes that canonical payload. Receivers consume
the resolved payload and digest; they do not independently look up an ID/version
in firmware and risk running different definitions. ID and version remain the
stable logic/UI vocabulary, while the resolved digest proves presentation
agreement.

Initial semantic roles:

- `background_low`
- `background_mid`
- `background_high`
- `primary`
- `secondary`
- `accent`
- `hud`
- `warning`

Components declare one color policy:

- `semantic`: the renderer consumes semantic roles or the ramp.
- `grade`: framework applies a bounded component-local color transform.
- `preserve`: exact colors are retained for flags, GIF art, diagnostics,
  calibration, or other identity-bearing content.

Tempo and luminance support are separate capabilities. A clock can preserve
wall time while consuming `hud` and `accent`; an exact-color diagnostic can
preserve color while accepting luminance.

### Migration strategy

- Start with a central palette registry and context object; do not rewrite every
  plugin.
- Pilot one procedural atmosphere family, the Clock overlay, one stateful/game
  animation, and one exact-color/GIF/flag animation.
- Provide manifest-local bridges from canonical vibe IDs to existing `mood` or
  `palette` values. Resolve them into presentation caches or ephemeral effective
  state without modifying the selected preset.
- Convert duplicated procedural palette tables to semantic roles incrementally.
- Treat final-frame color grading as a compatibility fallback, not the semantic
  palette architecture.
- Remove or truly implement misleading universal saturation/value controls only
  after compatibility data shows which presets depend on them.
- Test paired seeded runs with different presentation-only vibes and compare
  logical state, RNG state, and event history after multiple semantic updates.
- Characterize representative legacy speed-sensitive plugins under
  `legacy_speed_param`, then add baseline-equivalence tests proving authored,
  vibe, and operator tempo are each applied exactly once. Add equivalent tests
  for one `scaled_context` component and the Clock's `wall_clock` mode.

## Receiver Background and Foreground Architecture

### Explicit display ownership

Replace the current one-way `pi_connected` transition with orthogonal state:

```text
BaseMode
  StartupFallback
  LocalBackground
  HostFullScene

ForegroundState
  Cleared
  Active(controller_session_id, generation, scene_revision, lease)

MaintenanceState
  Inactive
  AssetTransfer or CalibrationTransfer
```

The controller creates a 128-bit session ID at process start. Scene revisions
and foreground generations are unsigned 64-bit monotonic counters scoped to that
session. Receivers compare counters only within the same session; equality is
idempotent only when the complete operation digest matches, lower values are
stale, and counters never wrap. A new session begins through an authoritative
session/reconciliation command that invalidates staged work and requires a full
desired-state and foreground snapshot before deltas.

```text
CONTROLLER_SESSION_BEGIN(controller_session_id, desired_state_revision,
                         authoritative_snapshot_digest)
```

The receiver accepts a new session only as part of reconciliation; beginning it
does not itself change base ownership or reveal partially staged state.

Rules:

- Boot starts `StartupFallback` using the compiled renderer.
- Starting a local background explicitly enters `LocalBackground`.
- A complete legacy host frame explicitly enters `HostFullScene`; version 1
  bypasses receiver foreground composition in this mode because the Pi has
  already flattened the scene.
- A partial legacy range update cannot take ownership from `LocalBackground`.
  The coordinator must send a complete first host frame for takeover before it
  resumes ordinary host dirty-range optimization.
- PING, status, brightness, configuration, parameter, upload, and foreground
  commands never change `BaseMode`.
- Switching from a hybrid native scene to a Python scene clears or hides the
  receiver foreground before host takeover so the clock is not duplicated.
- Stopping or failing a native background returns to the compiled fallback unless
  the coordinator deliberately activates a known Python fallback.

Hybrid presentation context is also staged state:

```text
PRESENTATION_CONTEXT_BEGIN(controller_session_id, scene_revision)
PRESENTATION_CONTEXT_SET(resolved_vibe_payload, vibe_digest,
                         resolved_plant_modifiers, modifier_revision,
                         modifier_digest)
PRESENTATION_CONTEXT_COMMIT(scene_epoch, present_at_scene_time)
```

The Pi is authoritative for exact vibe scalars/palette bytes and resolved plant
modifier IDs/strengths. All four receivers stage and verify the same context
digest before it becomes active. Host and firmware share fixed-point golden
vectors for luminance and any accepted grade/optic; status reports the resolved
digests rather than relying only on human-readable IDs.

### Foreground protocol

Add a versioned protocol namespace rather than reusing `SET_RANGE`:

```text
OVERLAY_BEGIN(controller_session_id, generation, prior_generation,
              scene_revision, scene_epoch, base_revision, format,
              expected_patches, lease)
OVERLAY_PATCH(controller_session_id, generation, start, count,
              premultiplied_rgba)
OVERLAY_COMMIT(controller_session_id, generation, scene_epoch, base_revision,
               present_at_scene_time)
OVERLAY_CLEAR(controller_session_id, generation, scene_revision)
OVERLAY_RENEW(controller_session_id, generation, lease)
```

- Staging uses a separate buffer or copy-on-begin. An interrupted or invalid
  generation leaves the prior committed foreground visible.
- `OVERLAY_BEGIN` binds staging to a required scene epoch and base revision or
  bundle binding. Every patch resolves through that staged binding, and commit
  succeeds only while the receiver still displays the same base. A retry from an
  older scene cannot commit over a newly activated background.
- Patch ordering and retry semantics are explicit. Duplicate identical chunks
  are idempotent; conflicting overlap, stale session/revision/generation, and a
  failed `prior_generation` compare-and-swap are rejected.
- Every serialized command, including headers and CRC, remains within the current
  4096-byte host/receiver transaction ceiling. Exact maximum-size packets are
  shared test fixtures.
- Phase 1 freezes the exact header and CRC size, then defines
  `max_rgba_pixels = floor((4096 - header_bytes - crc_bytes) / 4)`. A complete
  4,416-byte receiver foreground is necessarily split into multiple patches;
  full-snapshot chunking, ordering, and completion are part of the golden wire
  contract.
- A full foreground snapshot is always available for initial synchronization,
  periodic repair, and Pi/controller restart. Deltas are only an optimization.
- Alpha-zero patches remove old content and reveal the current base.
- The controller stages the same generation on all four receivers before commit.
  Partial commit triggers replay or compensation and a degraded status; it is
  never reported as a healthy scene.
- Commits use one future `present_at_scene_time` after all boards acknowledge
  staging. Strict LED-level v-sync remains out of scope, but first-to-last visible
  commit skew must be measured and remain below one accepted display period for
  a cross-boundary clock glyph; otherwise hybrid foreground stays experimental.
- Time-sensitive overlays such as clocks use a finite lease and clear after Pi
  loss. Decorative overlays may explicitly choose a hold policy.
- The Pi retains the authoritative full aggregate foreground and republishes it
  before resuming deltas after restart or receiver replacement.
- Every base transition defines foreground disposition. `HostFullScene` hides or
  clears receiver foreground; a new `LocalBackground` starts with foreground
  cleared unless a generation was staged for its exact epoch/base binding; a
  crash fallback invalidates the old binding and clears it by default.

### Memory and timing

Per 8 x 138 receiver, the initial foreground design requires approximately:

- 4,416 bytes for RGBA8;
- 138 bytes for a coverage bitset;
- 3,312 bytes for an additional RGB composite buffer;
- another 4,416 bytes if staging uses a full second RGBA plane.

This is preferable to alpha quantization until measured memory pressure says
otherwise. The receiver still emits a complete local WS2812 frame whenever the
native background or overlay changes; sparse foregrounds save Pi rendering and
SPI transfer, not LED wire time.

### Native cadence and synchronization

- The native ABI receives unscaled and scaled elapsed time, frame index, global
  geometry, local strip offset, deterministic seed, and scene epoch.
- The render result reports whether base pixels changed and the next desired
  deadline. The firmware does not call every native renderer on every 5 ms loop
  merely because the display task is awake.
- Analytic, global-coordinate backgrounds are the first accepted native family.
  Stateful cross-board simulations remain host-rendered until their shared-state
  and synchronization needs are demonstrated.
- Starts remain sequential in version 1. Record scheduled epoch, actual first
  frame time, skew, and drift for every receiver.

### Status and observability

Extend status without losing current CRC, queue, mailbox, encode, show, and
sequence counters. Expose at minimum:

- protocol and capability versions;
- logical receiver identity, global strip offset, controller session ID, and
  desired-state revision;
- base mode, component ID, active bundle digest, active payload digest, compact
  descriptor revision, and scene epoch;
- active and staged foreground generation, age, coverage, lease, and last result;
- vibe ID/profile/revision/resolved-profile digest, resolved plant-modifier
  IDs/strengths/revision/digest, and installation-profile digest;
- native render, foreground composition, encode, and display p50/p95/p99/max;
- declared cadence, missed deadlines, changed-frame ratio, and last frame time;
- SPI foreground bytes, ranges, full snapshots, retries, and rejected generations;
- cache used/free/reserve, upload progress, active/staged artifact, and quarantine;
- mode-transition reason, reset reason, watchdog count, and last operation result;
- cross-board agreement and degraded-state summary at the Pi.

Receipts and status are local; this plan does not introduce hosted telemetry.

## Installation Geometry Profile

Ship calibration as an independent, content-addressed artifact. Native packages
declare a geometry ABI/capability, not a hard-coded installation digest.

The compiled profile contains:

- format version, wall dimensions, global origin, and calibration digest;
- exact foliage and globe categories;
- the seven stable globe/rooting-bowl region identities;
- selected clearance and edge fields;
- optional quantized distance and normal fields when a measured use requires
  them;
- receiver slices plus explicit halo/precomputed fields where board boundaries
  matter;
- per-section lengths, CRCs, and an overall content digest.

Compile all geometry globally, then slice it. Do not independently dilate or
derive neighbor-dependent fields on four receivers and create artificial seams.
The Pi retains the source calibration evidence and authoritative compiled
profile; receivers cache only the bounded binary data needed at runtime.

Use geometry in two ways:

1. **Semantic component behavior.** Native and Python renderers may query
   foliage, exact globes, clearance, safe space, edges, normals, and regions for
   routing, placement, collision, portals, habitat, and HUD avoidance.
2. **Universal final optics.** Only modifiers already defined as framework-wide,
   bounded presentation operations may run after composition. Version 1 starts
   with `hue_shift`; a new final optic requires its own distinct capability and
   cannot silently reinterpret plugin-semantic `shadow` or `illuminate`.

Do not initially move all fourteen plant modifiers into firmware. Neighbor
sampling such as liquid-glass/refraction remains host-side until a halo or global
sampling contract passes cross-receiver seam tests. Missing required geometry
rejects component start; missing optional geometry produces an explicit no-op and
diagnostic.

Profile activation is separately staged on all four receivers. A scene never
runs with silently mixed calibration generations.

## Unsigned Native Background Lifecycle

### Trust boundary

Native Xtensa machine code is trusted and unsandboxed. Version 1 permits native
build/install only when:

- the plugin manifest and source are tracked in the repository allowlist;
- the build command resolves source beneath that package;
- the pinned local toolchain performs the build;
- the Pi accepts only a managed library ID or resolved managed path through IPC;
- the dashboard cannot upload packages or choose arbitrary filesystem paths;
- every receiver validates bounds, digest, ABI, target, geometry, exports, and
  allowed imports before activation.

There are deliberately no signing key, signature, trusted-key, or signed-index
fields. A development bypass from the reference branch is not reused because it
still assumes the signed package shape.

### ABI and bundle

Define a native background ABI v2 based on the useful reference-branch seam:

- stable `extern "C"` entrypoints;
- explicit init/render/context-update/cleanup lifecycle;
- local caller-owned RGB output;
- global geometry and local strip offset;
- unscaled/scaled time, frame index, scene epoch, and deterministic seed;
- compact typed parameter values;
- read-only vibe/palette and optional installation-profile views;
- read-only resolved plant-modifier IDs, strengths, revision, and capability
  state;
- bounded helper functions only; no driver or peripheral access;
- render result containing changed state, next deadline, and failure.

The supported ABI exposes no driver/peripheral handles, but it is not a memory
isolation boundary. Version 1 rejects module initializer/finalizer sections and
attributes the candidate payload digest before `dlopen`. Arm the hard watchdog
around load/relocation, entrypoint discovery, initialization, every context
update, render, cleanup, and unload. A reset during any phase quarantines the
attributed payload. Recovery remains best effort because trusted in-process code
can corrupt arbitrary firmware memory before a reset.

The deterministic unsigned bundle contains:

- canonical manifest and parameter schema;
- package, ABI, target, geometry, cadence, toolchain, and source identities;
- one shared Xtensa `.so` addressed by global offset;
- payload SHA-256 and exact size;
- generated animated WebP preview and provenance;
- no signature envelope.

Identity has two explicit levels:

- `bundle_digest` covers the complete canonical bundle, including manifest,
  schema, cadence, preview identity, and payload binding. Desired state,
  persistence, receipts, activation, and cross-receiver unanimity use it.
- `payload_digest` covers the executable bytes. Receiver blob caches and module
  quarantine use it, allowing a manifest-only bundle update to reuse an unchanged
  executable safely.

Each receiver stores a validated compact descriptor binding the bundle digest to
the payload digest, ABI, target, geometry, cadence, and parameter-schema revision.
Status always reports both identities; an unbound cached payload is not an
activatable component.

Use one shared module unless profiling demonstrates a device-specific need. Four
key-specific or device-specific builds are not retained merely because the signed
prototype used them.

### Build and validation

Compile source twice:

- an ESP32-S3 PIC C++17 shared object using the pinned Xtensa toolchain, no
  exceptions/RTTI, bounded exports/imports, and the versioned ABI;
- a trusted host preview shared object used only by an isolated builder/preview
  process.

The target ELF is never executed by the web or controller process. The host
preview renders all four 8 x 138 offsets and stitches a 32 x 138 result so global
coordinate errors are visible before packaging.

Build validation includes deterministic source/toolchain fingerprints, ELF
class/machine/type, ABI, target, geometry, exact export, import allowlist, size,
parameter defaults/bounds, output canaries, cadence behavior, and host preview
mean/p95/p99/max. A default target limit of 512 KiB may be retained from the
prototype until real packages justify a change.

Expose separate operations:

```text
just native-build <plugin-id>
just native-install <plugin-id-or-digest>
just native-start <plugin-id-or-digest>
just native-run <plugin-id>
```

`native-run` is convenience composition of build, preview, validate, publish,
probe, stage, verify, and activate. The underlying receipt retains each step.
These native-source commands operate on the selected package's exact working-tree
digest without requiring unrelated repository files to be clean. Modified tracked
package source is allowed only through this explicit development workflow and is
recorded in the receipt; arbitrary paths and untracked executable inputs remain
rejected. This does not weaken the clean-tree requirement for `just deploy`.

### Pi library and receiver cache

- Store published bundles in shared Pi state outside immutable application
  releases, virtual environments, runtime presets, and firmware images.
- Address immutable artifacts by bundle digest and payload digest. Publish
  atomically under a library lock.
- Never send large binary payloads through the single JSON control file. IPC
  carries managed IDs and progress/status only.
- Probe all four receiver caches before transfer. Existing matching content is
  never rewritten.
- Upload ordered, bounded, retryable chunks to `.part` state; verify size and
  digest before atomic visibility.
- Maintain a free-space reserve and evict only inactive, unpinned
  least-recently-used entries.
- Model explicit cache pins/reservations for the active binding, prior rollback
  binding, and staged candidate. Capacity-preflight every receiver before upload;
  staging cannot evict the payload or descriptor required for compensation.
  Release the rollback pin only after explicit soak acceptance or operator action.
- Refuse deletion of active, staged, or rollback-pinned bundle bindings and
  payloads.
- Cache loss is repaired from the Pi without rebuilding source.

### Activation and failure recovery

Activation follows:

```text
fresh identity/capabilities
  → probe all receivers
  → stage missing payloads
  → verify bundle/payload binding, ABI, and geometry on every receiver
  → stage parameters, vibe, resolved plant modifiers, profile, seed, and epoch
  → start sequentially near the shared epoch
  → verify unanimous state
```

Retain and pin the previous accepted bundle binding and executable payload until
the new one completes soak. If one start fails, stop the candidate and reactivate
the prior bundle everywhere. If unanimity cannot be proven, report degraded
state and use the complete host-frame kill path; never claim a healthy mixed wall.

Record the active bundle and payload digests before every module-controlled
phase. A callback failure or hard-watchdog reset quarantines the payload digest,
selects the compiled fallback when firmware remains healthy, and prevents
automatic retry until an explicit reinstall or quarantine-clear action. Because
the module shares firmware memory, fallback and quarantine are recovery aids,
not guarantees against arbitrary corruption; the retained firmware reflash path
remains the final recovery boundary. Receiver code still cannot mutate the
authoritative Pi library.

## API, Dashboard, Preview, and Persistence

### API and IPC

Add versioned commands and status for:

- reading and updating vibe;
- reading, validating, starting, and stopping scenes;
- independently updating background and overlay parameters;
- selecting component and scene presets;
- listing unified component descriptors by provider and role;
- native build/install/start progress through managed IDs;
- installation-profile status and activation;
- receiver agreement, degradation, fallback, and quarantine.

Do not expose arbitrary native package upload. Web and controller processes keep
their current control-channel boundary; the hardware-owning process remains the
only process that mutates receiver state.

### Dashboard

- Put vibe beside other global controls, not inside animation parameters.
- Present one component catalog with provider and role badges instead of a
  separate native gallery.
- Provide a fixed scene editor: one background slot and an ordered overlay list.
  Hide unsupported role/provider combinations.
- Show component presets separately from scene presets and clearly indicate when
  the active scene differs from a saved scene.
- Use build-time native previews and label them as previews; there is no promise
  of receiver framebuffer readback.
- Surface active/staged bundle and payload bindings, install progress, receiver
  agreement, overlay age/lease, vibe/profile revision, calibration digest, and
  fallback/quarantine.
- Suppress duplicate plugin-local controls when vibe or installation state is
  globally authoritative, while retaining schema support for direct/headless
  tests where required.

### Preview

- Python scenes preview through the same host compositor and fixed-point blend
  used live.
- Native backgrounds preview through the trusted host build artifact at each of
  the four logical offsets, then use the normal overlay compositor.
- Preview cache keys include component artifacts, authored parameters, source
  cadence tick, vibe profile/revision, scene layout, plant modifiers, and
  installation-profile digest.
- Vibe/overlay preview updates do not mutate or restart the live scene.

### Persistence and deployment

- Replace the single-animation before-deploy snapshot with versioned desired
  display state. Preserve scene, component presets/parameters, independent vibe,
  plant state, output controls, known Python fallback, and artifact digests.
- Restore only after validating provider capabilities and managed artifact
  existence. Unknown schema/provider/bundle binding falls back to the known
  Python scene rather than competing with unexplained receiver-local playback.
- On Pi restart, adopt receiver-native state only when all four boards report the
  expected bundle/payload binding, epoch, vibe, resolved plant-modifier revision,
  profile, and no quarantine. Then republish the complete foreground snapshot
  before deltas.
- Native source-only iteration builds and publishes an artifact; it does not
  rebuild the application venv, restart systemd, reboot, or flash baseline
  firmware.
- Loader, ABI, partition, or firmware changes use the full firmware deployment
  lane.
- Application release rollback preserves the native library and calibration
  library. It refuses to downgrade to software that cannot understand active
  native state until a separate explicit recovery operation has taken complete
  host-frame ownership and persisted a known Python fallback.

## Alternatives Considered

### Delivery orchestration

| Approach | Advantages | Disadvantages | Decision |
| --- | --- | --- | --- |
| Keep all policy in shell | Minimal migration | Harder failure injection, receipts, redaction, resume, and health testing | Retain shell only for leaf operations |
| Rewrite deployment wholesale in Python | One implementation language | Large simultaneous migration and loss of proven platform behavior | Rejected |
| Thin Python coordinator over existing helpers | Testable policy and receipts with incremental parity | Temporary dual path during migration | Chosen |
| Generic provider/action/transaction DSL | Abstractly uniform | Premature before two accepted domains expose real commonality | Deferred |
| Cache every successful gate | Fast repeated deploys | Unsafe incomplete keys and hidden external state | Rejected; measure first |

### Composition

| Approach | Advantages | Disadvantages | Decision |
| --- | --- | --- | --- |
| Wrapper `CompositeAnimation` | Fastest experiment; little manager/API work | Nested lifecycle, parameter naming, preset identity, and stateful-plugin hazards | Suitable only as a throwaway spike |
| Fixed background plus overlays | Solves clock and HUD use cases with bounded scheduling, UI, and transport semantics | Does not express arbitrary graphs | Chosen for version 1 |
| General render graph | Maximum flexibility for masks, effects, and many sources | Premature graph validation, dynamic schemas, scheduling, interaction routing, and cache complexity | Deferred |

### Vibe

| Approach | Advantages | Disadvantages | Decision |
| --- | --- | --- | --- |
| Mutate preset parameters | Reuses current schemas | Dirties presets, conflates authored/effective state, can reset logic | Rejected |
| Final-frame LUT only | Broad compatibility and simple implementation | Cannot express semantic colors or protect exact content; cannot inform logic | Compatibility fallback only |
| Runtime context plus semantic palette roles | Separates state, supports logic, and migrates incrementally | Requires capability declarations and selected plugin refactors | Chosen |

### Background execution

| Approach | Advantages | Disadvantages | Decision |
| --- | --- | --- | --- |
| Pi-only Python compositor | Lowest risk and easiest synchronization | Full Pi dependence and frame traffic | First proof and permanent fallback |
| Native C++ on the Pi | Faster dense rendering and shared build tooling | No receiver-offline or SPI benefit | Optional diagnostic/provider later |
| Statically linked receiver backgrounds | No upload/security/loader uncertainty | New backgrounds require a firmware flash | Required canary before dynamic loading |
| Dynamic unsigned native modules | Fast iteration, Pi offload, offline background | Trusted arbitrary machine code, cache/ABI/crash/distributed complexity | Chosen only after canary acceptance |
| Frame tracks/declarative primitives | Predictable and easier to validate | Storage or interpreter cost; less procedural flexibility | Future provider, not version 1 |
| WASM/bytecode | Potential sandbox boundary | Significant runtime/toolchain work and uncertain ESP32 performance | Deferred unless trust scope expands |

### Installation effects

| Approach | Advantages | Disadvantages | Decision |
| --- | --- | --- | --- |
| Keep everything on Pi | One global framebuffer and mature geometry | Cannot transform a current receiver-native base | Keep semantic host uses; insufficient alone |
| Move every modifier to firmware | Local final frame is available | Duplicates semantic logic and creates boundary/synchronization risk | Rejected |
| Shared profile plus selected receiver-safe optics | Preserves semantic geometry and enables hybrid final transforms | Requires profile ABI and parity tests | Chosen |

## Phased Delivery

Each phase must be independently useful, gated behind explicit capability or
feature state, and retain the previous complete-frame path. Do not cross a
phase's release boundary merely because its code exists. Portable implementation,
host UX, preview, fake orchestration, and a deliberately labeled degraded canary
may proceed when their prerequisite software contracts are complete, but they do
not close or waive unavailable physical acceptance. Subphases within Phase 0
ship separately in their listed order.

Dependency outline:

```text
Phase 0A quiet deploy UX
  → 0B reproducible inputs
  → 0C coordinator/receipts/health
  → 0D app releases/rollback
  → 0F automatic provisioning/firmware

Phase 0C → 0E collect timings/cache decision

Phase 0 delivery foundation
  → Phase 1 contracts and baseline
      ├── Phase 2A vibe
      └── Phase 2B host composition
              └── Phase 2C scene/catalog productization
                      ├── Phase 2D semantic-palette/hybrid UX rollout
                      ├── Phase 3A receiver ownership/static background
                      │       └── Phase 3B portable sparse foreground
                      │               └── Phase 3C portable geometry profile
                      └── Phase 3D native peer build/library

Phase 2D + portable Phase 3A → default-off Phase 3B hybrid showcase

Repaired return path + strict Phase 3A + accepted Phases 3B + 3C + 3D
  → Phase 4 dynamic loader and wall release
      → Phase 5 evidence-driven expansion
```

### Phase 0: Safe and Reproducible Delivery Foundation

Complete the delivery foundation before changing receiver ownership or flashing
a loader-capable baseline. Host-only contract and prototype work may be developed
locally, but no later phase is production-ready until its relevant Phase 0
prerequisite passes.

Implementation status (2026-08-13): **Phase 0 delivery foundation,
coordinator cutover, and Phase 1 contract freeze/portable baseline are complete.
Phase 2A top-level vibe, portable acceptance, and clean wall-deploy gate are
complete. Phase 2B fixed host composition, portable acceptance, and clean
wall-deploy gate are complete. Phase 2C scene/catalog productization, portable
acceptance, and clean wall-deploy gate are complete. Phase 3A portable
implementation and integrated repository gates are complete; a clean degraded
deployment/baseline has passed, while strict all-four status and the restored
one-receiver physical canary remain open. Phase 2D's explicit color-policy
inventory is complete across the unified 53-component selectable catalog, while
semantic palette migration and visual product work remain open. Phase 3B0's
contract, portable runtime, fake wall, batch/ack payload, product lifecycle,
strict readable-receiver runner, and explicitly degraded showcase runner are
complete and both deterministic payload gates are green. The strict physical
Phase 3B canary has now executed on one readable receiver and exposed a
receiver-task scheduling defect after proving compiled local playback. A
dual-core scheduling fix is portable-green but still awaits deployment and a
physical repeat. The receiver feature therefore stays gated; the degraded
four-wall showcase has not run, and there is still no accepted wall-showcase or
foreground timing/expiry evidence**.

- [x] Phase 0A–0B implementation: quiet captured phase logs; explicit clean,
  dirty, plan, and verbose modes; complete source accounting; locked Python
  groups and Pi export; digest-addressed smoke-tested runtimes; PlatformIO
  6.1.19, pioarduino 55.03.39, and native-platform 1.2.1 pins.
- [x] Phase 0C–0D implementation: injectable coordinator, redaction, atomic
  append-only local/remote receipts, advancing fresh-health samples, immutable
  releases, automatic app restoration, and fail-closed app-only rollback.
- [x] Phase 0E policy implementation: complete cache identities and defensive
  probes exist, but no cache or cache store ships. The evidence decision remains
  `insufficient_evidence` until twenty normal successful receipt timings exist.
- [x] Phase 0F portable implementation: exact desired/observed topology,
  distinct plan causality, configuration-only repair, selective common-image
  flashing, one bounded reboot resume, per-device partial-failure evidence, and
  app-activation gating are covered without touching hardware.
- [x] Recipe compatibility gate: `just --dry-run deploy` and
  `just --dry-run deploy-python` each invoke the authoritative executable
  coordinator exactly once; read-only `just deploy-plan` accounts for the dirty
  source and exact coordinator order; the retained shell leaves are reachable
  only through explicit `*-legacy` recovery recipes.
- [x] Legacy wall gate: a full dirty deployment passed the complete precheck,
  found SPI ready, skipped unchanged receiver firmware, reused the selected
  digest runtime without installation, restarted systemd, and reached the API
  health endpoint.
- [x] Coordinator cutover gate: shadow staging, target-side receipts, immutable
  releases, fresh desired-release health, unchanged reconciliation, explicit
  app-only rollback, and an actual clean `just deploy` passed on the Pi.

The coordinator is authoritative under the ordinary `deploy` and
`deploy-python` recipes. Paired append-only local/target receipts, exact
running-release identity in advancing API samples, and automatic restoration at
every post-activation failure boundary are enforced. The legacy shell leaves
remain available under explicit recovery names; their sync protections preserve
`current`, immutable releases, receipts, calibration evidence, and the receiver
library. Phase 0 is closed; do not broaden the retained legacy leaves or add a
deployment cache without the separately defined evidence gate.

Portable evidence on 2026-08-12 (development Mac, not Pi/ESP32 timing evidence):

- `just test`: 580 unit/plugin tests and 860 subtests passed; 18 rendering
  pipeline tests passed; 8 native firmware tests passed; 138 deployment tests
  and 63 subtests passed.
- Deployment coverage includes real filesystem staging/reuse, exact full,
  Python, and rollback step allowlists, failure injection at every
  post-activation boundary, activation-acknowledgement ambiguity, paired receipt
  failure semantics, controller/web/systemd identity disagreement, stale and
  non-advancing health, topology errors, reboot resume, partial flash, and
  unchanged reconciliation.
- The 32 x 138 stress benchmark passed the 4 ms p95 gate; the highest observed
  accepted p95 was 3.615 ms for `snake-max-density` at a 0.62 changed ratio.
- The pinned ESP32-S3 production build used 372,546 bytes flash (5.7 percent)
  and 53,092 bytes RAM (16.2 percent).
- Frozen controller/web import smoke, lock/export equality, Python compilation,
  `bash -n` for every deployment shell helper, and `git diff --check` passed.
- A live dirty-deploy failure exposed the relocation semantics of venv activation
  scripts: the environment was healthy, but activation selected the deleted
  temporary build path. Production startup now invokes the selected
  `venv/bin/python` directly; regression coverage poisons activation and proves
  both processes use the selected runtime, with no system-Python fallback.
- The operator's first live dirty deployment synced the managed source, created
  the digest runtime, and restarted the Pi service; receiver firmware was
  unchanged and correctly skipped. It failed fresh health because of the stale
  activation path described above.
- The corrected full dirty deployment then passed on the wall. It reused runtime
  identity `eed879c054a5c19f470cd12fa00bfd2d8877d6e5ea6787cebecb7f7927d31c97`
  with `installed: false`, again skipped unchanged firmware, restarted the
  service, and reported the web API healthy.
- Coordinator shadow staging froze 1,356 accounted files, rendered all 335
  dashboard previews from the frozen source, staged app release
  `8a85b4800d839e5ee91b316071a26f6dd34ac5c95201d448fdf2659ea01165ab`,
  and left the legacy service, `current`, firmware, and settings untouched.
- Authoritative full canary receipt
  `9487a83f5c93458d954daf2e76ff60a0` selected immutable release
  `9a380babbf33e3008180797e3ff0e301b518dd32c983724ae3d9243cc4c42beb`,
  moved systemd to `current`, restored the Clock state, skipped unchanged
  firmware across four attached serial devices, and accepted two advancing
  exact-release samples at 32 x 138 with logical receivers 0 through 3. The
  local and target receipt files had the same SHA-256 digest.
- Wall retries exposed and then regression-covered three integration faults
  before unsafe mutation: helper lifetime after incoming cleanup, immutable
  support permissions in a writable PlatformIO cache, and absolute snapshot
  paths/unseeded plugin state in preview identity. Two independent full shadow
  renders now produce identical snapshot ID
  `6cb89c5d1c1d95eb5739e420fd27f5cae26dbd88e88f3c6b6cada08a3de09133`.
- Unchanged receipt `97dfa953996446518eaafa70110a5d44` reused app release
  `30e56d99b70b11e489e929eb625bf87d4ba7e628522ecaaa741e9fa27db89c59`
  and the support release; dependency installation, unit/SPI changes, firmware
  build/flash, app activation, service restart, and state restore all skipped,
  while fresh health and the post-health timestamp still ran.
- App-only rollback receipt `ec980c05ab444f14878500796765b8e3`
  passed its exact seven-step allowlist and the round trip returned to release
  `30e56d99b70b11e489e929eb625bf87d4ba7e628522ecaaa741e9fa27db89c59`.
  Firmware-marker and boot-configuration hashes were unchanged across rollback.
- The exact clean operator command `just deploy` passed from commit `9d2ac07`
  with receipt `be50b119fd5948a78112eb6aab7e18e4`: clean source policy,
  no diff or safe-untracked inputs, full precheck, immutable release
  `c9a0cb505314bf62fa5c59d7334e7d2cdf017d0aa7582bddc41541ee713748bb`,
  fresh exact-release health, and a byte-identical local/target receipt. Systemd
  reported both its working directory and startup script through `current`.

#### Phase 0A: Quiet and explicit deployment UX

- Enable quiet `just` behavior and remove traced shell output from ordinary
  success paths.
- Add the captured-log runner and explicit clean, dirty, plan, and verbose source
  modes defined in the delivery architecture.
- Preserve every current helper, command order, protected target path,
  `PI_HOST`, `DEPLOY_DIR`, and compatibility flag.

Acceptance:

- Full and Python-only deployments invoke the same operations in the same order
  as before.
- Success produces no durable terminal noise; failure identifies the phase,
  concise cause, relevant log tail, and log path.
- `deploy-plan` accounts for every selected safe untracked file and every
  exclusion.

Stop before changing remote layout, dependencies, health, provisioning,
firmware, or systemd configuration.

#### Phase 0B: Reproducible dependencies and toolchains

- Lock Python dependency groups and PlatformIO/toolchain inputs as defined above.
- Export the pinned Pi runtime set and build fresh digest-addressed environments
  instead of updating an environment in place.
- Smoke-test controller and web entrypoints before an environment becomes
  activatable.

Acceptance:

- A clean checkout completes frozen setup and every non-hardware gate.
- Supported local and Pi Python identities resolve their intended sets, and a
  fresh Pi environment imports both runtime entrypoints.
- Dependency/toolchain changes alter identity; an unchanged repeat performs no
  installation.

Stop before replacing the existing shell coordinator or target release layout.

#### Phase 0C: Thin coordinator, receipts, and fresh health

- Implement `DeployContext`, `Step`, `StepResult`, `DeployReceipt`, redaction,
  and injectable subprocess/SSH execution.
- Build the existing deployment sequence procedurally over current leaf helpers.
- Persist success, failure, and interruption receipts, and require desired,
  post-restart fresh health before recording deployment success.
- Keep legacy shell entrypoints until coordinator parity passes on the wall.

Acceptance:

- Failure injection covers every phase, fail-fast order, interruption, stale
  health, dirty-source policy, state preservation, and diagnostics.
- Receipts remain atomic and useful after success, failure, and interruption;
  sensitive material cannot appear in commands, receipts, or logs.
- Existing deploy, deploy-dirty, and Python-only operator commands remain
  compatible.

Stop before a provider framework, deployment database/UI, gate cache, release
symlink, or receiver-artifact transaction.

#### Phase 0D: App-only staged releases and rollback

- Stage immutable app releases and generated previews by digest, activate
  through `current`, and keep target-owned/shared state outside releases.
- Validate before activation and automatically restore the previous healthy app
  when candidate health fails.
- Add release listing and explicit app rollback commands.

Acceptance:

- Two releases contain no stale code and preserve presets, `run_state`, logs,
  environments, calibration, firmware, and a receiver-library fixture.
- Injected unhealthy activation returns to the previous healthy API and records
  candidate failure plus restoration.
- Explicit app rollback performs no build, provisioning, reboot, or flash.

Stop before immutable provisioning/firmware releases, whole-system rollback
claims, or automatic release garbage collection.

#### Phase 0E: Measure before gate caching

- Collect at least twenty normal attempt timings through Phase 0C receipts.
- Apply the eligibility, complete-key, corruption, and force-test rules in the
  delivery architecture.

Acceptance:

- Every declared input demonstrably invalidates the candidate entry; corrupt or
  missing state reruns safely; receipts distinguish executed, cached, and skipped
  work.
- If no deterministic local gate regularly costs at least five seconds or saves
  meaningful time, close the phase by shipping no cache.

This optional optimization never blocks Phase 0F and never caches external or
physical acceptance.

#### Phase 0F: Robust automatic provisioning and firmware

- Reconcile desired/observed host and firmware state, resume bounded reboot
  phases idempotently, build before downtime, and flash the common image only to
  receivers whose firmware identity differs while reconciling logical identity
  as provisioned state.
- Fail on missing, duplicate, unexpected, or failed receivers. On partial flash,
  preserve evidence and require explicit recovery rather than activating the app
  or claiming rollback.
- Activate the staged app only after firmware success, then apply app-only health
  restoration if required.

Acceptance:

- An unchanged deployment performs no provisioning, reboot, dependency work,
  release activation, or flash.
- Package, SPI, unit, dependency, app, and firmware changes produce distinct,
  accurate plans.
- Automatic reboot resumes once without looping; partial firmware failure is
  explicit per device and never records success.
- Full deployment updates the API timestamp only after fresh readiness.

Stop before receiver-background packages or generic grouped activation. The
receiver-native domain adds its own transaction only after the static-background
and hardware gates prove the real semantics.

### Phase 1: Freeze Contracts and Baseline Evidence

Define byte, state, and timing contracts without changing visible behavior.

Implementation status (2026-08-12): **complete**.

- [x] Frozen v1 descriptor, scene, vibe/profile, desired-display,
  runtime-context, BaseFrame/OverlayFrame, cadence, timing-adapter, coordinate,
  fixed-point alpha, rollout-flag, ownership, failure, and dormant foreground
  wire contracts in
  [ANIMATION_PIPELINE_CONTRACT_V1.md](ANIMATION_PIPELINE_CONTRACT_V1.md).
- [x] Added one generated language-neutral golden fixture for alpha endpoints,
  black/transparency, rounding, saturation, opacity, ordered folds, dirty
  movement/clear, canvas/logical coordinates, all four receiver boundaries, and
  the exact two-patch receiver snapshot. Python and portable C++ tests enforce
  the fixture contract without wiring it into live behavior.
- [x] Classified all 50 then-shipped plugins reproducibly in
  [animation-plugin-compatibility-inventory.md](animation-plugin-compatibility-inventory.md):
  49 ordinary Python backgrounds, the current Clock as the sole compatibility
  full scene, and no direct-hardware/stateful packages. Phase 2B adds the
  separately classified `clock_overlay`, bringing the current generated total
  to 51.
- [x] Captured deterministic installed-geometry Clock cadence, latency,
  changed-frame, derived dirty-pixel/range, preview, and payload evidence in
  [clock-phase1-baseline.md](clock-phase1-baseline.md), with workstation timing
  separated explicitly from retained Pi/receiver identities and physical gates.
- [x] Introduced all six rollout flags as a strict immutable all-off reference
  object. Current manager/API/persistence/preview/receiver code does not import
  or consume it, preserving the Phase 1 no-op boundary.
- [x] Final portable gate: `just test` passed 619 Python unit/plugin tests and
  904 subtests, 18 rendering pipeline tests, 20 native firmware tests, the
  pinned production firmware build, and the complete deployment test suite.
  The highest accepted 32 x 138 stress p95 was 3.6417 ms for
  `snake-max-density` at a 0.62 changed ratio. The ESP32-S3 build retained the
  existing 372,546-byte flash payload and 53,092-byte RAM usage.
- [x] Deployment continuity: `just --dry-run deploy` and
  `just --dry-run deploy-python` still resolve to one authoritative clean-policy
  coordinator invocation; `just deploy-plan` accounts for the new app and
  firmware contract source and retains the complete ordered reconciliation
  sequence. No deployment or receiver flash was performed for this contract-only
  phase.

- Inventory every current plugin as ordinary background, compatibility full
  scene, or unsupported direct-hardware/stateful component.
- Write the versioned descriptor, scene, vibe, runtime-context, BaseFrame,
  OverlayFrame, fixed-point alpha, receiver ownership, and failure-state
  contracts.
- Add shared Python/C++ golden vectors for transparent black, opaque black,
  alpha endpoints, rounding, channel saturation, dirty-union movement, and
  complete clear, opacity, and ordered two-overlay folding.
- Freeze logical strip-major layout and add corner, strip-offset, board-boundary,
  and canvas-to-logical fixtures.
- Define protocol command IDs, controller-session/revision/generation widths and
  ordering, compare-and-swap/idempotency rules, exact header/CRC sizes, maximum
  RGBA pixels per patch, and multi-patch full-snapshot fixtures, but do not enable
  firmware behavior.
- Characterize the existing Clock's normal and animated cadences, dirty pixel
  count/range fragmentation, and desktop render p50/p95/p99/max at 32 x 138.
- Record current full-frame bytes/rate, manager changed-frame ratio, preview
  behavior, deployment preservation, Python tests, rendering benchmark, firmware
  portable tests, and retained validated firmware identities.
- Introduce feature flags defaulting off:
  `vibe_context`, `scene_layers`, `receiver_local_background`,
  `receiver_sparse_overlay`, `receiver_geometry_profile`, and
  `receiver_native_modules`.

#### Acceptance

- Every current plugin has one documented compatibility classification.
- Python and C++ alpha/reference vectors are byte-identical.
- Coordinate and board-slicing fixtures are byte-identical across Python and
  firmware, including all four receiver offsets.
- State transition tables identify which commands can change base ownership,
  foreground state, maintenance, and output state.
- Baseline tests and benchmarks complete with dimensions and machine recorded.
- No runtime output, API schema, persisted state, or receiver command behavior
  changes while all flags are off.

#### Stop boundary

Do not introduce scenes, vibe behavior, firmware modes, or a generic provider
framework in this phase.

### Phase 2A: Top-Level Vibe on the Existing Python Pipeline

Deliver useful vibe switching before composition or receiver-native playback.

Implementation status (2026-08-12): **complete**. Three parallel,
contract-first lanes converged: core
vibe/runtime and timing adapters; API, persistence, preview, dashboard, and
deploy-state integration; and four pilot capability mappings with adversarial
acceptance coverage. Focused manager/pilot/product tests, the deployment suite,
and the installed-geometry render gate pass. The final actual clean
`just deploy` also passed with restored display state and fresh wall health.

- [x] Immutable versioned vibe state, five-profile registry, deterministic
  digest, manager-owned revision, explicit neutral fallback diagnostic, status,
  API, IPC, persistence, deploy restore, and one global dashboard selector.
- [x] Authored/effective parameter separation, continuous scaled clock, all
  three timing adapters, presentation-only cache hook, and framework-owned
  grade/luminance applied once after plant optics.
- [x] Plain and parameterized preview isolation, preset identity preservation,
  and hidden mapped authored values retained across preset changes.
- [x] Clock, Lava Lamp, Snake, and Simple Test pilots cover semantic, grade, and
  preserve color policies; wall-clock and scaled-context timing; semantic and
  legacy palette bridges; seeded procedural/game parity; and diagnostic exact
  color preservation.
- [x] Adversarial pilot acceptance, manager timing/presentation acceptance, real
  local-dashboard round trips, deployment preservation, manifest validation,
  and the 4 ms p95 installed-geometry render gate pass.
- [x] Final full portable regression/firmware/build suite passes at installed
  geometry and the final pre-deploy source identity.
- [x] An actual clean wall `just deploy` passes at the committed Phase 2A source
  identity.

Portable evidence on 2026-08-12 (development Mac, not Pi/ESP32 timing evidence):

- `just test` passed 647 Python unit/plugin tests and 924 subtests, 18 rendering
  pipeline tests, 20 native firmware tests, the pinned production ESP32-S3
  build, and 145 deployment tests with 66 subtests.
- The highest accepted direct-plugin 32 x 138 stress p95 was 3.2551 ms for
  `snake-max-density` at a 0.62 changed ratio. The full manager Snake vivid
  grade+luminance path measured p50 0.0712 ms, p95 0.5293 ms, p99 0.5435 ms,
  and max 0.6256 ms; a transform on every changed frame measured 0.0828 ms p95.
- The production firmware remains unchanged at 372,546 bytes flash and 53,092
  bytes RAM. Both ordinary deploy dry runs still resolve to one clean-policy
  coordinator invocation.
- Actual clean `just deploy` passed for commit `f8aad7b` with deployment receipt
  `ae97abd085d04ec283ea196bcd0bf233`. The coordinator activated immutable release
  `188411799ec10630f30b025d17127fc27a1abab0c2dc4d8f55c39a4d7bb02d5c`,
  skipped unchanged receiver firmware, restored the running Gradient snapshot,
  and observed the desired release on both web and controller across two stable
  fresh-health samples at 32 x 138 with all four receivers.

- Add validated immutable `VibeState`, central profile registry, API/status,
  persistence, preview input, and one global dashboard control.
- Persist the versioned vibe alongside the existing single-animation snapshot in
  this phase. Phase 2C folds that already-versioned value into
  `DesiredDisplayState` through a legacy-scene adapter; it does not postpone vibe
  restart/deploy survival.
- Replace authored-speed mutation with explicit authored versus effective state.
  Implement the `legacy_speed_param`, `scaled_context`, and `wall_clock` adapters
  and keep compatibility behavior stable for current global tempo.
- Add presentation-context update hooks that cannot reset or advance simulation.
- Implement framework-owned vibe luminance once in the host render pipeline.
- Add semantic palette roles and component capability declarations.
- Pilot Clock, one procedural family, one stateful/game plugin, and one
  preserve-color asset/diagnostic plugin.
- Add manifest-local legacy mappings for selected existing `mood` and `palette`
  parameters.

#### Acceptance

- Vibe selection survives restart, deploy preservation, plain and parameterized
  previews, preset changes, and generic live updates.
- Vibe changes do not dirty component presets or alter selected preset identity.
- Paired seeded runs under presentation-only vibes have identical logical state,
  RNG state, and event history.
- Clock wall time is unaffected by tempo; every pilot claiming palette or tempo
  support changes visibly and every preserve-color pilot retains exact color.
- Unknown IDs/versions reject or fall back to `neutral` with observable status.
- Current Python tests and rendering gates remain green with `neutral` byte
  compatible wherever the contract declares compatibility.
- Representative legacy plugins prove authored speed, vibe tempo, and operator
  tempo are each applied exactly once; context-native and wall-clock pilots prove
  their distinct timing contracts.

#### Stop boundary

Do not rewrite all palettes, add scene presets, or send vibe to firmware yet.

### Phase 2B: Fixed Host-Side Background and Clock Composition

Prove layer semantics entirely on the Pi.

Implementation status (2026-08-13): **complete**. Three contract-first,
non-overlapping lanes delivered reusable
fixed-point host composition and dirty-coverage semantics; shared clock-face
helpers plus the transparent `clock_overlay` component; and manager-owned
lifecycle, semantic-cadence caching, preview, transport, and benchmark
integration. Cross-review also added run-generation/controller-I/O ownership so
a blocked old presentation cannot rejoin a restarted scene. Portable gates and
an actual clean wall deployment both pass. Phase 2B remains host-only: the existing
complete RGB transport is authoritative and no firmware protocol, dashboard
product surface, scene persistence, or receiver ownership changes are in scope.

Portable closure requires focused deterministic layer and lifecycle tests; the
complete Python, rendering, deployment, portable-firmware, and production-build
gates; installed-geometry scene benchmarks reporting p50/p95/p99/max, changed
ratio, overlay coverage/ranges, and observed presented RGB payload bytes;
ordinary deploy
dry-run and source-plan continuity; and an actual clean `just deploy` with fresh
wall health. Workstation results remain pre-hardware evidence and cannot close
the final deployment gate by themselves.

- [x] Add a manager-owned compositor with reusable output buffers, independent
  component clocks/counters, and semantic-cadence caching under manager polling.
- [x] Add `OverlayFrame` and a separate `clock_overlay` plugin package using shared
  Clock layout/glyph/time helpers.
- [x] Retain the existing Clock as a compatibility full scene and preserve its
  curated presets.
- [x] Support exactly one Python background plus one aggregate foreground plane and
  source-over alpha.
- [x] Implement previous/new coverage union, cached base and overlay frames,
  independent lifecycle, targeted interaction routing, and scene preview.
- [x] Move the current universal framework plant-optics invocation to the composed
  output path for composed scenes. Prove it is applied exactly once and retain
  background-only compatibility.
- [x] Initially flatten to the existing authoritative RGB path so firmware and
  transport remain unchanged.
- [x] Extend benchmarks with compositor cost, scene changed ratio, overlay dirty
  pixels/ranges, and bytes observed at the manager/controller presentation
  boundary. Receiver/SPI byte counters remain physical-deployment evidence.

Portable evidence on 2026-08-12 (development Mac, not Pi/ESP32 timing evidence):

- Focused host-compositor, manager, Clock/overlay, manifest, frozen-contract,
  inventory, and legacy-product acceptance passed 113 tests plus 300 subtests;
  the later real three-background preview/live addition also passes in the full
  suite.
- `just test-unit` passed 689 Python unit/plugin tests and 969 subtests.
  Coverage includes transparent/opaque black, per-fold rounding, clipping and
  ordered overlap, previous/new clear coverage, exact legacy Clock preset
  hashes, second/minute cadence, exact-once optics/luminance, legacy boundary
  rejection, manager-global plant authority, and a blocked-controller restart
  proving one presentation owner and stable frame-N bytes.
- `just test-rendering` passed 18 frame/SPI tests and every installed-geometry
  default/stress/scene 4 ms p95 gate. The highest direct stress p95 was 3.2966
  ms for `snake-max-density`. Gradient, Aurora Curtains, and Sparkle with Clock
  Overlay measured 0.3948, 0.7424, and 0.6160 ms p95 respectively; each observed
  one overlay revision in 100 simulated 200 Hz polls and 180 dirty pixels across
  32 ranges at rollover.
- Observed RGB payload at the manager/controller boundary was 13,788 bytes for
  static Gradient (one 13,248-byte full frame plus one 540-byte partial), 199,260
  bytes for Aurora Curtains, and 1,324,800 bytes for continuously changing
  Sparkle across the canonical 100-frame run.
- All 20 portable firmware tests and the pinned production ESP32-S3 build passed;
  firmware remains unchanged at 372,546 bytes flash and 53,092 bytes RAM.
  The deployment suite passed 145 tests plus 66 subtests.
- `git diff --check`, Python compilation, compatibility-inventory regeneration,
  both ordinary deploy dry runs, and the authoritative dirty source plan pass.
- Actual clean `just deploy` passed from commit `8791d1a` with deployment receipt
  `d7b2315d780341dca5b3a5be5d29cf4e`. The coordinator ran the complete local
  gate, staged and activated immutable app release
  `2d10f0edb109f1f0b4e08a3995f104887dc87449e91afdc2c5f53700642eda40`,
  skipped unchanged receiver firmware and host provisioning, restored display
  state, and observed that exact desired release across two stable fresh-health
  samples at 32 x 138 with all four receivers.

#### Acceptance

- The clock renders over at least three representative backgrounds without
  restarting them or adding a background advance outside the ordinary manager
  tick when the clock changes.
- Transparent and opaque black, movement, removal, enable/disable, opacity,
  minute/second rollover, and plant-aware placement leave no stale pixels.
- Translation/clipping and two ordered overlapping logical overlays match the
  canonical layout, opacity, and per-fold rounding vectors even though the
  receiver-facing foreground is aggregate.
- A changing base recomposites stable foreground coverage correctly.
- Universal plant optics are applied once after composition; disabling them
  preserves the existing background-only frame byte for byte.
- Cached calls return `changed=False`; at a 200 Hz manager poll rate the 1 Hz
  clock produces only one content revision/render per wall-clock tick.
- Combined generation and composition preserve the 4 ms p95 host-render gate at
  installed geometry for accepted scenes, with p99/max and changed ratio
  reported separately.
- Existing single-animation commands and presets still select a background-only
  compatibility scene.

#### Stop boundary

Do not add arbitrary graphs, multiple firmware planes, additive blend, native
clock execution, or receiver protocol changes.

### Phase 2C: Scene State, Unified Catalog, UI, and Persistence

Productize the host composition contract before adding another execution backend.

Implementation status (2026-08-13): **complete**. Work landed through three
parallel lanes: versioned descriptors and compatibility discovery; versioned
scene state and manager lifecycle; and IPC/API/dashboard/persistence
productization. Phase 2C remains host-only and preserves the complete-frame
transport, legacy animation/preset API, painter isolation, and clean deployment
path.

- [x] Versioned component descriptors scan without importing implementations,
  expose one provider/role-filterable catalog, and retain explicit compatibility
  metadata for legacy Python packages and stateful/full-scene components.
- [x] Versioned scene state validates and round-trips background, fixed overlay
  slot, component parameters/preset identity, placement, opacity, stale policy,
  and known Python fallback without capturing vibe or operator output state.
- [x] Live scene lifecycle supports targeted background/overlay updates,
  enable/disable/removal, status, and preview without restarting the unaffected
  background or mutating the live scene during preview.
- [x] IPC/API and the dashboard expose the unified catalog, fixed scene editor,
  component presets, scene presets, explicit compatibility diagnostics, and
  independent vibe controls while rejecting unsupported providers and roles.
- [x] Before-deploy preservation and restart restoration accept both legacy
  single-animation snapshots and the new desired-display/scene shape, validate
  before mutation, and fall back to the recorded Python component on incompatible
  schema/provider state.
- [x] Focused contract, lifecycle, web/IPC, persistence, migration, and negative
  tests pass together with the full Python, rendering, deployment, portable
  firmware, pinned production-build, dry-run, source-plan, and clean wall deploy
  gates required by the inherited Phase 2B closure standard.

Portable evidence on 2026-08-13 (development Mac, not Pi/ESP32 timing evidence):

- `just test` passed 722 Python unit/plugin tests and 990 subtests, 18 rendering
  pipeline tests, all 20 native firmware tests, the pinned ESP32-S3 production
  build, and 148 deployment tests plus 66 subtests.
- The production firmware remained unchanged at 372,546 bytes flash and 53,092
  bytes RAM. `git diff --check` and Python compilation passed.
- Every installed-geometry default, stress, and scene benchmark passed the 4 ms
  p95 gate. The highest accepted p95 was 3.639 ms for `snake-max-density`.
  Gradient, Aurora Curtains, and Sparkle with Clock Overlay measured 0.4575,
  0.8099, and 0.7583 ms p95 respectively; each rendered the overlay once in 100
  simulated 200 Hz polls and reported 180 dirty pixels across 32 ranges.
- Both ordinary deployment dry runs invoked the authoritative coordinator once.
  The full dirty source plan accounted for the new catalog, scene contracts,
  shared runtime adapter, API/UI, tests, and documentation.
- Actual clean `just deploy` passed from commit `6550f59` with deployment receipt
  `24033c7bb11444deaf762bcd47c78917`. The coordinator staged and activated app
  release `23d55113d9b62341e84760281502b8d68e6c6f7d3937c01fc4c07b1743be5dd4`,
  reused the pinned runtime and support release, skipped unchanged provisioning
  and receiver firmware, captured and restored the Gradient desired display,
  and accepted two fresh stable exact-release health samples at 32 x 138 with
  all four receivers. Read-only live probes then returned the versioned unified
  catalog with `clock_overlay` as the sole selectable Python overlay and the
  restored background-only Gradient `SceneState` from `/api/v1/scene`.

- Add versioned `SceneState`, component/scene preset separation, targeted live
  updates, manager status, IPC/API commands, and fixed-slot dashboard controls.
- Refactor plugin discovery into descriptor scanning plus the current Python
  loading adapter. Existing manifests retain a compatibility path.
- Generalize Phase 2B's fail-closed explicit Python provider/role/entrypoint/
  cadence subset into versioned descriptors and the unified catalog, without
  introducing a generic lifecycle DSL.
- Store selected scene independently from vibe and operator output state.
- Update before-deploy preservation, restart restoration, and preview cache keys.
- Expose one unified component catalog filterable by provider and role.
- Add explicit compatibility handling for `StatefulAnimationBase` and painter
  mode rather than allowing either to compose accidentally.

#### Acceptance

- Current animation/preset API consumers continue to work through compatibility
  translation.
- Scene presets round-trip background, overlays, parameters, placement, and stale
  policy without capturing vibe.
- Switching or removing an overlay never restarts the background.
- Preview/live state use the same scene and vibe resolution rules.
- Deploy restore handles both legacy single-animation and new scene snapshots.
- Descriptor/preset tests reject invalid provider, role, ID/path mismatch,
  unsupported combination, and undeclared controls.

#### Stop boundary

Support only Python providers in live scenes. Do not build a generic artifact
provider or receiver transaction abstraction before a receiver backend exists.

### Phase 2D: Semantic Palette and Hybrid UX Benefit Rollout

Turn the accepted vibe, scene, catalog, preview, and persistence foundations into
an obvious everyday product benefit while the receiver return path is awaiting
repair. This host/product lane depends only on Phase 2C and may run in parallel
with portable Phases 3B and 3D.

Implementation status (2026-08-13): **explicit color-policy inventory complete;
semantic migration and visual product rollout remain open and are authorized
under the temporary SPI1 return-path constraint**. Phase 2A proved the contract
with four pilots and Phase 2C shipped the global vibe control and scene editor.
Every selectable component now exposes an explicit policy without silently
enabling any presentation behavior. This phase still needs to expand the visible
benefit deliberately rather than treating the pilot mappings or classifications
as the finished palette product.

- [x] All 51 shipped Python packages declare `semantic`, `grade`, or `preserve`;
  Painter and the compiled receiver-static component make the unified generated
  inventory 53 components: 39 grade, 12 preserve, and 2 semantic.
- [x] The 46 newly classified packages retain zero vibe capabilities, mappings,
  or semantic roles. A 32 x 138 render-parity gate proves neutral versus vivid is
  byte-exact and leaves authored parameters unchanged until migration explicitly
  opts a component in.
- [ ] Convert palette-capable procedural families, review neutral/non-neutral
  visuals and curated presets, produce contact-sheet evidence, and finish the
  provider/role product presentation described below.

- Classify every shipped component as `semantic`, `grade`, or `preserve` color
  policy and make the policy visible in its descriptor. Preserve-color assets,
  flags, diagnostics, and calibration components are complete when they declare
  and prove preservation; they must not be recolored merely to satisfy a count.
- Convert every palette-capable procedural family to semantic roles or a
  documented temporary legacy bridge. Prioritize the most-used duplicated
  palette tables, then remove a bridge only after its curated presets pass
  neutral and non-neutral visual comparison.
- Keep vibe one global control. Suppress plugin-local palette, mood, saturation,
  or value controls only when their authored meaning is fully represented; do
  not silently discard a preset parameter or mutate selected preset identity.
- Extend the fixed scene editor with a feature-gated receiver-static background
  choice and the existing Python `clock_overlay`. Show provider/role badges,
  receiver agreement or degraded-return status, and an explicit **preview** label
  because there is no receiver framebuffer readback.
- Preview the compiled receiver background with the same resolved semantic role
  bytes, luminance, cadence, global offsets, fixed-point overlay blend, and scene
  layout used by the wire contract. Vibe and overlay edits must not mutate the
  live wall until the operator starts the candidate scene.
- Preserve desired scene, vibe, component parameters/presets, known Python
  fallback, and output controls through restart, deployment, and app rollback.

#### Acceptance

- A generated inventory proves every shipped component has one explicit color
  policy and every claimed vibe capability changes visibly under at least one
  non-neutral profile; preserve components remain byte-exact apart from separately
  declared luminance support.
- Paired seeded runs across every migrated stateful component retain identical
  logical state, RNG state, event history, and source cadence under different
  presentation-only vibes.
- Every curated preset for a migrated family validates, renders, and retains its
  selected identity. A labeled 32 x 138 contact sheet covers neutral plus every
  canonical vibe for representative migrated families and the hybrid scene.
- Dashboard, API, IPC, preview, desired-display persistence, deploy preservation,
  and fallback tests cover both a Python scene and the feature-gated receiver
  background plus clock overlay without exposing unsupported arbitrary uploads.
- Neutral remains compatible with the accepted authored baseline, default/stress
  paths remain inside the 4 ms host generation/composition p95 gate, and the
  feature-disabled path is behaviorally unchanged.

#### Stop boundary

Do not turn vibe into a generic theme system, recolor preserve-policy content,
add provider-specific galleries, or enable a receiver-local feature by default.
Palette productization may finish before the MISO repair; receiver acceptance may
not.

### Phase 3A: Explicit Receiver Ownership and Statically Linked Background

Establish safe receiver-local playback without packages, cache, or dynamic code.

Implementation status (2026-08-13): **portable implementation and integrated
gates complete; degraded deployment and streamed evidence pass; strict physical
gates remain open**. Three parallel contract-first lanes landed portable/live
receiver ownership and the static canary; backward-capable Pi protocol/status
plus deterministic fake four-board orchestration; and generated language-neutral
presentation-context/luminance vectors. The full repository regression suite,
native firmware tests, both pinned production and canary builds, deployment
recipe checks, focused host/orchestration tests, and cross-language vectors pass.
The ordinary production image keeps receiver-local playback disabled. A clean
dedicated-key deployment and the explicitly degraded status, dense-streamed, and
animation-sweep gates have passed. Completion still requires repaired return
telemetry, strict all-four status/streamed acceptance, and the deliberately
restored one-receiver physical canary followed by strict streamed reacceptance.

- [x] Explicit orthogonal receiver ownership replaces implicit first-command
  takeover while preserving the complete-host-frame kill path and all legacy
  streamed behavior when receiver-local playback is disabled.
- [x] Status v3/capabilities, static-background commands, staged presentation
  context, fixed-point luminance, cadence, transition, and failure contracts are
  byte-exact across firmware and Pi implementations.
- [x] The compiled rainbow supports explicit start, stop, live parameters,
  fallback, restart, global offsets, common seed/epoch, and bounded declared
  cadence without packages, cache, geometry profiles, or dynamic loading.
- [x] Portable firmware and host tests cover the full command-ownership matrix,
  malformed/bounds cases, old status versions, takeover, fallback, cadence, and
  four-board mixed-context/partial-failure rejection.
- [x] Full Python/rendering/deployment regressions, native firmware tests, both
  pinned firmware builds, and deployment recipe compatibility pass together.
- [x] A clean dedicated-key `just deploy` completes against the wall, with exact
  release health and scene restoration; the separately named degraded status,
  dense streamed-frame baseline, and full animation sweep pass on the known
  readable/write-only topology without pretending telemetry is complete.
- [ ] After the wire repair, fresh strict Phase 3A status proves all four
  receivers remain on the production feature-off image and the strict dense
  streamed-frame baseline plus animation sweep pass without receiver integrity,
  cadence, or takeover regressions.
- [ ] Exactly one deliberately flashed receiver passes the local-background
  disconnect/live-update/host-takeover canary, is restored to the production
  image, and the full streamed baseline passes again before Phase 3A closes.

Temporary installed-wall return-path policy (2026-08-13): the confirmed SPI1
MISO-to-MOSI short is a physical constraint, not a software compatibility case.
The strict all-four `receiver-phase3a-status` and streamed acceptance commands
remain unchanged and deferred until repair. A separately named
`receiver-phase3a-status-degraded-spi1` records strict SPI0 proof plus the exact
known SPI1 no-return state. Then
`receiver-streamed-wall-acceptance-degraded-spi1` may be used only for the
feature-off production streamed path, followed by the separately named
`live-animation-sweep-degraded-spi1`. They require full v3 identity, capability,
integrity, timing, and accounting acceptance on readable logical receivers 0
and 1; logical receivers 2 and 3 must both match the exact known status-v0,
no-status, no-capability, no-identity state and show advancing host frame,
transfer, and byte counters with zero new host SPI errors. Its JSON is required
to name the temporary degraded policy, set `telemetry_complete: false`, list
receivers 2 and 3 as write-only, state that receiver/display proof is absent,
and require visual inspection of every SPI1 lane. This is not evidence that either
write-only receiver accepted or displayed a frame.

Strict live sweep acceptance requires all four return paths and never silently
skips a receiver; only the explicit degraded sweep applies this temporary pair.

This policy does not waive or close any MISO-dependent gate. The single-receiver
local-background canary must target a readable receiver with strict v3 command
acknowledgements. All-four identity/capability proof, receiver telemetry for
logical receivers 2 and 3, strict SPI1 local-background/foreground/profile proof,
verified uploads, native-module activation, transactional compensation, and
production enablement remain backburnered until the short is repaired and the
strict all-four gates pass.

The fault does **not** backburner Phase 2D, portable Phase 3B/3C/3D work, fake
four-board failure coverage, strict one-receiver work on logical receiver 0 or 1,
or an explicit operator-started degraded hybrid showcase. A degraded showcase
may write the same idempotent context and foreground generations to all four
receivers, require full acknowledgement and timing proof from 0 and 1, require
host transfer/error evidence plus visual inspection for 2 and 3, and always keep
complete RGB takeover available. It must report `telemetry_complete: false`, may
not install or mutate cached native artifacts, may not survive as an automatic
startup default, and is demonstration evidence rather than release acceptance.

Portable/integrated evidence on 2026-08-13:

- Dedicated-key deployment from clean commit `5965951` completed every
  coordinator step against `192.168.1.62`, activated immutable release
  `b7777df24c4490c3ba43e498df78924c5f8bd401a7383314a1368233f5e7aad8`,
  restored the prior scene, and passed stable health acceptance with four host
  devices. The explicit degraded status gate then passed with v3 identity and
  capabilities on logical receivers 0 and 1 and the exact known no-return state
  on 2 and 3. This closes the deployment/SSH failure, not the deferred strict
  all-four MISO gate.
- The first target-200 dense diagnostic preserved and restored scene/FPS state
  but correctly failed its obsolete 180 FPS threshold at 155.31 displayed FPS.
  An output-rate sweep measured 120.8, 145.13, 156.0, 163.4, and 162.73
  displayed FPS at targets 120, 140, 160, 180, and 200 respectively, with zero
  readable-receiver CRC/display errors and exact cleanup. A Pi microbenchmark
  also found inherited `hue_shift=0.5` made Rainbow generation about 22 times
  slower than neutral. Dense acceptance now verifies neutral plant optics and
  restores the exact prior modifier state, and the installed release gate uses
  the measured target-160/minimum-150 envelope.
- The first degraded live sweep exercised the complete plugin registry with
  zero reported host or readable-receiver integrity deltas, then failed because
  its new write-only policy incorrectly required outbound progress from cached
  static plugins. The corrected evaluator requires equal host frame deltas on
  all four lanes, including a valid all-zero delta, while dense acceptance still
  requires positive traffic. Exact scene restoration succeeded on the failed
  run; the corrected sweep remains to be repeated after deployment.
- The second degraded live sweep restored the exact prior scene and reported no
  host or readable-receiver integrity failure, but three plugins observed a
  one-frame per-device counter skew (for example `[311, 312, 311, 311]`). Metrics
  may sample while the parallel SPI workers are mid-presentation, so the sweep
  now accepts at most one in-flight frame of host-counter spread and fails a
  spread of two or more. This bounded correction has explicit regression tests;
  the sweep remained open until repeated.
- The first target-160 rerun exposed a benchmark denominator defect: 9,156
  accepted/displayed frames were divided by 62.643 seconds even though the
  counter snapshots covered roughly the requested 60-second interval. Initial
  metrics-request and trailing-sleep time were incorrectly included. Acceptance
  now runs until the monotonic first-to-last sample interval itself reaches 60
  seconds and evaluates that exact counter window; cleanup remained exact on
  the failed run.
- Final clean commit `87bf91a` deployed through ordinary `just deploy` with the
  dedicated key and activated immutable release
  `21b95fd5878e79ab069249de89432a5929ce2e5f404fc9f2bdbb4b836a02be49`;
  health readiness observed the same release in two stable samples. Degraded
  status again passed the exact readable 0/1 and write-only 2/3 topology. The
  corrected 60-second streamed gate passed on both readable receivers at 154.53
  displayed FPS: 9,403 accepted, 9,403 displayed, zero superseded or integrity
  errors, encode p95 at most 494 us, and show p95 4,442 us. Plant modifiers,
  target FPS, and the prior scene all restored exactly. The final degraded live
  sweep passed all 50 registered animations with zero failed plugin or host SPI
  error delta and restored the prior `living_stained_glass` scene. Telemetry is
  still explicitly incomplete and visual verification is still required for
  receivers 2 and 3; none of this closes the strict MISO-dependent gates.
- The installed dense target-200 saturation run reported receiver encode p95
  490 us, display p95 4,442 us, and 9,362 accepted plus 9,362 displayed frames
  over 60.279 seconds (155.31 FPS), with no superseded frame or integrity error.
  One 8 x 138 SET_ALL packet is 3,315 bytes and takes 1,326 us at 20 MHz; the
  nominal WS2812 output is 4,440 us. The measured rate agrees with the current
  effective SPI-plus-encode-plus-display budget, so the installed full-frame
  release gate now targets 160 FPS and requires 150 FPS. Target 200 remains an
  output-rate saturation characterization rather than a production claim.
- After encoding the explicit degraded-return-path policy and fixing documented
  Just argument normalization, `just test` passed 834 Python tests and 1,119
  subtests, 21 rendering tests and 3 subtests, all 44 native firmware cases,
  both production and canary ESP32-S3 builds, and 153 deployment tests plus 66
  subtests. The slowest accepted installed-geometry p95 was 3.366 ms, below the
  4 ms gate. Strict recipes remain strict; the three separately named degraded
  recipes are the only temporary SPI1 exception.

- `just test` passed 800 Python tests and 1,105 subtests, 21 rendering tests and
  3 subtests, 43 portable firmware cases, and 148 deployment tests and 66
  subtests. The slowest accepted installed-geometry p95 was 3.266 ms, below the
  4 ms gate.
- ESP-IDF 5.5.4 built both the production feature-off image and named feature-on
  canary from the `elf_loader` 1.3.2 pinned baseline with loading disabled.
  Compile evidence reports `LEDGRID_ENABLE_LOCAL_BACKGROUND=0` and `=1`
  respectively. Each image uses 270,709 bytes flash and 50,688 bytes RAM.
- Generated JSON and C++ presentation fixtures are drift-checked. Focused tests
  cover exact wire bytes/digests, old status versions, stale acknowledgement
  rejection, a shared-time fake four-board bound of at most 5 ms, partial-failure
  compensation, render/SET_ALL and geometry/brightness interleavings, nonwrapping
  operation-sequence exhaustion, and queued fresh-status proof.
- The physical canary runner requires an explicit SPI address and logical ID,
  closes and reopens the controller for a default 60-second disconnect window,
  verifies exact binding/cadence/scene-time progress with zero new receiver
  faults, post-verifies live parameters, and always attempts a complete black
  host-frame takeover in `finally`. It never flashes firmware or manages the
  service.
- Clean `just deploy` attempt `20260813T155129.914376Z` reran the full local gate
  successfully, then stopped at the non-mutating `target.connect` step because
  `ledgridwall.local` did not resolve. The neighbor-table address
  `192.168.1.62` also timed out over HTTP and reported `Host is down` over SSH.
  No release, service, firmware, or wall state changed; deployment, streamed
  acceptance, and the restored one-receiver canary remain open stop gates.
- Closure resumed from clean commit `67d2b3e` on 2026-08-13. The active gate
  order is clean deployment and fresh feature-off status, dense streamed
  acceptance, one-receiver canary with mandatory complete-host-frame cleanup,
  production-image restoration, and a final streamed acceptance rerun. At that
  point Phase 3B was treated as wholly blocked; the updated plan now preserves
  that sequence for strict release closure while allowing the portable and
  explicitly degraded Phase 3B0 showcase defined below.
- The resumed read-only wall probe reached `ledgridwall.local` and confirmed
  logical receivers 0 and 1 at status v3 with the expected feature-off
  capability set and IDs. Logical receivers 2 and 3 returned no usable status or
  identity on the SPI1 return path. This was H0 evidence for the already
  documented MISO/MOSI fault, so strict streamed and local-background physical
  gates could not pass until that electrical
  path is repaired and all four fresh identities are observed.
- The resumed code audit found that beginning a replacement presentation context
  temporarily hid the committed context from the receiver clock before COMMIT.
  `active_context_present` now keeps the prior scene-time anchor advancing
  through `Staging` and `Ready`, then switches atomically at COMMIT; a native
  regression covers the complete replacement sequence. The audit also locked
  ordinary deployment to the feature-off PlatformIO environment with a
  behavioral target-build test.
- Physical acceptance tools now snapshot and verify restoration of the exact
  prior scene, and the output-rate sweep also restores its exact prior manager
  cadence. Preflight identity/capability rejection in the single-receiver
  canary is observation-only: it does not configure the addressed board or send
  a black takeover frame. Focused cleanup tests cover active and idle restore,
  asynchronous observation, timeouts, body failures, and cleanup failures; a
  canonical all-four dense streamed recipe removes the former device-0-only
  default from the full-wall gate.
- The final recipe audit caught a command-boundary regression in documented
  trailing arguments: Just passed `duration=60`, `target_fps=160`, and
  `seconds=2` literally to numeric Python options. The receiver acceptance,
  streamed-wall, live-sweep, and output-rate recipes now normalize their named
  prefixes while preserving defaults and positional calls. Focused tests run
  the exact Just recipes with `uv` replaced by an argv recorder, proving the
  command boundary without importing the tools or contacting the wall.
- Final settled-source portable gate on 2026-08-13: `just test` passed 813
  Python tests and 1,113 subtests, 21 rendering tests and 3 subtests, all 44
  native firmware cases, both pinned ESP32-S3 builds, and 149 deployment tests
  plus 66 subtests. The slowest accepted 32 x 138 p95 was 3.625 ms for
  `snake-max-density`, below the 4 ms gate. Production and canary images each
  used 270,729 bytes flash and 50,696 bytes RAM; the deployment regression
  proves only the feature-off production environment is eligible for ordinary
  `just deploy`.
- Clean deployment from commit `cfb8464` passed source validation, the complete
  local gate, target connection, immutable app/support staging, production
  firmware build, unchanged host provisioning, and a successful production-image
  flash to `/dev/ttyACM0` through `/dev/ttyACM3`. Receipt
  `7242a4a4490547c196a191f3fa614e9d` then failed at the non-mutating
  `app.validate` step because `ledgridwall.local` stopped resolving. The
  candidate app was not validated or activated, systemd was not restarted, and
  no state restore or health acceptance ran. The documented `PI_HOST` retry via
  `192.168.1.62` reached the address during a read-only probe but its deployment
  stopped at `target.connect` when the workstation SSH agent failed to sign;
  later approval reviews for the authenticated retry timed out before launch.
  The API and mDNS are currently unavailable, so service health and all-four
  post-flash identity remain unproven. Resume the same clean reconciliation only
  after stable SSH/API reachability, then repeat status and streamed gates; do
  not close Phase 3A from the successful flash alone.
- The repeated `target.connect` failure was isolated from wall reachability:
  OpenSSH reached `192.168.1.62`, but the workstation's 1Password-backed SSH
  agent failed while signing `id_rsa`. A dedicated-key path now keeps that
  external agent out of automated deployment: `just generate-ai-ssh-key`
  creates an ignored Ed25519 `.gpt-key` at mode `0600`, refuses overwrite, and
  prints the one-time authorization command. `SSH_KEY=./.gpt-key just deploy`
  resolves the key from the repository root and supplies both the exact `-i`
  path and `IdentitiesOnly=yes` to every coordinator SSH, rsync, receipt,
  rollback, and inspection operation. With `SSH_KEY` unset, the existing
  OpenSSH/agent behavior is byte-for-byte unchanged. Deployment regression
  coverage passes; physical reconciliation resumes only after the operator
  authorizes the generated public key and the dedicated identity passes a
  read-only connection probe.

- Replace `pi_connected` with explicit base, foreground, and maintenance state.
- Add a new backward-capable status/capability version and host parser.
- If the pinned dynamic loader requires the reference branch's native ESP-IDF
  entrypoint, migrate to that loader-capable baseline here with dynamic loading
  disabled. Begin from the prototype's pinned Espressif `elf_loader` 1.3.2 and
  revalidate that exact version against the locked firmware toolchain before
  changing it. Requalify streamed behavior before enabling even the static
  canary.
- Preserve current SPI, mailbox, LCD/I80, parallel output, brightness, and
  startup fallback behavior when the feature is disabled.
- Make the compiled rainbow selectable as a local background with declared
  cadence, global offset, common seed, and scene epoch.
- Ensure PING/config/status/brightness cannot take display ownership; only an
  explicit local-background command or complete host frame can do so.
- Implement start, stop, live parameter, fallback, restart, and host-takeover
  transitions using static code.
- Add staged, host-authoritative presentation context carrying resolved vibe
  values/digest and resolved plant-modifier state/revision. Apply vibe luminance
  exactly once in the receiver path; do not depend on firmware-local profile
  lookup.
- Record render cadence, timing, misses, epoch, and transition reasons.

#### Acceptance

- Existing portable waveform, mailbox, status, brightness, bounds, and
  production-build tests remain green.
- The pinned production image builds on the loader-capable baseline with the
  native-module feature disabled and retains current no-FastLED acceptance.
- New command-ownership matrix tests cover every command and mode.
- Feature disabled is behaviorally equivalent to current streamed frames.
- Static local playback observes preferred cadence and global offset; it does
  not render on every display-loop wakeup when unchanged.
- Full host takeover works after local playback without reboot or flash.
- Pi/controller disconnect and receiver restart follow the documented fallback
  policy.
- Host and firmware resolved-vibe/luminance vectors are byte-identical, and
  four-board fake orchestration rejects a mixed vibe or plant-modifier revision.

#### Stop boundary

Do not add upload, cache, dynamic loader, geometry profile, or full-wall release.
Portable tests and a deliberately scheduled one-receiver canary are sufficient.

### Phase 3B: Sparse Foreground over the Compiled Background

Prove the core hybrid value before solving native artifact loading.

Implementation status (2026-08-13): **portable vertical slice, batch/ack payload
optimization, feature-gated product lifecycle, strict one-receiver runner, and
explicitly degraded showcase runner complete; the strict physical canary has
isolated a receiver scheduling defect whose fix awaits physical confirmation,
and the degraded showcase remains open**. Generated
cross-language wire/state vectors,
host serializers and negotiated status-v4 parsing, fake four-board transactional
publication, feature-on receiver state/composition, exact cleanup, atomic
multi-span batch patches, and deterministic payload accounting now land
together with the manager publisher, receiver-static catalog and byte-exact
preview, desired-display fallback, and dashboard/API controls. Production and
ordinary deployment remain feature-off and compatible. The representative
`clock_overlay` tick and corrected 60-second trace pass both frozen payload
gates. Wall mutation remains deliberately out of scope for the portable product
slice. Strict four-board acceptance and production enablement remain blocked by
the separately documented return path.

#### Phase 3B0: next-context hybrid showcase

The next context begins here. Reuse the already accepted compiled rainbow,
resolved vibe context, `clock_overlay`, scene editor, preview compositor, and
complete-host-frame takeover. Do not begin with dynamic modules or a new visual
family.

1. Implement the complete foreground state machine in portable firmware and the
   host driver: RGBA8 full snapshot, sorted non-overlapping patches, coverage,
   session/revision/generation compare-and-swap, staged commit, clear, lease
   renew/expiry, status, and idempotent retries. Keep all buffer and wire bounds
   explicit and generated from the frozen contract.
2. Composite the committed foreground over each receiver's newly rendered local
   base using the shared fixed-point source-over vectors, then encode/display the
   result. A base cadence tick must recomposite unchanged foreground; a foreground
   tick must not advance background simulation.
3. Add host slicing and one aggregate foreground publisher for the existing
   `clock_overlay`. Send a complete authoritative snapshot at session start and
   after controller restart, sparse dirty-union patches on changed clock ticks,
   bounded lease renewals while unchanged, and periodic repair snapshots. A full
   RGB frame remains immediate takeover and bypasses/clears foreground state.
4. Connect the feature-gated receiver-static background to the existing unified
   catalog, scene editor, desired-display persistence, global vibe selector, and
   labeled host preview. The operator should be able to choose the compiled
   background, toggle/configure the clock, and switch `neutral`, `quiet`, `cozy`,
   `vivid`, or `celebration` without restarting either component.
5. Add a strict one-readable-receiver canary and a separately named degraded
   four-wall showcase. The degraded command must preflight the exact 0/1-readable,
   2/3-write-only topology; reject any other missing/partial telemetry shape;
   forbid cached-artifact operations; publish loud incomplete-telemetry status;
   require visual confirmation; and restore the exact prior desired display in
   `finally` through a complete host frame.

The user-visible demonstration is: the rainbow continues to animate on ESPs,
the Pi clock appears and moves over it using sparse foreground traffic, changing
vibe visibly affects the declared background/clock presentation without resetting
their semantic clocks, stopping the Pi expires the clock according to policy
while the receiver background continues, and selecting a normal Python scene
reclaims the complete wall without reboot or flash.

#### Phase 3B0 acceptance

- Feature-off production streaming remains byte/ownership compatible and all
  existing deployment, Python, rendering, native-firmware, and production-build
  gates remain green.
- Cross-language goldens cover alpha 0/255, black, rounding, saturation, ordered
  folds, dirty movement/clear, every receiver boundary, maximum-size packets,
  session/revision/generation ordering, and lease expiry.
- Focused failure tests cover duplicate, stale, interrupted, out-of-order, prior
  generation mismatch, receiver restart, controller restart, partial fake-wall
  staging, failed compensation, and full-host takeover at every intermediate
  state. The manager never reports a healthy mixed wall.
- Local dashboard/API tests prove catalog discovery, preview, start, live vibe,
  overlay update/toggle, persistence, fallback, and exact cleanup. Preview is
  explicitly labeled and does not mutate live state.
- A representative changed clock tick remains below 10 percent of one full wall
  RGB frame including headers and CRC. A deterministic 60-second 60 Hz local
  background plus 1 Hz clock trace reduces total Pi animation bytes by at least
  90 percent versus 60 Hz full RGB streaming after renewals, status, retries,
  acknowledgements, and repair snapshots are counted.
- On logical receiver 0 or 1, strict status proves local base cadence, committed
  foreground generation, lease age, composite/encode/display timing, zero new
  faults, Pi-disconnect expiry, and complete-host takeover. The optional degraded
  four-wall run reports receivers 2/3 as visually unverified and cannot close this
  strict physical item.
- Default and maximum work record host and receiver p50/p95/p99/max. Native base,
  composition, and encode remain inside the declared receiver cadence with no
  missed deadlines; the existing 4 ms host generation/composition p95 gate remains
  intact for Python fallback and preview paths.

Portable-slice evidence and open gates (2026-08-13):

- [x] Exact JSON/C++ goldens cover every foreground command and CRC, the
  4,096-byte maximum patch and 88-pixel tail, all receiver seams/slices,
  zero-patch delta generation agreement, counter/CAS/exhaustion, lease/binding,
  incomplete commit, and the before/at/after schedule boundary.
- [x] Host and feature-on portable receiver implement session, snapshot/delta,
  patch ordering, staged/scheduled commit, lease renewal/expiry, clear, exact
  status-v4 results, fixed-point source-over, foreground-only refresh without a
  base cadence advance, and complete-host-frame takeover. Feature-off production
  retains exact 320-byte status v3 and does not allocate sparse buffers.
- [x] Fake four-board tests cover boundary/no-op slicing, malformed preflight,
  missing capability, partial staging/commit/renew failure, newer-generation
  compensation, failed compensation, scheduled-state disagreement, expiry
  repair, restart authority, and full-frame takeover without reporting a healthy
  mixed wall.
- [x] Atomic multi-span batches and one exact status-v4 proof per whole batch
  meet both frozen thresholds while retaining byte-identical retry and legacy
  sparse-patch fallback. The same representative 35-span, 173-pixel changed
  clock tick now uses four receiver batches and 952 bytes including headers and
  CRC, or 7.1795% of the exact 13,260-byte complete wall RGB transfer, down from
  1,812 bytes and 13.6652%. The corrected deterministic 60-second trace now
  clocks 2,468,816 sparse bytes versus 47,736,000 complete-frame bytes, a saving
  of 94.8282%, up from 86.5648%. It counts 976 commands/acknowledgements, 5,600
  status queries (4 status-v3 and 5,596 status-v4), 128,400 command bytes,
  2,340,416 status-transfer bytes, and 4,937,632 full-duplex endpoint bytes.
  Status traffic remains an honest 94.7991% of SPI-clocked sparse traffic, but
  neither payload gate is red.
- [x] Manager, desired-display, dashboard/API, labeled preview, live vibe and
  clock controls, persistence/fallback, and exact lifecycle cleanup are complete
  as one explicitly gated product slice. `ReceiverSparsePublisher` owns fresh
  sessions, generation/CAS ordering, 3-second leases, bounded renewal, 30-second
  authoritative repair, and fail-closed status. The manager stages the compiled
  base before the first complete foreground snapshot, preserves overlay semantic
  clocks across vibe/plant context transactions, and uses complete RGB
  `set_all_pixels` for stop, Python-scene switch, or any unproven receiver state.
  The dashboard exposes cadence/seed, clock controls, host-simulation labeling,
  fallback selection, and receiver agreement/lease/degraded status. Desired
  state retains the exact native reference plus Python fallback when enabled and
  resolves that fallback before hardware mutation when disabled. The single
  `LEDGRID_RECEIVER_HYBRID_CANARY` switch maps to both required typed gates;
  production systemd and ordinary deployment pass neither, so the component is
  absent and handcrafted native scenes fail closed by default.
- [x] Product-slice portable evidence is green: 18 sparse-publisher tests, 9
  receiver-hybrid manager lifecycle tests, 9 compiled-renderer/catalog tests,
  exact foreground placement/opacity/clear coverage tests, and the broader
  API/IPC/UI/persistence suites. The settled complete project gate passes 970
  tests plus 1,264 subtests, rendering passes 23
  tests plus 3 subtests, native firmware passes all 59 cases, and deployment
  acceptance passes 162 tests plus 77 subtests. Both pinned builds remain inside
  their accepted envelopes: production feature-off uses 50,720 bytes RAM and
  272,273 bytes flash; the named canary uses 65,504 bytes RAM and 278,553 bytes
  flash. The final repeated 32 x 138 `snake-max-density` render stays below the
  4 ms p95 gate at 3.2268 ms. The authoritative ordinary clean deployment
  receipt is recorded below.
- [x] Ordinary clean `just deploy` passed from commit `d8212af` with deployment
  ID `a21fba2c1b3a40f6a5e05f6cd7a6fd2a` and app release
  `7c0c34f330f08c82d77ceb75248de4ca7ff1ba2e9bf8a4a75740f3a71f9db982`.
  All 14 coordinator phases completed in 2m28s; unchanged provisioning and
  production firmware were skipped, `living_stained_glass/before-deploy` was
  restored, and two stable health samples agreed on the desired/observed release
  across all four receivers. The source policy was clean with no dirty diff.
- [x] The strict one-readable-receiver runner now requires exact status v4 and
  the six explicit Phase 3B canary capabilities before mutation. Portable fakes
  cover full snapshot, sparse movement/clear delta, base/foreground cadence
  independence, positive timing telemetry, zero fault deltas, disconnect/lease
  expiry with the base continuing, every command failure boundary, and complete
  black host takeover in normal and `finally` paths.
- [x] The separately named degraded four-wall runner accepts only exact strict
  status-v4 receivers 0/1 plus the exact status-v0 no-return shape on 2/3. It
  allowlists only compiled-background/foreground packets, records outbound-only
  evidence for 2/3, requires nonce-bound human confirmation, reports
  `telemetry_complete: false`, and restores an exact supplied complete RGB frame
  plus desired state after mutation. A failed topology preflight is observation-only.
- [x] A fresh read-only installed-wall probe on 2026-08-13 passed the unchanged
  feature-off baseline: logical 0/1 reported exact v3 identity/capabilities and
  logical 2/3 matched the documented no-return state. It changed no wall state
  and is not Phase 3B feature-on evidence.
- [x] The physical canary procedure identified `/dev/ttyACM0` as logical
  receiver 1 (`9c:13:9e:bb:3d:14`) without guessing the other USB bindings.
  Exact status-v4/six-capability preflight rejected the wrong logical address
  without mutation, then passed on logical 1. The named feature-on image started
  the compiled rainbow and advanced its local cadence with valid context and
  zero receiver fault deltas. The first sparse controller-session command did
  not produce accepted evidence, so the procedure stopped before any foreground
  snapshot or degraded full-wall mutation. Every attempt's exit guard reflashed
  the pinned production image, restarted `ledgrid.service`, and restored
  `living_stained_glass`; the post-run production probe reported status versions
  `[3, 3, 0, 0]`, capabilities `[12, 12, 0, 0]`, and zero host/receiver errors.
- [x] Physical execution exposed three acceptance-tool defects before the
  receiver-side stop: direct CLI imports depended on the current directory;
  v4 acknowledgements and complete-host takeover could legitimately trail the
  first queued status; and real command completion could exceed the original
  two-query acknowledgement window. Direct launch now works from an unrelated
  directory, takeover and acknowledgement waits are bounded and exact, and
  command failures report the last version, command, operation sequence, and
  CRC/queue/display counters. Focused strict-canary, sparse-protocol, Phase 3A,
  and recipe tests cover these hardware-derived cases. Each settled fix was
  followed by a clean ordinary `just deploy`; final receipt
  `fc4acb673fe04f8b98269858ddea60fc` deployed clean commit `582c47b`, completed
  all 14 phases in 2m26s, activated immutable app release
  `bd974409717663640ac25f63f336c6dc4b0fd541d3d620fdd7e30e2843d68793`,
  skipped unchanged production firmware, and restored the exact prior scene.
- [x] A persisted diagnostic repeat proved that the sparse `0x20` session
  command was not rejected: status eventually reported command `0x20`, the next
  operation sequence, and success with zero CRC, SPI-queue, and display errors,
  but only after the host's bounded acknowledgement window. Extending and pacing
  that window to 128 status queries still failed, ruling out a host timeout
  adjustment. ESP-IDF pins `app_main`, which owns the SPI dequeue loop, to CPU0;
  the higher-priority continuous display task was also pinned to CPU0 and could
  starve command processing during local playback. The display task is now
  pinned to CPU1 through an explicit tested task policy while SPI remains on
  CPU0. All 60 native cases, 87 focused Python tests plus 155 subtests, and both
  pinned production/canary builds pass; image sizes remain within the accepted
  50,720/65,504-byte RAM and 272,273/278,553-byte flash envelopes.
- [x] The task-separation fix was delivered by a clean ordinary deployment.
  Receipt `46f84d04d42b4fe6be4f51a6f365426f` completed all phases from clean commit
  `ef908b7`, activated immutable app release
  `ee98ef83ab34344ad4b1947d7cfd30b24c3746688a2b39c770d9592a3bd95912`,
  flashed the exact production firmware to all four receivers, obtained two
  stable health samples, and restored `living_stained_glass`. Two preceding
  attempts failed closed and restored the prior service: concurrent PlatformIO
  uploads could delete their shared build output, and the combined `nobuild`
  plus `upload` targets generated invalid esptool address/file arguments.
  Deployment now hashes the prebuilt production image, flashes receivers
  sequentially with the supported `-t upload` target, and verifies the image
  before and after every port. Deployment regression coverage retains those
  hardware-derived failure cases and ordinary `just deploy` remains green.
- [x] The first post-deployment strict logical-1 repeat proved the CPU separation
  resolved the original sparse session setup failure: exact v4/six-capability
  preflight, context activation, compiled rainbow cadence, controller session,
  both full-snapshot batches, generation-1 commit, and foreground composition
  all succeeded. The stop gate then correctly rejected two new CRC faults. Exact
  next-operation-sequence acknowledgements prove both batch packets were valid;
  each fault instead followed an unpaced acknowledgement query while the
  two-deep slave queue was being refilled after batch CRC/digest/copy work.
  Control-path pre-drain and post-command queries are now paced by the existing
  bounded 1 ms interval without slowing complete-frame streaming. A real queued
  v3 response also clears cached v4-only telemetry, and complete-host takeover
  waits for a coherent v4 snapshot rather than accepting a v3/stale-v4 chimera.
  Timing-aware queue and takeover tests preserve exact acknowledgement and
  zero-fault requirements. The guarded outer cleanup reflashed the receipt-bound
  production image, restarted the feature-off service, restored exact state,
  and passed degraded production status.
- [x] The acknowledgement-pacing/status-coherence fix passed a second clean
  ordinary deployment. Receipt `7d3615f490134579b0700519d3e9ccb6`
  completed all 14 phases from clean commit `54257c2` in 2m09s, activated app
  release `6bc8c48215852c40201a5504de78265252e5810e724437a43c672df2bdb3d66f`,
  reused the exact unchanged production firmware, restored the prior scene,
  and passed stable readiness. An earlier attempt on the same clean commit was
  observation-only and failed before remote mutation because the default SSH
  agent could not sign; the successful repeat explicitly selected the ignored
  repository-local key with `IdentitiesOnly=yes`.
- [x] The next receipt-bound logical-1 canary proved zero new CRC, SPI-queue, or
  display faults and coherent exact-v4 complete-host takeover in `finally`.
  Snapshot composition then stopped only because the runner expected no base
  cadence during the entire paced session/snapshot transaction. One legitimate
  30 Hz base frame elapsed, so the correct composite delta was two: one natural
  base composition plus one foreground commit. Snapshot acceptance now requires
  rendered/deadline equality, a natural-frame bound of
  `floor(elapsed * cadence) + 1` for one in-flight status boundary, exactly one
  commit, zero expirations, and composites equal to natural base frames plus
  one. The later cadence-aligned sparse delta retains the stricter zero-base-
  advance requirement. Focused tests cover zero/one natural frames plus cadence,
  composite, commit, expiry, and implausible-rate failures. The exit guard again
  reflashed exact production firmware, restarted the feature-off service,
  restored byte-identical desired state, and passed production status.
- [x] The snapshot-accounting fix passed clean ordinary deployment receipt
  `acd01555d2e047fcbe014d6dab48e9ea`: all 14 phases completed from clean commit
  `51ece3d` in 2m13s, app release
  `9ee6fa19b268e577a1138d48e7381b5e4780097826ec139e0b5302dd97601873`
  activated, unchanged production firmware was reused, exact prior state was
  restored, and readiness passed.
- [x] The next guarded logical-1 canary passed zero-fault full snapshot and the
  realistic snapshot accounting with one natural 30 Hz base frame in 45.171 ms;
  it then passed the cadence-aligned delta with exact zero base advance and one
  foreground composite. The 0.501-second timing window sampled 15 completed
  frames: base render p95 1.136 ms, foreground composite p95 0.208 ms, encode
  p95 0.498 ms, and display p95 4.458 ms, with zero cadence misses. After the
  deliberate five-second disconnect, the receiver had continued for 152 base
  frames, but the freshly reopened host stopped one query too early on a queued
  v3 prefix. A reopen begins with PING, v3 query-size negotiation, and the
  two-deep legacy response queue, so coherent v4 arrives on the fifth read.
  Fresh-status draining now retains the four-read post-observation freshness
  floor, allows at most five reads for reopen negotiation, accepts only exact v4,
  and returns the strongest bounded non-v4 diagnostic on failure. Tests cover
  the real reopen sequence, a stale pre-observation v4, and bounded no-v4
  failure. Production reflash, feature-off restart, exact state restoration,
  health, and degraded production status passed again.
- [x] The bounded fresh-v4 drain passed clean ordinary deployment receipt
  `fdd532d4221b4e71bd28a54e7f6732c1`: all 14 phases completed from clean commit
  `9cf591e` in 2m10s, app release
  `e3d15b9936afd74e98f9c933207bcaeb07837d2e4afd26c3e6892aee068b5517`
  activated, unchanged production firmware was reused, exact state restored,
  and readiness passed. One canary invocation then stopped before mutation when
  five stale 160--191 MB recovery trees filled the target's 923 MB `/tmp` tmpfs.
  Only those exact `phase3b-production.*` trees were removed, returning `/tmp`
  to one-percent use, and the guard now deletes its own recovery tree after all
  production/state/health/status evidence is persisted.
- [x] Receipt-bound logical receiver 1 now passes the complete strict Phase 3B
  canary with zero CRC, queue, display, or cadence-miss deltas: full snapshot;
  aligned sparse movement/clear delta with exact zero base advance; 15 completed
  timing samples in 0.501 seconds; five-second disconnect; lease expiry with 152
  continuing base frames and one expiry refresh; normal complete-host takeover;
  and a second complete-host takeover in `finally`. Measured p95 values were
  1.101 ms base render, 0.209 ms foreground composite, 0.503 ms encode, and
  4.463 ms display. Guarded cleanup reflashed the exact production image,
  restarted the feature-off service, restored byte-identical desired state,
  passed health and degraded status, and reclaimed the temporary recovery tree.
- [x] The first guarded all-four attempt captured byte-exact desired state and a
  4,416-pixel RGB restoration frame, hash-verified and sequentially flashed the
  canary image to all four receivers, and obtained a complete strict pass from
  logical receiver 0. Its 15 timing samples had p95 values of 1.134 ms base
  render, 0.227 ms foreground composite, 0.501 ms encode, and 4.465 ms display;
  snapshot, aligned delta, five-second expiry, normal takeover, and `finally`
  takeover all passed without fault or cadence-miss deltas.
- [x] The same attempt proved the remaining logical-1 stop was an acceptance
  boundary, not a session-setup or transport failure. Receiver 1 passed its
  snapshot, aligned delta, and timing windows, then reported 151 completed base
  frames and 153 composites during expiry. Firmware records a composite before
  physical submit/wait but records the corresponding rendered base frame only
  after completion; lease expiry can independently add one clearing refresh.
  The strict evaluator therefore now permits completed base frames plus at most
  one expiry refresh and one in-flight base composite. It still rejects `+3`,
  while retaining exact expiry, commit, operation, session, status, cadence,
  timing, and zero-fault requirements. Focused Phase 3A/sparse/Phase 3B coverage
  passes 67 tests and 112 subtests, including `+1`, `+2`, and failing `+3` cases.
- [x] Because the logical-1 stop occurred before the visual body, no showcase
  challenge or operator response was created. The guarded `EXIT` path
  sequentially restored production firmware on all four receivers, restored
  byte-identical desired state, and passed restart, health, and degraded-status
  checks. Evidence is under
  `run_state/phase3b-showcase/20260814T153434Z-fdd532d4221b4e71bd28a54e7f6732c1`.
- [x] Bounded expiry accounting passed clean ordinary deployment receipt
  `045d06ab006d41f09a1cba7e729f3cfa` from commit `cc00e86`: all 14 phases
  completed in 2m14s, app release
  `49ed3befe76188964177724ea9cce9cc674ffbc2b006c0aefd0de95d62eef21a`
  activated, production firmware remained hash-identical, exact desired state
  restored, and stable four-receiver health passed.
- [x] The receipt-bound guarded retry again passed the complete strict canaries
  on logical receivers 0 and 1, then entered the degraded visual body. The
  operator correctly rejected the visual gate: only two lanes showed the
  compiled rainbow, the other two retained stained glass, and no clock was
  visible. The nonce response records `verdict=fail`; no release or physical
  pass is claimed. Its unconditional cleanup sequentially reflashed the exact
  production image on all four ports, restored byte-identical desired state,
  and passed service restart, health, and degraded production status. Evidence
  is under
  `run_state/phase3b-showcase/20260814T154726Z-045d06ab006d41f09a1cba7e729f3cfa`.
- [x] The failed visual gate exposed two deterministic runner defects. Fresh
  canary firmware initializes logical identity to `0xff` and rejects every
  runtime context/background/overlay command until CONFIG assigns an ID in
  `0..3`; normal identity provisioning deliberately omits that sixth CONFIG
  byte when status v3 is unreadable, so receivers 2/3 only latched outbound
  packets and retained their prior frame. Also, the showcase stopped foreground
  publication before publishing its confirmation challenge, allowing the
  three-second clock lease to expire while the operator was first being asked
  to observe it. The real clock source has nonzero alpha coverage on every lane;
  empty content was not the cause.
- [x] The degraded runner now explicitly provisions all four canary logical
  identities after the observation-only topology gate. Readable 0/1 require the
  exact next v4 CONFIG acknowledgement and identity; write-only 2/3 receive the
  exact six-byte CONFIG but remain loudly unacknowledged/unverified. Every raw
  control packet is paced by the established one-millisecond queue-refill
  interval, and device objects must match the installed bus/CS map.
- [x] Confirmation is now a live nonblocking exchange created only after the
  first successful snapshot and initial 2/3 host-counter evidence. The runner
  continues rendering, publishing, and renewing until both the minimum visible
  interval and the nonce response complete; polling is bounded strictly inside
  the renewal schedule and confirmation deadline, and late responses fail.
  The actual pass boundary freshly requires exact v4 identity, session,
  generation, positive lease, active foreground, and exact positive alpha
  coverage on readable 0/1 while reporting expected-but-unverified coverage for
  2/3. The delta splitter's duplicate range increment was also removed.
- [x] Firmware-faithful fakes boot write-only receivers at `0xff` and reject
  runtime commands before CONFIG. Tests cover exact identity ordering and raw
  pacing; actual clock coverage on every lane; confirmation delayed beyond the
  original lease; first-snapshot evidence before challenge; late response and
  unsafe-low-rate rejection; malformed/timeout cleanup; pass-boundary loss of
  state, coverage, v4, identity, session, generation, or lease; and gap-free
  delta batching. The combined degraded, publisher, orchestration, protocol,
  and strict-canary suite passes 95 tests and 148 subtests. The guarded hardware
  script now preserves raw output, atomically extracts the final JSON report,
  and asserts the new identity, live-confirmation, and visibility evidence.
- [x] The live-runner fix passed clean ordinary deployment receipt
  `ae764d0ab0eb4d00a21d90c23402d1a4` from commit `aed52fa`: all 14 phases
  completed in 2m16s, app release
  `3d662e76619123b06898404d46bb0bd3a11a0cd94ac236299f909278af9acb26`
  activated, exact state and stable health passed, and unchanged production
  firmware remained hash-identical.
- [x] The next guarded retry passed both strict readable-receiver gates and
  entered the corrected live exchange, but its 300-second operator window had
  already timed out and restored production before the later observation. The
  wall correctly showed stained glass at observation time; an earlier challenge
  file was not proof that the process was still live. Cleanup again passed all
  four production flashes, exact state hash, restart, health, and degraded
  status. Evidence is under
  `run_state/phase3b-showcase/20260814T163249Z-ae764d0ab0eb4d00a21d90c23402d1a4`.
- [x] A subsequent operator-held run is active under
  `run_state/phase3b-showcase/20260814T170342Z-ae764d0ab0eb4d00a21d90c23402d1a4`.
  The fixed webcam independently shows the compiled rainbow spanning the entire
  physical wall, proving the identity fix reached all four lanes. It also shows
  that the sparse amber clock is not legible over the bright base, so no complete
  rainbow-plus-clock visual pass is claimed yet.
- [x] Add an opt-in premultiplied black clock backdrop with exact content-dirty
  ranges, retaining byte-identical transparent default behavior. The portable
  implementation and 15 focused tests are complete: the prior default-frame
  SHA is unchanged, fixed-point premultiplied composition is exact, constant-
  alpha second ticks still emit RGB dirty ranges, and maximum-backdrop p99 is
  0.459 ms at 32x138. Physical all-four rainbow-plus-clock photography remains
  a cutover gate below.
- [x] Promote the degraded prototype into an explicit persistent transport
  policy backed by a strict shared `run_state` config: exact v4/ACK on 0/1,
  exact write-only shape and paced raw packets on 2/3, always
  `telemetry_complete=false` and never release-acceptable. Select the named
  feature-on firmware environment from that same config in service startup,
  build, flash, restore, and receipts; absent config remains fully feature-off.
- [x] Make ordinary deployment safe while native playback is active: capture
  desired state before receiver flashing, bind firmware skip markers to the
  selected environment and binary digest, restore the exact native scene only
  with readable-receiver proof, and retain universal complete-frame Python
  fallback. The portable implementation now passes the complete suite: 1,056
  unit tests plus 1,528 subtests, 23 policy tests, both firmware builds, and 167
  deployment tests plus 88 subtests. The final physical gates remain: deploy
  the persistent scene, photograph it, and repeat `just deploy` while it is
  active to prove exact native restore and service continuity.
- [x] Persistent degraded playback and ordinary reconciliation are now physical:
  clean receipt `09edc7d257d141549ee7e7eeb15f96f4` deployed commit `bcdf7ff`,
  selected app release
  `c397a7413987645cd6b76d124b6f986a6ea773561146e3bd9677ab66fc3b8874`,
  skipped the unchanged named canary firmware, restored the exact
  `compiled_rainbow` plus clock desired scene, and passed stable health. The
  scene continued through the next ordinary deploy without a receiver flash.
- [x] A four-color full-wall diagnostic after a physical cable change proved
  that transport/logical identity remained `(0,1,2,3)` while the actual
  physical left-to-right lane order became `(0,1,3,2)`: logical lanes 2 and 3
  were swapped. The durable rollout config now carries an exact validated
  permutation. The degraded facade keeps SPI routes, logical identities, and
  readable roles unchanged while using that permutation for receiver-native
  global strip offsets, sparse full/delta foreground slicing and coverage,
  complete host takeover, and every subsequent Python fallback frame. Legacy
  configs load as identity order only until atomically rewritten; malformed,
  duplicate, boolean, missing, and out-of-range mappings fail closed. Focused
  config/startup/transport tests pass 59 cases, including non-identity native
  offsets, overlay coverage, two consecutive host frames, and durable CLI
  round trip; 84 adjacent manager/persistence/deployment tests also pass.
- [x] Physical order `(0,1,3,2)` was atomically installed with config digest
  `08c0a8c2484d4b48f01cece74892246f335c1e7df74e8a7cc5238d3f6e930586`.
  Because the digest changed, fail-closed startup deliberately selected the
  known Python fallback; the saved native scene was then explicitly
  re-authorized. Live status proved the exact permutation, active hybrid
  ownership, zero publisher failures, and per-logical-device expected clock
  coverage `{0:85,1:136,2:34,3:136}`. Webcam evidence showed one continuous
  clock across all four physical lanes over the compiled rainbow. A final clean
  ordinary deploy from commit `8c066c6` succeeded as receipt
  `36a4a796c3bb415582a26a0093f45f10` in 1m56s: build and flash skipped the
  unchanged named canary firmware, app activation/restart/restore were
  unchanged and skipped, stable health passed, and a post-deploy photograph
  plus status confirmed the same running rainbow-plus-clock scene.
- [x] Camera-guided numeral repair exposed two independent direction domains. A
  higher-resolution
  eight-color, one-color-per-strip diagnostic exposed information the earlier
  four-color lane test could not: logical receivers 2 and 3 keep their SPI
  routes and lane positions but their eight local strips are physically
  reversed for host-authored frames. The durable topology contract now stores
  separate strict host-frame and receiver-native direction bits per logical
  receiver; malformed types and lengths fail closed. The installed host map is
  `(false,false,true,true)` while the native map remains
  `(false,false,false,false)`. Sparse full/delta patches plus complete and
  subsequent host frames use only the host map; CONFIG and receiver-native
  rainbow coordinates use only the native map. A real `02:41:59` to `02:42:00`
  regression reconstructs the aggregate RGBA plane byte-exactly across both
  affected logical receivers. The complete gate passed 1,066 Python tests plus
  1,544 subtests, 23 policy tests, all 60 native firmware tests, both ESP32
  builds, and 168 deployment tests plus 88 subtests.
- [x] Clean receipt `4c9831c3ed9c4f10b701d94078186823` deployed commit
  `e19c455`, app release
  `8ea4a042db2dd07a9806f2cbc18220faed74c5dae5519df5d1b1e1f014a00885`,
  and canary firmware
  `9118ea2bb803b66e09b66908d258145d93b78f5cf9067d6b94531ca856767c32`
  to all four receivers, restoring the exact rainbow-plus-clock scene. The
  camera-derived config is now physical order `(0,1,3,2)` with reversed logical
  receivers `(2,3)`, digest
  `f9a49ff7b3d4525fbb6e4171d8995c534a4ecac2d5b955311b059f3b98a43c4f`.
  Fresh status proved the exact host mapping, active hybrid ownership, zero
  publisher failures, and zero queue/display errors. The wall-only webcam crop
  proved contiguous `03:57` digits, but operator observation correctly rejected
  the receiver-native base: applying the host reversal to native coordinates
  mirrored the two right lanes into a chevron. That crop is retained as rejected
  diagnostic evidence at
  `run_state/physical-acceptance/20260814-rainbow-clock-strip-direction-fixed.png`
  with SHA-256
  `347433ff76a4c7cf4e064018dddbf86de26d3c950d199b94860c59499e027a9c`.
  Receipt `a30db509a9b04f32bc263df578b2a5b7` then proved ordinary deploy
  reconciliation still skipped the unchanged receiver firmware and retained the
  scene, but it intentionally does not override the rejected visual gate.
- [ ] Deploy the independent-native-direction correction, keep the host map
  `(false,false,true,true)` and native map `(false,false,false,false)`, then
  require one fresh webcam frame with both readable numerals and one continuous
  non-chevron rainbow across all four lanes. Repeat ordinary deploy and require
  unchanged firmware skip plus the same post-deploy visual result before handoff.
- [ ] Receiver timing percentiles, strict readable-receiver canary, optional
  explicitly degraded four-wall visual showcase, disconnect/expiry observation,
  and restored streamed acceptance remain physical work. No physical foreground
  acceptance is claimed here.

#### Next-context execution and handoff

Implement the vertical slice in this order so the next handoff contains a visible
benefit rather than only dormant protocol code:

1. Extend the generated foreground vectors and add focused host/firmware state
   machine tests. Expected focused ownership is
   `tests/unit/test_receiver_sparse_overlay_protocol.py`,
   `tests/unit/test_receiver_sparse_overlay_orchestration.py`, and portable
   receiver-runtime cases; reuse existing generated fixtures instead of creating
   a second blend oracle.
2. Land the firmware compositor and fake four-board publisher behind
   `receiver_sparse_overlay=false`, including complete-host-frame takeover, before
   integrating manager lifecycle. Build both production feature-off and deliberate
   canary variants and prove the ordinary deploy target cannot select the canary.
3. Connect the existing `clock_overlay`, receiver-static descriptor/preview,
   scene lifecycle, vibe updates, desired-display restoration, and dashboard in
   one product slice. Add `tests/unit/test_hybrid_scene_product_surface.py` for
   API/IPC/UI-facing state, preview isolation, feature-off rejection, fallback,
   and cleanup.
4. Run focused suites first, then `just test`, `just test-rendering`,
   `pio test -d firmware/esp32 -e native`, both pinned firmware builds,
   `just test-deployment`, and both ordinary deploy dry runs. The 32 x 138
   default/stress benchmark and deterministic payload trace are required before
   any wall mutation.
5. Demonstrate the local dashboard preview and state round trip. Only then run a
   strict readable-receiver canary; the degraded four-wall showcase is optional,
   manually observed, explicitly incomplete, and never a substitute for the
   post-repair strict gate.

Current handoff: items 1 through 4, the protocol batching/ack-amortization
follow-up, and the portable strict/degraded item-5 runners are complete as a
portable/fake-wall/product slice. The receiver-native scene is available only
under the deliberately named host canary switch and named feature-on firmware
build; ordinary production remains Python-only. Item 5 physical execution has
started on logical receiver 1 and stopped safely after local-background proof
when sparse controller-session acceptance was not established. The next hardware
work is therefore deployment and a physical repeat of the dual-core scheduling
fix on that strict readable receiver, not the degraded showcase. Only after the
strict canary passes may the optional explicitly degraded showcase run, followed
by complete-host-frame cleanup, production-image restoration, and restored
streamed acceptance. Do not interpret portable or local-background completion
as foreground physical acceptance, and do not enable the receiver path by
default while the documented SPI1 return-path fault remains.

Settled-source portable evidence on 2026-08-13: `just test` passed 1,008 Python
tests and 1,476 subtests, 23 rendering tests and 3 subtests, all 59 native
firmware cases (17 contract, 8 native-protocol, and 34 receiver-runtime), both
pinned firmware builds, and 162 deployment tests plus 77 subtests. After the
timing-percentile sampler was tightened to use the firmware's completion-gated
local-frame counter rather than its host-mailbox display counter, the final full
Python rerun passed 1,010 tests and 1,477 subtests. The final repeated
`snake-max-density` installed-geometry p95 was 3.4609 ms, below 4 ms. The
feature-off production image uses 50,720
bytes RAM and 272,273 bytes flash; the deliberate canary uses 65,504 bytes RAM
and 278,553 bytes flash, a feature-on delta of 14,784 bytes RAM and 6,280 bytes
flash. Generated-fixture drift checks, Python compilation, `git diff --check`,
the deterministic payload gate, both ordinary deploy dry runs, and the dirty
source-accounting deploy plan pass. These are workstation/portable numbers, not
Raspberry Pi or ESP32 runtime percentiles.

Keep the plan's implementation status, focused evidence, measured payload bytes,
timing percentiles, cleanup result, and any newly discovered stop gate current as
each numbered item lands.

- Implement staged RGBA8 foreground buffers, coverage tracking, fixed-point
  source-over composition, lease expiry, full snapshot, sparse patch, commit,
  clear, and status.
- Extend the host driver and four-device controller with exact serializers,
  queued-ack handling, global-to-local range slicing, retries, and generation
  reconciliation.
- Send the Pi-rendered clock overlay over the statically linked background.
- Stage every receiver before a common future presentation time; measure commit
  skew and compensate or replay any partial result.
- On Pi restart, publish the full authoritative foreground before deltas.
- Preserve complete host frames as explicit takeover and rollback.

#### Acceptance

- Host and firmware blend goldens are byte-identical.
- Moving/removing clock content, alpha-zero clears, black glyphs, duplicate or
  stale session/revision/generation, failed prior-generation compare-and-swap,
  out-of-order patches, interrupted staging, session replacement, and receiver
  restart leave no stale foreground.
- Native-base changes recomposite unchanged clock coverage at the declared base
  cadence.
- A representative changed clock tick uses less than 10 percent of one complete
  wall RGB frame in patch payload including headers and CRC. Separately, a fixed
  60-second native-background-plus-clock run uses at least 90 percent fewer total
  Pi animation bytes than 60 Hz complete RGB streaming, including
  acknowledgements, status, renewals, retries, and scheduled repair snapshots.
- A glyph crossing a receiver boundary becomes visible with first-to-last board
  skew below one accepted display period; otherwise the hybrid mode does not pass.
- On-device native render, composition, encode, display, and missed-cadence
  p50/p95/p99/max are recorded; desktop proxy numbers are labeled separately.
- Time-sensitive foreground expires after lease loss while the base continues.
- Full host takeover clears or bypasses the foreground deterministically.

#### Stop boundary

Keep one aggregate source-over foreground plane. Do not add dynamic native code,
receiver-rendered overlays, multiple blend modes, or general layer graphs.

### Phase 3C: Versioned Installation Geometry and Receiver-Safe Optics

Move only geometry and transforms justified by hybrid rendering.

Portable compiler, golden-vector, profile-library, host-preview, and fake-wall
work may proceed before the SPI1 repair. Strict profile activation, digest
agreement, receiver timing, and physical seam acceptance remain release-blocked.

- Add a deterministic compiler from current calibration evidence to the bounded
  binary installation profile.
- Golden-test global masks, clearance, edges, globe regions, distance/normals,
  slicing, coordinate orientation, and halos against Python geometry.
- Add Pi library, receiver staging/activation, digest/status, and rollback for
  profiles independently of native backgrounds.
- Expose read-only profile views through host context and reserve the native ABI
  view.
- Implement the existing universal `hue_shift` modifier after composition as the
  first receiver-safe optic. Separately exercise read-only foliage/globe geometry
  in the static/native canary without reclassifying semantic modifiers.
- Keep semantic routing, portals, habitat, emitter, HUD placement, and
  neighbor-sampling refraction in component code.

#### Acceptance

- Compiler output is deterministic and reconstructs exact 32 x 138 categories
  and region identities from receiver slices.
- Malformed, wrong-geometry, stale, and mixed-generation profiles reject with
  explicit status.
- Disabled and enabled-strength-zero paths retain byte, logical-state, and RNG
  parity.
- Shared host/firmware fixed-point vectors match nonzero `hue_shift` output at
  representative and maximum strengths, exact mask edges, and receiver
  boundaries.
- Live profile/modifier changes invalidate presentation caches without resetting
  or advancing simulation.
- Boundary fixtures and physical images show no new seam at 8-strip receiver
  boundaries for the accepted transform.
- Default and maximum transform work meet receiver cadence without resets or
  missed deadlines.

#### Stop boundary

Do not move every plant modifier, raw calibration JSON, or liquid-glass sampling
to receivers.

### Phase 3D: Repo-Peer Native Build and Managed Library

Build and validate an unsigned native peer without executing it on the installed
wall.

- Add explicit native descriptor validation to the unified catalog. A native
  package requires no `__init__.py` or Python class and cannot be imported as one.
- Put one analytic pilot background in a self-contained plugin package.
- Port the reference builder, ABI header, host preview harness, package
  canonicalization, import/export checks, and benchmark machinery without its
  signing path.
- Produce deterministic Xtensa and host-preview artifacts plus the unsigned
  bundle.
- Add the atomic Pi managed library, build/publish receipts, preview generation,
  and a fake receiver cache/install implementation.
- Keep build, install, and activate as separate steps and make native source-only
  iteration independent of app deploy and firmware flash.

#### Acceptance

- Existing Python discovery, presets, preview, and render tests remain unchanged.
- The native pilot is catalog-visible, preset-valid, previewable, and never
  imported/executed as Python.
- Rebuilding identical inputs with the pinned toolchain produces identical
  artifacts; source, ABI, flag, manifest, or toolchain changes alter identity.
- Validation rejects traversal, extra members, bad hash, wrong ELF
  class/machine/type, wrong ABI/target/geometry, missing export, forbidden import,
  initializer/finalizer sections, oversized payload, malformed schema/default,
  or output-canary overwrite.
- Host preview renders and stitches all four global offsets. Mean/p95/p99/max are
  reported as workstation proxy evidence only.
- A native source change produces a build/publish plan without app restart,
  dependency work, reboot, or firmware flash.
- The Pi library survives application deploy and rollback.

#### Stop boundary

Do not expose arbitrary upload or execute dynamic modules on installed receivers.

### Phase 4: Dynamic Loader, Cache, Four-Board Orchestration, and Release

Activate trusted unsigned modules only after the earlier contracts and hardware
prerequisites are proven.

- Port the dynamic loader, content-addressed cache, ordered upload, atomic commit,
  inactive LRU eviction, reserve, typed parameters, watchdog, quarantine, and
  status from the reference branch against the new unsigned ABI/package.
- Add Pi probe, stage, verify, activate, compensate, adopt, stop, parameter, and
  removal workflows under one receiver-background operation lock.
- Integrate native provider state with scene selection, unified catalog, preview,
  persistence, dashboard health, deployment receipts, and fallback.
- Require unanimous capability and identity before native activation. Mixed old
  and new receiver firmware continues ordinary streaming but cannot start a
  native scene.
- Preserve and pin the prior accepted bundle binding and payload, compiled
  fallback, known Python fallback, and previous firmware images through soak.
- Execute the one-receiver and four-receiver physical gates below before changing
  any feature default.

#### Acceptance

- Portable firmware tests cover upload bounds, ordering/retry, duplicate chunks,
  interruption, wrong bundle/payload binding, ABI/target/geometry, cache
  reserve/eviction, active deletion, atomic visibility, typed parameters,
  callback failure, watchdog, boot quarantine, and complete host takeover.
- Capacity preflight and cache-pin failure injection prove candidate staging
  cannot evict the active or rollback binding and fails before mutation when all
  three reservations do not fit.
- Watchdog attribution covers load/relocation, entrypoint, initialization,
  context update, render, cleanup, and unload; each phase has a deliberate
  failure/reset/quarantine test.
- Fake four-receiver tests fail every receiver/operation/ack point and never end
  with the manager claiming a healthy mixed wall.
- Pi restart adoption requires unanimous bundle/payload binding, mode, vibe,
  resolved plant-modifier revision, and profile, then republishes the foreground
  snapshot.
- App downgrade preflight refuses while incompatible receiver-native state is
  active; after the explicit recovery operation establishes a known Python full
  scene, app-only rollback succeeds without mutating receivers.
- One receiver and then all four pass physical install/start/update/stop,
  disconnect/restart, crash/fallback, overlay, geometry, switchback, skew/drift,
  and soak acceptance.

#### Stop boundary

Do not add third-party packages, frame tracks, strict v-sync, receiver-native
foregrounds, or general artifact-provider abstractions.

### Phase 5: Migrate Palettes and Expand Only from Evidence

Broaden the system after the vertical slice is production-accepted.

- Remove remaining temporary vibe bridges only when Phase 2D evidence and
  ordinary use show their procedural families retain preset intent; palette
  migration itself is no longer delayed until this phase.
- Add two or three analytic native backgrounds with materially different work
  profiles and geometry use.
- Add another sparse overlay only if the aggregate foreground contract supports
  it without new firmware semantics.
- Measure whether a second foreground plane, additive blend, frame-track provider,
  host-native provider, or stricter synchronization solves a demonstrated need.
- Generalize provider/build/install interfaces only after Python and accepted
  receiver-native implementations reveal genuinely shared behavior.
- Update `ANIMATION_SYSTEM.md`, rendering acceptance, deployment documentation,
  authoring examples, and rollback runbooks to reflect the accepted contracts.

#### Acceptance

- Every migrated plugin/preset passes schema, deterministic behavior, preview,
  visual comparison, and default/stress performance checks.
- The neutral vibe preserves intended baseline appearance or carries an explicit
  reviewed migration.
- New native backgrounds pass one-receiver and full-wall cadence, seam, fallback,
  and soak gates.
- No abstraction is introduced solely for a hypothetical provider or layer.

#### Stop boundary

Further expansion requires a new measured problem statement and acceptance gate.

## Failure and Recovery Matrix

| Event | Required behavior |
| --- | --- |
| Unknown vibe ID or profile | Reject API input; persisted unknown version falls back visibly to `neutral` |
| Overlay patch interrupted | Keep prior committed generation visible |
| Overlay lease expires | Clear time-sensitive foreground; continue native background |
| Pi disconnect | Continue accepted native background; enforce overlay stale policy |
| Pi/controller restart | Establish a new controller session, reconcile all receivers, adopt only unanimous state, publish full desired state and foreground snapshot |
| One receiver missing during stage | Activate nothing; leave previous scene running |
| One receiver fails after partial start | Stop candidate, restore the pinned prior bundle/payload binding or use complete host fallback, report degraded state |
| Native callback error or watchdog | Quarantine payload digest and run compiled fallback when firmware remains healthy; no automatic retry |
| Native cache loss | Rehydrate from Pi library before activation |
| Installation-profile mismatch | Reject required-profile start or use declared optional no-op; never mix silently |
| Mixed receiver capabilities | Continue legacy streaming; refuse native activation |
| Complete host frame arrives | Explicitly reclaim host ownership and bypass/clear receiver foreground |
| App rollback cannot understand scene | Refuse the app-only rollback; require an explicit receiver/scene recovery to a known Python full scene first |
| Firmware rollback | Stop native, take host/fallback ownership, retain prior binaries and partition data |
| Managed library corruption | Reject the invalid bundle/payload binding, retain the pinned prior artifact, rebuild from tracked source if necessary |

## Automated Acceptance

Run focused tests before broad suites. The eventual implementation must retain:

```bash
just test
just test-rendering
pio test -d firmware/esp32 -e native
```

Add focused suites for:

- descriptor and compatibility discovery;
- scene/vibe schema, resolved-profile digests, precedence, serialization,
  persistence, and unknown versions;
- semantic-state/RNG parity under presentation-only changes;
- timing-adapter equivalence; layer cadence, lifecycle, canonical coordinates,
  placement, opacity, dirty union, ordered fixed-point blending, caching, and
  interaction routing;
- preview/live parity and cache invalidation;
- protocol bytes, bounds, CRC, session/revision/generation ordering and
  wrap-avoidance, compare-and-swap, retries, queued acknowledgements, state
  ownership, scheduled commit skew, leases, and legacy-host compatibility;
- deterministic native builds, package/ELF validation, host preview, managed
  library, and deploy receipts;
- receiver cache, loader, quarantine, status, failure recovery, and four-board
  compensation;
- geometry packing, global slicing, category/region parity, halos, modifier
  no-op parity, and nonzero host/firmware transform parity.

Performance reports use the installed dimensions and include mean, p50, p95,
p99, maximum, changed-frame ratio, dirty pixels/ranges, and actual payload bytes.
At minimum:

- accepted host scenes retain the existing 4 ms generation/composition p95 gate
  inside the 5 ms manager period;
- native rendering remains below 4 ms p95 per 8 x 138 receiver at its declared
  cadence, with p99/max and cadence misses retained;
- receiver encoding remains at or below the existing 1 ms p95 gate and display
  DMA at or below 4.8 ms p95;
- the dense streamed capacity canary targets 160 FPS and retains at least 150
  displayed FPS with the
  existing accounting and integrity requirements;
- a representative changed clock tick uses less than 10 percent of one complete
  wall RGB frame, and a fixed 60-second native 60 Hz background plus 1 Hz clock
  run reduces total Pi animation payload by at least 90 percent versus complete
  RGB streaming after all control/status/repair bytes are counted;
- no benchmark result is described as Raspberry Pi or ESP32 evidence unless it
  ran on that hardware.

## Physical Acceptance and Release Gates

Hardware work is never implied by code completion. Schedule it explicitly after
portable tests pass. These gates control release claims and default enablement,
not host UX, portable implementation, preview, fake orchestration, or an
explicitly degraded visual showcase. Before the SPI1 repair, strict canaries may
target readable receiver 0 or 1; write-only receivers 2 and 3 can contribute only
host-counter plus visual demonstration evidence.

### Gate H0: wiring and streamed baseline

- Repair and verify the documented SPI1 MISO/MOSI fault before any all-wall
  native release.
- Read fresh identity/capability/status from all four receivers with no TX echo.
- Preserve the last validated firmware binaries and partition table.
- Run the existing one-receiver and full-wall streamed canaries before enabling
  any local-background feature.

### Gate H1: one receiver

- Flash the new baseline with all native feature flags off and requalify ordinary
  streamed frames.
- Exercise static local background start/update/stop, complete host takeover,
  Pi disconnect/restart, power cycle, and fallback.
- Exercise foreground full snapshot/delta/clear/lease, dropped/duplicate/stale
  generation, clock movement, and restart repair.
- Exercise native install/probe/cache/start/live parameters/stop, interrupted
  upload, wrong bundle/payload binding, cache eviction/pin protection, callback
  failure, deliberate watchdog, quarantine, and reinstall.
- Record native render/composite/encode/display p50/p95/p99/max and cadence misses.

### Gate H2: four receivers

- Require fresh identity and exact desired capabilities, bundle/payload binding,
  vibe, plant-modifier revision, and profile digest on every board.
- Inject a stage failure or unplugged receiver and prove no subset starts.
- Start the analytic background with offsets 0, 8, 16, and 24; record start skew
  and 30-minute drift.
- Move the clock across receiver boundaries and test old-pixel clears, alpha,
  scheduled presentation skew, lease expiry, Pi restart/new-session snapshot,
  and partial-generation recovery. First-to-last visible skew must remain below
  one accepted display period.
- Run the dense streamed canary and complete Python animation sweep again.

### Gate H3: geometry and visual seams

- Verify the same installation-profile digest on all receivers.
- Photograph foliage/globe-scoped hue output and the native geometry canary
  across every receiver boundary and relevant globe region.
- Compare accepted host and receiver preview frames for representative timestamps,
  allowing only documented quantization/timing differences.
- Verify live vibe/profile changes do not reset semantic state.

### Gate H4: soak and release

- Run separate 30-minute default and maximum-work native-background-plus-clock
  soaks.
- Require zero reset, watchdog, missed declared-cadence deadline, unexplained
  bundle/payload/mode change, CRC/queue error, or visible corruption.
- Retain render/composite/encode/display percentiles, SPI payload/error series,
  start skew, drift, memory, and cache state.
- Return to a Python full scene without rebooting or flashing after each soak.
- Keep the prior firmware, Python fallback, and native artifact until the new
  path remains accepted through ordinary use.

Only then may `receiver_native_modules`, sparse foreground, or geometry support
be enabled by default or described as production-ready.

## Migration and Rollback

1. Complete Phase 0 through app rollback and automatic provisioning acceptance;
   keep legacy shell entrypoints until wall parity is recorded.
2. Deploy host code that understands old and new status while every new feature
   is off. Legacy streaming remains the only active mode.
3. Migrate persistence to versioned desired display state and prove legacy
   single-animation round trips.
4. Enable vibe and host composition independently; either can be disabled
   without firmware changes.
5. Implement and qualify the explicit receiver-mode baseline plus sparse
   foreground portably, then exercise a strict readable one-receiver canary;
   local playback remains off by default.
6. Use the explicitly degraded hybrid showcase only for visual/product evidence.
   After return-path repair, qualify all four and enable the compiled background,
   sparse foreground, and geometry as separate strict gates.
7. Publish and install the native pilot without auto-start. Verify all hashes,
   identities, cache entries, and rollback artifacts.
8. Opt into dynamic native playback only after unanimous receiver readiness.

Rollback layers remain independent:

- **Deployment attempt:** retain logs and receipts for failure/interruption;
  resume only idempotent provisioning phases.
- **Scene:** select a known Python background-only scene.
- **Foreground:** clear the generation and continue the base.
- **Native artifact:** reserve and stage the candidate while the prior
  bundle/payload binding remains pinned; restore that binding on any activation
  failure.
- **Application:** preserve artifact/calibration libraries; refuse incompatible
  downgrade until an explicit recovery operation has taken host ownership, then
  perform app-only rollback without receiver mutation.
- **Firmware:** retain prior images and partition data; receiver cache may be
  erased because the Pi library is authoritative.
- **Persistent state:** unknown schema/provider/bundle binding fails to the stored
  Python fallback rather than starting a competing default animation.

## Deferred and Non-Goals

- No arbitrary dashboard upload, third-party native content, signature system,
  or claim that native modules are sandboxed.
- No wholesale merge of `native-animations`.
- No frame-track/GIF/WebP receiver provider in version 1.
- No general render graph, multiple receiver-native layers, blend-mode zoo, or
  receiver-rendered clock.
- No strict cross-board v-sync, distributed simulation state, or atomic physical
  LED latch across four independent receivers.
- No live receiver framebuffer readback; native previews are authoring-time
  representations.
- No target compilation or target ELF execution in the dashboard/controller.
- No mask copy embedded in every native package.
- No automatic move of all plant modifiers or neighbor-sampling transforms to
  firmware.
- No claim that sparse foreground avoids full receiver composition or WS2812
  output.
- No broad deployment-provider or grouped-transaction abstraction until two
  accepted backends demonstrate genuinely shared lifecycle behavior.
- No mandatory rewrite of all existing plugins or 290 curated presets before
  the vertical slice ships.

## Phase 1 Resolved Decisions

The exact versioned vocabulary, timing/epoch meaning, skew/drift acceptance,
stale policies and leases, authored/effective speed boundary, pilot selections,
ownership matrix, wire sizes, and failure vocabulary are frozen in
[ANIMATION_PIPELINE_CONTRACT_V1.md](ANIMATION_PIPELINE_CONTRACT_V1.md). In
summary:

- Stable vibe IDs are `neutral`, `quiet`, `cozy`, `vivid`, and
  `celebration`; exact role-color payloads remain versioned Phase 2A data.
- `next_deadline_scene_time` is absolute unscaled seconds since the scene epoch;
  serialized scheduling uses unsigned microseconds since a controller monotonic
  epoch, and the controller session remains an opaque 128-bit ID.
- Clock/HUD, alert, and decorative stale policies use explicit 3-second,
  15-second, and hold contracts respectively.
- Compatibility timing uses an ephemeral authored x vibe x operator speed view;
  it never persists or mutates authored values.
- Pilot selections are Clock, `aurora_curtains`, `snake`, `world_flags`, future
  `aurora_curtains_native`, and receiver-safe `hue_shift`.
- The dormant foreground protocol reserves exact big-endian IDs and sizes; a
  4,096-byte transfer carries 1,016 RGBA pixels, so one 8 x 138 receiver
  snapshot is the golden two-patch sequence `[0, 1016)`, `[1016, 1104)`.

These resolutions do not reopen the core boundaries: vibe stays independent,
provider stays explicit, foreground uses alpha, the Pi remains authoritative,
complete host frames remain fallback, and unsigned native code remains
restricted to trusted repository builds.

## Assumptions

- The installation remains one Mac development machine, one Raspberry Pi, and
  four ESP32-S3 receivers; no hosted registry or telemetry service is required.
- `just deploy` ultimately performs full desired-state reconciliation from a
  clean tree; `just deploy-dirty` is the explicit development exception.
- Successful deployment output is ephemeral, while receipts and failure logs
  remain durable and redacted.
- Application health failure automatically restores the prior app release.
  Firmware failure remains in place with explicit recovery and no false
  whole-system rollback claim.
- All four receivers run the same baseline firmware and expose stable logical
  identity/global offset.
- Application releases, runtime state, native artifact library, calibration
  library, firmware images, and receiver cache remain separate deployment
  domains.
- The startup renderer remains compiled into baseline firmware and independent
  of receiver cache contents.
- Current Python full-frame streaming remains supported indefinitely as fallback
  and for components that do not fit the receiver-native or overlay contracts.
- Physical deployment or wall operation is performed only in an explicitly
  scheduled hardware acceptance step.
