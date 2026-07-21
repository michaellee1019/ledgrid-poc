"""Tests for the long-form living ecosystem animation."""

import json
import re
import unittest
from pathlib import Path

import numpy as np

from animation import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.plugins.living_ecosystem import LivingEcosystemAnimation


class LivingEcosystemTests(unittest.TestCase):
    def make_animation(self, strips=32, leds=140, **config):
        return LivingEcosystemAnimation(PreviewLEDController(strips, leds), config)

    def test_animation_is_shipped_and_renders_canonical_frame(self):
        self.assertIn("living_ecosystem", AnimationManager.ALLOWED_PLUGINS)
        result = self.make_animation().generate_frame(1.0, 0)
        self.assertIsInstance(result, RenderedFrame)
        self.assertEqual(result.pixels.shape, (32 * 140, 3))
        self.assertEqual(result.pixels.dtype, np.uint8)
        self.assertGreater(np.count_nonzero(result.pixels), 32 * 140)

    def test_seed_reproduces_habitat_and_population(self):
        first = self.make_animation(seed=44)
        second = self.make_animation(seed=44)
        self.assertTrue(np.array_equal(first.water, second.water))
        self.assertTrue(np.array_equal(first.grass, second.grass))
        self.assertEqual([(c.species, c.pack, c.x, c.y) for c in first.creatures],
                         [(c.species, c.pack, c.x, c.y) for c in second.creatures])

    def test_day_night_cycle_has_distinct_light_levels(self):
        animation = self.make_animation()
        self.assertLess(animation._daylight(0), .05)
        self.assertGreater(animation._daylight(45), .95)
        self.assertLess(animation._daylight(90), .05)

    def test_lifecycle_duration_scales_canonical_ecology(self):
        animation = self.make_animation(lifecycle_minutes=5)
        animation.generate_frame(60.0, 0)
        self.assertAlmostEqual(animation.get_runtime_stats()["ecosystem_seconds"], 180.0, places=1)

    def test_density_controls_raise_population_capacity(self):
        sparse = self.make_animation(creature_density=.5)
        dense = self.make_animation(creature_density=2.0)
        self.assertLess(sparse._population_limits()[0], dense._population_limits()[0])
        self.assertLess(len(sparse.creatures), len(dense.creatures))

    def test_zero_creature_size_draws_exactly_one_pixel(self):
        animation = self.make_animation(creature_size=0, glow_strength=1)
        creature = animation.creatures[0]
        animation._canvas.fill(0)
        animation._draw_creature(creature, 1.0, animation._palette())
        self.assertEqual(np.count_nonzero(np.any(animation._canvas != 0, axis=2)), 1)

    def test_ecology_advances_at_fixed_rate_and_exposes_stats(self):
        animation = self.make_animation()
        animation.generate_frame(0.0, 0)
        animation.generate_frame(3.0, 1)
        stats = animation.get_runtime_stats()
        self.assertAlmostEqual(stats["ecosystem_seconds"], 3.0, places=1)
        self.assertGreater(stats["grazers"], 0)
        self.assertGreater(stats["hunters"], 0)
        self.assertEqual(stats["simulation_hz"], 8.0)

    def test_fit_nearby_parents_cross_genes_with_mutation(self):
        animation = self.make_animation(mutation_rate=.03)
        parents = [c for c in animation.creatures if c.species == 0][:2]
        animation.creatures = parents
        for parent in parents:
            parent.pack = 0
            parent.x = parent.y = 10.0
            parent.age = 30.0
            parent.energy = 100.0
            parent.cooldown = 0.0
        animation._reproduce()
        children = [c for c in animation.creatures if c.generation == 1]
        self.assertEqual(len(children), 1)
        self.assertTrue(np.all(children[0].genes[:4] >= .58))
        self.assertTrue(np.all(children[0].genes[:4] <= 1.5))

    def test_render_cap_reuses_frame(self):
        animation = self.make_animation(render_fps=40)
        first = animation.generate_frame(2.0, 0)
        skipped = animation.generate_frame(2.001, 1)
        advanced = animation.generate_frame(2.03, 2)
        self.assertTrue(first.changed)
        self.assertFalse(skipped.changed)
        self.assertIs(first.pixels, skipped.pixels)
        self.assertTrue(advanced.changed)

    def test_shipped_presets_cover_categories_and_color_metadata(self):
        preset_dir = Path(__file__).resolve().parents[2] / "presets" / "animations" / "living_ecosystem"
        presets = [json.loads(path.read_text(encoding="utf-8")) for path in preset_dir.glob("*.json")]
        categories = [preset["category"] for preset in presets]
        self.assertGreaterEqual(categories.count("Realistic"), 3)
        self.assertGreaterEqual(categories.count("Installation"), 3)
        self.assertGreaterEqual(categories.count("Sci-Fi"), 3)
        for preset in presets:
            self.assertEqual(preset["version"], 2)
            self.assertEqual(preset["animation"], "living_ecosystem")
            self.assertIn("lifecycle_minutes", preset["params"])
            self.assertIn(preset["params"]["palette"], LivingEcosystemAnimation.PALETTES)
            for color in preset["palette"]["colors"]:
                self.assertRegex(color, re.compile(r"^#[0-9A-Fa-f]{6}$"))


if __name__ == "__main__":
    unittest.main()
