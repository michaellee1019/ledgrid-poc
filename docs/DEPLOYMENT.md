# Deployment

Deployment targets `ledgridwall@ledgridwall.local` and `~/ledgrid-pod` by
default. Override `PI_HOST` or `DEPLOY_DIR` for another installation.

## Command surface

Use `just` recipes rather than invoking deployment helpers directly:

| Recipe | Purpose |
| --- | --- |
| `just setup-web` | Create the local web/preview environment |
| `just setup` | Prepare SSH, Pi permissions, SPI, and firmware tooling |
| `just test-unit` | Run Python unit and plugin tests |
| `just test-rendering` | Run frame-contract and render-performance checks |
| `just test-firmware` | Run native firmware tests and build production firmware |
| `just test-deployment` | Test deployment state and file selection logic |
| `just test` | Run every required local gate |
| `just preflight` | Alias for the full test gate |
| `just deploy-precheck` | Full test gate used by deployment |
| `just deploy` | Precheck, sync the application, provision, flash changed firmware, and restart |
| `just deploy-python` | Sync application files and restart without provisioning or firmware flash |
| `just fetch-presets` | Fetch Pi-saved runtime presets for review |

`deploy-no-firmware` is retained as a compatibility alias for
`deploy-python`; use the canonical name in new automation and documentation.

## First deployment

Prerequisites:

- Raspberry Pi OS with SSH enabled
- passwordless SSH for `ledgridwall@ledgridwall.local`
- the deploy user able to obtain passwordless sudo after setup
- all expected ESP32 USB serial devices attached when firmware must be flashed
- a reboot window if SPI device-tree settings need to change

Run:

```bash
just setup
just deploy
```

Setup installs PlatformIO in a dedicated environment on the Pi and verifies
serial permissions. The full deployment applies the supported SPI boot
configuration and reports whether a reboot is required. After that reboot,
confirm the expected `/dev/spidev0.0`, `0.1`, `1.0`, and `1.1` nodes and rerun
the full deployment.

## What is deployed

The sync set is derived from Git-tracked files in the working tree. This keeps
untracked caches, editor state, calibration photos, and local experiments off
the controller while still deploying intentional uncommitted edits to tracked
files.

A full sync removes stale managed files but preserves target-owned state:

- `run_state/`
- `presets/animations/`
- Python and PlatformIO environments/build caches
- runtime logs

Built-in plugin code, manifests, curated presets, tests needed by acceptance,
and owned assets deploy from `animation/plugins/<plugin_id>/`. The runtime
preset overlay is never the source of curated content.

## Full and Python-only flows

`just deploy` always runs `deploy-precheck`. It then syncs the tracked source,
ensures the Pi environment and SPI configuration are usable, flashes receiver
firmware only when its source hash changed, installs application dependencies,
and restarts the service.

`just deploy-python` is for changes that do not affect firmware, Pi packages,
permissions, or boot configuration. It verifies the existing target environment,
syncs the application subset, preserves the active animation settings, restarts
the service, restores those settings, and checks `/api/status`.

Do not use the Python-only flow after changing any of:

- `firmware/esp32/`
- dependency or environment setup
- SPI boot configuration
- systemd/startup behavior

## Runtime presets

Presets saved in the web UI belong to the deployment host. Retrieve them without
overwriting curated plugin presets:

```bash
just fetch-presets
```

Fetched files remain ignored under `presets/animations/<plugin_id>/`. Review a
candidate, normalize it, move it into
`animation/plugins/<plugin_id>/presets/`, and run the plugin preset tests before
committing it. The automatic deployment-state snapshot is operational state and
must not be curated.

## Verification after deployment

1. Confirm `http://ledgridwall.local:5000/api/status` is current and the UI
   lists the expected manifest-backed plugins.
2. Check `driver_stats.device_map`, geometry, and receiver integrity counters.
3. For transport or firmware changes, run:

   ```bash
   just receiver-acceptance
   just live-animation-sweep
   just output-rate-sweep
   ```

4. Visually inspect every controller and lane. Clean CRC/DMA counters cannot
   detect faults after the receiver output peripheral.

Use the thresholds and rollback procedure in
[Rendering acceptance](RENDERING_PIPELINE_ACCEPTANCE.md).

## Operations and diagnostics

```bash
just diagnose-remote
just diagnose-remote-restart
```

The first collects API, service, process, and log evidence. The second may also
clear a stale port binding and restart the web service. Output is written to the
ignored `diagnostics/remote_diagnostics.out` file.

For manual service operations, use the deployment service helper:

```bash
tools/deployment/stop_remote.sh status
tools/deployment/stop_remote.sh restart
tools/deployment/stop_remote.sh stop
```

## Failure handling

- If precheck fails, fix the local failure; do not bypass it.
- If setup changes boot configuration, reboot and verify device nodes before
  continuing.
- If firmware flash fails, the source hash is not recorded, so the next full
  deployment retries it.
- If the service health check fails, run remote diagnostics before another
  deployment.
- If electronic gates or visual acceptance fail, restore the last validated
  application/firmware pair before continuing experiments.
