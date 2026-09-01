"""One local, current-contract proof of the first live-first Scene v2 look."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from tests.unit.test_composer_runtime_preview import _PreviewManager, _WallChannel
from web.app import AnimationWebInterface
from web.scene_look_store import SceneLookStore
from web.starter_looks import get_starter
from web.working_draft_store import WorkingDraftStore


class SceneV2EndToEndDemoTests(unittest.TestCase):
    """Exercise the smallest operator journey without a wall or deployment."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        state_dir = Path(self.tmp.name)
        self.interface.composer_looks = SceneLookStore(state_dir / "looks.json")
        self.interface.working_draft = WorkingDraftStore(state_dir / "recovery.json")
        self.client = self.interface.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_builtin_selection_initializes_controls_before_the_preview_is_authored(self) -> None:
        """A Conway selection must not be reconstructed as the prior Aurora UI."""
        script = (Path(__file__).parents[2] / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn(
            "applyScene(starter.scene); await submit(starter.scene, {builtin: true});",
            script,
        )

    @staticmethod
    def _request(scene: dict, *, sequence: int) -> dict:
        return {
            "origin": "composer",
            "scene": scene,
            "client_id": "desktop-firefox",
            "mutation_id": f"operator-edit-{sequence}",
            "client_sequence": sequence,
        }

    def test_named_look_survives_the_live_first_operator_journey(self) -> None:
        # This built-in is deliberately a single complete Scene v2 look: a
        # receiver-native Background, transparent Conway animation, independently
        # timed Clock Widget, plants intent, and the one final Look/output path.
        named_scene = get_starter("aurora_conway_clock")["scene"]
        named_scene["plants"] = {
            "effects": {
                "version": 1,
                "active": ["illuminate"],
                "strengths": {"illuminate": 0.35},
            },
        }
        saved = self.client.post(
            "/api/composer/looks", json={"name": "Canopy Conway Clock", "scene": named_scene},
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        look = saved.get_json()["look"]
        self.assertEqual(look["name"], "Canopy Conway Clock")
        self.assertEqual(look["scene"], named_scene)

        # Selecting the named look accepts and acknowledges the same canonical
        # scene; preview remains inert and preserves 33x138 wall geometry.
        selected = self.client.post(
            f"/api/composer/looks/{look['id']}/open",
            json={"client_id": "desktop-firefox", "mutation_id": "select-look", "client_sequence": 1},
        )
        self.assertEqual(selected.status_code, 200, selected.get_json())
        selected_status = selected.get_json()["status"]
        self.assertEqual(selected_status["state"], "live")
        self.assertEqual(selected_status["desired"], selected_status["observed"])
        preview = self.client.post(
            "/api/composer/preview",
            json={"origin": "composer", "scene": named_scene,
                  "preview": {"monotonic_elapsed": 1.0, "wall_time": "2026-09-01T12:00:00+00:00"}},
        )
        self.assertEqual(preview.status_code, 200, preview.get_json())
        frame = preview.get_json()["frame"]
        self.assertEqual((frame["width"], frame["height"], frame["orientation"]),
                         (33, 138, "strip_major_led_zero_bottom"))
        self.assertEqual(self.wall.commands, [])

        # A valid live edit is observed immediately.  A rejected edit leaves the
        # last valid desired/observed output untouched.
        edited = copy.deepcopy(named_scene)
        edited["look"]["pace"] = 1.15
        live_edit = self.client.post("/api/composer/scene", json=self._request(edited, sequence=2))
        self.assertEqual(live_edit.status_code, 200, live_edit.get_json())
        acknowledged = live_edit.get_json()
        self.assertTrue(acknowledged["published"])
        self.assertEqual(acknowledged["desired"], acknowledged["observed"])
        invalid = copy.deepcopy(edited)
        invalid["look"]["presentation_brightness"] = 9
        rejected = self.client.post("/api/composer/scene", json=self._request(invalid, sequence=3))
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.get_json()["status"]["desired"], acknowledged["desired"])
        self.assertEqual(rejected.get_json()["status"]["observed"], acknowledged["observed"])

        # Stop makes a following valid edit local.  Only the explicit Go Live
        # action re-arms and observes that new scene.
        stopped = self.client.post("/api/composer/stop", json={"client_id": "desktop-firefox"})
        self.assertEqual(stopped.status_code, 200, stopped.get_json())
        self.assertEqual(stopped.get_json()["status"]["state"], "stopped")
        local = copy.deepcopy(edited)
        local["animation"]["parameters"]["seed"] = 909
        local_edit = self.client.post("/api/composer/scene", json=self._request(local, sequence=4))
        self.assertEqual(local_edit.status_code, 200, local_edit.get_json())
        self.assertFalse(local_edit.get_json()["published"])
        self.assertEqual(local_edit.get_json()["state"], "stopped")
        rearmed = self.client.post("/api/composer/go-live", json={"client_id": "desktop-firefox"})
        self.assertEqual(rearmed.status_code, 200, rearmed.get_json())
        self.assertEqual(rearmed.get_json()["status"]["desired"], rearmed.get_json()["status"]["observed"])

        # A reconnect never auto-applies an offline local edit.  Reload recovery
        # returns that exact current scene for the next page lifetime.
        self.assertFalse(self.client.post("/api/composer/connection", json={"connected": False}).get_json()["status"]["armed"])
        offline = copy.deepcopy(local)
        offline["look"]["palette_id"] = "ember"
        offline_edit = self.client.post("/api/composer/scene", json=self._request(offline, sequence=5))
        self.assertEqual(offline_edit.status_code, 200, offline_edit.get_json())
        self.assertFalse(offline_edit.get_json()["published"])
        reconnected = self.client.post("/api/composer/connection", json={"connected": True})
        self.assertEqual(reconnected.status_code, 200, reconnected.get_json())
        self.assertEqual(reconnected.get_json()["status"]["state"], "stopped")
        self.assertFalse(reconnected.get_json()["status"]["armed"])
        recovered = self.client.get("/api/composer/recovery?client_id=mobile-webkit").get_json()
        self.assertTrue(recovered["recovery"]["authoritative"])
        self.assertEqual(recovered["recovery"]["scene"], offline)
        resumed = self.client.post("/api/composer/go-live", json={"client_id": "mobile-webkit"})
        self.assertEqual(resumed.status_code, 200, resumed.get_json())
        self.assertEqual(resumed.get_json()["status"]["state"], "live")
        self.assertEqual(resumed.get_json()["status"]["desired"], resumed.get_json()["status"]["observed"])
        self.assertEqual(self.wall.commands, [])


if __name__ == "__main__":
    unittest.main()
