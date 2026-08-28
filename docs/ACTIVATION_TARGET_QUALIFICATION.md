# Activation target qualification

PERF-01 and POWER-01 are separate gates for one exact guarded activation.
Browser, controller/Pi, receiver, and electrical claims must remain separately
labeled. A browser estimate is never target or electrical evidence.

## Exact PERF-01 canary basis

The installed-wall canary uses the browser-managed Python `rainbow` background,
no Clock layer, the selected empty installation profile, neutral vibe, no plant
modifiers, operator speed `1.0`, controller brightness `50`, target `150 FPS`,
and the installed `33 x 138` geometry. The retained binding digest, guarded
basis digest, host-scene digest, global-settings digest, and activation ID come
from the same Check and activation receipt. They are values to capture, not
constants to copy from this document.

PERF-01 passes only when all of the following hold in one observation window:

- the guarded receipt stays `active` with identical requested and observed
  scene, globals, and installation-profile identities;
- the live plugin, brightness, profile, and target FPS match the checked basis;
- a fresh status drain reports exactly five status-v3 receivers with logical
  identities `0..4`, routes `0.0, 0.1, 1.1, 1.0, 1.2`, widths
  `8, 8, 8, 8, 1`, offsets `0, 8, 16, 24, 32`, installed direction maps, and
  aligned-envelope capability `1<<14` and FEC capability `1<<15`; every Host receiver reports
  `transport_envelope_enabled=true`, the aggregate enabled count is exactly
  five, and positive semantic/envelope/padding byte deltas reconcile exactly
  with wire-byte, transfer, CRC-byte, and FEC-parity deltas; exactly logical
  receiver 3 is requested/enabled for FEC after a settled three-observation
  negotiation;
- every receiver's dedicated successful `SET_ALL` counters advance at at least
  `150 FPS`; logical receivers 0-2 reconcile exactly from 3,313 semantic bytes
  to 3,320 aligned wire bytes per frame, logical receiver 3 from 3,313 to 3,380
  FEC wire bytes (26 codewords, 52 parity bytes, four outer zero-tail bytes),
  and logical receiver 4 from 415
  semantic bytes to 424 aligned wire bytes per frame, so status/SHOW/partial
  traffic cannot substitute for full-frame stress;
- full-frame response sampling remains auditable while the write-only fast path
  is active: sampled-response transfers plus write-only transfers equal every
  full-frame transfer, successfully parsed samples advance, sample-miss deltas
  stay zero, current and lifetime maximum sample gaps stay within 256 frames,
  and every selected spidev buffer can hold its receiver's maximum wire frame;
- every sampled Raspberry Pi rolling window reports ordered mean, p95, p99, and
  maximum latency; the retained values are the worst seen anywhere in the
  capture, p95 does not exceed the `6.67 ms` frame period, and the minimum
  observed cadence is at least `150 FPS`;
- all five receivers advance displayed-frame counters at at least `150 FPS`;
  their measured p95 critical-stage latency fits `6.67 ms` and CRC, publish-drop,
  SPI-queue, display, and status-miss counters have zero delta;
- receiver 3 reports status v7, and its received and accepted FEC deltas equal
  Host FEC-sent/full-frame deltas exactly; uncorrectable, semantic-CRC, and
  framing deltas are zero, while corrected packet/codeword deltas may be
  nonzero and must remain internally consistent;
- the complete pair is retained atomically at
  `run_state/activation_qualification_evidence.json` and remains no older than
  the checked-in four-hour evidence-freshness policy.

After the exact canary is active, run the deployed helper from the selected
release. Replace every placeholder with values from that activation's Check and
receipt:

```bash
cd /home/ledgridwall/ledgrid-pod/current
venv/bin/python tools/qualification/target_evidence.py \
  --binding-digest <check-qualification-binding-digest> \
  --basis-digest <check-basis-digest> \
  --expected-scene-digest <active-host-scene-digest> \
  --expected-global-settings-digest <check-global-settings-digest> \
  --expected-profile-digest <selected-profile-digest> \
  --activation-id <activation-id> \
  --plugin rainbow --target-fps 150 --brightness 50 --duration 60
```

The helper is observation-only apart from a receiver status query and the
atomic evidence-file replacement. It never starts, stops, or reconfigures the
wall. A failed capture leaves the previous evidence file untouched. A fresh
Composer Check loads the strict envelope only when its binding digest matches.
The retained target-evidence envelope is schema v2 and includes a strict
`transport` proof: exact aggregate and logical-device `0..4` full-frame/FEC deltas,
the 3,320/3,380/424-byte expected wire sizes, final sample-gap/FEC-decode gauges, selected spidev
buffer capacities, and write-only fast-path support. Aggregate additive values
must equal the receiver sums, while gap, capacity, and support aggregates must
equal the receiver maximum, minimum, and conjunction respectively. Schema-v1,
omitted, malformed, unknown, reset, or internally drifted transport evidence is
rejected rather than treated as a compatible receipt.

The same envelope is scoped to one immutable app release, one controller
process session and state revision, and the exact active controller runtime
identity. The activation receipt's requested, normalized, and observed
identities must be unanimous, and its post-activation revision must equal the
live controller revision at both capture boundaries. The receiver evidence
carries the canonical digest of the normalized transport proof, and activation
qualification-record schema v2 makes that digest mandatory. A deploy,
controller restart, state mutation, runtime-identity change, missing identity
field, or digest mismatch invalidates the retained target evidence and requires
a fresh capture; evidence from release A can never qualify release B.

The aligned/FEC transport is necessary but not itself PERF-01 integrity evidence.
[Espressif's ESP32-S3 SPI-slave DMA contract](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-reference/peripherals/spi_slave.html)
requires DMA RX buffers and transaction lengths to be word-aligned/four-byte
multiples and warns that inappropriate Host writes may be discarded. The
versioned envelope satisfies that rule and keeps terminal faults visible.
Because retained wall evidence contains 26 receiver-3 CRC failures in 60 seconds
at 20 MHz, PERF-01 remains red until full-size 3,380-byte receiver-3 FEC traffic
meets the exact 150-FPS gate with zero legacy/terminal fault growth.

## POWER-01 status: OPERATOR_WAIVED

The operator explicitly waived electrical measurement for this release work.
POWER-01 therefore remains fail-closed and does not pass.

The 2026-08-27 inventory found no calibrated measurement source:

- the controller is a Raspberry Pi 4 Model B Rev 1.5 with no `hwmon` device, no
  I2C device node, and no attached USB measurement instrument; its USB inventory
  contains only the five ESP32-S3 receiver debug interfaces and hubs;
- the Home Assistant wall relay is a GE `14288 / ZW1002` in-wall outlet and
  exposes switch, node-status, scene, ping, and firmware entities only—no power,
  current, voltage, or energy channel;
- receiver status exposes digital timing and integrity counters, not supply
  voltage or installed-wall current;
- the repository has no verified power-supply rating, fuse allocation, branch
  current limit, calibrated shunt, or instrument calibration record.

Accordingly, `config/installation_qualification_budget.json` intentionally
keeps calibration status `unqualified` and every physical limit `null`. Target
evidence carries `electrical: null`. Do not derive voltage/current from pixel
values, LED-count estimates, relay state, or nominal supply assumptions. A later
POWER-01 run requires a calibrated instrument, an as-built supply/fuse budget,
and measurements bound to this same brightness and activation identity.
