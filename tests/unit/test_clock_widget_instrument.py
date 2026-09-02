"""Clock Widget catalog and current Composer control contracts."""

from __future__ import annotations

from pathlib import Path
import unittest

from animation.plugins.clock_overlay import ClockOverlayAnimation
from tests.unit.test_composer_runtime_preview import _PreviewManager, _WallChannel, _clock, _request, _scene
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
        for token in ('id="clockPresetCards"', 'id="clockFormat"', 'id="clockSeconds"', 'id="clockTimeOffset"', 'id="clockAcross"', 'id="clockOffset"', 'id="clockPlacementStatus"'):
            self.assertIn(token, html)
        self.assertIn("const clockParameters", script)
        self.assertIn("clock.component.parameters = clockParameters(clock.component.parameters)", script)
        self.assertIn("['#clockFormat','#clockSeconds','#clockTimeOffset'].forEach((selector) => $(selector).addEventListener('input', edit));", script)
        self.assertIn("['#clockAcross','#clockOffset'].forEach((selector) => $(selector).addEventListener('input', edit));", script)
        self.assertIn("strip_translation: Math.trunc(number('#clockAcross'))", script)
        self.assertIn("led_translation: Math.trunc(number('#clockOffset'))", script)
        self.assertIn("if (clock) clock.component.parameters = preset.parameters", script)
        self.assertIn("id: 'composer-clock'", script)
        self.assertIn("placementWarning(body.widget_placements || {})", script)
        self.assertIn("Advisory only—the position is still live.", script)
        self.assertIn("if (node && node.textContent !== text) node.textContent = text", script)
        self.assertIn("<label>Clock across", html)
        self.assertIn("<label>Clock down", html)

    def test_both_clock_position_axes_publish_and_survive_recovery(self) -> None:
        scene = _scene(widgets=[_clock("clock", [255, 224, 128], led=37)])
        scene["widgets"][0]["component"]["parameters"] = {
            "format_24h": True, "show_seconds": False, "clock_offset_minutes": 90,
        }
        scene["widgets"][0]["placement"]["strip_translation"] = 9
        published = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": scene, "client_id": "clock-position",
            "client_sequence": 1,
        })
        self.assertEqual(published.status_code, 200, published.get_json())
        recovery = self.client.get("/api/composer/recovery?client_id=clock-position")
        self.assertEqual(recovery.status_code, 200, recovery.get_json())
        recovered_clock = recovery.get_json()["recovery"]["scene"]["widgets"][0]
        self.assertEqual(recovered_clock["id"], "clock")
        self.assertEqual(recovered_clock["placement"], {
            "mode": "manual", "strip_translation": 9, "led_translation": 37,
        })
        self.assertEqual(recovered_clock["component"], scene["widgets"][0]["component"])

    def test_preview_reports_a_clear_manual_clock_move_without_a_warning(self) -> None:
        automatic = _scene(widgets=[_clock("clock", [255, 224, 128], led=0)])
        automatic["widgets"][0]["placement"] = {"mode": "auto"}
        auto_preview = self.client.post("/api/composer/preview", json=_request(automatic))
        self.assertEqual(auto_preview.status_code, 200, auto_preview.get_json())
        resolved = auto_preview.get_json()["widget_placements"]["clock"]
        self.assertFalse(resolved["used_fallback"], resolved)
        self.assertEqual(resolved["overlap_pixels"], 0)

        moved = _scene(widgets=[_clock("clock", [255, 224, 128], led=resolved["led_translation"])])
        moved["widgets"][0]["placement"]["strip_translation"] = resolved["strip_translation"]
        manual_preview = self.client.post("/api/composer/preview", json=_request(moved))
        self.assertEqual(manual_preview.status_code, 200, manual_preview.get_json())
        placement = manual_preview.get_json()["widget_placements"]["clock"]
        self.assertEqual(placement["overlap_pixels"], 0)
        self.assertIsNone(placement["warning"])


if __name__ == "__main__":
    unittest.main()
