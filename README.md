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

For a Mac-only Composer session with no controller process or LED hardware, run
`just start-mac`. It runs the full-size software renderer on localhost.

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

Run `just test-composer-current` for the focused local Composer journey. See
[current validation](docs/CURRENT_COMPOSER_VALIDATION.md).

## Hardware deployment

`just deploy-python` replaces application code; `just deploy` also builds and
flashes the receivers. Deployment stops playback, preserves personal data outside
the application directory, copies/builds, restarts, and checks readiness. A failed
step may leave the wall stopped. Correct the error and rerun deployment; there is
no automatic rollback requirement. See [Deployment](docs/DEPLOYMENT.md).

The Pi renders full RGB frames. Receiver-native modules and sparse overlays are
retired. Composer retains browser rendering while connected; offline editing is
not supported. Calibration is one editable configuration for this wall.

## Required checks

Use focused behavior tests, syntax checks, and `git diff --check`. Firmware changes
require the installed-target build and transport/LED tests. Browser wiring changes
require the affected phone/desktop journey. Persistence and protocol changes need
independent review before deployment. Installed checks confirm mapping, brightness,
playback and switching; local tests are not evidence of physical output.

## Documentation

- [Animation plugins](docs/ANIMATION_SYSTEM.md)
- [Architecture](docs/ARCHITECTURE_DIAGRAM.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Hardware and wiring](docs/HARDWARE.md)
- [Debugging](docs/DEBUGGING.md)
- [Metrics](docs/METRICS.md)
- [GIF asset pipeline](docs/GIF_PIPELINE.md)
- [Plant-wall calibration](docs/PLANT_WALL_CALIBRATION.md)
- [Current Composer contract](docs/CURRENT_UX_ACCEPTANCE.md)

Use Git history and local Beads for historical designs and acceptance evidence.
Current operation does not require qualification packages or exact displayed-scene receipts.
