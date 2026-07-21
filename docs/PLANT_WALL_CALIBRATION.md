# Plant-wall webcam calibration

This runbook rebuilds the 32 × 138 foliage and globe masks after plant growth,
plant movement, or webcam movement. It is a closed loop: capture, derive,
illuminate, photograph, measure, and repeat until photographed light agrees
with the physical objects.

## Current production artifacts

- `config/webcam_wall_calibration.json`: camera and measured panel geometry
- `config/webcam_pixel_map_32x138_candidate.json`: LED-to-camera projection and fresh occlusion evidence
- `config/plant_globe_regions_32x138.json`: seven named, fixed 8 × 8 globe boxes
- `config/plant_globe_map_32x138.json`: circular globe layer, currently 356 in-wall pixels
- `config/plant_pixel_map_32x138.json`: globe-exclusive foliage layer, currently 504 pixels
- `Plant Mask Highlight`: low-brightness calibration feedback
- `Plant Glow`: production example using both semantic layers and edge halos

The mask files use logical LED coordinates, so camera movement does not
invalidate them by itself. It does invalidate the camera homography used to
measure new masks.

## When to recalibrate

Run the full workflow after the camera, a globe, or wall geometry changes. Run
the foliage and verification sections after material growth, pruning, or vine
movement. A monthly capture is a reasonable baseline; also recalibrate whenever
a normal animation visibly shines through new leaves.

## 1. Capture two lighting regimes

Install calibration dependencies once with:

```bash
uv venv .venv-calibration
uv pip install --python .venv-calibration/bin/python -r requirements-calibration.txt
source .venv-calibration/bin/activate
```

Use reduced room lighting for LED response, color correction, and panel-edge
detection. Use normal ambient room lighting for leaf/globe recognition. Outdoor
windows are acceptable because every LED-response measurement subtracts a
wall-off reference captured immediately before it.

Keep the webcam and exposure unchanged within a capture set. Make sure all four
physical wall corners are in frame; otherwise automatic camera reacquisition
must refuse to guess a clipped corner.

```bash
python3 scripts/capture_webcam_wall.py --prefix webcam-YYYYMMDD-led
```

Then turn normal ambient lighting on, stop the wall, and capture a separate
wall-off still as `calibration_photos/webcam-YYYYMMDD-ambient-off.jpg`. Do not
reuse an old ambient frame: leaf color and shadows change during the day.

## 2. Reacquire camera geometry

Always run this check, even when the tripod appears unchanged:

```bash
python3 scripts/detect_webcam_wall_corners.py \
  --on calibration_photos/webcam-YYYYMMDD-led-white.jpg \
  --off calibration_photos/webcam-YYYYMMDD-led-off.jpg \
  --overlay calibration_photos/webcam-YYYYMMDD-auto-corners.jpg \
  --report calibration_photos/webcam-YYYYMMDD-auto-corners.json \
  --write-config
```

The command writes new corners only at confidence 0.60 or better and caps
confidence below that threshold when any corner touches the camera frame. If
it refuses because the wall is clipped, reposition the webcam and recapture;
do not reuse or extrapolate a moved camera's old homography.

Re-run `process_webcam_wall.py` after corner changes. Its rectified image must
contain the complete wall with no room pixels and no cropped LED row. Confirm
orientation markers and run the dimension probe: physical row 137 is the
top-most magenta row, row 136 is cyan, and rows 138/139 are absent. A changed
result is a geometry defect to fix in host/firmware, not a crop to hide.

## 3. Rebuild LED response and foliage

Build a new mapping from the same full-white/off pair. `--flip-y` is required
because logical LED zero is at the camera-bottom edge.

```bash
python3 scripts/build_webcam_pixel_map.py \
  calibration_photos/webcam-YYYYMMDD-led-white.jpg \
  --off-image calibration_photos/webcam-YYYYMMDD-led-off.jpg \
  --config config/webcam_wall_calibration.json \
  --strips 32 --leds-per-strip 138 --flip-y \
  --output config/webcam_pixel_map_32x138_candidate.json \
  --overlay calibration_photos/webcam-YYYYMMDD-pixel-map.jpg

python3 scripts/cleanup_plant_pixel_map.py \
  config/webcam_pixel_map_32x138_candidate.json

python3 scripts/refine_foliage_from_ambient.py \
  --pixel-map config/webcam_pixel_map_32x138_candidate.json \
  --ambient-image calibration_photos/webcam-YYYYMMDD-ambient-off.jpg \
  --globe-map config/plant_globe_map_32x138.json \
  --fresh-baseline \
  --output config/plant_pixel_map_32x138.json \
  --overlay calibration_photos/webcam-YYYYMMDD-foliage-overlay.jpg
```

`--fresh-baseline` seeds brown/dark foliage from the new full-white occlusion
map and unions it with fresh ambient green detection. It does not union the
previous final map, which would accumulate stale pixels over repeated runs.
The globe mask is always subtracted from foliage.

Inspect the overlay for long wall seams, isolated room/hardware pixels, and
missed leaf clusters. Prefer threshold changes and a rerun from fresh evidence
over hand-editing individual indices.

## 4. Recenter the seven globes

Each globe is always an 8 × 8 logical box with a circular footprint. Bloom,
roots, and water can make its bright response look much larger or off-center;
never resize a globe to match that bloom.

Calibrate one stable region ID at a time under ambient lighting. Render it at
5–8% brightness as an outline plus center marker, take a settled frame, and
compare its response centroid with the glass rim/center in the ambient-off
frame. Translate `strip_start` and `led_start` by rounded whole-LED centroid
error, rebuild, and repeat until each axis is within 0.75 LED. Process stable
IDs left-to-right, top-to-bottom. A circular footprint clipped by the physical
right edge is expected and must not be shifted inward merely to restore pixels.

Root-heavy globes require rim/geometry detection rather than brightness
centroid. Dim, one-region captures prevent the bloom that caused the original
2–4 row errors.

```bash
python3 scripts/build_globe_pixel_map.py \
  --pixel-map config/webcam_pixel_map_32x138_candidate.json \
  --regions config/plant_globe_regions_32x138.json \
  --output config/plant_globe_map_32x138.json \
  --overlay calibration_photos/webcam-YYYYMMDD-globes-overlay.jpg
```

Rerun foliage refinement after editing globes so the layers remain disjoint.

## 5. Closed-loop acceptance

Deploy without firmware, start `Plant Mask Highlight` at 5–8% brightness, and
capture ambient-lit foliage-only, globe-only, and combined frames. Then start
`Plant Glow` near 20% brightness and capture the production result.

Accept only when:

- geometry is 32 × 138 = 4,416 pixels and the dimension probe agrees;
- all projected LED centers lie inside the wall quadrilateral;
- foliage and globe masks have zero overlap;
- every globe is an 8 × 8 circular footprint with center error at most 0.75 LED;
- no persistent foliage-mask clusters appear in empty wall areas;
- live telemetry has no CRC, SPI, publish, or display errors;
- unit tests, plugin registry tests, and render benchmark pass.

Save corner, foliage, globe, and Plant Glow overlays/reports together. Images
are calibration evidence; JSON files are the production source of truth.

## What reduced human iteration

The late corrections were not caused by image resolution. Combined,
high-brightness feedback made bloom look like object geometry. The reliable
process is fresh paired references, fully visible camera corners, fixed known
object dimensions, dim one-object feedback, integer centroid translation, and
a photographed acceptance loop. Stable region IDs also prevent ambiguous
instructions such as "the fifth globe." These checks now happen during the
workflow instead of as last-minute judgment calls.
