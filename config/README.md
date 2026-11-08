# Wall configuration

The installed renderer reads the editable plant masks from `config/`. On the
Pi, these files are linked to `data/config/` so they survive application
replacement. Back them up before editing.

- `plant_pixel_map_32x138.json` contains foliage coverage.
- `plant_globe_map_32x138.json` contains globe coverage.
- `plant_globe_regions_32x138.json` defines the seven fixed globe regions.
- `webcam_wall_calibration.json` records camera geometry for new captures.

The mask filenames are stable runtime paths. The JSON geometry records the
photographed coverage. For inputs covering fewer than 33 strips, the runtime
leaves the remaining strips unmasked. Produce a fresh 33×138 projection when
recalibrating the whole wall. Store indices in global strip-major coordinates,
independent of SPI routes and within-receiver strip direction.

Use [Plant-wall webcam calibration](../docs/PLANT_WALL_CALIBRATION.md) for
capture, mask generation, and photographed verification.
