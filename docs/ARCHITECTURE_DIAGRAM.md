# System architecture

The Raspberry Pi owns animation simulation, frame scheduling, user state, and
SPI transport. Five ESP32-S3 receivers validate frame packets and drive the
physical strips. The receivers do not run animation logic.

```text
browser
   │ HTTP
   ▼
web process (Flask) ── control/status files ── controller process
                                                │
                             animation registry ├─ plugin package
                             animation manager  ├─ frame scheduler
                             host driver         └─ metrics
                                                │ two SPI buses
                         ┌──────────────────────┴──────────────────────┐
                         ▼                                             ▼
                   ESP32-S3 x2                                    ESP32-S3 x3
                   on SPI0                                        on SPI1
                         │                                             │
                         └──────────── 33 WS2812 lanes total ──────────┘
```

## Processes and ownership

The deployed service starts two Python processes:

- The controller process loads allowlisted plugins, owns the active animation,
  renders frames, sends them to hardware, and writes status.
- The web process serves the UI and API, writes commands, and reads controller
  status. It does not own hardware or live animation state.

`ipc/control_channel.py` is the boundary between them:

```text
run_state/control.json   web -> controller commands
run_state/status.json    controller -> web status and preview data
```

Both files are runtime artifacts and are not versioned. Atomic replacement in
the channel prevents readers from observing a partially written JSON document.

## Plugin registry

Built-in plugins are packages under `animation/plugins/<plugin_id>/`. Their
manifest, implementation, curated presets, focused tests, and owned assets move
together. `animation/core/` contains framework and lifecycle contracts;
`animation/libraries/` contains reusable rendering or simulation primitives.

The allowlist is an exposure boundary: discovery alone does not make a package
available to the UI. The loader validates package and manifest identity before
the manager registers a plugin.

See [Animation plugins](ANIMATION_SYSTEM.md) for the package and frame contracts.

## Frame path

1. The animation manager asks the active plugin for a canonical RGB frame.
2. The manager applies scheduling and presentation hints, then passes the frame
   to `MultiDeviceLEDController`.
3. The controller divides the logical strip-major frame into five receiver
   chunks.
4. Devices sharing an SPI bus are sent serially; SPI0 and SPI1 transfers can
   overlap.
5. Each receiver validates CRC-16, publishes the newest complete RGB frame to a
   three-slot mailbox, and accounts for superseded frames.
6. A separate receiver task converts RGB into an eight-lane WS2812 waveform and
   submits it through the ESP-IDF LCD/I80 DMA peripheral.

The installed geometry is 33 strips x 138 LEDs. Four receivers own eight strips
each; the fifth owns only the extra rightmost strip and broadcasts that one
semantic strip across its physical outputs with lane mask `0xff`. Firmware still
retains capacity for up to 140 LEDs per strip. The camera-measured physical
receiver order is `(0,1,2,3,4)`, producing explicit global offsets
`(0,8,16,24,32)` by logical receiver ID; transport routes and direction flags
remain separate configuration domains.

## Receiver protocol

Commands are CRC-protected and defined by the host driver and firmware protocol
implementation. Bulk `SET_ALL` is the normal frame path; `SET_PIXEL`,
`SET_RANGE`, `SHOW`, `CLEAR`, `SET_BRIGHTNESS`, and `CONFIG` support incremental
or control operations.

The receiver returns a 320-byte `LGS3` status snapshot over MISO. Its first 64
bytes preserve the v2 packet, CRC, mailbox, frame, and display timing offsets;
the extension reports explicit ownership, capabilities, context digests,
receiver identity, cadence, and command-correlated results. These counters
cover the path only through ESP32 output DMA. WS2812 lanes have no return
channel, so visual output still requires physical acceptance.

## State and presets

The controller persists the current animation and applied settings so a service
restart can restore the last valid state. This deployment snapshot is operational
state, not a curated preset.

Curated presets live inside plugin packages. Presets saved through the UI live
under `presets/animations/<plugin_id>/` on the deployment host and remain
untracked unless deliberately promoted into the owning plugin.

## Configuration boundaries

- `drivers/led_layout.py`: installed host geometry
- `animation/core/`: plugin lifecycle and manager contracts
- `animation/libraries/`: cross-plugin rendering and simulation primitives
- `firmware/esp32/`: receiver capacity, pins, waveform, and protocol
- `config/`: calibrated wall projection and plant/globe masks
- environment variables and CLI flags: deployment-specific addresses, rates,
  brightness, and the alternate, non-installed HAT compatibility layout; the
  repository does not include that carrier's hardware design

Do not duplicate these constants in documentation or plugin code when a runtime
source already exists.

## Verification boundaries

- Unit and plugin tests verify registry, simulation, transforms, and protocol.
- Headless rendering benchmarks verify frame contract and generation budget.
- Native firmware tests verify encoding, bounds, mailbox behavior, and status.
- Receiver acceptance verifies live SPI and DMA counters.
- The full-wall sweep verifies every exposed plugin and visually qualifies the
  physical signal path.

See [Rendering acceptance](RENDERING_PIPELINE_ACCEPTANCE.md) for thresholds.

## Planned evolution and prototype boundary

Everything above describes the current deployed host-frame pipeline. The
[unified roadmap](plan-revamped-animation-pipeline.md) preserves it as fallback
while adding a fixed scene model, independent vibe state, a receiver-local
background, and a sparse premultiplied-RGBA foreground. Until the corresponding
phase passes acceptance, receivers still do not own selectable animation logic.

The `native-animations` branch is retained as an organ donor for that work, not
as an alternate architecture to deploy. Its ESP-IDF baseline, ABI, loader,
cache/status machinery, and four-board orchestration are reference
implementations. Its exclusive display mode, signed package/index, frame-track
player, separate catalog/gallery, and partial-RGB takeover semantics conflict
with the planned base-plus-foreground architecture and must be redesigned.
