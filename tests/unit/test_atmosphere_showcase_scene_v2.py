"""Focused Scene v2 proof for the authored atmosphere instrument packet."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.plugins.circadian_window import CircadianWindowAnimation
from animation.plugins.cloud_canyon import CloudCanyonAnimation
from animation.plugins.desert_wind import DesertWindAnimation
from animation.plugins.moonlit_fog_banks import MoonlitFogBanksAnimation
from animation.plugins.rain_on_glass import RainOnGlassAnimation
from animation.plugins.tidal_bioluminescence import TidalBioluminescenceAnimation
from animation.plugins.waterfall_veil import WaterfallVeilAnimation
from ipc.scene_contract import canonical_json_bytes
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.composer_component_presets import ComponentPresetCatalog
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


ROOT = Path(__file__).resolve().parents[2]
RENDERERS = {
    "circadian_window": CircadianWindowAnimation, "cloud_canyon": CloudCanyonAnimation,
    "desert_wind": DesertWindAnimation, "moonlit_fog_banks": MoonlitFogBanksAnimation,
    "rain_on_glass": RainOnGlassAnimation, "tidal_bioluminescence": TidalBioluminescenceAnimation,
    "waterfall_veil": WaterfallVeilAnimation,
}
EXPECTED = {
    "circadian_window": 4, "cloud_canyon": 4, "desert_wind": 4,
    "moonlit_fog_banks": 4, "rain_on_glass": 4, "tidal_bioluminescence": 3,
    "waterfall_veil": 3,
}


def _browser_json_round_trip(value):
    """Model JSON.stringify's integer-looking number projection from a browser."""

    if isinstance(value, dict):
        return {key: _browser_json_round_trip(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_browser_json_round_trip(item) for item in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


class AtmosphereShowcaseSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    @staticmethod
    def _scene(component_id: str) -> dict:
        scene = _current_scene()
        scene["animation"] = {"component_id": component_id, "version": 1, "provider": "python", "role": "animation", "parameters": {}}
        return scene

    def test_exact_authored_rows_are_the_only_component_choices(self) -> None:
        catalog = ComponentPresetCatalog(ROOT, {key: value._normalized_parameters for key, value in RENDERERS.items()})
        self.assertEqual(sum(EXPECTED.values()), 26)
        for component_id, count in EXPECTED.items():
            paths = sorted((ROOT / "animation/plugins" / component_id / "presets").glob("*.json"))
            self.assertEqual(len(paths), count)
            self.assertNotIn("default", [path.stem for path in paths])
            choices = catalog.choices(component_id)
            self.assertEqual(len(choices), count)
            for path, choice in zip(paths, choices):
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(choice["preset_id"], path.stem)
                self.assertSetEqual(set(choice["parameters"]), set(RENDERERS[component_id].COMPONENT_DEFAULTS))
                self.assertNotIn("brightness", choice["parameters"])
                self.assertNotIn("speed", choice["parameters"])
                self.assertNotIn("plant_aware", choice["parameters"])
                self.assertNotIn("plant_modifiers", choice["parameters"])

    def test_catalog_final_preview_and_bounded_distinct_frames(self) -> None:
        catalog = current_component_catalog()
        fingerprints = set()
        for component_id, renderer in RENDERERS.items():
            self.assertIs(catalog.require(provider="python", component_id=component_id, version=1), renderer.component_descriptor())
            animation = renderer(self.controller, renderer.COMPONENT_DEFAULTS)
            left = animation.generate_frame(.0, 0).pixels.copy()
            right = animation.generate_frame(.2, 1).pixels.copy()
            self.assertEqual(left.shape, (33 * 138, 3)); self.assertEqual(left.dtype, np.uint8)
            self.assertFalse(np.array_equal(left, right))
            fingerprints.add(left.tobytes())
            scene = self._scene(component_id); scene["animation"]["parameters"] = dict(renderer.COMPONENT_DEFAULTS)
            preview = ComposerFinalPreview(catalog, ROOT).render(
                __import__("ipc.scene_contract", fromlist=["normalize_composer_scene"]).normalize_composer_scene({"origin": "composer", "scene": scene}, catalog),
                1.0, datetime.now().astimezone(),
            )
            self.assertEqual(preview.pixels.shape, (33 * 138, 3))
        self.assertEqual(len(fingerprints), len(RENDERERS))

    def test_preset_api_live_recovery_and_invalid_candidate_are_atomic(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        for component_id, count in EXPECTED.items():
            self.assertEqual(len(client.get(f"/api/composer/components/{component_id}/presets").get_json()["presets"]), count)
        scene = interface.composer_presets.apply(self._scene("rain_on_glass"), "showcase")
        preview = client.post("/api/composer/preview", json={"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-09-01T12:00:00+00:00"}})
        self.assertEqual(preview.status_code, 200)
        published = client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "atmosphere", "client_sequence": 1})
        self.assertEqual(published.status_code, 200)
        published_body = published.get_json()
        self.assertEqual(preview.get_json()["basis"]["digest"], published_body["current"]["digest"])
        self.assertEqual(published_body["current"], published_body["desired"])
        self.assertEqual(published_body["desired"], published_body["observed"])
        baseline = client.get("/api/composer/status").get_json()
        invalid = copy.deepcopy(scene); invalid["animation"]["parameters"]["density"] = 2.0
        rejected = client.post("/api/composer/scene", json={"origin": "composer", "scene": invalid, "client_id": "atmosphere", "client_sequence": 2})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(client.get("/api/composer/status").get_json()["current"], baseline["current"])
        remix = copy.deepcopy(scene); remix["animation"]["parameters"]["motion"] = .73
        accepted = client.post("/api/composer/scene", json={"origin": "composer", "scene": remix, "client_id": "atmosphere", "client_sequence": 3})
        self.assertEqual(accepted.status_code, 200)
        recovered = client.get("/api/composer/recovery?client_id=atmosphere").get_json()["recovery"]["scene"]["animation"]["parameters"]
        self.assertEqual(recovered["motion"], .73)
        self.assertEqual(recovered["seed"], scene["animation"]["parameters"]["seed"])
        self.assertEqual(recovered["background"], scene["animation"]["parameters"]["background"])

    def test_browser_number_round_trip_is_canonical_before_preview_and_publication(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        client_id = "browser-atmosphere"
        last_scene = None
        last_basis = None
        for sequence, component_id in enumerate(RENDERERS, start=1):
            preset_id = interface.composer_presets.choices(component_id)[0]["preset_id"]
            scene = interface.composer_presets.apply(self._scene(component_id), preset_id)
            browser_scene = _browser_json_round_trip(scene)
            canonical = interface._composer_canonical({"origin": "composer", "scene": browser_scene})
            self.assertEqual(canonical.canonical_bytes, canonical_json_bytes(canonical.scene))
            preview = client.post("/api/composer/preview", json={
                "origin": "composer", "scene": browser_scene,
                "preview": {"monotonic_elapsed": float(sequence), "wall_time": "2026-09-01T12:00:00+00:00"},
            })
            self.assertEqual(preview.status_code, 200, preview.get_json())
            published = client.post("/api/composer/scene", json={
                "origin": "composer", "scene": browser_scene,
                "client_id": client_id, "client_sequence": sequence,
            })
            self.assertEqual(published.status_code, 200, published.get_json())
            body = published.get_json()
            self.assertEqual(preview.get_json()["basis"], canonical.identity.to_dict())
            self.assertEqual(body["current"], canonical.identity.to_dict())
            self.assertEqual(body["current"], body["desired"])
            self.assertEqual(body["desired"], body["observed"])
            last_scene, last_basis = browser_scene, canonical.identity.to_dict()

        recovery = client.get(f"/api/composer/recovery?client_id={client_id}").get_json()
        self.assertEqual(recovery["recovery"]["basis"], last_basis)
        self.assertEqual(recovery["status"]["current"], last_basis)
        self.assertEqual(recovery["status"]["desired"], last_basis)
        self.assertEqual(recovery["status"]["observed"], last_basis)

        float_scene = copy.deepcopy(last_scene)
        float_scene["animation"]["parameters"]["source_fps"] = 24.0
        integer_scene = _browser_json_round_trip(float_scene)
        self.assertEqual(integer_scene["animation"]["parameters"]["source_fps"], 24)
        self.assertEqual(
            interface._composer_canonical({"origin": "composer", "scene": float_scene}).canonical_bytes,
            interface._composer_canonical({"origin": "composer", "scene": integer_scene}).canonical_bytes,
        )

        baseline = client.get("/api/composer/recovery?client_id=browser-atmosphere").get_json()
        invalid = copy.deepcopy(last_scene)
        invalid["animation"]["parameters"]["source_fps"] = 41
        rejected_preview = client.post("/api/composer/preview", json={
            "origin": "composer", "scene": invalid,
            "preview": {"monotonic_elapsed": 9.0, "wall_time": "2026-09-01T12:00:00+00:00"},
        })
        self.assertEqual(rejected_preview.status_code, 400)
        rejected_publish = client.post("/api/composer/scene", json={
            "origin": "composer", "scene": invalid,
            "client_id": client_id, "client_sequence": 8,
        })
        self.assertEqual(rejected_publish.status_code, 400)
        self.assertEqual(
            client.get("/api/composer/recovery?client_id=browser-atmosphere").get_json(), baseline
        )

    def test_selected_only_controls_keep_hidden_preset_parameters(self) -> None:
        script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        css = (ROOT / "web/static/css/composer_slice.css").read_text(encoding="utf-8")
        self.assertIn("const atmosphereIds", script)
        self.assertIn("atmosphereParameters = (id, existing = {}) => ({...existing", script)
        self.assertIn("Object.values(atmosphereControls).flat().forEach", script)
        self.assertIn("...Object.entries(atmospherePresetTargets)", script)
        self.assertIn(".operation-row[hidden] { display: none !important; }", css)
        self.assertIn("label[hidden] { display: none !important; }", css)


if __name__ == "__main__":
    unittest.main()
