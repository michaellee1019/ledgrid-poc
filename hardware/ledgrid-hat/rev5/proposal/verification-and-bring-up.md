# Rev5 verification and bring-up plan

No live hardware work is part of this design package. This plan is for a later
engineering prototype after all release gates pass.

## EDA verification before manufacturing

1. Run schematic ERC with zero unexplained errors. Explicitly inspect all power
   flags, output-enable states, regulator feedback/control pins, USB CC pins,
   ESD grounds, and unused ESP pins.
2. Cross-check every Pi header pin and ESP GPIO against the authoritative table
   in `architecture.md`. Independently review receiver A and receiver B.
3. Run PCB DRC with zero unexplained errors using fab-approved rules.
4. Inspect L2 alone and with each critical net highlighted. It must remain
   continuous under all SPI and USB routes and their via transitions.
5. Run a stub/topology review for all eight SPI nets. Each must have exactly one
   driver, one receiver, one inline series element at the driver, and no branch.
6. Verify every SPI signal test point is after the damping resistor and has a
   ground point within 5 mm without a long test-point branch.
7. Verify all sixteen LED resistors and signal/ground connector pairs, all HCT
   bypass parts, and all four optional LED ESD arrays.
8. Validate both buck sections against the TI reference schematic/layout,
   including component voltage/current ratings, capacitor DC-bias derating,
   switch-node size, and thermal vias.
9. Apply locked antenna keepouts to every copper, mask, paste, silk, courtyard,
   and component layer; inspect in 2D and 3D with Pi hardware and spacers.
10. Check USB pair impedance against the selected fab stack, D+/D− mismatch,
    connector shell/CC wiring, ESD current path, and return-via symmetry.
11. Inspect solder-mask slivers, annular rings, clearances, board-edge spacing,
    courtyard conflicts, assembly access, and connector insertion paths.
12. Export fresh Gerbers/drills, render every layer and drill map, and visually
    compare them to the source. Confirm no unintended copper islands.
13. Export the final routed-length/transition report into
    `spi-routing-budget.csv` and replace every `TBD` value.

## Electrical pre-power checks

- Confirm no short from 5 V to ground or either 3.3 V rail.
- Confirm receiver A and B 3.3 V rails are not accidentally tied after their
  buck outputs.
- Verify CN1 contains no 5 V or LED-strip power.
- Verify 3.3 V-only SPI continuity end-to-end through the intended resistors and
  absence of continuity to 5 V.
- Inspect and document all DNP/alternate resistor populations.

## Power-only qualification

Use a current-limited bench source for the engineering prototype before Pi
attachment. Power each section in a controlled fixture where the final design
permits it, then power the complete logic load.

- Record inrush and steady-state 5 V input current.
- Measure A_3V3 and B_3V3 DC value, startup monotonicity, ripple, and droop during
  simultaneous radio/load activity.
- Observe both ESP `EN` pins and confirm reliable power-on reset.
- Measure local ground movement between Pi-header ground, each ESP ground, and
  CN1 ground during worst-case LED data switching.
- Check buck IC and inductor temperature at worst-case ambient and sustained
  activity.

## SPI qualification — FEC is not acceptance

1. Begin at a low SPI rate with LED outputs physically unpowered or disabled.
2. Send fixed patterns (`00`, `FF`, `AA`, `55`, walking ones/zeros) and
   pseudorandom payloads. Require CRC-clean operation.
3. Sweep the clock through representative steps to 20 MHz in SPI mode 0.
4. At each rate, exercise these cases separately:
   - LED output engine off
   - black frames while output timing still switches
   - one LED lane active
   - staggered lane groups
   - all sixteen lanes switching with high-transition patterns
5. Use a qualification interval of at least 10 million transfers per case after
   thermal stabilization. Require zero CRC failures and zero FEC corrections.
6. Any FEC correction, retry, CRC growth, receiver reset, or unexplained status
   counter increment is an electrical qualification failure. FEC may remain for
   detection/field resilience but may not convert a failing run into a pass.

## Measurements at each receiver

Probe at the ESP-side test point with the dedicated short ground spring:

- SCLK: overshoot/undershoot, ringing, monotonic threshold crossing, and setup
  relation to MOSI/MISO
- MOSI and CS: edge quality and setup/hold at the receiver
- MISO: edge quality at both ESP source and Pi receiver if accessible
- Local 3.3 V and buffer 5 V: droop/ripple during radio and LED switching
- Local ground relative to Pi-header ground: transient movement during the same
  patterns

Record probe model/loading, bandwidth limit, source-resistor population, SPI
rate, payload, LED state, supply, Pi model, cable assembly, ambient temperature,
and firmware revision with every capture.

## Damping selection

Start with 33 Ω on SPI and 68 Ω on LED outputs. Evaluate only the approved
population options from the BOM. Choose the smallest source resistance that
produces a single clean threshold crossing with adequate rise/fall time at the
receiver under worst-case simultaneous switching. Update schematic defaults,
BOM, assembly drawing, and silkscreen option notes together.
