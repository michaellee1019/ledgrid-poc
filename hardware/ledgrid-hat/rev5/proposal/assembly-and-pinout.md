# Assembly, labelling, and connector requirements

## Silkscreen

The assembled HAT must visibly identify:

- `LED GRID HAT REV5 — LOGIC POWER ONLY`
- `A / SPI0 / CE0` beside receiver A
- `B / SPI1 / CE0` beside receiver B
- `CN1: DATA/GND PAIRS — NO LED POWER`
- Pin 1 and connector keying on the Pi header, CN1, and both USB connectors
- Test points as `A_CLK`, `A_MOSI`, `A_MISO`, `A_CS`, `A_3V3`, `A_5V`,
  `A_GND`, and the corresponding `B_` labels
- Resistor option labels sufficient to distinguish SPI damping from LED-output
  damping during rework

Keep silkscreen out of exposed copper. Put a schematic mapping table on the
back silkscreen if it fits without reducing legibility:

```text
A SPI0: P19 MOSI | P21 MISO | P23 CLK | P24 CS
B SPI1: P38 MOSI | P35 MISO | P40 CLK | P12 CS
ESP: GPIO11 MOSI | GPIO13 MISO | GPIO12 CLK | GPIO10 CS
```

## CN1 LED connector

The proposal uses a keyed 34-position 2.54 mm four-wall header candidate,
`3M 30334-6002HB`. The mating connector/cable part must be selected before
layout release. Pin assignment is authoritative in `connector-pinout.csv`:
odd pins 1–31 are LED1–LED16, each followed by its dedicated ground on the next
even pin; pins 33 and 34 are additional grounds. There is no power pin.

Keep each signal and its paired return adjacent through the mating cable. Use
twisted signal/ground pairs where the cable system permits. Do not combine the
grounds only at the remote LED end; every ground pin connects through a short
via cluster to both HAT ground planes immediately at CN1.

## Test-point access

Provide at least 22 labelled access points:

- Eight SPI signal test points: four per receiver, after the source resistor
- Eight dedicated nearby SPI ground points: one within 5 mm of each signal point
- Three rail/reference points per receiver: 3.3 V, 5 V, and ground

Signal points should be miniature loop or probe-compatible pads accessible with
the HAT installed. They must not create branches longer than 2 mm; the preferred
implementation is an inline exposed pad or a pad immediately adjacent to the
routed trace.

## Assembly review

- Confirm polarity/orientation for both ESP modules, buck ICs, ESD arrays, USB
  connectors, and any fuse/load switch.
- AOI the WSON exposed pad and verify the thermal/ground via pattern against the
  assembly supplier's stencil process.
- Inspect all HCT bypass capacitors, ESP entry capacitors, and buck input/output
  capacitors for correct placement, not merely correct net connection.
- Confirm the production configuration keeps Wi-Fi/Bluetooth disabled. If RF
  is reintroduced, stop and perform a new placement/keepout/mechanical review.
- Fit-check the HAT on the selected Pi with the final spacers, USB plugs, and
  mating LED cable before ordering more than engineering prototypes.
