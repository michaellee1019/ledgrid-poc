# Uploadable Firmware Animations: Implementation Record

## Summary

The repository now has a second animation backend alongside Python plugins:

- **Native animations:** trusted C/C++ modules compiled as ESP32-S3 shared objects and loaded from PSRAM using Espressif's supported [`elf_loader`](https://components.espressif.com/components/espressif/elf_loader/versions/1.3.2/readme).
- **Frame animations:** GIF or animated WebP converted into compact, device-specific frame tracks.
- The Raspberry Pi owns the animation library; all four ESP32s maintain recoverable caches.
- One baseline firmware flash installs the loader. Subsequent animations are uploaded without reflashing.
- Strict cross-controller synchronization and shared v-sync remain out of scope; starts are sequential and each receiver uses its local clock.

## Cold-resume handoff (2026-08-08)

### Bottom line

The receiver-animation software and production provisioning flow are
implemented, but the installed wall is **not release-ready**. Physical
acceptance is blocked by a confirmed electrical coupling/short between SPI1
MISO and MOSI. Ordinary frame streaming can still look healthy because it is a
Pi-to-ESP32 MOSI workload; signed package identity, capability, upload,
acknowledgement, and control require the ESP32-to-Pi MISO return path and
correctly fail closed.

At this handoff the feature is committed on the `native-animations` branch;
inspect `git status` and recent history before editing or resuming. The clean
cutover is also intentional: **LGS3 is the only receiver status contract and
there is no LGS1/LGS2 or mixed-version compatibility path.** Do not weaken
`check_receiver_readiness.py`, forge capabilities, accept a position/identity
mismatch, enable unsigned development, or add a software fallback around the
wiring fault. Repair and verify the return path before `just deploy`.

### Latest software evidence

The latest recorded local run on 2026-08-08 completed
`just deploy-precheck` with exit 0. That recipe is the same full software gate
used by `just deploy`; it ran on the arm64 macOS 26.5.2 development machine and
produced:

| Recipe/check | Recorded result | What it does **not** prove |
|---|---|---|
| `just test-unit` | 521 pytest tests plus 791 nested/subtests passed in 20.11 s. | Installed wiring or receiver timing. |
| `just test-rendering` | 18 focused tests passed; the stress benchmark exited 0 with every reported p95 below the 4 ms desktop gate. | Raspberry Pi or ESP32 performance. |
| `just test-native-animations` | All three catalog examples passed default and stress profiles with 320 samples/profile, target cross-compilation, required export, and zero undefined imports. Full-run worst host-preview p95 was 0.003541 ms, worst p99 was 0.009 ms, and the largest observed callback was 0.019375 ms. | These are trusted arm64 Mac host-library measurements at 8×138, **not ESP32 physical timing**. |
| `just test-firmware` | Portable receiver suite passed 25/25; the production ESP32-S3 build succeeded with 505,509 flash bytes and 61,800 RAM bytes used. The most recent uncached build emitted a 505,765-byte image. | Flashing, on-device execution timing, reset behavior, or wall stability. |
| `just test-deployment` | 24 tests plus 4 nested/subtests passed; every maintained deployment shell script passed syntax validation. | A successful live deployment. |
| `git diff --check` | Passed. | This checks patch whitespace, not branch/worktree state; inspect `git status` separately. |

Rerun `just deploy-precheck` after any resumed edits. Keep the dated numbers
above as historical evidence rather than silently treating them as results for
a changed tree.

### Decisive installed-wall evidence

The installed SPI1 mapping is fixed and was confirmed during diagnosis:

| Logical receiver | Linux device | Chip select | Pi GPIO | Physical pin |
|---:|---|---|---:|---:|
| 2 | `/dev/spidev1.1` | CE1 | 17 | 11 |
| 3 | `/dev/spidev1.0` | CE0 | 18 | 12 |

Both receivers share Pi GPIO 19 / physical pin 35 for MISO, which must connect
to ESP32 GPIO 13. Pi GPIO 20 / physical pin 38 is the shared SPI1 MOSI line and
connects to ESP32 GPIO 11.

All four receivers were flashed with their distinct provisioned production
identities. SPI0 readback proves installed positions 0 and 1 report identities
0 and 1 with the required capabilities; the broken shared return path prevents
positions 2 and 3 from proving their flashed identities over SPI1.

With the LED-grid service stopped, direct sweeps on **both** SPI1 devices
returned the exact transmitted bytes instead of LGS3 at 20, 10, 5, and 1 MHz,
for transfer lengths from 3 through 4096 bytes, including random patterns,
`no_cs`, and chip-select-held-high cases. After GPIO 19 was temporarily unmuxed
from SPI1, it followed GPIO 20 through a low/high/low drive sequence with 20/20
matching samples. The SPI0 controllers returned valid LGS3 at every tested
speed, validating the test method and the running firmware. Together these
results are decisive: Pi GPIO 19 / pin 35 is electrically coupled or shorted to
Pi GPIO 20 / pin 38 somewhere in the installed SPI1 wiring, connector, or board
path. This is not a speed, chip-select, parser, or firmware-version problem.

The service was restored after the stopped-service sweeps. Receivers 0 and 1
remain controllable over SPI0; receivers 2 and 3 can receive ordinary streamed
frames over SPI1 MOSI but cannot return trustworthy status or acknowledgements.
The host therefore refuses package I/O before upload, as designed.

### First commands when resuming

Start from the repository root. These commands are read-only with respect to
the wall and establish the exact source/test baseline:

```bash
cd /Users/rtimmons/Projects/ledgrid-poc
git status --short
git log --oneline --decorate -12
git diff --check
just deploy-precheck
```

Production authoring state already exists on this workstation under ignored
`run_state/firmware_authoring/`: `deploy.env`, `public.pem`,
`signing_private.pem`, and signed Startup Rainbow, Aurora Ribbons, and Meteor
Shower packages. Do not print or track the key contents, and do not reprovision
merely to resume. Verify only the expected files if needed:

```bash
test -s run_state/firmware_authoring/deploy.env
test -s run_state/firmware_authoring/public.pem
test -s run_state/firmware_authoring/signing_private.pem
find run_state/firmware_authoring/packages -maxdepth 1 -name '*.lga' -print | sort
```

Do **not** use `just deploy` as a wiring probe. After the powered-off repair
below, restart the already-installed service and require a fresh four-receiver
readiness report first:

```bash
wall_restart_epoch="$(ssh ledgridwall@ledgridwall.local 'date +%s.%N')"
ssh ledgridwall@ledgridwall.local 'sudo systemctl restart ledgrid.service'
python3 tools/deployment/check_receiver_readiness.py \
  --url http://ledgridwall.local:5000/api/status \
  --wait-seconds 30 --interval-seconds 0.5 \
  --min-updated-at "$wall_restart_epoch"
```

That check must report four receivers with LGS3, logical identities 0–3 in
their corresponding positions, and all signed-package/upload/native/frame-track/
typed-parameter capabilities. Only then run the streamed electronic canary and
the deployment:

```bash
for device in 0 1 2 3; do
  just receiver-acceptance "$device" 60
done
just deploy
```

`just deploy` reruns the complete software gate, validates the existing ignored
provisioning, rebuilds signed examples, copies public material only, builds and
flashes four isolated logical-device images if their hash changed, installs the
examples into the Pi library, restarts both processes, and repeats fresh LGS3
identity/capability readiness. A failure is a stop condition, not permission to
set `TEST=false`, `SKIP_FIRMWARE`, or bypass readiness.

For focused work before the full gate, the exact recipes are:

```bash
just test-unit
just test-rendering
just test-native-animations
just test-firmware
just test-deployment
```

### Powered-off isolation and repair

1. Stop the service, shut the Pi down cleanly, then physically remove **all**
   power sources: Pi supply, every receiver USB/serial supply, LED power, and
   any powered intermediary. Wait for rails to discharge and verify the
   installation is unpowered before using resistance/continuity mode.

   ```bash
   ssh ledgridwall@ledgridwall.local \
     'sudo systemctl stop ledgrid.service && sudo shutdown -h now'
   ```
2. Label logical receiver 2 (`spidev1.1`, CE1 GPIO 17/pin 11) and logical
   receiver 3 (`spidev1.0`, CE0 GPIO 18/pin 12). Photograph the connectors
   before moving them.
3. Disconnect the SPI1 harness from Pi pins 35 (GPIO 19, MISO) and 38 (GPIO 20,
   MOSI). Measure the isolated Pi/header path and the isolated harness path
   separately. GPIO 19/MISO and GPIO 20/MOSI must not show a low-resistance
   connection.
4. If the harness remains shorted, disconnect the two ESP32 branches one at a
   time, including ESP GPIO 13/MISO and GPIO 11/MOSI, to localize the bad branch,
   connector, solder bridge, or swapped jumper. Inspect shared junctions and any
   intermediate board. Do not trust wire color alone.
5. Replace or repair the faulty segment. Still powered off, verify end-to-end
   continuity from Pi GPIO 19/pin 35 to **both** ESP GPIO 13 pins and from Pi
   GPIO 20/pin 38 to both ESP GPIO 11 pins. Verify the two nets are isolated
   from each other and not shorted to 3.3 V, 5 V, or ground. Recheck CE1 and CE0
   against the installed mapping above.
6. Reconnect, initially leaving LED power off if practical. Power the Pi and
   receivers, allow boot to finish, and run the fresh readiness command from
   the previous section before applying LED power or deploying anything.

Never continuity-test a powered circuit. Do not repeat the GPIO drive test
while an ESP32 or another output is connected to the net; two outputs fighting
through a short can damage the boards.

### Post-repair physical acceptance still required

None of these gates is closed by the desktop results above:

1. Fresh LGS3 readback must show all four exact logical identities and required
   signed playback capabilities; the four 60-second streamed-frame canaries
   must pass with no MISO/status, CRC, queue, display, or accounting failures.
2. Complete `just deploy`, open
   `http://ledgridwall.local:5000/firmware-animations`, and use **Install on
   receivers** then **Start on wall** for a production-signed example. Confirm
   each receiver reports the expected device-payload digest for the one active
   package. Package previews are authoring-time previews, not live framebuffer
   readback.
3. Exercise live parameters, stop/restart, and switch back to an ordinary
   Python animation. The first complete host frame must reclaim display
   ownership without flashing receiver firmware or rebooting.
4. Measure on the actual ESP32s at each package's declared cadence: native
   render p95 below 4 ms plus recorded p99/max, and frame-track decode p95 below
   2 ms plus recorded p99/max. The desktop callback numbers in this document do
   not count. LGS3 exposes `receiver_last_render_or_decode_us` and
   `receiver_max_render_or_decode_us` through `/api/metrics`; retain a time
   series to calculate percentiles rather than treating one snapshot or the
   running maximum as p95/p99. No current `just` recipe computes these
   receiver-local percentiles.
5. Verify interrupted upload and power loss around staging/metadata rename,
   inactive-cache eviction, receiver reset and power-loss recovery, native
   callback failure/watchdog fallback to the compiled rainbow, persisted
   quarantine, and explicit reinstall before retry.
6. While receiver-local playback is active, test controller-process restart and
   Pi disconnect/restart adoption. Record sequential-start skew and clock drift;
   strict v-sync remains intentionally outside v1.
7. Run one 30-minute production-signed native soak and one 30-minute signed
   frame-track soak. Require zero resets, watchdog events, missed deadlines,
   unexplained digest/mode changes, or visual corruption, then repeat the
   streamed-frame switchback. The three provisioned examples are native; build
   and sign a representative frame-track package with the
   [package SDK](../firmware_animations/README.md#cli) before the frame-track
   timing and soak gates.

Retain the results as dated physical evidence. Until every item passes, describe
the backend as software-complete and physically blocked/unverified—not
production-ready.

## Implementation status (2026-08-08)

**Core software and production provisioning status: implemented. Installed-wall
release status: pending physical receiver acceptance.** The implementation spans the
package SDK, signed receiver envelope, Pi library and orchestration, SPI
protocol, dashboard, ESP-IDF receiver runtime, persistent cache, dynamic loader,
quarantine path, and checked-in native examples.

| Area | Status | Acceptance evidence |
|---|---|---|
| Deterministic `.lga` SDK, CLI, signing, verification, install/recovery | Complete | `just test-unit` covers archive, signature, replacement, receiver-envelope, and CLI regressions. |
| LGS3/SPI transport and four-receiver orchestration | Complete | Capability gating, exact packet bounds, cache probe/upload/abort, compensating rollback, fail-closed runtime reconciliation, mode restoration, and residual-publication telemetry are automated. |
| ESP32-S3 cache, trust verification, frame/native playback, watchdog and persistence | Complete in software | `just test-firmware` runs the portable receiver suite and production ESP32-S3 cross-build. |
| Startup recovery animation and native examples | Complete | The fallback calls the same compiled native callback table as `startup-rainbow-native`; `aurora-ribbons-native` and `meteor-shower-native` add distinct bounded examples. |
| Host/API/dashboard and explicit playback modes | Complete | Manager, IPC, REST, persistent state, gallery, controls, unsupported health, and deletion rules pass the Python regression suite. |
| Native example performance | Desktop proxy complete | `just test-native-animations` cross-builds all catalog modules, enforces the standard export and zero undefined imports, builds/verifies signed packages, and measures default/stress profiles at 8×138 per callback. |
| Production provisioning | Complete | `just provision-native-animations` creates ignored workstation-only signing state and binds four stable `/dev/serial/by-id` ports. `just deploy` copies only the public key, builds and flashes isolated logical-device 0–3 images on pinned platform 55.03.39, installs the signed example gallery, configures both Pi processes through one environment file, and rejects missing capability or identity readback. |
| Physical release acceptance | Blocked on a confirmed SPI1 MISO-to-MOSI electrical coupling/short, then pending the full gate | SPI0 returns LGS3 at all tested speeds. With the service stopped, both SPI1 devices returned exact TX echo across speeds, lengths, CS-disabled/high, and random-pattern tests; unmuxed GPIO 19 followed GPIO 20. See the [cold-resume evidence and acceptance sequence](#decisive-installed-wall-evidence). |

`just test` is the canonical local merge gate. A local pass is software evidence,
not permission to flash the wall and not a substitute for the open provisioning
and physical gates.

### Intentional deviations and clarifications

- There is no possible distributed atomic filesystem rename across four
  independent receivers. The implemented transaction stages and verifies every
  target first, commits sequentially, never starts partial playback, and uses
  compensating removal plus re-probe/absence telemetry if a commit or its
  acknowledgement fails. This is the deliberate practical interpretation of
  “activates nothing” for v1.
- The compiled recovery rainbow is now also a native animation, but recovery
  invokes its callback table directly from the baseline image. It deliberately
  does not depend on SPIFFS, a signed package, the trust key, or `elf_loader`, so
  the original boot/crash-fallback guarantee is preserved without duplicate
  rendering logic.
- ABI v1 target modules are deliberately import-free. Bounded deterministic
  randomness, HSV/RGB565 conversion, and sine/cosine are passed through the host
  helper table; the verifier's undefined-import allowlist is empty. This is a
  tighter version of the planned allowlist boundary and prevents a package from
  verifying with a symbol the receiver cannot resolve.
- `LGT1` v1 payloads deliberately encode only infinite-loop track metadata
  (`loop_count == 0`). A finite/non-looping source sets the package's public
  `loop` default false, so the receiver holds after one pass; `loop=true` repeats
  indefinitely. Arbitrary fixed repeat counts are not a v1 runtime feature, and
  the SDK and receiver both reject a nonzero serialized `loop_count` instead of
  silently ignoring it.
- LGS3 is the only receiver-status contract for this backend. LGS1/LGS2
  parsing, encoding, prefix preservation, and mixed-version deployment are
  deliberately unsupported; all four receivers must move to the new baseline
  together. This avoids carrying compatibility branches into a new backend.
- Example source and canonical metadata are checked in, while signed `.lga`
  outputs are generated with ephemeral or operator-held keys. Production
  private keys and a production public trust anchor are intentionally absent
  from the repository. An empty configured trust key fails closed and advertises
  no signed upload/native capability.
- Production provisioning is intentionally explicit rather than USB-discovery
  based. Stable serial IDs are mapped once in logical wall order; every LGS3
  capability report carries an identity-valid bit plus logical index, and the
  host refuses package I/O if the SPI position and provisioned image disagree.
  Only the public key/config and signed packages are copied to the Pi. The
  authoring private key remains in ignored workstation state.
- Strict shared-clock start scheduling and v-sync remain intentionally deferred
  exactly as scoped. Sequential start ordering is implemented; hardware skew
  and drift remain measurements, not v1 gates.
- Desktop compilation and timing are regression proxies only. They do not close
  the physical performance/soak gate or make the feature production-ready.

## Firmware and Package Architecture

- The receiver entrypoint uses native ESP-IDF while preserving the SPI, mailbox,
  parallel LED driver, and compiled startup rainbow behavior.
- `elf_loader` 1.3.2 is pinned; ESP32-S3 PSRAM execution and dynamic shared
  objects are enabled; and native modules use a versioned `extern "C"` ABI.
- A native module exports one v1 entrypoint returning callbacks for
  initialization, full local-frame rendering, and cleanup. Each render receives:
  - local 8×138 output buffer;
  - global strip offset;
  - unscaled and globally scaled elapsed time plus frame index;
  - typed parameter values;
  - bounded host helpers for color conversion, math, and deterministic randomness.
- C++ modules compile without exceptions or RTTI. ABI v1 modules must have zero
  undefined imports and use the helper table for bounded runtime services.
  Signing is the security boundary because native code is not sandboxed.
- The frame-loop player uses per-device RGB565 keyframes plus forward delta/run
  encoding, original frame durations, `pause`, looping, playback speed, and
  asset brightness.
- The compiled startup rainbow remains the boot, missing-asset, and crash fallback.

### `.lga` Package

Each deterministic ZIP package contains:

- canonical manifest: ID, name, version, description, kind, ABI, target geometry, preferred FPS, parameter schema, payload hashes, and provenance;
- signed binary index containing all four device payload hashes;
- one native `.so` or four frame tracks;
- generated animated WebP preview;
- ECDSA P-256 signature.

Production firmware embeds one trusted public key. Private keys remain on authoring machines. An explicit development build flag may permit unsigned packages and must be exposed in receiver status.

Limits:

- 16 MiB `.lga` file;
- 512 KiB native payload;
- 2.5 MiB (2,621,440 bytes) frame payload per receiver;
- at least 512 KiB receiver filesystem reserve;
- non-active cached assets are evicted least-recently-used.

## Pi, Protocol, and Dashboard

- The local SDK/CLI:
  - builds C/C++ source for the pinned ESP32-S3 toolchain and a host preview renderer;
  - converts GIF/WebP to a nearest-neighbor 32×138 canvas and four tracks;
  - validates imports, ABI, geometry, timing, sizes, and parameter defaults;
  - generates the preview and signs the final `.lga`.
- Uploaded packages live under the Pi's persistent runtime animation library,
  which the deployment sync preserves.
- The SPI protocol includes:
  - capability/status query;
  - asset probe, begin, chunk, commit, idempotent `ASSET_ABORT` (`0x2A`), and
    remove;
  - firmware-animation start, stop, restart, and parameter update.
- The complete SPI transaction is capped at 4096 bytes. The sole 128-byte
  `LGS3` status contains transport counters, firmware-animation capabilities,
  active digest, cache space, upload progress/result, render timing, and
  quarantine state.
- Receiver responses are queued two deep. After each operation command, the host
  clocks two complete 128-byte status transfers before interpreting its
  acknowledgement; neither the concurrent response nor only one later snapshot
  is current for that command.
- Upload transaction:
  1. Freeze presentation on the last displayed frame.
  2. Probe caches and transfer only missing payloads.
  3. Write `.part` files with ordered, retryable chunks.
  4. Verify size, SHA-256, signature, ABI, geometry, and logical-device track.
  5. Commit each independently verified receiver in deterministic order.
  6. On failure, abort every receiver that may have entered upload/maintenance,
     issue compensating removals for possibly committed assets, re-probe every
     possibly published digest, expose abort/removal/residue failures in health
     telemetry, and never start partial playback. An abort acknowledgement is
     accepted only when its LGS3 snapshot also proves upload state `idle` and a
     display mode other than `maintenance`.
  7. Resume the previous presentation mode after an install-only operation.
- Starting an asset verifies all four caches, applies each logical strip offset and parameters, then sends sequential start commands. Switching back to Python frames automatically stops local playback.
- Manager state uses explicit `python`, `firmware_animation`, `painter`, and
  `idle` modes rather than representing firmware playback as a fake Python
  plugin. Provider, package digest, and parameters persist across Pi restarts.
- The dedicated "Receiver animations" dashboard gallery exposes preview,
  kind/version/health, upload progress, play, stop, live controls,
  install/reinstall, and delete. It rejects deletion of the active asset.
- Native manifests may expose `int`, `float`, `bool`, enum, and color parameters. Frame loops expose playback speed, pause, loop, and asset brightness. The global tempo control adjusts firmware time scale; plant modifiers and Python target-FPS controls are hidden as unsupported.
- Firmware-animation API endpoints:
  - `GET /api/firmware-animations`
  - `POST /api/firmware-animations/upload`
  - `POST /api/firmware-animations/<id>/play`
  - `POST /api/firmware-animations/<id>/install`
  - `GET /api/firmware-animations/<id>/preview`
  - `PATCH /api/firmware-animations/<id>/parameters`
  - `POST /api/firmware-animations/stop`
  - `DELETE /api/firmware-animations/<id>`

The general `POST /api/stop` route also stops the active provider, including
receiver-local playback.

## Failure Handling and Verification

- The receiver records the active native digest before execution. A panic or
  watchdog reset while it is active quarantines that digest, boots the compiled
  rainbow, and prevents automatic retry until explicit reinstall clears it.
- A render callback failure falls back cleanly; a 25 ms watchdog handles hangs.
- Automated tests cover ZIP traversal, signature failure, wrong
  target/ABI/geometry, malformed frame streams, truncated uploads, duplicate
  chunks, cache exhaustion, and idempotent retries. Power-interruption behavior
  still needs physical fault-injection acceptance.
- `startup-rainbow-native` exposes live speed, direction, and palette controls
  while sharing its renderer with the compiled fallback.
- Deterministically generated GIF and animated WebP test inputs are compared
  with decoded tracks within RGB565 quantization tolerance. Signed `.lga`
  outputs are build artifacts and are not checked into the repository.
- Tests cover mode transitions, Pi disconnect/restart state, persistence, cache
  eviction, live parameters, LGS3 parsing, dashboard APIs, and all-four-receiver
  transaction failure.
- Hardware acceptance:
  - native render p95 below 4 ms at 8×138 and its declared cadence;
  - frame decode p95 below 2 ms;
  - no receiver resets or missed deadlines during separate 30-minute native and frame-track soaks;
  - successful return to streamed Python animations without flashing or rebooting;
  - measure start skew and drift for reference, but do not gate v1 on cross-ESP synchronization.

## Software Acceptance Matrix

Every implementation lane has a focused automated gate in addition to the
repository-wide regression suite:

| Lane | Focused automated acceptance |
|---|---|
| `.lga` SDK and library | `tests/unit/test_firmware_package_sdk.py` and `tests/unit/test_firmware_package_cli.py` cover deterministic rebuilds, canonical manifests and signed indexes, P-256 verification, archive attacks, ABI/import/geometry/default rejection, receiver-compatible `LGT1` tracks, exact ASSET_BEGIN bytes, atomic install/replacement/recovery, interprocess reader/writer locking, active replacement/deletion rejection, and corrupt metadata isolation. |
| Receiver firmware | `pio test -d firmware/esp32 -e native` covers the sole LGS3 layout, the 4096-byte ceiling, display ownership, signed envelope binding, ordered/idempotent upload and abort, cache reserve/LRU policy, bounded track/loop-metadata decoding, typed controls, initialized capability reporting, quarantine, and watchdog fallback. |
| Native example authoring/runtime | `tests/unit/test_native_animation_examples.py` plus `just test-native-animations` compile every catalog source as standalone host and Xtensa modules, enforce ABI return/helper/zero-import/export contracts, generate the trusted preview, build and verify a signed `.lga` with a deterministic test-only key, render all four global offsets at 8×138, exercise default and maximum controls, compare distinct fingerprints, and record mean/p95/p99/max. Portable firmware tests prove the startup fallback is byte-identical to its native callback, preserve output canaries, and check long-running modular time across the 32-bit boundary. |
| Host transport and orchestration | `tests/unit/test_firmware_host_protocol.py` and `tests/unit/test_firmware_host_orchestration.py` cover transport-locked capability/status reads, queued acknowledgements, CRC and exact packet bounds, four-device signed-envelope binding, cache hits, upload abort, retry, rollback and residual-publication telemetry, fail-closed start/stop/parameter reconciliation, sequential starts, and host-frame takeover. |
| Manager, persistence, API, and dashboard | `tests/unit/test_firmware_modes.py` and `tests/unit/test_firmware_api.py` cover every explicit playback-mode transition, Pi restart and fast-deploy preservation/adoption for firmware state, firmware-only live controls, managed-path IPC, all REST validation/failure cases, active deletion, progress/degraded/unsupported health, and gallery assets/actions. |
| Production provisioning | `tests/unit/test_firmware_provisioning.py`, `tests/unit/test_firmware_build_hash.py`, and `tests/unit/test_deploy_recipes.py` cover key/config agreement, private-key separation, four unique stable ports, pinned device-specific build inputs, identity/capability readback, cache invalidation, and deploy wiring. |

The complete local merge gate is:

```bash
just test
```

For focused iteration, use `just test-unit`, `just test-native-animations`, and
`just test-firmware`; the last recipe runs both the portable PlatformIO suite
and the production ESP32-S3 build.

The production firmware is built with one trusted P-256 public key and a
distinct `LEDGRID_LOGICAL_DEVICE` value for each receiver. Both controller and
web processes must receive the matching public key through
`LEDGRID_LGA_TRUSTED_KEYS`. An empty receiver trust key advertises no
signed-upload capability. The unsigned-development flag must remain disabled
for installed receivers and is visible in both boot logs and LGS3 capabilities
when deliberately enabled on a bench.

Provisioning is initialized once with four stable ports in logical order. The
current workstation is already provisioned; the following is the initialization
recipe for a new/rotated installation, not a resume step:

```bash
just provision-native-animations \
  '/dev/serial/by-id/receiver-0,/dev/serial/by-id/receiver-1,/dev/serial/by-id/receiver-2,/dev/serial/by-id/receiver-3'
just deploy
```

The generated keypair, packages, and port mapping live under ignored
`run_state/firmware_authoring/`. Back up the private key securely; losing it
requires rotating the receiver trust key and rebuilding all signed packages.
`just deploy` fails rather than falling back to a shared logical-0 image,
mutable tty names, an empty trust key, or unsigned development mode.

Physical acceptance is a separate, required release gate because it cannot be
proved by a desktop build. Follow the authoritative
[post-repair sequence](#post-repair-physical-acceptance-still-required); do not
mark the feature production-ready from the software gates alone.

## Operating constraints

- All four receivers receive the new baseline firmware together.
- Packages are authored and signed through the local CLI; the dashboard never compiles source or holds private signing keys.
- The Pi is authoritative, while receiver flash is a disposable cache.
- The compiled startup native callback remains independent of uploaded assets,
  the dynamic loader, receiver cache, and configured trust key.
