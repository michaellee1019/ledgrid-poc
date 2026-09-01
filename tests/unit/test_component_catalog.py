"""The checked, finite Scene v2 Composer component packet."""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from animation.core.component_catalog import ComponentDescriptor
from animation.core.manager import PreviewLEDController
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.canopy_cup import CanopyCupAnimation
from animation.plugins.conway_life import ConwayLifeAnimation
from animation.plugins.emoji_arranger import EmojiArrangerAnimation
from animation.plugins.firefly_synchrony import FireflySynchronyAnimation
from animation.plugins.fireworks import FireworksAnimation
from animation.plugins.cyclic_reef import CyclicReefAnimation
from animation.plugins.maze_chase import MazeChaseAnimation
from animation.plugins.pinball import PinballAnimation
from animation.plugins.pixel_quest import PixelQuestAnimation
from animation.plugins.snake import SnakeAnimation
from animation.plugins.tetris import TetrisAnimation
from web.composer_final_preview import current_component_catalog


_ROOT = Path(__file__).resolve().parents[2]


class ComponentCatalogTests(unittest.TestCase):
    def test_catalog_contains_only_the_qualified_scene_v2_packet(self) -> None:
        catalog = current_component_catalog()

        entries = {
            descriptor.component_id: descriptor
            for descriptor in catalog.descriptors
        }
        self.assertEqual(set(entries), {
            "native_aurora", "aurora_curtains", "canopy_cup", "ascii_drop", "emoji", "christmas_tree", "night_train_windows", "conway_life", "tetris", "firefly_synchrony", "fireworks", "flame_burst", "fluid_tank", "cyclic_reef", "lava_lamp", "snake", "maze_chase", "pinball", "pixel_quest", "gradient", "rainbow", "solid", "sparkle", "wave", "circadian_window", "cloud_canyon", "desert_wind", "moonlit_fog_banks", "rain_on_glass", "tidal_bioluminescence", "waterfall_veil", "clock_overlay", "emoji_arranger",
        })
        self.assertEqual(
            [
                (entry.provider.value, entry.role.value, entry.timing_policy.value,
                 entry.alpha_behavior.value, entry.palette_policy.value,
                 tuple(capability.value for capability in entry.plant_capabilities),
                 entry.fidelity_exceptions)
                for entry in catalog.descriptors
            ],
            [
                ("receiver_native", "background", "scaled_context", "none", "semantic", ("final_optics",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "premultiplied_rgba", "semantic", ("simulation_inputs",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "animation", "scaled_context", "opaque", "semantic", ("effect_intent",), ()),
                ("python", "widget", "wall_clock", "premultiplied_rgba", "semantic", ("effect_intent",), ()),
                ("python", "widget", "scaled_context", "premultiplied_rgba", "semantic", ("effect_intent",), ()),
            ],
        )

    def test_plugin_manifests_match_their_qualified_descriptors(self) -> None:
        catalog = current_component_catalog()
        descriptors = {entry.component_id: entry for entry in catalog.descriptors}
        locations = {
            "aurora_curtains": _ROOT / "animation/plugins/aurora_curtains/manifest.json",
            "canopy_cup": _ROOT / "animation/plugins/canopy_cup/manifest.json",
            "conway_life": _ROOT / "animation/plugins/conway_life/manifest.json",
            "tetris": _ROOT / "animation/plugins/tetris/manifest.json",
            "firefly_synchrony": _ROOT / "animation/plugins/firefly_synchrony/manifest.json",
            "fireworks": _ROOT / "animation/plugins/fireworks/manifest.json",
            "cyclic_reef": _ROOT / "animation/plugins/cyclic_reef/manifest.json",
            "snake": _ROOT / "animation/plugins/snake/manifest.json",
            "maze_chase": _ROOT / "animation/plugins/maze_chase/manifest.json",
            "pinball": _ROOT / "animation/plugins/pinball/manifest.json",
            "pixel_quest": _ROOT / "animation/plugins/pixel_quest/manifest.json",
            "circadian_window": _ROOT / "animation/plugins/circadian_window/manifest.json",
            "cloud_canyon": _ROOT / "animation/plugins/cloud_canyon/manifest.json",
            "desert_wind": _ROOT / "animation/plugins/desert_wind/manifest.json",
            "moonlit_fog_banks": _ROOT / "animation/plugins/moonlit_fog_banks/manifest.json",
            "rain_on_glass": _ROOT / "animation/plugins/rain_on_glass/manifest.json",
            "tidal_bioluminescence": _ROOT / "animation/plugins/tidal_bioluminescence/manifest.json",
            "waterfall_veil": _ROOT / "animation/plugins/waterfall_veil/manifest.json",
            "clock_overlay": _ROOT / "animation/plugins/clock_overlay/manifest.json",
            "emoji_arranger": _ROOT / "animation/plugins/emoji_arranger/manifest.json",
        }
        for component_id, location in locations.items():
            manifest = json.loads(location.read_text(encoding="utf-8"))
            declaration = manifest.get("component", manifest)
            descriptor = descriptors[component_id]
            self.assertEqual(declaration["provider"], descriptor.provider.value)
            self.assertEqual(declaration["role"], descriptor.role.value)
            self.assertEqual(declaration["timing"], descriptor.timing_policy.value)
            self.assertEqual(declaration["alpha_behavior"], descriptor.alpha_behavior.value)
            self.assertEqual(declaration["palette_policy"], descriptor.palette_policy.value)
            self.assertEqual(declaration["plant_capabilities"], [item.value for item in descriptor.plant_capabilities])
            self.assertEqual(declaration["fidelity_exceptions"], list(descriptor.fidelity_exceptions))

    def test_provider_stays_an_integrity_address_not_a_discovery_filter(self) -> None:
        catalog = current_component_catalog()

        self.assertFalse(hasattr(catalog, "discover"))
        self.assertIs(
            catalog.require(provider="python", component_id="conway_life", version=1),
            next(item for item in catalog.descriptors if item.component_id == "conway_life"),
        )
        with self.assertRaisesRegex(ValueError, "qualified catalog component"):
            catalog.require(provider="receiver_native", component_id="conway_life", version=1)

    def test_descriptor_defaults_are_deeply_frozen_and_consumers_receive_copies(self) -> None:
        authored_defaults = {"nested": {"seed_cells": [[1, 2]]}}
        descriptor = ComponentDescriptor(
            "copy_safe", 1, "python", "animation", "scaled_context",
            "opaque", "semantic", ("none",), (), defaults=authored_defaults,
        )
        authored_defaults["nested"]["seed_cells"][0][0] = 99
        self.assertEqual(descriptor.defaults["nested"]["seed_cells"], ((1, 2),))
        with self.assertRaises(TypeError):
            descriptor.defaults["nested"]["seed_cells"][0] = (3, 4)

        copied = descriptor.default_parameters()
        copied["nested"]["seed_cells"][0][0] = 7
        self.assertEqual(descriptor.defaults["nested"]["seed_cells"], ((1, 2),))

        conway = ConwayLifeAnimation.component_descriptor()
        copied_conway_defaults = conway.default_parameters()
        copied_conway_defaults["seed_cells"].append([3, 4])
        self.assertEqual(conway.defaults["seed_cells"], ())

    def test_aurora_keeps_effect_intent_out_of_legacy_modifier_discovery(self) -> None:
        aurora = AuroraCurtainsAnimation(PreviewLEDController(strips=2, leds_per_strip=2))

        self.assertFalse(AuroraCurtainsAnimation.PLANT_MODIFIER_SUPPORT)
        self.assertNotIn("effect_intent", aurora.get_info()["plant_modifier_support"])

    def test_tetris_is_a_qualified_semantic_opaque_animation(self) -> None:
        descriptor = TetrisAnimation.component_descriptor()

        self.assertEqual(descriptor.component_id, "tetris")
        self.assertEqual(descriptor.alpha_behavior.value, "opaque")
        self.assertEqual(descriptor.palette_policy.value, "semantic")
        self.assertEqual(tuple(capability.value for capability in descriptor.plant_capabilities), ("effect_intent",))

    def test_canopy_cup_is_a_qualified_semantic_opaque_animation(self) -> None:
        descriptor = CanopyCupAnimation.component_descriptor()
        self.assertEqual((descriptor.component_id, descriptor.role.value, descriptor.timing_policy.value), ("canopy_cup", "animation", "scaled_context"))
        self.assertEqual((descriptor.alpha_behavior.value, descriptor.palette_policy.value, tuple(capability.value for capability in descriptor.plant_capabilities)), ("opaque", "semantic", ("effect_intent",)))

    def test_firefly_meadow_is_a_qualified_semantic_opaque_animation(self) -> None:
        descriptor = FireflySynchronyAnimation.component_descriptor()
        self.assertEqual((descriptor.component_id, descriptor.role.value, descriptor.timing_policy.value), ("firefly_synchrony", "animation", "scaled_context"))
        self.assertEqual((descriptor.alpha_behavior.value, descriptor.palette_policy.value, tuple(capability.value for capability in descriptor.plant_capabilities)), ("opaque", "semantic", ("effect_intent",)))

    def test_fireworks_is_a_qualified_semantic_opaque_animation(self) -> None:
        descriptor = FireworksAnimation.component_descriptor()
        self.assertEqual((descriptor.component_id, descriptor.role.value, descriptor.timing_policy.value), ("fireworks", "animation", "scaled_context"))
        self.assertEqual((descriptor.alpha_behavior.value, descriptor.palette_policy.value, tuple(capability.value for capability in descriptor.plant_capabilities)), ("opaque", "semantic", ("effect_intent",)))

    def test_snake_is_a_qualified_semantic_opaque_animation(self) -> None:
        descriptor = SnakeAnimation.component_descriptor()
        self.assertEqual((descriptor.component_id, descriptor.role.value, descriptor.timing_policy.value), ("snake", "animation", "scaled_context"))
        self.assertEqual((descriptor.alpha_behavior.value, descriptor.palette_policy.value, tuple(capability.value for capability in descriptor.plant_capabilities)), ("opaque", "semantic", ("effect_intent",)))

    def test_cyclic_reef_is_a_qualified_semantic_opaque_animation(self) -> None:
        descriptor = CyclicReefAnimation.component_descriptor()
        self.assertEqual((descriptor.component_id, descriptor.role.value, descriptor.timing_policy.value), ("cyclic_reef", "animation", "scaled_context"))
        self.assertEqual((descriptor.alpha_behavior.value, descriptor.palette_policy.value, tuple(capability.value for capability in descriptor.plant_capabilities)), ("opaque", "semantic", ("effect_intent",)))

    def test_emoji_message_is_a_scaled_semantic_widget(self) -> None:
        descriptor = EmojiArrangerAnimation.component_descriptor()
        self.assertEqual((descriptor.component_id, descriptor.role.value, descriptor.timing_policy.value),
                         ("emoji_arranger", "widget", "scaled_context"))
