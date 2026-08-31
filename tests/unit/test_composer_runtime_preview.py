"""Focused proof that Composer preview consumes the canonical local runtime."""

from __future__ import annotations

import base64
import unittest
from pathlib import Path

from web.app import AnimationWebInterface


class _Controller:
    strip_count = 33
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip


class _PreviewManager:
    controller = _Controller()
    plugin_loader = None


class _WallChannel:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    def send_command(self, action: str, **data: object) -> None:
        self.commands.append({"action": action, **data})


def _overlay(slot_id: str, color: list[int]) -> dict:
    return {
        "slot_id": slot_id,
        "component": {
            "component_id": "clock_overlay", "version": 1,
            "provider": "python", "role": "overlay",
            "parameters": {"color": color, "show_seconds": True},
        },
        "enabled": True,
        "opacity": 255,
        "placement": {
            "strip_translation": 0, "led_translation": 0,
            "clip_policy": "clip_to_wall",
        },
        "stale_policy": {"policy": "hold"},
    }


def _preview(overlays: list[dict] | None = None) -> dict:
    return {
        "origin": "composer",
        "scene": {
            "schema": "ledgrid.scene.v1",
            "background": {
                "slot_id": "background", "component_id": "aurora_curtains",
                "version": 1, "provider": "python", "role": "background",
                "parameters": {"seed": 812, "source_fps": 20.0},
            },
            "overlays": overlays or [],
            "vibe": "quiet",
            "master_brightness": 1.0,
        },
        "preview": {
            "monotonic_elapsed": 1.25,
            "wall_time": "2026-08-31T13:47:10+00:00",
        },
    }


class ComposerRuntimePreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        self.client = self.interface.app.test_client()

    def test_preview_is_deterministic_canonical_33_by_138_and_has_no_control_side_effect(self) -> None:
        request = _preview([_overlay("clock_primary", [255, 224, 128])])
        first = self.client.post("/api/composer/preview", json=request)
        second = self.client.post("/api/composer/preview", json=request)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        first_body, second_body = first.get_json(), second.get_json()
        self.assertEqual(first_body["basis"], second_body["basis"])
        self.assertEqual(first_body["frame"]["pixels"], second_body["frame"]["pixels"])
        self.assertEqual({
            key: first_body["frame"][key]
            for key in ("width", "height", "encoding", "orientation")
        }, {
            "width": 33,
            "height": 138,
            "encoding": "rgb_u8_base64",
            "orientation": "strip_major_led_zero_bottom",
        })
        self.assertEqual(len(base64.b64decode(first_body["frame"]["pixels"])), 33 * 138 * 3)
        self.assertEqual(first_body["wall_mutations"], 0)
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(self.interface.composer_control.commands, [])

    def test_reordered_overlapping_clocks_change_canonical_identity_and_pixels(self) -> None:
        red = _overlay("clock_primary", [255, 0, 0])
        blue = _overlay("clock_secondary", [0, 0, 255])
        first = self.client.post("/api/composer/preview", json=_preview([red, blue])).get_json()
        second = self.client.post("/api/composer/preview", json=_preview([blue, red])).get_json()
        self.assertNotEqual(first["basis"]["digest"], second["basis"]["digest"])
        self.assertNotEqual(first["frame"]["pixels"], second["frame"]["pixels"])

    def test_preview_failure_leaves_composer_reconciliation_and_wall_channels_unchanged(self) -> None:
        bad = _preview([_overlay("clock_primary", [255, 0, 0])])
        bad["preview"] = {"wall_time": "not-a-time"}
        response = self.client.post("/api/composer/preview", json=bad)
        self.assertEqual(response.status_code, 400)
        self.assertIn("wall_time", response.get_json()["error"])
        status = self.client.get("/api/composer/status").get_json()
        self.assertEqual(status["state"], "pending")
        self.assertIsNone(status["desired"])
        self.assertIsNone(status["observed"])
        self.assertEqual(self.wall.commands, [])
        self.assertEqual(self.interface.composer_control.commands, [])

    def test_root_exposes_one_local_preview_canvas_and_script_maps_bottom_origin(self) -> None:
        html = self.client.get("/").get_data(as_text=True)
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn('id="scenePreview"', html)
        self.assertIn("`${api}/preview`", script)
        self.assertIn("frame.height - 1 - led", script)
        self.assertIn("Preview unavailable", script)


if __name__ == "__main__":
    unittest.main()
