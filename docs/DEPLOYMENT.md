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
| `just deploy-plan` | Read-only full source accounting and coordinator step plan |
| `just deploy` | Clean-tree precheck, sync, provision, changed-firmware flash, and restart |
| `just deploy-dirty` | Explicit full deployment of tracked edits plus allowlisted safe untracked source |
| `just deploy-verbose` | Clean full deployment with normally captured phase output streamed live |
| `just deploy-python` | Clean-tree application sync/restart without provisioning or firmware flash |
| `just deploy-python-plan` | Read-only Python-only source accounting and coordinator step plan |
| `just deploy-python-dirty` | Explicit Python-only deployment of the dirty source manifest |
| `just fetch-presets` | Fetch Pi-saved runtime presets for review |

`deploy-no-firmware` is retained as a compatibility alias for
`deploy-python`; use the canonical name in new automation and documentation.

Successful deployments are intentionally quiet and retain per-phase logs in
the ignored `.deploy-logs/` directory. A failure prints its phase, exit status,
the relevant log tail, and the complete log path. Set `DEBUG=1` for leaf-helper
diagnostics or use the verbose recipe to stream phase output.

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

Setup installs pinned PlatformIO 6.1.19 in a dedicated environment on the Pi
and verifies serial permissions. Firmware uses the immutable pioarduino
`55.03.39` platform input. The full deployment applies the supported SPI boot
configuration and reports whether a reboot is required. After that reboot,
confirm the expected `/dev/spidev0.0`, `0.1`, `1.0`, and `1.1` nodes and rerun
the full deployment.

## What is deployed

The normal sync set is derived from a clean Git working tree. `deploy-dirty` is
the explicit exception: it includes tracked edits and only allowlisted safe
untracked application/tooling paths, and records the base commit, selected diff
digest, and included safe-untracked paths. `deploy-plan` prints every selected
path and every Git-visible exclusion with its reason before any mutation.

A full sync removes stale managed files but preserves target-owned state:

- `run_state/`
- `presets/animations/`
- Python and PlatformIO environments/build caches
- runtime logs
- calibration and receiver-artifact libraries

Built-in plugin code, manifests, curated presets, tests needed by acceptance,
and owned assets deploy from `animation/plugins/<plugin_id>/`. The runtime
preset overlay is never the source of curated content.

## Full and Python-only flows

`just deploy` always validates a clean source manifest and runs
`deploy-precheck`. It then invokes the existing full deployment leaf in its
established order. The Pi runtime is selected through an atomic `venv` symlink
to a fresh, digest-addressed `.venvs/` environment keyed by the hash-pinned
runtime lock and the Pi Python/platform identity. A candidate environment must
import both controller and web entrypoints before it can become active; an
unchanged identity performs no installation.

Production startup invokes `venv/bin/python` directly. Do not source
`venv/bin/activate`: activation scripts embed the temporary build location,
which is intentionally replaced by the final digest path during atomic
selection. Direct interpreter invocation resolves the selected environment and
fails closed if that interpreter is absent.

`just deploy-python` is for changes that do not affect firmware, Pi packages,
permissions, or boot configuration. It verifies the existing target environment,
syncs the application subset, preserves the active animation settings, restarts
the service, restores those settings, and checks `/api/status`.

Do not use the Python-only flow after changing any of:

- `firmware/esp32/`
- dependency or environment setup
- SPI boot configuration
- systemd/startup behavior

## Coordinator and immutable-release rollout

Phase 0 includes a tested thin coordinator, atomic/redacted attempt receipts,
fresh desired-release health, desired/observed reconciliation, and immutable app
release primitives. `deploy-plan` exposes the coordinator's stable ordered step
IDs read-only. The production `deploy` and `deploy-python` recipes deliberately
continue to invoke the established shell leaves until coordinator parity is
accepted on the physical wall, as required by the rollout plan.

The release manager stages an explicit source manifest plus generated previews
under `releases/<sha256>`, validates file digests/modes and shared-state links,
then can atomically select `current`. Presets, `run_state`, logs, environments,
calibration, firmware, and the receiver library stay outside releases. The
coordinated rollback path contains only validate, capture, activate, restart,
restore, and fresh-health steps; direct rollback execution fails closed until
the target operations are configured. No release garbage collection ships in
Phase 0.

Receipts distinguish executed, cached, and skipped steps. No gate cache ships:
the policy requires at least twenty normal successful receipt timings and a
deterministic local gate that regularly costs at least five seconds before a
separate cache implementation is eligible for review.

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
- The desired-state planner fails closed for missing, duplicate, unexpected, or
  unready receivers. A partial flash preserves per-device success/failure/pending
  evidence, keeps the service stopped, and blocks candidate app activation.
- If the service health check fails, run remote diagnostics before another
  deployment.
- If electronic gates or visual acceptance fail, restore the last validated
  application/firmware pair before continuing experiments.

## Planned receiver-native deployment

The commands above are the current supported deployment surface. Phase 0 of the
[unified roadmap](plan-revamped-animation-pipeline.md) supplies the portable
delivery foundation while retaining the legacy physical execution leaf until
wall parity. Later phases add separate native
`build -> publish -> probe -> stage -> verify -> activate/compensate` steps so a
background-source change does not imply an app restart, Pi reboot, or loader
firmware flash.

The `native-animations` branch is an organ donor for firmware hashing/readiness,
managed libraries, chunk upload, cache probing, and four-receiver transaction
tests. Do not run or port its deployment recipe as the new workflow: it assumes
signing/key provisioning, signed capabilities, exclusive receiver playback, and
branch-specific artifacts. Current `just deploy` also must not begin installing
receiver-native packages until the roadmap's explicit Phase 3/4 and hardware
gates pass.
