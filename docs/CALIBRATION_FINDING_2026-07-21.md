# LED wall geometry finding — 2026-07-21

## Outcome

The installed wall has **32 strips × 138 physically addressable LEDs**. The
host and all four receivers have been corrected from 140 to 138 LEDs per strip;
the two formerly configured positions above the physical top no longer exist in
the runtime geometry.

## Evidence

`Plant Calibration` version 1.2 adds a `dimension_probe` pattern. Its final four
logical LED positions are deliberately distinct:

| Logical LED | Probe color | Physical observation |
| ---: | --- | --- |
| 136 | cyan | visible below the top edge |
| 137 | magenta | visible at the top edge |
| 138 | orange | absent |
| 139 | white | absent |

The first four positions are visible at the opposite edge. Decade guide rows
are also visible throughout the panel, ruling out a camera crop that merely hid
the final colors. The minimum-brightness evidence capture is recorded in
`config/webcam_wall_calibration.json`; runtime photos remain under the ignored
`calibration_photos/` directory.

The final live-verified 32 × 138 map contains all 4,416 physical LED positions
and marks 504 as foliage-covered after camera-response, semantic cleanup, and
ambient-light vegetation refinement. The foliage map excludes all globe-layer
pixels:

- `config/plant_pixel_map_32x138.json`

A second camera-space calibration identifies the seven glass rooting vessels as
independent circular regions, each bounded by an exact 8 × 8 LED box. Their
combined layer contains 356 LEDs after removing the circular corners and
clipping the two right-edge footprints to the physical 32-column wall:

- `config/plant_globe_regions_32x138.json`
- `config/plant_globe_map_32x138.json`

`Plant Mask Highlight` renders the two layers independently and gives the globe
color precedence where a vessel and foliage overlap. `Plant Glow` uses the same
semantic masks plus reusable logical-grid halo rings for a production effect.
The live verification
captures are `webcam-20260721-globes-pass1.jpg` (initial globes-only sizing)
and `webcam-20260721-plant-layers-8x8-pass1.jpg` (corrected 8 × 8 globes with
cyan foliage).

## Applied production fix

1. The installed layout defaults now use 138 LEDs per strip throughout the host.
2. Receiver firmware retains a 140-pixel buffer capacity but accepts and boots
   with the installed 138-pixel geometry.
3. Live telemetry reports 138 LEDs per receiver and 4,416 total host pixels,
   with no receiver CRC, publish, SPI, or display errors during verification.
4. `Plant Mask Highlight` defaults to the live-verified
   `config/plant_pixel_map_32x138.json` artifact; the ignored
   `config/plant_pixel_map.json` remains only for backward compatibility.

This finding should be treated as an installation-geometry correction, not a
camera-cropping workaround.
