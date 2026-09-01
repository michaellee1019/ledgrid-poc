"""Focused Scene v2 contracts for Composer's final plant optics."""

from __future__ import annotations

import base64
import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np

from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.scene_look_store import SceneLookStore
from web.working_draft_store import WorkingDraftStore


class ComposerPlantControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        self.interface.composer_looks = SceneLookStore(Path(self.tmp.name) / "looks.json")
        self.interface.working_draft = WorkingDraftStore(Path(self.tmp.name) / "recovery.json")
        self.client = self.interface.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @staticmethod
    def _with_optics(*, illuminate: float = 0.0, shadow: float = 0.0,
                     hue_shift: float = 0.0, enabled: bool = True) -> dict:
        scene = _current_scene()
        scene["plants"] = {"effects": {
            "version": 1,
            "active": ["illuminate", "shadow", "hue_shift"] if enabled else [],
            "strengths": ({"illuminate": illuminate, "shadow": shadow, "hue_shift": hue_shift}
                          if enabled else {}),
        }}
        return scene

    def _pixels(self, scene: dict) -> np.ndarray:
        response = self.client.post("/api/composer/preview", json={
            "origin": "composer", "scene": scene,
            "preview": {"monotonic_elapsed": 1.25, "wall_time": "2026-09-01T12:00:00+00:00"},
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        return np.frombuffer(
            base64.b64decode(response.get_json()["frame"]["pixels"]), dtype=np.uint8,
        ).reshape((33 * 138, 3))

    def test_three_live_controls_are_explicit_and_do_not_expose_calibration_editing(self) -> None:
        html = Path("web/templates/composer.html").read_text(encoding="utf-8")
        script = Path("web/static/js/composer_slice.js").read_text(encoding="utf-8")
        for effect in ("Illuminate", "Shadow", "Hue shift"):
            self.assertIn(effect, html)
        for control in (
            "plantIlluminateEnabled", "plantIlluminateStrength",
            "plantShadowEnabled", "plantShadowStrength",
            "plantHueShiftEnabled", "plantHueShiftStrength",
        ):
            self.assertIn(f'id="{control}"', html)
        for forbidden in ("plantCalibration", "plantGeometry", "plantHomography", "plantMaskEditor"):
            self.assertNotIn(forbidden, html)
        self.assertIn("const plantOptics = Object.freeze([", script)
        self.assertIn("const preservedActive = active.filter((id) => !plantOpticIds.has(id));", script)
        self.assertIn("addEventListener('input', (event) => { syncPlantOpticControl(optic); renderPlantOpticsStatus(); edit(event); });", script)

    def test_optics_converge_through_preview_live_recovery_and_saved_look(self) -> None:
        neutral = self._with_optics(enabled=False)
        zero = self._with_optics()
        changed = self._with_optics(illuminate=.55, shadow=.35, hue_shift=.45)
        np.testing.assert_array_equal(self._pixels(neutral), self._pixels(zero))
        self.assertFalse(np.array_equal(self._pixels(neutral), self._pixels(changed)))

        response = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": changed, "client_id": "plant-controls", "client_sequence": 1,
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        status = response.get_json()
        self.assertEqual(status["current"], status["desired"])
        self.assertEqual(status["current"], status["observed"])
        canonical = status["canonical_scene"]
        self.assertEqual(canonical["plants"], changed["plants"])
        self.assertNotIn("calibration", canonical["plants"])

        recovery = self.client.get("/api/composer/recovery?client_id=plant-controls").get_json()
        self.assertTrue(recovery["recovery"]["authoritative"])
        self.assertEqual(recovery["recovery"]["scene"], canonical)
        self.assertEqual(recovery["status"]["current"], status["current"])

        saved = self.client.post("/api/composer/looks", json={"name": "Plant optics", "scene": changed})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        look = saved.get_json()["look"]
        self.assertEqual(look["scene"], canonical)

        stopped = self.client.post("/api/composer/stop", json={"client_id": "plant-controls"})
        self.assertEqual(stopped.status_code, 200, stopped.get_json())
        self.assertFalse(stopped.get_json()["status"]["armed"])
        rearmed = self.client.post("/api/composer/go-live", json={"client_id": "plant-controls"})
        self.assertEqual(rearmed.status_code, 200, rearmed.get_json())
        live = rearmed.get_json()["status"]
        self.assertTrue(live["armed"])
        self.assertEqual(live["current"], live["desired"])
        self.assertEqual(live["current"], live["observed"])

    def test_shadow_alone_changes_the_canonical_frame_but_off_and_zero_are_exact_noops(self) -> None:
        disabled = _current_scene()
        disabled["plants"] = {"effects": {"version": 1, "active": [], "strengths": {}}}
        zero = copy.deepcopy(disabled)
        zero["plants"] = {"effects": {
            "version": 1, "active": ["shadow"], "strengths": {"shadow": 0.0},
        }}
        shadowed = copy.deepcopy(zero)
        shadowed["plants"]["effects"]["strengths"]["shadow"] = 0.5

        disabled_pixels = self._pixels(disabled)
        self.assertEqual(disabled_pixels.shape, (33 * 138, 3))
        np.testing.assert_array_equal(disabled_pixels, self._pixels(zero))
        self.assertFalse(np.array_equal(disabled_pixels, self._pixels(shadowed)))

    def test_invalid_optics_do_not_replace_the_last_valid_identity(self) -> None:
        valid = self._with_optics(illuminate=.4, shadow=.25, hue_shift=.2)
        accepted = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": valid, "client_id": "plant-controls", "client_sequence": 1,
        })
        self.assertEqual(accepted.status_code, 200, accepted.get_json())
        before = accepted.get_json()
        invalid = copy.deepcopy(valid)
        invalid["plants"]["effects"]["strengths"]["hue_shift"] = 1.1
        rejected = self.client.post("/api/composer/scene", json={
            "origin": "composer", "scene": invalid, "client_id": "plant-controls", "client_sequence": 2,
        })
        self.assertEqual(rejected.status_code, 400)
        after = self.client.get("/api/composer/status?client_id=plant-controls").get_json()
        for identity in ("current", "desired", "observed"):
            self.assertEqual(after[identity], before[identity])
        recovery = self.client.get("/api/composer/recovery?client_id=plant-controls").get_json()
        self.assertEqual(recovery["recovery"]["basis"], before["current"])


if __name__ == "__main__":
    unittest.main()
