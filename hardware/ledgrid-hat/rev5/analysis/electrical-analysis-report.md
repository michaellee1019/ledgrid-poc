# Rev5 electrical analysis and harness envelope

This is a bounded post-route estimate, not an IBIS sign-off. The supplied files
do not define the fabricator stackup, Raspberry Pi/ESP32 output models, final LED
device, or cable construction. The sweeps therefore cover the missing values
explicitly and keep prototype measurement as a release gate.

## Decisions supported by the analysis

- Keep **33 Ω** as the default for the eight on-board SPI source resistors, with
  22 Ω and 47 Ω tuning populations retained. It provides a useful mid-band
  source match across the 45–85 Ω line and 15–45 Ω driver envelopes without
  threatening a 25 ns SPI half-cycle.
- Increase critical-track width from 0.152 mm to **0.254 mm**. Exact impedance
  still depends on the selected stackup; consistency and a continuous reference
  are the release requirements.
- Keep Inner2/L3 as an uninterrupted GND plane for every Bottom SPI and GPIO
  route. Inner1/L2 is GND-dominant with only the slow OE buses and the long B_CS
  segment; B_CS is referenced to the solid L3 plane. Do not turn L3 into a
  fragmented power layer.
- Keep **68 Ω** as the LED-output baseline and retain 33/47/100 Ω options. This
  approximately source-matches a typical HCT driver plus a 100 Ω paired cable.
- Use a dedicated ground conductor beside every LED data conductor. The cable
  sweep covers 0.25–3 m, 80–120 Ω, 10.5–50.5 pF remote loading, and both typical
  and datasheet-derived weak HCT drive corners.
- Add 100 kΩ pull-downs to all HCT data inputs and make output-enable fail-safe:
  10 kΩ pull-up to 5 V, 2N7002 pull-down, and an ESP GPIO8 gate with a 100 kΩ
  gate pull-down. Firmware enables the buffers only after all lane GPIOs are
  initialized low. This blocks the documented GPIO18 power-up high glitch.
- Retain 100 nF immediately at every HCT VCC pin and 1 µF per two-package group.
  Retain 10 µF + 1 µF + 100 nF at each ESP module supply entry.

## SPI damping sweep

| Series R | Worst overshoot | Latest 75% crossing | Latest 10% settling |
| ---: | ---: | ---: | ---: |
| 0 Ω | 62.57% | 3.225 ns | 8.379 ns |
| 22 Ω | 28.32% | 3.651 ns | 4.538 ns |
| 33 Ω | 16.64% | 3.898 ns | 5.098 ns |
| 47 Ω | 5.73% | 4.267 ns | 5.811 ns |

The model uses the eight serialized route lengths, 3.3 V, 0.8–3 ns source
edges, 5–15 pF receiver loading, 45–85 Ω lines, and 15–45 Ω driver resistance.
The 75% crossing is deliberately more conservative than a typical CMOS input
threshold. No shunt capacitor is populated on SPI in the baseline.

## ESP-to-HCT lane sweep

All **16** routed lanes are included. They span
**7.58–17.30 mm**;
with no series resistor, the bounded envelope gives
**31.28%** worst overshoot,
**2.768 ns** latest 75% crossing, and
**3.232 ns** latest 10% settling. These
short routes do not justify sixteen extra series parts in the baseline. The
100 kΩ input pull-down draws only 33 µA at 3.3 V and limits a 1 µA worst-case
leakage assumption to 0.1 V while the ESP is high-impedance.

## LED paired-harness sweep

| Series R | Worst overshoot | Latest 75% crossing | Latest 10% settling |
| ---: | ---: | ---: | ---: |
| 33 Ω | 31.21% | 39.825 ns | 69.844 ns |
| 47 Ω | 21.20% | 44.983 ns | 73.906 ns |
| 68 Ω | 8.74% | 61.757 ns | 83.281 ns |
| 100 Ω | 0.00% | 68.030 ns | 57.083 ns |

The LED model uses a 4.5 V worst-case HCT supply, 6–19 ns source edges,
25–110 Ω effective output resistance, a 3.5 V conservative receiver threshold,
and the 0.5 pF ESD-array loading. Propagation delay is included. The result is
not a blanket approval of a 3 m harness: cable crosstalk, remote ground shift,
connector construction, and the exact first LED must still be measured.

For a paired cable, a deliberately broad 2–10% coupling estimate gives
approximately 0.10–0.50 V of quiet-line crosstalk for a 5 V edge, below a
conservative 1.5 V input-low limit. An unpaired ribbon or shared LED-power return
is outside this result and is prohibited.

## Stackup sensitivity

Zero-thickness Hammerstad estimates for FR-4 (Er=4.1):

| L1/L4-to-plane dielectric | Estimated Z0 at 0.254 mm |
| ---: | ---: |
| 0.08 mm | 37.4 Ω |
| 0.10 mm | 43.4 Ω |
| 0.15 mm | 55.7 Ω |
| 0.20 mm | 65.1 Ω |

The fabricator must provide the actual width/spacing from its field solver.
Neither the 50 Ω SPI target nor the 90 Ω USB target can be released from a
generic 1.6 mm board description.

## Power and decoupling checks

- TPS62162 worst swept ripple current: **0.387 A p-p**.
- Calculated 1 A peak plus 20% margin: **1.432 A**;
  selected 1.8 A inductor clears it.
- TI's 2.2 µH / 22 µF combination is retained, with **10 µF minimum effective
  output capacitance after bias** as a release gate and 10 µF at VIN.
- Two 500 mA ESP rails draw about **0.733 A**
  from 5 V at 90% efficiency before HCT and margin; the 1.25 A logic-only entry
  budget remains appropriate.
- A 0.75 mm, 1 oz, 50 mm receiver branch is about
  **0.0328 Ω** and drops
  **16.4 mV** at 0.5 A. The shared entry
  must be at least 1.0 mm or an equivalent pour.
- The 10 kΩ/1 µF EN network gives a 10 ms time constant, comfortably above
  Espressif's 50 µs minimum rail-stabilization interval; scope verification is
  still required with the final buck ramp.

## Required bench gates

1. Obtain the final stackup and rerun impedance/clearance checks.
2. Measure SPI at the receiver-side probe pads with 22/33/47 Ω populations and
   select the smallest clean value; acceptance is zero CRC/FEC corrections.
3. Test the actual LED cable at 0/33/47/68/100 Ω while all lanes toggle. Confirm
   VIH/VIL margin, overshoot, ringing, crosstalk, and ground movement at the
   first LED.
4. Measure both 3.3 V rails during Wi-Fi-disabled worst-case ESP processing and
   concurrent LED switching. Confirm no dip approaches 3.0 V.
5. Verify HCT outputs remain high-impedance through power-up/reset and enable
   only after firmware drives every lane low.
