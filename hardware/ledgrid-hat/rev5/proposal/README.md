# LED Grid Wall HAT Rev5 design proposal

> **NON-FABRICATION PROPOSAL — NOT RELEASED FOR PCB ORDERING**

This package resolves the architectural decisions in the hardware iteration
plan, but it deliberately stops before manufacturing output. V4 now includes
editable EDA Standard schematic and PCB exports. They disagree on the most
important nets: the schematic shows independent buses while the PCB shares
MOSI/SCLK/MISO between both modules. Rev5 therefore requires an explicit,
reviewed source revision rather than treating either V4 file as correct by
default.

## Verified defects in V4

- Two-layer construction with no continuous ground plane under SPI.
- One 0.254 mm routing width used for signals, 5 V, 3.3 V, and ground.
- SPI breakout branches, no series-damping footprints, and no dedicated
  oscilloscope ground points.
- The schematic intends independent receiver buses, but the PCB source connects
  both ESPs to shared MOSI/SCLK/MISO nets and selectable SPI0/SPI1 jumpers. This
  is a net-level source mismatch and a direct MISO-contention/stub risk.
- Sixteen LED data pins with no paired return pins; no output damping or cable
  ESD footprints.
- AP2112 linear conversion from 5 V to 3.3 V is thermally marginal at the
  ESP32-S3's recommended 500 mA supply capability, and the schematic omits the
  regulator's recommended local input capacitor.
- USB lacks tuning/protection footprints and a documented 90 Ω differential
  routing constraint.
- ESP antenna keepout compliance cannot be proven from the supplied layers.
- Receiver numbering and selectable bus/CE jumpers make population-dependent
  behavior ambiguous; no fixed A=SPI0/B=SPI1 contract exists in the PCB.

See the full observation-by-observation report in
[`../../rev4/evidence/README.md`](../../rev4/evidence/README.md).

## Changes specified for Rev5

- Receiver A is point-to-point on Raspberry Pi SPI0; receiver B is
  point-to-point on SPI1. Breakout branches are removed.
- The PCB becomes four layers: critical signals/components, uninterrupted
  ground, power/low-speed, then secondary signals/components.
- Eight configurable 33 Ω SPI source-damping resistors are located at the
  actual driver: Pi end for SCLK/MOSI/CS and ESP end for MISO.
- Four configurable 68 Ω output resistors per HCT group (sixteen total) are
  placed at the buffer pins.
- One signal test point plus a nearby ground point is provided for each SPI
  signal, per receiver, along with local 3.3 V, 5 V, and ground test points.
- Each AP2112 is replaced by a 1 A fixed-3.3 V buck section using
  `TPS62162DSGR`, with its datasheet-recommended topology and local ESP bulk and
  high-frequency bypassing.
- CN1 becomes a keyed 34-position connector with sixteen data/ground pairs and
  two additional grounds. The connector never carries LED-strip power.
- Optional four-channel low-capacitance ESD arrays protect LED cable lines.
- Each native USB port gains 22 Ω series tuning footprints, DNP shunt-capacitor
  footprints, a two-channel low-capacitance ESD array, and a 90 Ω differential
  routing requirement.
- Both module antennas must face a board edge with a full multilayer keepout.

The complete electrical/layout contract is in [`architecture.md`](architecture.md).
The exact V4-to-Rev5 SPI net edits are enumerated in
[`source-change-map.csv`](source-change-map.csv).

## Rationale

SPI reliability is governed by edge rate and return-path geometry, not only the
20 MHz clock period. Even though a 20 MHz period is 50 ns, modern GPIO edges are
fast enough that connector inductance, branches, layer transitions, and ground
movement can cause ringing and threshold recrossing. Point-to-point routing over
solid ground, source damping, low-inductance decoupling, and separated LED
return currents directly address the failure signature described in the plan.

At 1 oz copper, a representative 50 mm × 0.254 mm trace is roughly 0.10 Ω. A
500 mA supply path can therefore lose about 50 mV in one conductor before
connector/via/return losses; a similarly narrow round trip approaches 100 mV.
Rev5 uses planes/pours and wide neck-downs rather than treating power as another
10 mil signal.

## Remaining uncertainties and release blockers

1. The two editable V4 exports are not mutually consistent. The owner must
   confirm whether the schematic is authoritative and approve removal of every
   SPI bus/CE selection jumper in favor of the fixed Rev5 mapping.
2. Exact LED cable connector family, mating part, keying, cable length, wire
   gauge, enclosure egress, and pin-current requirements are unknown.
3. Raspberry Pi model, mechanical stack height, spacer height, enclosure
   clearance, and USB cable approach are unknown.
4. The intended PCB fabricator/stackup is unknown, so final USB geometry and
   single-ended trace impedance cannot be field-solved.
5. ESP module, connector, regulator, and inductor footprints require library
   validation against manufacturer drawings and real mating parts.
6. Firmware must explicitly enable SPI1 and use the Rev5 A/B naming before
   integration; software changes are outside this hardware-only package.
7. The V4 source geometry has been audited, but no Rev5 schematic ERC, PCB DRC,
   antenna-keepout rule check, routed-length report, 3D mechanical check, or
   Gerber-versus-source comparison has been run.

The exact reconstruction and release gates are listed in
[`source-reconstruction-checklist.md`](source-reconstruction-checklist.md).

## DNP and configuration options

- SPI resistors: default 33 Ω; approved engineering substitutions 0, 22, or
  47 Ω. DNP is permitted only for an isolated diagnostic build.
- LED-output resistors: default 68 Ω; approved engineering substitutions 0, 33,
  47, or 100 Ω. Final selection requires cable/edge measurements.
- LED ESD arrays may be DNP only for permanently internal, inaccessible cables;
  the footprints remain.
- USB shunt capacitors are DNP by default. Populate only after measured USB eye
  or emissions work establishes a value.
- USB series resistors default to 22 Ω; retain 0/33 Ω tuning options.
- HCT output-enable configuration remains deliberately always enabled in the
  baseline proposal with a 0 Ω link to ground. An alternate pull-up/controlled
  enable is allowed only with a reviewed level-safe control circuit and matching
  firmware behavior.

## Proposed deliverables present here

- Architecture diagram and electrical/layout requirements
- Candidate BOM with exact orderable part numbers and population status
- Explicit SPI and LED connector mappings
- Proposed stackup and fabrication requirements
- Assembly/test-point requirements
- Bring-up and no-FEC acceptance plan
- Source reconstruction and release checklist

Revised Rev5 EDA source, revised schematic plot, PnP, final routed-length values,
revised copper overlay, and Gerber/drill archive are intentionally absent until
the release blockers are resolved. The unchanged editable V4 sources are kept
under `../../rev4/reference/` for the reconstruction.
