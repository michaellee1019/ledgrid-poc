#!/usr/bin/env python3
"""Build inexpensive Gallery images from warmed production Scene renders.

Run with the project Python environment. These are catalog illustrations, not
receipts of the installed output; no controller or publication is touched.
"""
from __future__ import annotations

import hashlib
import importlib
import io
import json
from datetime import datetime, timedelta
from pathlib import Path
import sys

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ipc.scene_contract import normalize_composer_scene  # noqa: E402
from web.composer_component_presets import ComponentPresetCatalog  # noqa: E402
from web.composer_final_preview import (  # noqa: E402
    ComposerFinalPreview, NATIVE_AURORA_BUNDLE_DIGEST, current_component_catalog,
)

OUTPUT = ROOT / "web/static/generated/gallery"
WALL_TIME = datetime.fromisoformat("2026-01-01T12:00:00+00:00")
FPS = 30
SECONDS = 8
# Long-lived growth needs real sequential history, even in a still image.
WARMUP_SECONDS = {"cellular_tapestry": 80, "reaction_diffusion_garden": 40,
                  "frostwork": 30, "tetris": 30}
PRESETS = {"gif_animation": "axolotl-bubble-column", "cloud_canyon": "daylight-canyon",
           "cellular_tapestry": "showcase", "reaction_diffusion_garden": "showcase",
           "moonlit_fog_banks": "silver-halo", "rain_on_glass": "pastel-sunshower",
           "tidal_bioluminescence": "showcase", "frostwork": "showcase"}


def preset_catalog(catalog):
    # Some older descriptors omit the optional normalizer. Use the same
    # plugin-owned normalization as Composer's preset endpoint for all ages.
    normalizers = {}
    for descriptor in catalog.descriptors:
        if descriptor.role.value != "animation":
            continue
        module = importlib.import_module(f"animation.plugins.{descriptor.component_id}")
        classes = {value for value in vars(module).values()
                   if isinstance(value, type)
                   and getattr(value, "COMPONENT_ID", None) == descriptor.component_id}
        if len(classes) != 1:
            raise ValueError(f"Ambiguous preset provider: {descriptor.component_id}")
        normalizers[descriptor.component_id] = classes.pop()._normalized_parameters
    return ComponentPresetCatalog(ROOT, normalizers)


def catalog_scene(descriptor, choice=None):
    parameters = descriptor.default_parameters()
    preset = PRESETS.get(descriptor.component_id)
    if preset:
        path = ROOT / "animation/plugins" / descriptor.component_id / "presets" / f"{preset}.json"
        parameters = descriptor.parameter_normalizer(json.loads(path.read_text())["params"])
    scene = {
        "schema": "ledgrid.scene.v2",
        "background": {"component_id": "native_aurora", "version": 1,
                       "provider": "receiver_native", "role": "background",
                       "bundle_digest": NATIVE_AURORA_BUNDLE_DIGEST,
                       "parameters": {"gain": .12, "source_fps": 30, "seed": 4201}},
        "animation": {"component_id": descriptor.component_id,
                      "version": descriptor.version, "provider": descriptor.provider.value,
                      "role": "animation", "parameters": parameters},
        "widgets": [],
        "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
        "look": {"palette_id": "spectrum", "pace": 1.0, "presentation_brightness": 1.4},
    }
    if choice is not None:
        scene["animation"]["parameters"] = choice["parameters"]
        if "installation_effects" in choice:
            scene["plants"]["effects"] = choice["installation_effects"]
    # Match the preset endpoint's JSON boundary (e.g. tuple-valued colors).
    return json.loads(json.dumps(scene))


def render_thumbnail(descriptor, catalog, choice=None):
    scene = catalog_scene(descriptor, choice)
    canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, catalog)
    renderer = ComposerFinalPreview(catalog, ROOT)
    best = None
    best_score = -1.0
    sample_time = 0.0
    # A first call at t=17 does not evolve bounded-step simulations. Advance
    # every sample through the real resolved Scene (including palette/pace).
    seconds = WARMUP_SECONDS.get(descriptor.component_id, SECONDS)
    for tick in range(seconds * FPS + 1):
        elapsed = tick / FPS
        frame = renderer.render(canonical, elapsed, WALL_TIME + timedelta(seconds=elapsed))
        if tick < FPS * (seconds - 3) or tick % (FPS // 2):
            continue
        # Prefer an established, populated frame over a dark/reset interval.
        foreground = frame.foreground.pixels[:, :3]
        score = float(np.maximum(foreground.max(axis=1) - 24.0, 0).sum())
        if score > best_score:
            best_score, sample_time = score, elapsed
            best = frame.pixels.copy()
    assert best is not None
    canvas = best.reshape(33, 138, 3)[:, ::-1, :].transpose(1, 0, 2)
    image = Image.fromarray(canvas)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), scene, sample_time


def main():
    catalog = current_component_catalog()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    entries = {}
    preset_entries = {}
    presets = preset_catalog(catalog)
    for descriptor in sorted(catalog.descriptors, key=lambda item: item.component_id):
        if descriptor.role.value != "animation":
            continue
        data, scene, elapsed = render_thumbnail(descriptor, catalog)
        digest = hashlib.sha256(data).hexdigest()
        filename = f"{descriptor.component_id}-{digest[:16]}.png"
        (OUTPUT / filename).write_bytes(data)
        entries[descriptor.component_id] = {"file": filename, "sha256": digest,
                                             "scene": scene, "elapsed": elapsed,
                                             "preset": PRESETS.get(descriptor.component_id)}
        print(f"{descriptor.component_id}: {len(data)} bytes at {elapsed}s", flush=True)
        choices = presets.choices(descriptor.component_id)
        preset_entries[descriptor.component_id] = {}
        for choice in choices:
            data, scene, elapsed = render_thumbnail(descriptor, catalog, choice)
            digest = hashlib.sha256(data).hexdigest()
            filename = f"{descriptor.component_id}-{choice['preset_id']}-{digest[:16]}.png"
            (OUTPUT / filename).write_bytes(data)
            preset_entries[descriptor.component_id][choice["preset_id"]] = {
                "file": filename, "sha256": digest, "scene": scene, "elapsed": elapsed,
            }
            print(f"  {choice['preset_id']}: {len(data)} bytes", flush=True)
    manifest = {"fps": FPS, "warmup_seconds": SECONDS, "wall_time": WALL_TIME.isoformat(),
                "entries": entries, "presets": preset_entries}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    urls = {key: f"/static/generated/gallery/{value['file']}" for key, value in entries.items()}
    (OUTPUT / "previews.js").write_text(
        "// Generated by tools/generate_gallery_previews.py\nwindow.ComposerGalleryPreviews = Object.freeze("
        + json.dumps(urls, indent=2, sort_keys=True) + ");\n"
        + "window.ComposerPresetPreviews = Object.freeze("
        + json.dumps({component: {key: f"/static/generated/gallery/{value['file']}"
                                 for key, value in choices.items()}
                      for component, choices in preset_entries.items()}, indent=2, sort_keys=True)
        + ");\n"
    )
    # Remove only obsolete images in this generator-owned directory.
    retained = {value["file"] for value in entries.values()}
    retained.update(value["file"] for choices in preset_entries.values() for value in choices.values())
    for path in OUTPUT.glob("*.png"):
        if path.name not in retained:
            path.unlink()


if __name__ == "__main__":
    main()
