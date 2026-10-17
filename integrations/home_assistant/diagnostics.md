# Home Assistant diagnostics and controller image

These entities describe the host controller and its existing receiver telemetry.
They do not establish that all five receivers displayed a requested frame. The
protected mains outlet stays a separate device. Reading diagnostics or an image
never starts, stops, renders, or changes playback/settings.

## Routine status

The existing shared two-second status poll supplies these sensors under the wall
device, with no extra per-entity HTTP requests:

- Requested content: the requested Scene's background/animation identifier.
- Active content: an observed saved Look/preset/animation/playlist match. Its four
  attributes retain the independent stable content IDs. Ambiguous or missing
  matches remain `none`; the last clicked card is not used as playback evidence.
- Controller playback: `running`, `stopped`, or `failed` when a playback error is
  present. Controller error and playlist error remain separate text sensors;
  `none` means no reported error and messages are bounded to 255 characters.
- Playlist status: the runner's actual phase, independently of controller state.
- Controller connectivity: `online` means a fresh valid controller observation;
  `offline` means no fresh observation, including failed HTTP access. It does not
  imply receiver connectivity or prove physical output.
- Receiver 0 through 4 connectivity: the existing logical receiver identifiers,
  kept stable across reconnect/reload. The installation's accepted receiver 3
  degradation refers to logical ID 3; labels are not shifted to 1 through 5.

Unavailable/stale controller observations make ordinary state entities
unavailable. Connectivity and error states are not inferred from a selected Scene.
Timestamp-only polls do not publish new status values.

## Optional diagnostic sensors

Host cadence/runtime sensors and detailed receiver counters are **disabled by
default**. Enable the ones you need in the device's entity settings. All enabled
details share one 30-second poll of `GET /api/v1/automation/diagnostics`. With all
details disabled there is no diagnostic poll. Unchanged projected values do not
publish new state, and routine state attributes contain neither frame blobs nor
full counter dictionaries.

Host sensors expose target and measured host cadence (`fps`), generated/presented
and skipped frames, average generation/send time and p95 frame time (`ms`), and
available deadline-miss counts. Host cadence describes host work; it is never
labelled receiver output cadence or installed-wall performance evidence.

Each receiver offers its measured output cadence (`fps`), cadence evidence,
protected-status summary, CRC errors, FEC corrected/uncorrectable packet counts,
FEC semantic CRC/framing errors, and status-integrity errors. Counters are sampled diagnostics, rather than live
per-frame measurements. Controller failure makes these entities unavailable
immediately via the routine poll; missing/stale/untrusted receiver data yields
unknown numeric values independently of the rest of the wall.

Receiver output cadence is calculated only between two fresh, trusted protected
counter samples in the same controller session. It uses displayed-frame counter
changes and the receiver observation interval, never the host's render/send rate.
A first/missing/stale/disconnected sample, unverified protected status, counter
reset/wrap, changed integrity-error count, or an interval longer than 65 seconds
has an explicit evidence reason and unknown cadence. Identical repeated samples
retain their already calculated interval; they do not manufacture zero FPS.

Protected status reports `verified`, `verified_with_rejections`,
`rejections_in_interval`, `unverified`, `stale`, or `missing`. The current driver
retains the last accepted protected sample and a cumulative rejection count; it
does not timestamp the last invalid packet. Consequently a rejection anywhere
within an observed interval makes that interval unsuitable for a rate, even if
another trusted sample subsequently arrived. Untrusted wire fields from a
rejected status packet are never exposed as authoritative counters. Receiver 3's
accepted CRC/FEC/rejection/low-rate degradation stays visible without making a
fresh controller or other receivers unavailable.

## Optional controller-output image

The `Controller output` image is **disabled by default**. Enable it, then view its
entity image to request a snapshot. There is no permanent video stream or image
capture task. The image is a PNG of the existing controller RGB buffer: width 33
strips and height 138 LEDs, with pixel `(x, y)` corresponding to controller buffer
index `x * 138 + y`. It is not a camera, receiver readback, or physical-display
receipt; the buffer also does not model downstream output brightness or room
appearance. When stopped it may show the last controller buffer.

`GET /api/v1/automation/frame.png` requests a correlated read-only snapshot
through the separate file IPC path. The controller control loop checks for a
request at most four times per second, copies its current frame only for a fresh
request in the current session, and limits captures to one per five seconds.
Expired/mismatched requests never trigger a later capture. Web PNG encoding and
HA image requests also coalesce/cache for five seconds, including failures.
Nothing is captured/encoded while the image is disabled or unrequested.

Readback waits at most 1.5 seconds, request intent expires in two seconds, and HA
image HTTP waits at most three seconds. Wrong geometry, incomplete/invalid RGB,
controller restart, stale observation or absent readback returns unavailable;
there is no substitute preview renderer or fabricated black frame. The PNG
response includes capture time, controller session and an explicit
`controller-output-not-physical-display` source label. Capture timestamps refer
to copying the existing buffer, not to a receiver output event.

## Isolated checks

No check below contacts the installed wall:

```sh
uv run python -m pytest -q tests/unit/test_automation_diagnostics.py
uv run --python 3.14 --with homeassistant==2026.10.1 --with pyyaml python integrations/home_assistant/check_diagnostics_runtime.py
```

The host tests use actual `AnimationManager` buffer readback for PNG geometry,
plus telemetry fixtures for freshness/integrity/reset cases. The HA runtime
checks use an isolated loopback API and temporary HA configuration, enable
selected optional entities, and verify grouping, shared polling, state
suppression, failure/stopped distinctions, default-disabled image and details,
image rate limiting, unload and zero playback writes. The focused overhead check
bounds diagnostics serialization and idle control-loop request checks. Installed
wall no-stutter observation remains part of final live acceptance.
