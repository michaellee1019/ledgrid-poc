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
| `just test-firmware` | Run portable firmware tests and build production, local-canary, and managed-native-canary images |
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
| `just deploy-force-firmware` | Clean full deployment that deliberately reflashes every attached receiver |
| `just deploy-python` | Clean-tree application sync/restart without provisioning or firmware flash |
| `just deploy-python-plan` | Read-only Python-only source accounting and coordinator step plan |
| `just deploy-python-dirty` | Explicit Python-only deployment of the dirty source manifest |
| `just releases` | Inspect immutable releases, selected `current`, and target receipt state |
| `just rollback <release-id>` | Coordinated application-only rollback; never provisions, reboots, builds, or flashes |
| `just native-plan <plugin-id>` | Read-only package-scoped source and action accounting |
| `just native-build <plugin-id>` | Deterministically build, preview, validate, and retain one repository-owned native bundle |
| `just native-publish <plugin-or-bundle>` | Atomically publish one validated bundle to the Pi-authoritative library without receiver mutation |
| `just native-install <plugin-or-digest>` | Install one published binding on the exact configured receiver roster without activation |
| `just native-start <plugin-or-digest>` | Retired compatibility command; fails before target access and directs activation to Composer Check + guarded activation |
| `just native-run <plugin-id>` | Retired compatibility command; fails before build, publication, installation, or target access |
| `just receiver-native-h2-evidence <scene-digest>` | Read-only, receipt-bound H2 exact-five/skew/drift supporting slice; defaults to 1,800 seconds |
| `just receiver-native-h4-default-soak <scene-digest>` | Read-only, receipt-bound H4 authored-default supporting soak; defaults to 1,800 seconds |
| `just receiver-native-h4-maximum-soak <scene-digest>` | Read-only, receipt-bound H4 maximum-work supporting soak; defaults to 1,800 seconds |
| `just guarded-wall-soak <activation-id> <scene-digest> <release-id> <basis-digest>` | Read-only WALL-02 exact-activation, exact-Check-basis, exact-release, five-receiver soak; defaults to 1,800 seconds at target/minimum displayed 150 FPS |
| `just deploy-legacy` | Explicit clean recovery path through the retained pre-cutover full shell leaf |
| `just deploy-python-legacy` | Explicit clean recovery path through the retained pre-cutover Python shell leaf |
| `just fetch-presets` | Compatibility alias that refreshes wall masks/data and Pi-saved runtime presets for review |

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
| Receiver binary already built but five boards need flashing | 5–6 minutes | 7 minutes |
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
- all expected ESP32 USB serial devices attached, each exposing a unique factory
  USB serial and physical USB location
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
`/dev/spidev0.0`, `/dev/spidev0.1`, `/dev/spidev1.0`,
`/dev/spidev1.1`, and `/dev/spidev1.2` nodes and rerun the full deployment.

On the installed carrier, SPI1 CE2 reaches the fifth receiver through
`SJ_SPI1_CE2` on BCM GPIO24 (physical pin 18), not the overlay's default GPIO16.
The deployment therefore selects `dtoverlay=spi1-3cs,cs2_pin=24`; the jumper
must be bridged before `/dev/spidev1.2` can select that receiver.

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
unchanged identity performs no installation. A non-empty legacy `venv`
directory is preserved before the symlink cutover; an empty fresh-slate
mountpoint carries no runtime and is removed idempotently, including when an
older preserved legacy environment already occupies its content-addressed
destination.

Phase 4 first-cutover contract: immediately after `app.stage`, full deploy runs
`app.bootstrap_legacy` when the target has no selected `current` release but
still runs the recognized legacy mutable root. The
step snapshots that application as a content-addressed immutable release and
records a receipt artifact with kind `legacy_app_bootstrap`, schema `1`, and the
snapshot digest/release ID. Candidate compensation must select that bootstrap;
an unsafe, incomplete, or unprovable legacy root fails before activation. This
bootstrap is a one-time rollback anchor, not acceptance of arbitrary mutable
target content. When neither an immutable release nor a running mutable service
exists, the step records a `blank_slate` skip and leaves selection unset for the
candidate activation; it must not invent a rollback anchor from an inactive
recovery checkout.

### Receiver hardware reconciliation

Before deciding that receiver firmware is unchanged, the Pi passively asks
PlatformIO for the devices currently attached to its USB bus. The coordinator
requires a unique ESP32 factory USB serial for every board and a unique
physical USB location. A changing `/dev/ttyACM*` number is never treated as
hardware identity. Once the target-owned ledger contains the complete configured
five-serial roster, ordinary deploys select that exact roster and ignore
additional unrelated ESP32 serial devices (currently the spare
`44:b1:76:c3:cf:b8` at `LOCATION=1-1.3:1.0`). They never choose the first five
TTYs or USB locations. Without a complete ledger roster, extra devices remain
ambiguous and fail closed; missing, malformed, or duplicate identities always
fail closed.

Successful installations are recorded atomically in the target-owned
`run_state/receiver_firmware_inventory.json` ledger. Each record binds one
factory hardware serial to the complete flash-installation digest, selected
PlatformIO environment, and application-image digest. An ordinary full deploy
then behaves as follows:

- A missing ledger causes one initialization flash of all attached boards. This
  is intentional: the old aggregate marker cannot prove which physical boards
  were flashed.
- A newly installed board has no matching record, so only its currently
  discovered port is flashed.
- A firmware, flash-layout, or selected-environment change flashes every board
  whose record no longer matches.
- A fully matching roster skips the flash helper entirely.
- Missing, malformed, or duplicate device identities fail closed before any
  upload begins.

This is successful-install evidence tied to actual hardware, not a byte-for-byte
readback of ESP32 flash. Reading the factory USB descriptor avoids resetting
healthy receivers into the ROM bootloader on every deployment. Use
`just deploy-force-firmware` when the ledger should be overridden and every
attached receiver deliberately reconciled.

During that reconciliation, a receiver that re-enumerates between the exact
roster check and OpenOCD attachment may be retried only when OpenOCD proves it
never attached and verified zero flash regions. Every retry revalidates the
same five factory serials and physical USB paths within the existing bounded
stabilization window. Partial programming, timeouts, and readback failures are
never retried automatically and remain fail-closed evidence.

Phase 4 post-flash contract: full-deploy health reports a `receiver_contract`
derived from the selected firmware environment. It must prove the exact
five-device roster, logical identities,
finalized topology, and minimum status/capability set expected from that image;
production requires at least status v3 and base ownership capabilities, local
canary requires status v5 plus local/profile capabilities, and native canary
requires status v6 plus the full native capability mask. Widths, offsets, output
masks, and LEDs per strip must also match exactly. The flash ledger alone is not
proof that a receiver booted the reconciled image.
Application-only rollback deliberately omits this new-firmware contract so a
known legacy application/firmware pair remains recoverable.

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
The schema-v5 file also carries the finalized five-receiver topology: logical
widths `(8,8,8,8,1)`, global offsets `(0,8,16,24,32)`, physical order
`(0,1,2,3,4)`, host reversal map `(false,false,false,false,false)`, retained
native reversal map `(false,false,true,true,false)`, and output masks
`(255,255,255,255,255)`. The last mask
broadcasts the compact tail strip because its assembled connector lane was not
recorded. Production, local receiver execution, and managed-native execution
map to three allowlisted firmware environments; callers cannot persist an
arbitrary environment.

This order was remeasured after the ESP32 cables changed. In the mirrored
2026-08-27 Photo Booth preview, the five receiver diagnostic appeared magenta,
blue, yellow, green, red; after accounting for the preview mirror, that identifies
logical receivers `(0,1,2,3,4)` from physical left to right, with receiver 4's
single column still rightmost. The partial wall view is sufficient for receiver
permutation only, not full camera homography or host/native strip-direction
acceptance. A later direct, unflipped AVFoundation capture selected the Anker by
name. Its reset eight-step host ramp proved logical receivers 2 and 3 also need
forward host order. That result does not change the independent native flags.
Clean application deployment of commit `7cc351a` activated release
`e6f5c43245a3b9198d96a1afa8388005751d0072a0f4a341323aeb16183527e8` without
flashing receiver firmware. The known schema-v4 file was then atomically
migrated to schema v5 with digest
`8fbbd06483a02351324068a10fa451e6035171e5a65d2c177c9b11e49c774bca`.
A direct post-fix ramp accepted all four broad receivers with correlations
`+0.72`, `+0.72`, `+0.84`, and `+0.95` after fresh camera registration.

Inspect the live selection before a receiver-native deployment:

```bash
ssh ledgridwall@ledgridwall.local -- \
  python3 /home/ledgridwall/ledgrid-pod/current/tools/deployment/receiver_hybrid_config.py \
  --root /home/ledgridwall/ledgrid-pod show
```

Enable the local receiver execution path (managed native modules still off):

```bash
ssh ledgridwall@ledgridwall.local -- \
  python3 /home/ledgridwall/ledgrid-pod/current/tools/deployment/receiver_hybrid_config.py \
  --root /home/ledgridwall/ledgrid-pod \
  enable-local
```

After H0/H1 acceptance, `enable-native` selects the managed-native firmware and
runtime gate. `disable` restores feature-off production while retaining the
finalized topology. A normal full deployment runs the idempotent `migrate`
operation only after the first candidate passes health. Before that point the
candidate recognizes the exact retired four-receiver schema as the same
feature-off finalized selection, allowing build, flash, restart, and rollback
without making the legacy service unbootable. Unknown legacy state fails closed.

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
two advancing post-boundary status samples, exact 33 x 138 geometry, ready
state, and exactly five distinct logical receiver IDs `0..4`.

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
   just receiver-streamed-wall-acceptance "$SCENE_DIGEST" duration=60 min_fps=150 target_fps=150
   just output-rate-observation "$SCENE_DIGEST" seconds=15 rate=150
   ```

   `SCENE_DIGEST` is the exact canonical digest from the guarded Composer
   activation receipt. These recipes accept the shown trailing `key=value` form
   as well as positional values. They are observation-only: the streamed-wall
   recipe names all five logical receivers and verifies the expected active
   Python scene and cadence without changing or restoring scene, modifiers, or
   cadence. The old multi-animation live sweep is retired because it cannot
   switch scenes safely without a fresh guarded activation for each scene.
   The finalized hardware has five readable return paths, so the strict recipes
   are the release gates. The old `*-degraded-spi1` recipes are recovery-only
   historical diagnostics and cannot close acceptance.

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
- If firmware flash fails, neither the aggregate marker nor the per-device
  ledger is advanced, so the next full deployment retries the affected roster.
- The desired-state planner fails closed for missing, duplicate, unexpected, or
  unready receivers. A partial flash preserves per-device success/failure/pending
  evidence, keeps the service stopped, and blocks candidate app activation.
- If the service health check fails, run remote diagnostics before another
  deployment.
- If electronic gates or visual acceptance fail, restore the last validated
  application/firmware pair before continuing experiments.

## Receiver-native deployment

The commands above are the supported deployment surface. Phase 0 of the
[unified roadmap](plan-revamped-animation-pipeline.md) supplies the coordinated,
immutable delivery foundation; the legacy physical leaf is recovery-only.
Phase 4 adds separate `native-plan`, `native-build`, `native-publish`, and
`native-install` workflows around the fail-closed package lifecycle,
so a background-source change does not imply an app restart, Pi reboot, or
loader firmware flash. These actions require the explicit native-canary firmware
selection and exact five-receiver readiness; the production firmware selection
remains feature-off.

The `native-animations` branch is an organ donor for firmware hashing/readiness,
managed libraries, chunk upload, cache probing, and historical four-receiver
transaction tests. Do not run or port its deployment recipe as the new workflow: it assumes
signing/key provisioning, signed capabilities, exclusive receiver playback, and
branch-specific artifacts. Ordinary `just deploy` deliberately reconciles the
application, baseline firmware, finalized topology, and target-owned libraries
without installing or activating a receiver-native package. Native activation
remains an explicit, default-off operator action until H0–H4 are accepted.

The supported explicit workflow is:

```bash
just native-plan aurora_curtains_native
just native-build aurora_curtains_native
just native-publish aurora_curtains_native
just native-install aurora_curtains_native
# Then select the managed component in Composer, run Check, and use guarded activation.
```

`native-start` and `native-run` are retained only as safe-failing compatibility
commands. `native-start` performs no target request. `native-run` fails before
building, publishing, installing, or contacting the target.

`native-publish` executes the Pi-side library transaction through the selected
immutable `current` application release. On a legacy first-cutover target with
no `current` symlink, `native-build` remains available but publication must wait
for the ordinary full deployment to create the rollback anchor, provision the
pinned runtime, and activate the first immutable release. Shadow staging alone
does not cross that boundary and must not be treated as activation.

Build writes an append-only coordinator receipt under the local native-receipt
directory. Publish writes the corresponding local receipt and a target receipt;
the managed-library publication receipt separately binds bundle digest, payload
digest, size, and publication metadata. Install output is command-bound package
evidence. Activation evidence comes only from the guarded Composer receipt and
must bind the complete canonical scene, bundle/payload, exact roster,
capabilities, topology, parameters, and current context/profile agreement.

A quarantined payload is never retried automatically. After diagnosing the
failure, clear only the exact managed binding through:

```text
POST /api/v1/native-backgrounds/<64-lowercase-hex-bundle>/clear-quarantine
```

The controller clears quarantine only after exact-five agreement and conflict
checks; reinstall remains a separate explicit action. To abandon native playback,
`POST /api/v1/receiver-native/recover` presents the recorded Python fallback as
a complete frame and clears managed-native ownership only after host takeover is
positively verified. Rejection or exception leaves degraded/native ownership and
the error visible so the operator can retry recovery safely.

The API-only physical runner covers only the subgates named in its report:

```bash
just receiver-native-h2-evidence "$SCENE_DIGEST"
just receiver-native-h4-default-soak "$SCENE_DIGEST"
just receiver-native-h4-maximum-soak "$SCENE_DIGEST"
```

All three recipes require the exact canonical scene digest from the guarded
activation receipt, default to a real 1,800-second observation, compare that
identity at every sample, and never install, activate, stop, or restore. Shorter
durations are useful diagnostics but cannot satisfy a
complete-gate claim. `--require-complete-gate` additionally requires at least
1,800 observed seconds and same-release/same-artifact companion evidence for the
explicitly outstanding transaction injection, restart/lease, dense-streamed,
animation-sweep, retained-artifact, timing-distribution, or other soak subgates.
The runner never turns supporting evidence into H2/H4 acceptance by itself.
