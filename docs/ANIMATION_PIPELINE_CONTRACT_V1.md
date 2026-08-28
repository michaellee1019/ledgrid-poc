# Animation Pipeline Contract v1

## Scope and authority

This document freezes the names, bytes, state boundaries, and rollout gates
needed by Phase 1 and activated incrementally through the bounded host-library,
host-context, receiver-decoder prerequisites, trusted native authoring/build
slice of Phase 3D, and gated native runtime slice of Phase 4 of
[plan-revamped-animation-pipeline.md](plan-revamped-animation-pipeline.md).
All receiver-local and managed-native rollout flags remain off in ordinary
production. Explicit local and native canaries activate only their named bounded
contracts; complete host frames remain the accepted wall path until H0-H4 pass.

Machine-readable reference vectors live in
`tests/fixtures/animation_pipeline_v1.json`. Phase 3A receiver-presentation
vectors live in `tests/fixtures/receiver_presentation_v1.json`. Python and
portable C++ tests must consume or reproduce those exact values before the
corresponding behavior is activated.

### Finalized topology amendment (updated 2026-08-27)

The installed geometry is now 33×138 across five receivers with logical widths
`(8,8,8,8,1)`. Transport routes are `0→0.0`, `1→0.1`, `2→1.1`, `3→1.0`, and
`4→1.2`; physical left-to-right logical order is `(0,1,2,3,4)` and native
global offsets by logical ID are `(0,8,16,24,32)`. Host reversal flags by
logical ID are `(false,false,false,false,false)`; independently, native reversal
flags remain `(false,false,true,true,false)` pending their native phase-field
test. Output masks are
`(0xff,0xff,0xff,0xff,0xff)`. The last mask broadcasts one compact semantic
strip across the dedicated receiver's physical outputs. This supersedes the
frozen 32×138/four-uniform-receiver operating assumption below while retaining
old fixtures as historical evidence at their measured geometry.

No schema is reinterpreted silently. Dimensions already encoded in profile,
bundle, ABI, and receiver configuration payloads must now carry the new values;
old 32×138 managed artifacts remain content-addressed but are not activatable on
the finalized wall. Exact-roster operations require all five receivers. Decoder,
cache, preview, orchestration, and acceptance code consumes explicit per-receiver
width and offset rather than `receiver_id * 8`. Logical width, transport route,
physical output-lane mask, host strip direction, and receiver-native direction
are independent fields. A fifth receiver may own one logical strip without
claiming that all eight of its output lanes are semantically populated.

The 2026-08-27 five-color Anker-camera view supersedes the former swapped-middle
physical-order assumption after the receiver cables changed. Photo Booth's live
preview was mirrored; the diagnostic's preview order was magenta, blue, yellow,
green, red, establishing logical receivers `(0,1,2,3,4)` from physical left to
right once the preview mirror was accounted for. The fifth column remained at
the right edge. Because the camera showed only part of the wall, this observation
does not accept the wall homography or either within-receiver direction map.
The subsequent unmirrored direct-AVFoundation eight-step ramp qualified the host
map only: physical broad-block correlations were `+0.84`, `+0.94`, `-0.93`, and
`-0.98` under the old map, so the current host map is all-forward. A host painter
frame is not evidence for receiver-native coordinates.

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
| Receiver status | `ledgrid.receiver-status` | 6 (v5-compatible prefix) |
| Native background ABI | `ledgrid.native-background-abi` | 2 |
| Unsigned native bundle | `ledgrid.native-background-bundle` | 1 |
| Installation profile | `ledgrid.installation-profile` | 1 |
| Receiver optic coefficients | `ledgrid.receiver-optics` | 1 |

The native ABI and bundle are accepted by the trusted repository authoring,
host-preview, validation, Pi-library, and feature-gated Phase 4 runtime. Ordinary
production firmware does not load or execute them. The installation-profile v1
bytes are frozen for the
portable compiler, Python decoder, topology slicer, Pi-authoritative managed
library, read-only host views, transport-neutral five-receiver transaction
engine and fake, a bounds-checked C++ receiver decoder/read-only view, and the
default-off receiver-profile staging contract below. Profile staging remains
display-inert. Activation never changes ownership or starts a renderer; after
the Phase 3C optic contract below, an active profile may change only the
post-composition `hue_shift` presentation when a committed context requests a
nonzero strength.
Status v4 is negotiated only after a legacy-safe v3 query exposes sparse
foreground support. Its first 320 bytes preserve status v3 exactly apart from
the `LGS4` magic/version. Status v3 preserves every v2 counter offset in its
first 64 bytes but uses `LGS3`; compatibility is intentionally
new-host-to-old-firmware. Status v5 is likewise requested only after a v3 query
exposes its capability; its first 416 bytes preserve all v4 field offsets apart
from `LGS5` magic/version. An old host that accepts only `LGS2` is not compatible
with new firmware. Status v6 is requested only when the v3 capability report
advertises it. Its 1,216-byte record preserves the complete 768-byte v5 layout
apart from `LGS6` magic/version, then adds native operation/result/watchdog state,
capacity and transfer counters, active/staged/rollback/quarantine identities,
typed-parameter binding, exact active geometry, timing, cache, and lifecycle
counters. Feature-off firmware does not advertise the v6/native capability set.

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

## Phase 3D native authoring and bundle seam

ABI v2 is the C-compatible contract in
`firmware/esp32/include/ledgrid/native_background_abi_v2.h`. The caller owns
aligned module state and the complete receiver-local RGB output buffer. Context,
parameter, vibe, modifier, profile, request, result, and output pointers are
borrowed only for their callback; the helper table/function pointers alone stay
valid from successful initialize through cleanup. A successful first render
must set `changed=1`. Later `changed=0` ignores the supplied output bytes and
retains the previous complete local frame. Changed-frame evidence counts whole
wall frames, not receiver callbacks.

The ABI deadline is an absolute unsigned microsecond count since scene epoch;
the bundle separately records
`absolute_unscaled_microseconds_since_scene_epoch` alongside the generic
descriptor's absolute-seconds semantic. Fixed-FPS results must return a future
deadline no more than one cadence period away. Vibe luminance is bounded Q8.8
`0..256` and is framework-owned: modules that claim `luminance` do not apply it,
because the receiver's post-render presentation pass applies it exactly once.

RGB output is receiver-native local-strip order. Physical origins
`0/8/16/24` map to logical receiver IDs `0/1/3/2`; their native directions are
forward, forward, reverse, reverse. The caller supplies the canonical origin
and `reverse_local_strip_order`; global strip is `origin + local` when forward
and `origin + local_strips - 1 - local` when reversed. Host preview exercises
those four installed views and stitches each strip back by global coordinate.

Frozen modifier IDs `1..14` are `illuminate`, `shadow`, `refract`, `hue_shift`,
`liquid_glass`, `attractor`, `repulsor`, `slow_zone`, `obstacle`, `portal`,
`bumper`, `hazard`, `habitat`, `emitter`. Profile section IDs `1..9` are
`category`, `clearance`, `foliage_edge`, `globe_edge`, `obstacle_edge`,
`globe_region`, `distance`, `normal_x`, `normal_y`; encodings `1..4` are
unsigned-enum, unsigned-boolean, unsigned-byte, signed-byte. Category values are
`0=open`, `1=foliage`, `2=globe`; globe-region `0` is none and `1..7` follow the
stable region order defined below.

An unsigned bundle is one canonical stored ZIP with exactly `manifest.json`,
`payload/module.so`, and `preview/preview.webp`; it contains no signature,
index, or frame track. Inspection re-encodes the ZIP canonically, verifies
separate complete-bundle and ELF-payload SHA-256 identities, strict source/ABI
provenance, toolchains and flags, bounded schema/defaults/vibe/profile metadata,
the deterministic animated WebP, and the complete ESP32-S3 ELF class,
endianness, OSABI, flags, load map, dynamic tags, one export, zero imports, and
no initializer/finalizer surface. This freezes authoring artifacts only; it does
not enable receiver loading, activation, or installed-wall execution.

## Phase 3A receiver ownership and status v3

### DMA-aligned transport envelope

All command layouts in this document are semantic bytes. Aligned transport
envelope v1 has wire command `0x0b`, version `1`, semantic length `u16`, the
exact semantic bytes, zero padding, and one trailing CRC-16/CCITT-FALSE over
the complete header, semantic payload, and padding. Its total wire size is a
multiple of four and at most 4,096 bytes, leaving at most 4,090 semantic bytes.
Malformed version, length, padding, alignment, or CRC rejects without semantic
dispatch. The receiver retains explicit legacy `semantic || CRC` decode.

Capability `aligned_envelope_v1 = 1<<14` gates Host use. Startup PING and status
discovery remain legacy. Enabling requires three consecutive exact parsed
status-v3+ snapshots that advertise the bit and carry a strictly advancing
receiver-owned `receiver_packets` counter. Stale, malformed, truncated, or
bad-magic input resets the pending streak without flipping active framing; a
counter rollback begins a new observation epoch. Once enabled, three fresh
consecutive capability-absent observations are required to downgrade, so one
corrupt snapshot cannot disable the envelope. The legacy four-byte and
authoritative eight-byte CONFIG forms, full frames, and negotiated
320/416/768/1,216-byte status queries then use aligned wire lengths 12, 16,
3,320, and 328/424/776/1,224 bytes respectively. Production deployment requires
the capability, all five Host envelope-enabled flags, and the exact aggregate
enabled count before health acceptance, preserving the firmware-first rolling
order. The outer CRC replaces, rather than nests, the legacy command CRC;
existing valid/invalid CRC counters retain their integrity meaning.

Logical receiver 3 alone may negotiate aligned-envelope v2 after the Host has
been explicitly configured and has observed capability `fec_envelope_v2 =
1<<15` in three fresh, strictly counter-advancing status snapshots. The
maintained service configuration is `LEDGRID_FEC_RECEIVER_IDS=3`; an empty
allowlist is off, and unlisted receivers cannot enable FEC. The v2 protected
data is:

`0x0b || 2 || canonical_v1_wire_length:u16 || canonical_v1_wire`

The complete v2 header and complete v1 packet, including its CRC, are SECDED
protected in fixed systematic codewords of 128 data bytes plus two parity bytes.
The parity field contains 11 shortened-Hamming check bits and one overall even
parity bit; its upper four bits are reserved zero. Codeword count is even for
DMA alignment and at most 30. A receiver recognizes the v2 marker only for a
valid even-codeword transaction when the total Hamming distance across raw
`0x0b02` is at most one, then corrects each codeword before validating the exact
v2 marker/length, canonical v1 size/padding/CRC, and zero outer tail. One bad bit
per codeword is corrected; two in one codeword are terminal and never dispatch.
The limits and installed sizes are exact:

| Semantic form | Inner v1 | Codewords | FEC wire | FEC data tail |
|---|---:|---:|---:|---:|
| receiver 3 `SET_ALL` (3,313 bytes) | 3,320 | 26 | 3,380 | 4 |
| one-strip `SET_ALL` (415 bytes) | 424 | 4 | 520 | 84 |
| maximum semantic (3,830 bytes) | 3,836 | 30 | 3,900 | 0 |

Status v7 is 1,248 bytes. It preserves v6 offsets and appends received,
accepted, corrected-packet, corrected-codeword, uncorrectable, semantic-CRC,
framing, and last/maximum decode-time fields. Received equals accepted plus the
three mutually exclusive terminal outcomes. Corrected packets are accepted and
may be nonzero; uncorrectable, semantic-CRC, and framing packets are rejected.
Host FEC sent/codeword/parity/tail counters advance only after successful I/O.

After envelope negotiation settles, ordinary full-frame streaming may use the
buffer-protocol `writebytes2` path because `SET_ALL` has no same-transaction
acknowledgement to consume. The Host must first read a positive kernel
`/sys/module/spidev/parameters/bufsiz` value and prove that the complete selected
wire packet fits; otherwise that frame uses one full-duplex transfer because
`writebytes2` may split an oversized write and release chip select between
pieces. Every explicit status query and every control command remains full
duplex. The five installed receivers retain staggered full-frame status samples
at distinct phases of one shared 128-wall-frame sequence, so no wall frame
schedules more than one ordinary sample and each receiver is scheduled every
0.853 seconds at 150 FPS. Receivers 0-2 capture the status snapshot in-band on
their 3,320-byte SET_ALL transfer and receiver 3 on its 3,380-byte FEC transfer.
Receiver 4's 424-byte wire frame cannot hold the 1,248-byte status-v7 snapshot,
so its scheduled phase first clocks one status-capable query and then keeps the
SET_ALL write-only; truncation is never
counted as a successful sample. A receiver without proven write-only support may
fall back to full duplex on an otherwise unsampled frame. Explicit acceptance
status refreshes remain authoritative and continue to observe the receiver-owned
CRC, packet, mailbox, and display counters. Per-device and aggregate
`full_frame_status_transfers` and `full_frame_write_only_transfers` counters must
sum to `full_frame_transfers`. Fresh `full_frame_status_samples` are a subset of
the raw full-duplex transfers; malformed, truncated, and stale snapshots advance
`full_frame_status_sample_misses`, while current and maximum frame-gap gauges
prove freshness. `spidev_buffer_size` exposes the exact capacity proof and
`full_frame_write_only_supported` exposes whether the selected full-frame wire
size is safe. An unavailable buffer-write API falls back to one full-duplex
transfer, while an ambiguous I/O failure is never retried blindly.

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
active context with the same scene epoch. CONFIG accepts four compatibility
forms. The four-byte form is `[0x07, local_strips, leds_hi, leds_lo]`; the
five-byte form appends the legacy debug/flags byte. The six-byte form appends a
logical receiver ID 0–3 at byte 5, uses flags bit 7 for receiver-native strip
reversal, and retains the previously provisioned global offset. The explicit
heterogeneous-topology form is exactly eight bytes:

| Byte | Field |
| ---: | --- |
| 0 | command `0x07` |
| 1 | active local strip count (`1..8`) |
| 2–3 | LEDs per strip / local height, big-endian `u16` |
| 4 | compatibility flags; bit 7 reverses receiver-native local strip order |
| 5 | installed logical receiver ID (`0..4`) |
| 6–7 | global strip offset, big-endian `u16` |

The finalized fifth receiver therefore receives
`07 01 00 8a 00 04 00 20`. Unprovisioned identity is `0xff`, and local commands
fail closed until identity is provisioned. The finalized host must additionally
verify active local width and global offset from status v3+; receiver 4 requires
the eight-byte form because the legacy six-byte identity range ends at 3.

Status discovery uses ID `0x08` followed by 319 zero bytes and returns the exact
320-byte, big-endian `LGS3` snapshot below. Only after its capability bits are
observed may the host negotiate a 416-byte `LGS4`, 768-byte `LGS5`, 1,216-byte
`LGS6`, or 1,248-byte `LGS7` query by padding the same command with zeros to that
exact transfer size. The v7 record preserves the complete v6 prefix apart from
magic/version and appends the FEC counters described above.

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
`1<<1`, status v3 `1<<2`, explicit ownership `1<<3`, and aligned transport
`1<<14`. Every current image advertises aligned transport. The ordinary image
otherwise advertises status/ownership only; the named canary image also
advertises the local/context bits.

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

The historical Phase 1 fixture wall is 32×138. The finalized topology amendment
above changes the installed wall to 33×138 and adds a fifth one-strip slice.
Receivers own explicit global offsets and widths; a receiver-local index is
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

The historical global golden has origin 0, all 32 strips, and canonical
ascending strip order. The finalized-wall golden has origin 0 and all 33 strips.
Its receiver views carry explicit widths `(8,8,8,8,1)` and physical origins;
bit 0 is set only when payload rows are stored in descending physical strip
order. Reassembly uses origin, width, and flag to recover canonical global order.

The portable C++ receiver decoder accepts the exact canonical byte count for the
explicitly expected local width (1 through 8), origin, and strip direction. The
four eight-strip views remain 10,264 bytes; the fifth one-strip view is smaller
according to the same header/table/section formula.
Before it exposes non-owning const section pointers, it validates the complete
frozen header/table, reserved bytes, content digest, every section CRC and
bound, enum and fixed-point ranges, category/region membership, obstacle
containment in clearance, all edge-subset invariants, and the exact equivalence
between zero distance and obstacle membership. Failure clears the output view.

The completed portable prerequisite did not add real receiver staging,
activation, receiver status, persistent firmware storage, optics, or wall
mutation. The transaction engine and C++ decoder remain transport/display
neutral; the separately gated runtime contract below composes them without
changing the frozen profile bytes.

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
canonical global profile and five immutable receiver views. Receiver-view cache
identity contains the global content digest, physical lane order, and native
strip direction. Transport routes and host-frame strip direction remain named
but do not change profile semantics or receiver bytes.

The portable transaction engine binds one global profile ID to five
receiver-specific payload SHA-256 values and operates through a small
transport-neutral receiver interface. Its lifecycle is deterministic
`preflight`, `stage`, `verify`, `commit`, and failure compensation. Capacity
plus reserve is checked on all five targets before mutation; active, rollback,
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

Resolution, artifact validation, topology slicing, 33x138 controller-geometry
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
serialize the installed 33x138 arrays per frame. The managed category, clearance, edge,
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

### Phase 3C receiver-profile staging and activation contract

Receiver profiles use the existing 4,096-byte SPI transaction ceiling and
trailing CRC-16/CCITT-FALSE. All integers are big-endian. The global profile ID
is the canonical global LGIP content digest; the payload digest is ordinary
SHA-256 over the exact receiver-specific 10,264-byte LGIP view. Every binding
carries both identities. Commands `0x40` through `0x47` are reserved for this
version-1 lifecycle:

| Command | ID | Exact fields after ID | Exact bytes before CRC |
| --- | ---: | --- | ---: |
| `PROFILE_PREFLIGHT` | `0x40` | global ID `bytes[32]`, payload digest `bytes[32]`, payload bytes `u32` | 69 |
| `PROFILE_BEGIN` | `0x41` | preflight token `u64`, global ID `bytes[32]`, payload digest `bytes[32]`, payload bytes `u32`, logical receiver ID `u8`, physical strip origin `u16`, flags `u8` | 81 |
| `PROFILE_CHUNK` | `0x42` | payload offset `u32`, data `bytes[1..4085]` | 6 through 4,090 semantic bytes |
| `PROFILE_FINALIZE` | `0x43` | global ID `bytes[32]`, payload digest `bytes[32]` | 65 |
| `PROFILE_VERIFY` | `0x44` | global ID `bytes[32]`, payload digest `bytes[32]` | 65 |
| `PROFILE_ACTIVATE` | `0x45` | expected binding generation `u64`, global ID `bytes[32]`, payload digest `bytes[32]` | 73 |
| `PROFILE_RESTORE` | `0x46` | expected binding generation `u64`, then active/staged/rollback binding slots | 204 |
| `PROFILE_ABORT` | `0x47` | none | 1 |

One restore binding slot is `present:u8, global_id:bytes[32],
payload_digest:bytes[32]`; absent slots require a zero present byte and 64 zero
digest bytes. BEGIN flag bit 0 is receiver-view reversed strip order; all other
bits are zero. Its logical ID, aligned physical origin, and direction must match
provisioned receiver identity and installed topology before payload acceptance.

The aligned envelope leaves exactly 4,085 production chunk-data bytes:

```text
4090 semantic - command(1) - offset(4) = 4085
```

The canonical receiver view therefore transfers at offsets `0`, `4085`, and
`8170` with data lengths `4085`, `4085`, and `2094`. Chunks are strictly
contiguous. An exact retry of the latest accepted chunk is idempotent; a gap,
overlap, conflicting retry, overflow, extra byte, or post-finalize chunk rejects
without making partial data visible. The rollback decoder still accepts the
former 4,089-byte legacy chunk when it arrives in legacy framing; current Hosts
never select that ceiling after aligned capability discovery.

PREFLIGHT is read-only. Success returns a nonzero opaque token bound to the
candidate identities and size, receiver identity/topology, binding generation,
capacity/reserve, and exact eviction plan. BEGIN consumes only that unchanged
token. An intervening cache/binding mutation invalidates it, allowing all five
receivers to preflight before the first mutation. FINALIZE verifies received
size and payload SHA-256 before atomic temporary-to-content-addressed rename.
VERIFY reopens the visible bytes, verifies both identities, and runs the strict
LGIP receiver decoder with provisioned origin/direction. Only a verified
binding may become staged or active.

ACTIVATE compares its expected generation with the current binding generation.
Success atomically promotes staged to active, prior active to rollback, clears
staging, increments generation, and pins active/rollback payloads. Exact retry
is idempotent; an older generation or different binding conflicts. RESTORE uses
the same compare-and-swap and restores the complete active/staged/rollback
snapshot only when every present binding has verified bytes. ABORT removes
partial transfer state but never deletes visible cached payloads or changes
bindings. Compensation is attempted on every receiver and accepted only after
exact snapshot and backing-byte validity are re-proven.

#### Persistent cache and partition contract

The common 16 MiB image uses this explicit partition layout; changing any row
is a complete firmware-installation change:

| Name | Type/subtype | Offset | Size |
| --- | --- | ---: | ---: |
| `nvs` | data/nvs | `0x9000` | `0x5000` |
| `otadata` | data/ota | `0xe000` | `0x2000` |
| `ota_0` | app/ota_0 | `0x10000` | `0x600000` |
| `ota_1` | app/ota_1 | `0x610000` | `0x600000` |
| `profilecache` | data/spiffs | `0xc10000` | `0x3e0000` |

`profilecache` is disposable receiver state, never authority. Final payload
names are content-addressed and partial files never use the final name. At
least `0x80000` bytes (512 KiB) of filesystem-reported free space remain after
a transaction. Capacity uses reported usable/free bytes and filesystem
overhead, not nominal partition size. Active, staged, and rollback bindings are
pins; only inactive valid payloads may be evicted, in deterministic LRU order.
Pinned deletion, reserve violation, corrupt content, unexpected names, or
unprovable metadata fails closed. Cache loss is repaired from the Pi library.

Binding metadata and generation survive an ordinary receiver restart. Startup
exposes only complete checksum-valid metadata whose referenced payloads reopen
and verify. Partial transfer is discarded. A corrupt active/staged/rollback
binding sets cache integrity false and cannot be healthy or auto-activated.
Profile recovery does not execute optics or change display ownership.

#### Status v5 and acknowledgement

Capability bits `1<<6` and `1<<7` mean installation-profile v1 and status v5.
The host first issues the legacy-safe 320-byte v3 query. Only after both bits
appear may it issue `STATUS_QUERY` followed by 767 zero bytes and parse the
768-byte `LGS5`, version-5 response. Bytes 5 through 415 retain v4 semantics
and offsets. The profile extension is:

| Offset | Bytes | Field |
| ---: | ---: | --- |
| 416 | 1 | profile operation result |
| 417 | 1 | transfer state |
| 418 | 1 | LGIP decoder error |
| 419 | 1 | flags |
| 420 | 28 | capacity, used, free, reserve, reclaimable, received, total (`u32` each) |
| 448 | 8 | binding generation |
| 456 | 8 | preflight token |
| 464 | 32 | last probed payload digest |
| 496 | 64 | transfer global ID and payload digest |
| 560 | 64 | active global ID and payload digest |
| 624 | 64 | staged global ID and payload digest |
| 688 | 64 | rollback global ID and payload digest |
| 752 | 8 | cache writes and evictions (`u32` each) |
| 760 | 8 | stage, verify, activate, and restore counters (`u16` each) |

Flag bits 0 through 6 are cache integrity, preflight can-stage, last probe
found, active present, staged present, rollback present, and transfer active;
bit 7 is zero. Absent binding digests are zero. Transfer states are `Idle=0`,
`PreflightReady=1`, `Receiving=2`, `Finalizing=3`, `Staged=4`, and `Failed=5`.
Profile results are `None=0`, `Ok=1`, `Unsupported=2`, `InvalidSize=3`,
`InvalidState=4`, `InvalidToken=5`, `InvalidOffset=6`, `DigestMismatch=7`,
`InvalidProfile=8`, `WrongDevice=9`, `WrongGeometry=10`, `StorageError=11`,
`NoSpace=12`, `NotFound=13`, `Conflict=14`, `Pinned=15`, and
`IntegrityError=16`.

Profile commands use the existing command/result operation-sequence rule. The
host drains the two-deep response queue and accepts only matching command ID,
next sequence, result, transfer state, token/generation, and digest fields. A
v3 response clears host overlay and profile extensions; v4 preserves overlay
and clears profile; v5 parses both atomically.

The `receiver_geometry_profile` rollout gate defaults false. Receiver firmware
uses the independently auditable compile gate
`LEDGRID_ENABLE_INSTALLATION_PROFILES`, which defaults to `0`; only the named
local canary and portable native-test environments set it to `1` in this slice.
While either side is off, the receiver does not advertise bits 6/7, mount or
mutate `profilecache`, or accept `0x40..0x47`, and the host emits no profile
command. Enabling both changes only cache and binding state until an active
profile and committed nonzero `hue_shift` context are both present. Profile
commands never claim base ownership, change foreground state, start a renderer,
or alter complete `SET_ALL` as the universal host takeover path. Preflight,
transfer, finalize, verify, abort, and idempotent activation remain display-
inert. A successful activation or restore that changes the active binding
invalidates one in-flight local presentation and requests a fresh frame; it
does not reset ownership, context, foreground generation, semantic time, or
cadence history.

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

### Receiver `hue_shift` optic v1

`hue_shift` is the only v1 framework optic implemented by both the host and
receiver. The committed presentation-context entry with modifier ID `4`
supplies an unsigned Q8.8 strength from `0` through `256`. Missing entry, zero
strength, no active valid installation profile, feature-off firmware, and
complete host-frame ownership are exact byte-for-byte no-ops. Other modifier
entries remain staged and reported but do not fall through to this optic.

The transform runs after receiver-local RGB rendering, resolved luminance, and
sparse premultiplied-RGBA foreground composition, but before physical master
brightness and encoding. It touches exactly the profile pixels whose category
is foliage (`1`) or globe (`2`); clearance-only and empty pixels are unchanged.
The receiver reads the active decoded profile without copying or mutating it.

For every quantized strength `s` in `0..256`, the generated contract table owns
one signed Q14 RGB matrix. The authoring transform is the existing YIQ hue
rotation with angle `pi * s / 256`:

```text
Y =  0.299 R + 0.587 G + 0.114 B
I =  0.596 R - 0.274 G - 0.322 B
Q =  0.211 R - 0.523 G + 0.312 B
I' = I cos(angle) - Q sin(angle)
Q' = I sin(angle) + Q cos(angle)
R' = Y + 0.956 I' + 0.621 Q'
G' = Y - 0.272 I' - 0.647 Q'
B' = Y - 1.106 I' + 1.703 Q'
```

The composed 3-by-3 coefficients are quantized once with signed half-away-from-
zero rounding at scale `16384`; strength zero is forced to the exact identity
matrix. Runtime code performs no trigonometry. For each output channel and RGB8
input vector, host and firmware compute exactly:

```text
accumulator = M[row][0] * R + M[row][1] * G + M[row][2] * B
channel = clamp_u8(floor((accumulator + 8192) / 16384))
```

The generated JSON/C++ vectors bind the complete coefficient-table digest and
cover RGB extrema, representative colors, strengths `0`, `1`, `64`, `128`, and
`256`, negative accumulators, clipping, exact obstacle edges, and installed
8-strip receiver boundaries. Zero strength exits before profile or pixel work;
invalid strength, geometry, or buffer bounds fail without partial mutation.

### Read-only installation-geometry canary v1

The native geometry canary is a diagnostic primitive, not a selectable scene
or an activation command. It replaces one caller-owned 8x138 receiver RGB
slice only after validating the complete read-only profile view and output
buffer. Invalid geometry, section values, bounds, or output/profile aliasing
fail before any output byte changes. Rendering never mutates or copies profile
section bytes.

Its 18 stable semantic classes are empty, clearance-only, foliage
interior/edge, and interior/edge for each of globe regions 1 through 7. Empty
is black; clearance-only has its own dim RGB identity. Each obstacle class has
a unique red/green identity, while blue is exactly `32` for an interior pixel
and `255` for an edge pixel. Thus a captured pixel mechanically preserves
category, globe-region identity, and the exact obstacle-edge bit rather than
letting edge color erase region identity. Category zero with clearance one is
clearance-only; category zero with clearance zero is empty. Foliage must have
region zero, and a globe must have one of the seven frozen region IDs.

Portable acceptance stitches the five installed receiver views in physical
order and proves all 18 classes, all three semantic fields, all five strip
origins/directions, every receiver boundary, the heterogeneous one-strip tail,
and read-only profile bytes.
This diagnostic does not constitute installed-wall profile activation,
photographic seam acceptance, ESP32 timing evidence, or strict all-five
receiver health acceptance.

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

The aligned-host patch limit is exact:

```text
max_rgba_pixels = floor((4090 semantic - 30) / 4) = 1015
```

An installed receiver's 1,104-pixel full foreground snapshot is therefore two
ascending patches: `[0, 1015)` and `[1015, 1104)`. Patches are contiguous or
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
exact aligned-host capacity rule is:

```text
sum(pixel_counts) + span_count <= 1015
```

Consequently one batch can contain at most 1,014 pixels in one span or 507
one-pixel spans. The largest legal semantic batch is 4,088 bytes; the aligned
envelope pads its 4,094 unaligned bytes to the 4,096-byte wire ceiling. A
canonical batch-mode 1,104-pixel snapshot uses logical spans `[0, 1014)` and
`[1014, 1104)` in two packets. Legacy semantic serializers and the receiver's
rollback decoder retain the former 1,016/1,015-pixel limits, but production
packing does not emit them.

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

### Phase 4 managed-native runtime amendment

`receiver_native_modules` is independent from the generic receiver-hybrid gate
and defaults off. When explicitly enabled, the host accepts only a
`ResolvedNativeBackground` from the Pi-authoritative managed library; IPC carries
the content digest, never an arbitrary filesystem path or module bytes. Before
probe or mutation, the controller proves the exact logical IDs `0..4`, widths
`(8,8,8,8,1)`, offsets `(0,8,16,24,32)`, native direction flags, output-lane
masks, required capability mask, bundle digest, and payload digest.

Install and activation are exact-roster transactions under one operation lock.
Every target is probed, staged, verified, and bound before activation; a partial
failure compensates every receiver and is healthy only after the complete prior
active/staged/rollback state is re-proven. Activation additionally binds the
typed-parameter digest, presentation context, installation-profile digest, vibe,
plant-modifier state, and scene revision. Restart adoption performs those same
unanimous checks without mutation before the manager republishes the foreground
snapshot.

A native load/callback/watchdog failure records the exact payload quarantine and
selects the compiled fallback; it is never retried automatically. Clearing
quarantine is an explicit exact-bundle operator action that verifies all five
receivers before reinstall may proceed. Complete host recovery is likewise an
explicit transition: the manager retains observable native ownership and error
state until a complete Python frame takeover and exact host-authority status are
positively acknowledged.

## Display ownership and command effects

The gated state is orthogonal:

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
| Mixed five-board stage | Do not report success or activate a healthy mixed scene; replay or compensate |
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
