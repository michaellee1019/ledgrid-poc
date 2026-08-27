---
name: calibrate-led-strips
description: Calibrate the ledgrid-poc wall's receiver permutation and host/native strip directions with orthogonal light patterns and direct captures from the Mac-attached Anker webcam. Use after receiver cables or strip wiring move, when one receiver block appears mirrored, or when asked to verify LED wall order; do not use this skill for foliage or globe mask calibration.
---

# Calibrate LED Strips

Measure one coordinate domain at a time, change only the domain proved wrong,
and retain a fresh direct camera capture from the final configuration.

## Direct Anker capture

The Anker webcam is attached to the Mac running Codex, not the Raspberry Pi.
Do not use Photo Booth: its live preview is horizontally mirrored and obscures
the sensor's true left-to-right order. Use
`python3 scripts/capture_anker_frame.py --output <absolute-path.jpg>`. The helper:

- enumerates AVFoundation cameras on every run and resolves `Anker PowerConf
  C200` by name instead of trusting a changeable device index;
- captures an unflipped 1920x1080 frame with `ffmpeg` after exposure settles;
- writes the SHA-256 and selected camera metadata to stdout.

If camera enumeration is empty inside the sandbox, retry the helper with the
required local camera permission. Close any application that exclusively owns
the webcam. Never add `hflip` merely to resemble a preview. Validate orientation
with a known receiver-color diagnostic before interpreting a gradient.

## Preserve operator state

Before any wall takeover, capture `/api/status`, including the complete scene,
parameters, target FPS, brightness, animation speed, vibe, and plant modifiers.
Use the painter full-frame endpoint for static diagnostics. In a `finally`-style
cleanup, restore the exact prior state and verify `painter_active=false`. A
failed photograph or ambiguous result is not permission to leave the diagnostic
running.

## Separate the coordinate domains

Inspect the live target-owned hybrid configuration and receiver status before
using repository defaults. Keep these independent:

1. logical receiver identity and SPI route;
2. physical left-to-right receiver permutation;
3. host full-frame and sparse strip direction;
4. receiver-native procedural strip direction;
5. LED index direction along each vertical strip.

A cable swap changes receiver permutation without necessarily changing either
direction map. A host painter frame proves host strip order only. Verify native
direction independently with a receiver-native signed phase field before copying
a host result into the native map.

## Orthogonal diagnostics

- Receiver permutation: give every receiver block a distinct saturated color.
- Within-receiver host direction: reset the same eight-level neutral luminance
  ramp in every eight-strip receiver, from light at local strip 0 to dark at
  local strip 7. Use large nonzero steps so exposure cannot hide the order. Give
  the one-strip receiver a separate sentinel color.
- Vertical LED direction: use a top-to-bottom ramp while holding every strip at
  the same value; do not infer this axis from the strip-axis ramp.
- Native direction: render an obvious signed horizontal phase slope in receiver
  code and compare its endpoints with the accepted host result.

Capture a known receiver-color frame first to establish whether the direct image
is sensor-true. Then capture the luminance ramp. For each eight-strip block,
record which physical edge is light and which is dark. The partial Anker view can
accept only blocks whose two endpoints are visible; leave clipped or occluded
blocks unresolved.

Do not reuse image-space strip coordinates across captures. The Anker can shift
its framing while the Mac and wall remain fixed. Re-register every frame from
the receiver-color bands, wall edges, and the one-strip sentinel before
calculating a direction or comparing pre/post correlations.

## Apply and accept a correction

Treat the operator's observation as authoritative and use the photograph to
identify the affected logical receiver. Flip only the proven host or native
direction bit. Update the central topology constants, migrations, generated
fixtures, documentation, and focused direction tests together; retain transport
routes, widths, offsets, and output masks.

Run focused mapping/config tests, fixture regeneration equality, the repository
gate proportional to the change, and the clean deployment appropriate to the
changed layer. Repeat the same ramp after the final restart. Accept only when
every visible broad receiver has the same physical light-to-dark direction, the
single strip remains at the correct edge, all five identities are readable, and
error counters remain stable through a fresh measured window.

Save the raw frame, a wall-only crop when useful, SHA-256, active release,
configuration digest, exact diagnostic definition, and what the partial view
does and does not prove. Label rejected or pre-fix captures explicitly. Restore
the operator's scene before reporting completion.
