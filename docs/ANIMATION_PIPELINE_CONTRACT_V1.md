# Animation Pipeline Contract v1

## Scope and authority

This document freezes the names, bytes, state boundaries, and rollout gates
needed by Phase 1 of
[plan-revamped-animation-pipeline.md](plan-revamped-animation-pipeline.md).
It is a reference contract, not an active scene or receiver implementation.
All six rollout flags remain off, so the current Python manager, API,
persistence, preview, SPI protocol, and receiver behavior remain authoritative.

Machine-readable reference vectors live in
`tests/fixtures/animation_pipeline_v1.json`. Python and portable C++ tests must
consume or reproduce those exact values before a later phase can activate the
corresponding behavior.

## Frozen schema and protocol identities

Schema IDs are stable wire and persistence vocabulary. Display labels may
change; these identifiers and versions may not change in place.

| Contract | Schema or namespace | Version |
| --- | --- | ---: |
| Component descriptor | `ledgrid.component-descriptor` | 1 |
| Scene state | `ledgrid.scene-state` | 1 |
| Desired display state | `ledgrid.desired-display-state` | 1 |
| Vibe state | `ledgrid.vibe-state` | 1 |
| Vibe profile | `ledgrid.vibe-profile` | 1 |
| Animation runtime context | `ledgrid.animation-runtime-context` | 1 |
| Base/overlay frame contract | `ledgrid.layer-frame` | 1 |
| Pipeline golden vectors | `ledgrid.animation-pipeline-golden` | 1 |
| Rollout flags | `ledgrid.animation-pipeline-feature-flags` | 1 |
| Foreground protocol | `ledgrid.foreground-protocol` | 1 |
| Receiver status | `ledgrid.receiver-status` | 3 (reserved) |
| Native background ABI | `ledgrid.native-background-abi` | 2 (reserved) |
| Unsigned native bundle | `ledgrid.native-background-bundle` | 1 (reserved) |
| Installation profile | `ledgrid.installation-profile` | 1 (reserved) |

Reserved contracts are deliberately not accepted by current runtime code.
Receiver status v2 remains the only live receiver status contract in Phase 1.

## Component and presentation state

Version 1 has one component per plugin package. The provider and role
vocabularies are independent:

- providers: `python`, `receiver_native`;
- roles: `background`, `overlay`, `full_scene`;
- timing adapters: `legacy_speed_param`, `scaled_context`, `wall_clock`;
- cadence modes: `fixed_fps`, `event_driven`;
- placement clip policy: `clip_to_wall`;
- foreground stale policies: `clear_after_lease`, `hold`.

Existing plugins are classified in
[animation-plugin-compatibility-inventory.md](animation-plugin-compatibility-inventory.md).
The compatibility adapter will treat ordinary `AnimationBase` packages as
Python backgrounds, retain the existing Clock as a full scene, and reject any
direct-hardware or `StatefulAnimationBase` package from composition until it is
converted explicitly.

The versioned desired display shape remains the one defined in the roadmap:
scene, vibe, plant modifiers, installation-profile digest, and output state are
separate. A selected preset keeps both its identity/fingerprint and a canonical
resolved parameter snapshot. Preset drift restores that snapshot and marks the
selection dirty rather than silently changing output.

Authored parameters are immutable presentation input. For legacy plugins, a
compatibility adapter may construct an ephemeral effective parameter view for
one render call:

```text
effective speed = authored speed * vibe tempo * operator tempo
```

That view is never passed to ordinary live-parameter mutation or persistence.
`scaled_context` receives scaled elapsed time and does not multiply authored
speed again. `wall_clock` receives wall/unscaled time and ignores tempo.

## Time, cadence, and epoch

`next_deadline_scene_time` means an absolute, unscaled number of seconds since
the current scene epoch. It is neither wall time nor a relative delay. Absolute
deadlines prevent cumulative cadence drift and have the same meaning in Python
previews and receiver renderers.

The controller samples its monotonic clock once at scene creation. The opaque
`scene_epoch` is that unsigned 64-bit monotonic-nanosecond sample. Serialized
scheduled times are unsigned 64-bit microseconds since that epoch; host runtime
contexts expose the equivalent seconds as a finite non-negative value.
Controller-session, scene-revision, and foreground-generation counters are
unsigned 64-bit monotonic values and must never wrap. A controller process owns
an opaque 128-bit random session ID.

Version 1 does not claim strict receiver v-sync. Physical acceptance requires:

- scheduled foreground first-to-last visible skew below 5 ms, one accepted
  200 Hz display period;
- analytic-background phase drift no greater than one 60 Hz source period
  (16,667 microseconds) over 30 minutes;
- recorded per-board scheduled time, actual first frame, skew, and drift.

Failure of either bound keeps hybrid playback experimental and leaves complete
host frames as the accepted path.

## Frame, alpha, opacity, and coordinate rules

`BaseFrame` is contiguous `uint8 (total_leds, 3)` RGB. `OverlayFrame` is
contiguous premultiplied `uint8 (total_leds, 4)` RGBA. Every RGB channel in an
overlay must be less than or equal to alpha. Black and transparency are
independent: `(0,0,0,255)` is opaque black and `(0,0,0,0)` contributes nothing.

Opacity is an unsigned byte. Every scale and source-over fold uses:

```text
scale_u8(value, factor) = (value * factor + 127) // 255
out_rgb = source_rgb + scale_u8(destination_rgb, 255 - source_alpha)
```

Channels saturate at 255. Logical overlays fold bottom to top and round after
every fold. Reordering two overlapping overlays is therefore allowed to produce
a different exact byte result. Neither component input buffer may be mutated.

Coordinates are global logical strip-major coordinates:

```text
flat_index = strip * leds_per_strip + led
```

The installed wall is 32×138. Receivers own global strip offsets 0, 8, 16, and
24, each with 1,104 local pixels. A receiver-local index is
`(global_strip - strip_offset) * 138 + led`. Canvas coordinates and wiring/color
transforms do not cross this boundary.

Dirty ranges are sorted, non-overlapping, half-open ranges. Movement/removal
uses the union of old and new coverage. A complete clear covers every formerly
covered pixel even when the new overlay contains no nonzero alpha.

## Dormant foreground wire contract

All integers are big-endian. Every foreground command carries protocol version
1 and a trailing CRC-16/CCITT-FALSE. The transaction ceiling remains 4,096
bytes. These IDs are reserved and are not dispatched in Phase 1:

| Command | ID | Exact bytes before CRC |
| --- | ---: | ---: |
| `CONTROLLER_SESSION_BEGIN` | `0x20` | 58 |
| `OVERLAY_BEGIN` | `0x30` | 66 |
| `OVERLAY_PATCH` | `0x31` | 30-byte header plus RGBA |
| `OVERLAY_COMMIT` | `0x32` | 50 |
| `OVERLAY_CLEAR` | `0x33` | 34 |
| `OVERLAY_RENEW` | `0x34` | 30 |

Widths are fixed: session ID 16 bytes; generation, prior generation, scene
revision, scene epoch, base revision, and presentation time `u64`; lease
milliseconds `u32`; patch start/count and expected-patch count `u16`; format
and update-kind `u8`.

The patch limit is exact:

```text
max_rgba_pixels = floor((4096 - 30 - 2) / 4) = 1016
```

An installed receiver's 1,104-pixel full foreground snapshot is therefore two
ascending patches: `[0, 1016)` and `[1016, 1104)`. Patches are contiguous or
strictly ascending and non-overlapping as declared by the update kind. An exact
retry of the latest accepted patch is idempotent; a byte-conflicting retry,
overlap, gap where a full snapshot requires continuity, or out-of-order patch is
rejected. Equal begin/commit operations are idempotent only when their complete
operation digest is identical. Lower revisions/generations are stale, and
`prior_generation` is a compare-and-swap precondition.

The frozen rejection vocabulary distinguishes unsupported version/format,
bounds/size, stale session/revision/generation, failed compare-and-swap,
ordering/overlap/conflict, base binding, incomplete staging, expired lease, and
invalid state. Later implementations must publish the exact result rather than
collapse these cases into a generic failure.

## Display ownership and command effects

The future state is orthogonal:

- base: `StartupFallback`, `LocalBackground`, `HostFullScene`;
- foreground: `Cleared`, `Staging`, `Active`;
- maintenance: `Inactive`, `AssetTransfer`, `CalibrationTransfer`.

Only an explicit local-background start or a validated complete host frame may
change base ownership. The current implementation's implicit first-command
takeover is not part of the frozen contract.

| Command/event | Base | Foreground | Maintenance/output |
| --- | --- | --- | --- |
| Boot/reset | `StartupFallback` | `Cleared` | `Inactive` |
| Complete host `SET_ALL` | `HostFullScene` | hidden/cleared | unchanged |
| Explicit local-background start | `LocalBackground` | cleared unless exact binding staged | unchanged |
| Explicit local-background stop | `StartupFallback` | cleared | unchanged |
| `SET_PIXEL`, `SET_RANGE`, `SHOW`, `CLEAR` | unchanged | unchanged | modify only an already-owned host mailbox/output |
| PING/status/config/brightness | unchanged | unchanged | status/config/output only |
| Session begin | unchanged | discard staging; committed state follows its lease | unchanged |
| Presentation-context stage/commit | unchanged | unchanged | presentation context only |
| Overlay begin/patch | unchanged | `Staging`; prior active remains visible | unchanged |
| Valid overlay commit | unchanged | `Active` | unchanged |
| Overlay clear or lease expiry | unchanged | `Cleared` | unchanged |
| Asset/calibration begin | unchanged | unchanged | matching maintenance state |
| Asset/calibration finish/failure | unchanged | unchanged | `Inactive` |

Failure recovery is equally explicit:

| Failure | Required result |
| --- | --- |
| Invalid/interrupted foreground generation | Reject staging; retain prior committed foreground until clear/lease |
| Receiver restart | Compiled startup fallback, cleared foreground, inactive maintenance |
| Pi disconnect during local background | Continue healthy base; expire time-sensitive foreground by lease |
| Native start/load/context/render/cleanup failure | Attribute failure, quarantine when applicable, return to fallback, clear invalid binding |
| Maintenance transfer failure | Remove partial visibility, return maintenance inactive, retain prior active display |
| Mixed four-board stage | Do not report success or activate a healthy mixed scene; replay or compensate |
| Complete host frame | Reclaim host ownership and bypass/clear receiver foreground |

## Lease defaults

Lease intervals are part of overlay intent, not a universal magic timeout:

| Overlay class | Stale policy | Lease | Normal renewal |
| --- | --- | ---: | ---: |
| Clock/time-sensitive HUD | `clear_after_lease` | 3,000 ms | 1,000 ms |
| Alert/status HUD | `clear_after_lease` | 15,000 ms | 5,000 ms |
| Decorative layer | `hold` | none | none |

The controller republishes a full foreground snapshot before deltas after a new
session, restart, or receiver replacement.

## Frozen pilot choices

These choices close Phase 1 selection ambiguity without enabling behavior:

- stable vibe IDs: `neutral`, `quiet`, `cozy`, `vivid`, `celebration`;
- Clock is the `wall_clock`/HUD pilot;
- `aurora_curtains` is the procedural-atmosphere vibe pilot;
- `snake` is the seeded semantic/game parity pilot;
- `world_flags` is the preserve-color pilot;
- `aurora_curtains_native` is the analytic receiver-native build pilot;
- `hue_shift` is the first stateless host/receiver installation-transform
  parity pilot.

The initial profile contract targets are:

| Vibe | Tempo | Luminance | Visual intent |
| --- | ---: | ---: | --- |
| `neutral` | 1.00 | 1.00 | Authored palette and motion |
| `quiet` | 0.65 | 0.55 | Restrained chroma and contrast |
| `cozy` | 0.85 | 0.75 | Warm primary/secondary roles |
| `vivid` | 1.15 | 0.95 | Saturated, higher-contrast roles |
| `celebration` | 1.35 | 1.00 | Broad accent use and high energy |

Exact semantic-role RGB bytes and optional 256-entry ramps are Phase 2A data;
they must be versioned and hashed, and may not change these IDs' intent in
place.

## Rollout flags and Phase 1 no-op guarantee

The complete flag set is:

- `vibe_context`;
- `scene_layers`;
- `receiver_local_background`;
- `receiver_sparse_overlay`;
- `receiver_geometry_profile`;
- `receiver_native_modules`.

All default to `false`. Unknown names and non-boolean values are rejected. No
Phase 1 runtime path reads these flags, so adding the contract cannot alter
frames, manager timing, API/status JSON, persisted snapshots, SPI commands, or
receiver ownership. Each future phase must integrate only its own flag together
with that phase's migration and rollback tests.
