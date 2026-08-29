# Rev5 placement and routing review

This review records what the importable EasyEDA scaffold changes and what it
does not claim to have completed.

## Placement decisions

| Area | Decision | Reason | Remaining check |
| --- | --- | --- | --- |
| Receiver A | Use V4 `2_SMD` at the left; move it 11.176 mm upward | Opens a short, monotonic GPIO-to-HCT fanout | Verify courtyard and restore the approved 3D model |
| Receiver B | Keep V4 `1_SMD` facing its buffers; move it 4.064 mm upward | Wi-Fi is unused, so direct GPIO routing takes priority over antenna orientation | Verify courtyard and restore the approved 3D model |
| A buffers | Move `TRAN2_A` and `TRAN2_B` 8.128 mm upward | Clears damping resistors and the reserved connector corridor | Re-check with the approved CN1/ESD footprints |
| B buffers | Retain the V4 locations | They already sit above the connector reservation and below receiver B | Re-place with the exact output resistor/ESD footprints |
| SPI damping | Pi-driven resistors at the Pi source; MISO resistors at the ESP source | Source damping must precede the long route | Approve the 0402 land pattern and tune 0/22/33/47 ohm on prototypes |
| SPI probing | In-line signal pads with nearby grounded probe pads | Avoid test-point stubs and support ground-spring measurements | Confirm accessibility with the assembled HAT |
| ESP-to-HCT | Permute gates within each HCT package; route all 16 lanes on Bottom | Preserves GPIO-to-LED mapping while eliminating geometric crossings | Confirm firmware lane table against the manifest |
| Buffer safe state | Add 100 kΩ input pulls and GPIO8/2N7002 OE gating | Prevent floating HCT inputs and GPIO18 boot-glitch propagation | Scope outputs throughout reset and firmware enable |
| LED damping | Put 68 Ω immediately at all 16 HCT outputs | Source-match the paired harness envelope | Tune 0/33/47/68/100 Ω using the real cable |
| Power | Reserve separate A/B buck areas; remove AP2112 placement | The V4 linear regulators do not meet the Rev5 power architecture | Place from the TPS62162 reference layout after footprint review |
| Output connector | Remove V4 CN1 and reserve a 34-position lower corridor | V4 provided no paired signal returns | Approve connector, mating cable, and enclosure egress |
| USB | Remove the V4 breakout placements | Their connector/ESD/series-layout geometry is not suitable as a Rev5 commitment | Select connector style and route a stackup-specific 90 ohm pair |

## Critical-routing decisions

- A/SPI0 uses a Top-layer corridor. SCLK, MOSI, and CS have no vias. A MISO
  changes to Bottom once after the ESP-side source resistor.
- B/SPI1 uses Top/Bottom for SCLK and MOSI, Bottom for MISO, and a short
  Inner1 segment for the long CS route. Inner2/L3 is the continuous reference.
- All serialized SPI routes are point-to-point. Signal probe pads sit on the
  route rather than on branches.
- Every critical route has a nearby GND probe pad and signal transitions have
  local ground access for probing/return stitching.
- Inner2/L3 is an uninterrupted board-shaped GND plane. Inner1/L2 is
  GND-dominant but carries local OE buses and B-CS, both referenced to L3.
- Antenna cutouts are intentionally absent because both radios are disabled.
- All sixteen GPIO lanes transition to Bottom at their source-side 100 kΩ
  pull-downs, fan out monotonically, use clearance-checked doglegs where a
  bottom-row HCT input sits behind an OE pin, and transition at the input pads.
- HCT OE buses use Inner1 outside the package pad rows with individual branches;
  they do not cross the input vias. All sixteen driver-to-68 Ω output stubs are
  short Top routes.
- The script removes every V4 track and via before adding the reviewed routes,
  so no selector branch or shared receiver bus survives by geometry.

## Automated checks passed

- EasyEDA PCB document type and four copper-layer configuration.
- Authoritative Pi header, receiver A, and receiver B pad-net mapping.
- Receiver A/B translations and receiver B buffer-facing orientation.
- L2 ground-dominant and L3 uninterrupted GND copper areas; no antenna cutout.
- Eight SPI lengths below their 75/90 mm draft budgets.
- Up to two signal vias only on the B-CS and B-MISO routes; zero/one elsewhere.
- Sixteen serialized ESP-to-HCT routes and sixteen output-to-damping routes.
- Fifty-four critical nets proven end-to-end.
- No different-net track intersection, at least 0.635 mm track-center
  separation, and at least 0.160 mm conservative different-net copper-edge
  clearance across pad/pad, track/pad, via/pad, track/via, and via/via checks.
- Removal of V4 selector and obsolete/blocking footprints.

## Checks that still require EasyEDA and approved mechanics

The generator is not a substitute for native EDA checks. Before fabrication,
run EasyEDA DRC and visually inspect pad/track clearance,
copper-area refill, thermals, return paths, board-edge clearance, mask slivers,
silkscreen, courtyards, and every moved footprint. Then complete the missing
schematic and placement sections, run ERC, check 3D fit, and review fresh
Gerber/drill output. Until those steps pass, this artifact remains a routing
study rather than a manufacturing release.
