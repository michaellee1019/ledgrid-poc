# Proposed stackup and fabrication notes

These are source-capture requirements, not instructions for ordering the V4
archive or an unreleased Rev5 board.

## Stackup request

| Layer | Copper | Purpose |
| --- | --- | --- |
| L1 | 1 oz | Components, point-to-point SPI, USB pairs |
| L2 | 1 oz | Solid ground plane; no routing or splits |
| L3 | 1 oz | 5 V/3.3 V distribution and low-speed signals |
| L4 | 1 oz | Secondary signals, principally LED fanout |

- Nominal finished thickness: 1.6 mm unless the Pi mechanical stack requires a
  different thickness.
- Material: FR-4, Tg ≥150 °C.
- Finish: ENIG preferred for module/connector coplanarity and test-point life;
  lead-free HASL is acceptable only after assembly-flatness review.
- Solder mask: both sides. Silkscreen: both sides where it improves assembly.
- Ask the selected fabricator for its actual dielectric thickness/Dk and obtain
  90 Ω USB differential geometry from its field solver. Do not copy a generic
  width/spacing pair into the PCB.

## Source DRC targets

These conservative capture values must be reconciled with the selected fab:

- Minimum signal width/clearance: 0.15/0.15 mm.
- Preferred via: 0.30 mm finished drill, ≥0.60 mm pad.
- Minimum finished annular ring: 0.15 mm.
- Copper-to-board-edge: ≥0.30 mm, larger at antennas and connectors.
- Solder-mask sliver: ≥0.10 mm where the fab process supports it.
- Silkscreen-to-exposed-copper clearance: ≥0.15 mm.
- Do not tent testable vias that are explicitly used as ground spring points.

## Power copper

- Use L3 pours for 5 V and local 3.3 V, with L1/L4 pours only when they do not
  disrupt controlled return paths or antenna keepouts.
- Use multiple ground vias at the Pi power entry, each buck input/output return,
  each ESP supply entry, each HCT bypass group, each ESD array, and CN1 ground
  region.
- Avoid power neck-downs below 0.75 mm for each receiver branch and 1.0 mm for
  the shared 5 V entry unless IPC-2152 and fab-stack calculations justify a
  different value. Plane current capacity and connector ratings must be checked
  for 1.25 A logic-only budget.
- Keep buck switch nodes on L1, as small as the regulator reference layout
  permits, and keep copper off other layers beneath them except the permitted
  ground recommended by the regulator datasheet.

## Critical signal constraints

- SPI: single-ended impedance target approximately 50 Ω where stackup permits;
  consistent geometry and solid reference are more important than exact
  impedance at these short lengths.
- SCLK maximum length 75 mm; MOSI/MISO/CS maximum 90 mm.
- SPI layer transitions: zero preferred, one maximum with adjacent L2 return
  stitching. No branches/stubs.
- Keep SCLK at least 3× its trace width from other SPI nets and farther from LED
  outputs where placement permits.
- USB: 90 Ω ±10% differential, D+/D− length mismatch ≤0.5 mm, no stubs, no
  plane discontinuities.
- Do not route signals or planes in either module antenna keepout on any layer.

## Drill-file release rule

The V4 archive's `Drill_PTH_Through.DRL` already includes all 80 via hits;
`Drill_PTH_Through_Via.DRL` is a subset. Never send both as independent drill
operations without an explicit fab note. The new EDA project must generate a
single authoritative plated drill set or clearly documented mutually exclusive
PTH/via sets.

## Required manufacturing package after release gates pass

- Gerber X2 or IPC-2581 with four copper layers, masks, paste, silk, and outline
- Authoritative plated and non-plated drill files plus drill map
- Fabrication drawing with stackup, thickness, finish, impedance, tolerance,
  controlled-depth requirements (if any), and panel notes
- BOM with approved manufacturer parts and alternates
- Pick-and-place/centroid file and assembly drawings for both sides
- PDF schematic and high-resolution copper/keepout plots
- ERC/DRC reports, source-to-Gerber visual review, and final routed-length table

None of those Rev5 manufacturing outputs exist in this proposal.
