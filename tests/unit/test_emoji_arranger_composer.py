"""Composer publication coverage for the Emoji Message Widget."""

from __future__ import annotations

import unittest

from tests.unit.test_composer_runtime_preview import (
    _PreviewManager, _WallChannel, _clock, _component, _request, _scene,
)
from web.app import AnimationWebInterface


def _emoji(text: str = "HI🔥") -> dict:
    return {
        "id": "composer-emoji-message",
        "component": _component("emoji_arranger", "widget", {
            "text": text, "x_offset": 8, "y_offset": 3,
            "char_spacing": 1, "line_spacing": 1,
            "scroll_speed": 2.0, "pulse_speed": .5,
        }),
        "visible": True,
        "placement": {"mode": "manual", "strip_translation": 0, "led_translation": 0},
    }


class EmojiMessageComposerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        self.client = self.interface.app.test_client()

    def test_add_edit_preview_and_live_acknowledgement_preserve_last_valid_scene(self) -> None:
        scene = _scene(widgets=[_clock("clock", [255, 224, 128], led=-8), _emoji()])
        published = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": scene, "client_id": "emoji-desktop", "client_sequence": 1,
        })
        self.assertEqual(published.status_code, 200, published.get_json())
        current = published.get_json()["current"]
        recovered = self.client.get("/api/composer/recovery?client_id=emoji-desktop").get_json()["recovery"]["scene"]
        self.assertEqual([item["id"] for item in recovered["widgets"]],
                         ["clock", "composer-emoji-message"])
        self.assertTrue(recovered["widgets"][1]["visible"])
        preview = self.client.post("/api/composer/preview", json=_request(scene, elapsed=1.25))
        self.assertEqual(preview.status_code, 200, preview.get_json())
        self.assertIn("composer-emoji-message", preview.get_json()["widget_placements"])
        self.assertEqual(self.wall.commands, [])

        invalid = _scene(widgets=[_clock("clock", [255, 224, 128], led=-8), _emoji("?")])
        rejected = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": invalid, "client_id": "emoji-desktop", "client_sequence": 2,
        })
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.client.get("/api/composer/status").get_json()["current"], current)


if __name__ == "__main__":
    unittest.main()
