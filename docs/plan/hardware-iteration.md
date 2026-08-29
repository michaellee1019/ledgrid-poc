# LED Grid Wall HAT hardware iteration prompt

You are reviewing and revising a Raspberry Pi HAT PCB for reliable high-speed SPI communication with two ESP32-S3 receivers. The attached inputs are:

- `Gerber_LedGridWallHatV4_LedGridWallHatV4_2026-08-28.zip`
- `Schematic_LedGridWallHatV4_2026-08-28.png`

Work only on the design files. Do not connect to, query, power, flash, or otherwise operate any live hardware.

## Product goal

The HAT connects a Raspberry Pi to two ESP32-S3-WROOM modules. Each ESP32 drives eight WS2812-compatible LED data lanes through two 74HCT125 quad buffers, for 16 LED outputs total. The Pi sends frames to the ESP32s using SPI mode 0 at up to 20 MHz. The intended design must make SPI electrically reliable without depending on forward-error correction during normal operation. CRC and telemetry may remain for detection, but corrected errors should be exceptional rather than expected.

A related installed five-receiver system has demonstrated severe route-specific corruption. At 20 MHz, one receiver produced 514 CRC failures in 5,016 transfers when LED outputs switched concurrently. Three-phase output staggering reduced this to 27 failures—a 94.7% reduction—but did not eliminate errors. Other receivers sharing the SPI controller were mostly clean. This strongly suggests switching noise, ground/reference movement, branch integrity, power integrity, or local coupling rather than ordinary random interference. Software work has escalated into interleaved Reed–Solomon FEC capable of correcting long structured bursts; the purpose of this PCB revision is to correct the underlying electrical weaknesses instead.

## Existing V4 observations to verify

Initial Gerber inspection indicates:

- Approximately 65 × 56.5 mm Raspberry Pi HAT outline.
- Two copper layers only.
- No copper-filled regions or continuous ground plane.
- Routed tracks appear to be 0.254 mm / 10 mil, including power and ground.
- Approximately 80 vias and numerous layer transitions.
- Direct 3.3 V SPI between Pi and ESP32s, with no source-termination footprints.
- ESP32 SPI pins are GPIO10–13:
  - CS: GPIO10
  - MOSI: GPIO11
  - SCLK: GPIO12
  - MISO: GPIO13
- Four 74HCT125 packages provide sixteen 5 V LED data outputs.
- The existing 16-pin LED connector appears to carry sixteen data signals with no adjacent ground-return pins.
- Local regulators appear to be AP2112K-3.3 devices.
- The matching schematic appears to connect one ESP32 to SPI0 and the other to SPI1, while a separate software description historically assumed both ESP32s were on SPI0 CE0/CE1. The revised design must remove this ambiguity.

Do not assume these observations are correct merely because they are stated here. Verify each against the supplied files and explicitly report any discrepancy.

## Required architectural decision

Use one independent Pi SPI bus per ESP32 unless the supplied design proves that this is mechanically or electrically impossible. This avoids multidrop clock/data stubs and MISO contention.

Preferred explicit mapping:

ESP32 A on Raspberry Pi SPI0:

- Pi GPIO10, physical pin 19: MOSI → ESP GPIO11
- Pi GPIO9, physical pin 21: MISO ← ESP GPIO13
- Pi GPIO11, physical pin 23: SCLK → ESP GPIO12
- Pi GPIO8, physical pin 24: CE0 → ESP GPIO10

ESP32 B on Raspberry Pi SPI1:

- Pi GPIO20, physical pin 38: MOSI → ESP GPIO11
- Pi GPIO19, physical pin 35: MISO ← ESP GPIO13
- Pi GPIO21, physical pin 40: SCLK → ESP GPIO12
- Pi GPIO18, physical pin 12: CE0 → ESP GPIO10

Verify the Raspberry Pi alternate-function assignments before committing the mapping. Put the final bus, chip-select, Pi GPIO, header-pin, and ESP GPIO mapping in the schematic, fabrication README, and silkscreen where practical. If a different mapping is necessary, explain why and provide the complete replacement mapping.

## PCB changes

Redesign this as a four-layer board unless a compelling fabrication constraint prevents it. Preferred stack:

1. Components and critical signals
2. Uninterrupted ground plane
3. Power distribution and low-speed signals
4. Secondary signals and components as needed

Follow these requirements:

- Maintain an uninterrupted ground reference under every SPI trace.
- Do not route SPI over plane gaps, voids, power islands, or board-edge discontinuities.
- Route each ESP’s SCLK, MOSI, MISO, and CS directly, without branches or unused stubs.
- Keep SPI primarily on one signal layer, minimize vias, and add nearby ground-stitching vias wherever a critical signal changes layers.
- Keep SCLK short and separated from LED-buffer outputs and connector fanout.
- Avoid long parallel runs between SPI signals and the sixteen 5 V LED outputs.
- Keep high-current or high-edge-rate LED return currents out of the Pi-to-ESP ground-reference path.
- Use substantially wider copper or planes for 5 V, 3.3 V, and ground. Size them from the expected logic, ESP32 radio-peak, and buffer currents rather than treating all nets as 10 mil signals.
- Preserve Raspberry Pi HAT mechanical dimensions, mounting holes, connector clearances, and keepouts unless a documented change is necessary.
- Observe the ESP32-S3-WROOM antenna keepout exactly. Place antennas at a board edge where possible, with no copper, traces, ground, components, or metal hardware in the prohibited region.
- Route USB D+/D− as a proper short differential pair over continuous ground, using the impedance and protection practices appropriate for ESP32-S3 native USB.
- Do not place 5 V logic on Pi-to-ESP SPI. Both sides of SPI are 3.3 V.

## Damping and testability

Add configurable series-damping footprints:

- SCLK, MOSI, and CS: resistor footprint at the Raspberry Pi source, per bus.
- MISO: resistor footprint immediately beside each ESP32, because the ESP32 drives MISO.
- Initial engineering range: approximately 22–47 Ω, but select the default population from calculated/estimated trace impedance and expected driver behavior.
- Make 0 Ω and DNP assembly options straightforward.
- Do not add arbitrary parallel termination or large capacitors to SPI lines.

Add labelled test points for each receiver:

- SCLK
- MOSI
- MISO
- CS
- Local 3.3 V
- Local 5 V
- Local ground

Every signal test point should have a nearby ground point suitable for a short oscilloscope ground spring. Ensure test points are accessible with the HAT assembled.

Retain or add a defined inactive state for each CS, normally a 10 kΩ pull-up near the ESP32. Review reset/enable pull-ups and RC values against Espressif recommendations.

## Power integrity and decoupling

Review the complete regulator and decoupling network against the exact component datasheets:

- Provide the AP2112-required input and output capacitors with short, direct ground returns.
- Place at least one close high-frequency ceramic capacitor at every ESP32 supply entry and every 74HCT125 package.
- Provide appropriate local bulk capacitance for each ESP32/regulator section and each LED-buffer group.
- Keep decoupling loops extremely small and connect them directly into the ground plane with nearby vias.
- Avoid daisy-chaining one ESP32’s supply or ground through the other receiver’s current path.
- Clearly document that Pi-header 5 V powers controller logic only unless the design is explicitly rated otherwise. It must not power the LED strips.

Do not choose capacitor values solely from generic convention. Check AP2112 stability requirements, ESP32-S3 hardware guidance, buffer transient demand, voltage derating, and package placement.

## LED output interface

The existing connector apparently provides sixteen single-ended data lines without local ground returns. Redesign the output interface so every LED signal has a short, unambiguous return path.

Preferred options, in order:

1. Data/ground pairs for every LED output.
2. At minimum, interspersed ground pins serving no more than one or two adjacent data outputs.
3. If the connector footprint cannot change, add a dedicated adjacent ground-return connector and document the required paired cable assembly.

Also:

- Add optional source-series resistor footprints at each 74HCT125 output, close to the driver. A likely experimental range is 33–100 Ω, but final values depend on cable impedance and measured edges.
- Keep each buffer’s bypass capacitor immediately adjacent to its supply pins.
- Verify 74HCT125 input thresholds are suitable for 3.3 V ESP32 outputs at the actual 5 V buffer supply.
- Do not replace HCT with a faster family without considering the additional EMI and ringing.
- Tie output-enable pins to a deliberate, documented state.
- Add ESD/protection footprints if LED cables leave the enclosure or are routinely handled.
- Publish the complete connector pinout, including all grounds and any changed keying.

## Source-file limitation

Gerbers are fabrication outputs, not a reliable net-aware editing format. First look for an editable EasyEDA/KiCad/Altium source in the supplied material. If no editable PCB source exists:

- State that limitation clearly.
- Reconstruct the schematic and PCB into an editable project rather than blindly patching coordinates in the Gerbers.
- Cross-check every reconstructed net against the schematic and Gerber connectivity.
- Do not claim net-level correctness solely from visual similarity.
- Identify any pin, footprint, package, or net whose identity cannot be proven.

## Verification

Before delivering:

- Run ERC and DRC with zero unexplained errors.
- Verify every Pi-header pin against the chosen SPI mapping.
- Verify every ESP32 SPI pin, CS pull-up, reset/enable network, USB pair, regulator, buffer, and LED connector net.
- Confirm the ground plane is continuous beneath all SPI routes.
- Inspect return paths for every critical signal and every layer transition.
- Confirm SPI traces have no stubs.
- Confirm antenna keepouts on every copper, mask, silkscreen, and component layer.
- Confirm resistor and test-point placement is at the correct driver end.
- Review solder-mask slivers, annular rings, clearances, via sizes, board-edge spacing, courtyard conflicts, assembly access, and HAT mechanical fit.
- Generate and visually inspect fresh Gerber plots and drill maps.
- Confirm there are no unintended copper islands.
- Estimate worst-case DC voltage drop and provide a qualitative power-integrity review.
- Provide an SPI signal-integrity rationale based on edge rate and return path, not only the nominal 20 MHz clock frequency.

## Deliverables

Return:

- Editable schematic and PCB source.
- Revised schematic PDF or high-resolution image.
- Complete Gerber and drill archive.
- BOM with exact manufacturer part numbers and population options.
- Pick-and-place file where applicable.
- Board stackup and fabrication notes.
- Assembly drawing and connector pinout.
- A concise change report organized as:
  - Verified defects in V4
  - Changes made
  - Rationale
  - Remaining uncertainties
  - DNP/configuration options
- Side-by-side or overlaid copper plots showing the old and revised SPI, ground, power, and LED-output routing.
- A table of final SPI trace lengths, layer transitions, series-resistor defaults, and test-point locations.
- A bring-up plan for later use that requires no FEC for acceptance:
  - Start at a low SPI rate.
  - Validate CRC-clean fixed and pseudorandom payloads.
  - Sweep to 20 MHz.
  - Exercise LED outputs off, black-but-switching, one lane, stagger groups, and all lanes.
  - Require zero CRC growth during the qualification interval.
  - Measure SCLK, MOSI, CS, 3.3 V, buffer 5 V, and local-ground movement at the ESP32.
  - Treat FEC corrections as a failed electrical qualification, not a successful result.

Do not conceal uncertainty. If mechanical constraints, unavailable source files, unknown connector requirements, or missing component data prevent a sound revision, stop at a documented design proposal and list the exact information needed rather than manufacturing an unjustified PCB.
