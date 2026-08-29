# Rev5 architecture and routing contract

This document is the net-aware contract for revising the Rev5 schematic and
PCB. It is not a substitute for the reviewed Rev5 EDA source still to be made
from the supplied V4 exports.

## System partition

[`rev5-architecture.svg`](rev5-architecture.svg) shows the required signal and
power boundaries. The Pi 5 V rail powers controller logic only. LED-strip power
must enter elsewhere and must never pass through the HAT connector, LED data
connector, or HCT buffers.

The two receiver sections are electrically symmetric except for their Pi SPI
controller. Use `A_` and `B_` net/refdes prefixes so the bus identity is obvious
in the schematic, PCB, BOM, test-point labels, and silkscreen.

## Authoritative SPI mapping

The GPIO alternate functions below are verified against the Raspberry Pi SPI
documentation. Both ESP32-S3 modules use the same local pin mapping.

| Receiver | Bus | Signal | Pi GPIO | Pi physical pin | Direction at Pi | ESP GPIO |
| --- | --- | --- | ---: | ---: | --- | ---: |
| A | SPI0 | MOSI | 10 | 19 | output | 11 |
| A | SPI0 | MISO | 9 | 21 | input | 13 |
| A | SPI0 | SCLK | 11 | 23 | output | 12 |
| A | SPI0 | CE0/CS | 8 | 24 | output | 10 |
| B | SPI1 | MOSI | 20 | 38 | output | 11 |
| B | SPI1 | MISO | 19 | 35 | input | 13 |
| B | SPI1 | SCLK | 21 | 40 | output | 12 |
| B | SPI1 | CE0/CS | 18 | 12 | output | 10 |

Schematic and silkscreen callouts must say `A / SPI0 / CE0` and
`B / SPI1 / CE0`; a bare `RX1`/`RX2` label is not sufficient. Both buses are
3.3 V only and operate in SPI mode 0 at up to 20 MHz.

### Point-to-point topology

- Route exactly one Pi driver to exactly one ESP receiver on SCLK, MOSI, and CS.
- Route exactly one ESP driver to exactly one Pi receiver on MISO.
- Do not fit the V4 SPI breakout headers. Do not leave test-header branches,
  via stubs, or alternate receiver footprints on the nets.
- Default series resistance is 33 Ω. Place A/B SCLK, MOSI, and CS resistors next
  to the Pi header breakout; place each MISO resistor immediately beside its ESP
  module pad.
- Place each signal test point after its source resistor. Put a dedicated ground
  test point within 5 mm so a ground spring can be used.
- Put a 10 kΩ CS pull-up at each ESP input, after the series resistor.

Final routed values must be written to `spi-routing-budget.csv` from the EDA
length-tuning report. Proposed hard limits are 75 mm for SCLK and 90 mm for
MOSI/MISO/CS, with zero signal-layer transitions preferred and one permitted
only when accompanied by ground-return stitching vias.

## Four-layer floorplan

1. **L1 — components and critical signals.** Keep each SPI bus on L1 from the Pi
   header through its source resistors to its receiver where possible. Keep USB
   pairs short on L1.
2. **L2 — uninterrupted ground.** No splits, slots, power islands, or routed
   traces beneath SPI, USB, module RF boundaries, or high-edge-rate LED data.
3. **L3 — power and low-speed.** Use separate wide 5 V feeds to the A and B buck
   sections and local 3.3 V pours. Do not route one receiver through the other's
   supply/ground path.
4. **L4 — secondary signals/components.** LED fanout may use L4 with a continuous
   L2 reference and spatial separation from SPI. Avoid routing beneath antennas.

Place the receivers so their antennas face different board edges and the
manufacturer keepout projects inward over no copper, trace, component, metal
hardware, or plane on any layer. Add the keepout as a locked rule area, not
only a silkscreen drawing. Maintain at least the module datasheet keepout and
check the additional enclosure clearance recommended by Espressif.

## Power architecture

### Input and branch budget

Budget each ESP 3.3 V rail for 500 mA continuous design capability. With two
loads at 500 mA and 90% conversion efficiency, the estimated 5 V draw for the
ESP sections is:

`2 × 3.3 V × 0.5 A / (5 V × 0.90) ≈ 0.73 A`

Allow 1.25 A at the Pi 5 V entry for both receiver sections, four HCT125
packages, transient/headroom, and USB logic. Confirm that allowance against the
selected Pi model and its upstream supply. Add a 1.5 A resettable fuse or
current-limited load switch only after verifying inrush and Pi header policy;
the proposal BOM leaves this as a design-review item.

### Per-receiver buck section

- `TPS62162DSGR`, fixed 3.3 V, rated 1 A.
- 2.2 µH shielded inductor selected for saturation and ripple current.
- 10 µF X5R/X7R input and 22 µF X5R/X7R output at the converter pins, rated at
  10 V so DC-bias derating is reviewable.
- Keep the switch node compact and entirely away from SPI, USB, antennas, and
  module edges. Follow the TI datasheet example layout before optimizing
  placement.
- At each module 3.3 V entry, add 10 µF bulk, 1 µF ceramic, and 0.1 µF ceramic
  with direct L2 ground vias. Keep the 0.1 µF loop shortest.
- Use separate L3 branches from the Pi 5 V entry to A and B; do not daisy-chain.

The AP2112 is not retained. At 500 mA it would dissipate about 0.85 W from 5 V,
before radio current peaks and ambient/enclosure effects.

### Reset and enable

For each ESP, use a 10 kΩ pull-up and 1 µF capacitor on `EN` as the starting
Espressif-recommended RC. Keep the reset button. Verify power-on/reset timing on
prototype units before release. `EN` must never float.

## LED translation and cable interface

- Keep four `SN74HCT125DR` devices at 5 V. The family accepts a minimum 2.0 V
  input high at 4.5–5.5 V, so a 3.3 V ESP high is valid.
- Put 0.1 µF at every HCT VCC pin pair and one 1 µF local bulk capacitor per two
  packages, with direct L2 ground vias.
- Keep output enables intentionally low through a documented 0 Ω link in the
  baseline. Do not leave them floating.
- Place one configurable output resistor at every HCT output pin; default 68 Ω,
  options 0/33/47/100 Ω.
- Route LED1–LED8 from receiver A and LED9–LED16 from receiver B unless the
  existing firmware/lane contract proves a different assignment before source
  capture. Label the assignment in schematic and silkscreen.
- Use CN1 as sixteen adjacent data/ground pairs plus two extra grounds. See
  `connector-pinout.csv`. Do not put 5 V or LED-strip power on CN1.
- Place four `TPD4E05U06DQAR` ESD-array footprints between the output resistors
  and CN1. Connect their grounds to L2 with the shortest possible path.
- Keep SPI and LED fanout in separate routing corridors. No long parallel runs;
  cross orthogonally on different layers if unavoidable.

## Native USB

Provide one native USB device port per ESP using GPIO19 as D− and GPIO20 as D+.
For each port:

- Place 22 Ω series footprints close to the ESP; retain 0 and 33 Ω tuning
  options.
- Place optional small shunt-capacitor footprints after the resistors, DNP by
  default.
- Place `TPD2EUSB30DRTR` next to the connector with a direct ground connection.
- Route a short, tightly coupled 90 Ω ±10% differential pair over solid L2.
- Match D+/D− within 0.5 mm, avoid stubs, and avoid layer changes. If a change is
  unavoidable, transition both lines together and add symmetric nearby ground
  vias.
- If a direct USB-C receptacle replaces the existing breakout, add the required
  USB-device CC pull-downs and validate connector shell grounding/ESD.

Final connector style and placement remain a mechanical release blocker.

## Mechanical contract

- Preserve the 64.99999 × 56.49999 mm V4 outline and the four mounting-hole
  centers recorded in the V4 evidence unless a reviewed 34-position connector
  fit study requires a documented change.
- Start from the official Raspberry Pi HAT mechanical drawing and verify the
  40-pin header, mounting holes, USB/network/PoE clearance, and 15–16 mm spacer
  recommendation against the chosen Pi.
- The proposed `30334-6002HB` connector is approximately the full board width;
  mating-cable strain relief, insertion access, and enclosure clearance must be
  demonstrated in 3D before footprint commitment.
- Keep all test points accessible with the HAT installed.
