# System architecture

The Raspberry Pi owns the animation library, user state, orchestration, and SPI
transport. It renders and schedules Python plugins. In the separate
`firmware_animation` mode, four ESP32-S3 receivers run signed native modules or
pre-encoded frame tracks from their local caches and clocks.

```text
browser
   │ HTTP
   ▼
web process (Flask) ── control/status files ── controller process
                                                │
                             Python registry    ├─ plugin + frame scheduler
                             signed .lga library ├─ receiver orchestrator
                             animation manager  ├─ explicit provider/mode
                             host driver         └─ metrics
                                                │ two SPI buses
                         ┌──────────────────────┴──────────────────────┐
                         ▼                                             ▼
                   ESP32-S3 x2                                    ESP32-S3 x2
                   on SPI0                                        on SPI1
                         │                                             │
                         └──────────── 32 WS2812 lanes total ──────────┘
```

## Processes and ownership

The deployed service starts two Python processes:

- The controller process loads allowlisted plugins and the Pi-authoritative
  `.lga` library, owns the active provider/mode, renders streamed frames or
  orchestrates receiver-local playback, and writes status.
- The web process serves the UI and API, writes commands, and reads controller
  status. It does not own hardware or live animation state.

`ipc/control_channel.py` is the boundary between them:

```text
run_state/control.json   web -> controller commands
run_state/status.json    controller -> web status and preview data
```

Both files are runtime artifacts and are not versioned. Atomic replacement in
the channel prevents readers from observing a partially written JSON document.
The shared firmware-animation library separately uses an interprocess
shared/exclusive file lock plus atomic publication so controller reads cannot
race web installs, replacement, deletion, or recovery.

## Plugin registry

Built-in plugins are packages under `animation/plugins/<plugin_id>/`. Their
manifest, implementation, curated presets, focused tests, and owned assets move
together. `animation/core/` contains framework and lifecycle contracts;
`animation/libraries/` contains reusable rendering or simulation primitives.

The allowlist is an exposure boundary: discovery alone does not make a package
available to the UI. The loader validates package and manifest identity before
the manager registers a plugin.

See [Animation system](ANIMATION_SYSTEM.md) for both backend contracts.

## Streamed Python frame path

1. The animation manager asks the active plugin for a canonical RGB frame.
2. The manager applies scheduling and presentation hints, then passes the frame
   to `MultiDeviceLEDController`.
3. The controller divides the logical strip-major frame into four receiver
   chunks.
4. Devices sharing an SPI bus are sent serially; SPI0 and SPI1 transfers can
   overlap.
5. Each receiver validates CRC-16, publishes the newest complete RGB frame to a
   three-slot mailbox, and accounts for superseded frames.
6. A separate receiver task converts RGB into an eight-lane WS2812 waveform and
   submits it through the ESP-IDF LCD/I80 DMA peripheral.

The installed geometry is 32 strips x 138 LEDs. Each receiver owns eight strips
and retains firmware capacity for up to 140 LEDs per strip.

## Receiver-local animation path

1. The dashboard installs a signed `.lga` into the persistent Pi library; the
   web and controller processes exchange only a managed package path and ID.
2. The controller probes all four receiver caches and uploads only missing
   device payloads through ordered, idempotent chunks.
3. Each receiver validates the signed envelope, device digest, ABI/target,
   geometry, logical-device identity, and payload structure before making its
   cache entry visible.
4. The controller verifies all four caches, then starts receivers in
   deterministic order with offsets 0, 8, 16, and 24.
5. Each receiver renders an import-free native module or decodes its `LGT1`
   track using its local clock. Strict v-sync and common-clock scheduling are
   intentionally deferred.
6. A complete host frame stops local playback and returns display ownership to
   the streamed path.

If a transfer fails after begin, the host aborts every receiver that may have
entered maintenance, removes and re-probes possibly committed payloads, and
exposes any residue. Start, stop, and live-parameter failures reconcile receiver
status and retain a degraded/fail-closed host state unless a unanimous safe
state is proved; they do not silently declare a mixed wall healthy.

The package WebP is an authoring-time dashboard preview. Receivers do not expose
a framebuffer readback, so the preview is not proof of the exact physical frame.

## Receiver protocol

Commands are CRC-protected and defined by the host driver and firmware protocol
implementation. Bulk `SET_ALL` is the normal frame path; `SET_PIXEL`,
`SET_RANGE`, `SHOW`, `CLEAR`, `SET_BRIGHTNESS`, and `CONFIG` support incremental
or control operations.

The receiver returns the sole 128-byte `LGS3` status snapshot over MISO with
packet, CRC, mailbox, frame, display, capability, cache/upload, active digest,
render/decode, and quarantine fields. LGS1/LGS2 and mixed-version deployments
are intentionally unsupported. Status for a command appears on a later
transfer because the concurrent SPI response was queued before that command.
With the receiver's queue depth of two, the host sends the command and then
clocks two complete 128-byte status transfers before interpreting its
acknowledgement.

These counters cover the path only through ESP32 output DMA. WS2812 lanes have
no return channel, so visual output still requires physical acceptance.

## State and presets

The controller persists the current animation and applied settings so a service
restart can restore the last valid state. This deployment snapshot is operational
state, not a curated preset.

Receiver-local playback survives a Pi disconnect or controller-process restart.
Persisted provider, package digest, and parameters let the manager adopt the
retained mode. The Pi package library is authoritative; receiver caches are
disposable and recoverable.

Curated presets live inside plugin packages. Presets saved through the UI live
under `presets/animations/<plugin_id>/` on the deployment host and remain
untracked unless deliberately promoted into the owning plugin.

## Configuration boundaries

- `drivers/led_layout.py`: installed host geometry
- `animation/core/`: plugin lifecycle and manager contracts
- `animation/libraries/`: cross-plugin rendering and simulation primitives
- `firmware/esp32/`: receiver capacity, pins, waveform, and protocol
- `firmware_animations/`: signed package SDK, library, tracks, and native ABI
- `config/`: calibrated wall projection and plant/globe masks
- environment variables and CLI flags: deployment-specific addresses, rates,
  brightness, and optional HAT layout

Do not duplicate these constants in documentation or plugin code when a runtime
source already exists.

## Verification boundaries

- Unit and plugin tests verify registry, simulation, transforms, and protocol.
- Headless rendering benchmarks verify frame contract and generation budget.
- Native firmware tests verify encoding, bounds, mailbox behavior, LGS3,
  package verification, local playback, and fallback state.
- Native-example benchmarks verify host-preview behavior and a desktop p95
  proxy; they do not measure ESP32 execution.
- Receiver acceptance verifies live SPI/DMA counters and must separately cover
  on-device render/decode, failure recovery, and soak behavior.
- The full-wall sweep verifies every exposed plugin and visually qualifies the
  physical signal path.

See [Rendering acceptance](RENDERING_PIPELINE_ACCEPTANCE.md) for thresholds.

Production receiver-animation provisioning is implemented: the full deploy
validates ignored workstation signing state, builds four isolated images with a
common trust key and logical identities 0–3, copies public material only,
installs the signed examples, and requires fresh LGS3 identity/capability
readback. Physical acceptance remains separate and is currently blocked by the
installed SPI1 return wiring; see the
[cold-resume handoff](plan-native-animations.md#cold-resume-handoff-2026-08-08).
