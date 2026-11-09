"""Holiday Looks recolor the requested instruments without changing simulation."""

from __future__ import annotations

import unittest
from pathlib import Path

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import ResolvedScene
from animation.libraries.scene_palettes import HOLIDAY_IDS, SCENE_PALETTE_IDS
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation, SEMANTIC_PALETTES as AURORA_PALETTES
from animation.plugins.firefly_synchrony import FireflySynchronyAnimation
from animation.plugins.fireworks import FireworksAnimation
from animation.plugins.lava_lamp import LavaLampAnimation
from animation.plugins.ocean_waves import OceanWavesAnimation
from animation.plugins.mini_metro import MiniMetroAnimation
from animation.plugins.campfire import CampfireAnimation
from animation.plugins.rainbow import RainbowAnimation
from animation.plugins.snake import SnakeAnimation
from animation.plugins.sparkle import SparkleAnimation
from animation.plugins.wave import WaveAnimation
from web.automation_advanced import PALETTES as AUTOMATION_PALETTES


ROOT = Path(__file__).resolve().parents[2]
LOOKS = ("ember", "rainbow", *HOLIDAY_IDS)


def _context(animation, palette_id: str, phase_time: float = 1.0) -> ResolvedScene:
    return ResolvedScene(
        canonical_scene={"plants": {"effects": {"version": 1, "active": [], "strengths": {}}}},
        canonical_bytes=b"look", digest="look",
        descriptor=animation.component_descriptor(),
        parameters=dict(animation.params),
        palette={"palette_id": palette_id}, phase_time=phase_time, plant_inputs={},
    )


class HolidayPaletteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_composer_and_automation_publish_the_holiday_looks(self) -> None:
        html = (ROOT / "web/templates/composer.html").read_text(encoding="utf-8")
        for palette_id, label in (
            ("xmas", "Christmas"), ("fall", "Fall"), ("easter", "Easter"),
            ("july4", "4th of July"), ("rainbow", "Rainbow"),
        ):
            self.assertIn(f'value="{palette_id}"', html)
            self.assertIn(label, html)
        self.assertEqual(AUTOMATION_PALETTES, SCENE_PALETTE_IDS)
        self.assertEqual(set(AURORA_PALETTES), set(SCENE_PALETTE_IDS))

    def test_fireworks_and_snake_recolor_from_the_same_show(self) -> None:
        for factory in (FireworksAnimation, SnakeAnimation):
            with self.subTest(component=factory.COMPONENT_ID):
                animation = factory(self.controller)
                for index in range(36):
                    animation.generate_frame(index / 24.0, index)
                snapshot = dict(animation.semantic_snapshot())
                frames = {}
                for palette_id in LOOKS:
                    animation._paint(palette_id, 1.0)
                    frames[palette_id] = animation._pixels.copy()
                    self.assertEqual(dict(animation.semantic_snapshot()), snapshot)
                self.assertEqual(len({pixels.tobytes() for pixels in frames.values()}), len(frames))

    def test_lava_sparkle_wave_rainbow_and_aurora_keep_motion_and_change_color(self) -> None:
        for factory in (
            AuroraCurtainsAnimation, CampfireAnimation, FireflySynchronyAnimation, LavaLampAnimation, MiniMetroAnimation,
            OceanWavesAnimation, RainbowAnimation, SparkleAnimation, WaveAnimation,
        ):
            with self.subTest(component=factory.COMPONENT_ID):
                frames = {}
                snapshots = []
                for palette_id in LOOKS:
                    animation = factory(self.controller)
                    animation.set_presentation_context(_context(animation, palette_id))
                    frames[palette_id] = np.asarray(animation.generate_frame(1.0, 0).pixels).copy()
                    if hasattr(animation, "semantic_snapshot"):
                        snapshots.append(dict(animation.semantic_snapshot()))
                self.assertEqual(len({pixels.tobytes() for pixels in frames.values()}), len(frames))
                if snapshots:
                    self.assertTrue(all(item == snapshots[0] for item in snapshots))


if __name__ == "__main__":
    unittest.main()
