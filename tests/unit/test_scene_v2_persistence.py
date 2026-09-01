"""Focused current-only Scene v2 look and recovery API contracts."""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from ipc.scene_contract import LocalSceneAdapter
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel
from tests.unit.test_live_scene_state import _Channel
from tests.unit.test_scene_activation_contract import _catalog, _scene
from web.app import AnimationWebInterface
from web.live_scene_state import LiveSceneState
from web.scene_look_store import SceneLookStore
from web.working_draft_store import WorkingDraftStore


class SceneV2PersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.control = _Channel()
        self.interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        catalog = _catalog()
        self.interface.composer_catalog = catalog
        self.interface.composer_adapter = LocalSceneAdapter(catalog)
        self.interface.composer_control = self.control
        self.interface.composer_live = LiveSceneState(catalog, self.interface.composer_adapter, self.control)
        self.interface.composer_looks = SceneLookStore(Path(self.tmp.name) / "looks.json")
        self.interface.working_draft = WorkingDraftStore(Path(self.tmp.name) / "recovery.json")
        self.client = self.interface.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _changed_scene(seed: int) -> dict:
        value = _scene()
        value["animation"] = {**value["animation"], "parameters": {"seed": seed}}
        return value

    def _submit(self, scene: dict, **extra: object):
        return self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, **extra})

    def test_valid_live_submission_updates_recovery_and_rejection_leaves_it_unchanged(self) -> None:
        accepted = self._submit(self._changed_scene(31), client_id="operator")
        self.assertEqual(accepted.status_code, 200)
        recovered = self.client.get("/api/composer/draft").get_json()["draft"]
        self.assertEqual(recovered["basis"], accepted.get_json()["desired"])
        self.assertEqual(recovered["scene"]["animation"]["parameters"]["seed"], 31)
        self.assertNotIn("output_power", recovered["scene"])
        self.assertNotIn("calibration", recovered["scene"]["plants"])

        invalid = self._changed_scene(32)
        invalid["output_power"] = True
        self.assertEqual(self._submit(invalid, client_id="operator").status_code, 400)
        still_recovered = self.client.get("/api/composer/draft").get_json()["draft"]
        self.assertEqual(still_recovered["basis"], recovered["basis"])
        self.assertEqual(len(self.control.commands), 1)

    def test_timeout_after_acceptance_recovers_new_desired_scene_without_changing_observed_output(self) -> None:
        first = self._submit(self._changed_scene(35), client_id="operator")
        self.assertEqual(first.status_code, 200)
        first_status = first.get_json()
        self.control.fail_next = True
        timed_out = self._submit(self._changed_scene(36), client_id="operator")
        self.assertEqual(timed_out.status_code, 504)
        status = timed_out.get_json()["status"]
        self.assertEqual(status["state"], "recovery")
        self.assertNotEqual(status["desired"], first_status["desired"])
        self.assertEqual(status["observed"], first_status["observed"])
        recovered = self.client.get("/api/composer/draft").get_json()["draft"]
        self.assertEqual(recovered["basis"], status["desired"])
        self.assertEqual(recovered["scene"]["animation"]["parameters"]["seed"], 36)

    def test_recovery_can_reseed_a_restarted_live_state_before_go_live(self) -> None:
        accepted = self._submit(self._changed_scene(37), client_id="first-page", client_sequence=1)
        self.assertEqual(accepted.status_code, 200)
        # Simulate a local-process restart: the durable Scene v2 remains, while
        # the in-memory current scene has not yet been explicitly re-established.
        self.interface.composer_live = LiveSceneState(
            self.interface.composer_catalog, self.interface.composer_adapter, self.control,
        )
        recovery = self.client.get("/api/composer/recovery?client_id=reloaded-page").get_json()
        self.assertIsNone(recovery["status"]["current"])
        self.assertEqual(recovery["recovery"]["scene"]["animation"]["parameters"]["seed"], 37)
        reseeded = self._submit(recovery["recovery"]["scene"], client_id="reloaded-page", client_sequence=1)
        self.assertEqual(reseeded.status_code, 200)
        live = self.client.post("/api/composer/go-live", json={"client_id": "reloaded-page"})
        self.assertEqual(live.status_code, 200)
        self.assertIsNotNone(live.get_json()["status"]["current"])

    def test_save_as_open_and_save_update_whole_scene_without_storing_power(self) -> None:
        first = self._changed_scene(41)
        saved = self.client.post("/api/composer/looks", json={"name": "Garden", "scene": first})
        self.assertEqual(saved.status_code, 200)
        look = saved.get_json()["look"]
        self.assertEqual(set(look["scene"]), {"schema", "background", "animation", "widgets", "plants", "look"})

        self.assertEqual(self.client.post("/api/composer/stop", json={}).status_code, 200)
        opened = self.client.post(f"/api/composer/looks/{look['id']}/open", json={"client_id": "operator"})
        self.assertEqual(opened.status_code, 200)
        self.assertEqual(opened.get_json()["status"]["state"], "stopped")
        self.assertFalse(opened.get_json()["status"]["armed"])

        changed = self._changed_scene(42)
        updated = self.client.post("/api/composer/looks/save", json={"scene": changed})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["look"]["id"], look["id"])
        self.assertEqual(updated.get_json()["look"]["scene"]["animation"]["parameters"]["seed"], 42)
        reloaded = self.interface.composer_looks.get(look["id"])
        self.assertEqual(reloaded["scene"], updated.get_json()["look"]["scene"])
        self.assertEqual(self.client.get("/api/composer/draft").get_json()["draft"]["opened_look_id"], look["id"])

    def test_deleting_opened_look_clears_recovery_cursor_and_requires_save_as(self) -> None:
        saved = self.client.post("/api/composer/looks", json={"name": "Garden", "scene": self._changed_scene(51)}).get_json()["look"]
        self.assertEqual(self.client.post(f"/api/composer/looks/{saved['id']}/open", json={}).status_code, 200)
        self.assertEqual(self.client.delete(f"/api/composer/looks/{saved['id']}").status_code, 200)
        recovered = self.client.get("/api/composer/draft").get_json()["draft"]
        self.assertIsNone(recovered["opened_look_id"])
        self.assertEqual(self.client.post("/api/composer/looks/save", json={"scene": self._changed_scene(52)}).status_code, 409)

    def test_builtin_selection_clears_user_look_cursor_and_keeps_live_state(self) -> None:
        saved = self.client.post("/api/composer/looks", json={"name": "Garden", "scene": self._changed_scene(61)}).get_json()["look"]
        self.assertEqual(self.client.post(f"/api/composer/looks/{saved['id']}/open", json={}).status_code, 200)
        builtin = self.client.post("/api/composer/built-ins/open", json={"scene": self._changed_scene(62)})
        self.assertEqual(builtin.status_code, 200)
        self.assertTrue(builtin.get_json()["builtin"])
        self.assertEqual(builtin.get_json()["status"]["state"], "live")
        recovered = self.client.get("/api/composer/draft").get_json()["draft"]
        self.assertIsNone(recovered["opened_look_id"])
        self.assertEqual(self.client.post("/api/composer/looks/save", json={"scene": self._changed_scene(63)}).status_code, 409)

    def test_save_requires_an_opened_user_look_and_import_is_one_time_and_atomic(self) -> None:
        self.assertEqual(self.client.post("/api/composer/looks/save", json={"scene": _scene()}).status_code, 409)
        valid = {"name": "Imported", "selected": True, "scene_v2": _scene()}
        invalid = copy.deepcopy(valid)
        invalid["name"] = "Broken"
        invalid["scene_v2"]["output_power"] = True
        rejected = self.client.post("/api/composer/looks/import-legacy", json={"looks": [valid, invalid]})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.client.get("/api/composer/looks").get_json()["looks"], [])

        imported = self.client.post("/api/composer/looks/import-legacy", json={"looks": [valid]})
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.get_json()["looks"][0]["name"], "Imported")
        again = self.client.post("/api/composer/looks/import-legacy", json={"looks": []})
        self.assertEqual(again.status_code, 400)
        self.assertIn("already", again.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
