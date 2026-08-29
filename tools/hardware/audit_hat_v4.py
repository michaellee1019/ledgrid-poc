#!/usr/bin/env python3
"""Audit the LED Grid Wall HAT V4 design exports without live hardware.

The audit intentionally uses only Python's standard library. It extracts only
fabrication facts that Gerber and Excellon can prove, then uses the separately
supplied EDA Standard JSON source for named-net route metrics. Keeping those
evidence classes separate prevents schematic intent from being mistaken for
fabrication proof.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import struct
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


GERBER_SCALE = 100_000  # EasyEDA archive uses 4.5 coordinates in millimetres.
EDA_UNIT_MM = 0.254  # EDA Standard PCB coordinates use 10 mil drawing units.

SPI_NETS = {
    "1IO10": ("CS for 1_SMD", "CS", 10),
    "2IO10": ("CS for 2_SMD", "CS", 10),
    "1IO11": ("shared by 1_SMD and 2_SMD", "MOSI", 11),
    "1IO12": ("shared by 1_SMD and 2_SMD", "SCLK", 12),
    "1IO13": ("shared by 1_SMD and 2_SMD", "MISO", 13),
}


@dataclass(frozen=True, order=True)
class Point:
    x: int
    y: int

    def millimetres(self) -> tuple[float, float]:
        return self.x / GERBER_SCALE, self.y / GERBER_SCALE


@dataclass(frozen=True)
class Segment:
    start: Point
    end: Point
    aperture: int | None
    width_mm: float | None

    @property
    def length_mm(self) -> float:
        return math.hypot(self.end.x - self.start.x, self.end.y - self.start.y) / GERBER_SCALE


@dataclass
class GerberLayer:
    name: str
    apertures: dict[int, dict[str, object]] = field(default_factory=dict)
    segments: list[Segment] = field(default_factory=list)
    flashes: list[tuple[Point, int | None]] = field(default_factory=list)
    regions: int = 0
    coordinate_places: int = 5


@dataclass
class DrillFile:
    name: str
    tools_mm: dict[int, float] = field(default_factory=dict)
    hits: list[tuple[Point, int]] = field(default_factory=list)


def _parse_fixed(raw: str, decimal_places: int) -> int:
    sign = -1 if raw.startswith("-") else 1
    digits = raw.lstrip("+-")
    value_at_source_scale = sign * int(digits)
    if decimal_places == 5:
        return value_at_source_scale
    return round(value_at_source_scale * GERBER_SCALE / (10**decimal_places))


def parse_gerber(name: str, text: str) -> GerberLayer:
    layer = GerberLayer(name=name)
    current = Point(0, 0)
    current_aperture: int | None = None
    current_operation: int | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        format_match = re.fullmatch(r"%FSLAX(\d)(\d)Y(\d)(\d)\*%", line)
        if format_match:
            x_places = int(format_match.group(2))
            y_places = int(format_match.group(4))
            if x_places != y_places:
                raise ValueError(f"{name}: differing X/Y decimal formats are unsupported")
            layer.coordinate_places = x_places
            continue

        aperture_match = re.fullmatch(r"%ADD(\d+)([A-Z0-9_]+),?([^*]*)\*%", line)
        if aperture_match:
            code = int(aperture_match.group(1))
            modifiers = aperture_match.group(3)
            first_number = re.search(r"[-+]?\d+(?:\.\d+)?", modifiers)
            layer.apertures[code] = {
                "shape": aperture_match.group(2),
                "modifiers": modifiers,
                "first_dimension_mm": float(first_number.group(0)) if first_number else None,
            }
            continue

        if line == "G36*":
            layer.regions += 1
            continue

        aperture_select = re.fullmatch(r"D(\d+)\*", line)
        if aperture_select and int(aperture_select.group(1)) >= 10:
            current_aperture = int(aperture_select.group(1))
            continue

        if not (line.startswith("X") or line.startswith("Y")):
            continue

        coordinate_match = re.fullmatch(
            r"(?:X([+-]?\d+))?(?:Y([+-]?\d+))?"
            r"(?:I([+-]?\d+))?(?:J([+-]?\d+))?(?:D0?([123]))?\*",
            line,
        )
        if not coordinate_match:
            continue

        x_raw, y_raw, _i_raw, _j_raw, operation_raw = coordinate_match.groups()
        next_point = Point(
            _parse_fixed(x_raw, layer.coordinate_places) if x_raw is not None else current.x,
            _parse_fixed(y_raw, layer.coordinate_places) if y_raw is not None else current.y,
        )
        if operation_raw is not None:
            current_operation = int(operation_raw)

        if current_operation == 1:
            aperture = layer.apertures.get(current_aperture or -1, {})
            width = aperture.get("first_dimension_mm")
            layer.segments.append(
                Segment(
                    start=current,
                    end=next_point,
                    aperture=current_aperture,
                    width_mm=float(width) if width is not None else None,
                )
            )
        elif current_operation == 3:
            layer.flashes.append((next_point, current_aperture))

        current = next_point

    return layer


def parse_excellon(name: str, text: str) -> DrillFile:
    drill = DrillFile(name=name)
    decimal_places = 3
    current_tool: int | None = None
    in_header = True

    for raw_line in text.splitlines():
        line = raw_line.strip()
        format_match = re.fullmatch(r";FILE_FORMAT=(\d):(\d)", line)
        if format_match:
            decimal_places = int(format_match.group(2))
            continue
        tool_definition = re.fullmatch(r"T(\d+)C(\d+(?:\.\d+)?)", line)
        if tool_definition:
            drill.tools_mm[int(tool_definition.group(1))] = float(tool_definition.group(2))
            continue
        if line == "%":
            in_header = False
            continue
        tool_select = re.fullmatch(r"T(\d+)", line)
        if tool_select and not in_header:
            current_tool = int(tool_select.group(1))
            continue
        coordinate_match = re.fullmatch(r"X([+-]?\d+)Y([+-]?\d+)", line)
        if coordinate_match and current_tool is not None:
            drill.hits.append(
                (
                    Point(
                        _parse_fixed(coordinate_match.group(1), decimal_places),
                        _parse_fixed(coordinate_match.group(2), decimal_places),
                    ),
                    current_tool,
                )
            )
    return drill


def png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as source:
        header = source.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValueError(f"{path} is not a PNG with an IHDR header")
    return struct.unpack(">II", header[16:24])


def _component_ref_and_pads(shape: str) -> tuple[str | None, list[dict[str, object]]]:
    sections = shape.split("#@$")
    refdes = None
    pads: list[dict[str, object]] = []
    for section in sections:
        fields = section.split("~")
        if fields[0] == "TEXT" and len(fields) > 10 and fields[1] == "P" and refdes is None:
            refdes = fields[10]
        elif fields[0] == "PAD" and len(fields) > 8:
            try:
                pads.append(
                    {
                        "x": float(fields[2]),
                        "y": float(fields[3]),
                        "layer": int(fields[6]),
                        "net": fields[7],
                        "pin": fields[8],
                    }
                )
            except ValueError:
                continue
    return refdes, pads


def _node(layer: int, x: float, y: float) -> tuple[int, float, float]:
    # EDA exports occasionally differ by 0.001 drawing units at a pad/track join.
    return layer, round(x, 2), round(y, 2)


def _route_metric(
    net: str,
    tracks: list[dict[str, object]],
    vias: list[dict[str, object]],
    pads: list[dict[str, object]],
) -> dict[str, object]:
    net_tracks = [track for track in tracks if track["net"] == net]
    net_vias = [via for via in vias if via["net"] == net]
    layer_lengths: dict[int, float] = {1: 0.0, 2: 0.0}
    total_length = 0.0
    for track in net_tracks:
        coordinates = track["coordinates"]
        assert isinstance(coordinates, list)
        layer = int(track["layer"])
        for left_xy, right_xy in zip(coordinates, coordinates[1:]):
            length = math.hypot(right_xy[0] - left_xy[0], right_xy[1] - left_xy[1]) * EDA_UNIT_MM
            layer_lengths[layer] = layer_lengths.get(layer, 0.0) + length
            total_length += length

    pad_records = [
        {"refdes": str(pad["refdes"]), "pin": str(pad["pin"]), "layer": int(pad["layer"])}
        for pad in pads
        if pad["net"] == net
    ]
    selector_refs = sorted({item["refdes"] for item in pad_records if item["refdes"].startswith("SJ_")})
    selector_options = []
    for refdes in selector_refs:
        peer_nets = sorted(
            {
                str(pad["net"])
                for pad in pads
                if pad["refdes"] == refdes and pad["net"] != net
            }
        )
        selector_options.append({"refdes": refdes, "other_pad_nets": peer_nets})
    receiver_refs = sorted(
        {item["refdes"] for item in pad_records if item["refdes"] in {"1_SMD", "2_SMD"}}
    )
    return {
        "source_track_count": len(net_tracks),
        "source_via_count": len(net_vias),
        "source_pad_count": len(pad_records),
        "source_pads": sorted(pad_records, key=lambda item: (item["refdes"], item["pin"])),
        "track_widths_mm": sorted({round(float(track["width"]) * EDA_UNIT_MM, 4) for track in net_tracks}),
        "total_routed_copper_mm": round(total_length, 3),
        "layer_routed_copper_mm": {
            "top": round(layer_lengths.get(1, 0.0), 3),
            "bottom": round(layer_lengths.get(2, 0.0), 3),
        },
        "receiver_refs_on_net": receiver_refs,
        "shared_between_receivers": len(receiver_refs) > 1,
        "selector_options": selector_options,
    }


def parse_eda_sources(pcb_json: Path, schematic_json: Path) -> dict[str, object]:
    pcb_bytes = pcb_json.read_bytes()
    pcb = json.loads(pcb_bytes)
    schematic_bytes = schematic_json.read_bytes()
    schematic_export = json.loads(schematic_bytes)
    schematic_payload = schematic_export["schematics"][0]["dataStr"]
    schematic = json.loads(schematic_payload) if isinstance(schematic_payload, str) else schematic_payload

    tracks: list[dict[str, object]] = []
    vias: list[dict[str, object]] = []
    pads: list[dict[str, object]] = []
    component_refs: list[str] = []
    shape_counts: dict[str, int] = {}
    for shape in pcb["shape"]:
        shape_type = shape.split("~", 1)[0]
        shape_counts[shape_type] = shape_counts.get(shape_type, 0) + 1
        fields = shape.split("~")
        if shape_type == "TRACK" and len(fields) > 5:
            raw_coordinates = [float(value) for value in fields[4].split()]
            tracks.append(
                {
                    "width": float(fields[1]),
                    "layer": int(fields[2]),
                    "net": fields[3],
                    "coordinates": list(zip(raw_coordinates[0::2], raw_coordinates[1::2])),
                }
            )
        elif shape_type == "VIA" and len(fields) > 5:
            vias.append({"x": float(fields[1]), "y": float(fields[2]), "net": fields[4]})
        elif shape_type == "LIB":
            refdes, component_pads = _component_ref_and_pads(shape)
            if refdes:
                component_refs.append(refdes)
                for pad in component_pads:
                    pad["refdes"] = refdes
                    pads.append(pad)

    schematic_refs = []
    for shape in schematic["shape"]:
        if shape.startswith("LIB~"):
            for section in shape.split("#@$"):
                fields = section.split("~")
                if fields[0] == "T" and len(fields) > 12 and fields[1] == "P":
                    schematic_refs.append(fields[12])
                    break

    routes = []
    for net, (role, signal, esp_gpio) in SPI_NETS.items():
        route = {
            "net": net,
            "v4_role": role,
            "signal": signal,
            "esp_gpio": esp_gpio,
        }
        route.update(_route_metric(net, tracks, vias, pads))
        routes.append(route)

    return {
        "pcb_source": pcb_json.name,
        "pcb_sha256": hashlib.sha256(pcb_bytes).hexdigest(),
        "schematic_source": schematic_json.name,
        "schematic_sha256": hashlib.sha256(schematic_bytes).hexdigest(),
        "editor_version": pcb["head"].get("editorVersion"),
        "pcb_shape_counts": dict(sorted(shape_counts.items())),
        "pcb_component_count": len(component_refs),
        "schematic_component_count": len(schematic_refs),
        "router_rule": pcb.get("routerRule"),
        "spi_routes": routes,
    }


EXPECTED_MEMBERS = {
    "Drill_PTH_Through.DRL",
    "Drill_PTH_Through_Via.DRL",
    "Gerber_TopLayer.GTL",
    "Gerber_BottomLayer.GBL",
    "Gerber_TopSilkscreenLayer.GTO",
    "Gerber_BottomSilkscreenLayer.GBO",
    "Gerber_TopPasteMaskLayer.GTP",
    "Gerber_TopSolderMaskLayer.GTS",
    "Gerber_BottomSolderMaskLayer.GBS",
    "Gerber_BoardOutlineLayer.GKO",
    "Gerber_DocumentLayer.GDL",
    "How-to-order-PCB.txt",
}


def audit(
    gerber_archive: Path,
    schematic_png: Path,
    pcb_json: Path,
    schematic_json: Path,
) -> dict[str, object]:
    archive_bytes = gerber_archive.read_bytes()
    with zipfile.ZipFile(gerber_archive) as archive:
        members = set(archive.namelist())
        text_files = {
            name: archive.read(name).decode("utf-8", errors="replace")
            for name in members
        }

    top = parse_gerber("top copper", text_files["Gerber_TopLayer.GTL"])
    bottom = parse_gerber("bottom copper", text_files["Gerber_BottomLayer.GBL"])
    outline = parse_gerber("board outline", text_files["Gerber_BoardOutlineLayer.GKO"])
    pth = parse_excellon("plated through", text_files["Drill_PTH_Through.DRL"])
    vias = parse_excellon("vias", text_files["Drill_PTH_Through_Via.DRL"])

    outline_points = [
        point
        for segment in outline.segments
        for point in (segment.start, segment.end)
    ]
    min_x = min(point.x for point in outline_points)
    max_x = max(point.x for point in outline_points)
    min_y = min(point.y for point in outline_points)
    max_y = max(point.y for point in outline_points)

    pth_points = {point for point, _tool in pth.hits}
    via_points = {point for point, _tool in vias.hits}
    widths = sorted(
        {
            round(segment.width_mm, 4)
            for layer in (top, bottom)
            for segment in layer.segments
            if segment.width_mm is not None
        }
    )
    copper_members = sorted(
        name for name in members if name.endswith((".GTL", ".GBL", ".G1", ".G2"))
    )

    return {
        "inputs": {
            "gerber_archive": gerber_archive.name,
            "gerber_sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "schematic_png": schematic_png.name,
            "schematic_sha256": hashlib.sha256(schematic_png.read_bytes()).hexdigest(),
            "schematic_pixels": png_dimensions(schematic_png),
            "archive_members": sorted(members),
            "missing_expected_members": sorted(EXPECTED_MEMBERS - members),
            "unexpected_members": sorted(members - EXPECTED_MEMBERS),
        },
        "eda_source": parse_eda_sources(pcb_json, schematic_json),
        "fabrication_facts": {
            "eda_generator": "EasyEDA v6.5.57 (from Gerber comments)",
            "copper_layers": copper_members,
            "copper_layer_count": len(copper_members),
            "board_outline_centerline_bounds_mm": {
                "min_x": min_x / GERBER_SCALE,
                "max_x": max_x / GERBER_SCALE,
                "min_y": min_y / GERBER_SCALE,
                "max_y": max_y / GERBER_SCALE,
                "width": (max_x - min_x) / GERBER_SCALE,
                "height": (max_y - min_y) / GERBER_SCALE,
            },
            "track_aperture_widths_mm": widths,
            "top_copper_regions": top.regions,
            "bottom_copper_regions": bottom.regions,
            "top_draw_segments": len(top.segments),
            "bottom_draw_segments": len(bottom.segments),
            "top_flashes": len(top.flashes),
            "bottom_flashes": len(bottom.flashes),
            "via_drill_hits": len(vias.hits),
            "all_plated_drill_hits": len(pth.hits),
            "via_drill_diameters_mm": sorted({vias.tools_mm[tool] for _point, tool in vias.hits}),
            "plated_drill_diameters_mm": sorted({pth.tools_mm[tool] for _point, tool in pth.hits}),
            "via_file_is_subset_of_all_plated_file": via_points <= pth_points,
            "non_via_plated_holes": len(pth_points - via_points),
        },
        "limitations": [
            "Gerbers contain no net names, component identities, design rules, or stackup.",
            "Gerber facts and EDA-source facts are reported separately; named-net metrics come only from the JSON source.",
            "The supplied schematic PNG is a visual cross-check and is not used to derive automated net connectivity.",
            "This audit parses tracks, vias, pads, and metadata but is not a replacement for EDA Standard ERC/DRC.",
            "No revised PCB is released because connector, enclosure, fab stackup, and mechanical choices remain open.",
        ],
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gerbers", required=True, type=Path)
    parser.add_argument("--schematic", required=True, type=Path)
    parser.add_argument("--pcb-json", required=True, type=Path)
    parser.add_argument("--schematic-json", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    result = audit(args.gerbers, args.schematic, args.pcb_json, args.schematic_json)
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
