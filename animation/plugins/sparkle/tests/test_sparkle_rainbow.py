"""Rainbow Sparkle assigns a stable random hue per newly lit pixel."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.manager import PreviewLEDController
from animation.core.presentation_contracts import ResolvedScene
from animation.plugins.sparkle import SparkleAnimation


def _context(animation: SparkleAnimation, palette_id: str, phase_time: float) -> ResolvedScene:
    return ResolvedScene(
        canonical_scene={"plants": {"effects": {"version": 1, "active": [], "strengths": {}}}},
        canonical_bytes=b"look", digest="look",
        descriptor=animation.component_descriptor(),
        parameters=dict(animation.params),
        palette={"palette_id": palette_id}, phase_time=phase_time, plant_inputs={},
    )


class SparkleRainbowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def _run(self, palette_id: str, frames: int = 48, seed: int = 6104) -> SparkleAnimation:
        animation = SparkleAnimation(self.controller, {"seed": seed, "density": 0.45, "linger": 0.8, "twinkle": 0.9})
        hz = SparkleAnimation.SIMULATION_HZ
        for index in range(frames):
            animation.set_presentation_context(_context(animation, palette_id, index / hz))
            animation.generate_frame(index / hz, index)
        return animation

    def test_each_new_pixel_gets_its_own_hue_and_keeps_it_while_fading(self) -> None:
        animation = self._run("rainbow", frames=36)
        before_spark = animation._spark.copy()
        before_hue = animation._spark_hue.copy()
        hz = SparkleAnimation.SIMULATION_HZ
        tick = 36
        animation.set_presentation_context(_context(animation, "rainbow", tick / hz))
        animation.generate_frame(tick / hz, tick)
        newborn = (before_spark < 1.0e-6) & (animation._spark > 0.99)
        self.assertGreater(int(np.count_nonzero(newborn)), 8)
        born_hues = np.round(animation._spark_hue[newborn], 5)
        self.assertEqual(len(set(born_hues.tolist())), born_hues.size)
        fading = (before_spark > 0.2) & (animation._spark < before_spark - 1.0e-6) & (animation._spark > 0.05)
        self.assertGreater(int(np.count_nonzero(fading)), 8)
        np.testing.assert_allclose(animation._spark_hue[fading], before_hue[fading], atol=1.0e-6)
        live = animation._spark > 0.35
        hues = animation._spark_hue[live]
        xs = np.broadcast_to(animation._x, animation._spark.shape)[live]
        self.assertGreaterEqual(len(set(np.round(hues, 2).tolist())), 12)
        self.assertLess(abs(float(np.corrcoef(hues, xs)[0, 1])), 0.35)

    def test_rainbow_recolors_without_changing_where_sparkles_appear(self) -> None:
        ember = self._run("ember")
        rainbow = self._run("rainbow")
        np.testing.assert_array_equal(ember._spark, rainbow._spark)
        self.assertEqual(dict(ember.semantic_snapshot()), dict(rainbow.semantic_snapshot()))
        self.assertFalse(np.array_equal(ember._pixels, rainbow._pixels))
        lit = rainbow._spark.reshape(-1) > 0.4
        channels = np.argmax(rainbow._pixels[lit], axis=1)
        self.assertGreaterEqual(len(set(channels.tolist())), 3)


if __name__ == "__main__":
    unittest.main()
