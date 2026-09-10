"""Scene v2 and Composer routing proof for Pinball's primary gesture."""

from __future__ import annotations

import unittest
from pathlib import Path

from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[4]


class PinballComposerInteractionTests(unittest.TestCase):
    def test_composer_canvas_routes_pinball_as_the_existing_primary_event(self) -> None:
        script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        start = script.index("const primaryInstruments = Object.freeze")
        end = script.index("function renderLibrary()", start)
        interaction = script[start:end]
        self.assertIn("pinball:", interaction)
        self.assertIn("kind: 'primary'", interaction)
        self.assertIn("fetch('/api/interaction'", interaction)

    def test_primary_route_accepts_pinball_without_changing_scene_identity(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        client = interface.app.test_client()
        scene = _current_scene()
        scene["animation"] = {
            "component_id": "pinball", "version": 1, "provider": "python",
            "role": "animation", "parameters": {},
        }
        publication = client.post("/api/composer/scene", json={
            "origin": "composer", "scene": scene, "client_id": "pinball",
            "client_sequence": 1,
        }).get_json()
        self.assertEqual(publication["state"], "live")
        basis = publication["current"]

        accepted = client.post("/api/interaction", json={
            "kind": "primary", "x": 8.0, "y": 90.0, "strength": 1.0,
        })
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.get_json()["accepted"])
        self.assertEqual(accepted.get_json()["component_id"], "pinball")
        self.assertEqual(accepted.get_json()["basis"]["digest"], basis["digest"])

        rejected = client.post("/api/interaction", json={
            "kind": "secondary", "x": 8.0, "y": 90.0, "strength": 1.0,
        })
        self.assertEqual(rejected.status_code, 400)
        current = interface.composer_live.snapshot(include_current_scene=True)["current"]
        self.assertEqual(current["digest"], basis["digest"])


if __name__ == "__main__":
    unittest.main()
