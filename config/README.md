# Configuration

Place environment-specific configuration files here.

## Installed plant-wall geometry

The photographed Phase 3C calibration inputs remain 32×138 because that is
the geometry for which the checked-in camera evidence was captured:

- `plant_pixel_map_32x138.json`: foliage coverage in global strip-major pixel
  indices;
- `plant_globe_map_32x138.json`: solid globe coverage and pixel metadata;
- `plant_globe_regions_32x138.json`: the seven stable named globe regions;
- `webcam_wall_calibration.json`: camera model, measured wall geometry, and
  homography evidence.

The installation-profile compiler validates those dimensions, appends the
finalized wall's 33rd column as explicitly unobserved and unmasked, and emits a
33×138 global profile before receiver slicing. The legacy filenames are evidence
identifiers, not the installed runtime geometry; do not rename or reinterpret
them without a new photographed calibration set.

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

## Activation qualification budget

`installation_qualification_budget.json` is the versioned source for the
installed voltage, current, brightness, safety, and evidence-freshness limits
used by portable activation qualification. Its checked-in physical limits are
intentionally `null` and its calibration state is `unqualified`: the repository
does not contain enough as-built electrical evidence to choose those values.
Qualification therefore fails closed until measured, reviewed limits replace
the unknowns in a new revision. Never infer these fields from nominal component
ratings or software defaults.

Do not mark the budget calibrated merely because a meter is available. First
record and review the installed supply, fuse/branch, conductor, connector,
injection, return-path, and LED-strip limits. Then increment the revision and
populate every physical limit from that reviewed as-built envelope with an
explicit derating/headroom policy. The current file must remain unqualified
until that work and the first traceable measurement are complete.

The target capture helper can ingest a separate
`ledgrid.calibrated-electrical-capture` schema-v2 descriptor. It names the exact
qualification binding digest, calibrated budget digest, activation ID, and
brightness, but contains no trusted summary statistics. Instead it pins an
immutable raw `ledgrid-electrical-csv-v1` logger export and calibration
certificate by absolute local path, byte size, and SHA-256. The raw rows are
`timestamp_ms,voltage_v,current_a`; target loading and final evaluation both
reopen the artifacts and recompute sample count, interval/window, and ordered
mean/p95/p99/max voltage/current statistics. The descriptor must also bind every
exact measurement point and branch, or pin a reviewed common-distribution
topology attestation. This ingestion support does not make the current budget
calibrated and does not turn whole-home or browser estimates into installed-wall
evidence. See
[`docs/ACTIVATION_TARGET_QUALIFICATION.md`](../docs/ACTIVATION_TARGET_QUALIFICATION.md)
for the physical measurement and evidence-retention procedure.
