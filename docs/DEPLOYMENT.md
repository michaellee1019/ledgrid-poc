# Deployment

Deployment targets `ledgridwall@ledgridwall.local` and `~/ledgrid-pod` by
default. Override `PI_HOST` or `DEPLOY_DIR` for another installation.

For unattended or agent-driven work, `SSH_KEY` selects one explicit private
key and adds OpenSSH `IdentitiesOnly=yes`; this prevents an SSH agent failure
from falling through to unrelated identities. Relative key paths resolve from
the repository root. When `SSH_KEY` is unset, deployment retains ordinary
OpenSSH configuration and agent behavior unchanged.

## Command surface

Use `just` recipes rather than invoking deployment helpers directly:

| Recipe | Purpose |
| --- | --- |
| `just generate-ai-ssh-key` | Create the ignored `.gpt-key` identity and print its one-time authorization command |
| `just setup-web` | Create the local web/preview environment |
| `just setup` | Prepare SSH, Pi permissions, SPI, and firmware tooling |
| `just test-unit` | Run Python unit and plugin tests |
| `just test-rendering` | Run frame-contract and render-performance checks |
| `just test-firmware` | Run native firmware tests and build production firmware |
| `just test-deployment` | Test deployment state and file selection logic |
| `just test` | Run every required local gate |
| `just preflight` | Alias for the full test gate |
| `just deploy-precheck` | Full test gate used by deployment |
| `just deploy-plan` | Read-only source accounting and authoritative coordinator step plan |
| `just deploy-shadow` | Freeze and verify an immutable snapshot without contacting the Pi |
| `just deploy-shadow-stage` | Stage immutable app/support releases on the Pi without activation or host/receiver mutation |
| `just deploy` | Clean-tree coordinated release, provision, changed-firmware reconciliation, activation, and fresh health |
| `just deploy-dirty` | Explicit coordinated deployment of tracked edits plus allowlisted safe untracked source |
| `just deploy-verbose` | Clean full deployment with normally captured phase output streamed live |
| `just deploy-python` | Clean-tree application sync/restart without provisioning or firmware flash |
| `just deploy-python-plan` | Read-only Python-only source accounting and coordinator step plan |
| `just deploy-python-dirty` | Explicit Python-only deployment of the dirty source manifest |
| `just releases` | Inspect immutable releases, selected `current`, and target receipt state |
| `just rollback <release-id>` | Coordinated application-only rollback; never provisions, reboots, builds, or flashes |
| `just deploy-legacy` | Explicit clean recovery path through the retained pre-cutover full shell leaf |
| `just deploy-python-legacy` | Explicit clean recovery path through the retained pre-cutover Python shell leaf |
| `just fetch-presets` | Fetch Pi-saved runtime presets for review |

`deploy-no-firmware` is retained as a compatibility alias for
`deploy-python`; use the canonical name in new automation and documentation.

Deployments print one start/completion line per coordinator step, including a
normal timing expectation and the measured duration. Detailed command output
remains captured under ignored `.deploy-logs/`; append-only attempt receipts
are required both locally in `.deploy-logs/receipts/` and on the target in
`run_state/deploy_receipts/`. A missing receipt copy makes the command fail
even when the wall operation itself succeeded. A failure prints its phase,
exit status, relevant log tail, and complete log path. Set `DEBUG=1` for
leaf-helper diagnostics or use the verbose recipe to stream the final receipt
as well.

### Timing expectations

These are operating ranges, not timeouts. The command identifies the active
phase so an operator can distinguish expected compilation from a stalled SSH
or health operation.

| Full-deploy situation | Expected elapsed time | Investigate after |
| --- | ---: | ---: |
| App changed; receiver firmware already installed | 2–3 minutes | 4 minutes |
| Receiver binary already built but four boards need flashing | 4–5 minutes | 6 minutes |
| Firmware changed with a populated compiler cache | 5–8 minutes | 10 minutes |
| First firmware build after cache reset | 16–18 minutes | 19 minutes |

`just deploy-python` is the normal 2–3 minute path for app-only changes. The
full flow deliberately spends about 1–2 minutes in the local regression gate.
`TEST=false` is an explicit exception for an operator who has intentionally
accepted skipping that gate, not the normal way to meet the timing range.

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

To create a dedicated repository-local automation identity instead, run:

```bash
PI_HOST=ledgridwall@192.168.1.62 just generate-ai-ssh-key
ssh-copy-id -i .gpt-key.pub ledgridwall@192.168.1.62
SSH_KEY=./.gpt-key PI_HOST=ledgridwall@192.168.1.62 just deploy
```

The private key and public-key sidecar are ignored by Git. The generator uses
Ed25519, creates the private key with mode `0600`, and refuses to overwrite an
existing key. The authorization command intentionally uses normal SSH handling
once; subsequent commands that set `SSH_KEY` do not consult the agent for other
identities.

Setup installs pinned PlatformIO 6.1.19 in a dedicated environment on the Pi
and installs `ccache` before verifying serial permissions. Firmware uses the
immutable pioarduino `55.03.39` platform input. Content-addressed firmware
workspaces share a persistent PlatformIO cache and ESP-IDF compiler cache below
`build/firmware/`, so framework and unchanged source objects survive a new
firmware digest without weakening the immutable source/workspace boundary. The
full deployment applies the supported SPI boot configuration and reports
whether a reboot is required. After that reboot, confirm the expected
`/dev/spidev0.0`, `0.1`, `1.0`, and `1.1` nodes and rerun the full deployment.

## What is deployed

The normal sync set is derived from a clean Git working tree. `deploy-dirty` is
the explicit exception: it includes tracked edits and only allowlisted safe
untracked application/tooling paths, and records the base commit, selected diff
digest, and included safe-untracked paths. `deploy-plan` prints every selected
path and every Git-visible exclusion with its reason before any mutation.

The coordinator never syncs over the live root. It uploads one verified source
snapshot to `.incoming/<attempt-id>`, stages application files in an immutable
`releases/<sha256>` directory, stages firmware/support inputs separately in
`support_releases/<sha256>`, and removes the incoming snapshot. Target-owned
state remains outside immutable releases:

- `run_state/`
- `presets/animations/`
- Python and PlatformIO environments/build caches
- runtime logs
- calibration and receiver-artifact libraries

The retained legacy full-sync leaf has matching excludes for `current`,
immutable release trees, incoming/receipt evidence, calibration, receiver
artifacts, runtime state, presets, environments, and logs. Use it only as an
explicit recovery path.

Built-in plugin code, manifests, curated presets, tests needed by acceptance,
and owned assets deploy from `animation/plugins/<plugin_id>/`. The runtime
preset overlay is never the source of curated content.

## Full and Python-only flows

`just deploy` always validates a clean source manifest and runs
`deploy-precheck`. It then runs the coordinator's stable full sequence: source,
tests, target connection, immutable app stage, receiver build, settings capture,
host provision, receiver flash reconciliation, candidate validation, activation,
restart, settings restoration, fresh health, and rollback-safe release pruning.
State capture deliberately precedes any receiver flash because flashing can
reset a receiver-native scene and must not cause its Python fallback to overwrite
the saved desired display. The Pi runtime is
selected through an atomic `venv` symlink
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
permissions, or boot configuration. It stages an immutable application release,
verifies the existing target environment, preserves the active animation
settings, atomically selects `current`, restarts only when selection changed,
restores those settings, and requires release-aware fresh health. An identical
repeat reuses the release and skips activation/restart while still proving fresh
health and recording the post-health deployment timestamp.

Do not use the Python-only flow after changing any of:

- `firmware/esp32/`
- dependency or environment setup
- SPI boot configuration
- systemd/startup behavior

## Durable receiver-hybrid rollout

`run_state/receiver_hybrid.json` is the single target-owned authority used by
startup, firmware selection, state restore, and deployment receipts. Absence or
an explicit disabled selection chooses the feature-off production firmware.
Only the allowlisted degraded policy chooses
`esp32-s3-devkitc-1-local-canary`; callers cannot persist an arbitrary firmware
environment beside it.

Inspect the live selection before a receiver-native deployment:

```bash
ssh ledgridwall@ledgridwall.local -- \
  python3 /home/ledgridwall/ledgrid-pod/current/tools/deployment/receiver_hybrid_config.py \
  --root /home/ledgridwall/ledgrid-pod show
```

The currently camera-verified installed mapping is written atomically with:

```bash
ssh ledgridwall@ledgridwall.local -- \
  python3 /home/ledgridwall/ledgrid-pod/current/tools/deployment/receiver_hybrid_config.py \
  --root /home/ledgridwall/ledgrid-pod \
  --physical-lane-order 0,1,3,2 \
  --reversed-logical-receivers 2,3 \
  --reversed-native-logical-receivers 2,3 \
  enable-degraded
```

Do not copy a host direction result into the native option without a separate
camera diagnostic. `--reversed-logical-receivers` controls complete RGB and
sparse RGBA slicing; `--reversed-native-logical-receivers` controls the
firmware renderer's local-to-global coordinate transform. A four-color
receiver-lane test determines only `--physical-lane-order`; use 32 distinct
strip colors to determine host local direction and a signed receiver-native
phase field to determine native direction.

Changing the selection digest is a fail-closed scene-authority boundary. Restart
the service, inspect whether the known Python fallback was selected, explicitly
re-authorize the desired native scene if needed, and retain the new scene/config
evidence before deployment. Then run ordinary `just deploy`, not merely
`deploy-python`, so the selected firmware environment and exact binary digest
are reconciled. Acceptance requires the same config digest, desired scene, and
camera-visible result after deploy. A write-only receiver's outbound counters
do not substitute for visual evidence or MISO acknowledgement.

## Coordinator and immutable-release rollout

Phase 0's thin coordinator is the authoritative `deploy` and `deploy-python`
path. `deploy-plan` exposes its stable ordered step IDs read-only. The old shell
leaves remain only under explicit `*-legacy` recovery recipes and do not share
the authoritative command names.

The release manager stages an explicit source manifest plus previews rendered
from the frozen source under `releases/<sha256>`, validates every digest, mode,
and shared-state link, then atomically selects `current`. Presets, `run_state`,
logs, environments, calibration, firmware, and the receiver library stay
outside releases. The coordinated rollback path contains only source/release
validation, capture, activate, restart, restore, fresh health, and app-release
retention; its step policy cannot acquire provision, build, reboot, or flash
operations. No release is pruned until after fresh health acceptance. Five
valid app releases are kept by default, including the active release, and
malformed or unrecognized release directories are preserved for diagnosis.
Override the total ceiling with `DEPLOY_RETAIN_RELEASES`; values below two are
rejected so one rollback remains.

Every release contains `.release.json`. Startup accepts that identity only when
the lowercase SHA-256 digest matches both its content-addressed directory and
the target's selected `current` symlink. `/api/status` publishes web and
controller release identities plus `release_consistent`. Acceptance requires
active systemd, agreement between systemd/current/web/controller identities,
two advancing post-boundary status samples, exact 32 x 138 geometry, ready
state, and exactly four distinct logical receiver IDs `0..3`.

The cutover sequence is deliberately graduated:

```bash
just deploy-plan
just deploy-shadow
just deploy-shadow-stage
just releases
just deploy-dirty       # development canary only
just deploy             # final clean-tree gate
```

Shadow staging may create/reuse immutable app/support releases, but cannot
change `current`, systemd, firmware, settings, or deployment timestamp. If any
post-activation boundary fails or is interrupted, the coordinator selects the
prior immutable app release, restarts it, restores settings, proves its fresh
health, and records the failure evidence in both receipts.

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
   just receiver-phase3a-status
   just receiver-streamed-wall-acceptance duration=60 min_fps=150 target_fps=160
   just live-animation-sweep seconds=2
   just output-rate-sweep seconds=15 rates=120,140,160,180,200
   ```

   These recipes accept the shown trailing `key=value` form as well as their
   positional arguments and defaults. The streamed-wall recipe always names all
   four logical receivers, neutralizes global plant modifiers for a deterministic
   transport load, and restores the prior scene, modifier state, and cadence.

   While the installed SPI1 MISO net remains shorted, keep those strict commands
   as open release gates and use only the separately named temporary diagnostics:

   ```bash
   just receiver-phase3a-status-degraded-spi1
   just receiver-streamed-wall-acceptance-degraded-spi1 \
     duration=60 min_fps=150 target_fps=160
   just live-animation-sweep-degraded-spi1 seconds=2
   ```

   These require complete telemetry on logical receivers 0 and 1, the exact
   known no-return state plus advancing outbound host traffic on 2 and 3, and
   visual inspection of every SPI1 lane. They report incomplete telemetry and
   cannot close MISO-dependent Phase 3A or later receiver-native gates.

   The Phase 3A status gate is intentionally separate from generic deployment
   health so a rollback to v2 firmware remains possible. It asks the controller
   process to drain fresh serialized status responses from every receiver and
   rejects cached/mismatched refresh evidence, missing status-v3 or ownership
   capabilities, unavailable boards, and wrong receiver-reported identities.
   During the scheduled one-receiver local canary, use
   `just receiver-phase3a-canary-status <logical-device>` to additionally
   require the static-background and presentation-context capability bits on
   exactly that receiver.

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
clear a stale port binding and restart `ledgrid.service`; it never launches a
standalone process from the mutable root. Output is written to the
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
[unified roadmap](plan-revamped-animation-pipeline.md) supplies the coordinated,
immutable delivery foundation; the legacy physical leaf is recovery-only.
Later phases add separate native
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
