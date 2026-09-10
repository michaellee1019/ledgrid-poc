"""Focused Scene-v2 proof for the five authored light sculptures."""

from __future__ import annotations

import copy
import base64
import json
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import resolve_scene
from animation.libraries.procedural_sculptures import SEMANTIC_PALETTES
from animation.plugins.cellular_tapestry import CellularTapestryAnimation
from animation.plugins.flow_field_silk import FlowFieldSilkAnimation
from animation.plugins.frostwork import FrostworkAnimation
from animation.plugins.living_stained_glass import LivingStainedGlassAnimation
from animation.plugins.quasicrystal_bloom import QuasicrystalBloomAnimation
from ipc.scene_contract import canonical_json_bytes, normalize_composer_scene
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_component_presets import ComponentPresetCatalog
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
RENDERERS = {
    "cellular_tapestry": CellularTapestryAnimation, "flow_field_silk": FlowFieldSilkAnimation,
    "frostwork": FrostworkAnimation, "living_stained_glass": LivingStainedGlassAnimation,
    "quasicrystal_bloom": QuasicrystalBloomAnimation,
}
COUNTS = {"cellular_tapestry": 3, "flow_field_silk": 3, "frostwork": 4,
          "living_stained_glass": 8, "quasicrystal_bloom": 4}
SCENE_OWNED_RAW_KEYS = frozenset({
    "brightness", "mood", "plant_aware", "plant_modifiers", "calibration",
    "geometry", "mask",
})
REWRITTEN_DESCRIPTIONS = {
    ("cellular_tapestry", "night"), ("cellular_tapestry", "showcase"),
    ("flow_field_silk", "night"), ("flow_field_silk", "showcase"),
    ("living_stained_glass", "night"), ("living_stained_glass", "showcase"),
    ("quasicrystal_bloom", "showcase"),
}
UNCHANGED_DESCRIPTIONS = {
    ("cellular_tapestry", "quiet"): "Slow triangular lace accumulates one row at a time.",
    ("flow_field_silk", "quiet"): "A few coherent blue fibers drift through deep water.",
    ("frostwork", "night"): "Sparse cold branches kept dim for late hours.",
    ("frostwork", "pastel-daybreak"): "Soft rose and blue crystals grow slowly against a radiant winter morning.",
    ("frostwork", "quiet"): "Slow blue-white border crystals for calm room light.",
    ("frostwork", "showcase"): "Dense magenta-cyan branching frost with visible regrowth.",
    ("living_stained_glass", "aurora-transept"): "Green-blue panes migrate at a measured pace across a luminous northern window.",
    ("living_stained_glass", "candlelight-mosaic"): "Amber and cream panes breathe slowly like a wall lit by many candles.",
    ("living_stained_glass", "daylight-rose"): "A radiant white-and-sky rose window evolves with broad, readable panes.",
    ("living_stained_glass", "pastel-chapel"): "Blush, lavender, and powder-blue panes drift with a gentle luminous glow.",
    ("living_stained_glass", "quiet"): "Stable ocean-colored panes breathe with soft daylight.",
    ("living_stained_glass", "synthwave-basilica"): "Fast magenta and cyan panes pulse over radiant midnight-violet glass.",
    ("quasicrystal_bloom", "daylight-prism"): "Eightfold crystalline geometry turns slowly over a radiant daylight field.",
    ("quasicrystal_bloom", "night"): "Low-contrast fivefold bands preserve deep negative space.",
    ("quasicrystal_bloom", "quiet"): "Broad tenfold sea-glass rosettes evolve without scrolling.",
}


class SculptureShowcaseSceneV2Tests(unittest.TestCase):
    def setUp(self): self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    @staticmethod
    def scene(component_id):
        scene = _current_scene()
        scene["animation"] = {"component_id": component_id, "version": 1, "provider": "python", "role": "animation", "parameters": {}}
        return scene

    def test_exact_authored_rows_are_qualified_choices_without_default(self):
        presets = ComponentPresetCatalog(ROOT, {key: cls._normalized_parameters for key, cls in RENDERERS.items()})
        self.assertEqual(sum(COUNTS.values()), 22)
        for component_id, count in COUNTS.items():
            paths = sorted((ROOT / "animation/plugins" / component_id / "presets").glob("*.json"))
            self.assertEqual(len(paths), count); self.assertNotIn("default", [path.stem for path in paths])
            choices = presets.choices(component_id); self.assertEqual(len(choices), count)
            for path, choice in zip(paths, choices):
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(path.stem, choice["preset_id"])
                self.assertSetEqual(set(choice["parameters"]), set(RENDERERS[component_id].COMPONENT_DEFAULTS))
                self.assertNotIn("brightness", choice["parameters"]); self.assertNotIn("speed", choice["parameters"])
                self.assertFalse(SCENE_OWNED_RAW_KEYS & set(raw["params"]))
                self.assertFalse(any(key.startswith("background") for key in raw["params"]))

    def test_exactly_seven_visible_descriptions_are_rewritten_without_touching_frost_copy(self):
        rewritten = {
            (component_id, path.stem)
            for component_id in COUNTS
            for path in (ROOT / "animation/plugins" / component_id / "presets").glob("*.json")
            if (component_id, path.stem) in REWRITTEN_DESCRIPTIONS
        }
        self.assertSetEqual(rewritten, REWRITTEN_DESCRIPTIONS)
        for component_id, preset_id in REWRITTEN_DESCRIPTIONS:
            payload = json.loads((ROOT / "animation/plugins" / component_id / "presets" / f"{preset_id}.json").read_text(encoding="utf-8"))
            self.assertNotRegex(payload["description"], r"(?i)plant|foliage|mask|globe|calibrat")
        self.assertEqual(len(REWRITTEN_DESCRIPTIONS) + len(UNCHANGED_DESCRIPTIONS), 22)
        for (component_id, preset_id), expected in UNCHANGED_DESCRIPTIONS.items():
            payload = json.loads((ROOT / "animation/plugins" / component_id / "presets" / f"{preset_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["description"], expected)

    def test_all_sculpture_presets_apply_and_round_trip_without_global_mutation(self):
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        catalog = current_component_catalog()
        for component_id, count in COUNTS.items():
            choices = interface.composer_presets.choices(component_id)
            self.assertEqual(len(choices), count)
            for choice in choices:
                source = self.scene(component_id)
                source["look"]["palette_id"] = "ember"
                source["look"]["pace"] = 1.25
                source["look"]["presentation_brightness"] = 0.61
                source["background"]["parameters"]["gain"] = 0.37
                source["plants"]["effects"] = {"version": 1, "active": ["shadow"], "strengths": {"shadow": 0.4}}
                before = copy.deepcopy(source)
                applied = interface.composer_presets.apply(source, choice["preset_id"])
                self.assertEqual(source, before)
                self.assertEqual(applied["background"], before["background"])
                self.assertEqual(applied["look"], before["look"])
                self.assertEqual(applied["plants"], before["plants"])
                self.assertEqual(applied["animation"]["parameters"], choice["parameters"])
                canonical = normalize_composer_scene({"origin": "composer", "scene": applied}, catalog).scene
                self.assertEqual(canonical["background"], before["background"])
                self.assertEqual(canonical["look"], before["look"])
                self.assertEqual(canonical["plants"], before["plants"])
                self.assertEqual(canonical["animation"]["parameters"], choice["parameters"])

    def test_catalog_preview_distinct_bounded_and_semantic(self):
        catalog = current_component_catalog(); fingerprints = set()
        for component_id, renderer in RENDERERS.items():
            descriptor = renderer.component_descriptor()
            self.assertIs(catalog.require(provider="python", component_id=component_id, version=1), descriptor)
            self.assertEqual((descriptor.alpha_behavior.value, descriptor.palette_policy.value), ("opaque", "semantic"))
            animation = renderer(self.controller, renderer.COMPONENT_DEFAULTS)
            first, next_frame = animation.generate_frame(0., 0), animation.generate_frame(1.2, 1)
            self.assertEqual(first.pixels.shape, (33 * 138, 3)); self.assertEqual(first.pixels.dtype, np.uint8)
            self.assertFalse(np.array_equal(first.pixels, next_frame.pixels)); fingerprints.add(first.pixels.tobytes())
            canonical = normalize_composer_scene({"origin":"composer", "scene":self.scene(component_id)}, catalog)
            frame = ComposerFinalPreview(catalog, ROOT).render(canonical, 1., datetime.now().astimezone())
            self.assertEqual(frame.pixels.shape, (33 * 138, 3))
        self.assertEqual(len(fingerprints), len(RENDERERS))

    def test_every_named_sculpture_preset_uses_resolved_scene_palette_in_production_preview(self):
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        catalog = current_component_catalog()
        checked = 0
        for component_id, renderer in RENDERERS.items():
            if component_id == "quasicrystal_bloom":
                continue
            for choice in interface.composer_presets.choices(component_id):
                selected = interface.composer_presets.apply(self.scene(component_id), choice["preset_id"])
                palette_frames = {}
                for palette_id in ("mist", "ember"):
                    candidate = copy.deepcopy(selected)
                    candidate["look"]["palette_id"] = palette_id
                    resolved = resolve_scene(candidate, catalog, monotonic_elapsed=2.0)
                    animation = renderer(self.controller, candidate["animation"]["parameters"])
                    rng_before = copy.deepcopy(animation.rng.bit_generator.state)
                    tick_before = animation._last_sim_tick
                    animation.set_presentation_context(resolved)
                    self.assertEqual(animation.rng.bit_generator.state, rng_before)
                    self.assertEqual(animation._last_sim_tick, tick_before)
                    self.assertTrue(animation.render_resolved_scene(resolved).changed)
                    self.assertFalse(animation.render_resolved_scene(resolved).changed)
                    np.testing.assert_array_equal(animation.palette(), SEMANTIC_PALETTES[palette_id])
                    response = client.post("/api/composer/preview", json={
                        "origin": "composer", "scene": candidate,
                        "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-09-01T12:00:00+00:00"},
                    })
                    self.assertEqual(response.status_code, 200, f"{component_id}/{choice['preset_id']}/{palette_id}")
                    frame = response.get_json()["frame"]
                    self.assertEqual((frame["width"], frame["height"], frame["encoding"]), (33, 138, "rgb_u8_base64"))
                    pixels = np.frombuffer(base64.b64decode(frame["pixels"]), dtype=np.uint8)
                    self.assertEqual(pixels.shape, (33 * 138 * 3,))
                    palette_frames[palette_id] = pixels.copy()
                self.assertFalse(np.array_equal(palette_frames["mist"], palette_frames["ember"]))
                checked += 1
        self.assertEqual(checked, 18)

    def test_api_normalizes_browser_numbers_and_rejects_before_mutation(self):
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True); client = interface.app.test_client()
        for component_id, count in COUNTS.items(): self.assertEqual(len(client.get(f"/api/composer/components/{component_id}/presets").get_json()["presets"]), count)
        authored = interface.composer_presets.apply(self.scene("living_stained_glass"), "showcase")
        authored["animation"]["parameters"]["light_direction"] = 0.0
        browser = copy.deepcopy(authored); browser["animation"]["parameters"]["light_direction"] = 0
        canonical = interface._composer_canonical({"origin":"composer", "scene":authored})
        self.assertEqual(canonical.canonical_bytes, interface._composer_canonical({"origin":"composer", "scene":browser}).canonical_bytes)
        self.assertEqual(canonical.canonical_bytes, canonical_json_bytes(canonical.scene))
        accepted = client.post("/api/composer/scene", json={"origin":"composer", "scene":browser, "client_id":"sculpture", "client_sequence":1})
        self.assertEqual(accepted.status_code, 200); body = accepted.get_json(); self.assertEqual(body["current"], body["desired"]); self.assertEqual(body["desired"], body["observed"])
        baseline = client.get("/api/composer/recovery?client_id=sculpture").get_json()
        invalid = copy.deepcopy(browser); invalid["animation"]["parameters"]["lead_width"] = 4
        self.assertEqual(client.post("/api/composer/preview", json={"origin":"composer", "scene":invalid, "preview":{"monotonic_elapsed":1., "wall_time":"2026-09-01T12:00:00+00:00"}}).status_code, 400)
        self.assertEqual(client.post("/api/composer/scene", json={"origin":"composer", "scene":invalid, "client_id":"sculpture", "client_sequence":2}).status_code, 400)
        self.assertEqual(client.get("/api/composer/recovery?client_id=sculpture").get_json(), baseline)

    def test_selected_controls_preserve_hidden_parameters_and_render_only_selected_group(self):
        script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        for token in ("const sculptureIds", "sculptureParameters = (id, existing = {}) => ({...existing", "Object.values(sculptureControls).flat().forEach", "...Object.entries(sculpturePresetTargets)", "installSculptureControls()"):
            self.assertIn(token, script)


if __name__ == "__main__": unittest.main()
