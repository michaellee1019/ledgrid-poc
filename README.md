# LED Grid Control System

Controller, web UI, animation plugins, and ESP32-S3 firmware for a 33 x 138
(4,554-pixel) plant-wall installation. A Raspberry Pi renders frames and sends
them over two SPI buses to five receivers. Four receivers expose eight logical
WS2812 lanes and the fifth drives the extra rightmost strip as one logical lane
through an explicit all-output broadcast mask on its otherwise dedicated board.
The camera-measured physical order is logical receivers `(0,1,2,3,4)` from
left to right, at global strip offsets `(0,8,16,24,32)`.

## Local development

The repository uses `just` as its command entry point and `uv` with a committed
lockfile for reproducible runtime, test, calibration, and firmware-tool groups.

```bash
just setup-local
just setup-web
just test
just start
```

`just start` runs the web/preview process at <http://127.0.0.1:5000>. Hardware
output runs as a separate controller process on the Raspberry Pi.

For a Mac-only dashboard with no controller process or LED hardware, run
`just start-mac`. It builds a separate 30 FPS contact-preview catalog and runs
the full-size software renderer at the browser's display cadence on localhost.
The first run takes longer while those local-only WebP loops are generated;
content-addressed results are reused on later launches.

## Repository layout

- `animation/core/`: plugin framework, manager, and lifecycle contracts
- `animation/libraries/`: reusable rendering and simulation primitives shared by
  multiple plugins, with colocated tests
- `animation/plugins/<plugin_id>/`: one self-contained package per animation,
  including its manifest, curated presets, tests, and owned assets
- `drivers/`: host-side frame transport and LED layout
- `firmware/esp32/`: ESP32-S3 receiver firmware and native tests
- `ipc/`: file-based web/controller communication
- `scripts/`: runtime and calibration entry points
- `tools/`: deployment, diagnostics, and acceptance utilities
- `web/`: Flask application and templates
- `config/`: production plant-wall geometry and semantic masks

The repository documents the runtime wiring contract for assembled hardware but
does not contain PCB/schematic source, a BOM, or fabrication outputs. See
[Hardware and wiring](docs/HARDWARE.md) for the known configuration and the
explicit as-built gaps.

The root `presets/animations/` tree is a runtime/user-writable overlay. Curated
presets belong to the plugin that owns them.

## Hardware deployment

```bash
just setup             # prepare the Pi and local web environment
just deploy-precheck   # local validation without changing the Pi
just deploy-plan       # read-only source and deployment-step accounting
just deploy            # full application and firmware deployment
```

`just deploy` and `just deploy-python` require a clean worktree. Use the
corresponding `*-dirty` recipe only for an intentional development deployment;
it records the base commit, selected diff digest, and safe untracked files. Use
`*-verbose` to stream the normally captured deployment log. The deployment
target defaults to `ledgridwall@ledgridwall.local`. Both commands stage an
immutable release, atomically select `current`, require advancing release-aware
health, and persist matching local/target receipts. Use `just releases` to
inspect release state, `just rollback <release-id>` for an application-only
rollback, and the explicitly named `*-legacy` recipes only for recovery.

Receiver-native software is present but remains default-off and is not yet a
production-accepted wall path. Repository-owned modules use the explicit
`just native-plan`, `just native-build`, `just native-publish`,
and `just native-install` recipes; ordinary `just deploy` never installs or
activates a module. The retired `native-start` and `native-run` compatibility
commands fail before target access or partial build/publication/install work;
activation now goes through Composer Check and guarded activation. The H2 and
H4 evidence recipes require the exact scene digest from that activation receipt,
default to real 1,800-second read-only observations, never restore or otherwise
mutate the wall, and remain supporting evidence until every companion and
photographed gate is complete. See
[Deployment](docs/DEPLOYMENT.md#receiver-native-deployment) and
[Rendering acceptance](docs/RENDERING_PIPELINE_ACCEPTANCE.md#phase-4-receiver-native-software-and-physical-evidence).

## Required checks

Before merging or deploying a change:

1. `just test` passes.
2. Every discovered plugin has a valid manifest and its focused tests and
   curated presets live inside the plugin package.
3. `just deploy-precheck` reports no missing source, configuration, or runtime
   asset.
4. Rendering or transport changes also pass `just test-rendering`.
5. Firmware changes pass the receiver and full-wall gates in
   [Rendering acceptance](docs/RENDERING_PIPELINE_ACCEPTANCE.md).
6. Calibration changes satisfy the photographed checks in
   [Plant-wall calibration](docs/PLANT_WALL_CALIBRATION.md).

## Documentation

- [Animation plugins](docs/ANIMATION_SYSTEM.md)
- [Architecture](docs/ARCHITECTURE_DIAGRAM.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Hardware and wiring](docs/HARDWARE.md)
- [Debugging](docs/DEBUGGING.md)
- [Metrics](docs/METRICS.md)
- [GIF asset pipeline](docs/GIF_PIPELINE.md)
- [Plant-wall calibration](docs/PLANT_WALL_CALIBRATION.md)
- [Unified delivery and animation roadmap](docs/plan-revamped-animation-pipeline.md)

Repository documentation describes the current supported system. Use Git
history for change history and abandoned approaches. The `native-animations`
branch is a retained prototype/organ donor for the roadmap's Phase 3 and Phase 4
work; it is not the deployed architecture or a merge target. The roadmap records
the reusable commits and the signing, frame-track, exclusive-mode, and UI pieces
that must not be ported unchanged.
