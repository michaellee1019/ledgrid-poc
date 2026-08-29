#!/usr/bin/env python3
"""Run bounded electrical analyses for the LED Grid Wall HAT Rev5 scaffold.

The script intentionally uses documented envelopes instead of pretending that
the missing PCB stackup, Raspberry Pi IBIS model, ESP32-S3 IBIS model, LED type,
or cable construction are known.  It writes machine-readable sweeps and a
concise Markdown report that records every assumption.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Iterable


SPI_RESISTORS_OHM = (0.0, 22.0, 33.0, 47.0)
LED_RESISTORS_OHM = (33.0, 47.0, 68.0, 100.0)


def line_step(
    *,
    v_final: float,
    source_resistance: float,
    z0: float,
    delay_ns: float,
    rise_ns: float,
    load_cap_pf: float,
    duration_ns: float,
    line_resistance: float = 0.0,
) -> dict[str, float | None]:
    """Lossless-line bounce simulation with a capacitive receiver.

    The Bergeron travelling-wave formulation exactly represents the line delay
    and impedance.  Backward Euler integrates the shunt input capacitance.
    Small conductor loss is represented as a one-way wave attenuation.
    """
    if min(source_resistance, z0, delay_ns, rise_ns, load_cap_pf) <= 0:
        raise ValueError("line parameters must be positive")
    dt_ns = min(delay_ns / 96.0, rise_ns / 120.0)
    delay_steps = max(1, round(delay_ns / dt_ns))
    dt_ns = delay_ns / delay_steps
    count = math.ceil(duration_ns / dt_ns) + 1
    forward = [0.0] * count
    backward = [0.0] * count
    load_voltage = 0.0
    samples: list[tuple[float, float]] = []
    k = z0 * load_cap_pf * 1e-3 / dt_ns  # ohm*pF/ns is dimensionless
    attenuation = math.exp(-line_resistance / (2.0 * z0))

    for index in range(count):
        time_ns = index * dt_ns
        source_voltage = v_final * min(1.0, time_ns / rise_ns)
        if index >= delay_steps:
            arriving_forward = forward[index - delay_steps] * attenuation
            arriving_backward = backward[index - delay_steps] * attenuation
        else:
            arriving_forward = arriving_backward = 0.0

        # Thevenin source boundary: V = a+b and I = (a-b)/Z0.
        launched = (
            source_voltage * z0
            + arriving_backward * (source_resistance - z0)
        ) / (source_resistance + z0)
        forward[index] = launched

        # Capacitive load boundary, using backward-Euler charge integration.
        reflected = (
            arriving_forward * (1.0 - k) + k * load_voltage
        ) / (1.0 + k)
        load_voltage = arriving_forward + reflected
        backward[index] = reflected
        samples.append((time_ns, load_voltage))

    maximum = max(voltage for _, voltage in samples)
    minimum = min(voltage for _, voltage in samples)
    threshold_time = crossing_time(samples, 0.75 * v_final)
    rise_10 = crossing_time(samples, 0.1 * v_final)
    rise_90 = crossing_time(samples, 0.9 * v_final)
    settling = settling_time(samples, v_final, 0.1)
    return {
        "maximum_v": round(maximum, 4),
        "minimum_v": round(minimum, 4),
        "overshoot_pct": round(max(0.0, maximum / v_final - 1.0) * 100.0, 2),
        "threshold_75pct_ns": rounded(threshold_time),
        "rise_10_90_ns": rounded(None if rise_10 is None or rise_90 is None else rise_90 - rise_10),
        "settle_10pct_ns": rounded(settling),
    }


def crossing_time(samples: Iterable[tuple[float, float]], level: float) -> float | None:
    previous: tuple[float, float] | None = None
    for current in samples:
        if current[1] >= level:
            if previous is None or current[1] == previous[1]:
                return current[0]
            fraction = (level - previous[1]) / (current[1] - previous[1])
            return previous[0] + fraction * (current[0] - previous[0])
        previous = current
    return None


def settling_time(samples: list[tuple[float, float]], final: float, tolerance: float) -> float | None:
    low, high = final * (1.0 - tolerance), final * (1.0 + tolerance)
    last_bad = -1
    for index, (_, voltage) in enumerate(samples):
        if not low <= voltage <= high:
            last_bad = index
    next_index = last_bad + 1
    return samples[next_index][0] if next_index < len(samples) else None


def rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def microstrip_z0(width_mm: float, dielectric_height_mm: float, er: float) -> float:
    """Hammerstad-Jensen zero-thickness microstrip approximation."""
    ratio = width_mm / dielectric_height_mm
    effective_er = (er + 1.0) / 2.0 + (er - 1.0) / 2.0 / math.sqrt(1.0 + 12.0 / ratio)
    if ratio <= 1.0:
        impedance = 60.0 / math.sqrt(effective_er) * math.log(8.0 / ratio + ratio / 4.0)
    else:
        impedance = 120.0 * math.pi / (
            math.sqrt(effective_er) * (ratio + 1.393 + 0.667 * math.log(ratio + 1.444))
        )
    return impedance


def spi_sweep(route_lengths: dict[str, float]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for signal, length_mm in sorted(route_lengths.items()):
        delay_ns = length_mm / 165.0
        for resistor in SPI_RESISTORS_OHM:
            for corner, z0, driver, load, rise in (
                ("low_z_fast", 45.0, 15.0, 5.0, 0.8),
                ("nominal", 60.0, 30.0, 8.0, 1.5),
                ("high_z_fast", 85.0, 15.0, 8.0, 0.8),
                ("high_c_slow", 70.0, 45.0, 15.0, 3.0),
            ):
                result = line_step(
                    v_final=3.3,
                    source_resistance=driver + resistor,
                    z0=z0,
                    delay_ns=delay_ns,
                    rise_ns=rise,
                    load_cap_pf=load,
                    duration_ns=18.0,
                    line_resistance=0.15,
                )
                rows.append({
                    "signal": signal,
                    "length_mm": length_mm,
                    "corner": corner,
                    "z0_ohm": z0,
                    "driver_ohm": driver,
                    "series_ohm": resistor,
                    "load_pf": load,
                    "driver_rise_ns": rise,
                    **result,
                })
    return rows


def gpio_to_hct_sweep(route_lengths: dict[str, float]) -> list[dict[str, object]]:
    """Bound the short ESP-to-HCT traces with the populated 100 kΩ pull-down."""
    rows: list[dict[str, object]] = []
    for signal, length_mm in sorted(route_lengths.items()):
        delay_ns = length_mm / 165.0
        for corner, z0, driver, load, rise in (
            ("low_z_fast", 45.0, 15.0, 5.0, 0.8),
            ("nominal", 60.0, 30.0, 8.0, 1.5),
            ("high_z_fast", 85.0, 15.0, 10.0, 0.8),
            ("high_c_slow", 70.0, 45.0, 10.0, 3.0),
        ):
            result = line_step(
                v_final=3.3,
                source_resistance=driver,
                z0=z0,
                delay_ns=delay_ns,
                rise_ns=rise,
                load_cap_pf=load,
                duration_ns=12.0,
                line_resistance=0.05,
            )
            rows.append({
                "signal": signal, "length_mm": length_mm, "corner": corner,
                "z0_ohm": z0, "driver_ohm": driver, "series_ohm": 0.0,
                "load_pf": load, "driver_rise_ns": rise,
                "input_pulldown_ohm": 100000,
                "pulldown_dc_current_at_3p3_ua": 33.0,
                "pulldown_rc_at_10pf_us": 1.0,
                **result,
            })
    return rows


def summarize_gpio(rows: list[dict[str, object]]) -> dict[str, float | int | None]:
    return {
        "lane_count": len({str(row["signal"]) for row in rows}),
        "shortest_length_mm": min(float(row["length_mm"]) for row in rows),
        "longest_length_mm": max(float(row["length_mm"]) for row in rows),
        "worst_overshoot_pct": max(float(row["overshoot_pct"]) for row in rows),
        "latest_threshold_75pct_ns": max_or_none(row["threshold_75pct_ns"] for row in rows),
        "latest_settle_10pct_ns": max_or_none(row["settle_10pct_ns"] for row in rows),
        "pulldown_dc_current_at_3p3_ua": 33.0,
        "pulldown_worst_leakage_drop_v_at_1ua": 0.1,
    }


def led_harness_sweep() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for length_m in (0.25, 1.0, 3.0):
        for resistor in LED_RESISTORS_OHM:
            for corner, z0, driver, load, rise in (
                ("typical", 100.0, 33.0, 15.5, 8.0),
                ("low_z_fast", 80.0, 25.0, 10.5, 6.0),
                ("high_z", 120.0, 33.0, 30.5, 8.0),
                ("weak_driver_heavy_load", 100.0, 110.0, 50.5, 19.0),
            ):
                result = line_step(
                    v_final=4.5,
                    source_resistance=driver + resistor,
                    z0=z0,
                    delay_ns=5.0 * length_m,
                    rise_ns=rise,
                    load_cap_pf=load,
                    duration_ns=max(100.0, 5.0 * length_m + 80.0),
                    line_resistance=0.4 * length_m,
                )
                rows.append({
                    "length_m": length_m,
                    "corner": corner,
                    "z0_ohm": z0,
                    "driver_ohm": driver,
                    "series_ohm": resistor,
                    "load_pf_including_esd": load,
                    "driver_rise_ns": rise,
                    "threshold_v": 3.5,
                    **result,
                })
    return rows


def summarize_resistors(rows: list[dict[str, object]], key: str) -> dict[str, dict[str, float | None]]:
    output: dict[str, dict[str, float | None]] = {}
    values = sorted({float(row[key]) for row in rows})
    for value in values:
        selected = [row for row in rows if float(row[key]) == value]
        output[f"{value:g}"] = {
            "worst_overshoot_pct": max(float(row["overshoot_pct"]) for row in selected),
            "latest_threshold_75pct_ns": max_or_none(row["threshold_75pct_ns"] for row in selected),
            "latest_settle_10pct_ns": max_or_none(row["settle_10pct_ns"] for row in selected),
        }
    return output


def max_or_none(values: Iterable[object]) -> float | None:
    numbers = [float(value) for value in values if value is not None]
    return round(max(numbers), 3) if numbers else None


def power_analysis() -> dict[str, object]:
    vin = 5.25
    vout = 3.3
    l_min = 2.2e-6 * 0.8
    fs_min = 2.25e6 * 0.8
    ripple_a = vout * (1.0 - vout / vin) / (l_min * fs_min)
    peak_500ma = 0.5 + ripple_a / 2.0
    peak_1a = 1.0 + ripple_a / 2.0
    required_isat = peak_1a * 1.2
    dcr = 0.115
    branch_r = 1.724e-8 * 0.050 / (0.00075 * 35e-6)
    entry_r = 1.724e-8 * 0.050 / (0.001 * 35e-6)
    effective_local_cap = 8.0e-6 + 0.6e-6 + 0.08e-6
    return {
        "buck": {
            "vin_corner_v": vin,
            "vout_v": vout,
            "switching_frequency_corner_mhz": round(fs_min / 1e6, 3),
            "inductor_min_uh": round(l_min * 1e6, 3),
            "inductor_ripple_a_pp": round(ripple_a, 3),
            "peak_at_500ma_a": round(peak_500ma, 3),
            "peak_at_1a_a": round(peak_1a, 3),
            "minimum_isat_with_20pct_margin_a": round(required_isat, 3),
            "selected_inductor_rating_a": 1.8,
            "selected_inductor_dcr_ohm": dcr,
            "inductor_copper_loss_at_500ma_w": round(0.5**2 * dcr, 4),
            "inductor_copper_loss_at_1a_w": round(dcr, 4),
            "minimum_effective_cout_uf_release_gate": 10.0,
            "nominal_cout_uf": 22.0,
            "nominal_cin_uf": 10.0,
        },
        "distribution": {
            "per_receiver_5v_current_at_500ma_3v3_90pct_a": round(3.3 * 0.5 / (5.0 * 0.9), 3),
            "two_receiver_5v_current_a": round(2.0 * 3.3 * 0.5 / (5.0 * 0.9), 3),
            "logic_entry_budget_a": 1.25,
            "0p75mm_1oz_50mm_branch_resistance_ohm": round(branch_r, 4),
            "branch_drop_at_0p5a_mv": round(branch_r * 0.5 * 1000.0, 1),
            "1mm_1oz_50mm_entry_resistance_ohm": round(entry_r, 4),
            "entry_drop_at_1p25a_mv": round(entry_r * 1.25 * 1000.0, 1),
        },
        "local_decoupling": {
            "assumed_effective_10uf_uf": 8.0,
            "assumed_effective_1uf_uf": 0.6,
            "assumed_effective_100nf_uf": 0.08,
            "250ma_1us_step_droop_mv": round(0.25 * 1e-6 / effective_local_cap * 1000.0, 1),
            "note": "First-microsecond charge estimate only; converter loop and interconnect ESL require bench validation.",
        },
        "hct": {
            "vcc_min_v": 4.5,
            "vih_min_v": 2.0,
            "esp_high_nominal_v": 3.3,
            "voh_min_at_minus_6ma_v": 3.84,
            "input_capacitance_max_pf": 10.0,
            "transition_time_max_50pf_ns": 19.0,
            "bypass_per_package_uf": 0.1,
            "bulk_per_two_packages_uf": 1.0,
            "input_pulldown_ohm": 100000,
            "pulldown_worst_leakage_drop_v_at_1ua": 0.1,
        },
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def svg_plot(path: Path, spi_summary: dict[str, dict[str, float | None]],
             led_summary: dict[str, dict[str, float | None]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width, height = 940, 480
    margin_left, top, plot_height = 80, 70, 310
    spi_values = [(float(key), float(value["worst_overshoot_pct"] or 0.0)) for key, value in spi_summary.items()]
    led_values = [(float(key), float(value["worst_overshoot_pct"] or 0.0)) for key, value in led_summary.items()]
    ymax = max(10.0, *(value for _, value in spi_values + led_values)) * 1.15

    def points(values: list[tuple[float, float]], x0: float, span: float) -> str:
        xmin, xmax = min(x for x, _ in values), max(x for x, _ in values)
        return " ".join(
            f"{x0 + (x-xmin)/(xmax-xmin)*span:.1f},{top + plot_height*(1-y/ymax):.1f}"
            for x, y in values
        )

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#0f172a"/>',
        '<text x="40" y="35" fill="#f8fafc" font-size="24" font-family="sans-serif">Rev5 bounded source-damping sweep</text>',
        f'<line x1="{margin_left}" y1="{top+plot_height}" x2="{width-40}" y2="{top+plot_height}" stroke="#64748b"/>',
        f'<line x1="{margin_left}" y1="{top}" x2="{margin_left}" y2="{top+plot_height}" stroke="#64748b"/>',
    ]
    for index in range(6):
        value = ymax * index / 5
        y = top + plot_height * (1-index/5)
        lines.extend([
            f'<line x1="{margin_left}" y1="{y:.1f}" x2="{width-40}" y2="{y:.1f}" stroke="#334155"/>',
            f'<text x="10" y="{y+5:.1f}" fill="#cbd5e1" font-size="13" font-family="sans-serif">{value:.1f}%</text>',
        ])
    lines.extend([
        f'<polyline points="{points(spi_values, 110, 320)}" fill="none" stroke="#fb7185" stroke-width="4"/>',
        f'<polyline points="{points(led_values, 540, 320)}" fill="none" stroke="#60a5fa" stroke-width="4"/>',
        '<text x="180" y="420" fill="#fb7185" font-size="17" font-family="sans-serif">SPI series resistance (Ω)</text>',
        '<text x="620" y="420" fill="#60a5fa" font-size="17" font-family="sans-serif">LED series resistance (Ω)</text>',
    ])
    for values, x0, span, color in ((spi_values, 110, 320, "#fb7185"), (led_values, 540, 320, "#60a5fa")):
        xmin, xmax = min(x for x, _ in values), max(x for x, _ in values)
        for x, yvalue in values:
            px = x0 + (x-xmin)/(xmax-xmin)*span
            py = top + plot_height*(1-yvalue/ymax)
            lines.extend([
                f'<circle cx="{px:.1f}" cy="{py:.1f}" r="5" fill="{color}"/>',
                f'<text x="{px-9:.1f}" y="{top+plot_height+24}" fill="#e2e8f0" font-size="13" font-family="sans-serif">{x:g}</text>',
            ])
    lines.append('</svg>')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def report_text(
    *,
    spi_summary: dict[str, dict[str, float | None]],
    gpio_summary: dict[str, float | int | None],
    led_summary: dict[str, dict[str, float | None]],
    impedance: list[dict[str, float]],
    power: dict[str, object],
) -> str:
    def table(summary: dict[str, dict[str, float | None]]) -> str:
        rows = ["| Series R | Worst overshoot | Latest 75% crossing | Latest 10% settling |",
                "| ---: | ---: | ---: | ---: |"]
        for resistor, values in summary.items():
            rows.append(
                f"| {resistor} Ω | {values['worst_overshoot_pct']:.2f}% | "
                f"{values['latest_threshold_75pct_ns']:.3f} ns | "
                f"{values['latest_settle_10pct_ns']:.3f} ns |"
            )
        return "\n".join(rows)

    impedance_rows = ["| L1/L4-to-plane dielectric | Estimated Z0 at 0.254 mm |",
                      "| ---: | ---: |"]
    for item in impedance:
        impedance_rows.append(f"| {item['height_mm']:.2f} mm | {item['z0_ohm']:.1f} Ω |")

    buck = power["buck"]
    distribution = power["distribution"]
    return f"""# Rev5 electrical analysis and harness envelope

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

{table(spi_summary)}

The model uses the eight serialized route lengths, 3.3 V, 0.8–3 ns source
edges, 5–15 pF receiver loading, 45–85 Ω lines, and 15–45 Ω driver resistance.
The 75% crossing is deliberately more conservative than a typical CMOS input
threshold. No shunt capacitor is populated on SPI in the baseline.

## ESP-to-HCT lane sweep

All **{gpio_summary['lane_count']}** routed lanes are included. They span
**{gpio_summary['shortest_length_mm']:.2f}–{gpio_summary['longest_length_mm']:.2f} mm**;
with no series resistor, the bounded envelope gives
**{gpio_summary['worst_overshoot_pct']:.2f}%** worst overshoot,
**{gpio_summary['latest_threshold_75pct_ns']:.3f} ns** latest 75% crossing, and
**{gpio_summary['latest_settle_10pct_ns']:.3f} ns** latest 10% settling. These
short routes do not justify sixteen extra series parts in the baseline. The
100 kΩ input pull-down draws only 33 µA at 3.3 V and limits a 1 µA worst-case
leakage assumption to 0.1 V while the ESP is high-impedance.

## LED paired-harness sweep

{table(led_summary)}

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

{chr(10).join(impedance_rows)}

The fabricator must provide the actual width/spacing from its field solver.
Neither the 50 Ω SPI target nor the 90 Ω USB target can be released from a
generic 1.6 mm board description.

## Power and decoupling checks

- TPS62162 worst swept ripple current: **{buck['inductor_ripple_a_pp']:.3f} A p-p**.
- Calculated 1 A peak plus 20% margin: **{buck['minimum_isat_with_20pct_margin_a']:.3f} A**;
  selected 1.8 A inductor clears it.
- TI's 2.2 µH / 22 µF combination is retained, with **10 µF minimum effective
  output capacitance after bias** as a release gate and 10 µF at VIN.
- Two 500 mA ESP rails draw about **{distribution['two_receiver_5v_current_a']:.3f} A**
  from 5 V at 90% efficiency before HCT and margin; the 1.25 A logic-only entry
  budget remains appropriate.
- A 0.75 mm, 1 oz, 50 mm receiver branch is about
  **{distribution['0p75mm_1oz_50mm_branch_resistance_ohm']:.4f} Ω** and drops
  **{distribution['branch_drop_at_0p5a_mv']:.1f} mV** at 0.5 A. The shared entry
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
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    route_lengths = {
        name: float(values["length_mm"])
        for name, values in manifest["spi_route_metrics"].items()
    }
    gpio_route_lengths = {
        name: float(values["length_mm"])
        for name, values in manifest["esp_to_buffer_route_metrics"].items()
    }
    spi_rows = spi_sweep(route_lengths)
    gpio_rows = gpio_to_hct_sweep(gpio_route_lengths)
    led_rows = led_harness_sweep()
    spi_summary = summarize_resistors(spi_rows, "series_ohm")
    gpio_summary = summarize_gpio(gpio_rows)
    led_summary = summarize_resistors(led_rows, "series_ohm")
    impedance = [
        {"height_mm": height, "z0_ohm": round(microstrip_z0(0.254, height, 4.1), 2)}
        for height in (0.08, 0.10, 0.15, 0.20)
    ]
    power = power_analysis()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "spi-source-damping-sweep.csv", spi_rows)
    write_csv(args.output_dir / "esp-to-hct-sweep.csv", gpio_rows)
    write_csv(args.output_dir / "led-harness-sweep.csv", led_rows)
    summary = {
        "status": "BOUNDED POST-ROUTE ESTIMATE; BENCH AND NATIVE EDA SIGN-OFF REQUIRED",
        "model": "Bergeron lossless transmission line with capacitive load and bounded conductor loss",
        "spi_series_summary": spi_summary,
        "esp_to_hct_summary": gpio_summary,
        "led_series_summary": led_summary,
        "microstrip_impedance_sensitivity": impedance,
        "power": power,
        "assumptions": {
            "wifi": "disabled; no RF performance claim or antenna copper keepout",
            "spi": "3.3 V, 45-85 ohm Z0, 15-45 ohm driver, 5-15 pF load, 0.8-3 ns edge",
            "esp_to_hct": (
                f"3.3 V, {gpio_summary['shortest_length_mm']:.2f}-"
                f"{gpio_summary['longest_length_mm']:.2f} mm routes, 45-85 ohm Z0, "
                "5-10 pF HCT input, 100 kohm pull-down"
            ),
            "led_harness": "4.5 V, paired data/ground, 0.25-3 m, 80-120 ohm Z0, 10.5-50.5 pF load",
            "omitted": "package/connector discontinuities, frequency-dependent dielectric and conductor loss, IBIS clamps, crosstalk field solve",
        },
    }
    (args.output_dir / "electrical-analysis.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "electrical-analysis-report.md").write_text(
        report_text(spi_summary=spi_summary, gpio_summary=gpio_summary, led_summary=led_summary,
                    impedance=impedance, power=power),
        encoding="utf-8",
    )
    svg_plot(args.output_dir / "source-damping-envelope.svg", spi_summary, led_summary)
    print(f"wrote analysis to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
