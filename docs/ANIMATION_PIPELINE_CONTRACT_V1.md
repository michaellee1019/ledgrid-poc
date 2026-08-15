# Animation Pipeline Contract v1

## Scope and authority

This document freezes the names, bytes, state boundaries, and rollout gates
needed by Phase 1 and activated incrementally through the bounded host-library,
host-context, and receiver-decoder prerequisite slices of Phase 3C of
[plan-revamped-animation-pipeline.md](plan-revamped-animation-pipeline.md).
The scene/provider rollout flags remain off, while Phase 3A activates explicit
receiver ownership and status v3. Ordinary production firmware keeps the
statically linked local-background capability disabled until its one-receiver
canary; complete host frames remain the accepted wall path.

Machine-readable reference vectors live in
`tests/fixtures/animation_pipeline_v1.json`. Phase 3A receiver-presentation
vectors live in `tests/fixtures/receiver_presentation_v1.json`. Python and
portable C++ tests must consume or reproduce those exact values before the
corresponding behavior is activated.

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
| Receiver presentation context | `ledgrid.receiver-presentation-golden` | 1 |
| Receiver status | `ledgrid.receiver-status` | 4 (v3-compatible prefix) |
| Native background ABI | `ledgrid.native-background-abi` | 2 (reserved) |
| Unsigned native bundle | `ledgrid.native-background-bundle` | 1 (reserved) |
| Installation profile | `ledgrid.installation-profile` | 1 (portable receiver decode; transport/runtime reserved) |

Contracts marked runtime-reserved are deliberately not accepted by current
receiver runtime code. The installation-profile v1 bytes are frozen for the
portable compiler, Python decoder, topology slicer, Pi-authoritative managed
library, read-only host views, transport-neutral four-receiver transaction
engine and fake, and a bounds-checked C++ receiver decoder/read-only view. There
are no real profile transfer, activation, status, persistent receiver-cache, or
receiver-optics commands in this phase.
Status v4 is negotiated only after a legacy-safe v3 query exposes sparse
foreground support. Its first 320 bytes preserve status v3 exactly apart from
the `LGS4` magic/version. Status v3 preserves every v2 counter offset in its
first 64 bytes but uses `LGS3`; compatibility is intentionally
new-host-to-old-firmware. An old host that accepts only `LGS2` is not compatible
with new firmware.

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

## Phase 3A receiver ownership and status v3

The live base states are `StartupFallback=0`, `LocalBackground=1`, and
`HostFullScene=2`. Foreground is `Cleared=0` in this phase and maintenance is
`Inactive=0`. `PING`, `CONFIG`, `STATUS_QUERY`, brightness, partial RGB,
`SHOW`, `CLEAR`, and presentation-context traffic never claim the base. Only an
accepted complete `SET_ALL` or `LOCAL_BACKGROUND_START` can do so. With the
local feature disabled, local/context commands return `Unsupported`; SET_ALL
retains the same host takeover path.

Local command bytes, before the shared trailing CRC-16, are:

| Command | ID | Exact fields after ID | Exact bytes |
| --- | ---: | --- | ---: |
| `LOCAL_BACKGROUND_START` | `0x10` | component `u16`, cadence Hz `u16`, global strip offset `u32`, common seed `u32`, scene epoch `u64` | 21 |
| `LOCAL_BACKGROUND_STOP` | `0x11` | none | 1 |
| `LOCAL_BACKGROUND_PARAMETERS` | `0x12` | cadence Hz `u16`, global strip offset `u32`, common seed `u32` | 11 |

Component `1` is the compiled rainbow; cadence is 1–200 Hz. START requires an
active context with the same scene epoch. CONFIG remains exactly four or five
bytes for legacy callers; its six-byte form appends a logical receiver ID 0–3.
Unprovisioned identity is `0xff`, and local commands fail closed until the host
has capability-gated and verified that identity.

`STATUS_QUERY` is ID `0x08` followed by 319 zero bytes. The response is the
following exact 320-byte, big-endian `LGS3` snapshot:

| Offset | Bytes | Field |
| ---: | ---: | --- |
| 0 | 4 | magic `LGS3` |
| 4 | 1 | version `3` |
| 5 | 59 | complete status-v2 fields at their original offsets |
| 64 | 4 | capability bits |
| 68 | 6 | base, foreground, maintenance, transition reason, result, context state |
| 74 | 6 | component ID, cadence Hz, luminance Q8.8 (`u16` each) |
| 80 | 8 | global strip offset and common seed (`u32` each) |
| 88 | 32 | scene epoch and active scene/vibe/modifier revisions (`u64` each) |
| 120 | 16 | cadence deadlines, rendered frames, misses (`u32`); last/max render (`u16`) |
| 136 | 8 | last rendered scene time in microseconds |
| 144 | 96 | active context, vibe, and modifier SHA-256 digests |
| 240 | 40 | staged scene revision and staged context digest |
| 280 | 32 | active and staged controller session IDs |
| 312 | 1 | logical receiver ID |
| 313 | 1 | last processed non-query command ID |
| 314 | 2 | reserved zero |
| 316 | 4 | operation sequence |

Capability bits are static local background `1<<0`, presentation context v1
`1<<1`, status v3 `1<<2`, and explicit ownership `1<<3`. The ordinary image
advertises status/ownership only; the named canary image also advertises the
local/context bits.

Every CRC-valid dispatched non-status command advances the nonwrapping
operation sequence exactly once and records its command/result. CRC failures
and STATUS_QUERY do not advance it. Because the ESP32 keeps two SPI response
buffers queued, the host serializes transfers, drains twice before and after a
command, and accepts an acknowledgement only when both command ID and the next
operation sequence match.

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

## Phase 3C portable installation-profile contract

The canonical profile is compiled globally before receiver slicing. Its payload
never contains SPI routes, logical-to-physical receiver assignment, host-frame
strip reversal, or receiver-native strip reversal. A separate topology adapter
applies the physical lane permutation and native local-strip direction exactly
once when it creates receiver views. Transport routes and host/native directions
remain independently named even when two installed values happen to match.

Version 1 is a bounded big-endian binary with a 112-byte fixed header followed
by nine 24-byte section entries and contiguous section payloads. The maximum
complete profile is 65,535 bytes. Reserved bytes and flag bits are zero. Header
fields are:

| Offset | Bytes | Field |
| ---: | ---: | --- |
| 0 | 4 | magic `LGIP` |
| 4 | 2 | format version `1` |
| 6 | 2 | fixed-header bytes `112` |
| 8 | 4 | flags; bit 0 means local strip payload order is reversed |
| 12 | 2 | canonical global strip count |
| 14 | 2 | LEDs per strip |
| 16 | 2 | first physical global strip represented by this view |
| 18 | 2 | represented strip count |
| 20 | 4 | represented pixel count, exactly `strip_count * leds_per_strip` |
| 24 | 1 | 8-neighbor clearance radius, range 0 through 4 |
| 25 | 1 | stable globe-region vocabulary count, exactly 7 |
| 26 | 2 | section count, exactly 9 |
| 28 | 2 | section-entry bytes, exactly 24 |
| 30 | 2 | reserved zero |
| 32 | 4 | complete profile bytes |
| 36 | 32 | canonical calibration-input SHA-256 |
| 68 | 32 | profile content SHA-256 |
| 100 | 12 | reserved zero |

Each section entry is `(id:u16, encoding:u8, element_width:u8,
element_count:u32, offset:u32, length:u32, crc32:u32, reserved:u32)`. Entries
use the exact ascending ID order below, have one byte per represented pixel,
start immediately after the complete header/table, and are contiguous without
gaps or overlap. Encoding IDs are `1=unsigned enum byte`, `2=unsigned boolean
byte`, `3=unsigned byte`, and `4=signed byte`; `element_width` is exactly `1`
for every v1 section.

| ID | Name | Encoding | Values |
| ---: | --- | --- | --- |
| 1 | `category` | unsigned enum byte | `0=empty`, `1=foliage`, `2=globe` |
| 2 | `clearance` | unsigned boolean byte | `0` or `1` |
| 3 | `foliage_edge` | unsigned boolean byte | `0` or `1` |
| 4 | `globe_edge` | unsigned boolean byte | `0` or `1` |
| 5 | `obstacle_edge` | unsigned boolean byte | `0` or `1` |
| 6 | `globe_region` | unsigned enum byte | `0` or stable region ID `1..7` |
| 7 | `distance` | unsigned byte | exact 8-neighbor/Chebyshev obstacle distance |
| 8 | `normal_x` | signed byte | normalized strip-axis gradient in signed Q0.7 |
| 9 | `normal_y` | signed byte | normalized LED-axis gradient in signed Q0.7 |

The stable one-based region order is `top_left`, `top_right`, `upper_middle`,
`middle_left`, `middle_right`, `lower_left`, `lower_right`. Every region byte is
zero outside exact globe pixels. Globes take category precedence if foliage and
globe evidence overlap. Region overlap, a globe without exactly one known
region, or a region pixel outside the globe category is invalid.

Clearance is derived globally by applying the recorded number of non-wrapping
8-neighbor dilations to `foliage | globe`. Edge fields are non-wrapping
four-neighbor inner edges. Distance is the exact integer expansion step from the
combined obstacle. Normals are the normalized NumPy-compatible one-sided/central
gradient of that distance with axis 0 as global strips and axis 1 as LEDs;
finite values are quantized as `sign(value) * floor(abs(value) * 127 + 0.5)`.
These globally derived bytes are sliced without recomputation so receiver
boundaries cannot introduce seams.

The calibration digest is SHA-256 over a canonical compact JSON object containing
the four role-named parsed calibration inputs (`foliage`, `globes`, `regions`,
and measured `wall`). Object keys are sorted; array order is preserved and must
already satisfy each input's canonical ascending or stable-region order. The
content digest is SHA-256 of the complete profile with bytes 68 through 99
replaced by zero. Each payload uses standard IEEE CRC-32. A decoder validates
all sizes, offsets, encodings, counts, reserved values, CRCs, digests, category
and region bounds before exposing a view.

The global golden has origin 0, all 32 strips, and canonical ascending strip
order. A receiver view has eight strips and its physical lane origin; bit 0 is
set only when its payload rows are stored in descending physical strip order.
Reassembly uses the origin and flag to recover canonical global order.

The portable C++ receiver decoder accepts only the exact 10,264-byte
eight-strip view for an explicitly expected aligned origin and strip direction.
Before it exposes non-owning const section pointers, it validates the complete
frozen header/table, reserved bytes, content digest, every section CRC and
bound, enum and fixed-point ranges, category/region membership, obstacle
containment in clearance, all edge-subset invariants, and the exact equivalence
between zero distance and obstacle membership. Failure clears the output view.

The portable Phase 3C stop boundary still forbids real receiver staging,
activation, receiver status, persistent firmware storage, optics, and wall
mutation. The transaction engine and C++ decoder are acceptance/runtime
prerequisites only and perform no transport or display behavior.

### Phase 3C host-library and fake-transaction contract

The Pi-authoritative target-owned root is `installation_profile_library/`, kept
outside immutable app releases and protected from full-sync deletion. Canonical
global artifacts publish atomically at
`profiles/<content-digest>/profile.bin`; the sibling `receipt.json` contains
exactly schema version, profile-format version, ID, embedded content digest,
calibration digest, ordinary file SHA-256, byte size, and UTC publication time.
The timestamp records the event but never participates in identity. An existing
missing, conflicting, or corrupt entry fails closed rather than being repaired
implicitly. Identical publication returns the original receipt.

Managed IDs are exactly 64 lowercase hexadecimal characters. Resolution
revalidates immutable artifact bytes and receipt metadata before exposing the
canonical global profile and four immutable receiver views. Receiver-view cache
identity contains the global content digest, physical lane order, and native
strip direction. Transport routes and host-frame strip direction remain named
but do not change profile semantics or receiver bytes.

The portable transaction engine binds one global profile ID to four
receiver-specific payload SHA-256 values and operates through a small
transport-neutral receiver interface. Its lifecycle is deterministic
`preflight`, `stage`, `verify`, `commit`, and failure compensation. Capacity
plus reserve is checked on all four targets before mutation; active, rollback,
and staged payloads are pinned; only inactive payloads may be evicted in
least-recently-used order. A partial operation attempts compensation on every
receiver and reports compensation only after exact staged/active/rollback
snapshots and backing payload validity are re-proven. Timeout and operational
adapter failures enter that same path; incomplete or unprovable recovery is
reported degraded rather than healthy. Fake status may report `healthy`,
`no_active`, `mixed_generation`, or `degraded`; mixed or corrupt state cannot
start another transaction or be reported healthy. This vocabulary reserves no
command IDs, wire bytes, receiver storage layout, or runtime status fields.

### Phase 3C host runtime and preview selection contract

The host owns one installation-profile selection independently of scene, vibe,
plant modifiers, and output state. The explicit compatibility selection is the
64-character all-zero digest. It resolves to no managed view, retains the
legacy JSON-backed `PlantMaskGeometry` path, and must not create or mutate the
managed-library filesystem. Every nonzero selection is a lowercase SHA-256
content digest and resolves only through the Pi-authoritative managed library.

Resolution, artifact validation, topology slicing, 32x138 controller-geometry
validation, and immutable runtime-view construction all complete before the
active selection changes. A rejection leaves the prior digest, selection
revision, runtime view, scene components, preview session, authored parameters,
and output state unchanged. Re-selecting the active digest is idempotent and
does not advance the selection or presentation revision.

The global Python runtime view contains compact profile/calibration/version/
geometry identity, the separately named topology, and one immutable
`PlantMaskGeometry`. All logical, flat, edge, distance, normal, and seven
ordered region arrays are non-writeable. Runtime contexts pass that view by
reference to single animations, fixed-scene components, receiver-hybrid Python
foregrounds, ordinary previews, and scene previews; they do not copy or
serialize the 32x138 arrays per frame. The managed category, clearance, edge,
distance, and region fields reproduce the portable artifact exactly. Signed
Q0.7 normals are dequantized once to Python float fields at this boundary. An
explicit `get_plant_masks(radius)` call derives only a non-writeable clearance
layer from the frozen global distance field, caches it, and shares every other
managed array; the default call returns the artifact's recorded radius exactly.

Presentation identity includes the profile and calibration digests, format and
geometry, physical lane order, and native receiver direction. Transport routes
and host-frame direction remain status-visible but are deliberately excluded:
they do not change global host geometry or receiver-profile bytes. A semantic
profile/topology change invalidates base and plugin-owned presentation/geometry
caches and future plans without replacing components, changing authored
parameters, advancing simulation clocks, or consuming RNG. It forces the next
host result dirty, but emits no receiver profile/context command and performs no
receiver staging or activation.

Persisted desired display state restores the profile as part of aggregate
validation. Startup preflights any nonzero saved digest before controller or
animation construction. Aggregate restore selects the validated profile before
scene start so the first frame sees the correct geometry, and restores the prior
selection if scene start rejects. The web preview process uses its own manager
against the same target-owned library root, follows the live status digest, and
retains its last valid view while reporting a rejected synchronization. There is
no installation-profile mutator in the dashboard or runtime IPC command set in
this slice.

Dirty ranges are sorted, non-overlapping, half-open ranges. Movement/removal
uses the union of old and new coverage. A complete clear covers every formerly
covered pixel even when the new overlay contains no nonzero alpha.

## Phase 3A staged presentation-context wire contract

The Pi is authoritative for the resolved presentation context. A receiver does
not look up a vibe by ID, infer a plant-modifier default, or receive calibrated
plant geometry through this contract. It stages the exact resolved values from
the host and activates them only after a matching commit.

All integers are unsigned big-endian. Each command starts with its command ID
and protocol version `1`. The byte counts below include that two-byte prefix but
exclude the transport's trailing CRC-16/CCITT-FALSE. The context serializer
returns pre-CRC bytes; the existing SPI transport adds and verifies the CRC.

| Command | ID | Exact bytes before CRC |
| --- | ---: | ---: |
| `PRESENTATION_CONTEXT_BEGIN` | `0x21` | 58 |
| `PRESENTATION_CONTEXT_SET` | `0x22` | `145 + 3 × modifier_count` (maximum 187) |
| `PRESENTATION_CONTEXT_COMMIT` | `0x23` | 74 |

`PRESENTATION_CONTEXT_BEGIN` freezes one expected staged body:

| Offset | Field | Encoding |
| ---: | --- | --- |
| 0 | command | `u8 = 0x21` |
| 1 | version | `u8 = 1` |
| 2 | controller session ID | opaque `bytes[16]` |
| 18 | scene revision | `u64` |
| 26 | expected context digest | opaque SHA-256 `bytes[32]` |

`PRESENTATION_CONTEXT_SET` carries the complete context. The eight palette
roles are packed as RGB8 in this fixed order: `background_low`,
`background_mid`, `background_high`, `primary`, `secondary`, `accent`, `hud`,
`warning`.

| Offset | Field | Encoding |
| ---: | --- | --- |
| 0 | command | `u8 = 0x22` |
| 1 | version | `u8 = 1` |
| 2 | controller session ID | opaque `bytes[16]` |
| 18 | scene revision | `u64` |
| 26 | canonical vibe ID | `u8` |
| 27 | vibe profile version | `u32` |
| 31 | vibe revision | `u64` |
| 39 | resolved vibe-profile digest | opaque SHA-256 `bytes[32]` |
| 71 | resolved palette | eight canonical RGB8 roles, `bytes[24]` |
| 95 | resolved tempo scale | unsigned Q8.8 `u16` |
| 97 | resolved luminance scale | unsigned Q8.8 `u16`, range 0 through 256 |
| 99 | resolved chroma scale | unsigned Q8.8 `u16` |
| 101 | resolved energy | unsigned Q8.8 `u16` |
| 103 | plant-modifier state version | `u8 = 1` |
| 104 | plant-modifier revision | `u64` |
| 112 | resolved plant-modifier digest | opaque SHA-256 `bytes[32]` |
| 144 | modifier count | `u8`, maximum 14 |
| 145 | canonical modifier entries | repeated `(modifier_id:u8, strength_q8_8:u16)` |

Vibe IDs have fixed numeric values: `neutral=1`, `quiet=2`, `cozy=3`,
`vivid=4`, and `celebration=5`. Plant modifier numeric values are one-based in
the frozen `PLANT_MODIFIER_IDS` order:

```text
illuminate, shadow, refract, hue_shift, liquid_glass,
attractor, repulsor, slow_zone, obstacle, portal, bumper,
hazard, habitat, emitter
```

Every active modifier appears once, in that order, with an explicit resolved
strength; SET never asks firmware to supply a default. The modifier digest is:

```text
SHA256(state_version:u8 || modifier_count:u8 || canonical_modifier_entries)
```

The context digest in BEGIN and COMMIT is:

```text
SHA256(SET bytes from offset 18 through the final modifier entry)
```

It therefore binds the scene revision, resolved vibe identity/version/revision,
resolved vibe digest, exact palette and scalar bytes, plant state
version/revision/digest, and every modifier entry. It deliberately excludes the
command/version, controller session, scene epoch, and scheduled commit time.
BEGIN and SET independently bind the staging operation to the session; COMMIT
binds the same digest to its schedule. Equal revisions are idempotent only when
the complete digest also matches.

`PRESENTATION_CONTEXT_COMMIT` activates only the matching staged body:

| Offset | Field | Encoding |
| ---: | --- | --- |
| 0 | command | `u8 = 0x23` |
| 1 | version | `u8 = 1` |
| 2 | controller session ID | opaque `bytes[16]` |
| 18 | scene revision | `u64` |
| 26 | scene epoch | `u64` |
| 34 | presentation time since scene epoch | `u64` microseconds |
| 42 | context digest | opaque SHA-256 `bytes[32]` |

Sequential board commits compensate transport time from one host monotonic
anchor. If the requested base scene time is `S0` at host time `T0`, the host
drains that board's two queued acknowledgements, samples `Ti`, and serializes
`S0 + (Ti - T0)` in microseconds. The receiver advances from that value using
elapsed time since its local command receipt. Thus every board estimates the
same scene time at a later real instant, apart from the bounded command-transfer
interval; portable acceptance requires the resulting scene/pixel skew to remain
at or below 5 ms. The host caches only the latest adjusted COMMIT per receiver,
keyed by session, scene revision, and context digest, so an active retry is
byte-identical without retaining one schedule per historical scene. Compensation
does not alter the context digest.

### Fixed-point and luminance rule

All resolved presentation scalars use unsigned Q8.8. A finite non-negative
host scalar is quantized with round-half-up:

```text
q8_8 = floor(value * 256 + 0.5)
```

Values that exceed `u16`, negative or non-finite values, booleans, unknown IDs
or fields, incomplete/reordered palette roles, duplicate/out-of-order modifier
entries, noncanonical plant combinations, and counters outside `u64` are
rejected rather than clamped or ignored. Luminance and plant strength are
additionally bounded to `[0, 256]` after quantization.

Receiver-local RGB luminance is applied exactly once, after the local renderer
has produced its authored RGB and before the separate physical master-
brightness/output step:

```text
presented_u8 = min(255, (authored_u8 * luminance_q8_8 + 128) // 256)
```

Thus luminance zero is exact black, `128` rounds one-half upward, and unity
`256` is an exact byte-for-byte no-op. The host must not pre-apply vibe
luminance to receiver-local content, and firmware must not apply it once per
layer or again at display output. The shared fixture includes zero/unity
endpoints, both sides of the half-up boundary, current profile quantization,
multiple vibes, multiple modifier sets, high counter values, exact packet
bytes, and all three digests.

## Feature-gated foreground wire contract

All integers are big-endian. Every foreground command carries protocol version
1 and a trailing CRC-16/CCITT-FALSE. The transaction ceiling remains 4,096
bytes. These IDs were reserved but not dispatched in Phase 1; the Phase 3B0
portable receiver now accepts them only in the deliberate feature-on canary
build:

| Command | ID | Exact bytes before CRC |
| --- | ---: | ---: |
| `CONTROLLER_SESSION_BEGIN` | `0x20` | 58 |
| `OVERLAY_BEGIN` | `0x30` | 66 |
| `OVERLAY_PATCH` | `0x31` | 30-byte header plus RGBA |
| `OVERLAY_COMMIT` | `0x32` | 50 |
| `OVERLAY_CLEAR` | `0x33` | 34 |
| `OVERLAY_RENEW` | `0x34` | 30 |
| `OVERLAY_PATCH_BATCH` | `0x35` | 28-byte fixed header plus span entries |

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

`OVERLAY_PATCH_BATCH` is the smallest backward-compatible extension: receivers
advertise `sparse_overlay_batch_v1 = 1<<5` in addition to the original
`sparse_overlay_v1 = 1<<4`; without both bits the host sends the unchanged
single-span `OVERLAY_PATCH`. The batch packet has this exact shape before the
shared trailing CRC:

| Offset | Field | Encoding |
| ---: | --- | --- |
| 0 | command | `u8 = 0x35` |
| 1 | version | `u8 = 1` |
| 2 | controller session | opaque `bytes[16]` |
| 18 | foreground generation | `u64` |
| 26 | span count | `u16`, at least one |
| 28 | span entries | repeated `start:u16, count:u16, rgba:bytes[count*4]` |

Each span count is nonzero, every RGBA body is premultiplied, and entries are
sorted, non-overlapping, and in receiver-local bounds. Full snapshots are also
contiguous from local pixel zero across packet boundaries. Including the
28-byte fixed header, four bytes per span descriptor, and the two-byte CRC, the
exact capacity rule is:

```text
sum(pixel_counts) + span_count <= 1016
```

Consequently one batch can contain at most 1,015 pixels in one span or 508
one-pixel spans. The largest legal batch is 4,094 bytes; the two unused bytes at
the 4,096-byte ceiling are unavoidable because both descriptors and pixels are
four-byte units. A canonical batch-mode 1,104-pixel snapshot uses logical spans
`[0, 1015)` and `[1015, 1104)` in two packets.

`OVERLAY_BEGIN.expected_patches` and status-v4 `accepted_patches` count logical
spans, not SPI packets: a legacy patch contributes one and a batch contributes
its declared span count. The receiver validates the complete CRC-bound batch
before mutating staging. A byte-exact retry of the latest accepted whole batch
returns `Idempotent` without reapplying pixels or incrementing accepted spans;
a same-position/count retry with any byte conflict returns `PatchConflict`.
Unsorted, overlapping, out-of-bounds, truncated, over-capacity, and
non-premultiplied batches retain the existing exact rejection vocabulary.

Batching amortizes status traffic without weakening acknowledgement proof. One
accepted batch advances the nonwrapping operation sequence once. After draining
the real two-deep response queue, the host accepts the batch only when status v4
reports command `0x35`, exactly the next operation sequence, and `Ok` or
`Idempotent`; the CRC binds every descriptor and RGBA byte to that one result.
Status v4 remains exactly 416 bytes and retains its existing offsets:

| Offset | Bytes | Foreground proof field |
| ---: | ---: | --- |
| 320 | 1 | overlay operation result |
| 321 | 1 | update kind |
| 322 | 2 | expected logical spans |
| 324 | 2 | accepted logical spans |
| 326 | 2 | committed coverage pixels |
| 328 | 8 | committed generation |
| 336 | 8 | staged generation |
| 344 | 40 | scene revision/epoch, base revision, scheduled time, lease |
| 384 | 16 | controller session ID |
| 400 | 16 | compositor/commit/expiration counters |

`expected_patches=0` is valid only for a `Delta` update. Its commit advances the
common foreground generation without changing pixels or coverage, allowing an
unaffected receiver to agree with the other receivers in one wall transaction.
A zero-patch `FullSnapshot` remains incomplete. The delta no-op commit is still
subject to the normal session, scene-revision, prior-generation, base-binding,
lease, and scheduled-presentation checks; it is not an ordering bypass.

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
