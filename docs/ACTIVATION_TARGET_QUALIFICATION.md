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
- a fresh status drain reports exactly five receivers with actual latest
  status v3+ responses and per-process `receiver_status_max_version_seen=7`
  proof (a later ordinary v3 response does not erase an earlier v7
  observation), with logical identities `0..4`, routes
  `0.0, 0.1, 1.1, 1.0, 1.2`, widths
  `8, 8, 8, 8, 1`, offsets `0, 8, 16, 24, 32`, installed direction maps, and
  aligned-envelope capability `1<<14`, retained FEC-v2/v3/v4/v5/v6 rollback
  capabilities `1<<15`, `1<<16`, `1<<17`, `1<<18`, and `1<<19`, and active
  FEC-v7 capability `1<<20`; every Host receiver reports
  `transport_envelope_enabled=true`, the aggregate enabled count is exactly
  five, and positive semantic/envelope/padding byte deltas reconcile exactly
  with wire-byte, transfer, CRC-byte, and FEC-parity deltas; exactly logical
  receiver 3 is requested/enabled for FEC after a settled three-observation
  negotiation;
- every receiver's dedicated successful `SET_ALL` counters advance at at least
  `150 FPS`; logical receivers 0-2 reconcile exactly from 3,313 semantic bytes
  to 3,320 aligned wire bytes per frame, logical receiver 3 from 3,313 to 4,088
  FEC wire bytes (68 diagonally interleaved codewords, 680 inner RS parity
  bytes, one 50-byte outer XOR parity shard, 26 zero-tail bytes, and two
  four-byte raw headers),
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
- all receivers have actually produced status v7 in the current Host process;
  receiver 3's received and accepted FEC deltas equal
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

When an independently calibrated electrical logger spans the same complete
capture window, add both arguments below. The JSON is a descriptor for the raw
logger export and certificate; it is not a summary file. Neither argument is
valid alone:

```bash
  --electrical-measurement <calibrated-capture-descriptor.json> \
  --budget-digest <checked-calibrated-budget-digest>
```

The helper is observation-only apart from a receiver status query and the
atomic evidence-file replacement. It never starts, stops, or reconfigures the
wall. A failed capture leaves the previous evidence file untouched. A fresh
Composer Check loads the strict envelope only when its binding digest matches.
The retained target-evidence envelope is schema v3 and includes a strict
`transport` proof: exact aggregate and logical-device `0..4` full-frame/FEC deltas,
the 3,320/4,088/424-byte expected wire sizes, separate latest and sticky maximum
status-version fields, final sample-gap/FEC-decode gauges, selected spidev
buffer capacities, and write-only fast-path support. Aggregate additive values
must equal the receiver sums, while gap, capacity, and support aggregates must
equal the receiver maximum, minimum, and conjunction respectively. Schema-v1/v2,
omitted, malformed, unknown, reset, or internally drifted transport evidence is
rejected rather than treated as a compatible receipt.

Schema v3 also permits one optional strict `electrical_measurement` capture
descriptor with `capture_started_at`. Those fields must appear together. The
descriptor is schema `ledgrid.calibrated-electrical-capture` version 2 and pins
two local, regular, non-symlink artifacts by absolute path, byte size, and
SHA-256: the immutable raw logger export and its calibration certificate. A
reviewed-common-distribution topology pins a third attestation artifact the same
way. Every load reopens and verifies those files. A missing, replaced, resized,
or modified artifact invalidates the complete envelope.

The evaluator does not trust declared measurement summaries. The only accepted
raw logger format is UTF-8 CSV `ledgrid-electrical-csv-v1`:

```text
timestamp_ms,voltage_v,current_a
<integer Unix epoch milliseconds>,<finite volts>,<finite amps>
...
```

Timestamps must be canonical positive integers, strictly increasing, and no
adjacent gap may exceed 1,000 ms. There must be at least two simultaneous
voltage/current rows. The first and last samples must tightly bracket the target
capture (at most one observed sample interval of extra data on either side).
The loader and final qualification evaluator independently recompute sample
count, sample window, nearest-rank interval p95/p99/max, mean interval, and
nearest-rank mean/p95/p99/max voltage and current. Retained derived values must
match exactly. Adding hand-authored `sample_count`, `window`, `voltage_v`, or
`current_a` fields to the descriptor is rejected as an unknown-field error.

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

Deployment readiness uses the Host-process terminal baselines because receiver
counters survive Host restarts. Target capture and the guarded soak retain their
own explicit before/after counter windows and continue requiring zero terminal
growth in those windows; they do not substitute the process baseline for their
stronger capture-local delta proof.

The aligned/FEC transport is necessary but not itself PERF-01 integrity evidence.
[Espressif's ESP32-S3 SPI-slave DMA contract](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-reference/peripherals/spi_slave.html)
requires DMA RX buffers and transaction lengths to be word-aligned/four-byte
multiples and warns that inappropriate Host writes may be discarded. The
versioned envelope satisfies that rule and keeps terminal faults visible.
Because retained wall evidence contains receiver-3 CRC and uncorrectable failures
at 20 MHz, PERF-01 remains red until full-size 4,088-byte v7 receiver-3 FEC traffic
meets the exact 150-FPS gate with zero legacy/terminal fault growth.

## POWER-01 status: UNCALIBRATED

No qualifying electrical measurement exists for the installed wall. POWER-01
therefore remains fail-closed and does not pass.

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

A read-only recheck on 2026-08-29 found the same limitation. Home Assistant
still exposes only `switch.light_living_ledwall`, node status, ping, scene, and
firmware for the wall relay. Whole-home Sense power/voltage entities and other
device-specific energy sensors are not isolated to the wall, do not provide the
required wall current/voltage sample pair, and have no retained calibration
certificate tied to this installation. They cannot qualify POWER-01. The V4
HAT schematic/PCB exports contain ESP32 modules, AP2112 regulators, HCT buffers,
USB breakouts, and logic/data connectors, but no voltage/current monitor,
calibrated shunt, or LED-power distribution measurement path. The HAT also does
not document the installed five-receiver wall's supply/fuse topology.

Accordingly, `config/installation_qualification_budget.json` intentionally
keeps calibration status `unqualified` and every physical limit `null`. Target
evidence carries `electrical: null`. Do not derive voltage/current from pixel
values, LED-count estimates, relay state, or nominal supply assumptions. A later
POWER-01 run requires a calibrated instrument, an as-built supply/fuse budget,
and measurements bound to this same brightness and activation identity.

### Required POWER-01 calibration and evidence procedure

1. **Record the as-built electrical system before choosing limits.** Photograph
   and transcribe every supply manufacturer/part number and nameplate rating,
   AC feed and relay rating, fuse/breaker type and value, wire gauge, branch
   allocation, injection point, connector, and ground/return path. Identify the
   LED strip part number and allowed supply range. A copied software default or
   LED-count estimate is not an as-built rating.
2. **Choose the measurement boundary explicitly.** For the installed 5 V wall,
   measure total current at the common DC distribution input and voltage at the
   electrically worst LED injection/return point while the exact canary runs.
   If multiple independent supplies or branches cannot be observed at one common
   point, log every branch simultaneously and retain both the per-branch samples
   and their time-aligned total. A mains-only whole-home estimate does not prove
   the 5 V distribution or branch protection.
3. **Use traceable equipment.** The voltage/current logger or power analyzer
   must have a serial number and an unexpired calibration certificate covering
   the complete capture window. Retain the manufacturer, model, serial,
   certificate ID, calibration laboratory, calibration and expiry times,
   measurement location, acquisition method, sample rate, and raw export.
4. **Calibrate the versioned budget from verified limits.** Increment the budget
   revision and set `calibration.status` to `calibrated`. Populate every physical
   limit: minimum mean voltage, maximum p99 voltage, maximum current, maximum
   controller brightness, required current headroom ratio, maximum p99 power,
   and evidence age. Limits must come from the verified strip/supply/protection
   envelope and reviewed derating policy, not from the measured scene itself.
   A defensible first qualification may cap brightness at exactly `50`; raising
   it requires a separately reviewed calibration.
5. **Freeze the source artifacts.** Export the logger once in the exact CSV
   format above and retain the original calibration certificate. Move both to
   an immutable evidence location; do not normalize, crop, spreadsheet-edit, or
   overwrite them after their digests are recorded. Use absolute local paths.
6. **Create the capture descriptor.** Use schema
   `ledgrid.calibrated-electrical-capture`, version `2`, with the exact Check
   binding digest, calibrated budget digest, activation ID, and brightness.
   Include `raw_logger_export` (`path`, `sha256`, `size_bytes`, and
   `format: ledgrid-electrical-csv-v1`), `calibration_certificate` (`path`,
   `sha256`, `size_bytes`), instrument manufacturer/model/serial, and calibration
   certificate ID/laboratory/start/expiry. Do not include summary statistics.

   Bind the electrical coverage in one of two ways:

   - `exact_measurement_points`: for a single wall-exclusive branch, name that
     one `branch_id` and its exact `voltage_point` and `current_point`. Each raw
     row is the simultaneous pair at those points.
   - `reviewed_common_distribution`: name the common voltage/current points and
     every covered branch, then include reviewer identity/time and a locally
     digested `topology_attestation` proving that the common points cover them.

   Multiple independent branch pairs are intentionally outside this simple v2
   CSV contract; qualify them through a reviewed common point or a later
   versioned multi-channel format. A vague location string or an unreviewed
   common-feed assertion is rejected.
7. **Run one simultaneous target capture.** Start the independent logger before
   `target_evidence.py`; keep it recording until after the helper's final status
   observation. Pass `--electrical-measurement` and `--budget-digest`. The helper
   rejects unknown/missing fields, identity/brightness/budget mismatches, invalid
   artifact digests or sizes, malformed/non-finite/unordered raw rows, excessive
   sample gaps, incomplete window coverage, invalid topology scope, and expired
   calibration. It attaches the calibrated measurement only to `controller_pi`;
   receiver electrical evidence remains null.
8. **Review the retained result.** Independently verify every local artifact
   digest, size, measurement point/branch, and certificate. Confirm the evaluator
   re-derived the retained raw-sample statistics. POWER-01 passes
   only if mean/p99 voltage, maximum current with required headroom, p99 power,
   brightness, freshness, and exact activation binding all pass the checked-in
   evaluator. Retain failures rather than editing them into passing evidence.

The installed wall cannot execute this procedure with its present relay, Pi,
receivers, or HAT alone. It can be qualified without redesigning the HAT by
temporarily installing a suitable calibrated DC voltage/current logger at the
documented distribution boundary. Permanent unattended qualification requires
a calibrated, maintainable measurement channel plus a calibration-renewal
process; the proposed V4 board does not provide one.
