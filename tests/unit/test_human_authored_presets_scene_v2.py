"""Acceptance for human-authored runtime looks promoted into Scene v2."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
IMPORTED = {
    "conway_life": ("chaos", "chaos", "chaos"),
    "cyclic_reef": ("fancy-coral", "fancy_coral", "Fancy Coral"),
    "living_ecosystem": ("neon-microverse", "neon_microverse", "Neon Microverse"),
    "sparkle": ("twilight-sparkle", "twilight_sparkle", "Twilight Sparkle"),
    "tetris": ("avalanche-factory", "avalanche_factory", "Avalanche Factory"),
}
STARTERS = {
    "conway_life": "human_conway_chaos",
    "cyclic_reef": "human_fancy_coral",
    "living_ecosystem": "human_neon_microverse",
    "sparkle": "human_twilight_sparkle",
    "tetris": "human_avalanche_factory",
}
LIBRARY_NAMES = {
    "conway_life": "Chaos",
    "cyclic_reef": "Fancy Coral",
    "living_ecosystem": "Neon Microverse",
    "sparkle": "Twilight Sparkle",
    "tetris": "Avalanche Factory",
}
CONFIG_EDITS = {
    "conway_life": ("generations_per_second", 19.5),
    "cyclic_reef": ("mutation", .003),
    "living_ecosystem": ("migration", .6),
    "sparkle": ("density", .32),
    "tetris": ("fall_rate", 3.3),
}
SCENE_FIELDS = {"brightness", "palette", "plant_aware", "plant_modifiers", "speed"}


class HumanAuthoredPresetSceneV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.interface = AnimationWebInterface(
            _WallChannel(), _PreviewManager(), local_mode=True
        )
        self.client = self.interface.app.test_client()

    @staticmethod
    def _scene(component_id: str) -> dict:
        scene = _current_scene()
        scene["animation"] = {
            "component_id": component_id,
            "version": 1,
            "provider": "python",
            "role": "animation",
            "parameters": {},
        }
        return scene

    def test_promoted_looks_are_local_selectable_and_preview_through_scene_v2(self) -> None:
        for component_id, (preset_id, _legacy_id, name) in IMPORTED.items():
            with self.subTest(component=component_id, preset=preset_id):
                path = (
                    ROOT / "animation" / "plugins" / component_id
                    / "presets" / f"{preset_id}.json"
                )
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(raw["preset_id"], preset_id)
                self.assertEqual(raw["name"], name)

                choices = {
                    choice["preset_id"]: choice
                    for choice in self.interface.composer_presets.choices(component_id)
                }
                self.assertIn(preset_id, choices)
                self.assertFalse(
                    SCENE_FIELDS & set(choices[preset_id]["parameters"])
                )
                scene = self.interface.composer_presets.apply(
                    self._scene(component_id), preset_id
                )
                self.assertEqual(
                    scene["animation"]["parameters"], choices[preset_id]["parameters"]
                )

                frames = []
                for elapsed in (0.0, 0.25, 0.5, 1.0):
                    response = self.client.post("/api/composer/preview", json={
                        "origin": "composer",
                        "scene": scene,
                        "preview": {
                            "monotonic_elapsed": elapsed,
                            "wall_time": "2026-09-01T12:00:00+00:00",
                        },
                    })
                    self.assertEqual(response.status_code, 200, response.get_json())
                    frame = response.get_json()["frame"]
                    self.assertEqual((frame["width"], frame["height"]), (33, 138))
                    frames.append(frame["pixels"])
                self.assertGreater(len({json.dumps(frame) for frame in frames}), 1)

    def test_promoted_looks_start_in_library_and_remain_configurable(self) -> None:
        library = self.client.get("/api/composer/library").get_json()["items"]
        by_id = {item["id"]: item for item in library}
        self.assertTrue(set(STARTERS.values()) <= set(by_id))

        for component_id, (preset_id, _legacy_id, name) in IMPORTED.items():
            with self.subTest(component=component_id, preset=preset_id):
                starter_id = STARTERS[component_id]
                self.assertEqual(by_id[starter_id]["name"], LIBRARY_NAMES[component_id])
                starter = self.client.get(
                    f"/api/composer/starters/{starter_id}"
                ).get_json()["starter"]
                scene = starter["scene"]
                expected = {
                    choice["preset_id"]: choice["parameters"]
                    for choice in self.interface.composer_presets.choices(component_id)
                }[preset_id]
                expected = json.loads(json.dumps(expected))
                self.assertEqual(scene["animation"]["component_id"], component_id)
                self.assertEqual(scene["animation"]["parameters"], expected)

                configured = deepcopy(scene)
                parameter, value = CONFIG_EDITS[component_id]
                configured["animation"]["parameters"][parameter] = value
                preview = self.client.post("/api/composer/preview", json={
                    "origin": "composer", "scene": configured,
                })
                self.assertEqual(preview.status_code, 200, preview.get_json())
                checked = self.client.post("/api/composer/check", json={
                    "origin": "composer", "scene": configured,
                })
                self.assertEqual(checked.status_code, 200, checked.get_json())

    def test_avalanche_factory_controls_preserve_performance_parameters(self) -> None:
        script = (ROOT / "web" / "static" / "js" / "composer_slice.js").read_text(
            encoding="utf-8"
        )
        for control_id in (
            "tetrisPieces", "tetrisFallRate", "tetrisRisk", "tetrisSmoothDrop",
        ):
            self.assertIn(control_id, script)
        self.assertIn("tetrisParameters(next.animation.parameters)", script)
        self.assertIn("smooth_drop_max_pieces: 32", script)
        self.assertIn("high_density_render_fps: 150", script)

    def test_precuration_separator_aliases_remain_on_disk_without_duplicate_cards(self) -> None:
        with TemporaryDirectory() as directory:
            self.interface.animation_presets_dir = Path(directory)
            self.interface._legacy_preset_is_ambiguous = lambda _component_id: False
            self.interface._curated_animation_preset_dir = lambda component_id: (
                ROOT / "animation" / "plugins" / component_id / "presets"
            )
            for component_id, (preset_id, legacy_id, name) in IMPORTED.items():
                legacy_dir = Path(directory) / component_id
                legacy_dir.mkdir(parents=True)
                (legacy_dir / f"{legacy_id}.json").write_text(json.dumps({
                    "version": 2,
                    "preset_id": legacy_id,
                    "name": name,
                    "animation": component_id,
                    "params": {},
                }), encoding="utf-8")
                listed = self.interface._list_animation_presets(component_id)
                listed_ids = [preset["preset_id"] for preset in listed]
                self.assertEqual(listed_ids.count(preset_id), 1)
                if legacy_id != preset_id:
                    self.assertNotIn(legacy_id, listed_ids)
                selected = next(
                    preset for preset in listed if preset["preset_id"] == preset_id
                )
                self.assertEqual(selected["ownership"], "built_in")
                loaded = self.interface._load_animation_preset(
                    component_id, preset_id
                )
                self.assertIsNotNone(loaded)
                self.assertEqual(loaded["version"], 2)
                self.assertEqual(loaded["preset_id"], preset_id)
                self.assertTrue((legacy_dir / f"{legacy_id}.json").is_file())


if __name__ == "__main__":
    unittest.main()
