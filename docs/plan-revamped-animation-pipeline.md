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

The existing 32 x 138 wall, four 8 x 138 receivers, 200 Hz manager target, and
compiled startup rainbow remain the operating envelope and fallback. Existing
Python plugins and presets must continue to work throughout migration. Every
phase is independently useful, has an acceptance gate, and stops before the next
risk domain.

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
feature state, and retain the previous complete-frame path. Do not begin a later
phase merely because earlier code exists; its acceptance and stop boundary must
be satisfied. Subphases within Phase 0 ship separately in their listed order.

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
                      ├── Phase 3A receiver ownership/static background
                      │       └── Phase 3B sparse foreground
                      │               └── Phase 3C geometry profile
                      └── Phase 3D native peer build/library

Phases 3A + 3B + 3C + 3D
  → Phase 4 dynamic loader and wall release
      → Phase 5 evidence-driven expansion
```

### Phase 0: Safe and Reproducible Delivery Foundation

Complete the delivery foundation before changing receiver ownership or flashing
a loader-capable baseline. Host-only contract and prototype work may be developed
locally, but no later phase is production-ready until its relevant Phase 0
prerequisite passes.

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

- Add a manager-owned compositor with reusable output buffers and independent
  component scheduling.
- Add `OverlayFrame` and a separate `clock_overlay` plugin package using shared
  Clock layout/glyph/time helpers.
- Retain the existing Clock as a compatibility full scene and preserve its
  curated presets.
- Support exactly one Python background plus one aggregate foreground plane and
  source-over alpha.
- Implement previous/new coverage union, cached base and overlay frames,
  independent lifecycle, targeted interaction routing, and scene preview.
- Move the current universal framework plant-optics invocation to the composed
  output path for composed scenes. Prove it is applied exactly once and retain
  background-only compatibility.
- Initially flatten to the existing authoritative RGB path so firmware and
  transport remain unchanged.
- Extend benchmarks with compositor cost, scene changed ratio, overlay dirty
  pixels/ranges, and actual transmitted bytes.

#### Acceptance

- The clock renders over at least three representative backgrounds without
  restarting or advancing them when the clock changes.
- Transparent and opaque black, movement, removal, enable/disable, opacity,
  minute/second rollover, and plant-aware placement leave no stale pixels.
- Translation/clipping and two ordered overlapping logical overlays match the
  canonical layout, opacity, and per-fold rounding vectors even though the
  receiver-facing foreground is aggregate.
- A changing base recomposites stable foreground coverage correctly.
- Universal plant optics are applied once after composition; disabling them
  preserves the existing background-only frame byte for byte.
- Cached calls return `changed=False`; a 1 Hz clock does not render 200 times per
  second.
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

- Add versioned `SceneState`, component/scene preset separation, targeted live
  updates, manager status, IPC/API commands, and fixed-slot dashboard controls.
- Refactor plugin discovery into descriptor scanning plus the current Python
  loading adapter. Existing manifests retain a compatibility path.
- Validate provider/role/capability fields without introducing a generic
  lifecycle DSL.
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

### Phase 3A: Explicit Receiver Ownership and Statically Linked Background

Establish safe receiver-local playback without packages, cache, or dynamic code.

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

- Convert the easiest duplicated procedural palette families to semantic roles
  and remove manifest-local vibe bridges only when their presets remain visually
  accepted.
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
- the dense streamed capacity canary retains at least 180 displayed FPS with the
  existing accounting and integrity requirements;
- a representative changed clock tick uses less than 10 percent of one complete
  wall RGB frame, and a fixed 60-second native 60 Hz background plus 1 Hz clock
  run reduces total Pi animation payload by at least 90 percent versus complete
  RGB streaming after all control/status/repair bytes are counted;
- no benchmark result is described as Raspberry Pi or ESP32 evidence unless it
  ran on that hardware.

## Physical Acceptance and Release Gates

Hardware work is never implied by code completion. Schedule it explicitly after
portable tests pass.

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
5. Qualify the explicit receiver-mode baseline on one receiver, then all four,
   while local playback remains off by default.
6. Enable the compiled background canary, then sparse foreground, then geometry,
   as separate gates.
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

## Open Decisions to Close in Phase 1

- Final names and visual definitions for the five initial vibe IDs.
- Exact schema/version names for descriptors, scenes, runtime context, native
  ABI, unsigned bundle, status, and foreground protocol.
- Whether `next_deadline` is absolute scene time or a bounded relative duration;
  the same meaning must be used by Python preview and firmware.
- The common scene-epoch source and acceptable measured skew/drift for analytic
  backgrounds.
- Default foreground stale policies and lease intervals for clock, alerts, and
  decorative layers.
- The simplest compatibility representation for current global speed while
  authored/effective state is separated.
- Which one procedural family, game/stateful plugin, and exact-color plugin form
  the vibe pilot set.
- Which analytic background becomes the first repo-peer native pilot.
- Which stateless installation transform provides the clearest first physical
  parity test.

These decisions may choose names and numeric bounds. They may not reopen the
core boundaries: vibe stays independent, provider stays explicit, foreground
uses alpha, the Pi remains authoritative, complete host frames remain fallback,
and unsigned native code remains restricted to trusted repository builds.

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
