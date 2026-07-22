# LED Grid Control System

Controller, web UI, animation plugins, and ESP32-S3 firmware for a 32 x 138
(4,416-pixel) plant-wall installation. A Raspberry Pi renders frames and sends
them over two SPI buses to four receivers; each receiver drives eight WS2812
lanes in parallel.

## Local development

The repository uses `just` as its command entry point and `uv` for isolated
test dependencies.

```bash
just setup-web
just test
just start
```

`just start` runs the web/preview process at <http://127.0.0.1:5000>. Hardware
output runs as a separate controller process on the Raspberry Pi.

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

The root `presets/animations/` tree is a runtime/user-writable overlay. Curated
presets belong to the plugin that owns them.

## Hardware deployment

```bash
just setup             # prepare the Pi and local web environment
just deploy-precheck   # local validation without changing the Pi
just deploy            # full application and firmware deployment
```

Use `just deploy-python` when firmware and boot configuration are unchanged.
The deployment target defaults to `ledgridwall@ledgridwall.local`.

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

Repository documentation describes the current supported system. Use Git
history for change history and abandoned approaches.
