"""Focused Scene v2 contract proof for the Flame Burst and Fluid Tank pair."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.flame_burst import FlameBurstAnimation
from animation.plugins.fluid_tank import FluidTankAnimation
from animation.plugins.lava_lamp import LavaLampAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.local_control import LocalControlChannel
from web.composer_component_presets import ComponentPresetCatalog
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog
from ipc.scene_contract import normalize_composer_scene

ROOT = Path(__file__).resolve().parents[2]


class FlameFluidSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_exact_authored_preset_families_have_no_default(self) -> None:
        expected = {
            "flame_burst": ["afterburner", "campfire-bloom", "off-center-comet", "rapid-ignition", "solar-pulse"],
            "fluid_tank": ["bubble-column", "caustic-laboratory", "flash-flood", "quiet-aquarium", "she_cute", "storm-tank"],
        }
        classes = {"flame_burst": FlameBurstAnimation, "fluid_tank": FluidTankAnimation}
        for component_id, identifiers in expected.items():
            paths = sorted((ROOT / "animation" / "plugins" / component_id / "presets").glob("*.json"))
            self.assertEqual([path.stem for path in paths], identifiers)
            self.assertNotIn("default", identifiers)
            for path in paths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertSetEqual(set(classes[component_id]._normalized_parameters(payload["params"])), set(classes[component_id].DEFAULTS))

    def test_catalog_final_preview_and_rejected_candidate_are_atomic(self) -> None:
        catalog = current_component_catalog()
        for renderer in (FlameBurstAnimation, FluidTankAnimation):
            self.assertIs(catalog.require(provider="python", component_id=renderer.COMPONENT_ID, version=1), renderer.component_descriptor())
            scene = _current_scene(); scene["animation"] = {"component_id": renderer.COMPONENT_ID, "version": 1, "provider": "python", "role": "animation", "parameters": dict(renderer.DEFAULTS)}
            canonical = normalize_composer_scene({"origin": "composer", "scene": scene}, catalog)
            preview = ComposerFinalPreview(catalog, ROOT).render(canonical, 1.0, __import__("datetime").datetime.now().astimezone())
            self.assertEqual(preview.pixels.shape, (33 * 138, 3))
            rejected = copy.deepcopy(scene); rejected["animation"]["parameters"]["brightness"] = .5
            with self.assertRaisesRegex(ValueError, "non-local"):
                renderer._normalized_parameters(rejected["animation"]["parameters"])
            self.assertEqual(canonical.scene["animation"]["parameters"], dict(renderer.DEFAULTS))

    def test_instruments_are_deterministic_distinct_and_bound_manual_events(self) -> None:
        for renderer, modified in ((FlameBurstAnimation, {"ignition_cadence": 3.5, "flare_size": .82}), (FluidTankAnimation, {"flow_rate": 1.7, "surface_energy": .94})):
            left, right = renderer(self.controller, {"seed": 77}), renderer(self.controller, {"seed": 77})
            frames = []
            for index in range(90):
                a, b = left.generate_frame(index / 20, index), right.generate_frame(index / 20, index)
                np.testing.assert_array_equal(a.pixels, b.pixels); frames.append(a.pixels.copy())
            self.assertEqual(frames[-1].shape, (33 * 138, 3)); self.assertLessEqual(left.cadence_snapshot()["simulation_hz"], 30.0)
            self.assertTrue(left.handle_interaction("primary", 8., 60., 1.)); self.assertFalse(left.handle_interaction("secondary", 8., 60., 1.)); self.assertFalse(left.handle_interaction("primary", 33., 60., 1.))
            varied = renderer(self.controller, {"seed": 77, **modified})
            varied.generate_frame(0., 0)
            changed = varied.generate_frame(4.5, 91).pixels
            self.assertFalse(np.array_equal(frames[-1], changed))

    def test_local_remix_keeps_hidden_seed_and_selected_only_contract(self) -> None:
        catalog = ComponentPresetCatalog(ROOT, {"flame_burst": FlameBurstAnimation._normalized_parameters, "fluid_tank": FluidTankAnimation._normalized_parameters})
        scene = _current_scene(); scene["animation"] = {"component_id": "flame_burst", "version": 1, "provider": "python", "role": "animation", "parameters": {**FlameBurstAnimation.DEFAULTS, "seed": 999}}
        selected = catalog.apply(scene, "solar-pulse")
        remixed = {**selected["animation"]["parameters"], "flicker": .8}
        self.assertIn("seed", remixed); self.assertEqual(remixed["seed"], selected["animation"]["parameters"]["seed"])
        script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        for token in ("flameParameters = (existing = {}) => ({...existing", "fluidParameters = (existing = {}) => ({...existing", "flame_burst: ['#flameCadence'", "fluid_tank: ['#fluidFlow'", "setComponentRegion(document.getElementById(targetId), id === componentId)"):
            self.assertIn(token, script)

    def test_preset_api_and_live_state_keep_one_valid_basis_after_rejection(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        scene = _current_scene(); scene["animation"] = {"component_id": "fluid_tank", "version": 1, "provider": "python", "role": "animation", "parameters": dict(FluidTankAnimation.DEFAULTS)}
        self.assertEqual(len(client.get("/api/composer/components/flame_burst/presets").get_json()["presets"]), 5)
        self.assertEqual(len(client.get("/api/composer/components/fluid_tank/presets").get_json()["presets"]), 6)
        accepted = client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "flow", "client_sequence": 1}).get_json()
        self.assertEqual(accepted["current"], accepted["desired"]); self.assertEqual(accepted["desired"], accepted["observed"])
        invalid = copy.deepcopy(scene); invalid["animation"]["parameters"]["speed"] = 1
        rejected = client.post("/api/composer/scene", json={"origin": "composer", "scene": invalid, "client_id": "flow", "client_sequence": 2})
        self.assertEqual(rejected.status_code, 400)
        recovery = client.get("/api/composer/recovery?client_id=flow").get_json()["recovery"]["scene"]
        self.assertEqual(recovery["animation"]["parameters"], dict(FluidTankAnimation.DEFAULTS))

    def test_installed_final_pointer_routes_only_primary_instruments(self) -> None:
        script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        start = script.index("const primaryInstruments = Object.freeze")
        end = script.index("function renderLibrary()", start)
        gesture = script[start:end]
        for component_id, message in (("lava_lamp", "Lava stirred on the installed final scene."), ("flame_burst", "Flame Burst ignited on the installed final scene."), ("fluid_tank", "Fluid Tank pulsed on the installed final scene.")):
            self.assertIn(f"{component_id}:", gesture)
            self.assertIn(message, gesture)
        self.assertIn("const trigger = primaryInstruments[componentId];", gesture)
        self.assertLess(gesture.index("!trigger) return"), gesture.index("fetch('/api/interaction'"))
        self.assertIn("body: JSON.stringify({kind: 'primary', x, y, strength: 1})", gesture)
        self.assertIn("body.accepted !== true", gesture)

    def test_live_primary_trigger_api_accepts_only_supported_instrument_input(self) -> None:
        for renderer in (FlameBurstAnimation, FluidTankAnimation):
            manager = AnimationManager(self.controller, auto_start=False)
            manager.current_animation = renderer(self.controller)
            manager.current_animation_name = renderer.COMPONENT_ID
            client = AnimationWebInterface(LocalControlChannel(manager), manager, local_mode=True).app.test_client()
            accepted = client.post("/api/interaction", json={"kind": "primary", "x": 8., "y": 60., "strength": 1.})
            self.assertEqual(accepted.status_code, 200); self.assertTrue(accepted.get_json()["accepted"])
            self.assertEqual(client.post("/api/interaction", json={"kind": "secondary", "x": 8., "y": 60., "strength": 1.}).status_code, 400)
            self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 33., "y": 60., "strength": 1.}).status_code, 400)

    def test_published_composer_basis_routes_primary_without_legacy_manager_seed(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        for sequence, renderer in enumerate((LavaLampAnimation, FlameBurstAnimation, FluidTankAnimation), start=1):
            scene = _current_scene(); scene["animation"] = {"component_id": renderer.COMPONENT_ID, "version": 1, "provider": "python", "role": "animation", "parameters": dict(renderer.DEFAULTS)}
            client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "published", "client_sequence": sequence})
            self.assertEqual(client.post("/api/composer/stop", json={"client_id": "published"}).status_code, 200)
            self.assertEqual(client.post("/api/composer/go-live", json={"client_id": "published"}).status_code, 200)
            accepted = client.post("/api/interaction", json={"kind": "primary", "x": 8., "y": 60., "strength": 1.})
            self.assertEqual(accepted.status_code, 200); self.assertEqual(accepted.get_json()["component_id"], renderer.COMPONENT_ID)
            self.assertEqual(client.post("/api/interaction", json={"kind": "secondary", "x": 8., "y": 60., "strength": 1.}).status_code, 400)
        unrelated = _current_scene()
        client.post("/api/composer/scene", json={"origin": "composer", "scene": unrelated, "client_id": "published", "client_sequence": 4})
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 8., "y": 60., "strength": 1.}).status_code, 400)
        client.post("/api/composer/stop", json={"client_id": "published"})
        self.assertEqual(client.post("/api/interaction", json={"kind": "primary", "x": 8., "y": 60., "strength": 1.}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
