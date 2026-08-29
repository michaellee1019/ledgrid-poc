# LED Grid Wall HAT Rev5 EasyEDA analyzed routing scaffold

`PCB_LedGridWallHatRev5_SPI_SCAFFOLD.json` is an EasyEDA Standard PCB source
file. In EasyEDA Standard, use **File > Open > EasyEDA** and select the JSON.
The ZIP in this directory contains the same PCB source, the V4 schematic
reference, validation/analysis results, and these notes. This remains **NOT FOR
FABRICATION**: it is a routed and analyzed starting point, not a completed Rev5
schematic or a native EasyEDA DRC release.

## Implemented in the PCB source

- Receiver A is the physical left module (`2_SMD`) on SPI0 and owns the
  LED1-LED8 baseline nets.
- Receiver B is the physical right module (`1_SMD`) on SPI1 and owns the
  LED9-LED16 baseline nets.
- The Pi header and receiver pads use explicit A/SPI0 and B/SPI1 nets; all 14
  selector footprints and their branches are gone.
- Wi-Fi/Bluetooth are intentionally unused. Antenna copper cutouts are removed,
  and receiver B keeps its GPIO edge toward its buffer bank instead of rotating
  for RF clearance.
- Receiver A moves 11.176 mm upward, receiver B moves 4.064 mm upward, and the
  A HCT banks move 8.128 mm upward to open routed fanout/damping corridors.
- All eight SPI paths are point-to-point through 33 ohm source damping. All
  sixteen ESP GPIO4/5/6/7/15/16/17/18 paths are routed to HCT inputs.
- HCT gate assignments are permuted inside each package to make the physical
  fanout monotonic while preserving the original GPIO-to-LED mapping.
- Every HCT input has a 100 kohm pull-down. Each output reaches an adjacent
  68 ohm configurable damping resistor.
- HCT OE is fail-safe: 10 kohm to 5 V, a 2N7002 pull-down controlled by ESP
  GPIO8, and 100 kohm from the MOSFET gate to ground. Firmware must initialize
  all lane GPIOs low before asserting GPIO8.
- Each HCT package has 100 nF at VCC and each receiver group has 1 uF bulk.
  Each ESP entry has 10 uF + 1 uF + 100 nF and a 10 kohm/1 uF EN network.
- Critical signal width is 0.254 mm. Inner2/L3 is uninterrupted GND for Bottom
  routes. Inner1/L2 is GND-dominant with only local OE and B-CS routing.
- The stale V4 tracks, vias, regulator sections, USB breakouts, reset networks,
  output connector, selector graphics, and V0.4 board title are removed before
  the new review geometry is added.

## Draft SPI route metrics

These values are calculated from the serialized EasyEDA track centerlines.
They are review values, not released fabrication values.

| Signal | Length | Signal layers | Signal vias | Limit |
| --- | ---: | --- | ---: | ---: |
| A SCLK | 34.46 mm | Top | 0 | 75 mm |
| A MOSI | 36.65 mm | Top | 0 | 90 mm |
| A MISO | 35.78 mm | Top/Bottom | 1 | 90 mm |
| A CS | 45.37 mm | Top | 0 | 90 mm |
| B SCLK | 25.86 mm | Top/Bottom | 1 | 75 mm |
| B MOSI | 30.61 mm | Top/Bottom | 1 | 90 mm |
| B MISO | 32.03 mm | Bottom | 2 | 90 mm |
| B CS | 69.31 mm | Top/Bottom/Inner1 | 2 | 90 mm |

The sixteen ESP-to-HCT paths are 7.58–17.30 mm. The generator proves 54
critical named nets end-to-end. Minimum different-net track center separation
is 0.635 mm. Its conservative different-net copper edge result is 0.160 mm
against a 0.152 mm draft rule; that check includes pad/pad, track/pad,
via/pad, track/via, and via/via geometry.

## Placement reservations and remaining work

Document-layer rectangles reserve the following areas:

- `A_BUCK_RESERVED`: receiver A's TPS62162 power section.
- `B_BUCK_RESERVED`: receiver B's TPS62162 power section.
- `CN1_34POS_RESERVED`: the proposed data/ground-pair connector corridor.

This file remains **NOT FOR FABRICATION**. The following work is intentionally
not guessed from the supplied V4 sources:

- Capture and approve a complete Rev5 schematic that incorporates the drafted
  reset/enable and damping networks plus buck, USB-C, cable ESD, and CN1.
- Replace or approve the generator's review-only 0402 and probe-pad
  footprints against manufacturer land patterns and assembly requirements.
- Resolve the exact Pi, spacer, enclosure, USB connector, CN1 mating cable, and
  fabricator stackup.
- Place and route the buck power, USB, output-ESD-to-CN1, and final 5 V trunks
  without violating the critical corridors or L3 ground continuity.
- Reattach approved 3D models to the moved footprints.
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
- `plots/rev5-placement-routing-scaffold.svg` and `.png`: combined review of
  retained components, routed critical nets, and reserved placement areas.
- `plots/rev5-top-copper.*`, `rev5-inner1-copper.*`,
  `rev5-inner2-ground.*`, and `rev5-bottom-copper.*`: SVG and PNG views of each
  Rev5 copper layer. Inner-layer images include the serialized GND-plane fill.
- `LedGridWallHatRev5_SPI_SCAFFOLD_EasyEDA.zip`: transfer bundle.

The repository-root `hardware-increment.zip` additionally contains the supplied
V4 schematic PNG and PNG conversions of the V4 copper/silkscreen evidence plots
for visual comparison. That archive is intentionally ignored by Git.

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

The generator validates fixed pad nets, placement transforms, route lengths,
serialized critical-net connectivity, track/track separation, conservative
pad/track/via spacing, and the four-layer reference strategy. EasyEDA's
native DRC remains mandatory after import. The reproducible electrical and
harness sweeps are in `../analysis/` and are rebuilt with
`tools/hardware/analyze_hat_rev5_electrical.py`. They include SPI source
damping, all sixteen ESP-to-HCT lanes, the paired LED harness, stackup
sensitivity, buck-current/inductor sizing, power-drop, and decoupling estimates.

Per-layer SVGs are generated with `render_hat_rev5_scaffold.py --layer` using
`top`, `inner1`, `inner2`, or `bottom`; `rsvg-convert` produces the corresponding
PNG review images.
