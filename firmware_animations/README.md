# Firmware-animation package SDK

`firmware_animations` is the trusted authoring and Raspberry Pi library for
receiver-local `.lga` animations. The dashboard never compiles or signs code.

The software/package flow is implemented, but the installed wall is not yet
release-ready. A confirmed SPI1 MISO-to-MOSI short/coupling blocks receivers 2
and 3 from returning identity and acknowledgements even though MOSI frame
streaming works. Do not bypass readiness; resume from the
[dated implementation handoff](../docs/plan-native-animations.md#cold-resume-handoff-2026-08-08).

## Package contract

Every package is a deterministic ZIP with fixed timestamps and permissions. It
contains canonical `manifest.json`, a fixed 176-byte signed `index.lgix`, a raw
64-byte low-S ECDSA P-256 signature, an animated 32×138 WebP preview, and either
one ESP32-S3 ELF shared object or four `LGT1` receiver tracks. Rebuilding with
the same inputs and key produces byte-identical output.

The index is big-endian and fixed-width:

```
LGIX | format:u8 | kind:u8 | abi:u16 | target:u16 |
receivers:u8 | local_strips:u8 | wall_strips:u16 | leds:u16 |
manifest_sha256:32 | device_payload_sha256:4x32
```

ABI and target IDs are both `1` for `lga-animation-v1` and
`esp32s3-elf-loader-1.3.2`. Signatures are deterministic RFC 6979 P-256
`r || s`; key IDs are `key-` plus the first 16 lowercase hex characters of the
public-key fingerprint.

Before install, verification checks the archive structure and expansion ratio,
signature and trusted key, every hash, ABI/target/geometry, native ELF/imports,
typed defaults, preview, and complete frame-track decode. Package, native, and
per-receiver frame limits are 16 MiB, 512 KiB, and 2.5 MiB (2,621,440 bytes)
respectively.

Native ABI v1 modules must be ELF32 Xtensa shared objects, export the unmangled
`ledgrid_animation_v1` symbol, and have no undefined imports. Their
initialize/render callbacks return `LEDGRID_ANIMATION_OK` (`0`) on success and
write exactly one caller-owned 8×138 RGB frame. Runtime services come from the
helper table; modules do not call receiver drivers or peripherals.

Frame packages contain four device-specific `LGT1` tracks. V1 track metadata is
always infinite (`loop_count == 0`) and nonzero serialized counts are rejected
by both SDK and receiver. The public `loop` control expresses the supported
runtime choice: false holds after one pass and true repeats indefinitely. The
other controls are `pause`, `playback_speed`, and `asset_brightness`. The manager
adds global `time_scale`; receivers clamp composed playback speed to 0.1–4.0x.

## Public controller API

```python
from firmware_animations import FirmwareAnimationLibrary

library = FirmwareAnimationLibrary(root, {key_id: public_key_path})
installed = library.install(package_path)
payload = library.read_payload(installed.package_id, device_index=0)
envelope = library.verification_envelope(installed.package_id, device_index=0)
asset_begin = envelope.asset_begin_command()
```

`asset_begin` is the exact 313-byte big-endian receiver command (315 bytes with
SPI CRC). It binds the selected payload size/digest and logical device to the
signed kind, ABI, target, geometry, manifest, and four device hashes. Library
publication uses fsynced temporary files, atomic rename, and a shared/exclusive
interprocess file lock across the controller and web processes; recovery removes
partial and orphan objects. An `active_id_provider` hook rejects deletion by
package ID or content digest and rejects replacing an active ID with different
content.

The deployed server constructs this library at `run_state/firmware_animations`
and reads trusted public keys from `LEDGRID_LGA_TRUSTED_KEYS`. The variable is a
path-separated list of `key-id=path/to/public.pem` entries and must be present
in both the controller and web processes. `just deploy` supplies both processes
from the validated public provisioning environment; the private key is never
copied to the Pi.

For the checked-in examples, initialize the ignored authoring state once with
four stable USB serial paths in logical wall order, then deploy:

```bash
just provision-native-animations \
  '/dev/serial/by-id/receiver-0,/dev/serial/by-id/receiver-1,/dev/serial/by-id/receiver-2,/dev/serial/by-id/receiver-3'
just deploy
```

That provisioning command is installation bootstrap/rotation, not an ordinary
resume step. The current workstation was already provisioned as of 2026-08-08;
follow the dated handoff and verify its ignored state without exposing key
contents before deploying.

Open `/firmware-animations` (the “Receiver animations” navigation item) to see
the installed Startup Rainbow, Aurora Ribbons, and Meteor Shower packages.
“Install on receivers” fills missing caches without changing the prior display
mode; “Start on wall” installs missing payloads and starts local playback;
“Apply to playing wall” sends live typed parameters. “Stop receiver playback”
returns to the non-package mode, and starting any root-dashboard Python plugin
reclaims display ownership with its first complete host frame. The preview is a
trusted build-time WebP, not live framebuffer readback. The root dashboard
continues to list Python plugins only.

## CLI

Run the CLI inside the repository environment so Pillow and ECDSA are present:

```bash
mkdir -p build

uv run --with pillow --with 'ecdsa>=0.19.0' \
  python tools/firmware_animation_package.py keygen \
  --private authoring.pem --public trusted.pem

uv run --with pillow --with 'ecdsa>=0.19.0' \
  python tools/firmware_animation_package.py build-frames \
  --source loop.webp --metadata metadata.json \
  --private-key authoring.pem --output loop.lga

uv run --with pillow --with 'ecdsa>=0.19.0' \
  python tools/firmware_animation_package.py verify loop.lga \
  --trusted-key trusted.pem
```

The metadata file supplies `id`, `name`, semantic `version`, `description`,
`preferred_fps`, `parameter_schema`, and `provenance`. Geometry, kind, ABI,
target, hashes, and signing key ID are owned by the packer and must not be added
by callers. The checked-in native metadata under `examples/` is the canonical
reference.

For a native source, first inspect or execute the pinned target/host build
contract, then package the target module with the trusted host library used to
generate its preview:

```bash
uv run --with pillow --with 'ecdsa>=0.19.0' \
  python tools/firmware_animation_package.py native-build \
  --source animation.cpp \
  --module-output build/animation.esp32.so \
  --host-output build/animation.host.so \
  --execute

uv run --with pillow --with 'ecdsa>=0.19.0' \
  python tools/firmware_animation_package.py build-native \
  --module build/animation.esp32.so \
  --host-library build/animation.host.so \
  --metadata metadata.json --private-key authoring.pem \
  --output animation.lga
```

`native-build` prints the compiler commands and executes them only with
`--execute`. `build-native` uses Xtensa `nm` to enforce zero undefined imports
and the required ABI export before packaging. The pinned Xtensa/ESP-IDF
toolchain is an external authoring prerequisite; the CLI does not download it.

## Acceptance

```bash
just test-unit
just test-native-animations
just test-firmware
```

`just test-native-animations` compiles every checked-in catalog example as a
host preview and standalone Xtensa module, signs and verifies a test package,
renders all four global offsets at exact 8×138 geometry, checks default and
maximum-work fingerprints, and enforces a 4 ms desktop p95 proxy. It is not an
ESP32 measurement. `just test-firmware` runs the portable receiver suite and
the production cross-build; it does not flash hardware.

The repository checks in source, metadata, and deterministic test-source
generation, not production-signed `.lga` files. On-wall timing, crash/reset
recovery, start skew/drift, return to streamed frames, and native/frame-track
soaks remain separate physical release gates.
