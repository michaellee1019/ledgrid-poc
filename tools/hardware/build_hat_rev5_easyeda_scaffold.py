#!/usr/bin/env python3
"""Build the EasyEDA Standard Rev5 placement/routing review scaffold.

The generated PCB is intentionally not fabrication-ready. It replaces the V4
SPI copper and unsafe placement with a bounded, machine-checked critical-route
draft, routes the complete ESP-to-HCT control path, and places the associated
damping, reset-state, and decoupling networks. Buck, USB, output ESD, and the
connector remain explicit release blockers.
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
SIGNAL_WIDTH = 1.0  # 0.254 mm; final impedance comes from the fab stackup.
POWER_WIDTH = 2.0  # 0.508 mm short local branches; final trunks use pours/wider copper.
MIN_TRACK_CENTRE_CLEARANCE = 1.6

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
    "2": "5V", "4": "5V", "6": "GND", "9": "GND", "14": "GND",
    "20": "GND", "25": "GND", "30": "GND", "34": "GND", "39": "GND",
    "19": "A_SPI0_MOSI_PI", "21": "A_SPI0_MISO_PI",
    "23": "A_SPI0_SCLK_PI", "24": "A_SPI0_CS_PI",
    "38": "B_SPI1_MOSI_PI", "35": "B_SPI1_MISO_PI",
    "40": "B_SPI1_SCLK_PI", "12": "B_SPI1_CS_PI",
}

A_MODULE_NETS = {
    "1": "GND", "2": "A_3V3", "3": "A_EN", "4": "A_GPIO4",
    "5": "A_GPIO5", "6": "A_GPIO6", "7": "A_GPIO7",
    "8": "A_GPIO15", "9": "A_GPIO16", "10": "A_GPIO17",
    "11": "A_GPIO18", "12": "A_LED_ENABLE", "13": "A_GPIO19", "14": "A_GPIO20",
    "18": "A_SPI0_CS", "19": "A_SPI0_MOSI", "20": "A_SPI0_SCLK",
    "21": "A_SPI0_MISO", "40": "GND", "41": "GND",
}
B_MODULE_NETS = {
    "1": "GND", "2": "B_3V3", "3": "B_EN", "4": "B_GPIO4",
    "5": "B_GPIO5", "6": "B_GPIO6", "7": "B_GPIO7",
    "8": "B_GPIO15", "9": "B_GPIO16", "10": "B_GPIO17",
    "11": "B_GPIO18", "12": "B_LED_ENABLE", "13": "B_GPIO19", "14": "B_GPIO20",
    "18": "B_SPI1_CS", "19": "B_SPI1_MOSI", "20": "B_SPI1_SCLK",
    "21": "B_SPI1_MISO", "40": "GND", "41": "GND",
}
BUFFER_NETS = {
    # Gate-to-lane assignments are deliberately permuted within each package.
    # This keeps the original GPIO-to-LED mapping while making the physical
    # ESP-to-HCT order monotonic, avoiding crossings in the short fanout.
    "TRAN2_A": {"1":"A_LED_OE_N","2":"A_GPIO15","3":"LED4_DRV",
                "4":"A_LED_OE_N","5":"A_GPIO17","6":"LED2_DRV","7":"GND",
                "8":"LED1_DRV","9":"A_GPIO18","10":"A_LED_OE_N",
                "11":"LED3_DRV","12":"A_GPIO16","13":"A_LED_OE_N","14":"5V"},
    "TRAN2_B": {"1":"A_LED_OE_N","2":"A_GPIO4","3":"LED8_DRV",
                "4":"A_LED_OE_N","5":"A_GPIO6","6":"LED6_DRV","7":"GND",
                "8":"LED5_DRV","9":"A_GPIO7","10":"A_LED_OE_N",
                "11":"LED7_DRV","12":"A_GPIO5","13":"A_LED_OE_N","14":"5V"},
    "TRAN1_A": {"1":"B_LED_OE_N","2":"B_GPIO15","3":"LED12_DRV",
                "4":"B_LED_OE_N","5":"B_GPIO17","6":"LED10_DRV","7":"GND",
                "8":"LED9_DRV","9":"B_GPIO18","10":"B_LED_OE_N",
                "11":"LED11_DRV","12":"B_GPIO16","13":"B_LED_OE_N","14":"5V"},
    "TRAN1_B": {"1":"B_LED_OE_N","2":"B_GPIO4","3":"LED16_DRV",
                "4":"B_LED_OE_N","5":"B_GPIO6","6":"LED14_DRV","7":"GND",
                "8":"LED13_DRV","9":"B_GPIO7","10":"B_LED_OE_N",
                "11":"LED15_DRV","12":"B_GPIO5","13":"B_LED_OE_N","14":"5V"},
}
LOGICAL_GPIO_TO_LED = {
    **{f"A_GPIO{gpio}": f"LED{12-gpio}_DRV" for gpio in (4, 5, 6, 7)},
    **{f"A_GPIO{gpio}": f"LED{19-gpio}_DRV" for gpio in (15, 16, 17, 18)},
    **{f"B_GPIO{gpio}": f"LED{20-gpio}_DRV" for gpio in (4, 5, 6, 7)},
    **{f"B_GPIO{gpio}": f"LED{27-gpio}_DRV" for gpio in (15, 16, 17, 18)},
}

PLACEMENT_ZONES = {
    "A_BUCK_RESERVED": {"x": 4115.0, "y": 3476.0, "width": 25.0, "height": 24.0},
    "B_BUCK_RESERVED": {"x": 4142.0, "y": 3450.0, "width": 20.0, "height": 24.0},
    "CN1_34POS_RESERVED": {"x": 4045.0, "y": 3528.0, "width": 183.0, "height": 27.0},
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
    resistor_parts = {
        "33R": "ERJ-2RKF33R0X", "68R": "ERJ-2RKF68R0X",
        "10k": "ERJ-2RKF1002X", "100k": "ERJ-2RKF1003X",
    }
    capacitor_parts = {"100n": "CL05B104KO5NNNC", "1u": "CL05A105KP5NNNC"}
    manufacturer = "Panasonic" if device == "R" else "Samsung Electro-Mechanics"
    part = resistor_parts.get(value, "VERIFY_VALUE") if device == "R" else capacitor_parts.get(value, "VERIFY_VALUE")
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


def make_0805_cap(refdes: str, x: float, y: float, pad1_net: str, pad2_net: str,
                  value: str, orientation: str = "horizontal") -> tuple[str, dict[str, tuple[float, float]]]:
    horizontal = orientation == "horizontal"
    offset = 4.0
    p1 = (x - offset, y) if horizontal else (x, y - offset)
    p2 = (x + offset, y) if horizontal else (x, y + offset)
    pw, ph, rotation = ((3.2, 4.8, 0) if horizontal else (4.8, 3.2, 90))
    sections = [
        component_header(refdes, x, y, rotation, "C0805", {
            "Manufacturer": "Samsung Electro-Mechanics",
            "Manufacturer Part": "CL21A106KAYNNNE", "Value": value,
            "Status": "REV5 REVIEW FOOTPRINT; VERIFY DC BIAS", "spicePre": "C",
        }),
        *component_text(refdes, x, y, value),
        pad(refdes + ":1", *p1, pw, ph, 1, pad1_net, "1", rotation=rotation),
        pad(refdes + ":2", *p2, pw, ph, 1, pad2_net, "2", rotation=rotation),
    ]
    return "#@$".join(sections), {"1": p1, "2": p2}


def make_sot23_nmos(refdes: str, x: float, y: float, gate_net: str,
                    source_net: str, drain_net: str) -> tuple[str, dict[str, tuple[float, float]]]:
    locations = {"1": (x - 2.5, y + 2.5), "2": (x + 2.5, y + 2.5), "3": (x, y - 2.5)}
    sections = [
        component_header(refdes, x, y, 0, "SOT-23-3", {
            "Manufacturer": "Nexperia", "Manufacturer Part": "2N7002,215",
            "Value": "2N7002", "Status": "REV5 REVIEW FOOTPRINT", "spicePre": "Q",
        }),
        *component_text(refdes, x, y, "2N7002"),
        pad(refdes + ":1", *locations["1"], 2.2, 3.0, 1, gate_net, "1"),
        pad(refdes + ":2", *locations["2"], 2.2, 3.0, 1, source_net, "2"),
        pad(refdes + ":3", *locations["3"], 2.2, 3.0, 1, drain_net, "3"),
    ]
    return "#@$".join(sections), locations


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


def make_ground_plane(layer: int, name: str) -> str:
    return (f"COPPERAREA~0.6~{layer}~GND~{BOARD_PATH}~0.6~solid~{sid(name)}~direct~none~~1"
            f"~{name}~1~0.6~0.6~1.2~yes~0.8")


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


def validate_net_connectivity(shapes: list[str], required_nets: set[str]) -> int:
    """Prove every pad carrying each required net is in one serialized graph."""
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
            via_nodes = [node(net, layer, x, y) for layer in (1, 21, 22, 2)]
            for left, right in zip(via_nodes, via_nodes[1:]):
                connect(left, right)
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
                    through_nodes = [node(net, item, x, y) for item in (1, 21, 22, 2)]
                    for left, right in zip(through_nodes, through_nodes[1:]):
                        connect(left, right)
                    pad_nodes[(refdes, number)] = through_nodes[0]
                else:
                    pad_nodes[(refdes, number)] = node(net, layer, x, y)

    pads_by_net: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)
    for item, pad_node in pad_nodes.items():
        if pad_node[0] in required_nets:
            pads_by_net[pad_node[0]].append(item)
    for net in sorted(required_nets):
        pads = pads_by_net[net]
        if len(pads) < 2:
            raise ValueError(f"connectivity check found fewer than two pads on {net}: {pads}")
        nodes = [pad_nodes[item] for item in pads]
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
    return len(required_nets)


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


def validate_routed_copper_to_pads(
    routes: list[dict[str, object]], shapes: list[str],
    extra_vias: list[tuple[str, float, float, str]],
) -> float:
    """Conservative track/via-to-different-net pad spacing check.

    Rectangular pads use half their smaller dimension, so this is a structural
    pre-check rather than a replacement for EasyEDA's shape-aware native DRC.
    """
    pads = []
    for shape in shapes:
        if kind(shape) != "LIB":
            continue
        refdes = component_ref(shape) or "?"
        for section in shape.split("#@$"):
            fields = section.split("~")
            if len(fields) <= 8 or fields[0] != "PAD" or not fields[7]:
                continue
            pads.append({
                "refdes": refdes, "number": fields[8], "net": fields[7],
                "x": float(fields[2]), "y": float(fields[3]),
                "radius": min(float(fields[4]), float(fields[5])) / 2.0,
                "layers": {1, 21, 22, 2} if int(fields[6]) == 11 else {int(fields[6])},
            })
    minimum_edge = math.inf
    for index, left in enumerate(pads):
        for right in pads[index + 1:]:
            if not left["layers"].intersection(right["layers"]) or left["net"] == right["net"]:
                continue
            edge = math.dist(
                (float(left["x"]), float(left["y"])),
                (float(right["x"]), float(right["y"])),
            ) - float(left["radius"]) - float(right["radius"])
            minimum_edge = min(minimum_edge, edge)
            if edge + 1e-6 < 0.6:
                raise ValueError(
                    f"pad-to-pad clearance: {left['refdes']} pad {left['number']} ({left['net']}) "
                    f"vs {right['refdes']} pad {right['number']} ({right['net']}) = {edge:.3f}"
                )
    for route in routes:
        if route.get("via"):
            continue
        layer, net = int(route["layer"]), str(route["net"])
        width = float(route.get("width", SIGNAL_WIDTH))
        points = route["points"]
        assert isinstance(points, list)
        for start, end in zip(points, points[1:]):
            for candidate in pads:
                if layer not in candidate["layers"] or net == candidate["net"]:
                    continue
                distance = point_segment_distance(
                    (float(candidate["x"]), float(candidate["y"])), start, end
                )
                edge = distance - width / 2.0 - float(candidate["radius"])
                minimum_edge = min(minimum_edge, edge)
                if edge + 1e-6 < 0.6:
                    raise ValueError(
                        f"track-to-pad clearance: {net} ({route['label']} {start}->{end}) vs {candidate['refdes']} "
                        f"pad {candidate['number']} ({candidate['net']}) on L{layer} = {edge:.3f}"
                    )
    via_items = [
        (str(route["label"]), float(route["points"][0][0]), float(route["points"][0][1]), str(route["net"]))
        for route in routes if route.get("via")
    ] + extra_vias
    for index, left in enumerate(via_items):
        for right in via_items[index + 1:]:
            if left[3] == right[3]:
                continue
            edge = math.dist((left[1], left[2]), (right[1], right[2])) - 2.4
            minimum_edge = min(minimum_edge, edge)
            if edge + 1e-6 < 0.6:
                raise ValueError(
                    f"via-to-via clearance: {left[0]} ({left[3]}) vs {right[0]} ({right[3]}) = {edge:.3f}"
                )
    for route in routes:
        if route.get("via"):
            continue
        net = str(route["net"])
        width = float(route.get("width", SIGNAL_WIDTH))
        points = route["points"]
        assert isinstance(points, list)
        for start, end in zip(points, points[1:]):
            for label, x, y, via_net in via_items:
                if net == via_net:
                    continue
                edge = point_segment_distance((x, y), start, end) - width / 2.0 - 1.2
                minimum_edge = min(minimum_edge, edge)
                if edge + 1e-6 < 0.6:
                    raise ValueError(
                        f"track-to-via clearance: {net} ({route['label']} {start}->{end}) vs "
                        f"{label} ({via_net} at {(x, y)}) = {edge:.3f}"
                    )
    for label, x, y, net in via_items:
        for candidate in pads:
            if net == candidate["net"]:
                continue
            distance = math.dist((x, y), (float(candidate["x"]), float(candidate["y"])))
            edge = distance - 1.2 - float(candidate["radius"])
            minimum_edge = min(minimum_edge, edge)
            if edge + 1e-6 < 0.6:
                raise ValueError(
                    f"via-to-pad clearance: {label} ({net}) vs {candidate['refdes']} "
                    f"pad {candidate['number']} ({candidate['net']}) = {edge:.3f}"
                )
    return minimum_edge


def add_review_layout(shapes: list[str]) -> tuple[list[str], dict[str, object]]:
    output = list(shapes)
    locations: dict[str, dict[str, tuple[float, float]]] = {}
    routes: list[dict[str, object]] = []
    extra_vias: list[tuple[str, float, float, str]] = []

    module_pads = {"A": component_pads(shapes, "2_SMD"), "B": component_pads(shapes, "1_SMD")}
    buffer_refs = {"A": ("TRAN2_A", "TRAN2_B"), "B": ("TRAN1_A", "TRAN1_B")}
    buffer_pads = {refdes: component_pads(shapes, refdes) for refs in buffer_refs.values() for refdes in refs}

    def add_0402(refdes: str, x: float, y: float, net1: str, net2: str, value: str,
                 device: str = "R", orientation: str = "horizontal", layer: int = 1) -> None:
        component, locations[refdes] = make_0402(
            refdes, x, y, net1, net2, value, device, orientation, board_layer=layer,
        )
        output.append(component)

    # Source damping is located at the actual driver for each direction.
    spi_resistors = [
        ("R_ASCLK", 4151.673, 3362.0, "A_SPI0_SCLK_PI", "A_SPI0_SCLK", "vertical", 1),
        ("R_AMOSI", 4141.0, 3372.0, "A_SPI0_MOSI_PI", "A_SPI0_MOSI", "horizontal", 1),
        ("R_ACS", 4160.0, 3362.0, "A_SPI0_CS_PI", "A_SPI0_CS", "vertical", 1),
        ("R_AMISO", 4096.0, 3429.5, "A_SPI0_MISO", "A_SPI0_MISO_PI", "horizontal", 1),
        ("R_BMOSI", 4226.673, 3362.0, "B_SPI1_MOSI_PI", "B_SPI1_MOSI", "vertical", 2),
        ("R_BSCLK", 4237.673, 3362.0, "B_SPI1_SCLK_PI", "B_SPI1_SCLK", "vertical", 2),
        ("R_BCS", 4096.673, 3362.0, "B_SPI1_CS_PI", "B_SPI1_CS", "vertical", 2),
        ("R_BMISO", 4241.5, 3417.5, "B_SPI1_MISO", "B_SPI1_MISO_PI", "horizontal", 2),
    ]
    for item in spi_resistors:
        add_0402(*item[:5], "33R", orientation=item[5], layer=item[6])
    add_0402("R_ACS_PU", 4100.0, 3452.0, "A_SPI0_CS", "A_3V3", "10k", orientation="vertical")
    add_0402("R_BCS_PU", 4248.0, 3432.5, "B_SPI1_CS", "B_3V3", "10k")

    # ESP reset, entry decoupling, and output-enable fail-safe networks.
    enable_parts: list[str] = []
    esp_caps: list[str] = []
    for side in ("A", "B"):
        pads = module_pads[side]
        module_y = float(pads["2"]["y"])
        if side == "A":
            cap_x, cap_y, en_x = 4017.5, module_y + 8.0, 4024.0
            q_x, q_y = 4112.0, 3474.0
            oe_r_x, oe_r_y = 4112.0, 3466.0
            gate_r_x, gate_r_y = 4104.0, 3476.5
        else:
            cap_x, cap_y, en_x = 4158.0, module_y + 8.0, 4169.0
            q_x, q_y = 4249.0, 3467.0
            oe_r_x, oe_r_y = 4249.0, 3458.0
            gate_r_x, gate_r_y = 4241.0, 3469.5
        cap10, locations[f"C_{side}_IN10"] = make_0805_cap(
            f"C_{side}_IN10", cap_x, cap_y, "GND", f"{side}_3V3", "10u"
        )
        output.append(cap10)
        esp_caps.append(f"C_{side}_IN10")
        add_0402(f"C_{side}_IN1", cap_x, cap_y + 8.0, "GND", f"{side}_3V3", "1u", "C")
        add_0402(f"C_{side}_IN01", cap_x, cap_y + 14.0, "GND", f"{side}_3V3", "100n", "C")
        add_0402(f"R_{side}_EN_PU", en_x, module_y + 6.0, f"{side}_EN", f"{side}_3V3",
                 "10k", orientation="vertical")
        en_cap_x = en_x + 5.0 if side == "A" else en_x - 2.0
        add_0402(f"C_{side}_EN", en_cap_x, module_y + 17.0, f"{side}_EN", "GND",
                 "1u", "C", orientation="vertical")
        transistor, locations[f"Q_{side}_OE"] = make_sot23_nmos(
            f"Q_{side}_OE", q_x, q_y, f"{side}_LED_ENABLE", "GND", f"{side}_LED_OE_N"
        )
        output.append(transistor)
        add_0402(f"R_{side}_OE_PU", oe_r_x, oe_r_y, f"{side}_LED_OE_N", "5V", "10k")
        add_0402(f"R_{side}_GATE_PD", gate_r_x, gate_r_y, "GND", f"{side}_LED_ENABLE", "100k")
        enable_parts.extend([f"Q_{side}_OE", f"R_{side}_OE_PU", f"R_{side}_GATE_PD"])
        esp_caps.extend([f"C_{side}_IN1", f"C_{side}_IN01", f"C_{side}_EN", f"R_{side}_EN_PU"])

    # One 100 nF directly at each HCT package and one 1 uF per four packages.
    bypass_caps: list[str] = []
    for refdes in ("TRAN2_A", "TRAN2_B", "TRAN1_A", "TRAN1_B"):
        vcc = buffer_pads[refdes]["14"]
        x, y = float(vcc["x"]), float(vcc["y"])
        cap_ref = "C_" + refdes.replace("TRAN", "HCT")
        add_0402(cap_ref, x, y - 5.0, "GND", "5V", "100n", "C", orientation="vertical")
        bypass_caps.append(cap_ref)
    add_0402("C_A_HCT_BULK", 4071.0, 3522.0, "GND", "5V", "1u", "C")
    add_0402("C_B_HCT_BULK", 4203.5, 3514.0, "GND", "5V", "1u", "C")
    bypass_caps.extend(["C_A_HCT_BULK", "C_B_HCT_BULK"])

    # Shunt pull-downs define every HCT input while the ESPs reset.  The
    # resistor bodies sit at the source fanout so the routes remain monotonic.
    input_pulldowns: list[str] = []
    input_routes: list[str] = []
    input_ground_pads: defaultdict[str, list[tuple[float, float]]] = defaultdict(list)
    gpio_pad_numbers = {4: "4", 5: "5", 6: "6", 7: "7", 15: "8", 16: "9", 17: "10", 18: "11"}
    for side in ("A", "B"):
        for gpio, module_pad_number in gpio_pad_numbers.items():
            net = f"{side}_GPIO{gpio}"
            target_ref = target_pad_number = None
            for refdes in buffer_refs[side]:
                for number, assigned in BUFFER_NETS[refdes].items():
                    if assigned == net:
                        target_ref, target_pad_number = refdes, number
            if target_ref is None or target_pad_number is None:
                raise ValueError(f"no HCT input found for {net}")
            source = (float(module_pads[side][module_pad_number]["x"]),
                      float(module_pads[side][module_pad_number]["y"]))
            target = (float(buffer_pads[target_ref][target_pad_number]["x"]),
                      float(buffer_pads[target_ref][target_pad_number]["y"]))
            pd_ref = f"R_{side}_GPIO{gpio}_PD"
            add_0402(pd_ref, source[0], source[1] + 6.0, net, "GND", "100k", orientation="vertical")
            input_pulldowns.append(pd_ref)
            pd_signal = locations[pd_ref]["1"]
            routes.append({"label":net+"_SOURCE", "signal":net, "net":net, "layer":1,
                           "points":[source, pd_signal]})
            package_ys = [float(item["y"]) for item in buffer_pads[target_ref].values()]
            package_top, package_bottom = min(package_ys), max(package_ys)
            lane_index = list(gpio_pad_numbers).index(gpio)
            # Peel the rightmost lanes away first.  The staircase keeps each
            # diagonal clear of the next source via before the fanout opens.
            source_escape = (pd_signal[0], pd_signal[1] + (8.0 - lane_index))
            if math.isclose(target[1], package_bottom):
                # Bottom-row inputs sit behind an OE pad on the top row.  A
                # 0.635 mm dogleg threads between the OE and neighbouring input
                # vias, then turns only after clearing the top row.
                detour_x = target[0] - 2.5
                bottom_points = [pd_signal, source_escape, (detour_x, source[1] + 15.0),
                                 (detour_x, package_top + 4.0),
                                 (target[0], package_top + 4.0), target]
            else:
                bottom_points = [pd_signal, source_escape,
                                 (target[0], source[1] + 15.0), target]
            # All sixteen lanes use a monotonic Bottom fanout. The intentional
            # gate permutation above preserves logical LED order and removes
            # every geometric crossing.
            routes.extend([
                {"label":net+"_BOTTOM", "signal":net, "net":net, "layer":2,
                 "points":bottom_points},
                {"label":net+"_VIA_SOURCE", "signal":net, "net":net, "layer":0,
                 "points":[pd_signal], "via":True},
                {"label":net+"_VIA_HCT", "signal":net, "net":net, "layer":0,
                 "points":[target], "via":True},
            ])
            pd_ground = locations[pd_ref]["2"]
            input_ground_pads[side].append(pd_ground)
            input_routes.append(net)

    # A Top-layer ground rail joins the input pull-downs and drops into both
    # ground planes just outside the Bottom-layer fanout.  This avoids sixteen
    # through-vias competing with the 1.27 mm lane pitch.
    for side, ground_pads in input_ground_pads.items():
        ordered = sorted(ground_pads)
        bus_via = (ordered[0][0] - 5.0, ordered[0][1])
        routes.append({"label":f"{side}_INPUT_PULL_GND_BUS", "signal":"INPUT_PULL_GND",
                       "net":"GND", "layer":1, "points":[bus_via, *ordered]})
        extra_vias.append((f"{side}_INPUT_PULL_GND_BUS:via", *bus_via, "GND"))

    # 68 ohm source damping is immediately outside every HCT output pad.
    output_resistors: list[str] = []
    output_route_nets: list[str] = []
    for refdes, mapping in BUFFER_NETS.items():
        ys = [float(item["y"]) for item in buffer_pads[refdes].values()]
        top_y, bottom_y = min(ys), max(ys)
        for number, net in mapping.items():
            if not net.startswith("LED") or not net.endswith("_DRV"):
                continue
            pad_info = buffer_pads[refdes][number]
            x, y = float(pad_info["x"]), float(pad_info["y"])
            led_number = int(net[3:-4])
            resistor_ref = f"R_LED{led_number}"
            if math.isclose(y, top_y):
                add_0402(resistor_ref, x, y - 4.2, f"LED{led_number}_OUT", net,
                         "68R", orientation="vertical")
                driver_pad = locations[resistor_ref]["2"]
            elif math.isclose(y, bottom_y):
                add_0402(resistor_ref, x, y + 4.2, net, f"LED{led_number}_OUT",
                         "68R", orientation="vertical")
                driver_pad = locations[resistor_ref]["1"]
            else:
                raise ValueError(f"unexpected output row for {refdes} pad {number}")
            routes.append({"label":net+"_DAMP", "signal":net, "net":net, "layer":1,
                           "points":[(x, y), driver_pad]})
            output_resistors.append(resistor_ref)
            output_route_nets.append(net)

    # Eight OE pads per receiver are stitched on Inner1 around the packages.
    # The buses sit outside each pad row and use individual branches so they
    # never pass through the signal vias on HCT input pads 2/5/9/12.
    for side in ("A", "B"):
        net = f"{side}_LED_OE_N"
        package_anchors: list[tuple[float, float]] = []
        all_oe_points: list[tuple[float, float]] = []
        for refdes in buffer_refs[side]:
            package_points = []
            for number in ("1", "4", "10", "13"):
                point = (float(buffer_pads[refdes][number]["x"]), float(buffer_pads[refdes][number]["y"]))
                package_points.append(point)
                all_oe_points.append(point)
                routes.append({"label":f"{refdes}_OE{number}_VIA", "signal":net, "net":net,
                               "layer":0, "points":[point], "via":True})
            local_top_y = min(y for _, y in package_points)
            local_bottom_y = max(y for _, y in package_points)
            top = sorted(point for point in package_points if math.isclose(point[1], local_top_y))
            bottom = sorted(point for point in package_points if math.isclose(point[1], local_bottom_y))
            top_bus_y, bottom_bus_y = local_top_y - 4.0, local_bottom_y + 4.0
            join_x = min(x for x, _ in package_points) - 4.0
            anchor = (join_x, top_bus_y)
            package_anchors.append(anchor)
            routes.extend([
                {"label":refdes+"_OE_TOP_BUS", "signal":net, "net":net, "layer":21,
                 "points":[(top[0][0],top_bus_y),(top[-1][0],top_bus_y)]},
                {"label":refdes+"_OE_BOTTOM_BUS", "signal":net, "net":net, "layer":21,
                 "points":[(bottom[0][0],bottom_bus_y),(bottom[-1][0],bottom_bus_y)]},
                {"label":refdes+"_OE_ROW_JOIN", "signal":net, "net":net, "layer":21,
                 "points":[(top[0][0],top_bus_y),anchor,
                           (join_x,bottom_bus_y),(bottom[0][0],bottom_bus_y)]},
            ])
            for row_name, row, bus_y in (("TOP",top,top_bus_y),("BOTTOM",bottom,bottom_bus_y)):
                for branch_index, point in enumerate(row):
                    routes.append({"label":f"{refdes}_OE_{row_name}_{branch_index}",
                                   "signal":net, "net":net, "layer":21,
                                   "points":[point,(point[0],bus_y)]})
        top_y = min(y for _, y in all_oe_points)
        q_drain = locations[f"Q_{side}_OE"]["3"]
        q_via = (q_drain[0], q_drain[1] - 2.0)
        routes.extend([
            {"label":side+"_OE_Q_TOP", "signal":net, "net":net, "layer":1,
             "points":[locations[f"R_{side}_OE_PU"]["1"], q_drain, q_via]},
            {"label":side+"_OE_Q_INNER1", "signal":net, "net":net, "layer":21,
             "points":[q_via, (q_via[0], top_y - 3.0),
                       (package_anchors[-1][0], top_y - 3.0), package_anchors[-1]]},
            {"label":side+"_OE_Q_VIA", "signal":net, "net":net, "layer":0,
             "points":[q_via], "via":True},
        ])
        for index, anchor in enumerate(package_anchors[:-1]):
            routes.append({"label":f"{side}_OE_BRANCH_{index}", "signal":net, "net":net, "layer":21,
                           "points":[(package_anchors[-1][0], top_y - 3.0),
                                     (anchor[0], top_y - 3.0), anchor]})
        enable_source = (float(module_pads[side]["12"]["x"]), float(module_pads[side]["12"]["y"]))
        q_gate = locations[f"Q_{side}_OE"]["1"]
        routes.append({"label":side+"_LED_ENABLE", "signal":f"{side}_LED_ENABLE",
                       "net":f"{side}_LED_ENABLE", "layer":1,
                       "points":[enable_source, (enable_source[0], q_gate[1] + 3.0),
                                 (q_gate[0], q_gate[1] + 3.0), q_gate,
                                 locations[f"R_{side}_GATE_PD"]["2"]]})
        extra_vias.extend([
            (f"Q_{side}_OE:source", *locations[f"Q_{side}_OE"]["2"], "GND"),
            (f"R_{side}_GATE_PD:gnd", *locations[f"R_{side}_GATE_PD"]["1"], "GND"),
        ])

    # Short local bypass connections. The 5 V trunk and both buck converters
    # remain explicit review zones because their final approved footprints are
    # not in the supplied source data.
    for refdes in ("TRAN2_A", "TRAN2_B", "TRAN1_A", "TRAN1_B"):
        cap_ref = "C_" + refdes.replace("TRAN", "HCT")
        vcc = (float(buffer_pads[refdes]["14"]["x"]), float(buffer_pads[refdes]["14"]["y"]))
        cap_gnd, cap_5v = locations[cap_ref]["1"], locations[cap_ref]["2"]
        routes.append({"label":cap_ref+"_5V", "signal":"BUFFER_POWER", "net":"5V", "layer":1,
                       "points":[vcc, cap_5v], "width":POWER_WIDTH})
        extra_vias.extend([
            (cap_ref+":gnd", *cap_gnd, "GND"),
            (refdes+":gnd", float(buffer_pads[refdes]["7"]["x"]),
             float(buffer_pads[refdes]["7"]["y"]), "GND"),
        ])
    for side, cap_ref, local_cap_ref, corridor_x in (
        ("A", "C_A_HCT_BULK", "C_HCT2_A", 4071.25),
        ("B", "C_B_HCT_BULK", "C_HCT1_A", 4203.50),
    ):
        bulk_gnd, bulk_5v = locations[cap_ref]["1"], locations[cap_ref]["2"]
        local_5v = locations[local_cap_ref]["2"]
        routes.append({"label":side+"_HCT_BULK_5V", "signal":"BUFFER_POWER", "net":"5V",
                       "layer":1, "points":[bulk_5v,(corridor_x,bulk_5v[1]),
                                             (corridor_x,local_5v[1]),local_5v]})
        extra_vias.append((cap_ref+":gnd", *bulk_gnd, "GND"))

    a = module_pads["A"]
    b = module_pads["B"]
    p = locations
    spi_routes: list[dict[str, object]] = [
        {"label":"A_MOSI_PRE","signal":"A_MOSI","net":"A_SPI0_MOSI_PI","layer":1,
         "points":[(4131.673,3353.5),(4131.673,3372.0),p["R_AMOSI"]["1"]]},
        {"label":"A_SCLK_PRE","signal":"A_SCLK","net":"A_SPI0_SCLK_PI","layer":1,
         "points":[(4151.673,3353.5),p["R_ASCLK"]["1"]]},
        {"label":"A_CS_PRE","signal":"A_CS","net":"A_SPI0_CS_PI","layer":1,
         "points":[(4151.673,3343.5),(4157.0,3348.827),(4157.0,3359.5),p["R_ACS"]["1"]]},
        {"label":"A_SCLK_POST","signal":"A_SCLK","net":"A_SPI0_SCLK","layer":1,
         "points":[p["R_ASCLK"]["2"],(4141.0,3364.5),(4141.0,3379.0),(4102.0,3379.0),
                   (4102.0,float(a["20"]["y"])),(float(a["20"]["x"]),float(a["20"]["y"]))]},
        {"label":"A_MOSI_POST","signal":"A_MOSI","net":"A_SPI0_MOSI","layer":1,
         "points":[p["R_AMOSI"]["2"],(4143.5,3384.0),(4106.0,3384.0),
                   (4106.0,float(a["19"]["y"])),(float(a["19"]["x"]),float(a["19"]["y"]))]},
        {"label":"A_CS_POST","signal":"A_CS","net":"A_SPI0_CS","layer":1,
         "points":[p["R_ACS"]["2"],(4160.0,3389.0),(4110.0,3389.0),
                   (4110.0,float(a["18"]["y"])),(float(a["18"]["x"]),float(a["18"]["y"])),
                   p["R_ACS_PU"]["1"]]},
        {"label":"A_MISO_SRC","signal":"A_MISO","net":"A_SPI0_MISO","layer":1,
         "points":[(float(a["21"]["x"]),float(a["21"]["y"])),p["R_AMISO"]["1"]]},
        {"label":"A_MISO_TOP","signal":"A_MISO","net":"A_SPI0_MISO_PI","layer":1,
         "points":[p["R_AMISO"]["2"],(4098.5,float(a["21"]["y"])-4.5)]},
        {"label":"A_MISO_BOTTOM","signal":"A_MISO","net":"A_SPI0_MISO_PI","layer":2,
         "points":[(4098.5,float(a["21"]["y"])-4.5),(4084.0,3409.5),(4084.0,3378.0),
                   (4141.673,3378.0),(4141.673,3353.5)]},
        {"label":"A_MISO_VIA","signal":"A_MISO","net":"A_SPI0_MISO_PI","layer":0,
         "points":[(4098.5,float(a["21"]["y"])-4.5)],"via":True},
        {"label":"B_MOSI_PRE","signal":"B_MOSI","net":"B_SPI1_MOSI_PI","layer":2,
         "points":[(4221.673,3343.5),(4226.673,3348.5),p["R_BMOSI"]["1"]]},
        {"label":"B_SCLK_PRE","signal":"B_SCLK","net":"B_SPI1_SCLK_PI","layer":2,
         "points":[(4231.673,3343.5),(4237.673,3349.5),p["R_BSCLK"]["1"]]},
        {"label":"B_CS_PRE","signal":"B_CS","net":"B_SPI1_CS_PI","layer":2,
         "points":[(4091.673,3343.5),(4096.673,3348.5),p["R_BCS"]["1"]]},
        {"label":"B_CS_INNER1","signal":"B_CS","net":"B_SPI1_CS","layer":21,
         "points":[p["R_BCS"]["2"],(4096.673,3370.0),(4254.0,3370.0),
                   (4254.0,float(b["18"]["y"])),(float(b["18"]["x"]),float(b["18"]["y"]))]},
        {"label":"B_CS_TOP","signal":"B_CS","net":"B_SPI1_CS","layer":1,
         "points":[(float(b["18"]["x"]),float(b["18"]["y"])),
                   (4241.0,float(b["18"]["y"])),p["R_BCS_PU"]["1"]]},
        {"label":"B_CS_VIA_SOURCE","signal":"B_CS","net":"B_SPI1_CS","layer":0,
         "points":[p["R_BCS"]["2"]],"via":True},
        {"label":"B_CS_VIA_HCT","signal":"B_CS","net":"B_SPI1_CS","layer":0,
         "points":[(float(b["18"]["x"]),float(b["18"]["y"]))],"via":True},
        {"label":"B_MOSI_BOTTOM_STUB","signal":"B_MOSI","net":"B_SPI1_MOSI","layer":2,
         "points":[p["R_BMOSI"]["2"],(4226.673,3367.0)]},
        {"label":"B_MOSI_TOP","signal":"B_MOSI","net":"B_SPI1_MOSI","layer":1,
         "points":[(4226.673,3367.0),(4254.0,3375.0),(4254.0,float(b["19"]["y"])),
                   (float(b["19"]["x"]),float(b["19"]["y"]))]},
        {"label":"B_MOSI_VIA","signal":"B_MOSI","net":"B_SPI1_MOSI","layer":0,
         "points":[(4226.673,3367.0)],"via":True},
        {"label":"B_SCLK_BOTTOM_STUB","signal":"B_SCLK","net":"B_SPI1_SCLK","layer":2,
         "points":[p["R_BSCLK"]["2"],(4237.673,3385.0),(4249.0,3385.0)]},
        {"label":"B_SCLK_TOP","signal":"B_SCLK","net":"B_SPI1_SCLK","layer":1,
         "points":[(4249.0,3385.0),(4249.0,float(b["20"]["y"])),
                   (float(b["20"]["x"]),float(b["20"]["y"]))]},
        {"label":"B_SCLK_VIA","signal":"B_SCLK","net":"B_SPI1_SCLK","layer":0,
         "points":[(4249.0,3385.0)],"via":True},
        {"label":"B_MISO_SRC","signal":"B_MISO","net":"B_SPI1_MISO","layer":2,
         "points":[(float(b["21"]["x"]),float(b["21"]["y"])),p["R_BMISO"]["1"]]},
        {"label":"B_MISO_TOP","signal":"B_MISO","net":"B_SPI1_MISO_PI","layer":2,
         "points":[p["R_BMISO"]["2"],(4259.0,float(b["21"]["y"]))]},
        {"label":"B_MISO_BOTTOM","signal":"B_MISO","net":"B_SPI1_MISO_PI","layer":2,
         "points":[(4259.0,float(b["21"]["y"])),(4259.0,3390.0),(4216.173,3390.0),
                   (4211.673,3353.5)]},
        {"label":"B_MISO_VIA","signal":"B_MISO","net":"B_SPI1_MISO_PI","layer":0,
         "points":[(4259.0,float(b["21"]["y"]))],"via":True},
        {"label":"B_MISO_SOURCE_VIA","signal":"B_MISO","net":"B_SPI1_MISO","layer":0,
         "points":[(float(b["21"]["x"]),float(b["21"]["y"]))],"via":True},
    ]
    routes.extend(spi_routes)

    signal_tps = {
        "TP_A_MISO": (4098.5, float(a["21"]["y"]) - 4.5, "A_SPI0_MISO_PI"),
        "TP_A_SCLK": (4102.0, float(a["20"]["y"]), "A_SPI0_SCLK"),
        "TP_A_MOSI": (4106.0, float(a["19"]["y"]), "A_SPI0_MOSI"),
        "TP_A_CS": (4110.0, float(a["18"]["y"]), "A_SPI0_CS"),
        "TP_B_MISO": (4259.0, float(b["21"]["y"]), "B_SPI1_MISO_PI"),
        "TP_B_SCLK": (4249.0, float(b["20"]["y"]), "B_SPI1_SCLK"),
        "TP_B_MOSI": (4254.0, float(b["19"]["y"]), "B_SPI1_MOSI"),
        "TP_B_CS": (4241.0, float(b["18"]["y"]), "B_SPI1_CS"),
    }
    ground_tps = {
        "TP_A_GMISO": (4116.0, float(a["21"]["y"]) - 4.5),
        "TP_A_GSCLK": (4116.0, float(a["20"]["y"])),
        "TP_A_GMOSI": (4116.0, float(a["19"]["y"])),
        "TP_A_GCS": (4116.0, float(a["18"]["y"])),
        "TP_B_GMISO": (4262.0, float(b["21"]["y"]) - 6.0),
        "TP_B_GSCLK": (4262.0, float(b["20"]["y"]) - 17.0),
        "TP_B_GMOSI": (4262.0, float(b["19"]["y"]) + 14.5),
        "TP_B_GCS": (4262.0, float(b["18"]["y"]) + 15.5),
    }
    for refdes, (x, y, net) in signal_tps.items():
        output.append(make_testpoint(refdes, x, y, net))
    for refdes, (x, y) in ground_tps.items():
        output.append(make_testpoint(refdes, x, y, "GND"))
        extra_vias.append((refdes+":via", x, y, "GND"))

    # Local EN and decoupling copper; the future buck output joins each 3V3
    # trunk at the bulk-capacitor pad.  The short 3V3 tree is on Bottom so it
    # cannot cut through the compact Top-layer EN network.
    for side in ("A", "B"):
        pads = module_pads[side]
        net3v3, en = f"{side}_3V3", f"{side}_EN"
        cap_refs = [f"C_{side}_IN10", f"C_{side}_IN1", f"C_{side}_IN01"]
        module_3v3 = (float(pads["2"]["x"]), float(pads["2"]["y"]))
        trunk_x = 4021.5 if side == "A" else 4162.0
        trunk_top = module_3v3[1] + 3.0
        trunk_bottom = max(locations[ref]["2"][1] for ref in cap_refs)
        routes.extend([
            {"label":side+"_3V3_ENTRY", "signal":"ESP_POWER", "net":net3v3,
             "layer":2, "points":[module_3v3,(trunk_x,trunk_top)], "width":POWER_WIDTH},
            {"label":side+"_3V3_TRUNK", "signal":"ESP_POWER", "net":net3v3,
             "layer":2, "points":[(trunk_x,trunk_top),(trunk_x,trunk_bottom)], "width":POWER_WIDTH},
        ])
        extra_vias.append((side+"_3V3_MODULE:via", *module_3v3, net3v3))
        for ref in cap_refs:
            cap_3v3 = locations[ref]["2"]
            routes.append({"label":ref+"_3V3", "signal":"ESP_POWER", "net":net3v3,
                           "layer":2, "points":[(trunk_x,cap_3v3[1]),cap_3v3], "width":POWER_WIDTH})
            extra_vias.append((ref+":3v3", *cap_3v3, net3v3))
        pull_3v3 = locations[f"R_{side}_EN_PU"]["2"]
        routes.append({"label":side+"_EN_PULLUP_3V3", "signal":"ESP_POWER", "net":net3v3,
                       "layer":2, "points":[(trunk_x,pull_3v3[1]),pull_3v3], "width":POWER_WIDTH})
        extra_vias.append((side+"_EN_PULLUP_3V3:via", *pull_3v3, net3v3))
        en_pull = locations[f"R_{side}_EN_PU"]["1"]
        en_cap = locations[f"C_{side}_EN"]["1"]
        module_en = (float(pads["3"]["x"]), float(pads["3"]["y"]))
        safe_y = en_pull[1] + 1.0
        en_escape_x = en_pull[0] + 3.0
        en_points = [module_en, (module_en[0], safe_y), (en_pull[0], safe_y), en_pull,
                     (en_escape_x,en_pull[1]),(en_escape_x,en_cap[1]),en_cap]
        routes.append({"label":side+"_EN_LOCAL", "signal":en, "net":en, "layer":1,
                       "points":en_points})
        for ref in cap_refs:
            extra_vias.append((ref+":gnd", *locations[ref]["1"], "GND"))
        extra_vias.append((f"C_{side}_EN:gnd", *locations[f"C_{side}_EN"]["2"], "GND"))

    # Serialize routes only after all route lists are complete.
    for route in routes:
        if route.get("via"):
            x, y = route["points"][0]  # type: ignore[index]
            output.append(make_via(str(route["label"]), x, y, str(route["net"])))
        else:
            output.append(make_track(str(route["label"]), str(route["net"]), int(route["layer"]),
                                     route["points"], float(route.get("width", SIGNAL_WIDTH))))  # type: ignore[arg-type]
    for label, x, y, net in extra_vias:
        output.append(make_via(label, x, y, net))

    metrics = route_metrics(routes)
    # Check every serialized segment, including the completed local power and
    # ground copper.  Unrouted buck/USB/CN1 regions contain no speculative
    # tracks and remain explicit blockers rather than audit exceptions.
    checked_routes = routes
    clearance = validate_clearance(checked_routes)
    pad_clearance = validate_routed_copper_to_pads(checked_routes, output, extra_vias)
    return output, {
        "series_resistors": [item[0] for item in spi_resistors],
        "cs_pullups": ["R_ACS_PU", "R_BCS_PU"],
        "signal_testpoints": sorted(signal_tps), "ground_testpoints": sorted(ground_tps),
        "buffer_bypass_capacitors": bypass_caps,
        "esp_reset_and_entry_decoupling": esp_caps,
        "buffer_enable_parts": enable_parts,
        "buffer_input_pulldowns": sorted(input_pulldowns),
        "led_output_damping_resistors": sorted(output_resistors, key=lambda value: int(value[5:])),
        "spi_route_metrics": {name:value for name,value in metrics.items()
                              if name in {"A_SCLK","A_MOSI","A_CS","A_MISO",
                                          "B_SCLK","B_MOSI","B_CS","B_MISO"}},
        "esp_to_buffer_route_metrics": {name:value for name,value in metrics.items()
                                        if name in input_routes},
        "buffer_output_route_metrics": {name:value for name,value in metrics.items()
                                        if name in output_route_nets},
        "minimum_different_net_track_centre_clearance_mm": round(clearance * EDA_UNIT_MM, 3),
        "minimum_conservative_different_net_copper_edge_clearance_mm": round(
            pad_clearance * EDA_UNIT_MM, 3
        ),
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
                shape = transform_component(shape, dy=-44.0)
                placement_changes.append({"refdes":refdes,"role":"A/SPI0/LED1-8",
                    "change":"moved 11.176 mm upward","reason":"open a routed GPIO-to-buffer fanout corridor"})
            elif refdes == "1_SMD":
                shape, changes = rewrite_pads(shape, refdes, B_MODULE_NETS)
                pad_changes.extend(changes)
                shape = transform_component(shape, dy=-16.0)
                placement_changes.append({"refdes":refdes,"role":"B/SPI1/LED9-16",
                    "change":"kept GPIO edge toward buffers and moved 4.064 mm upward",
                    "reason":"Wi-Fi is unused; direct ESP-to-HCT routing takes priority"})
            elif refdes == "U12":
                shape, changes = rewrite_pads(shape, refdes, PI_NETS)
                pad_changes.extend(changes)
            elif refdes in BUFFER_NETS:
                shape, changes = rewrite_pads(shape, refdes, BUFFER_NETS[refdes])
                pad_changes.extend(changes)
                if refdes.startswith("TRAN2"):
                    shape = transform_component(shape, dy=-32.0)
                    placement_changes.append({"refdes":refdes,"role":"A output buffer",
                        "change":"moved 8.128 mm upward","reason":"clear routed damping and connector corridors"})
        shapes.append(shape)

    shapes.append(make_ground_plane(21, "L2_GND"))
    shapes.append(make_ground_plane(22, "L3_GND"))
    for name, zone in PLACEMENT_ZONES.items():
        shapes.append(make_zone(name, zone))
    shapes, route_manifest = add_review_layout(shapes)
    output["shape"] = shapes

    layers = output.get("layers")
    if not isinstance(layers, list):
        raise ValueError("missing layers")
    output["layers"] = [
        "21~Inner1_GND~#999966~true~false~true~0~Signal" if str(layer).startswith("21~") else
        "22~Inner2_GND~#008000~true~false~true~0~Signal" if str(layer).startswith("22~") else str(layer)
        for layer in layers
    ]
    pad_count, nets = pad_inventory(shapes)
    router = output.get("routerRule")
    if not isinstance(router, dict):
        raise ValueError("missing routerRule")
    router.update({"trackWidth":0.254,"trackClearance":0.152,"viaHoleD":0.305,"viaDiameter":0.61,
                   "routerLayers":[1,21,22,2],"smdClearance":0.152,"specialNets":[],
                   "nets":nets,"padsCount":pad_count})
    drc = output.get("DRCRULE")
    if isinstance(drc, dict):
        drc.update({"Default":{"trackWidth":1.0,"clearance":0.6,"viaHoleDiameter":2.4,"viaHoleD":1.2},
                    "isRealtime":True,"isDrcOnRoutingOrPlaceVia":True,"checkObjectToCopperarea":True})
    head = output.get("head")
    if not isinstance(head, dict):
        raise ValueError("missing header")
    params = head.setdefault("c_para", {})
    if not isinstance(params, dict):
        raise ValueError("invalid c_para")
    params.update({"Revision":"Rev5 analyzed routing scaffold","Status":"NOT FOR FABRICATION",
                   "ReceiverRoles":"2_SMD=A/SPI0; 1_SMD=B/SPI1",
                   "LayerIntent":"Top signals / Inner1 GND-dominant plus OE and B_CS / Inner2 solid GND / Bottom signals",
                   "Scope":"SPI and ESP-to-HCT routed; buck, USB, LED ESD and connector still require approved footprints",
                   "RadioIntent":"Wi-Fi/Bluetooth unused; no antenna copper exclusion claimed"})

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
        "radio_layout_note":"Wi-Fi/Bluetooth unused; antenna exclusion zones intentionally removed",
        "remaining_pad_count":pad_count,
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
    expected_maps = {"2_SMD":A_MODULE_NETS,"1_SMD":B_MODULE_NETS,"U12":PI_NETS, **BUFFER_NETS}
    for refdes, expected in expected_maps.items():
        actual = component_pads(shapes, refdes)
        bad = {number:(net,actual.get(number,{}).get("net")) for number,net in expected.items()
               if actual.get(number,{}).get("net") != net}
        if bad:
            raise ValueError(f"{refdes} net mismatch: {bad}")
    logical_mapping = {}
    for mapping in BUFFER_NETS.values():
        for input_pin, output_pin in (("2", "3"), ("5", "6"), ("9", "8"), ("12", "11")):
            logical_mapping[mapping[input_pin]] = mapping[output_pin]
    if logical_mapping != LOGICAL_GPIO_TO_LED:
        raise ValueError(f"logical GPIO-to-LED mapping changed: {logical_mapping}")
    a_pads, b_pads = component_pads(shapes,"2_SMD"), component_pads(shapes,"1_SMD")
    if not math.isclose(float(a_pads["19"]["y"]),3439.5,abs_tol=.01):
        raise ValueError("receiver A placement failed")
    if not (math.isclose(float(b_pads["19"]["x"]),4235.02,abs_tol=.01) and
            math.isclose(float(b_pads["19"]["y"]),3427.5,abs_tol=.01)):
        raise ValueError("receiver B placement/orientation failed")
    copper = [item for item in shapes if kind(item)=="COPPERAREA"]
    if len(copper)!=2 or {int(item.split("~")[2]) for item in copper}!={21,22}:
        raise ValueError("dual inner-layer ground areas missing")
    cutouts=[item for item in shapes if kind(item)=="SOLIDREGION" and "~cutout~" in item]
    if cutouts:
        raise ValueError("radio antenna cutouts unexpectedly remain")
    spi_nets = {
        "A_SPI0_MOSI_PI","A_SPI0_MOSI","A_SPI0_SCLK_PI","A_SPI0_SCLK",
        "A_SPI0_CS_PI","A_SPI0_CS","A_SPI0_MISO","A_SPI0_MISO_PI",
        "B_SPI1_MOSI_PI","B_SPI1_MOSI","B_SPI1_SCLK_PI","B_SPI1_SCLK",
        "B_SPI1_CS_PI","B_SPI1_CS","B_SPI1_MISO","B_SPI1_MISO_PI",
    }
    gpio_nets = {f"{side}_GPIO{gpio}" for side in ("A","B") for gpio in (4,5,6,7,15,16,17,18)}
    control_nets = {f"{side}_{suffix}" for side in ("A","B")
                    for suffix in ("LED_OE_N","LED_ENABLE","EN")}
    led_driver_nets = {f"LED{index}_DRV" for index in range(1,17)}
    connectivity_groups = validate_net_connectivity(
        shapes, spi_nets | gpio_nets | control_nets | led_driver_nets
    )
    metrics=manifest["spi_route_metrics"]
    assert isinstance(metrics,dict)
    limits={"SCLK":75.0,"MOSI":90.0,"MISO":90.0,"CS":90.0}
    for signal,values in metrics.items():
        assert isinstance(values,dict)
        via_limit = 2 if signal in {"B_CS", "B_MISO"} else 1
        if float(values["length_mm"])>limits[signal.split("_",1)[1]] or int(values["signal_vias"])>via_limit:
            raise ValueError(f"route limit failed for {signal}: {values}")
    manifest["validation"]={
        "easyeda_document_type":3,"all_v4_tracks_and_vias_removed_before_rerouting":True,
        "fixed_dual_spi_pad_assignments":"passed","logical_gpio_to_led_mapping":"passed",
        "route_length_and_via_limits":"passed",
        "serialized_critical_connectivity_nets":connectivity_groups,
        "complete_esp_to_hct_routes":16,"buffer_output_to_damping_routes":16,
        "different_net_track_clearance":"passed structural centre-line check",
        "different_net_copper_geometry":(
            "passed conservative pad/pad, track/pad, via/pad, track/via, and via/via checks"
        ),
        "antenna_exclusion":"intentionally omitted because all radios are unused",
        "inner_ground_copper_areas_present":"L2 ground-dominant; L3 uninterrupted solid GND",
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
