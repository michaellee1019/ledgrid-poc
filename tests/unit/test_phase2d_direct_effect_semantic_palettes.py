"""Phase 2D acceptance for direct-effect semantic palette ownership."""

from __future__ import annotations

import json
import math
import time
import unittest
from copy import deepcopy
from hashlib import sha256

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import (
    CANONICAL_VIBE_IDS,
    VIBE_PALETTE_ROLES,
    AnimationRuntimeContext,
    component_preset_fingerprint,
    resolve_vibe,
)
from animation.plugins.fireworks import FRAMEWORK_GRADE_RATIONALE

PLUGIN_IDS = (
    "fireworks",
    "flame_burst",
    "sparkle",
    "wave",
    "pixel_chase",
)
SEMANTIC_ROLES = {
    "flame_burst": {"background_low", "primary", "accent"},
    "sparkle": {"background_low", "primary", "accent"},
    "wave": {"background_low", "primary", "accent"},
    "pixel_chase": {"primary", "secondary", "accent"},
}
BASELINE_CONFIGS = {
    "fireworks": {
        "random_seed": 2408,
        "palette": "patriotic",
        "launch_rate": 1.4,
    },
    "flame_burst": {
        "visible_leds": 138,
        "color_saturation": 0.83,
        "color_value": 0.91,
    },
    "sparkle": {
        "base_red": 7,
        "base_green": 19,
        "base_blue": 31,
        "sparkle_red": 241,
        "sparkle_green": 173,
        "sparkle_blue": 67,
    },
    "wave": {
        "wave_red": 231,
        "wave_green": 47,
        "wave_blue": 181,
        "background_red": 3,
        "background_green": 9,
        "background_blue": 27,
        "axis": "diagonal",
    },
    "pixel_chase": {
        "color_mode": "rainbow",
        "color_cycle_speed": 0.37,
        "pixel_count": 5,
    },
}
NEUTRAL_SEQUENCE_DIGESTS = {
    "fireworks": "4e605b56bfa244abb8107a3ef771ffa566c4974daf7fb6a57baf8e211265a991",
    "flame_burst": "0f5878cbe564f1b0979d0ba4cf9dd4f5ad541d6d2d5cc7375073439936806fc2",
    "sparkle": "5cab0de4a38f9c9b1ebc0bb23046e1a5396163b6628a71e75c1ecf9307d7a061",
    "wave": "48fc95a48ad44433252484bd70cb26f371b712986916b07eb6a344cc7c8184bc",
    "pixel_chase": "207ccf0171f5ac30ba1f00068f50ed7a1a77d1b0fdff43eda743d830758c73d6",
}
SAMPLE_TIMES = (0.0, 0.041, 0.083, 0.167, 0.333, 0.667)
STRESS_CONFIGS = {
    "fireworks": {
        "random_seed": 313,
        "launch_rate": 5.0,
        "max_rockets": 10,
        "particles_per_burst": 160,
        "burst_size": 0.7,
        "spark_lifetime": 5.0,
        "secondary_spark_chance": 1.0,
        "trail_persistence": 0.995,
    },
    "flame_burst": {
        "visible_leds": 138,
        "speed": 5.0,
        "burst_rate": 3.0,
        "shell_thickness": 0.6,
        "flicker": 1.0,
        "afterglow": 1.0,
    },
    "sparkle": {
        "sparkle_probability": 0.1,
        "fade_speed": 0.99,
    },
    "wave": {
        "frequency": 12.0,
        "speed": 4.0,
        "amplitude": 1.0,
        "axis": "diagonal",
    },
    "pixel_chase": {
        "pixels_per_second": 1000.0,
        "pixel_count": 32,
        "color_mode": "rainbow",
        "color_cycle_speed": 4.0,
        "tail_style": "fade",
        "tail_length": 32,
    },
}


class Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


def pixels(rendered) -> np.ndarray:
    return rendered.pixels if isinstance(rendered, RenderedFrame) else rendered


def changed(rendered) -> bool:
    return rendered.changed if isinstance(rendered, RenderedFrame) else True


def context(
    vibe_id,
    elapsed,
    frame_index,
    *,
    palette_roles=None,
    effective_time_scale=1.0,
):
    resolved = resolve_vibe(vibe_id)
    profile = resolved.profile
    return AnimationRuntimeContext(
        wall_time=1_787_318_400.0 + elapsed,
        unscaled_elapsed=elapsed,
        scaled_elapsed=elapsed,
        frame_index=frame_index,
        scene_epoch=41,
        global_width=Controller.strip_count,
        height=Controller.leds_per_strip,
        local_strip_offset=0,
        local_width=Controller.strip_count,
        vibe_id=vibe_id,
        vibe_profile_version=profile.profile_version,
        resolved_profile_digest=profile.resolved_profile_digest,
        palette_roles=(
            profile.palette_roles if palette_roles is None else palette_roles
        ),
        capability_values=profile.capability_values,
        tempo_scale=1.0,
        luminance_scale=1.0,
        operator_tempo_scale=1.0,
        authored_speed=1.0,
        effective_time_scale=effective_time_scale,
        installation_profile_view={},
        plant_modifiers={},
    )


def rng_state_equal(left, right):
    return (
        left[0] == right[0]
        and np.array_equal(left[1], right[1])
        and left[2:] == right[2:]
    )


def logical_state(plugin_id, animation):
    if plugin_id == "fireworks":
        return (
            deepcopy(animation._rng.getstate()),
            tuple(vars(rocket).items() for rocket in animation._rockets),
            tuple(vars(spark).items() for spark in animation._sparks),
            animation._trail.tobytes(),
            animation._last_time,
            animation._launch_accumulator,
            animation._burst_count,
        )
    if plugin_id == "flame_burst":
        return (
            animation._last_time if hasattr(animation, "_last_time") else None,
            animation._plant_foliage_flash
            if hasattr(animation, "_plant_foliage_flash") else None,
        )
    if plugin_id == "sparkle":
        return (
            animation.sparkle_brightness.tobytes(),
            animation._emitted_last_frame,
            animation._emitted_total,
            animation._last_emitter_elapsed,
        )
    if plugin_id == "wave":
        return animation._phase.tobytes(), animation._blend.tobytes()
    if plugin_id == "pixel_chase":
        return (
            animation._path.tobytes(),
            animation._path_kind.tobytes(),
            animation._last_step,
            animation._last_head_pixels.tobytes(),
            animation._last_output_pixels.tobytes(),
            animation._last_output_pixel,
        )
    raise AssertionError(f"unhandled plugin {plugin_id}")


class DirectEffectSemanticPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
        cls.plugins = cls.loader.load_all_plugins()
        cls.controller = Controller()

    def test_manifests_bind_semantic_roles_or_document_full_spectrum_exception(self):
        self.assertEqual(set(self.plugins), set(PLUGIN_IDS))
        for plugin_id, expected_roles in SEMANTIC_ROLES.items():
            with self.subTest(plugin=plugin_id):
                vibe = self.loader.plugin_manifests[plugin_id]["vibe"]
                animation_class = self.plugins[plugin_id]
                self.assertEqual(vibe["color_policy"], "semantic")
                self.assertEqual(vibe["timing_adapter"], "legacy_speed_param")
                self.assertEqual(vibe["capabilities"], ["palette_roles"])
                self.assertSetEqual(set(vibe["semantic_roles"]), expected_roles)
                self.assertEqual(animation_class.VIBE_COLOR_POLICY, "semantic")
                self.assertEqual(
                    animation_class.VIBE_CAPABILITIES,
                    frozenset(("palette_roles",)),
                )

        fireworks = self.loader.plugin_manifests["fireworks"]["vibe"]
        self.assertEqual(fireworks["color_policy"], "grade")
        self.assertEqual(fireworks["capabilities"], [])
        self.assertIn("full-spectrum", FRAMEWORK_GRADE_RATIONALE)
        schema = self.plugins["fireworks"](
            self.controller
        ).get_parameter_schema()
        self.assertIn("palette", schema)
        self.assertIn("base_hue", schema)
        self.assertIn("hue_spread", schema)

    def test_neutral_output_keeps_pre_migration_authored_bytes(self):
        for plugin_id, animation_class in self.plugins.items():
            config = BASELINE_CONFIGS[plugin_id]
            if plugin_id == "sparkle":
                np.random.seed(20260821)
            direct = animation_class(self.controller, config)
            direct_frames = []
            for index, elapsed in enumerate(SAMPLE_TIMES):
                direct_frames.append(
                    pixels(direct.generate_frame(elapsed, index)).copy()
                )

            if plugin_id == "sparkle":
                np.random.seed(20260821)
            neutral = animation_class(self.controller, config)
            effective_speed = float(
                neutral.get_authored_parameter("speed", 1.0)
            )
            neutral_frames = []
            for index, elapsed in enumerate(SAMPLE_TIMES):
                neutral_frames.append(pixels(
                    neutral.generate_frame_with_context(
                        context(
                            "neutral",
                            elapsed,
                            index,
                            effective_time_scale=effective_speed,
                        )
                    )
                ).copy())

            with self.subTest(plugin=plugin_id):
                for expected, actual in zip(direct_frames, neutral_frames):
                    np.testing.assert_array_equal(actual, expected)
                digest = sha256(
                    b"".join(frame.tobytes() for frame in neutral_frames)
                ).hexdigest()
                self.assertEqual(
                    digest, NEUTRAL_SEQUENCE_DIGESTS[plugin_id]
                )

    def test_renderers_consume_every_and_only_declared_role(self):
        configs = {
            "flame_burst": {"visible_leds": 138},
            "sparkle": {"plant_aware": True, "sparkle_probability": 0.1},
            "wave": {"plant_aware": True},
            "pixel_chase": {
                "color_mode": "rainbow",
                "pixel_count": 3,
                "tail_style": "none",
            },
        }
        vivid_roles = dict(resolve_vibe("vivid").profile.palette_roles)
        for plugin_id, expected_roles in SEMANTIC_ROLES.items():
            animation_class = self.plugins[plugin_id]

            def render_digest(roles):
                if plugin_id == "sparkle":
                    np.random.seed(8241)
                animation = animation_class(
                    self.controller, configs[plugin_id]
                )
                digest = sha256()
                for index, elapsed in enumerate((0.0, 0.137, 0.421)):
                    rendered = animation.generate_frame_with_context(context(
                        "vivid", elapsed, index, palette_roles=roles
                    ))
                    digest.update(pixels(rendered).tobytes())
                return digest.digest()

            baseline = render_digest(vivid_roles)
            consumed = set()
            for role in VIBE_PALETTE_ROLES:
                mutated = dict(vivid_roles)
                mutated[role] = tuple(
                    255 - channel for channel in vivid_roles[role]
                )
                if render_digest(mutated) != baseline:
                    consumed.add(role)
            with self.subTest(plugin=plugin_id):
                self.assertSetEqual(consumed, expected_roles)

    def test_role_anchors_are_used_exactly_without_rewriting_authored_colors(self):
        vivid = context("vivid", 0.0, 0)
        roles = vivid.palette_roles

        flame = self.plugins["flame_burst"](self.controller)
        flame.set_presentation_context(vivid)
        flame_colors = np.empty((3, 3), dtype=np.uint8)
        flame._write_flame_colors(
            np.asarray((0.0, 0.5, 1.0), dtype=np.float32),
            1.0,
            1.0,
            flame_colors,
        )
        np.testing.assert_array_equal(flame_colors[0], roles["background_low"])
        np.testing.assert_array_equal(flame_colors[1], roles["primary"])
        np.testing.assert_array_equal(flame_colors[2], roles["accent"])

        sparkle = self.plugins["sparkle"](
            self.controller,
            {"base_red": 11, "sparkle_green": 17},
        )
        sparkle.set_presentation_context(vivid)
        base, highlight = sparkle._presentation_colors()
        np.testing.assert_array_equal(base, roles["background_low"])
        np.testing.assert_array_equal(highlight, roles["accent"])
        self.assertEqual(sparkle.authored_params_snapshot()["base_red"], 11)
        self.assertEqual(sparkle.authored_params_snapshot()["sparkle_green"], 17)

        wave = self.plugins["wave"](
            self.controller, {"wave_red": 19, "background_green": 23}
        )
        wave.set_presentation_context(vivid)
        np.testing.assert_array_equal(wave._color("background"), roles["background_low"])
        np.testing.assert_array_equal(wave._color("wave"), roles["primary"])
        self.assertEqual(wave.authored_params_snapshot()["wave_red"], 19)
        self.assertEqual(wave.authored_params_snapshot()["background_green"], 23)

        chase = self.plugins["pixel_chase"](
            self.controller,
            {"color_mode": "rainbow", "pixel_count": 3, "brightness": 1.0},
        )
        chase.set_presentation_context(vivid)
        for head, role in enumerate(("primary", "secondary", "accent")):
            self.assertEqual(
                chase._pixel_color(0, head, 0, 120.0), roles[role]
            )
        self.assertEqual(
            chase.authored_params_snapshot()["color_mode"], "rainbow"
        )

    def test_every_canonical_vibe_is_visibly_distinct_for_semantic_renderers(self):
        for plugin_id in SEMANTIC_ROLES:
            fingerprints = set()
            for vibe_id in CANONICAL_VIBE_IDS:
                if plugin_id == "sparkle":
                    np.random.seed(20269)
                animation = self.plugins[plugin_id](
                    self.controller, BASELINE_CONFIGS[plugin_id]
                )
                digest = sha256()
                for index, elapsed in enumerate(SAMPLE_TIMES):
                    rendered = animation.generate_frame_with_context(context(
                        vibe_id,
                        elapsed,
                        index,
                        effective_time_scale=float(
                            animation.get_authored_parameter("speed", 1.0)
                        ),
                    ))
                    digest.update(pixels(rendered).tobytes())
                fingerprints.add(digest.digest())
            with self.subTest(plugin=plugin_id):
                self.assertEqual(len(fingerprints), len(CANONICAL_VIBE_IDS))

    def test_palette_switch_preserves_rng_state_events_geometry_and_cadence(self):
        for plugin_id, animation_class in self.plugins.items():
            for vibe_id in CANONICAL_VIBE_IDS[1:]:
                config = BASELINE_CONFIGS[plugin_id]
                if plugin_id == "sparkle":
                    np.random.seed(19017)
                neutral = animation_class(self.controller, config)
                shifted = animation_class(self.controller, config)
                effective_speed = float(
                    neutral.get_authored_parameter("speed", 1.0)
                )
                neutral_changed = []
                shifted_changed = []
                neutral_frame = shifted_frame = None
                for index, elapsed in enumerate(SAMPLE_TIMES):
                    if plugin_id == "sparkle":
                        before = np.random.get_state()
                    neutral_frame = neutral.generate_frame_with_context(
                        context(
                            "neutral",
                            elapsed,
                            index,
                            effective_time_scale=effective_speed,
                        )
                    )
                    if plugin_id == "sparkle":
                        after = np.random.get_state()
                        np.random.set_state(before)
                    shifted_frame = shifted.generate_frame_with_context(
                        context(
                            vibe_id,
                            elapsed,
                            index,
                            effective_time_scale=effective_speed,
                        )
                    )
                    if plugin_id == "sparkle":
                        self.assertTrue(
                            rng_state_equal(after, np.random.get_state())
                        )
                        np.random.set_state(after)
                    neutral_changed.append(changed(neutral_frame))
                    shifted_changed.append(changed(shifted_frame))
                    with self.subTest(
                        plugin=plugin_id, vibe=vibe_id, elapsed=elapsed
                    ):
                        self.assertEqual(
                            logical_state(plugin_id, neutral),
                            logical_state(plugin_id, shifted),
                        )
                        self.assertEqual(
                            neutral.get_runtime_stats(),
                            shifted.get_runtime_stats(),
                        )
                self.assertEqual(neutral_changed, shifted_changed)
                if plugin_id in SEMANTIC_ROLES:
                    self.assertFalse(np.array_equal(
                        pixels(neutral_frame), pixels(shifted_frame)
                    ))
                else:
                    np.testing.assert_array_equal(
                        pixels(neutral_frame), pixels(shifted_frame)
                    )

    def test_pixel_chase_live_vibe_switch_invalidates_only_color_cache(self):
        chase = self.plugins["pixel_chase"](
            self.controller,
            {"color_mode": "rainbow", "pixel_count": 5},
        )
        authored = chase.authored_params_snapshot()
        quiet = chase.generate_frame_with_context(context("quiet", 0.333, 67))
        quiet_pixels = pixels(quiet).copy()
        cached = chase.generate_frame_with_context(context("quiet", 0.333, 68))
        self.assertFalse(cached.changed)
        before = logical_state("pixel_chase", chase)

        vivid = chase.generate_frame_with_context(context("vivid", 0.333, 69))
        after = logical_state("pixel_chase", chase)
        self.assertTrue(vivid.changed)
        self.assertFalse(np.array_equal(quiet_pixels, pixels(vivid)))
        self.assertEqual(before, after)
        self.assertEqual(chase.authored_params_snapshot(), authored)
        self.assertFalse(
            chase.generate_frame_with_context(
                context("vivid", 0.333, 70)
            ).changed
        )

    def test_all_presets_validate_and_render_across_all_canonical_vibes(self):
        expected_counts = {
            "fireworks": 5,
            "flame_burst": 5,
            "sparkle": 5,
            "wave": 6,
            "pixel_chase": 0,
        }
        for plugin_id, animation_class in self.plugins.items():
            paths = tuple(self.loader.iter_curated_preset_files(plugin_id))
            self.assertEqual(len(paths), expected_counts[plugin_id])
            for path in paths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                original = deepcopy(payload)
                validated = self.loader.validate_component_parameters(
                    plugin_id, payload["params"]
                )
                preset_fingerprint = component_preset_fingerprint(
                    plugin_id, payload["preset_id"], validated
                )
                for vibe_id in CANONICAL_VIBE_IDS:
                    if plugin_id == "sparkle":
                        np.random.seed(4139)
                    animation = animation_class(self.controller, validated)
                    authored_before = animation.authored_params_snapshot()
                    rendered = animation.generate_frame_with_context(
                        context(
                            vibe_id,
                            0.217,
                            13,
                            effective_time_scale=float(
                                animation.get_authored_parameter("speed", 1.0)
                            ),
                        )
                    )
                    frame = pixels(rendered)
                    with self.subTest(
                        plugin=plugin_id, preset=path.stem, vibe=vibe_id
                    ):
                        self.assertEqual(frame.shape, (Controller.total_leds, 3))
                        self.assertEqual(frame.dtype, np.uint8)
                        self.assertTrue(frame.flags.c_contiguous)
                        self.assertEqual(
                            animation.authored_params_snapshot(), authored_before
                        )
                        for name, value in validated.items():
                            self.assertEqual(authored_before[name], value)
                        selected = {
                            name: animation.authored_params_snapshot()[name]
                            for name in validated
                        }
                        self.assertEqual(
                            component_preset_fingerprint(
                                plugin_id, payload["preset_id"], selected
                            ),
                            preset_fingerprint,
                        )
                self.assertEqual(payload, original)

            if not paths:
                for vibe_id in CANONICAL_VIBE_IDS:
                    animation = animation_class(self.controller)
                    frame = pixels(animation.generate_frame_with_context(
                        context(vibe_id, 0.217, 13)
                    ))
                    self.assertEqual(frame.shape, (Controller.total_leds, 3))

    def test_default_and_stress_real_wall_p95_is_below_four_ms(self):
        warmup = 20
        samples = 100
        for plugin_id, animation_class in self.plugins.items():
            for scenario, config in (
                ("default", {}),
                ("stress", STRESS_CONFIGS[plugin_id]),
            ):
                if plugin_id == "sparkle":
                    np.random.seed(313)
                animation = animation_class(self.controller, config)
                effective_speed = float(
                    animation.get_authored_parameter("speed", 1.0)
                )
                timings = []
                for index in range(warmup + samples):
                    # A 60 Hz clock lets particle systems reach representative
                    # burst state while the 1 kpixel/s chase still changes.
                    runtime = context(
                        "vivid",
                        index / 60.0,
                        index,
                        effective_time_scale=effective_speed,
                    )
                    started = time.perf_counter_ns()
                    rendered = animation.generate_frame_with_context(runtime)
                    duration = (
                        time.perf_counter_ns() - started
                    ) / 1_000_000.0
                    if index >= warmup:
                        timings.append(duration)
                    self.assertEqual(
                        pixels(rendered).shape, (Controller.total_leds, 3)
                    )
                p95 = sorted(timings)[math.ceil(samples * 0.95) - 1]
                with self.subTest(
                    plugin=plugin_id,
                    scenario=scenario,
                    p95_ms=round(p95, 3),
                    max_ms=round(max(timings), 3),
                ):
                    self.assertLess(p95, 4.0)


if __name__ == "__main__":
    unittest.main()
