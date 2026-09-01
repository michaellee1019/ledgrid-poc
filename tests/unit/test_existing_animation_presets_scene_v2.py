"""Focused Scene v2 proof for the checked existing-animation preset packet."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from animation.plugins.firefly_synchrony import FireflySynchronyAnimation
from animation.plugins.tetris import TetrisAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_IDS = {
    "aurora_curtains": ["night", "quiet", "showcase", "solar-morning"],
    "conway_life": ["arcade-afterlife", "aurora-garden", "bioluminescent-tide", "classic-green", "deep-space-acorn", "earth-cities", "gosper-foundry", "ice-crystal", "maximum-chaos", "neon-glider-storm", "oscillator-orchard", "pulsar-observatory", "r-pentomino-laboratory", "solar-embers", "synthwave-sunset"],
    "tetris": ["avalanche-factory", "classic-quartet", "cooperative-swarm", "impossible-shift", "solo-zen"],
    "firefly_synchrony": ["lantern-meadow", "night", "quiet", "showcase"],
}
NORMALIZERS = {
    "aurora_curtains": AuroraCurtainsAnimation._normalized_parameters,
    "conway_life": ConwayLifeAnimation._normalized_parameters,
    "tetris": TetrisAnimation._normalized_parameters,
    "firefly_synchrony": FireflySynchronyAnimation._normalized_parameters,
}
DEFAULTS = {
    "aurora_curtains": AuroraCurtainsAnimation.DEFAULTS,
    "conway_life": ConwayLifeAnimation.DEFAULTS,
    "tetris": TetrisAnimation.DEFAULTS,
    "firefly_synchrony": FireflySynchronyAnimation.DEFAULTS,
}


class ExistingAnimationPresetSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        self.client = self.interface.app.test_client()

    @staticmethod
    def _scene(component_id: str) -> dict:
        scene = _current_scene()
        scene["animation"] = {
            "component_id": component_id, "version": 1, "provider": "python",
            "role": "animation", "parameters": {},
        }
        return scene

    def test_exactly_the_twenty_eight_checked_component_cards_are_local(self) -> None:
        self.assertEqual(sum(len(ids) for ids in EXPECTED_IDS.values()), 28)
        for component_id, expected_ids in EXPECTED_IDS.items():
            directory = ROOT / "animation" / "plugins" / component_id / "presets"
            paths = sorted(directory.glob("*.json"))
            self.assertEqual([path.stem for path in paths], expected_ids)
            self.assertNotIn("default", expected_ids)
            for path in paths:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(raw["animation"], component_id)
                self.assertEqual(raw["preset_id"], path.stem)
                self.assertSetEqual(set(NORMALIZERS[component_id](raw["params"])), set(DEFAULTS[component_id]))
                self.assertFalse({"brightness", "speed", "plant_aware", "plant_modifiers", "palette", "geometry", "calibration"} & set(raw["params"]))

    def test_catalog_applies_only_local_parameters_and_every_choice_previews(self) -> None:
        for component_id, expected_ids in EXPECTED_IDS.items():
            choices = self.interface.composer_presets.choices(component_id)
            self.assertEqual([choice["preset_id"] for choice in choices], expected_ids)
            self.assertEqual(self.client.get(f"/api/composer/components/{component_id}/presets").status_code, 200)
            for choice in choices:
                scene = self._scene(component_id)
                selected = self.interface.composer_presets.apply(scene, choice["preset_id"])
                self.assertEqual(selected["background"], scene["background"])
                self.assertEqual(selected["widgets"], scene["widgets"])
                self.assertEqual(selected["plants"], scene["plants"])
                self.assertEqual(selected["look"], scene["look"])
                self.assertEqual(selected["animation"]["parameters"], choice["parameters"])
                preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": selected, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-09-01T12:00:00+00:00"}})
                self.assertEqual(preview.status_code, 200, choice["preset_id"])
                self.assertEqual(preview.get_json()["frame"]["width"], 33)

    def test_representative_preset_remixes_keep_hidden_local_parameters_in_preview_and_live(self) -> None:
        selected_ids = {"aurora_curtains": "showcase", "conway_life": "deep-space-acorn", "tetris": "cooperative-swarm", "firefly_synchrony": "lantern-meadow"}
        edits = {"aurora_curtains": ("glow_intensity", 0.33), "conway_life": ("generations_per_second", 4.5), "tetris": ("fall_rate", 1.75), "firefly_synchrony": ("synchrony", 0.77)}
        for sequence, (component_id, preset_id) in enumerate(selected_ids.items(), start=1):
            selected = self.interface.composer_presets.apply(self._scene(component_id), preset_id)
            remix = copy.deepcopy(selected)
            key, value = edits[component_id]
            remix["animation"]["parameters"][key] = value
            preview = self.client.post("/api/composer/preview", json={"origin": "composer", "scene": remix, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-09-01T12:00:00+00:00"}}).get_json()
            live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": remix, "client_id": f"existing-{component_id}", "client_sequence": sequence}).get_json()
            recovered = self.client.get(f"/api/composer/recovery?client_id=existing-{component_id}").get_json()["recovery"]["scene"]
            self.assertEqual(preview["basis"]["digest"], live["current"]["digest"])
            self.assertEqual(recovered["animation"]["parameters"][key], value)
            for parameter, expected in selected["animation"]["parameters"].items():
                if parameter != key:
                    self.assertEqual(recovered["animation"]["parameters"][parameter], json.loads(json.dumps(expected)))

    def test_three_existing_component_normalizers_reject_invalid_live_and_preview_before_mutation(self) -> None:
        checks = {
            "aurora_curtains": ("showcase", "curtain_density"),
            "conway_life": ("deep-space-acorn", "generations_per_second"),
            "tetris": ("cooperative-swarm", "fall_rate"),
        }
        for sequence, (component_id, (preset_id, parameter)) in enumerate(checks.items(), start=1):
            scene = self.interface.composer_presets.apply(self._scene(component_id), preset_id)
            preview_request = {"origin": "composer", "scene": scene, "preview": {"monotonic_elapsed": 2.0, "wall_time": "2026-09-01T12:00:00+00:00"}}
            preview = self.client.post("/api/composer/preview", json=preview_request)
            self.assertEqual(preview.status_code, 200)
            self.assertEqual(self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": f"{component_id}-validator", "client_sequence": sequence}).status_code, 200)
            before_status = self.client.get("/api/composer/status").get_json()
            before_commands = copy.deepcopy(self.interface.composer_control.commands)
            invalid = copy.deepcopy(scene); invalid["animation"]["parameters"][parameter] = 99
            rejected_live = self.client.post("/api/composer/scene", json={"origin": "composer", "scene": invalid, "client_id": f"{component_id}-validator", "client_sequence": sequence + 10})
            rejected_preview = self.client.post("/api/composer/preview", json={**preview_request, "scene": invalid})
            self.assertEqual(rejected_live.status_code, 400)
            self.assertIn(parameter, rejected_live.get_json()["error"])
            self.assertEqual(rejected_preview.status_code, 400)
            self.assertIn(parameter, rejected_preview.get_json()["error"])
            after_status = self.client.get("/api/composer/status").get_json()
            for identity in ("current", "desired", "observed"):
                self.assertEqual(after_status[identity], before_status[identity])
            self.assertEqual(self.interface.composer_control.commands, before_commands)
            recovered_preview = self.client.post("/api/composer/preview", json=preview_request).get_json()
            self.assertEqual(recovered_preview["basis"], preview.get_json()["basis"])
            self.assertEqual(recovered_preview["frame"]["pixels"], preview.get_json()["frame"]["pixels"])

    def test_desktop_cards_use_component_catalogs_and_preserve_firefly_seed_and_coupling(self) -> None:
        script = (ROOT / "web" / "static" / "js" / "composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("loadExistingComponentPresets", script)
        for component_id in EXPECTED_IDS:
            self.assertIn(f"'{component_id}'", script)
        self.assertIn("fireflyParameters = (existing = {}) => ({...existing", script)
        self.assertIn("fireflyParameters(next.animation.parameters)", script)
        self.assertIn("syncComponentPresetUI", script)
        self.assertIn("target.hidden = !visible; target.inert = !visible", script)
        self.assertIn("aria-pressed", script)
        self.assertIn("Custom remix", script)
        for component_id in ("aurora_curtains", "firefly_synchrony", "tetris"):
            self.assertIn(component_id, script)
        self.assertNotIn("components/aurora_curtains/looks", script)


if __name__ == "__main__":
    unittest.main()
