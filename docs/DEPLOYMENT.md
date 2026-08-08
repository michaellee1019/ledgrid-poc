# Deployment

Deployment targets `ledgridwall@ledgridwall.local` and `~/ledgrid-pod` by
default. Override `PI_HOST` or `DEPLOY_DIR` for another installation.

The installed wall currently has a confirmed SPI1 MISO-to-MOSI electrical
short/coupling. Do not use deployment as a wiring test and do not bypass the
four-receiver readiness gate. Repair the powered-off wall and run the
pre-deploy hardware check in the authoritative
[cold-resume handoff](plan-native-animations.md#cold-resume-handoff-2026-08-08)
before `just deploy`.

## Command surface

Use `just` recipes rather than invoking deployment helpers directly:

| Recipe | Purpose |
| --- | --- |
| `just setup-web` | Create the local web/preview environment |
| `just setup` | Prepare SSH, Pi permissions, SPI, and firmware tooling |
| `just test-unit` | Run Python unit and plugin tests |
| `just test-rendering` | Run frame-contract and render-performance checks |
| `just test-native-animations` | Cross-build and benchmark the checked-in native-module catalog |
| `just test-firmware` | Run native firmware tests and build production firmware |
| `just test-deployment` | Test deployment state and file selection logic |
| `just test` | Run every required local gate |
| `just preflight` | Alias for the full test gate |
| `just deploy-precheck` | Full test gate used by deployment |
| `just deploy` | Precheck, sync the application, provision, flash changed firmware, and restart |
| `just deploy-python` | Sync application files and restart without provisioning or firmware flash |
| `just fetch-wall-data` | Fetch Pi-saved masks and runtime presets for review |

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

The sync set contains Git-tracked working-tree files plus non-ignored new files
under the approved application roots, including `firmware_animations/`. This
allows a coherent uncommitted feature to deploy without admitting root-level
scratch files, ignored caches, editor state, or calibration photos.

A full sync removes stale managed files but preserves target-owned state:

- `run_state/`
- `presets/animations/`
- Python and PlatformIO environments/build caches
- runtime logs

The Pi-authoritative `.lga` library lives under
`run_state/firmware_animations/`, so ordinary full syncs preserve installed
packages. Receiver caches remain disposable and are rebuilt from that library.

Built-in plugin code, manifests, curated presets, tests needed by acceptance,
and owned assets deploy from `animation/plugins/<plugin_id>/`. The runtime
preset overlay is never the source of curated content.

## Full and Python-only flows

Initialize production receiver-animation security once, passing stable USB
serial paths in logical wall order:

```bash
just provision-native-animations \
  '/dev/serial/by-id/receiver-0,/dev/serial/by-id/receiver-1,/dev/serial/by-id/receiver-2,/dev/serial/by-id/receiver-3'
```

The current workstation was already provisioned as of 2026-08-08. The ignored
`run_state/firmware_authoring/` directory holds the private/public keypair,
public deployment environment, explicit port map, and three signed example
packages. Do not rerun initialization merely to resume, print or track key
contents, or copy the private key to the Pi. Back up the private key securely.

`just deploy` always runs `deploy-precheck`. It then syncs the deployment
manifest and public provisioning, builds four isolated images with logical IDs
0–3 and a common public trust key, flashes only the configured stable ports,
installs the signed examples into the managed Pi gallery, configures both Pi
processes from the same environment file, and restarts the service. Deployment
fails unless all four LGS3 reports return the expected logical identity and the
signed-package, upload, native, frame-track, and typed-parameter capabilities.
Readiness ignores status preserved from the previous service process and polls
fresh telemetry for up to 30 seconds, so receiver initialization is not treated
as a failure. The firmware/platform/provisioning hash avoids unnecessary
reflashes.

`just deploy-python` is for changes that do not affect firmware, Pi packages,
permissions, or boot configuration. It verifies the existing target environment,
syncs the application subset, preserves the active animation settings, restarts
the service, restores those settings, and checks `/api/status`.

Do not use the Python-only flow after changing any of:

- `firmware/esp32/`
- dependency or environment setup
- SPI boot configuration
- systemd/startup behavior
- receiver signing keys or stable port assignments

## Runtime presets

Presets saved in the web UI belong to the deployment host. Retrieve them without
overwriting curated plugin presets:

```bash
just fetch-wall-data
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

   These commands qualify streamed-frame transport. They do not install signed
   packages or close native-render/frame-decode timing, crash fallback,
   restart-adoption, skew/drift, or soak gates.

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
- If readiness reports receivers 2 and 3 at status version 0, stop. On the
  installed wall, logical 2 is `/dev/spidev1.1` on CE1 (GPIO 17, pin 11),
  logical 3 is `/dev/spidev1.0` on CE0 (GPIO 18, pin 12), and both return from
  ESP GPIO 13 over shared Pi GPIO 19 / pin 35. The 2026-08-08 stopped-service
  tests proved that return net is coupled/shorted to MOSI GPIO 20 / pin 38;
  follow the [powered-off isolation procedure](plan-native-animations.md#powered-off-isolation-and-repair).
- Streamed output working on SPI1 is not contrary evidence: ordinary frames are
  Pi-to-ESP MOSI traffic, while LGS3 identity, capability, upload, and operation
  acknowledgement require MISO.
- If electronic gates or visual acceptance fail, restore the last validated
  application/firmware pair before continuing experiments.
