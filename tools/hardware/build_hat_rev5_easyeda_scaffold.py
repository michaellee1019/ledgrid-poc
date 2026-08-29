#!/usr/bin/env python3
"""Build the EasyEDA Standard Rev5 placement/routing review scaffold.

The generated PCB is intentionally not fabrication-ready. It replaces the V4
SPI copper and unsafe placement with a bounded, machine-checked critical-route
draft while leaving unresolved buck, USB, output-protection, and connector
footprints as explicit placement reservations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path


EDA_UNIT_MM = 0.254
SIGNAL_WIDTH = 0.6  # 0.1524 mm
POWER_WIDTH = 1.2
MIN_TRACK_CENTRE_CLEARANCE = 1.2

SELECTOR_LABELS = {"SPI0", "SPI1", "CE0", "CE1", "CE2", "CE3"}
STALE_TITLES = {"LED GRID WALL HAT V0.4"}
STALE_RECTS = {
    (4062.501, 3361.5, 84.75, 16.25),
    (4158.001, 3361.5, 84.75, 16.25),
}
OBSOLETE_REFS = {
    "1_USB_BOB", "2_USB_BOB", "U3", "C1", "1R_CE_PULLUP",
    "2R_CE_PULLUP", "1B_RESET", "1C_RESET", "1R_RESET",
    "2B_RESET", "2C_RESET", "2R_RESET", "1C1", "1C2", "2C1",
    "2C2", "1_3V3_REG", "2_3V3_REG", "C_TRAN1_A",
    "C_TRAN1_B", "C_TRAN2_A", "C_TRAN2_B", "CN1", "SJ_VUSB",
}

PI_NETS = {
    "19": "A_SPI0_MOSI_PI", "21": "A_SPI0_MISO_PI",
    "23": "A_SPI0_SCLK_PI", "24": "A_SPI0_CS_PI",
    "38": "B_SPI1_MOSI_PI", "35": "B_SPI1_MISO_PI",
    "40": "B_SPI1_SCLK_PI", "12": "B_SPI1_CS_PI",
}

A_MODULE_NETS = {
    "1": "GND", "2": "A_3V3", "3": "A_EN", "4": "A_GPIO4",
    "5": "A_GPIO5", "6": "A_GPIO6", "7": "A_GPIO7",
    "8": "A_GPIO15", "9": "A_GPIO16", "10": "A_GPIO17",
    "11": "A_GPIO18", "13": "A_GPIO19", "14": "A_GPIO20",
    "18": "A_SPI0_CS", "19": "A_SPI0_MOSI", "20": "A_SPI0_SCLK",
    "21": "A_SPI0_MISO",
}
B_MODULE_NETS = {
    "1": "GND", "2": "B_3V3", "3": "B_EN", "4": "B_GPIO4",
    "5": "B_GPIO5", "6": "B_GPIO6", "7": "B_GPIO7",
    "8": "B_GPIO15", "9": "B_GPIO16", "10": "B_GPIO17",
    "11": "B_GPIO18", "13": "B_GPIO19", "14": "B_GPIO20",
    "18": "B_SPI1_CS", "19": "B_SPI1_MOSI", "20": "B_SPI1_SCLK",
    "21": "B_SPI1_MISO",
}
BUFFER_NETS = {
    "TRAN2_A": {"2": "A_GPIO18", "3": "LED1", "5": "A_GPIO17",
                "6": "LED2", "8": "LED3", "9": "A_GPIO16",
                "11": "LED4", "12": "A_GPIO15"},
    "TRAN2_B": {"2": "A_GPIO7", "3": "LED5", "5": "A_GPIO6",
                "6": "LED6", "8": "LED7", "9": "A_GPIO5",
                "11": "LED8", "12": "A_GPIO4"},
    "TRAN1_A": {"2": "B_GPIO18", "3": "LED9", "5": "B_GPIO17",
                "6": "LED10", "8": "LED11", "9": "B_GPIO16",
                "11": "LED12", "12": "B_GPIO15"},
    "TRAN1_B": {"2": "B_GPIO7", "3": "LED13", "5": "B_GPIO6",
                "6": "LED14", "8": "LED15", "9": "B_GPIO5",
                "11": "LED16", "12": "B_GPIO4"},
}

PLACEMENT_ZONES = {
    "A_BUCK_RESERVED": {"x": 4115.0, "y": 3476.0, "width": 25.0, "height": 24.0},
    "B_BUCK_RESERVED": {"x": 4142.0, "y": 3450.0, "width": 20.0, "height": 24.0},
    "CN1_34POS_RESERVED": {"x": 4045.0, "y": 3528.0, "width": 183.0, "height": 27.0},
}
ANTENNA_KEEPOUTS = {
    "A_LEFT_EDGE": (3992.0, 3425.0, 4017.0, 3495.0),
    "B_RIGHT_EDGE": (4240.0, 3401.0, 4265.0, 3471.0),
}
BOARD_PATH = (
    "M 4020.5315 3332.7517 L 4252.8149 3332.7517 "
    "A 11.811 11.811 0 0 1 4264.626 3344.5627 L 4264.626 3543.3816 "
    "A 11.811 11.811 0 0 1 4252.8149 3555.1924 L 4020.5315 3555.1924 "
    "A 11.811 11.811 0 0 1 4008.7205 3543.3816 L 4008.7205 3344.5627 "
    "A 11.811 11.811 0 0 1 4020.5315 3332.7517 Z"
)


def sid(label: str) -> str:
    return "gge" + hashlib.sha1(label.encode()).hexdigest()[:16]


def suuid(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()[:32]


def fmt(value: float) -> str:
    result = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if result in {"", "-0"} else result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def kind(shape: str) -> str:
    return shape.split("~", 1)[0]


def component_ref(shape: str) -> str | None:
    for section in shape.split("#@$"):
        fields = section.split("~")
        if len(fields) > 10 and fields[0] == "TEXT" and fields[1] == "P":
            return fields[10]
    return None


def text_value(shape: str) -> str | None:
    fields = shape.split("~")
    return fields[10] if len(fields) > 10 and fields[0] == "TEXT" else None


def rewrite_pads(shape: str, refdes: str, mapping: dict[str, str]) -> tuple[str, list[dict[str, str]]]:
    sections = shape.split("#@$")
    changes: list[dict[str, str]] = []
    for index, section in enumerate(sections):
        fields = section.split("~")
        if len(fields) <= 8 or fields[0] != "PAD" or fields[8] not in mapping:
            continue
        old_net, new_net = fields[7], mapping[fields[8]]
        if old_net != new_net:
            fields[7] = new_net
            sections[index] = "~".join(fields)
            changes.append({"refdes": refdes, "pad": fields[8], "old_net": old_net, "new_net": new_net})
    return "#@$".join(sections), changes


TOKEN = re.compile(r"[A-Za-z]|[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")


def transform_xy(x: float, y: float, cx: float, cy: float, angle: float, dx: float, dy: float) -> tuple[float, float]:
    radians = math.radians(angle)
    rx, ry = x - cx, y - cy
    return (cx + rx * math.cos(radians) - ry * math.sin(radians) + dx,
            cy + rx * math.sin(radians) + ry * math.cos(radians) + dy)


def transform_pairs(value: str, cx: float, cy: float, angle: float, dx: float, dy: float, sep: str = " ") -> str:
    numbers = [float(item) for item in re.findall(r"[-+]?\d+(?:\.\d+)?", value)]
    if len(numbers) % 2:
        raise ValueError(f"odd coordinate count in {value!r}")
    output: list[str] = []
    for x, y in zip(numbers[::2], numbers[1::2]):
        nx, ny = transform_xy(x, y, cx, cy, angle, dx, dy)
        output.extend([fmt(nx), fmt(ny)])
    return sep.join(output)


def transform_path(value: str, cx: float, cy: float, angle: float, dx: float, dy: float) -> str:
    tokens = TOKEN.findall(value)
    output: list[str] = []
    index = 0
    command: str | None = None
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index].upper()
            output.append(command)
            index += 1
            if command == "Z":
                command = None
            continue
        if command in {"M", "L"}:
            nx, ny = transform_xy(float(tokens[index]), float(tokens[index + 1]), cx, cy, angle, dx, dy)
            output.extend([fmt(nx), fmt(ny)])
            index += 2
            if command == "M":
                command = "L"
        elif command == "A":
            arc = [float(item) for item in tokens[index:index + 7]]
            nx, ny = transform_xy(arc[5], arc[6], cx, cy, angle, dx, dy)
            if angle % 180:
                arc[2] = (arc[2] + angle) % 360
            output.extend([fmt(item) for item in arc[:5]] + [fmt(nx), fmt(ny)])
            index += 7
        else:
            raise ValueError(f"unsupported path command {command!r}")
    return " ".join(output)


def transform_component(shape: str, *, angle: float = 0, dx: float = 0, dy: float = 0) -> str:
    header = shape.split("#@$", 1)[0].split("~")
    cx, cy = float(header[1]), float(header[2])
    output: list[str] = []
    for section in shape.split("#@$"):
        fields = section.split("~")
        section_kind = fields[0]
        if section_kind == "SVGNODE":
            continue  # Restore the STEP model from the approved library later.
        if section_kind == "LIB":
            fields[1], fields[2] = map(fmt, transform_xy(float(fields[1]), float(fields[2]), cx, cy, angle, dx, dy))
            fields[4] = fmt((float(fields[4] or 0) + angle) % 360)
        elif section_kind == "TEXT":
            fields[2], fields[3] = map(fmt, transform_xy(float(fields[2]), float(fields[3]), cx, cy, angle, dx, dy))
            fields[5] = fmt((float(fields[5] or 0) + angle) % 360)
            if len(fields) > 11 and fields[11]:
                fields[11] = transform_path(fields[11], cx, cy, angle, dx, dy)
        elif section_kind == "CIRCLE":
            fields[1], fields[2] = map(fmt, transform_xy(float(fields[1]), float(fields[2]), cx, cy, angle, dx, dy))
        elif section_kind == "SOLIDREGION":
            fields[3] = transform_path(fields[3], cx, cy, angle, dx, dy)
        elif section_kind == "TRACK":
            fields[4] = transform_pairs(fields[4], cx, cy, angle, dx, dy)
        elif section_kind == "PAD":
            fields[2], fields[3] = map(fmt, transform_xy(float(fields[2]), float(fields[3]), cx, cy, angle, dx, dy))
            if fields[10]:
                fields[10] = transform_pairs(fields[10], cx, cy, angle, dx, dy)
            fields[11] = fmt((float(fields[11] or 0) + angle) % 360)
            if len(fields) > 19 and fields[19]:
                fields[19] = transform_pairs(fields[19], cx, cy, angle, dx, dy, ",")
        output.append("~".join(fields))
    return "#@$".join(output)


def component_header(refdes: str, x: float, y: float, rotation: float, package: str, attrs: dict[str, str]) -> str:
    metadata = ["package", package]
    for key, value in attrs.items():
        metadata.extend([key, value])
    return "~".join(["LIB", fmt(x), fmt(y), "`".join(metadata) + "`", fmt(rotation), "",
                     sid(refdes + ":lib"), "1", suuid(refdes), "1787880000", "0", "", "yes", "", ""])


def component_text(refdes: str, x: float, y: float, value: str, layer: int = 3) -> list[str]:
    return [
        "~".join(["TEXT", "N", fmt(x), fmt(y - 4.5), "0.5", "0", "0", str(layer), "", "4",
                  value, "", "none", sid(refdes + ":value"), "", "0", ""]),
        "~".join(["TEXT", "P", fmt(x), fmt(y + 4.5), "0.5", "0", "0", str(layer), "", "4",
                  refdes, "", "", sid(refdes + ":ref"), "", "0", ""]),
    ]


def pad(label: str, x: float, y: float, width: float, height: float, layer: int,
        net: str, number: str, shape: str = "RECT", rotation: float = 0) -> str:
    return "~".join(["PAD", shape, fmt(x), fmt(y), fmt(width), fmt(height), str(layer), net,
                     number, "0", "", fmt(rotation), sid(label), "0", "", "Y", "0", "", "",
                     f"{fmt(x)},{fmt(y)}"])


def make_0402(refdes: str, x: float, y: float, pad1_net: str, pad2_net: str,
              value: str, device: str, orientation: str,
              board_layer: int = 1) -> tuple[str, dict[str, tuple[float, float]]]:
    horizontal = orientation == "horizontal"
    offset = 2.5
    p1 = (x - offset, y) if horizontal else (x, y - offset)
    p2 = (x + offset, y) if horizontal else (x, y + offset)
    pw, ph, rotation = ((2.0, 2.4, 0) if horizontal else (2.4, 2.0, 90))
    manufacturer = "Panasonic" if device == "R" else "Samsung Electro-Mechanics"
    part = "ERJ-2GEJ330X" if device == "R" else "CL05B104KO5NNNC"
    sections = [
        component_header(refdes, x, y, rotation, device + "0402", {
            "Manufacturer": manufacturer, "Manufacturer Part": part, "Value": value,
            "Status": "REV5 REVIEW FOOTPRINT", "spicePre": device,
        }),
        *component_text(refdes, x, y, value, 3 if board_layer == 1 else 4),
        "~".join(["TRACK", "0.4", str(3 if board_layer == 1 else 4), "",
                  f"{fmt(x-2.1)} {fmt(y-1.4)} {fmt(x+2.1)} {fmt(y-1.4)} "
                  f"{fmt(x+2.1)} {fmt(y+1.4)} {fmt(x-2.1)} {fmt(y+1.4)} {fmt(x-2.1)} {fmt(y-1.4)}",
                  sid(refdes + ":silk"), "0"]),
        pad(refdes + ":1", *p1, pw, ph, board_layer, pad1_net, "1", rotation=rotation),
        pad(refdes + ":2", *p2, pw, ph, board_layer, pad2_net, "2", rotation=rotation),
    ]
    return "#@$".join(sections), {"1": p1, "2": p2}


def make_testpoint(refdes: str, x: float, y: float, net: str) -> str:
    return "#@$".join([
        component_header(refdes, x, y, 0, "TESTPOINT-SMD-1.0MM-REVIEW", {
            "Value": "PROBE PAD", "Status": "REPLACE OR APPROVE BEFORE FABRICATION", "spicePre": "TP",
        }),
        *component_text(refdes, x, y, "TP"),
        pad(refdes + ":1", x, y, 3.6, 3.6, 1, net, "1", shape="ELLIPSE"),
    ])


def make_track(label: str, net: str, layer: int, points: list[tuple[float, float]], width: float = SIGNAL_WIDTH) -> str:
    coordinates = " ".join(f"{fmt(x)} {fmt(y)}" for x, y in points)
    return f"TRACK~{fmt(width)}~{layer}~{net}~{coordinates}~{sid(label)}~0"


def make_via(label: str, x: float, y: float, net: str) -> str:
    return f"VIA~{fmt(x)}~{fmt(y)}~2.4~{net}~0.6~{sid(label)}~0"


def make_keepout(label: str, layer: int, bounds: tuple[float, float, float, float]) -> str:
    x1, y1, x2, y2 = bounds
    path = f"M {fmt(x1)} {fmt(y1)} L {fmt(x2)} {fmt(y1)} L {fmt(x2)} {fmt(y2)} L {fmt(x1)} {fmt(y2)} Z"
    return f"SOLIDREGION~{layer}~~{path}~cutout~{sid(label)}~~~~0"


def make_zone(label: str, zone: dict[str, float]) -> str:
    return (f"RECT~{fmt(zone['x'])}~{fmt(zone['y'])}~{fmt(zone['width'])}~{fmt(zone['height'])}"
            f"~12~{sid(label)}~1~0.5~none~~~")


def make_ground_plane() -> str:
    return (f"COPPERAREA~0.6~21~GND~{BOARD_PATH}~0.6~solid~{sid('L2_GND')}~direct~none~~1"
            "~L2_GND~1~0.6~0.6~1.2~yes~0.8")


def pad_inventory(shapes: list[str]) -> tuple[int, list[str]]:
    count, nets = 0, set()
    for shape in shapes:
        if kind(shape) != "LIB":
            continue
        for section in shape.split("#@$"):
            fields = section.split("~")
            if len(fields) > 8 and fields[0] == "PAD":
                count += 1
                if fields[7]:
                    nets.add(fields[7])
    return count, sorted(nets)


def component_pads(shapes: list[str], wanted: str) -> dict[str, dict[str, object]]:
    for shape in shapes:
        if kind(shape) != "LIB" or component_ref(shape) != wanted:
            continue
        result = {}
        for section in shape.split("#@$"):
            fields = section.split("~")
            if len(fields) > 8 and fields[0] == "PAD":
                result[fields[8]] = {"net": fields[7], "x": float(fields[2]), "y": float(fields[3]),
                                     "layer": int(fields[6])}
        return result
    raise ValueError(f"missing component {wanted}")


def validate_spi_connectivity(shapes: list[str]) -> int:
    """Prove each serialized SPI net joins all of its intended pads."""
    adjacency: defaultdict[tuple[str, int, float, float], set[tuple[str, int, float, float]]] = defaultdict(set)
    pad_nodes: dict[tuple[str, str], tuple[str, int, float, float]] = {}

    def node(net: str, layer: int, x: float, y: float) -> tuple[str, int, float, float]:
        # EasyEDA exports related pad/track joins with occasional sub-mil
        # coordinate noise, so connectivity uses the same 0.01 drawing-unit
        # tolerance as the V4 evidence audit.
        return net, layer, round(x, 2), round(y, 2)

    def connect(left: tuple[str, int, float, float], right: tuple[str, int, float, float]) -> None:
        adjacency[left].add(right)
        adjacency[right].add(left)

    for shape in shapes:
        if kind(shape) == "TRACK":
            fields = shape.split("~")
            net, layer = fields[3], int(fields[2])
            numbers = [float(value) for value in fields[4].split()]
            points = list(zip(numbers[::2], numbers[1::2]))
            for start, end in zip(points, points[1:]):
                connect(node(net, layer, *start), node(net, layer, *end))
        elif kind(shape) == "VIA":
            fields = shape.split("~")
            net, x, y = fields[4], float(fields[1]), float(fields[2])
            connect(node(net, 1, x, y), node(net, 2, x, y))
        elif kind(shape) == "LIB":
            refdes = component_ref(shape)
            if not refdes:
                continue
            for section in shape.split("#@$"):
                fields = section.split("~")
                if len(fields) <= 8 or fields[0] != "PAD" or not fields[7]:
                    continue
                net, layer = fields[7], int(fields[6])
                x, y, number = float(fields[2]), float(fields[3]), fields[8]
                if layer == 11:
                    top, bottom = node(net, 1, x, y), node(net, 2, x, y)
                    connect(top, bottom)
                    pad_nodes[(refdes, number)] = top
                else:
                    pad_nodes[(refdes, number)] = node(net, layer, x, y)

    groups = {
        "A_SPI0_MOSI_PI": [("U12", "19"), ("R_AMOSI", "1")],
        "A_SPI0_MOSI": [("R_AMOSI", "2"), ("TP_A_MOSI", "1"), ("2_SMD", "19")],
        "A_SPI0_SCLK_PI": [("U12", "23"), ("R_ASCLK", "1")],
        "A_SPI0_SCLK": [("R_ASCLK", "2"), ("TP_A_SCLK", "1"), ("2_SMD", "20")],
        "A_SPI0_CS_PI": [("U12", "24"), ("R_ACS", "1")],
        "A_SPI0_CS": [("R_ACS", "2"), ("TP_A_CS", "1"), ("2_SMD", "18"), ("R_ACS_PU", "1")],
        "A_SPI0_MISO": [("2_SMD", "21"), ("R_AMISO", "1")],
        "A_SPI0_MISO_PI": [("R_AMISO", "2"), ("TP_A_MISO", "1"), ("U12", "21")],
        "B_SPI1_MOSI_PI": [("U12", "38"), ("R_BMOSI", "1")],
        "B_SPI1_MOSI": [("R_BMOSI", "2"), ("TP_B_MOSI", "1"), ("1_SMD", "19")],
        "B_SPI1_SCLK_PI": [("U12", "40"), ("R_BSCLK", "1")],
        "B_SPI1_SCLK": [("R_BSCLK", "2"), ("TP_B_SCLK", "1"), ("1_SMD", "20")],
        "B_SPI1_CS_PI": [("U12", "12"), ("R_BCS", "1")],
        "B_SPI1_CS": [("R_BCS", "2"), ("TP_B_CS", "1"), ("1_SMD", "18"), ("R_BCS_PU", "2")],
        "B_SPI1_MISO": [("1_SMD", "21"), ("R_BMISO", "2")],
        "B_SPI1_MISO_PI": [("R_BMISO", "1"), ("TP_B_MISO", "1"), ("U12", "35")],
    }
    for net, pads in groups.items():
        nodes = []
        for item in pads:
            if item not in pad_nodes:
                raise ValueError(f"connectivity check cannot find {item[0]} pad {item[1]}")
            nodes.append(pad_nodes[item])
        visited = {nodes[0]}
        pending = [nodes[0]]
        while pending:
            current = pending.pop()
            for neighbour in adjacency[current]:
                if neighbour not in visited:
                    visited.add(neighbour)
                    pending.append(neighbour)
        missing = [pads[index] for index, candidate in enumerate(nodes) if candidate not in visited]
        if missing:
            raise ValueError(f"serialized net {net} does not connect intended pads: {missing}")
    return len(groups)


def point_segment_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0 and dy == 0:
        return math.dist(p, a)
    t = max(0.0, min(1.0, ((p[0]-a[0])*dx + (p[1]-a[1])*dy) / (dx*dx + dy*dy)))
    return math.dist(p, (a[0] + t*dx, a[1] + t*dy))


def orientation(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def intersects(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    return orientation(a, b, c) * orientation(a, b, d) < 0 and orientation(c, d, a) * orientation(c, d, b) < 0


def segment_distance(left: tuple[tuple[float, float], tuple[float, float]],
                     right: tuple[tuple[float, float], tuple[float, float]]) -> float:
    if intersects(*left, *right):
        return 0.0
    return min(point_segment_distance(left[0], *right), point_segment_distance(left[1], *right),
               point_segment_distance(right[0], *left), point_segment_distance(right[1], *left))


def route_metrics(routes: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    lengths: defaultdict[str, float] = defaultdict(float)
    layers: defaultdict[str, set[int]] = defaultdict(set)
    vias: Counter[str] = Counter()
    for route in routes:
        signal = str(route["signal"])
        if route.get("via"):
            vias[signal] += 1
            continue
        points = route["points"]
        assert isinstance(points, list)
        lengths[signal] += sum(math.dist(a, b) * EDA_UNIT_MM for a, b in zip(points, points[1:]))
        layers[signal].add(int(route["layer"]))
    return {signal: {"length_mm": round(length, 2), "layers": sorted(layers[signal]),
                     "signal_vias": vias[signal]} for signal, length in sorted(lengths.items())}


def validate_clearance(routes: list[dict[str, object]]) -> float:
    segments = []
    for route in routes:
        if route.get("via"):
            continue
        points = route["points"]
        assert isinstance(points, list)
        for start, end in zip(points, points[1:]):
            segments.append((str(route["net"]), int(route["layer"]), start, end, str(route["label"])))
    minimum = math.inf
    for index, left in enumerate(segments):
        for right in segments[index+1:]:
            if left[1] != right[1] or left[0] == right[0]:
                continue
            distance = segment_distance((left[2], left[3]), (right[2], right[3]))
            minimum = min(minimum, distance)
            if distance + 1e-6 < MIN_TRACK_CENTRE_CLEARANCE:
                raise ValueError(
                    f"track clearance: {left[0]} ({left[4]} {left[2]}->{left[3]}) vs "
                    f"{right[0]} ({right[4]} {right[2]}->{right[3]}) on L{left[1]} = {distance:.3f}"
                )
    return minimum


def add_review_layout(shapes: list[str]) -> tuple[list[str], dict[str, object]]:
    output = list(shapes)
    locations: dict[str, dict[str, tuple[float, float]]] = {}
    resistors = [
        ("R_ASCLK", 4151.673, 3362.0, "A_SPI0_SCLK_PI", "A_SPI0_SCLK", "vertical"),
        ("R_AMOSI", 4141.0, 3372.0, "A_SPI0_MOSI_PI", "A_SPI0_MOSI", "horizontal"),
        ("R_ACS", 4160.0, 3362.0, "A_SPI0_CS_PI", "A_SPI0_CS", "vertical"),
        ("R_AMISO", 4096.5, 3457.5, "A_SPI0_MISO", "A_SPI0_MISO_PI", "horizontal"),
        ("R_BMOSI", 4221.673, 3362.0, "B_SPI1_MOSI_PI", "B_SPI1_MOSI", "vertical"),
        ("R_BSCLK", 4231.673, 3362.0, "B_SPI1_SCLK_PI", "B_SPI1_SCLK", "vertical"),
        ("R_BCS", 4091.673, 3362.0, "B_SPI1_CS_PI", "B_SPI1_CS", "vertical"),
        ("R_BMISO", 4157.0, 3438.5, "B_SPI1_MISO_PI", "B_SPI1_MISO", "horizontal"),
        ("R_ACS_PU", 4100.0, 3478.0, "A_SPI0_CS", "A_3V3", "vertical"),
        ("R_BCS_PU", 4157.0, 3415.0, "B_3V3", "B_SPI1_CS", "vertical"),
    ]
    for refdes, x, y, net1, net2, orient in resistors:
        value = "10k" if refdes.endswith("_PU") else "33R"
        component, locations[refdes] = make_0402(
            refdes, x, y, net1, net2, value, "R", orient,
            board_layer=2 if refdes in {"R_BMOSI", "R_BSCLK", "R_BCS"} else 1,
        )
        output.append(component)

    caps = [("C_AH1", 4069.75, 3501.689), ("C_AH2", 4032.25, 3501.689),
            ("C_BH1", 4202.25, 3485.689), ("C_BH2", 4164.75, 3485.689)]
    for refdes, x, y in caps:
        component, locations[refdes] = make_0402(refdes, x, y, "GND", "5V", "100n", "C", "horizontal")
        output.append(component)

    signal_tps = {
        "TP_A_SCLK": (4102.0, 3410.0, "A_SPI0_SCLK"),
        "TP_A_MOSI": (4106.0, 3422.0, "A_SPI0_MOSI"),
        "TP_A_CS": (4110.0, 3434.0, "A_SPI0_CS"),
        "TP_A_MISO": (4099.0, 3445.0, "A_SPI0_MISO_PI"),
        "TP_B_CS": (4150.0, 3423.5, "B_SPI1_CS"),
        "TP_B_MOSI": (4155.0, 3428.5, "B_SPI1_MOSI"),
        "TP_B_SCLK": (4160.0, 3433.5, "B_SPI1_SCLK"),
        "TP_B_MISO": (4150.0, 3438.5, "B_SPI1_MISO_PI"),
    }
    ground_tps = {
        "TP_A_GSCLK": (4096.0, 3410.0), "TP_A_GMOSI": (4100.0, 3422.0),
        "TP_A_GCS": (4104.0, 3434.0), "TP_A_GMISO": (4093.0, 3445.0),
        "TP_B_GCS": (4139.0, 3423.5), "TP_B_GMOSI": (4144.0, 3428.5),
        "TP_B_GSCLK": (4149.0, 3433.5), "TP_B_GMISO": (4150.0, 3444.5),
    }
    for refdes, (x, y, net) in signal_tps.items():
        output.append(make_testpoint(refdes, x, y, net))
    for refdes, (x, y) in ground_tps.items():
        output.append(make_testpoint(refdes, x, y, "GND"))

    p = locations
    routes: list[dict[str, object]] = [
        {"label":"A_MOSI_PRE","signal":"A_MOSI","net":"A_SPI0_MOSI_PI","layer":1,
         "points":[(4131.673,3353.5),(4131.673,3372.0),p["R_AMOSI"]["1"]]},
        {"label":"A_SCLK_PRE","signal":"A_SCLK","net":"A_SPI0_SCLK_PI","layer":1,
         "points":[(4151.673,3353.5),p["R_ASCLK"]["1"]]},
        {"label":"A_CS_PRE","signal":"A_CS","net":"A_SPI0_CS_PI","layer":1,
         "points":[(4151.673,3343.5),(4160.0,3351.827),p["R_ACS"]["1"]]},
        {"label":"A_SCLK_POST","signal":"A_SCLK","net":"A_SPI0_SCLK","layer":1,
         "points":[p["R_ASCLK"]["2"],(4141.0,3364.5),(4141.0,3379.0),(4102.0,3379.0),
                   (4102.0,3410.0),(4102.0,3462.5),(4092.02,3462.5)]},
        {"label":"A_MOSI_POST","signal":"A_MOSI","net":"A_SPI0_MOSI","layer":1,
         "points":[p["R_AMOSI"]["2"],(4143.5,3384.0),(4106.0,3384.0),(4106.0,3422.0),
                   (4106.0,3467.5),(4092.02,3467.5)]},
        {"label":"A_CS_POST","signal":"A_CS","net":"A_SPI0_CS","layer":1,
         "points":[p["R_ACS"]["2"],(4160.0,3389.0),(4110.0,3389.0),(4110.0,3434.0),
                   (4110.0,3472.5),(4100.0,3472.5),(4092.02,3472.5)]},
        {"label":"A_CS_PULLUP","signal":"A_CS","net":"A_SPI0_CS","layer":1,
         "points":[(4100.0,3472.5),p["R_ACS_PU"]["1"]]},
        {"label":"A_MISO_SRC","signal":"A_MISO","net":"A_SPI0_MISO","layer":1,
         "points":[(4092.02,3457.5),p["R_AMISO"]["1"]]},
        {"label":"A_MISO_TOP","signal":"A_MISO","net":"A_SPI0_MISO_PI","layer":1,
         "points":[p["R_AMISO"]["2"],(4099.0,3445.0),(4099.0,3441.0)]},
        {"label":"A_MISO_BOTTOM","signal":"A_MISO","net":"A_SPI0_MISO_PI","layer":2,
         "points":[(4099.0,3441.0),(4080.0,3422.0),(4080.0,3362.0),
                   (4133.173,3362.0),(4141.673,3353.5)]},
        {"label":"A_MISO_VIA","signal":"A_MISO","net":"A_SPI0_MISO_PI","layer":0,
         "points":[(4099.0,3441.0)],"via":True},
        {"label":"B_MOSI_PRE","signal":"B_MOSI","net":"B_SPI1_MOSI_PI","layer":2,
         "points":[(4221.673,3343.5),p["R_BMOSI"]["1"]]},
        {"label":"B_SCLK_PRE","signal":"B_SCLK","net":"B_SPI1_SCLK_PI","layer":2,
         "points":[(4231.673,3343.5),p["R_BSCLK"]["1"]]},
        {"label":"B_CS_PRE","signal":"B_CS","net":"B_SPI1_CS_PI","layer":2,
         "points":[(4091.673,3343.5),p["R_BCS"]["1"]]},
        {"label":"B_CS_BOTTOM","signal":"B_CS","net":"B_SPI1_CS","layer":2,
         "points":[p["R_BCS"]["2"],(4091.673,3370.0),(4125.0,3370.0),(4145.0,3390.0),
                   (4145.0,3423.5)]},
        {"label":"B_CS_TOP","signal":"B_CS","net":"B_SPI1_CS","layer":1,
         "points":[(4145.0,3423.5),(4150.0,3423.5),(4157.0,3423.5),(4164.98,3423.5)]},
        {"label":"B_CS_PULLUP","signal":"B_CS","net":"B_SPI1_CS","layer":1,
         "points":[p["R_BCS_PU"]["2"],(4157.0,3423.5)]},
        {"label":"B_CS_VIA","signal":"B_CS","net":"B_SPI1_CS","layer":0,
         "points":[(4145.0,3423.5)],"via":True},
        {"label":"B_MOSI_BOTTOM","signal":"B_MOSI","net":"B_SPI1_MOSI","layer":2,
         "points":[p["R_BMOSI"]["2"],(4210.0,3376.173),(4180.0,3376.173),(4150.0,3406.173),
                   (4150.0,3428.5)]},
        {"label":"B_MOSI_TOP","signal":"B_MOSI","net":"B_SPI1_MOSI","layer":1,
         "points":[(4150.0,3428.5),(4155.0,3428.5),(4164.98,3428.5)]},
        {"label":"B_MOSI_VIA","signal":"B_MOSI","net":"B_SPI1_MOSI","layer":0,
         "points":[(4150.0,3428.5)],"via":True},
        {"label":"B_SCLK_BOTTOM","signal":"B_SCLK","net":"B_SPI1_SCLK","layer":2,
         "points":[p["R_BSCLK"]["2"],(4217.0,3381.173),(4185.0,3381.173),(4155.0,3411.173),
                   (4155.0,3433.5)]},
        {"label":"B_SCLK_TOP","signal":"B_SCLK","net":"B_SPI1_SCLK","layer":1,
         "points":[(4155.0,3433.5),(4160.0,3433.5),(4164.98,3433.5)]},
        {"label":"B_SCLK_VIA","signal":"B_SCLK","net":"B_SPI1_SCLK","layer":0,
         "points":[(4155.0,3433.5)],"via":True},
        {"label":"B_MISO_SRC","signal":"B_MISO","net":"B_SPI1_MISO","layer":1,
         "points":[(4164.98,3438.5),p["R_BMISO"]["2"]]},
        {"label":"B_MISO_TOP","signal":"B_MISO","net":"B_SPI1_MISO_PI","layer":1,
         "points":[p["R_BMISO"]["1"],(4150.0,3438.5),(4146.0,3438.5)]},
        {"label":"B_MISO_BOTTOM","signal":"B_MISO","net":"B_SPI1_MISO_PI","layer":2,
         "points":[(4146.0,3438.5),(4146.0,3450.0),(4238.0,3450.0),
                   (4238.0,3362.0),(4211.673,3362.0),(4211.673,3353.5)]},
        {"label":"B_MISO_VIA","signal":"B_MISO","net":"B_SPI1_MISO_PI","layer":0,
         "points":[(4146.0,3438.5)],"via":True},
    ]

    ground_via_positions = {
        "TP_A_GSCLK": (4092.5, 3410.0),
        "TP_A_GMOSI": (4096.5, 3422.0),
        "TP_A_GCS": (4104.0, 3430.5),
        "TP_A_GMISO": (4089.5, 3445.0),
        "TP_B_GCS": (4135.5, 3423.5),
        "TP_B_GMOSI": (4140.5, 3428.5),
        "TP_B_GSCLK": (4145.5, 3433.5),
        "TP_B_GMISO": (4146.0, 3444.5),
    }
    ground_vias = []
    for refdes, (x, y) in ground_tps.items():
        vx, vy = ground_via_positions[refdes]
        routes.append({"label":refdes+"_GND","signal":"GND_TEST","net":"GND","layer":1,
                       "points":[(x,y),(vx,vy)]})
        ground_vias.append((refdes+":via",vx,vy))
    ground_vias.extend([
        ("A_MISO_RETURN",4099.0,3435.0),
    ])

    buffer_vcc = {"C_AH1":(4075.0,3501.689),"C_AH2":(4037.5,3501.689),
                  "C_BH1":(4207.5,3485.689),"C_BH2":(4170.0,3485.689)}
    for refdes, vcc in buffer_vcc.items():
        cap_gnd, cap_5v = p[refdes]["1"], p[refdes]["2"]
        via = (cap_gnd[0]-3.0, cap_gnd[1])
        routes.extend([
            {"label":refdes+"_5V","signal":"BUFFER_POWER","net":"5V","layer":1,
             "points":[vcc,cap_5v],"width":POWER_WIDTH},
            {"label":refdes+"_GND","signal":"BUFFER_POWER","net":"GND","layer":1,
             "points":[cap_gnd,via],"width":POWER_WIDTH},
        ])
        ground_vias.append((refdes+":return",*via))

    for route in routes:
        if route.get("via"):
            x, y = route["points"][0]  # type: ignore[index]
            output.append(make_via(str(route["label"]), x, y, str(route["net"])))
        else:
            output.append(make_track(str(route["label"]), str(route["net"]), int(route["layer"]),
                                     route["points"], float(route.get("width", SIGNAL_WIDTH))))  # type: ignore[arg-type]
    for label, x, y in ground_vias:
        output.append(make_via(label, x, y, "GND"))

    metrics = route_metrics(routes)
    clearance = validate_clearance(routes)
    return output, {
        "series_resistors": [item[0] for item in resistors[:8]],
        "cs_pullups": [item[0] for item in resistors[8:]],
        "signal_testpoints": sorted(signal_tps), "ground_testpoints": sorted(ground_tps),
        "buffer_bypass_capacitors": [item[0] for item in caps],
        "spi_route_metrics": {name:value for name,value in metrics.items() if name.startswith(("A_","B_"))},
        "minimum_different_net_track_centre_clearance_mm": round(clearance * EDA_UNIT_MM, 3),
    }


def stale_rect(shape: str) -> bool:
    fields = shape.split("~")
    try:
        key = tuple(round(float(fields[index]), 3) for index in range(1, 5))
    except (ValueError, IndexError):
        return False
    return fields[0] == "RECT" and key in STALE_RECTS


def build(source: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    source_head = source.get("head")
    if not isinstance(source_head, dict) or source_head.get("docType") != "3":
        raise ValueError("input is not an EasyEDA Standard PCB document")
    original = source.get("shape")
    if not isinstance(original, list) or not all(isinstance(item, str) for item in original):
        raise ValueError("invalid EasyEDA shape array")

    output = json.loads(json.dumps(source))
    shapes: list[str] = []
    removed_selectors: list[str] = []
    removed_obsolete: list[str] = []
    pad_changes: list[dict[str, str]] = []
    placement_changes: list[dict[str, object]] = []
    removed_labels = removed_titles = removed_rects = 0

    for shape in original:
        shape_kind = kind(shape)
        if shape_kind in {"TRACK", "VIA", "COPPERAREA"}:
            continue
        if shape_kind == "SOLIDREGION" and "~cutout~" in shape:
            continue
        if stale_rect(shape):
            removed_rects += 1
            continue
        if shape_kind == "TEXT" and text_value(shape) in SELECTOR_LABELS:
            removed_labels += 1
            continue
        if shape_kind == "TEXT" and text_value(shape) in STALE_TITLES:
            removed_titles += 1
            continue
        if shape_kind == "LIB":
            refdes = component_ref(shape)
            if refdes and refdes.startswith("SJ_SPI"):
                removed_selectors.append(refdes)
                continue
            if refdes in OBSOLETE_REFS:
                removed_obsolete.append(refdes or "")
                continue
            if refdes == "2_SMD":
                shape, changes = rewrite_pads(shape, refdes, A_MODULE_NETS)
                pad_changes.extend(changes)
                shape = transform_component(shape, dy=-16.0)
                placement_changes.append({"refdes":refdes,"role":"A/SPI0/LED1-8",
                    "change":"moved 4.064 mm upward","reason":"clear output connector corridor"})
            elif refdes == "1_SMD":
                shape, changes = rewrite_pads(shape, refdes, B_MODULE_NETS)
                pad_changes.extend(changes)
                shape = transform_component(shape, angle=180.0)
                placement_changes.append({"refdes":refdes,"role":"B/SPI1/LED9-16",
                    "change":"rotated 180 degrees","reason":"antenna faces right board edge"})
            elif refdes == "U12":
                shape, changes = rewrite_pads(shape, refdes, PI_NETS)
                pad_changes.extend(changes)
            elif refdes in BUFFER_NETS:
                shape, changes = rewrite_pads(shape, refdes, BUFFER_NETS[refdes])
                pad_changes.extend(changes)
                if refdes.startswith("TRAN2"):
                    shape = transform_component(shape, dy=-24.0)
                    placement_changes.append({"refdes":refdes,"role":"A output buffer",
                        "change":"moved 6.096 mm upward","reason":"clear output connector corridor"})
        shapes.append(shape)

    shapes.append(make_ground_plane())
    for name, bounds in ANTENNA_KEEPOUTS.items():
        for layer in (1, 21, 22, 2):
            shapes.append(make_keepout(f"{name}:L{layer}", layer, bounds))
    for name, zone in PLACEMENT_ZONES.items():
        shapes.append(make_zone(name, zone))
    shapes, route_manifest = add_review_layout(shapes)
    output["shape"] = shapes

    layers = output.get("layers")
    if not isinstance(layers, list):
        raise ValueError("missing layers")
    output["layers"] = [
        "21~Inner1_GND~#999966~true~false~true~0~Signal" if str(layer).startswith("21~") else
        "22~Inner2_PWR~#008000~true~false~true~0~Signal" if str(layer).startswith("22~") else str(layer)
        for layer in layers
    ]
    pad_count, nets = pad_inventory(shapes)
    router = output.get("routerRule")
    if not isinstance(router, dict):
        raise ValueError("missing routerRule")
    router.update({"trackWidth":0.152,"trackClearance":0.152,"viaHoleD":0.305,"viaDiameter":0.61,
                   "routerLayers":[1,21,22,2],"smdClearance":0.152,"specialNets":[],
                   "nets":nets,"padsCount":pad_count})
    drc = output.get("DRCRULE")
    if isinstance(drc, dict):
        drc.update({"Default":{"trackWidth":0.6,"clearance":0.6,"viaHoleDiameter":2.4,"viaHoleD":1.2},
                    "isRealtime":True,"isDrcOnRoutingOrPlaceVia":True,"checkObjectToCopperarea":True})
    head = output.get("head")
    if not isinstance(head, dict):
        raise ValueError("missing header")
    params = head.setdefault("c_para", {})
    if not isinstance(params, dict):
        raise ValueError("invalid c_para")
    params.update({"Revision":"Rev5 placement/routing scaffold","Status":"NOT FOR FABRICATION",
                   "ReceiverRoles":"2_SMD=A/SPI0; 1_SMD=B/SPI1",
                   "LayerIntent":"Top critical / Inner1 solid GND / Inner2 reserved power / Bottom secondary",
                   "Scope":"SPI routed; buck, USB, LED protection and connector unresolved"})

    manifest: dict[str, object] = {
        "artifact":"LED Grid Wall HAT Rev5 EasyEDA placement/routing scaffold",
        "status":"NOT FOR FABRICATION",
        "source_shape_counts":dict(sorted(Counter(kind(item) for item in original).items())),
        "output_shape_counts":dict(sorted(Counter(kind(item) for item in shapes).items())),
        "removed_selector_footprints":sorted(removed_selectors),
        "removed_obsolete_or_blocking_footprints":sorted(removed_obsolete),
        "removed_selector_labels":removed_labels,"removed_stale_titles":removed_titles,
        "removed_selector_bank_rectangles":removed_rects,
        "pad_net_changes":sorted(pad_changes,key=lambda item:(item["refdes"],int(item["pad"]))),
        "placement_changes":placement_changes,"placement_reservations":PLACEMENT_ZONES,
        "antenna_keepouts":ANTENNA_KEEPOUTS,"remaining_pad_count":pad_count,
        "remaining_named_nets":len(nets),**route_manifest,
    }
    validate(output, manifest)
    return output, manifest


def validate(board: dict[str, object], manifest: dict[str, object]) -> None:
    shapes = board["shape"]
    assert isinstance(shapes, list)
    refs = {component_ref(item) for item in shapes if kind(item) == "LIB"}
    stale = sorted(ref for ref in refs if ref and (ref.startswith("SJ_SPI") or ref in OBSOLETE_REFS))
    if stale:
        raise ValueError(f"stale footprints remain: {stale}")
    for refdes, expected in {"2_SMD":A_MODULE_NETS,"1_SMD":B_MODULE_NETS,"U12":PI_NETS}.items():
        actual = component_pads(shapes, refdes)
        bad = {number:(net,actual.get(number,{}).get("net")) for number,net in expected.items()
               if actual.get(number,{}).get("net") != net}
        if bad:
            raise ValueError(f"{refdes} net mismatch: {bad}")
    a_pads, b_pads = component_pads(shapes,"2_SMD"), component_pads(shapes,"1_SMD")
    if not math.isclose(float(a_pads["19"]["y"]),3467.5,abs_tol=.01):
        raise ValueError("receiver A placement failed")
    if not math.isclose(float(b_pads["19"]["x"]),4164.98,abs_tol=.01):
        raise ValueError("receiver B rotation failed")
    copper = [item for item in shapes if kind(item)=="COPPERAREA"]
    if len(copper)!=1 or "~21~GND~" not in copper[0]:
        raise ValueError("L2 ground area missing")
    for layer in (1,21,22,2):
        cutouts=[item for item in shapes if item.startswith(f"SOLIDREGION~{layer}~") and "~cutout~" in item]
        if len(cutouts)!=2:
            raise ValueError(f"antenna cutouts missing on layer {layer}")
    connectivity_groups = validate_spi_connectivity(shapes)
    metrics=manifest["spi_route_metrics"]
    assert isinstance(metrics,dict)
    limits={"SCLK":75.0,"MOSI":90.0,"MISO":90.0,"CS":90.0}
    for signal,values in metrics.items():
        assert isinstance(values,dict)
        if float(values["length_mm"])>limits[signal.split("_",1)[1]] or int(values["signal_vias"])>1:
            raise ValueError(f"route limit failed for {signal}")
    manifest["validation"]={
        "easyeda_document_type":3,"all_v4_tracks_and_vias_removed_before_rerouting":True,
        "fixed_dual_spi_pad_assignments":"passed","route_length_and_via_limits":"passed",
        "serialized_spi_connectivity_groups":connectivity_groups,
        "different_net_track_clearance":"passed structural centre-line check",
        "antenna_orientation_and_four-layer_cutouts":"passed","l2_ground_copper_area_present":True,
        "native_easyeda_drc":"required after import; not available in this generator",
    }


def parse_args() -> argparse.Namespace:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcb-input",type=Path,required=True)
    parser.add_argument("--pcb-output",type=Path,required=True)
    parser.add_argument("--manifest-output",type=Path,required=True)
    parser.add_argument("--schematic-input",type=Path)
    parser.add_argument("--schematic-reference-output",type=Path)
    return parser.parse_args()


def main() -> int:
    args=parse_args()
    board,manifest=build(json.loads(args.pcb_input.read_text(encoding="utf-8")))
    args.pcb_output.parent.mkdir(parents=True,exist_ok=True)
    args.pcb_output.write_text(json.dumps(board,indent=2)+"\n",encoding="utf-8")
    manifest["source_pcb"]={"file":args.pcb_input.name,"sha256":sha256(args.pcb_input)}
    manifest["generated_pcb"]={"file":args.pcb_output.name,"sha256":sha256(args.pcb_output)}
    if bool(args.schematic_input)!=bool(args.schematic_reference_output):
        raise ValueError("provide both schematic arguments or neither")
    if args.schematic_input and args.schematic_reference_output:
        args.schematic_reference_output.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(args.schematic_input,args.schematic_reference_output)
        manifest["schematic_reference"]={"file":args.schematic_reference_output.name,
            "sha256":sha256(args.schematic_reference_output),"note":"Unmodified V4 reference; not Rev5"}
    args.manifest_output.write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf-8")
    print(f"wrote {args.pcb_output}")
    print(f"wrote {args.manifest_output}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
