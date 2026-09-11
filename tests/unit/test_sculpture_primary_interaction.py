"""Scene-v2 proofs for the bounded Cellular and Frost Composer gestures."""

from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np

from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.presentation_contracts import resolve_scene
from animation.plugins.cellular_tapestry import CellularTapestryAnimation
from animation.plugins.frostwork import FrostworkAnimation
from animation.plugins.reaction_diffusion_garden import ReactionDiffusionGardenAnimation
from animation.plugins.wind_in_the_reeds import WindInTheReedsAnimation
from tests.unit.test_composer_slice import _PreviewManager, _WallChannel, _current_scene
from web.app import AnimationWebInterface
from web.local_control import LocalControlChannel
from web.composer_final_preview import current_component_catalog
from web.scene_look_store import SceneLookStore
from web.working_draft_store import WorkingDraftStore


ROOT = Path(__file__).resolve().parents[2]
WALL_TIME = datetime.fromisoformat("2026-09-01T12:00:00+00:00")


class SculpturePrimaryInteractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        self.catalog = current_component_catalog()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _scene(self, renderer, parameters: dict) -> dict:
        scene = _current_scene()
        scene["animation"] = {
            "component_id": renderer.COMPONENT_ID, "version": 1,
            "provider": "python", "role": "animation", "parameters": parameters,
        }
        scene["look"]["palette_id"] = "spectrum"
        scene["look"]["pace"] = 1.0
        return scene

    def _render(self, animation, scene: dict, elapsed: float):
        context = resolve_scene(scene, self.catalog, monotonic_elapsed=elapsed)
        return animation.render_resolved_scene(context)

    @staticmethod
    def _rng(animation):
        return copy.deepcopy(animation.rng.bit_generator.state)

    def test_cellular_primary_waits_for_one_completed_row_and_preserves_rng(self) -> None:
        parameters = {"seed": 2801, "mutation": 0.01, "motion": 1.0, "row_interval": 0.1}
        scene = self._scene(CellularTapestryAnimation, parameters)
        primary = CellularTapestryAnimation(self.controller, parameters)
        baseline = CellularTapestryAnimation(self.controller, parameters)

        self._render(primary, scene, 0.0)
        self._render(baseline, scene, 0.0)
        before_current = primary.current.copy()
        before_history = primary.history.copy()
        rng_before = self._rng(primary)
        last_tick = primary._last_sim_tick
        self.assertTrue(primary.handle_interaction("primary", 8.9, 60.0, 1.0))
        self.assertFalse(primary.handle_interaction("primary", 9.0, 60.0, 1.0))
        self.assertFalse(primary.handle_interaction("secondary", 8.0, 60.0, 1.0))
        self.assertFalse(primary.handle_interaction("primary", 33.0, 60.0, 1.0))

        cached = self._render(primary, scene, 0.0)
        self.assertFalse(cached.changed)
        np.testing.assert_array_equal(primary.current, before_current)
        np.testing.assert_array_equal(primary.history, before_history)
        self.assertEqual(self._rng(primary), rng_before)
        self.assertEqual(primary._last_sim_tick, last_tick)

        self._render(primary, scene, 0.05)
        self._render(baseline, scene, 0.05)
        boundary = self._render(primary, scene, 0.10)
        unchanged_row = self._render(baseline, scene, 0.10)
        np.testing.assert_array_equal(boundary.pixels, unchanged_row.pixels)
        np.testing.assert_array_equal(primary.history, baseline.history)
        self.assertNotEqual(bool(primary.current[8]), bool(baseline.current[8]))
        self.assertEqual(self._rng(primary), self._rng(baseline))
        self.assertEqual(primary.get_runtime_stats(), {
            "primary_interactions_received": 1, "primary_interactions_applied": 1,
            "primary_interactions_rejected": 3, "primary_interaction_pending": False,
        })

        injected = self._render(primary, scene, 0.20)
        untouched = self._render(baseline, scene, 0.20)
        self.assertFalse(np.array_equal(injected.pixels, untouched.pixels))
        self.assertEqual(self._rng(primary), self._rng(baseline))

    def test_frost_primary_waits_for_the_next_tick_and_preserves_rng(self) -> None:
        parameters = {"seed": 1401, "motion": 0.46, "density": 0.48, "melt_cycle": 0.55}
        scene = self._scene(FrostworkAnimation, parameters)
        primary = FrostworkAnimation(self.controller, parameters)
        baseline = FrostworkAnimation(self.controller, parameters)

        self._render(primary, scene, 0.0)
        self._render(baseline, scene, 0.0)
        occupied_before = primary.occupied.copy()
        age_before = primary.age.copy()
        rng_before = self._rng(primary)
        last_tick = primary._last_sim_tick
        self.assertTrue(primary.handle_interaction("primary", 8.9, 60.9, 1.0))
        self.assertFalse(primary.handle_interaction("primary", 9.0, 60.0, 1.0))
        self.assertFalse(primary.handle_interaction("secondary", 8.0, 60.0, 1.0))
        self.assertFalse(primary.handle_interaction("primary", -0.1, 60.0, 1.0))

        cached = self._render(primary, scene, 0.0)
        self.assertFalse(cached.changed)
        np.testing.assert_array_equal(primary.occupied, occupied_before)
        np.testing.assert_array_equal(primary.age, age_before)
        self.assertEqual(self._rng(primary), rng_before)
        self.assertEqual(primary._last_sim_tick, last_tick)

        changed = self._render(primary, scene, 0.05)
        untouched = self._render(baseline, scene, 0.05)
        self.assertFalse(np.array_equal(changed.pixels, untouched.pixels))
        self.assertTrue(primary.occupied[8, 60])
        self.assertEqual(self._rng(primary), self._rng(baseline))
        self.assertEqual(primary.get_runtime_stats(), {
            "primary_interactions_received": 1, "primary_interactions_applied": 1,
            "primary_interactions_rejected": 3, "primary_interaction_pending": False,
        })

    def test_garden_primary_queues_a_fixed_clamped_reagent_disk_without_rng(self) -> None:
        primary = ReactionDiffusionGardenAnimation(self.controller, {"seed": 9101})
        baseline = ReactionDiffusionGardenAnimation(self.controller, {"seed": 9101})
        initial = primary.generate_frame(0.0, 0)
        baseline.generate_frame(0.0, 0)
        before = (primary.u.copy(), primary.v.copy(), primary.age.copy(), self._rng(primary), primary._last_sim_tick, primary._render_key)

        self.assertTrue(primary.handle_interaction("primary", 0.0, 137.9, .25))
        self.assertFalse(primary.handle_interaction("primary", 8.0, 60.0, 1.0))
        self.assertFalse(primary.handle_interaction("secondary", 8.0, 60.0, 1.0))
        self.assertFalse(primary.handle_interaction("primary", 33.0, 60.0, 1.0))
        self.assertFalse(primary.handle_interaction("primary", 8.0, 138.0, 1.0))
        self.assertFalse(primary.handle_interaction("primary", 8.0, 60.0, float("nan")))

        cached = primary.generate_frame(0.0, 1)
        self.assertFalse(cached.changed)
        np.testing.assert_array_equal(cached.pixels, initial.pixels)
        np.testing.assert_array_equal(primary.u, before[0]); np.testing.assert_array_equal(primary.v, before[1]); np.testing.assert_array_equal(primary.age, before[2])
        self.assertEqual(self._rng(primary), before[3]); self.assertEqual(primary._last_sim_tick, before[4]); self.assertEqual(primary._render_key, before[5])

        primary.generate_frame(1.0 / primary.SOURCE_FPS, 2)
        baseline.generate_frame(1.0 / baseline.SOURCE_FPS, 2)
        np.testing.assert_array_equal(primary.u[0:3, 135:138], np.full((3, 3), .2, dtype=np.float32))
        np.testing.assert_array_equal(primary.v[0:3, 135:138], np.full((3, 3), .9, dtype=np.float32))
        self.assertEqual(self._rng(primary), self._rng(baseline))
        self.assertEqual(primary.get_runtime_stats(), {
            "primary_interactions_received": 1, "primary_interactions_applied": 1,
            "primary_interactions_rejected": 5, "primary_interaction_pending": False,
        })
        repeated = primary.generate_frame(1.0 / primary.SOURCE_FPS, 3)
        self.assertFalse(repeated.changed)
        self.assertEqual(primary.get_runtime_stats()["primary_interactions_applied"], 1)

    def test_reeds_primary_queues_one_local_gaussian_that_then_relaxes(self) -> None:
        params = {"seed": 6101, "wind": 0.0, "gustiness": 0.0}
        primary = WindInTheReedsAnimation(self.controller, params)
        baseline = WindInTheReedsAnimation(self.controller, params)
        initial = primary.generate_frame(0.0, 0)
        baseline.generate_frame(0.0, 0)
        before = (primary.base_x.copy(), primary.lengths.copy(), primary.phases.copy(), primary.bend.copy(), primary.gust_phase, self._rng(primary), primary._last_sim_tick)

        self.assertTrue(primary.handle_interaction("primary", 8.0, 100.0, .75))
        self.assertFalse(primary.handle_interaction("primary", 9.0, 100.0, .75))
        self.assertFalse(primary.handle_interaction("secondary", 8.0, 100.0, 1.0))
        self.assertFalse(primary.handle_interaction("primary", -0.1, 100.0, 1.0))
        self.assertFalse(primary.handle_interaction("primary", 8.0, 138.0, 1.0))
        self.assertFalse(primary.handle_interaction("primary", 8.0, 100.0, 0.0))

        cached = primary.generate_frame(0.0, 1)
        self.assertFalse(cached.changed)
        np.testing.assert_array_equal(cached.pixels, initial.pixels)
        np.testing.assert_array_equal(primary.base_x, before[0]); np.testing.assert_array_equal(primary.lengths, before[1]); np.testing.assert_array_equal(primary.phases, before[2]); np.testing.assert_array_equal(primary.bend, before[3])
        self.assertEqual(primary.gust_phase, before[4]); self.assertEqual(self._rng(primary), before[5]); self.assertEqual(primary._last_sim_tick, before[6])

        primary.generate_frame(1.0 / primary.SOURCE_FPS, 2)
        baseline.generate_frame(1.0 / baseline.SOURCE_FPS, 2)
        tip_y = primary._shape[1] - 1 - baseline.lengths
        nearest_y = np.clip(100.0, tip_y, primary._shape[1] - 1)
        weight = np.exp(-.5 * (((baseline.base_x - 8.0) / (primary._shape[0] * .12)) ** 2 + ((nearest_y - 100.0) / (primary._shape[1] * .10)) ** 2))
        np.testing.assert_allclose(primary.bend, np.clip(.85 * .75 * weight, -1.5, 1.5))
        self.assertFalse(np.array_equal(primary.bend, baseline.bend))
        self.assertEqual(self._rng(primary), self._rng(baseline))
        self.assertEqual(primary.get_runtime_stats(), {
            "primary_interactions_received": 1, "primary_interactions_applied": 1,
            "primary_interactions_rejected": 5, "primary_interaction_pending": False,
        })
        primary.generate_frame(2.0 / primary.SOURCE_FPS, 3)
        np.testing.assert_allclose(primary.bend, np.clip(.85 * .75 * weight, -1.5, 1.5) * .82)
        self.assertEqual(primary.get_runtime_stats()["primary_interactions_applied"], 1)

    def test_living_interactions_have_exact_no_input_seeded_parity(self) -> None:
        for renderer in (ReactionDiffusionGardenAnimation, WindInTheReedsAnimation):
            left, right = renderer(self.controller), renderer(self.controller)
            for tick in range(121):
                left_frame = left.generate_frame(tick / left.SOURCE_FPS, tick)
                right_frame = right.generate_frame(tick / right.SOURCE_FPS, tick)
                np.testing.assert_array_equal(left_frame.pixels, right_frame.pixels)
                self.assertEqual(left.logical_state(), right.logical_state())
                self.assertEqual(self._rng(left), self._rng(right))
                self.assertEqual(left._last_sim_tick, right._last_sim_tick)
                self.assertEqual(left._render_key, right._render_key)

    def test_actual_composer_route_admits_only_declared_sculpture_primaries(self) -> None:
        interface = AnimationWebInterface(_WallChannel(), _PreviewManager(), local_mode=True)
        interface.composer_looks = SceneLookStore(Path(self.tmp.name) / "looks.json")
        interface.working_draft = WorkingDraftStore(Path(self.tmp.name) / "recovery.json")
        client = interface.app.test_client()
        script = (ROOT / "web/static/js/composer_slice.js").read_text(encoding="utf-8")
        start = script.index("const primaryInstruments = Object.freeze")
        end = script.index("function renderLibrary()", start)
        gesture = script[start:end]

        for sequence, renderer in enumerate((CellularTapestryAnimation, FrostworkAnimation, ReactionDiffusionGardenAnimation, WindInTheReedsAnimation), start=1):
            scene = self._scene(renderer, dict(renderer.COMPONENT_DEFAULTS))
            published = client.post("/api/composer/scene", json={
                "origin": "composer", "scene": scene, "client_id": "sculpture-primary",
                "client_sequence": sequence,
            })
            self.assertEqual(published.status_code, 200, published.get_json())
            basis = published.get_json()["current"]
            accepted = client.post("/api/interaction", json={
                "kind": "primary", "x": 8.0, "y": 60.0, "strength": 1.0,
            })
            self.assertEqual(accepted.status_code, 200, accepted.get_json())
            self.assertTrue(accepted.get_json()["accepted"])
            self.assertEqual(accepted.get_json()["component_id"], renderer.COMPONENT_ID)
            self.assertEqual(accepted.get_json()["basis"]["digest"], basis["digest"])
            self.assertEqual(client.post("/api/interaction", json={
                "kind": "secondary", "x": 8.0, "y": 60.0, "strength": 1.0,
            }).status_code, 400)
            preview = client.post("/api/composer/preview", json={
                "origin": "composer", "scene": scene,
                "preview": {"monotonic_elapsed": 0.05, "wall_time": WALL_TIME.isoformat()},
            })
            self.assertEqual(preview.status_code, 200, preview.get_json())

        for component_id in ("cellular_tapestry", "frostwork", "reaction_diffusion_garden", "wind_in_the_reeds"):
            self.assertIn(f"{component_id}:", gesture)
        for component_id in ("flow_field_silk", "living_stained_glass", "quasicrystal_bloom"):
            self.assertNotIn(f"{component_id}:", gesture)

    def test_living_primary_routes_through_normal_live_api_path(self) -> None:
        manager = AnimationManager(self.controller, auto_start=False)
        client = AnimationWebInterface(LocalControlChannel(manager), manager, local_mode=True).app.test_client()
        for renderer in (ReactionDiffusionGardenAnimation, WindInTheReedsAnimation):
            with self.subTest(component=renderer.COMPONENT_ID):
                manager.current_animation = renderer(self.controller)
                manager.current_animation_name = renderer.COMPONENT_ID
                accepted = client.post("/api/interaction", json={"kind": "primary", "x": 8.0, "y": 60.0, "strength": .75})
                self.assertEqual(accepted.status_code, 200, accepted.get_json())
                self.assertTrue(accepted.get_json()["accepted"])
                self.assertEqual(client.post("/api/interaction", json={"kind": "secondary", "x": 8.0, "y": 60.0, "strength": .75}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
