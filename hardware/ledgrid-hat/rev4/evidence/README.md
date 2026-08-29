# V4 evidence and discrepancy report

## Inputs and provenance

| Input | SHA-256 |
| --- | --- |
| `Gerber_LedGridWallHatV4_LedGridWallHatV4_2026-08-28.zip` | `043b4bd539e6e9c3d89c5a95f3a45cd85fd3ebdad9a142121bf7bbce679f08c2` |
| `Schematic_LedGridWallHatV4_2026-08-28.png` | `8f3e7103ff76494aaec61f3f21cbb1f3bb4be1c9914f792f80f130c739151108` |
| `PCB_LedGridWallHatV4_2026-08-28.json` | `f2a4c7fe5d6d0049d4cf687849ca29969da905b71c6f73ca78dc18d3887e705a` |
| `SCH_LedGridWallHatV4_2026-08-28.json` | `c3682a5fb574e7799fffc0c24faba3f396fa8aa8a6d50f9074b48824c7e17821` |

The Gerbers and EDA Standard sources identify editor version 6.5.57 and carry an
export date of 2026-08-28. The PCB JSON has 45 component objects, 387 track
objects, and 80 via objects; the schematic JSON has 45 component objects. The
1265 × 1002 PNG remains useful as a visual cross-check.

## Existing-observation verification

| Observation from the plan | Result | Evidence or discrepancy |
| --- | --- | --- |
| About 65 × 56.5 mm | Verified | Outline centerline bounds are 64.99999 × 56.49999 mm. Four 2.751 mm plated mounting holes are centered at (0.635, −3.81), (58.635, −3.81), (0.635, −52.81), and (58.635, −52.81) mm in the V4 export coordinates. |
| Two copper layers | Verified | The archive contains one `.GTL` and one `.GBL`, with no internal copper files. |
| No copper-filled regions or ground plane | Verified | Both copper files contain zero Gerber `G36` regions. The plots show routed traces rather than a continuous plane. |
| Tracks are 0.254 mm / 10 mil, including power and ground | Verified at Gerber aperture level | Every drawn copper segment uses the 0.254 mm circular aperture. Pad flashes use separate apertures. |
| About 80 vias | Verified | The via drill file contains 80 hits at 0.306 mm. The combined plated drill file contains 154 hits, including the same 80 vias and 74 other plated holes. |
| Direct 3.3 V SPI with no source termination | Verified from schematic and editable PCB source | No series-damping components appear between the Pi header and ESP SPI nets. The PCB source contains selectable bus/CE solder jumpers and long branched routes. |
| ESP SPI is CS/MOSI/SCLK/MISO on GPIO10/11/12/13 | Verified from schematic | Both receiver sections use that mapping. Named-net continuity cannot be independently proven from Gerbers without netlist data. |
| Four 74HCT125 packages provide 16 outputs | Verified from schematic | `TRAN1_A`, `TRAN1_B`, `TRAN2_A`, and `TRAN2_B` are 74HCT125 devices powered from 5 V, with output enable tied low. |
| 16-pin LED connector has no local grounds | Verified from schematic | CN1 exposes LED1–LED16 only. |
| AP2112K-3.3 local regulators | Verified from schematic | One AP2112K-3.3 is used per ESP section. A 10 µF output capacitor is shown; the recommended input capacitor is not shown at either regulator. |
| One receiver on SPI0 and one on SPI1 | **Schematic intent verified; PCB contradicts it** | The schematic defines separate `1IO11/12/13` and `2IO11/12/13` nets, intending `1_SMD` on SPI1 and `2_SMD` on SPI0. The PCB source instead assigns both module pads to `1IO11`, `1IO12`, and `1IO13`, then exposes those shared nets through SPI0/SPI1 selection jumpers. Only `1IO10` and `2IO10` remain separate. This is a source-level design discrepancy, not just a naming issue. |

## Additional V4 findings

- The PCB's shared MOSI/SCLK/MISO nets create a real multidrop bus, not merely a
  visual branch concern. `1IO11` (MOSI) contains 85.993 mm of routed copper and
  5 vias; `1IO12` (SCLK) contains 87.805 mm and 4 vias; `1IO13` (MISO) contains
  82.378 mm and 2 vias. Each connects both ESP modules plus SPI0 and SPI1
  selector pads. Populating both MISO drivers on one shared net also creates a
  contention risk unless the unselected ESP reliably tri-states at all times.
- The separate CS nets are also heavily selectable: `1IO10` contains 100.400 mm
  and 4 vias; `2IO10` contains 81.241 mm and 6 vias. Each reaches four CE
  selection pads in addition to its ESP and pull-up.
- At a 500 mA ESP design load, an AP2112 dropping 5 V to 3.3 V dissipates
  approximately 0.85 W. Using the datasheet's 184 °C/W SOT-25 junction-to-ambient
  figure gives an idealized 156 °C rise before board-specific thermal relief;
  this is not an acceptable Rev5 power architecture.
- Each ESP `EN` network uses 10 kΩ and 0.1 µF. Espressif's usual starting value
  is 10 kΩ and 1 µF, so the timing must be updated and validated.
- The native USB D+/D− connections have no visible series-resistor, optional
  shunt-capacitor, or ESD footprints.
- One module antenna is near the board edge; the other is placed farther inside
  the outline. The archive has no explicit antenna-keepout layer, so clearance
  on every copper/component layer cannot be certified.
- The archive contains both a combined plated drill file and a via-only file.
  The latter is a strict subset. Fabrication instructions must identify the
  combined file as authoritative to prevent ambiguous double submission.

## Reproduce the machine audit

From the repository root:

```sh
python3 tools/hardware/audit_hat_v4.py \
  --gerbers hardware/ledgrid-hat/rev4/reference/Gerber_LedGridWallHatV4_LedGridWallHatV4_2026-08-28.zip \
  --schematic hardware/ledgrid-hat/rev4/reference/Schematic_LedGridWallHatV4_2026-08-28.png \
  --pcb-json hardware/ledgrid-hat/rev4/reference/PCB_LedGridWallHatV4_2026-08-28.json \
  --schematic-json hardware/ledgrid-hat/rev4/reference/SCH_LedGridWallHatV4_2026-08-28.json \
  --output hardware/ledgrid-hat/rev4/evidence/v4-audit.json
```

The JSON audit keeps fabrication facts and EDA-source facts separate. Gerber
copper still does not encode net names; named-net route lengths, pad membership,
and selector options come from the PCB JSON. The audit is not an EDA Standard
ERC/DRC implementation.

## Plots

The `plots/` directory contains flattened SVG views of top/bottom copper and
silkscreen generated from the supplied archive. These are review aids, not
fabrication outputs. A revised-copper overlay cannot honestly be produced until
the Rev5 board has been reconstructed and routed.
