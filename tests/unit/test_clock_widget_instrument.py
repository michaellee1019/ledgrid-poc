"""Clock Widget catalog and current Composer control contracts."""

from __future__ import annotations

from pathlib import Path
import unittest

from animation.plugins.clock_overlay import ClockOverlayAnimation
from tests.unit.test_composer_runtime_preview import _PreviewManager, _WallChannel, _clock, _scene
from web.app import AnimationWebInterface


class ClockWidgetInstrumentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        self.client = self.interface.app.test_client()

    def test_stable_disk_presets_are_discovered_and_schema_valid(self) -> None:
        response = self.client.get("/api/composer/components/clock_overlay/presets")
        self.assertEqual(response.status_code, 200)
        presets = response.get_json()["presets"]
        self.assertEqual(
            [preset["preset_id"] for preset in presets],
            ["local-12-hour", "precision-seconds", "remote-team-plus-six"],
        )
        for preset in presets:
            self.assertEqual(
                set(preset["parameters"]),
                {"format_24h", "show_seconds", "clock_offset_minutes"},
            )
            self.assertEqual(
                ClockOverlayAnimation._normalized_parameters(preset["parameters"]),
                preset["parameters"],
            )

    def test_invalid_clock_values_reject_without_replacing_the_live_scene(self) -> None:
        scene = _scene(widgets=[_clock("clock", [255, 224, 128], led=-8)])
        published = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": scene, "client_id": "clock", "client_sequence": 1,
        })
        self.assertEqual(published.status_code, 200)
        before = published.get_json()["current"]
        invalid = _scene(widgets=[_clock("clock", [255, 224, 128], led=-8)])
        invalid["widgets"][0]["component"]["parameters"]["clock_offset_minutes"] = 841
        rejected = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": invalid, "client_id": "clock", "client_sequence": 2,
        })
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.client.get("/api/composer/status").get_json()["current"], before)

    def test_widget_controls_use_actual_inputs_and_preserve_widget_identity(self) -> None:
        html = Path("web/templates/composer.html").read_text(encoding="utf-8")
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        for token in ('id="clockPresetCards"', 'id="clockFormat"', 'id="clockSeconds"', 'id="clockTimeOffset"'):
            self.assertIn(token, html)
        self.assertIn("const clockParameters", script)
        self.assertIn("clock.component.parameters = clockParameters(clock.component.parameters)", script)
        self.assertIn("['#clockFormat','#clockSeconds','#clockTimeOffset'].forEach((selector) => $(selector).addEventListener('input', edit));", script)
        self.assertIn("if (clock) clock.component.parameters = preset.parameters", script)
        self.assertIn("id: 'composer-clock'", script)
        self.assertIn("clock.placement.strip_translation ?? 0", script)


if __name__ == "__main__":
    unittest.main()
