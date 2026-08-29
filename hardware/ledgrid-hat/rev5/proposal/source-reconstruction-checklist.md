# Editable-source reconstruction and release gates

Rev5 must be captured as a reviewed net-aware revision of the supplied EDA
Standard source. Gerber coordinates must not be patched. The PCB's shared SPI
nets must not be carried forward merely because they differ from the schematic.

## Information required before layout commitment

- Owner decision that the schematic's independent-bus intent is authoritative,
  plus approval to delete every SPI/CE selection jumper and rename the receiver
  sections A=SPI0 and B=SPI1.
- Original cloud project/history and managed libraries if they contain newer
  information than the exported EDA Standard JSON.
- Exact Raspberry Pi model(s), HAT spacer height, enclosure CAD, allowed board
  envelope, and USB/LED cable approach volumes.
- Exact CN1 mating connector and cable: keying, conductor gauge, pair/twist
  construction, length range, strain relief, enclosure egress, and ESD exposure.
- Selected PCB fabricator, four-layer stackup, copper weight, thickness, minimum
  geometry, controlled-impedance service, and assembly capabilities.
- Firmware confirmation of A=SPI0, B=SPI1, GPIO10–13 local mapping, LED lane
  ownership, and the GPIO8-controlled HCT output-enable sequence.
- Expected ambient/enclosure temperature and Pi 5 V current budget.

## Reconstruction procedure

1. Create the schematic hierarchy: Pi/power, receiver A, receiver B, LED output,
   USB A, USB B, and connectors/test points.
2. Enter the authoritative mapping from `architecture.md`; use explicit global
   net names rather than relying on graphic adjacency.
3. Use manufacturer symbols and footprints. Verify every pin number against the
   current datasheet, especially ESP module GPIOs, USB connector pins, WSON
   exposed pad, HCT enables, and ESD-array channel ordering.
4. Cross-check every reconstructed V4 function against both JSON sources and the
   schematic PNG. Record the known SPI schematic/PCB mismatch and every other
   ambiguous or intentionally changed net in the schematic review log.
5. Import the V4 outline and mounting holes, then dimension them in the new PCB.
   Compare the result against both the V4 outline and official Pi HAT mechanics.
6. Record Wi-Fi/Bluetooth as intentionally disabled. Do not add antenna
   keepouts; reopen placement and RF review if that requirement changes.
7. Place power sections first using regulator reference layouts, then Pi header,
   receivers, USB, HCT devices/output resistors, CN1, and accessible
   test points.
8. Route point-to-point SPI with an uninterrupted adjacent GND reference, then
   USB, power, and LED fanout. Preserve L3 as solid GND beneath Bottom/L2 routes;
   do not preserve V4 route shapes merely for visual similarity.
9. Fill planes/pours, inspect return paths, and run the verification checklist.

## Net-by-net review record required

The source review must explicitly sign off:

- Pi pins 12, 19, 21, 23, 24, 35, 38, and 40
- ESP GPIO4, 5, 6, 7, 8, 10, 11, 12, 13, 15, 16, 17, 18, 19, 20, `EN`,
  3.3 V, and ground on both modules
- Both buck inputs, switch nodes, feedback/control pins, outputs, and grounds
- All four HCT packages: A inputs, Y outputs, OE state, VCC, ground, bypass
- LED1–LED16 through resistors/ESD to the correct CN1 data pins and paired grounds
- Both USB connectors: D+/D−, CC pins, shield, ESD, resistor/capacitor options
- Every test point, DNP option, mounting hole, and chassis/shield connection

## Release artifacts and gates

Rev5 remains **NOT FOR FABRICATION** until all boxes below can be checked:

- [ ] Editable schematic and PCB source are committed.
- [ ] Schematic PDF/high-resolution plot is committed and visually reviewed.
- [ ] Component and footprint library provenance is recorded.
- [ ] ERC has zero unexplained findings.
- [ ] DRC has zero unexplained findings using fab-approved rules.
- [ ] Mechanical/3D fit is approved with the selected Pi, spacers, enclosure,
      USB plugs, and mating CN1 cable.
- [ ] Release firmware disables Wi-Fi/Bluetooth and RF performance is not a
      product requirement; otherwise a new RF layout review is complete.
- [ ] Power/thermal calculation is reviewed for worst-case load/ambient.
- [ ] USB impedance is resolved for the actual stackup.
- [x] Scaffold SPI routed lengths/transitions replace every `TBD` in the CSV.
- [ ] Final native-EasyEDA routing is remeasured after the remaining interfaces
      are completed.
- [ ] Complete exact BOM and approved alternates are released.
- [ ] PnP and both-side assembly drawings are generated and inspected.
- [ ] Gerber/drill package is regenerated from source and every layer rendered.
- [ ] Old/new copper plots or overlays are committed for review.
- [ ] An independent reviewer signs the pin/net/return-path checklist.

Only then may the proposal banner be replaced with a fabrication revision and
the bring-up plan be used on a prototype.
