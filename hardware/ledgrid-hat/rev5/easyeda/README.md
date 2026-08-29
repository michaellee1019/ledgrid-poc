# LED Grid Wall HAT Rev5 EasyEDA placement/routing scaffold

`PCB_LedGridWallHatRev5_SPI_SCAFFOLD.json` is an EasyEDA Standard PCB source
file. In EasyEDA Standard, use **File > Open > EasyEDA** and select the JSON.
The ZIP in this directory contains the same PCB source, the V4 schematic
reference, validation manifest, and these notes.

## Implemented in the PCB source

- Receiver A is the physical left module (`2_SMD`) on SPI0 and owns the
  LED1-LED8 baseline nets.
- Receiver B is the physical right module (`1_SMD`) on SPI1 and owns the
  LED9-LED16 baseline nets.
- The Pi header and receiver pads use explicit A/SPI0 and B/SPI1 nets; all 14
  selector footprints and their branches are gone.
- Receiver B is rotated 180 degrees so its PCB antenna faces the right board
  edge. Receiver A's antenna remains at the left edge.
- Copper cutouts cover both antenna areas on Top, Inner1, Inner2, and Bottom.
- Receiver A moves 4.064 mm upward and its two HCT buffers move 6.096 mm upward
  to clear the reserved 34-position output-connector corridor.
- Four copper layers are enabled. Inner1 contains a board-shaped GND copper
  area; Inner2 is reserved for power and low-speed routing.
- Eight 33 ohm 0402 source-resistor footprints, two 10 kohm CS pull-ups, eight
  in-line SPI probe pads, eight nearby ground pads, and four HCT bypass
  capacitors are placed.
- All eight critical SPI signals are routed point-to-point without branches or
  unused stubs. Every route is below the proposal's length limit and uses no
  more than one signal via.
- Receiver B's SPI routes use Bottom and transition once beside the receiver.
  This separates them from receiver A's Top-layer corridor. Inner2 must remain
  free of power islands beneath that corridor or receive an approved continuous
  reference treatment during final power layout.
- The stale V4 tracks, vias, regulator sections, USB breakouts, reset networks,
  output connector, selector graphics, and V0.4 board title are removed before
  the new review geometry is added.

## Draft SPI route metrics

These values are calculated from the serialized EasyEDA track centerlines.
They are review values, not released fabrication values.

| Signal | Length | Signal layers | Signal vias | Limit |
| --- | ---: | --- | ---: | ---: |
| A SCLK | 41.57 mm | Top | 0 | 75 mm |
| A MOSI | 43.77 mm | Top | 0 | 90 mm |
| A MISO | 43.32 mm | Top/Bottom | 1 | 90 mm |
| A CS | 50.40 mm | Top | 0 | 90 mm |
| B SCLK | 36.82 mm | Top/Bottom | 1 | 75 mm |
| B MOSI | 36.13 mm | Top/Bottom | 1 | 90 mm |
| B MISO | 61.04 mm | Top/Bottom | 1 | 90 mm |
| B CS | 36.22 mm | Top/Bottom | 1 | 90 mm |

The generator's minimum different-net track centerline separation is 0.508 mm
against 0.152 mm trace width and 0.152 mm nominal clearance. Source resistors
serve as deliberate same-layer crossovers where required; the crossing trace
runs through the clearance gap between resistor pads, not through copper.

## Placement reservations and remaining work

Document-layer rectangles reserve the following areas:

- `A_BUCK_RESERVED`: receiver A's TPS62162 power section.
- `B_BUCK_RESERVED`: receiver B's TPS62162 power section.
- `CN1_34POS_RESERVED`: the proposed data/ground-pair connector corridor.

This file remains **NOT FOR FABRICATION**. The following work is intentionally
not guessed from the supplied V4 sources:

- Capture and approve the complete Rev5 schematic, including reset/enable,
  buck, USB-C, LED output resistors, cable ESD, and 34-position connector.
- Replace or approve the generator's review-only 0402 and probe-pad
  footprints against manufacturer land patterns and assembly requirements.
- Resolve the exact Pi, spacer, enclosure, USB connector, CN1 mating cable, and
  fabricator stackup.
- Place and route the power, USB, and LED interfaces without violating the SPI
  corridors, L2 ground continuity, L3 reference requirements, antenna
  keepouts, or reserved connector corridor.
- Reattach approved 3D models to the moved/rotated footprints.
- Run EasyEDA ERC/DRC, inspect every plane fill and return path, and generate
  fresh Gerbers only after the full design and libraries are approved.

See [`placement-routing-review.md`](placement-routing-review.md) for the exact
placement decisions and unresolved checks.

## Files

- `PCB_LedGridWallHatRev5_SPI_SCAFFOLD.json`: importable placement/routing PCB
  scaffold.
- `SCH_LedGridWallHatV4_REFERENCE.json`: byte-for-byte V4 schematic export for
  comparison only; it is not the Rev5 schematic.
- `scaffold-manifest.json`: transformations, source hashes, route measurements,
  and validation results.
- `plots/rev5-placement-routing-scaffold.svg`: review rendering of the retained
  components, critical routes, antenna cutouts, and reserved placement areas.
- `LedGridWallHatRev5_SPI_SCAFFOLD_EasyEDA.zip`: transfer bundle.

## Rebuild

From the repository root:

```sh
python3 tools/hardware/build_hat_rev5_easyeda_scaffold.py \
  --pcb-input hardware/ledgrid-hat/rev4/reference/PCB_LedGridWallHatV4_2026-08-28.json \
  --pcb-output hardware/ledgrid-hat/rev5/easyeda/PCB_LedGridWallHatRev5_SPI_SCAFFOLD.json \
  --manifest-output hardware/ledgrid-hat/rev5/easyeda/scaffold-manifest.json \
  --schematic-input hardware/ledgrid-hat/rev4/reference/SCH_LedGridWallHatV4_2026-08-28.json \
  --schematic-reference-output hardware/ledgrid-hat/rev5/easyeda/SCH_LedGridWallHatV4_REFERENCE.json
```

The generator validates the fixed pad nets, placement transforms, critical
route lengths, signal-via counts, different-net track separation, four-layer
setup, L2 GND area, and antenna cutouts. EasyEDA's native DRC remains mandatory
after import.
