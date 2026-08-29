#!/usr/bin/env python3
"""Render a compact SVG placement/routing review from an EasyEDA PCB JSON."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path


KEY_COMPONENTS = {
    "1_SMD": ("Receiver B / SPI1", "#93c5fd"),
    "2_SMD": ("Receiver A / SPI0", "#fca5a5"),
    "TRAN1_A": ("B buffer", "#bfdbfe"),
    "TRAN1_B": ("B buffer", "#bfdbfe"),
    "TRAN2_A": ("A buffer", "#fecaca"),
    "TRAN2_B": ("A buffer", "#fecaca"),
}


def component_ref_and_pads(shape: str) -> tuple[str | None, list[tuple[float, float]]]:
    refdes = None
    pads = []
    for section in shape.split("#@$"):
        fields = section.split("~")
        if len(fields) > 10 and fields[0] == "TEXT" and fields[1] == "P" and refdes is None:
            refdes = fields[10]
        elif len(fields) > 8 and fields[0] == "PAD":
            pads.append((float(fields[2]), float(fields[3])))
    return refdes, pads


def points(value: str) -> str:
    numbers = value.split()
    return " ".join(
        f"{numbers[index]},{numbers[index + 1]}" for index in range(0, len(numbers), 2)
    )


def render(board: dict[str, object]) -> str:
    shapes = board.get("shape")
    if not isinstance(shapes, list):
        raise ValueError("PCB has no shape array")
    content = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="3990 3324 285 240">',
        "<defs>",
        '<pattern id="keepout" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">',
        '<rect width="2" height="4" fill="#ef4444" opacity="0.35"/></pattern>',
        "</defs>",
        '<rect x="3990" y="3324" width="285" height="240" fill="#101827"/>',
        '<rect x="4008.7205" y="3332.7517" width="255.9055" height="222.4407" rx="11.811" '
        'fill="#17352d" stroke="#e5e7eb" stroke-width="1"/>',
        '<text x="4012" y="3329" fill="#f9fafb" font-size="5" font-family="sans-serif">Rev5 analyzed routing scaffold — NOT FOR FABRICATION</text>',
    ]

    for shape in shapes:
        fields = str(shape).split("~")
        if fields[0] == "RECT" and len(fields) > 6 and fields[5] == "12":
            content.append(
                f'<rect x="{fields[1]}" y="{fields[2]}" width="{fields[3]}" height="{fields[4]}" '
                'fill="#f59e0b" fill-opacity="0.08" stroke="#fbbf24" stroke-width="0.7" stroke-dasharray="3 2"/>'
            )
        elif fields[0] == "SOLIDREGION" and len(fields) > 4 and fields[4] == "cutout" and fields[1] == "1":
            content.append(f'<path d="{html.escape(fields[3])}" fill="url(#keepout)" stroke="#ef4444" stroke-width="0.5"/>')

    for shape in shapes:
        fields = str(shape).split("~")
        if fields[0] != "TRACK" or len(fields) < 6 or fields[2] not in {"1", "2", "21"}:
            continue
        net = fields[3]
        if net.startswith("A_"):
            color = "#fb7185"
        elif net.startswith("B_"):
            color = "#60a5fa"
        elif net == "GND":
            color = "#94a3b8"
        elif net == "5V":
            color = "#facc15"
        elif net.startswith("LED"):
            color = "#34d399"
        else:
            continue
        dash = (' stroke-dasharray="2 1"' if fields[2] == "2" else
                ' stroke-dasharray="4 1 1 1"' if fields[2] == "21" else "")
        content.append(
            f'<polyline points="{points(fields[4])}" fill="none" stroke="{color}" '
            f'stroke-width="{max(float(fields[1]), 0.8)}" stroke-linecap="round" stroke-linejoin="round"{dash}/>'
        )

    for shape in shapes:
        if not str(shape).startswith("LIB~"):
            continue
        refdes, pads = component_ref_and_pads(str(shape))
        if not refdes or not pads:
            continue
        if refdes in KEY_COMPONENTS:
            label, color = KEY_COMPONENTS[refdes]
            margin = 2.0
            min_x, max_x = min(x for x, _ in pads) - margin, max(x for x, _ in pads) + margin
            min_y, max_y = min(y for _, y in pads) - margin, max(y for _, y in pads) + margin
            content.append(
                f'<rect x="{min_x}" y="{min_y}" width="{max_x-min_x}" height="{max_y-min_y}" '
                f'fill="{color}" fill-opacity="0.13" stroke="{color}" stroke-width="0.6"/>'
            )
            content.append(
                f'<text x="{min_x+1}" y="{min_y+5}" fill="{color}" font-size="4" font-family="sans-serif">{html.escape(label)}</text>'
            )
        elif refdes.startswith(("R_A", "R_B", "R_LED", "TP_A", "TP_B", "C_A", "C_B", "Q_A", "Q_B")):
            x = sum(point[0] for point in pads) / len(pads)
            y = sum(point[1] for point in pads) / len(pads)
            color = ("#34d399" if refdes.startswith("R_LED") else
                     "#fda4af" if "_A" in refdes else "#93c5fd")
            content.append(f'<circle cx="{x}" cy="{y}" r="1.7" fill="{color}" fill-opacity="0.75"/>')

    for shape in shapes:
        fields = str(shape).split("~")
        if fields[0] == "VIA" and fields[4].startswith(("A_", "B_")):
            content.append(f'<circle cx="{fields[1]}" cy="{fields[2]}" r="1.2" fill="none" stroke="#ffffff" stroke-width="0.5"/>')

    content.extend([
        '<g font-family="sans-serif" font-size="4">',
        '<rect x="4012" y="3540" width="5" height="2" fill="#fb7185"/><text x="4019" y="3542" fill="#f9fafb">Receiver A</text>',
        '<rect x="4052" y="3540" width="5" height="2" fill="#60a5fa"/><text x="4059" y="3542" fill="#f9fafb">Receiver B</text>',
        '<rect x="4092" y="3540" width="5" height="2" fill="#34d399"/><text x="4099" y="3542" fill="#f9fafb">buffer-to-damping</text>',
        '<text x="4154" y="3542" fill="#f9fafb">dashed = Bottom · dash-dot = Inner1</text>',
        '<rect x="4225" y="3540" width="5" height="2" fill="#f59e0b" fill-opacity="0.3" stroke="#fbbf24"/><text x="4232" y="3542" fill="#f9fafb">reservation</text>',
        "</g></svg>",
    ])
    return "\n".join(content) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pcb", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    board = json.loads(args.pcb.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(board), encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
