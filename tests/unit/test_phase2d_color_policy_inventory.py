"""Phase 2D acceptance for explicit shipped-component color policies."""

from __future__ import annotations

import random
import unittest
from copy import deepcopy

import numpy as np

from animation.core.component_catalog import color_policy_inventory
from animation.core.manager import AnimationManager
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import AnimationRuntimeContext, resolve_vibe
from animation.core.receiver_static_component import (
    receiver_static_component_descriptor,
)

SEMANTIC_IDS = frozenset((
    "ascii_drop",
    "aurora_curtains",
    "cellular_tapestry",
    "clock",
    "clock_overlay",
    "cloud_canyon",
    "circadian_window",
    "cyclic_reef",
    "desert_wind",
    "firefly_synchrony",
    "flame_burst",
    "flow_field_silk",
    "fluid_tank",
    "frostwork",
    "lava_lamp",
    "living_stained_glass",
    "moonlit_fog_banks",
    "night_train_windows",
    "physarum_network",
    "pixel_chase",
    "plant_glow",
    "quasicrystal_bloom",
    "rain_on_glass",
    "reaction_diffusion_garden",
    "snake",
    "sparkle",
    "spiral_single",
    "tidal_bioluminescence",
    "waterfall_veil",
    "wave",
    "wind_in_the_reeds",
))
PRESERVE_IDS = frozenset((
    "emoji",
    "emoji_arranger",
    "gif_animation",
    "gradient",
    "plant_calibration",
    "plant_mask_highlight",
    "simple_test",
    "solid",
    "strip_order",
    "world_flags",
))
VIBE_ENABLED_IDS = frozenset((
    "ascii_drop",
    "aurora_curtains",
    "cellular_tapestry",
    "clock",
    "clock_overlay",
    "cloud_canyon",
    "circadian_window",
    "cyclic_reef",
    "desert_wind",
    "firefly_synchrony",
    "flame_burst",
    "flow_field_silk",
    "fluid_tank",
    "frostwork",
    "lava_lamp",
    "living_stained_glass",
    "moonlit_fog_banks",
    "night_train_windows",
    "physarum_network",
    "pixel_chase",
    "plant_glow",
    "quasicrystal_bloom",
    "rain_on_glass",
    "reaction_diffusion_garden",
    "simple_test",
    "snake",
    "sparkle",
    "spiral_single",
    "tidal_bioluminescence",
    "waterfall_veil",
    "wave",
    "wind_in_the_reeds",
))


class Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


def pixels(rendered) -> np.ndarray:
    return rendered.pixels if hasattr(rendered, "pixels") else rendered


def runtime_context(vibe_id: str, authored_speed: float) -> AnimationRuntimeContext:
    resolved = resolve_vibe(vibe_id)
    return AnimationRuntimeContext(
        wall_time=1_776_032_262.0,
        unscaled_elapsed=0.0,
        scaled_elapsed=0.0,
        frame_index=0,
        scene_epoch=23,
        global_width=Controller.strip_count,
        height=Controller.leds_per_strip,
        local_strip_offset=0,
        local_width=Controller.strip_count,
        vibe_id=resolved.state.vibe_id,
        vibe_profile_version=resolved.state.profile_version,
        resolved_profile_digest=resolved.state.resolved_profile_digest,
        palette_roles=resolved.profile.palette_roles,
        capability_values=resolved.profile.capability_values,
        tempo_scale=1.0,
        luminance_scale=1.0,
        operator_tempo_scale=1.0,
        authored_speed=authored_speed,
        effective_time_scale=authored_speed,
        installation_profile_view={},
        plant_modifiers={},
    )


def synchronize_rngs(source, target) -> None:
    """Align runtime RNGs that do not accept a constructor seed (notably Tetris)."""
    for name, value in vars(source).items():
        other = getattr(target, name, None)
        if isinstance(value, random.Random) and isinstance(other, random.Random):
            other.setstate(value.getstate())
        elif isinstance(value, np.random.Generator) and isinstance(
            other, np.random.Generator
        ):
            other.bit_generator.state = deepcopy(value.bit_generator.state)


class Phase2DColorPolicyInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = AnimationPluginLoader()
        cls.plugin_ids = tuple(cls.loader.scan_plugins())
        cls.scanned_descriptors = {
            plugin_id: cls.loader.get_component_descriptor(plugin_id)
            for plugin_id in cls.plugin_ids
        }
        cls.loader.load_all_plugins()

    def test_generated_inventory_covers_every_selectable_component(self):
        receiver_static = receiver_static_component_descriptor({
            "receiver_local_background": True,
            "receiver_sparse_overlay": True,
        })
        self.assertIsNotNone(receiver_static)
        inventory = color_policy_inventory([
            *self.loader.component_catalog(),
            receiver_static,
        ])

        # Python packages plus catalog-only Painter, the repository-native
        # pilot, and the separately supplied compiled receiver builtin.
        self.assertEqual(inventory["component_count"], len(self.plugin_ids) + 3)
        self.assertEqual(
            inventory["counts"],
            {"grade": 10, "preserve": 12, "semantic": 32},
        )
        identities = {
            (item["provider"], item["plugin_id"])
            for item in inventory["components"]
        }
        self.assertEqual(len(identities), inventory["component_count"])
        self.assertIn(("python", "painter"), identities)
        self.assertIn(("receiver_native", "aurora_curtains_native"), identities)
        self.assertIn(("receiver_native", "compiled_rainbow"), identities)
        self.assertTrue(all(item["color_policy"] for item in inventory["components"]))

    def test_manifests_and_descriptors_expose_the_authored_classification(self):
        expected = {
            plugin_id: (
                "semantic"
                if plugin_id in SEMANTIC_IDS
                else "preserve"
                if plugin_id in PRESERVE_IDS
                else "grade"
            )
            for plugin_id in self.plugin_ids
        }
        self.assertEqual(len(expected), 51)

        for plugin_id, policy in expected.items():
            with self.subTest(plugin=plugin_id):
                manifest = self.loader.plugin_manifests[plugin_id]
                self.assertIn("vibe", manifest)
                self.assertEqual(manifest["vibe"]["color_policy"], policy)
                self.assertEqual(
                    self.scanned_descriptors[plugin_id]["vibe_color_policy"], policy
                )
                descriptor = self.loader.get_component_descriptor(plugin_id)
                self.assertEqual(descriptor["vibe_color_policy"], policy)
                animation_class = self.loader.get_plugin(plugin_id)
                self.assertEqual(animation_class.VIBE_COLOR_POLICY, policy)
                self.assertEqual(
                    descriptor["vibe_capabilities"],
                    manifest["vibe"]["capabilities"],
                )

        painter = self.loader.get_component_descriptor("painter")
        self.assertEqual(painter["vibe_color_policy"], "preserve")
        self.assertEqual(painter["vibe_capabilities"], [])

    def test_classification_only_manifests_cannot_activate_vibe_behavior(self):
        classification_only = set(self.plugin_ids).difference(VIBE_ENABLED_IDS)
        self.assertEqual(len(classification_only), 19)
        for plugin_id in sorted(classification_only):
            with self.subTest(plugin=plugin_id):
                vibe = self.loader.plugin_manifests[plugin_id]["vibe"]
                self.assertEqual(vibe["timing_adapter"], "legacy_speed_param")
                self.assertEqual(vibe["capabilities"], [])
                self.assertNotIn("semantic_roles", vibe)
                self.assertNotIn("legacy_parameter_mappings", vibe)
                animation_class = self.loader.get_plugin(plugin_id)
                self.assertEqual(animation_class.VIBE_CAPABILITIES, frozenset())
                self.assertEqual(animation_class.VIBE_PARAMETER_MAPPINGS, {})

    def test_policy_capability_invariants_fail_closed(self):
        catalog = self.loader.component_catalog()
        semantic = next(
            item for item in catalog if item["plugin_id"] == "clock_overlay"
        )
        preserve = next(
            item for item in catalog if item["plugin_id"] == "world_flags"
        )

        missing_roles = deepcopy(semantic)
        missing_roles["vibe_capabilities"] = ["luminance"]
        with self.assertRaisesRegex(ValueError, "must claim palette_roles"):
            color_policy_inventory([missing_roles])

        recolored_preserve = deepcopy(preserve)
        recolored_preserve["vibe_capabilities"] = ["palette_roles"]
        with self.assertRaisesRegex(ValueError, "cannot claim palette_roles"):
            color_policy_inventory([recolored_preserve])

        missing_policy = deepcopy(preserve)
        missing_policy.pop("vibe_color_policy")
        with self.assertRaisesRegex(ValueError, "lacks an explicit"):
            color_policy_inventory([missing_policy])

    def test_every_classification_only_component_is_byte_exact_under_vivid(self):
        seed_config = {
            "seed": 7391,
            "random_seed": 7391,
            "background_seed": 7391,
        }
        profile = resolve_vibe("vivid").profile
        classification_only = set(self.plugin_ids).difference(VIBE_ENABLED_IDS)
        for plugin_id in sorted(classification_only):
            with self.subTest(plugin=plugin_id):
                animation_class = self.loader.get_plugin(plugin_id)
                baseline = animation_class(Controller(), seed_config)
                vivid = animation_class(Controller(), seed_config)
                synchronize_rngs(baseline, vivid)
                authored_speed = float(
                    vivid.get_authored_parameter("speed", 1.0)
                )
                baseline_params = baseline.authored_params_snapshot()
                vivid_params = vivid.authored_params_snapshot()
                python_random_state = random.getstate()
                numpy_random_state = np.random.get_state()
                try:
                    random.seed(7391)
                    np.random.seed(7391)
                    baseline_frame = pixels(baseline.generate_frame(0.0, 0)).copy()
                    random.seed(7391)
                    np.random.seed(7391)
                    vivid_frame = pixels(vivid.generate_frame_with_context(
                        runtime_context("vivid", authored_speed)
                    )).copy()
                    np.testing.assert_array_equal(vivid_frame, baseline_frame)
                    presented, _ = AnimationManager._apply_vibe_presentation(
                        vivid,
                        vivid_frame,
                        profile=profile,
                        changed=True,
                        state=AnimationManager._empty_presentation_state(),
                    )
                    np.testing.assert_array_equal(presented, baseline_frame)
                    self.assertEqual(baseline.authored_params_snapshot(), baseline_params)
                    self.assertEqual(vivid.authored_params_snapshot(), vivid_params)
                finally:
                    random.setstate(python_random_state)
                    np.random.set_state(numpy_random_state)
                    baseline.cleanup()
                    vivid.cleanup()


if __name__ == "__main__":
    unittest.main()
