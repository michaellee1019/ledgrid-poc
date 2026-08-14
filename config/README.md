# Configuration

Place environment-specific configuration files here.

## Installed plant-wall geometry

The canonical 32×138 Phase 3C compiler inputs are:

- `plant_pixel_map_32x138.json`: foliage coverage in global strip-major pixel
  indices;
- `plant_globe_map_32x138.json`: solid globe coverage and pixel metadata;
- `plant_globe_regions_32x138.json`: the seven stable named globe regions;
- `webcam_wall_calibration.json`: camera model, measured wall geometry, and
  homography evidence.

The installation profile must be compiled from canonical global wall
coordinates before receiver slicing. Do not bake the current SPI route, physical
lane permutation, or host/native strip reversal into these calibration files.
Those are independent target-owned topology fields in
`run_state/receiver_hybrid.json`; the staging adapter applies them exactly once
when producing a receiver-local slice.

Camera movement does not change the logical mask indices, but it invalidates the
stored homography for new photographic measurements. Reacquire camera geometry
before rectifying or comparing pixels from a moved view. Preserve prior evidence
and label rejected/superseded captures rather than overwriting them.
