# Uploadable Firmware Animations

## Summary

Introduce a second animation backend alongside Python plugins:

- **Native animations:** trusted C/C++ modules compiled as ESP32-S3 shared objects and loaded from PSRAM using Espressif's supported [`elf_loader`](https://components.espressif.com/components/espressif/elf_loader/versions/1.3.2/readme).
- **Frame animations:** GIF or animated WebP converted into compact, device-specific frame tracks.
- The Raspberry Pi owns the animation library; all four ESP32s maintain recoverable caches.
- One baseline firmware flash installs the loader. Subsequent animations are uploaded without reflashing.
- Strict cross-controller synchronization and shared v-sync remain out of scope; starts are sequential and each receiver uses its local clock.

## Firmware and Package Architecture

- Migrate the receiver entrypoint from Arduino wrappers to native ESP-IDF while preserving the existing SPI, mailbox, parallel LED driver, and compiled startup rainbow.
- Pin `elf_loader` 1.3.2, enable ESP32-S3 PSRAM execution and dynamic shared objects, and expose a versioned `extern "C"` animation ABI.
- A native module exports one v1 entrypoint returning callbacks for initialization, full local-frame rendering, and cleanup. Each render receives:
  - local 8×138 output buffer;
  - global strip offset;
  - scaled elapsed time and frame index;
  - typed parameter values;
  - bounded host helpers for color conversion, math, and deterministic randomness.
- C++ modules compile without exceptions or RTTI. Imported symbols are checked against the SDK allowlist for compatibility; signing is the security boundary because native code is not sandboxed.
- Add a generic frame-loop player using per-device RGB565 keyframes plus forward delta/run encoding, original frame durations, looping, pause, playback speed, and asset brightness.
- Keep the compiled startup rainbow as the boot, missing-asset, and crash fallback.

### `.lga` Package

A deterministic ZIP package contains:

- canonical manifest: ID, name, version, description, kind, ABI, target geometry, preferred FPS, parameter schema, payload hashes, and provenance;
- signed binary index containing all four device payload hashes;
- one native `.so` or four frame tracks;
- generated animated WebP preview;
- ECDSA P-256 signature.

Production firmware embeds one trusted public key. Private keys remain on authoring machines. An explicit development build flag may permit unsigned packages and must be exposed in receiver status.

Limits:

- 16 MiB package upload;
- 512 KiB native payload;
- 2.5 MiB frame payload per receiver;
- at least 512 KiB receiver filesystem reserve;
- non-active cached assets are evicted least-recently-used.

## Pi, Protocol, and Dashboard

- Add a local SDK/CLI that:
  - builds C/C++ source for the pinned ESP32-S3 toolchain and a host preview renderer;
  - converts GIF/WebP to a nearest-neighbor 32×138 canvas and four tracks;
  - validates imports, ABI, geometry, timing, sizes, and parameter defaults;
  - generates the preview and signs the final `.lga`.
- Store uploaded packages under the Pi's persistent runtime animation library so deployments preserve them.
- Extend the SPI protocol with:
  - capability/status query;
  - asset probe, begin, chunk, commit, and remove;
  - firmware-animation start, stop, restart, and parameter update.
- Increase the DMA transaction buffer to 4096 bytes and introduce a backward-compatible 128-byte `LGS3` status containing firmware-animation capabilities, active digest, cache space, upload progress/result, render timing, quarantine state, and existing receiver counters.
- Upload transaction:
  1. Freeze presentation on the last displayed frame.
  2. Probe caches and transfer only missing payloads.
  3. Write `.part` files with ordered, retryable chunks.
  4. Verify size, SHA-256, signature, ABI, geometry, and logical-device track.
  5. Atomically rename only after every receiver verifies.
  6. Resume the previous animation; a partial failure activates nothing and leaves the Pi package marked for retry.
- Starting an asset verifies all four caches, applies each logical strip offset and parameters, then sends sequential start commands. Switching back to Python frames automatically stops local playback.
- Extend manager state to explicit `python`, `firmware_animation`, `painter`, and `idle` modes rather than representing firmware playback as a fake Python plugin. Persist provider, package digest, and parameters across Pi restarts.
- Add a dedicated "On-device animations" dashboard gallery with preview, kind/version/health, upload progress, play, stop, live controls, retry, and delete. Reject deletion of the active asset.
- Native manifests may expose `int`, `float`, `bool`, enum, and color parameters. Frame loops expose playback speed, pause, loop, and asset brightness. The global tempo control adjusts firmware time scale; plant modifiers and Python target-FPS controls are hidden as unsupported.
- API additions:
  - `GET /api/firmware-animations`
  - `POST /api/firmware-animations/upload`
  - `POST /api/firmware-animations/<id>/play`
  - `PATCH /api/firmware-animations/<id>/parameters`
  - `DELETE /api/firmware-animations/<id>`

## Failure Handling and Verification

- Record the active native digest before execution. A panic or watchdog reset while it is active quarantines that digest, boots the compiled rainbow, and prevents automatic retries until explicit reinstall or quarantine clearance.
- Stop cleanly when a render callback returns failure. A 25 ms render watchdog handles hangs.
- Validate ZIP traversal protection, signature failure, wrong target/ABI/geometry, malformed frame streams, truncated uploads, duplicate chunks, CRC loss, power interruption, cache exhaustion, and idempotent retries.
- Add a native example that reproduces the startup rainbow with live speed, direction, and palette controls.
- Add representative GIF and animated WebP packages and compare decoded frames against the packer within RGB565 quantization tolerance.
- Test all mode transitions, Pi disconnect/restart behavior, persistence, cache eviction, live parameters, status parsing, dashboard APIs, and all-four-receiver transactional failure.
- Hardware acceptance:
  - native render p95 below 4 ms at 8×138 and its declared cadence;
  - frame decode p95 below 2 ms;
  - no receiver resets or missed deadlines during a 30-minute native and frame-loop soak;
  - successful return to streamed Python animations without flashing or rebooting;
  - measure start skew and drift for reference, but do not gate v1 on cross-ESP synchronization.

## Assumptions

- All four receivers receive the new baseline firmware together.
- Packages are authored and signed through the local CLI; the dashboard never compiles source or holds private signing keys.
- The Pi is authoritative, while receiver flash is a disposable cache.
- The current compiled startup animation remains independent of uploaded assets.
