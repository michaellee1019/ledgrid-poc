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
- `tools/benchmarks/native_animations.py`: compiles the checked-in native module
  catalog and measures default/stress callbacks at exact 8x138 geometry.
- `tools/benchmarks/live_animation_sweep.py`: live plugin integrity sweep.
- `firmware/esp32/`: receiver startup renderer, SPI protocol, status encoding,
  frame mailbox, parallel LED driver, native tests, and production build.
- `drivers/spi_controller.py` and `drivers/multi_device.py`: host protocol,
  receiver telemetry, logical device mapping, and all-board orchestration.
- `web/` and `ipc/control_channel.py`: dashboard/API exposure and the boundary
  between the web and hardware-owning controller processes.
- `docs/ANIMATION_SYSTEM.md`: architecture and creation overview.
- `docs/RENDERING_PIPELINE_ACCEPTANCE.md`: rendering acceptance details.
- `docs/plan-native-animations.md`: approved direction for signed native modules,
  frame-track packages, receiver caching, and dashboard control.
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

## Receiver-side firmware animations

The architecture normally streams four logical 8-by-138 frame slices from the
Pi to four ESP32-S3 receivers. Receiver-local playback is a separate explicit
mode. The compiled startup rainbow uses the same native ABI renderer as its
uploadable example while remaining linked into the baseline image for recovery.
Work on receiver animations crosses the firmware, SPI driver, multi-device
controller, manager lifecycle, IPC status, persistence, and dashboard; do not
implement it as an isolated firmware renderer.

- Preserve the compiled startup animation as a boot and recovery path. Its
  wrapper must call the canonical native callback table directly without
  depending on SPIFFS, signature verification, or `elf_loader`.
- Keep host-frame, receiver-animation, startup/fallback, and maintenance states
  distinct. Brightness, configuration, status queries, and asset chunks must not
  accidentally switch display ownership; a complete host frame should.
- All four boards run the same source baseline and each owns eight consecutive
  logical strips, but production builds require distinct immutable
  `LEDGRID_LOGICAL_DEVICE` values 0–3. Pass the corresponding global strip
  offset to procedural modules and select the matching pre-sliced track for
  frame assets.
- The complete host/receiver SPI transfer ceiling is 4096 bytes including the
  two CRC bytes. Command bodies are at most 4094 bytes and asset chunks at most
  4089 bytes. Exercise the exact maximum packet.
- The installed 16 MB flash layout includes two 6 MiB OTA application slots and
  a 3.875 MiB filesystem partition. Treat that space as a cache, retain a free-space
  reserve, and measure filesystem overhead rather than budgeting only payload
  bytes.
- LGS3 is the sole 128-byte status contract. It carries packet, CRC, mailbox,
  encode, show, sequence, capability, active-digest, cache/upload,
  render/decode, operation-result, and quarantine fields. Do not restore LGS1,
  LGS2, prefix-compatibility, or mixed-version paths; all boards move together.
  Responses use a two-deep queue: after sending a command, the host must clock
  two complete 128-byte status transfers before interpreting its operation
  acknowledgement. The concurrent response or only one later snapshot is stale.
- The Pi library and package manifest are authoritative. Send filesystem paths
  through IPC only after resolving them inside the managed library; never place
  large binary payloads in the single JSON control file.
- The controller and web processes share the package library, so protect
  list/get/verify with a shared interprocess lock and install/delete/recovery
  with an exclusive lock in addition to atomic publication.
- Pause or freeze host presentation while an exclusive asset transfer uses SPI,
  keep status publication alive, and expose progress. Resume the prior mode when
  an install-only operation finishes or fails. On failure, abort every receiver
  that may have accepted begin before compensating removal/re-probe; expose
  abort failures and never leave an unreported maintenance/upload state. Treat
  abort as successful only when LGS3 also proves idle upload and a display mode
  other than maintenance.
- Do not expose firmware-local playback as a fake `AnimationBase`. The manager
  and persisted state use explicit provider/mode fields so Python controls,
  previews, plant modifiers, and target FPS are not applied accidentally.
- A receiver-local animation has no live framebuffer readback. Use the package
  preview in the dashboard and label it as a preview instead of presenting it as
  the exact current physical frame.
- Native modules require a baseline receiver firmware containing a loader and
  trust anchor. Subsequent package installation is not a firmware flash, but
  changing the loader ABI or trusted public key is.
- ABI v1 callbacks return `LEDGRID_ANIMATION_OK` (`0`) on success and nonzero on
  failure. `scaled_elapsed_us` already includes global tempo; module speed is an
  additional local multiplier and must be applied exactly once.
- ABI v1 native payloads are import-free. Use the helper table for deterministic
  randomness, HSV/RGB565 conversion, and sine/cosine; both package inspection
  and the target loader should reject an undefined symbol.
- Checked-in examples live in
  `firmware/esp32/src/native_examples/` and are enumerated by
  `firmware_animations/examples/native_catalog.json`. Each source has a named
  built-in entrypoint for the production smoke build and exports
  `ledgrid_animation_v1` when compiled as a package module.
- `LGT1` v1 accepts only infinite serialized track metadata
  (`loop_count == 0`). Preserve one-shot versus repeating author intent through
  the public boolean `loop` default/control; reject arbitrary nonzero on-wire
  repeat counts instead of silently ignoring them.
- Initialize production provisioning with `just provision-native-animations`
  and four stable `/dev/serial/by-id` paths in logical wall order. Ignored
  `run_state/firmware_authoring/` retains the private/public keypair, port map,
  and signed example packages. `just deploy` copies only the public material,
  builds isolated logical-device 0–3 images on the pinned platform, configures
  both Pi processes with the matching key, and fails unless all four LGS3
  reports return the expected identities and signed playback capabilities.
  Do not replace this with tty sorting, a shared logical-0 image, or an unsigned
  shortcut.
- The installed-wall state as of 2026-08-08 has a decisive physical blocker,
  recorded authoritatively in `docs/plan-native-animations.md`. Logical receiver
  2 is `/dev/spidev1.1` on CE1 (Pi GPIO 17/pin 11); logical receiver 3 is
  `/dev/spidev1.0` on CE0 (GPIO 18/pin 12). Both share SPI1 MISO on Pi GPIO 19/
  pin 35 to ESP GPIO 13. With the service stopped, both devices returned exact
  TX echo at 20/10/5/1 MHz for lengths 3 through 4096, random data, `no_cs`, and
  CS-high tests; unmuxed GPIO 19 followed MOSI GPIO 20 low/high/low with 20/20
  matching samples. SPI0 returned LGS3 at every tested speed, and the service
  was restored afterward. GPIO 19/pin 35 is electrically coupled/shorted to
  MOSI GPIO 20/pin 38. Frames can still stream outbound over MOSI, but signed
  package identity/ack/control requires MISO and must fail closed. Repair with
  all Pi, ESP, USB, and LED power removed; never add a code bypass for readiness.

The design and current implementation status are documented in
`docs/plan-native-animations.md`:
signed `.lga` packages built by a trusted local CLI, C/C++ shared objects loaded
on ESP32-S3, GIF/WebP-derived device tracks, a Pi-authoritative library, and
transactional all-board staging. Strict cross-board clock synchronization and
shared v-sync are deferred; retain measured skew/drift in hardware acceptance.

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

For receiver-side firmware animation work, also run the portable firmware tests
before any hardware request:

```bash
just test-native-animations
just test-firmware
```

The first command compiles standalone trusted host libraries and records
mean/p95/p99/max for default and maximum-work controls at 8x138 per callback.
The second runs the portable receiver suite and the ESP32-S3 production
cross-build. Neither is a physical timing result.

Run the focused host tests for LGS3 serialization/parsing, package and signature
validation, track encoding/decoding, IPC commands, mode persistence, and
all-board transaction failure. Physical acceptance must cover one receiver
before all four, switching back to streamed frames, Pi disconnect/restart,
interrupted uploads, cache eviction, module crash fallback, and a soak at each
payload's declared cadence. Do not flash or start the wall unless the user has
explicitly put hardware operation in scope.

## Performance interpretation

Measure the default and stress configurations separately. A fast mean can hide expensive spawn, lock, clear, or replan frames, so retain p95, p99, and maximum timings. Desktop results demonstrate relative code cost and acceptance-gate compliance; they do not prove Raspberry Pi latency. Prefer an algorithm whose per-frame work is structurally bounded before considering a lower render rate.
