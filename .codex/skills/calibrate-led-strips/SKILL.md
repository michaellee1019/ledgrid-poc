---
name: calibrate-led-strips
description: Verify and calibrate the installed wall receiver permutation and host strip directions using direct webcam captures after wiring changes or visible mapping errors.
---

# Calibrate LED Strips

Use the current AGENTS.md host RGB contract. Measure one coordinate domain at a
time and change only a domain proved wrong. Record known receiver-3 CRC/FEC noise;
controller playback and connectivity do not prove exact physical display.

## Direct camera capture

The documented Anker webcam is attached to the Mac, not the Pi. Use
`python3 scripts/capture_anker_frame.py --list`, then capture with the exact
camera name and an absolute output path. The helper enumerates AVFoundation,
settles exposure and produces an unflipped image. If the documented camera is
absent, establish which available camera points at the wall before capturing.
Retry with local camera permission if sandbox enumeration is empty. Do not use
Photo Booth's mirrored preview or add a horizontal flip.

## Preserve settings

Capture current telemetry from `/api/v1/composer/operations/telemetry` and the
selected Scene/settings before takeover. Use existing supported diagnostic
animations or direct full RGB frames during an authorized maintenance window.
Preserve current brightness, including zero. Return to the prior supported
Scene, or stop if its components have been removed; retain unsupported personal
content and report the limitation. Do not silently replace a saved Scene.

## Independent geometry checks

The fixed installation has 33 strips of 138 pixels, receiver widths
`(8,8,8,8,1)`, routes `(0.0,0.1,1.1,1.0,1.2)`, and measured physical order
`(0,1,2,3,4)`. Host strip direction is forward on every receiver. Check:

1. One distinct saturated color per receiver for physical permutation.
2. The same eight-level luminance ramp within each broad receiver, plus a
   sentinel for the one-strip receiver, for within-receiver direction.
3. A vertical ramp for LED-index direction.
4. Boundary-crossing moving host content for slicing and visible continuity.

Re-register image coordinates from each capture; cameras can move. Accept only
visible endpoints and leave clipped or occluded regions unresolved. Native
procedural direction and sparse overlays have been retired.

## Corrections and evidence

Update the fixed host mapping only when physical evidence establishes a change.
Keep USB serial flashing identity separate from SPI routing and wall position.
Run focused layout/configuration checks, deploy the affected layer, and repeat
the pattern. Save raw captures, hashes, mapping, diagnostic definition, measured
counters, and the limits of the camera view in the current Bead. Preserve
hardware design assets and do not turn known receiver-3 degradation into an
unrequested hardware investigation.
