# Rev5 placement and routing review

This review records what the importable EasyEDA scaffold changes and what it
does not claim to have completed.

## Placement decisions

| Area | Decision | Reason | Remaining check |
| --- | --- | --- | --- |
| Receiver A | Use V4 `2_SMD` at the left edge; move it 4.064 mm upward | Shorter SPI0 corridor and room for the output connector | Verify module courtyard and enclosure RF clearance |
| Receiver B | Use V4 `1_SMD` at the right edge; rotate it 180 degrees | The V4 orientation put its antenna toward the board interior | Restore and inspect the approved 3D model |
| A buffers | Move `TRAN2_A` and `TRAN2_B` 6.096 mm upward | The proposed 34-position connector occupies the lower corridor | Re-place after the exact CN1 footprint is approved |
| B buffers | Retain the V4 locations | They already sit above the connector reservation and below receiver B | Re-place with the exact output resistor/ESD footprints |
| SPI damping | Pi-driven resistors at the Pi source; MISO resistors at the ESP source | Source damping must precede the long route | Approve the 0402 land pattern and tune 0/22/33/47 ohm on prototypes |
| SPI probing | In-line signal pads with nearby grounded probe pads | Avoid test-point stubs and support ground-spring measurements | Confirm accessibility with the assembled HAT |
| Power | Reserve separate A/B buck areas; remove AP2112 placement | The V4 linear regulators do not meet the Rev5 power architecture | Place from the TPS62162 reference layout after footprint review |
| Output connector | Remove V4 CN1 and reserve a 34-position lower corridor | V4 provided no paired signal returns | Approve connector, mating cable, and enclosure egress |
| USB | Remove the V4 breakout placements | Their connector/ESD/series-layout geometry is not suitable as a Rev5 commitment | Select connector style and route a stackup-specific 90 ohm pair |

## Critical-routing decisions

- A/SPI0 uses a Top-layer corridor. SCLK, MOSI, and CS have no vias. A MISO
  changes to Bottom once after the ESP-side source resistor.
- B/SPI1 uses Bottom for its long runs, with one transition at each receiver
  pad. The short Top segments contain the accessible probe pads.
- All serialized SPI routes are point-to-point. Signal probe pads sit on the
  route rather than on branches.
- Every critical route has a nearby GND probe pad connected into L2 through a
  short path and via. Return vias accompany signal transitions.
- Inner1 has one board-shaped GND copper area. Both antenna areas are copper
  cutouts on all four copper layers.
- Inner2 is intentionally unrouted. The final power layout must preserve a
  continuous reference under the Bottom-layer B/SPI1 corridor; it must not put
  isolated power copper directly beneath those signals.
- The script removes every V4 track and via before adding the reviewed routes,
  so no selector branch or shared receiver bus survives by geometry.

## Automated checks passed

- EasyEDA PCB document type and four copper-layer configuration.
- Authoritative Pi header, receiver A, and receiver B pad-net mapping.
- Receiver A translation, receiver B rotation, and two antenna cutouts per
  copper layer.
- Exactly one L2 GND copper area.
- Eight SPI lengths below their 75/90 mm draft budgets.
- Zero or one signal via per SPI route.
- No different-net track intersections and at least 0.508 mm centerline
  separation in the serialized critical routing.
- Removal of V4 selector and obsolete/blocking footprints.

## Checks that still require EasyEDA and approved mechanics

The generator is not a substitute for native EDA checks. Before fabrication,
run EasyEDA DRC and visually inspect pad-to-track and pad-to-pad clearance,
copper-area refill, thermals, return paths, board-edge clearance, mask slivers,
silkscreen, courtyards, and every moved footprint. Then complete the missing
schematic and placement sections, run ERC, check 3D fit, and review fresh
Gerber/drill output. Until those steps pass, this artifact remains a routing
study rather than a manufacturing release.
