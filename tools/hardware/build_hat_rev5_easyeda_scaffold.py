#!/usr/bin/env python3
"""Build an unrouted EasyEDA Standard PCB scaffold for LED HAT Rev5.

The input is the V4 EasyEDA Standard PCB JSON export. The generated board keeps
the proven outline, holes, component footprints, and placement as a mechanical
starting point, but deliberately removes every track and via. It also removes
the SPI/CE selector footprints and assigns the Raspberry Pi and ESP32-S3 pads
to two independent, fixed SPI buses.

This is a design-starting artifact, not a fabrication output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path


SELECTOR_PREFIX = "SJ_SPI"
SELECTOR_LABELS = {"SPI0", "SPI1", "CE0", "CE1", "CE2", "CE3"}
STALE_BOARD_TITLES = {"LED GRID WALL HAT V0.4"}

# Raspberry Pi physical pin -> authoritative fixed Rev5 net.
PI_HEADER_NETS = {
    "19": "1IO11",  # SPI0 MOSI -> receiver A GPIO11
    "21": "1IO13",  # SPI0 MISO <- receiver A GPIO13
    "23": "1IO12",  # SPI0 SCLK -> receiver A GPIO12
    "24": "1IO10",  # SPI0 CE0  -> receiver A GPIO10
    "38": "2IO11",  # SPI1 MOSI -> receiver B GPIO11
    "35": "2IO13",  # SPI1 MISO <- receiver B GPIO13
    "40": "2IO12",  # SPI1 SCLK -> receiver B GPIO12
    "12": "2IO10",  # SPI1 CE0  -> receiver B GPIO10
}

# ESP32-S3 module pad -> corrected receiver-B net.
RECEIVER_B_NETS = {
    "19": "2IO11",
    "20": "2IO12",
    "21": "2IO13",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def component_ref(shape: str) -> str | None:
    for section in shape.split("#@$"):
        fields = section.split("~")
        if len(fields) > 10 and fields[0] == "TEXT" and fields[1] == "P":
            return fields[10]
    return None


def rewrite_component_pads(
    shape: str,
    refdes: str,
    pad_nets: dict[str, str],
) -> tuple[str, list[dict[str, str]]]:
    sections = shape.split("#@$")
    changes: list[dict[str, str]] = []
    for index, section in enumerate(sections):
        fields = section.split("~")
        if len(fields) <= 8 or fields[0] != "PAD":
            continue
        pad_number = fields[8]
        if pad_number not in pad_nets:
            continue
        old_net = fields[7]
        new_net = pad_nets[pad_number]
        if old_net != new_net:
            fields[7] = new_net
            sections[index] = "~".join(fields)
            changes.append(
                {
                    "refdes": refdes,
                    "pad": pad_number,
                    "old_net": old_net,
                    "new_net": new_net,
                }
            )
    return "#@$".join(sections), changes


def shape_kind(shape: str) -> str:
    return shape.split("~", 1)[0]


def text_value(shape: str) -> str | None:
    fields = shape.split("~")
    if len(fields) > 10 and fields[0] == "TEXT":
        return fields[10]
    return None


def pad_inventory(shapes: list[str]) -> tuple[int, list[str]]:
    count = 0
    nets: set[str] = set()
    for shape in shapes:
        if shape_kind(shape) != "LIB":
            continue
        for section in shape.split("#@$"):
            fields = section.split("~")
            if len(fields) > 8 and fields[0] == "PAD":
                count += 1
                if fields[7]:
                    nets.add(fields[7])
    return count, sorted(nets)


def component_pad_nets(shapes: list[str], wanted_refdes: str) -> dict[str, str]:
    for shape in shapes:
        if shape_kind(shape) != "LIB" or component_ref(shape) != wanted_refdes:
            continue
        result: dict[str, str] = {}
        for section in shape.split("#@$"):
            fields = section.split("~")
            if len(fields) > 8 and fields[0] == "PAD":
                result[fields[8]] = fields[7]
        return result
    raise ValueError(f"component {wanted_refdes!r} was not found")


def build_scaffold(source: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    if source.get("head", {}).get("docType") != "3":  # type: ignore[union-attr]
        raise ValueError("input is not an EasyEDA Standard PCB document (docType 3)")

    original_shapes = source.get("shape")
    if not isinstance(original_shapes, list) or not all(isinstance(item, str) for item in original_shapes):
        raise ValueError("input PCB does not contain a valid shape array")

    output = json.loads(json.dumps(source))
    kept_shapes: list[str] = []
    removed_selectors: list[str] = []
    removed_labels: list[str] = []
    removed_stale_titles: list[str] = []
    pad_changes: list[dict[str, str]] = []

    for shape in original_shapes:
        kind = shape_kind(shape)
        if kind in {"TRACK", "VIA"}:
            continue
        if kind == "TEXT" and text_value(shape) in SELECTOR_LABELS:
            removed_labels.append(text_value(shape) or "")
            continue
        if kind == "TEXT" and text_value(shape) in STALE_BOARD_TITLES:
            removed_stale_titles.append(text_value(shape) or "")
            continue
        if kind == "LIB":
            refdes = component_ref(shape)
            if refdes and refdes.startswith(SELECTOR_PREFIX):
                removed_selectors.append(refdes)
                continue
            if refdes == "2_SMD":
                shape, changes = rewrite_component_pads(shape, refdes, RECEIVER_B_NETS)
                pad_changes.extend(changes)
            elif refdes == "U12":
                shape, changes = rewrite_component_pads(shape, refdes, PI_HEADER_NETS)
                pad_changes.extend(changes)
        kept_shapes.append(shape)

    output["shape"] = kept_shapes

    layers = output.get("layers")
    if not isinstance(layers, list):
        raise ValueError("input PCB does not contain a valid layers array")
    configured_layers: list[str] = []
    for layer in layers:
        layer_id = str(layer).split("~", 1)[0]
        if layer_id == "21":
            configured_layers.append("21~Inner1_GND~#999966~true~false~true~0~Signal")
        elif layer_id == "22":
            configured_layers.append("22~Inner2_PWR~#008000~true~false~true~0~Signal")
        else:
            configured_layers.append(str(layer))
    output["layers"] = configured_layers

    router_rule = output.get("routerRule")
    if not isinstance(router_rule, dict):
        raise ValueError("input PCB does not contain valid router rules")
    pad_count, nets = pad_inventory(kept_shapes)
    router_rule["routerLayers"] = [1, 21, 22, 2]
    router_rule["specialNets"] = []
    router_rule["nets"] = nets
    router_rule["padsCount"] = pad_count

    head = output.get("head")
    if not isinstance(head, dict):
        raise ValueError("input PCB does not contain a valid header")
    parameters = head.setdefault("c_para", {})
    if not isinstance(parameters, dict):
        raise ValueError("input PCB header c_para is not an object")
    parameters.update(
        {
            "Revision": "Rev5 SPI scaffold",
            "Status": "NOT FOR FABRICATION",
            "DerivedFrom": "LED Grid Wall HAT V0.4 EasyEDA Standard PCB source",
            "Routing": "Unrouted; all V4 tracks and vias deliberately removed",
            "LayerIntent": "Top signal / Inner1 GND / Inner2 power / Bottom signal",
        }
    )

    input_counts = Counter(shape_kind(shape) for shape in original_shapes)
    output_counts = Counter(shape_kind(shape) for shape in kept_shapes)
    manifest: dict[str, object] = {
        "artifact": "LED Grid Wall HAT Rev5 EasyEDA SPI scaffold",
        "status": "NOT FOR FABRICATION",
        "source_shape_counts": dict(sorted(input_counts.items())),
        "output_shape_counts": dict(sorted(output_counts.items())),
        "removed_selector_footprints": sorted(removed_selectors),
        "removed_selector_labels": sorted(removed_labels),
        "removed_stale_board_titles": sorted(removed_stale_titles),
        "pad_net_changes": sorted(pad_changes, key=lambda item: (item["refdes"], int(item["pad"]))),
        "routing_layers": ["TopLayer", "Inner1_GND", "Inner2_PWR", "BottomLayer"],
        "remaining_pad_count": pad_count,
        "remaining_named_nets": len(nets),
    }

    validate_scaffold(output, manifest)
    return output, manifest


def validate_scaffold(board: dict[str, object], manifest: dict[str, object]) -> None:
    shapes = board["shape"]
    assert isinstance(shapes, list)

    forbidden = [shape_kind(shape) for shape in shapes if shape_kind(shape) in {"TRACK", "VIA"}]
    if forbidden:
        raise ValueError("generated scaffold unexpectedly contains copper routing")

    refs = {component_ref(shape) for shape in shapes if shape_kind(shape) == "LIB"}
    stale_selectors = sorted(ref for ref in refs if ref and ref.startswith(SELECTOR_PREFIX))
    if stale_selectors:
        raise ValueError(f"generated scaffold still contains selectors: {stale_selectors}")

    expected_component_nets = {
        "1_SMD": {"18": "1IO10", "19": "1IO11", "20": "1IO12", "21": "1IO13"},
        "2_SMD": {"18": "2IO10", "19": "2IO11", "20": "2IO12", "21": "2IO13"},
        "U12": PI_HEADER_NETS,
    }
    for refdes, expected in expected_component_nets.items():
        actual = component_pad_nets(shapes, refdes)
        mismatches = {
            pad: {"expected": net, "actual": actual.get(pad)}
            for pad, net in expected.items()
            if actual.get(pad) != net
        }
        if mismatches:
            raise ValueError(f"{refdes} fixed-SPI pad validation failed: {mismatches}")

    router_rule = board.get("routerRule")
    if not isinstance(router_rule, dict) or router_rule.get("routerLayers") != [1, 21, 22, 2]:
        raise ValueError("generated scaffold does not enable the intended four copper layers")

    parameters = board.get("head", {}).get("c_para", {})  # type: ignore[union-attr]
    if not isinstance(parameters, dict) or parameters.get("Status") != "NOT FOR FABRICATION":
        raise ValueError("generated scaffold is missing its safety status marker")

    manifest["validation"] = {
        "easyeda_document_type": 3,
        "unrouted": True,
        "fixed_spi_pad_assignments": "passed",
        "selector_footprints_absent": True,
        "four_copper_layers_enabled": True,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pcb-input", type=Path, required=True)
    parser.add_argument("--pcb-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path, required=True)
    parser.add_argument("--schematic-input", type=Path)
    parser.add_argument("--schematic-reference-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = json.loads(args.pcb_input.read_text(encoding="utf-8"))
    board, manifest = build_scaffold(source)

    args.pcb_output.parent.mkdir(parents=True, exist_ok=True)
    args.pcb_output.write_text(json.dumps(board, indent=2) + "\n", encoding="utf-8")
    manifest["source_pcb"] = {
        "file": args.pcb_input.name,
        "sha256": sha256(args.pcb_input),
    }
    manifest["generated_pcb"] = {
        "file": args.pcb_output.name,
        "sha256": sha256(args.pcb_output),
    }

    if bool(args.schematic_input) != bool(args.schematic_reference_output):
        raise ValueError("provide both schematic arguments or neither")
    if args.schematic_input and args.schematic_reference_output:
        args.schematic_reference_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(args.schematic_input, args.schematic_reference_output)
        manifest["schematic_reference"] = {
            "file": args.schematic_reference_output.name,
            "sha256": sha256(args.schematic_reference_output),
            "note": "Unmodified V4 reference; not a Rev5 schematic",
        }

    args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.pcb_output}")
    print(f"wrote {args.manifest_output}")
    if args.schematic_reference_output:
        print(f"copied {args.schematic_reference_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
