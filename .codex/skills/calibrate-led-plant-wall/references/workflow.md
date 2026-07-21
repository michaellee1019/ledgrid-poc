# LED plant-wall calibration reference

## Acceptance gates

| Gate | Required result |
| --- | --- |
| Physical geometry | 32 strips x 138 LEDs = 4,416 |
| Dimension edge | row 137 magenta at top, row 136 cyan below, no rows above |
| Camera geometry | all four corners in frame; automatic confidence >= 0.60 |
| Foliage baseline | current full-white occlusion plus current ambient segmentation |
| Semantic overlap | zero foliage indices in the globe layer |
| Globe geometry | exactly seven named 8x8 circular regions |
| Globe alignment | response/glass center error <= 0.75 logical LED per axis |
| Runtime | no CRC, SPI, publish, or display errors |

## Closed-loop globe correction

For each region ID, show only that globe at 5-8% brightness with an outline and
center marker. Capture after camera auto-exposure settles. Use the fresh
ambient wall-off image to locate the physical glass rim, not the brightest root
mass. Project both centroids to logical space using the current pixel map and
apply `round(target - response)` to `strip_start` and `led_start`. Preserve
`width=8` and `height=8`, rebuild, and remeasure. Do not correct an edge-clipped
circle inward.

## Anti-drift rules

- Pair every emitting capture with an immediate wall-off reference.
- Reacquire homography whenever the camera moves.
- Use `refine_foliage_from_ambient.py --fresh-baseline` after plant movement.
- Do not repeatedly union a prior final foliage mask.
- Rebuild foliage after every globe-map update.
- Keep stable globe IDs; use ordering only as a secondary check.
- Save overlays and reports from every accepted run.

## Visual diagnosis

- Large white/cyan blocks: feedback brightness is too high; reduce to 5-8%.
- Globe looks four rows too tall: bloom was mistaken for geometry; restore 8x8.
- Root-heavy globe seems off-center: use glass rim, not intensity centroid.
- Long straight mask components: wall seams leaked into foliage segmentation.
- Old leaf positions remain: run from a fresh baseline, not the old final map.
- Top corner lands on camera edge: recapture with the complete wall in frame.
