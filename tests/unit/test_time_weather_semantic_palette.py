"""Scene-v2 palette/cache acceptance for the reviewed time-weather quartet."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from pathlib import Path
import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import resolve_scene
from animation.plugins.circadian_window import CircadianWindowAnimation
from animation.plugins.desert_wind import DesertWindAnimation
from animation.plugins.moonlit_fog_banks import MoonlitFogBanksAnimation
from animation.plugins.tidal_bioluminescence import TidalBioluminescenceAnimation
from ipc.scene_contract import normalize_composer_scene
from tests.unit.test_composer_slice import _current_scene
from web.composer_final_preview import ComposerFinalPreview, current_component_catalog


RENDERERS = {
    "circadian_window": CircadianWindowAnimation,
    "desert_wind": DesertWindAnimation,
    "moonlit_fog_banks": MoonlitFogBanksAnimation,
    "tidal_bioluminescence": TidalBioluminescenceAnimation,
}
MOOD_PAIRS = {
    "circadian_window": ("natural", "sleeper"),
    "desert_wind": ("ochre", "mars"),
    "moonlit_fog_banks": ("moonlit", "sleeper"),
    "tidal_bioluminescence": ("moonlit", "violet"),
}


class TimeWeatherSemanticPaletteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = current_component_catalog()
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    @staticmethod
    def _scene(component_id: str) -> dict:
        renderer = RENDERERS[component_id]
        scene = _current_scene()
        scene["animation"] = {
            "component_id": component_id,
            "version": 1,
            "provider": "python",
            "role": "animation",
            "parameters": dict(renderer.COMPONENT_DEFAULTS),
        }
        if component_id == "circadian_window":
            # Fixed time keeps this palette packet independent of host time.
            scene["animation"]["parameters"].update({"hour": 8.0, "time_scale": 0.0})
        return scene

    def test_scene_palettes_repaint_at_one_source_tick_without_semantic_or_cadence_changes(self) -> None:
        for component_id, renderer in RENDERERS.items():
            with self.subTest(component=component_id):
                source = self._scene(component_id)
                animation = renderer(self.controller, renderer.COMPONENT_DEFAULTS)
                fingerprints = set()
                semantic = cadence = None
                for palette_id in ("neutral", "mist", "spectrum", "ember"):
                    candidate = copy.deepcopy(source)
                    candidate["look"]["palette_id"] = palette_id
                    resolved = resolve_scene(candidate, self.catalog, monotonic_elapsed=2.0)
                    rendered = animation.render_resolved_scene(resolved)
                    self.assertTrue(rendered.changed)
                    self.assertEqual(rendered.pixels.shape, (33 * 138, 3))
                    self.assertEqual(renderer.component_descriptor().alpha_behavior.value, "opaque")
                    self.assertTrue(np.all(rendered.pixels >= 0))
                    fingerprints.add(rendered.pixels.tobytes())
                    if semantic is None:
                        semantic, cadence = animation.semantic_snapshot(), animation.cadence_snapshot()
                    else:
                        self.assertEqual(animation.semantic_snapshot(), semantic)
                        self.assertEqual(animation.cadence_snapshot(), cadence)
                    self.assertFalse(animation.render_resolved_scene(resolved).changed)
                self.assertEqual(len(fingerprints), 4)

    def test_composer_preview_keeps_moods_useful_and_applies_final_optics_once(self) -> None:
        root = Path(__file__).resolve().parents[2]

        def render(candidate: dict):
            # A Composer Preview runtime is created per candidate, matching an
            # independently resolved Preview request rather than a stale frame.
            return ComposerFinalPreview(self.catalog, root).render(
                normalize_composer_scene({"origin": "composer", "scene": candidate}, self.catalog),
                2.0,
                datetime(2026, 9, 10, 12, tzinfo=timezone.utc),
            )

        for component_id in RENDERERS:
            with self.subTest(component=component_id):
                source = self._scene(component_id)
                palette_frames = set()
                for palette_id in ("neutral", "mist", "spectrum", "ember"):
                    candidate = copy.deepcopy(source)
                    candidate["look"]["palette_id"] = palette_id
                    frame = render(candidate)
                    self.assertEqual(frame.stage_trace.count("plant_optics"), 1)
                    palette_frames.add(frame.pixels.tobytes())
                self.assertEqual(len(palette_frames), 4)

                mood_frames = []
                for mood in MOOD_PAIRS[component_id]:
                    candidate = copy.deepcopy(source)
                    candidate["look"]["palette_id"] = "mist"
                    candidate["animation"]["parameters"]["mood"] = mood
                    mood_frames.append(render(candidate).pixels)
                delta = np.abs(mood_frames[0].astype(np.int16) - mood_frames[1].astype(np.int16))
                self.assertGreater(float(delta.mean()), 0.25, f"{component_id} mood pair")
                self.assertGreater(int(delta.max()), 8, f"{component_id} mood pair")

                dark, bright = copy.deepcopy(source), copy.deepcopy(source)
                dark["background"]["parameters"]["gain"] = .16
                bright["background"]["parameters"]["gain"] = .94
                np.testing.assert_array_equal(render(dark).pixels, render(bright).pixels)

                zero = copy.deepcopy(source)
                zero["plants"] = {"effects": {"version": 1, "active": ["shadow"], "strengths": {"shadow": 0.0}}}
                active = copy.deepcopy(source)
                active["plants"] = {"effects": {"version": 1, "active": ["shadow"], "strengths": {"shadow": 0.5}}}
                off_frame, zero_frame, active_frame = (render(candidate) for candidate in (source, zero, active))
                np.testing.assert_array_equal(off_frame.pixels, zero_frame.pixels)
                self.assertFalse(np.array_equal(off_frame.pixels, active_frame.pixels))
                self.assertEqual([frame.stage_trace.count("plant_optics") for frame in (off_frame, zero_frame, active_frame)], [1, 1, 1])

    def test_circadian_fixed_hour_remains_scaled_scene_time(self) -> None:
        scene = self._scene("circadian_window")
        scene["animation"]["parameters"].update({"hour": 5.0, "time_scale": 3600.0})
        scene["look"]["pace"] = 2.0
        animation = CircadianWindowAnimation(self.controller, scene["animation"]["parameters"])
        early = animation.render_resolved_scene(resolve_scene(scene, self.catalog, monotonic_elapsed=0.0)).pixels.copy()
        late = animation.render_resolved_scene(resolve_scene(scene, self.catalog, monotonic_elapsed=2.0)).pixels.copy()
        self.assertFalse(np.array_equal(early, late))

    def test_circadian_samples_host_clock_once_per_source_tick_even_when_palette_repaints(self) -> None:
        scene = self._scene("circadian_window")
        scene["animation"]["parameters"].update({"hour": -1.0, "time_scale": 1.0})
        animation = CircadianWindowAnimation(self.controller, scene["animation"]["parameters"])
        sampled_hours = iter((6.5, 7.5))
        animation._current_hour = lambda _time: next(sampled_hours)  # type: ignore[method-assign]

        neutral = resolve_scene(scene, self.catalog, monotonic_elapsed=2.0)
        first = animation.render_resolved_scene(neutral)
        first_state = animation.semantic_snapshot()
        repainted_scene = copy.deepcopy(scene)
        repainted_scene["look"]["palette_id"] = "ember"
        repainted = animation.render_resolved_scene(
            resolve_scene(repainted_scene, self.catalog, monotonic_elapsed=2.0)
        )
        self.assertTrue(repainted.changed)
        self.assertFalse(np.array_equal(first.pixels, repainted.pixels))
        self.assertEqual(animation.semantic_snapshot(), first_state)
        self.assertEqual(animation.semantic_snapshot()["circadian_hour"], 6.5)

        animation.render_resolved_scene(
            resolve_scene(repainted_scene, self.catalog, monotonic_elapsed=2.1)
        )
        self.assertEqual(animation.semantic_snapshot()["circadian_hour"], 7.5)


if __name__ == "__main__":
    unittest.main()
