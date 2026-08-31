"""Focused product-surface checks for the local Composer overlay chooser."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tests.unit.test_composer_slice import _conway, _overlay, _scene
from web.composer_component_editor import ComponentEditorError, editor_catalog, validate_editor_scene
from web.app import AnimationWebInterface
from web.scene_look_store import SceneLookStore


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _PreviewManager:
    controller = _Controller()
    plugin_loader = None


class _Wall:
    def __init__(self): self.commands = []
    def send_command(self, action, **data): self.commands.append({"action": action, **data})


class ComposerComponentEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wall = _Wall()
        self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        self.interface.composer_looks = SceneLookStore(Path(self.tmp.name) / "looks.json")
        self.client = self.interface.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def test_catalog_is_qualified_and_only_exposes_meaningful_controls(self):
        catalog = editor_catalog(self.interface.composer_catalog)
        self.assertEqual([choice["slot_id"] for choice in catalog["choices"]], ["conway_lower", "clock_upper"])
        self.assertEqual([control["id"] for control in catalog["choices"][0]["controls"]], ["seed", "rule", "initial_density", "generations_per_second"])
        self.assertEqual([control["id"] for control in catalog["choices"][1]["controls"]], ["show_seconds", "color"])
        response = self.client.get("/api/composer/components")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), catalog)

    def test_clock_first_and_conway_only_round_trip_with_stable_slots(self):
        clock = _overlay("clock_upper", {"show_seconds": False, "color": [10, 20, 30]})
        conway = _conway("conway_lower", {"seed": 42, "rule": "B36/S23", "initial_density": 0.2, "generations_per_second": 7.5})
        response = self.client.post("/api/composer/preview", json=_scene([clock, conway]))
        self.assertEqual(response.status_code, 200)
        only_conway = self.client.post("/api/composer/looks", json={"name": "Life", "draft": _scene([conway])})
        self.assertEqual(only_conway.status_code, 200)
        self.assertEqual(only_conway.get_json()["look"]["scene"]["overlays"][0]["slot_id"], "conway_lower")
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(self.interface.composer_control.commands, [])

    def test_duplicate_or_unsupported_overlay_rejects_before_preview_or_activation(self):
        duplicate = _scene([_overlay("clock_upper"), _overlay("clock_upper")])
        unsupported = _scene([{
            **_overlay("alert_upper"),
            "component": {**_overlay("alert_upper")["component"], "component_id": "alert"},
        }])
        for draft in (duplicate, unsupported):
            response = self.client.post("/api/composer/preview", json=draft)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(self.client.post("/api/composer/check", json=draft).status_code, 400)
        with self.assertRaises(ComponentEditorError):
            validate_editor_scene(duplicate, self.interface.composer_catalog)
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(self.interface.composer_control.commands, [])

    def test_wrong_stable_slot_rejects_before_preview_check_or_persistence(self):
        wrong_slot = _scene([_overlay("conway_lower")])
        for endpoint in ("/api/composer/preview", "/api/composer/check"):
            response = self.client.post(endpoint, json=wrong_slot)
            self.assertEqual(response.status_code, 400)
            self.assertIn("stable component slot", response.get_json()["error"])

        saved = self.client.post("/api/composer/looks", json={"name": "Basis", "draft": _scene()}).get_json()["look"]
        rejected_look = self.client.post("/api/composer/looks", json={"name": "Wrong slot", "draft": wrong_slot})
        self.assertEqual(rejected_look.status_code, 400)
        self.assertEqual([look["id"] for look in self.client.get("/api/composer/looks").get_json()["looks"]], [saved["id"]])

        reference = {"kind": "look", "id": saved["id"], "basis": saved["basis"]}
        rejected_draft = self.client.post("/api/composer/draft", json={"draft": wrong_slot, "reference": reference})
        self.assertEqual(rejected_draft.status_code, 400)
        self.assertIsNone(self.client.get("/api/composer/draft").get_json()["draft"])
        with self.assertRaises(ComponentEditorError):
            validate_editor_scene(wrong_slot, self.interface.composer_catalog)
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(self.interface.composer_control.commands, [])
