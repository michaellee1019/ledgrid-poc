"""Focused contract tests for inert, output-accurate Composer library cards."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from animation.core.scene_runtime import CanonicalSceneRuntimeError
from tests.unit.test_composer_looks import _PreviewManager, _WallChannel
from web.app import AnimationWebInterface
from web.composer_library_state import ComposerLibraryState
from web.scene_look_store import SceneLookStore
from web.working_draft_store import WorkingDraftStore


class ComposerVisualLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.wall = _WallChannel()
        self.interface = AnimationWebInterface(self.wall, _PreviewManager(), local_mode=True)
        self.interface.composer_looks = SceneLookStore(Path(self.tmp.name) / "looks.json")
        self.interface.composer_library = ComposerLibraryState(Path(self.tmp.name) / "library.json")
        self.interface.working_draft = WorkingDraftStore(Path(self.tmp.name) / "draft.json")
        self.client = self.interface.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_starter_card_matches_direct_fixed_time_preview_without_state_changes(self) -> None:
        before_status = self.client.get("/api/composer/status").get_json()
        before_library = self.client.get("/api/composer/library").get_json()
        card = self.client.post("/api/composer/library/cards", json={"reference": {"kind": "starter", "id": "aurora_clock"}})
        self.assertEqual(card.status_code, 200)
        body = card.get_json()
        starter = self.client.get("/api/composer/starters/aurora_clock").get_json()["starter"]
        direct = self.client.post("/api/composer/preview", json={
            "origin": "composer",
            "scene": starter["scene"],
            "preview": body["preview_time"],
        }).get_json()
        self.assertEqual(body["reference"], {"kind": "starter", "id": "aurora_clock"})
        self.assertEqual(body["preview_time"], {"monotonic_elapsed": 12.0, "wall_time": "2026-08-31T12:00:00+00:00"})
        self.assertEqual((body["basis"], body["frame"]), (direct["basis"], direct["frame"]))
        self.assertEqual((body["frame"]["width"], body["frame"]["height"], body["wall_mutations"]), (33, 138, 0))
        self.assertEqual(self.client.get("/api/composer/status").get_json(), before_status)
        self.assertEqual(self.client.get("/api/composer/library").get_json(), before_library)
        self.assertIsNone(self.interface.working_draft.get())
        self.assertEqual((self.wall.commands, self.interface.composer_control.commands), ([], []))

    def test_saved_look_card_uses_its_current_canonical_basis_without_opening_it(self) -> None:
        scene = self.client.get("/api/composer/starters/aurora").get_json()["starter"]["scene"]
        saved = self.client.post("/api/composer/looks", json={"name": "Night Garden", "scene": scene}).get_json()["look"]
        before = self.client.get("/api/composer/library").get_json()
        response = self.client.post("/api/composer/library/cards", json={"reference": {"kind": "look", "id": saved["id"]}})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["basis"], saved["basis"])
        self.assertEqual(body["reference"], {"kind": "look", "id": saved["id"]})
        self.assertEqual(self.client.get("/api/composer/library").get_json(), before)
        self.assertEqual(self.client.get("/api/composer/looks").get_json()["looks"][0]["name"], "Night Garden")
        self.assertEqual((self.wall.commands, self.interface.composer_control.commands), ([], []))

    def test_bad_card_reference_fails_without_recent_favorite_draft_or_live_mutation(self) -> None:
        before_status = self.client.get("/api/composer/status").get_json()
        before_library = self.client.get("/api/composer/library").get_json()
        response = self.client.post("/api/composer/library/cards", json={"reference": {"kind": "look", "id": "missing"}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/composer/status").get_json(), before_status)
        self.assertEqual(self.client.get("/api/composer/library").get_json(), before_library)
        self.assertIsNone(self.interface.working_draft.get())

    def test_card_runtime_failure_is_json_visible_without_state_mutation(self) -> None:
        before_status = self.client.get("/api/composer/status").get_json()
        before_library = self.client.get("/api/composer/library").get_json()
        with patch.object(self.interface, "_composer_preview_payload", side_effect=CanonicalSceneRuntimeError("card runtime failed")):
            response = self.client.post("/api/composer/library/cards", json={"reference": {"kind": "starter", "id": "aurora"}})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "card runtime failed"})
        self.assertEqual(self.client.get("/api/composer/status").get_json(), before_status)
        self.assertEqual(self.client.get("/api/composer/library").get_json(), before_library)
        self.assertIsNone(self.interface.working_draft.get())
        self.assertEqual((self.wall.commands, self.interface.composer_control.commands), ([], []))

    def test_client_opens_current_built_ins_through_the_live_scene_boundary(self) -> None:
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        self.assertIn("`${api}/starters/${item.id}`", script)
        self.assertIn("await submit(starter.scene, {builtin: true})", script)
        self.assertIn("const endpoint = builtin ? '/built-ins/open' : '/scene';", script)

    def test_every_current_built_in_opens_without_stopping_live_output(self) -> None:
        scene = self.client.get("/api/composer/starters/aurora").get_json()["starter"]["scene"]
        self.client.post("/api/composer/scene", json={"origin": "composer", "scene": scene, "client_id": "starter-test", "client_sequence": 1})
        self.client.post("/api/composer/go-live", json={"client_id": "starter-test"})
        for sequence, starter in enumerate(self.client.get("/api/composer/starters").get_json()["starters"], start=2):
            detail = self.client.get(f"/api/composer/starters/{starter['id']}").get_json()["starter"]
            response = self.client.post("/api/composer/built-ins/open", json={"scene": detail["scene"], "client_id": "starter-test", "client_sequence": sequence})
            self.assertEqual(response.status_code, 200, response.get_json())
            self.assertTrue(response.get_json()["status"]["running"])


if __name__ == "__main__":
    unittest.main()
