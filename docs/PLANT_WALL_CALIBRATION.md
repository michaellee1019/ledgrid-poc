# Plant-wall webcam calibration

Rebuild the editable foliage and globe maps when the camera, plants, or globes
move. Use the installed 33 × 138 geometry and verify strip order separately
using the [hardware map](HARDWARE.md). Camera movement invalidates the
projection used for new measurements.

## Capture and project

1. Record the active Scene, power, brightness, and fresh receiver telemetry.
   Back up the existing calibration files in `data/config/` on the Pi.
2. In a maintenance window, capture paired wall-off and white frames under
   reduced room light. Use bounded brightness. Capture an additional wall-off
   frame under normal room lighting for leaf and globe segmentation.
3. Capture orientation and dimension references. Check that logical row 0 is
   at the camera-bottom edge and row 137 is the top physical row. Confirm all
   33 columns are visible. Keep the camera position and exposure fixed within
   each pair.

Use the [direct webcam capture helper](../.codex/skills/calibrate-led-strips/scripts/capture_anker_frame.py)
on the Mac. Work in a separate calibration staging directory. Copy
`webcam_wall_calibration.json` there as `wall.json` and set `CALIBRATION_DIR`
to that directory, which also holds the photographs.

```bash
python3 scripts/detect_webcam_wall_corners.py \
  --on "$CALIBRATION_DIR/led-white.jpg" \
  --off "$CALIBRATION_DIR/led-off.jpg" \
  --config "$CALIBRATION_DIR/wall.json" \
  --overlay "$CALIBRATION_DIR/corners.jpg" \
  --report "$CALIBRATION_DIR/corners.json" \
  --write-config

python3 scripts/build_webcam_pixel_map.py "$CALIBRATION_DIR/led-white.jpg" \
  --off-image "$CALIBRATION_DIR/led-off.jpg" \
  --config "$CALIBRATION_DIR/wall.json" \
  --strips 33 --leds-per-strip 138 --flip-y \
  --output "$CALIBRATION_DIR/pixel-map.json" \
  --overlay "$CALIBRATION_DIR/pixels.jpg"
```

Require corner confidence at least 0.60 and four unclipped corners. Inspect
the projection overlay before deriving masks.

## Rebuild the semantic masks

Copy the current globe-region JSON into staging as `regions.json`. Set its
`geometry` to the new projection's `geometry` and `source_image` to the fresh
ambient-off photograph. Preserve the seven region IDs and their 8 × 8
footprints. For each region, illuminate an outline and center at 5–8%
brightness and compare the projected light center with the glass rim. Move its
`strip_start` and `led_start` by whole LEDs until each axis is within 0.75 LED.
Allow the physical wall edge to clip the footprint.

```bash
python3 scripts/build_globe_pixel_map.py \
  --pixel-map "$CALIBRATION_DIR/pixel-map.json" \
  --regions "$CALIBRATION_DIR/regions.json" \
  --output "$CALIBRATION_DIR/globes.json" \
  --overlay "$CALIBRATION_DIR/globes.jpg"

python3 scripts/refine_foliage_from_ambient.py \
  --pixel-map "$CALIBRATION_DIR/pixel-map.json" \
  --ambient-image "$CALIBRATION_DIR/ambient-off.jpg" \
  --globe-map "$CALIBRATION_DIR/globes.json" \
  --fresh-baseline \
  --output "$CALIBRATION_DIR/foliage.json" \
  --overlay "$CALIBRATION_DIR/foliage.jpg"
```

Check for false foliage over empty wall, missed leaf clusters, and overlap
between foliage and globes. Rerun foliage refinement after any globe edit.

## Verify and publish

Use the current paths listed in [Configuration](../config/README.md) when
installing the validated JSON. Keep the previous copies for recovery. Reload
the controller during the maintenance window so it reads the new maps.
Observe low-brightness foliage and globe feedback separately, then observe
`Plant Glow` in Composer. Record photos, mask overlap, per-globe position
error, and fresh receiver telemetry on all five routes. Restore the recorded
operator brightness, power and Scene after the calibration run.
