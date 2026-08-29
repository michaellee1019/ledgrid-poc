# LED Grid Wall HAT Rev5 EasyEDA scaffold

`PCB_LedGridWallHatRev5_SPI_SCAFFOLD.json` is an EasyEDA Standard PCB source
file. In EasyEDA Standard, use **File > Open > EasyEDA** and select the JSON
file. The ZIP in this directory contains the same source plus its reference and
validation files for convenient transfer.

## What is already changed

- Four copper routing layers are enabled: Top, `Inner1_GND`, `Inner2_PWR`, and
  Bottom.
- All 387 V4 tracks and all 80 V4 vias are deliberately removed. The unsafe V4
  shared-bus routing therefore cannot survive into this draft.
- All 14 SPI/CE selector footprints and their board labels are removed.
- The stale `V0.4` board-title silkscreen is removed rather than relabeling an
  unfinished design as Rev5.
- The Raspberry Pi header pads are assigned directly to two fixed SPI buses.
- Receiver B's module pads are corrected from the receiver-A nets to
  `2IO11`, `2IO12`, and `2IO13`.
- The V4 outline, mounting holes, component footprints, and placement remain as
  a mechanical starting point.

The fixed interface encoded in the PCB is:

| Receiver | Pi physical pin | Pi function | ESP32-S3 net |
| --- | ---: | --- | --- |
| A | 19 | SPI0 MOSI / GPIO10 | `1IO11` |
| A | 21 | SPI0 MISO / GPIO9 | `1IO13` |
| A | 23 | SPI0 SCLK / GPIO11 | `1IO12` |
| A | 24 | SPI0 CE0 / GPIO8 | `1IO10` |
| B | 38 | SPI1 MOSI / GPIO20 | `2IO11` |
| B | 35 | SPI1 MISO / GPIO19 | `2IO13` |
| B | 40 | SPI1 SCLK / GPIO21 | `2IO12` |
| B | 12 | SPI1 CE0 / GPIO18 | `2IO10` |

## Important limitation

This file is a **placement and net-review scaffold, not a fabrication-ready
Rev5 board**. It is intentionally unrouted. The V4 power and output circuitry
is retained only as a placement reference, and the V4 schematic reference
still contains the old selector topology.

Before fabrication, finish the Rev5 schematic and layout work described in
`../proposal/`, including the 5 V buck supply, series damping, test points,
revised output connector, USB/ESD changes, grounding, power distribution,
placement review, routing, pours, DRC, and generated-output review. Treat
`Inner1_GND` and `Inner2_PWR` as intended uses; this scaffold does not create
plane fills automatically.

## Files

- `PCB_LedGridWallHatRev5_SPI_SCAFFOLD.json`: importable EasyEDA Standard PCB
  scaffold.
- `SCH_LedGridWallHatV4_REFERENCE.json`: byte-for-byte V4 schematic export for
  comparison only; it is not the Rev5 schematic.
- `scaffold-manifest.json`: transformations, source hashes, and validation
  results.
- `LedGridWallHatRev5_SPI_SCAFFOLD_EasyEDA.zip`: transfer bundle containing the
  files above and this README.

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

The generator validates the EasyEDA document type, fixed SPI pad assignments,
selector removal, absence of routing, and four-layer configuration before
writing the manifest.
