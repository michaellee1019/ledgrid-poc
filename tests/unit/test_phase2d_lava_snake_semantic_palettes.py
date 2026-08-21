"""Focused Phase 2D semantic-palette acceptance for Lava Lamp and Snake."""

from __future__ import annotations

import hashlib
import json
import math
import time
import unittest
from copy import deepcopy

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.manager import AnimationManager
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import (
    CANONICAL_VIBE_IDS,
    AnimationRuntimeContext,
    component_preset_fingerprint,
    resolve_vibe,
)
from animation.plugins.lava_lamp import (
    PALETTES,
    LavaLampAnimation,
)
from animation.plugins.lava_lamp import (
    SEMANTIC_PALETTE_ROLES as LAVA_ROLES,
)
from animation.plugins.snake import (
    SEMANTIC_PALETTE_ROLES as SNAKE_ROLES,
)
from animation.plugins.snake import (
    SnakeAnimation,
)

PLUGIN_IDS = ("lava_lamp", "snake")
NEUTRAL_SEQUENCE_DIGESTS = {
    "lava_lamp": "f64cbfe72776292b725e3d44cdd0c6d720cd677a7c2e93b86c181565d9e69e73",
    "snake": "53d11353e1f1ce46d518d11bd60643c52cae7bdb5cc20578703735e3d82b24b3",
}
NEUTRAL_CONFIGS = {
    "lava_lamp": {"seed": 2468, "palette": "ocean"},
    "snake": {"seed": 2468, "visual_style": "ice", "snake_count": 3},
}
NEUTRAL_TIMES = (0.0, 0.01, 0.025, 0.05, 0.1, 0.17)


class Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


def pixels(rendered) -> np.ndarray:
    return rendered.pixels if isinstance(rendered, RenderedFrame) else rendered


def runtime_context(
    vibe_id: str,
    *,
    unscaled: float,
    scaled: float,
    frame_index: int,
) -> AnimationRuntimeContext:
    resolved = resolve_vibe(vibe_id)
    return AnimationRuntimeContext(
        wall_time=1_776_032_262.0 + unscaled,
        unscaled_elapsed=unscaled,
        scaled_elapsed=scaled,
        frame_index=frame_index,
        scene_epoch=37,
        global_width=32,
        height=138,
        local_strip_offset=0,
        local_width=32,
        vibe_id=vibe_id,
        vibe_profile_version=resolved.state.profile_version,
        resolved_profile_digest=resolved.state.resolved_profile_digest,
        palette_roles=resolved.profile.palette_roles,
        capability_values=resolved.profile.capability_values,
        tempo_scale=resolved.profile.tempo_scale,
        luminance_scale=resolved.profile.luminance_scale,
        operator_tempo_scale=1.0,
        authored_speed=1.0,
        effective_time_scale=1.0,
        installation_profile_view={},
        plant_modifiers={},
    )


def lava_state(animation: LavaLampAnimation) -> tuple:
    return (
        json.dumps(
            animation.authored_params_snapshot(), sort_keys=True, separators=(",", ":")
        ),
        animation.x.tobytes(),
        animation.y.tobytes(),
        animation.vx.tobytes(),
        animation.vy.tobytes(),
        animation.radius.tobytes(),
        animation.temperature.tobytes(),
        animation.cooldown.tobytes(),
        animation.phase.tobytes(),
        animation.active.tobytes(),
        animation.previous_x.tobytes(),
        animation.previous_y.tobytes(),
        animation.previous_radius.tobytes(),
        animation.simulation_time,
        animation._accumulator,
        animation._steps,
        animation._dropped_steps,
        animation._midline_up,
        animation._midline_down,
        animation._splits,
        animation._merges,
        animation._interactions_applied,
        animation._plant_contacts,
        animation._portal_transfers,
        animation._hazard_recycles,
        animation._emissions,
        deepcopy(animation.rng.bit_generator.state),
    )


def snake_state(animation: SnakeAnimation) -> tuple:
    return (
        json.dumps(
            animation.authored_params_snapshot(), sort_keys=True, separators=(",", ":")
        ),
        tuple(
            (
                tuple(snake.body),
                snake.direction,
                snake.target_length,
                snake.hue_offset,
                snake.score,
                snake.respawn_ticks,
                snake.portal_exit_region,
                snake.portal_cooldown_ticks,
            )
            for snake in animation.snakes
        ),
        tuple(sorted(animation.food)),
        tuple(sorted(animation.walls)),
        tuple(sorted(animation.portals.items())),
        animation._trail.tobytes(),
        animation._trail_hue.tobytes(),
        animation.moves,
        animation.food_eaten,
        animation.deaths,
        animation.plant_contacts,
        animation.plant_teleports,
        animation.plant_hazard_deaths,
        animation._step_accumulator,
        animation.random.getstate(),
    )


class LavaSnakeSemanticPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
        cls.plugins = cls.loader.load_all_plugins()
        cls.controller = Controller()

    def test_manifests_bind_exact_direct_semantic_contracts(self) -> None:
        expected_roles = {
            "lava_lamp": sorted(LAVA_ROLES),
            "snake": sorted(SNAKE_ROLES),
        }
        for plugin_id, animation_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                vibe = self.loader.plugin_manifests[plugin_id]["vibe"]
                self.assertEqual(vibe["color_policy"], "semantic")
                self.assertEqual(vibe["timing_adapter"], "scaled_context")
                self.assertEqual(
                    vibe["capabilities"], ["luminance", "palette_roles", "tempo"]
                )
                self.assertEqual(vibe["semantic_roles"], expected_roles[plugin_id])
                self.assertNotIn("legacy_parameter_mappings", vibe)
                self.assertEqual(animation_class.VIBE_COLOR_POLICY, "semantic")
                self.assertEqual(
                    animation_class.VIBE_CAPABILITIES,
                    frozenset(("luminance", "palette_roles", "tempo")),
                )
                self.assertEqual(animation_class.VIBE_PARAMETER_MAPPINGS, {})

    def test_neutral_frames_keep_historical_nondefault_authored_bytes(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            direct = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
            neutral = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
            direct_bytes = []
            neutral_bytes = []
            for index, elapsed in enumerate(NEUTRAL_TIMES):
                expected = pixels(direct.generate_frame(elapsed, index)).copy()
                actual = pixels(neutral.generate_frame_with_context(runtime_context(
                    "neutral", unscaled=elapsed, scaled=elapsed, frame_index=index
                ))).copy()
                with self.subTest(plugin=plugin_id, elapsed=elapsed):
                    np.testing.assert_array_equal(actual, expected)
                direct_bytes.append(expected.tobytes())
                neutral_bytes.append(actual.tobytes())
            digest = hashlib.sha256(b"".join(neutral_bytes)).hexdigest()
            self.assertEqual(direct_bytes, neutral_bytes)
            self.assertEqual(digest, NEUTRAL_SEQUENCE_DIGESTS[plugin_id])

    def test_renderers_consume_declared_roles_directly(self) -> None:
        vivid = runtime_context("vivid", unscaled=0.0, scaled=0.0, frame_index=0)

        lava = self.plugins["lava_lamp"](self.controller, {"palette": "ocean"})
        lava.set_presentation_context(vivid)
        self.assertEqual(
            tuple(tuple(color) for color in lava._presentation_palette()),
            tuple(vivid.palette_roles[role] for role in LAVA_ROLES),
        )
        self.assertEqual(lava.authored_params_snapshot()["palette"], "ocean")

        snake = self.plugins["snake"](self.controller, {"visual_style": "ice"})
        snake.set_presentation_context(vivid)
        self.assertTupleEqual(tuple(snake._palette[0]), vivid.palette_roles["secondary"])
        self.assertTupleEqual(tuple(snake._palette[127]), vivid.palette_roles["primary"])
        self.assertTupleEqual(tuple(snake._palette[128]), vivid.palette_roles["primary"])
        self.assertTupleEqual(tuple(snake._palette[255]), vivid.palette_roles["accent"])
        self.assertEqual(snake.authored_params_snapshot()["visual_style"], "ice")

    def test_every_canonical_vibe_has_a_distinct_frame(self) -> None:
        cases = (
            (self.plugins["lava_lamp"], {"seed": 771, "palette": "violet"}),
            (self.plugins["snake"], {"seed": 771, "visual_style": "prism"}),
        )
        for animation_class, config in cases:
            fingerprints = set()
            for vibe_id in CANONICAL_VIBE_IDS:
                animation = animation_class(self.controller, config)
                rendered = None
                for index, elapsed in enumerate((0.0, 0.04, 0.09, 0.17)):
                    rendered = animation.generate_frame_with_context(runtime_context(
                        vibe_id,
                        unscaled=elapsed,
                        scaled=elapsed,
                        frame_index=index,
                    ))
                assert rendered is not None
                fingerprints.add(hashlib.sha256(pixels(rendered).tobytes()).digest())
            with self.subTest(animation=animation_class.__name__):
                self.assertEqual(len(fingerprints), len(CANONICAL_VIBE_IDS))

    def test_palette_only_vibe_changes_preserve_state_rng_events_and_cadence(self) -> None:
        cases = (
            (self.plugins["lava_lamp"], {"seed": 9917}, lava_state),
            (self.plugins["snake"], {"seed": 9917}, snake_state),
        )
        for animation_class, config, snapshot in cases:
            neutral = animation_class(self.controller, config)
            vivid = animation_class(self.controller, config)
            changed_neutral = []
            changed_vivid = []
            neutral_frame = vivid_frame = None
            for index, elapsed in enumerate((0.0, 0.005, 0.01, 0.025, 0.05, 0.1, 0.17)):
                neutral_frame = neutral.generate_frame_with_context(runtime_context(
                    "neutral", unscaled=elapsed, scaled=elapsed, frame_index=index
                ))
                vivid_frame = vivid.generate_frame_with_context(runtime_context(
                    "vivid", unscaled=elapsed, scaled=elapsed, frame_index=index
                ))
                changed_neutral.append(neutral_frame.changed)
                changed_vivid.append(vivid_frame.changed)
                with self.subTest(animation=animation_class.__name__, elapsed=elapsed):
                    self.assertEqual(snapshot(neutral), snapshot(vivid))
            assert neutral_frame is not None and vivid_frame is not None
            self.assertEqual(changed_neutral, changed_vivid)
            self.assertFalse(np.array_equal(pixels(neutral_frame), pixels(vivid_frame)))

    def test_live_vibe_switch_invalidates_only_the_presentation_cache(self) -> None:
        cases = (
            (self.plugins["lava_lamp"], {"seed": 383}, lava_state),
            (self.plugins["snake"], {"seed": 383}, snake_state),
        )
        for animation_class, config, snapshot in cases:
            animation = animation_class(self.controller, config)
            rendered = None
            for index, elapsed in enumerate((0.0, 0.04, 0.09, 0.17)):
                rendered = animation.generate_frame_with_context(runtime_context(
                    "quiet", unscaled=elapsed, scaled=elapsed, frame_index=index
                ))
            assert rendered is not None
            before_frame = pixels(rendered).copy()
            before_state = snapshot(animation)
            refreshed = animation.generate_frame_with_context(runtime_context(
                "celebration", unscaled=0.17, scaled=0.17, frame_index=5
            ))
            with self.subTest(animation=animation_class.__name__):
                self.assertTrue(refreshed.changed)
                self.assertEqual(snapshot(animation), before_state)
                self.assertFalse(np.array_equal(pixels(refreshed), before_frame))

    def test_framework_adds_only_declared_luminance_after_semantic_color(self) -> None:
        quiet = resolve_vibe("quiet").profile
        for animation_class in (
            self.plugins["lava_lamp"],
            self.plugins["snake"],
        ):
            animation = animation_class(self.controller, {"seed": 827})
            source = pixels(animation.generate_frame_with_context(runtime_context(
                "quiet", unscaled=0.17, scaled=0.17, frame_index=3
            ))).copy()
            presented, changed = AnimationManager._apply_vibe_presentation(
                animation,
                source,
                profile=quiet,
                changed=True,
                state=AnimationManager._empty_presentation_state(),
            )
            expected = np.rint(source.astype(np.float32) * quiet.luminance_scale)
            with self.subTest(animation=animation_class.__name__):
                self.assertTrue(changed)
                np.testing.assert_array_equal(presented, expected.astype(np.uint8))

    def test_all_curated_presets_validate_render_distinctly_and_keep_identity(self) -> None:
        expected_counts = {"lava_lamp": 18, "snake": 10}
        for plugin_id, animation_class in self.plugins.items():
            paths = tuple(self.loader.iter_curated_preset_files(plugin_id))
            self.assertEqual(len(paths), expected_counts[plugin_id])
            neutral_fingerprints = set()
            vivid_fingerprints = set()
            for path in paths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                original = deepcopy(payload)
                identity = component_preset_fingerprint(
                    plugin_id, payload["preset_id"], payload["params"]
                )
                validated = self.loader.validate_component_parameters(
                    plugin_id, payload["params"]
                )
                neutral = animation_class(self.controller, validated)
                vivid = animation_class(self.controller, validated)
                neutral_frame = vivid_frame = None
                for index, elapsed in enumerate((0.0, 0.05, 0.1, 0.17)):
                    neutral_frame = neutral.generate_frame_with_context(runtime_context(
                        "neutral", unscaled=elapsed, scaled=elapsed, frame_index=index
                    ))
                    vivid_frame = vivid.generate_frame_with_context(runtime_context(
                        "vivid", unscaled=elapsed, scaled=elapsed, frame_index=index
                    ))
                assert neutral_frame is not None and vivid_frame is not None
                neutral_fingerprints.add(
                    hashlib.sha256(pixels(neutral_frame).tobytes()).digest()
                )
                vivid_fingerprints.add(
                    hashlib.sha256(pixels(vivid_frame).tobytes()).digest()
                )
                with self.subTest(plugin=plugin_id, preset=path.stem):
                    self.assertEqual(payload, original)
                    self.assertEqual(payload["preset_id"], path.stem)
                    self.assertEqual(
                        component_preset_fingerprint(
                            plugin_id, payload["preset_id"], payload["params"]
                        ),
                        identity,
                    )
                    self.assertFalse(
                        np.array_equal(pixels(neutral_frame), pixels(vivid_frame))
                    )
            with self.subTest(plugin=plugin_id):
                self.assertEqual(len(neutral_fingerprints), len(paths))
                self.assertEqual(len(vivid_fingerprints), len(paths))

        lava_schema = self.plugins["lava_lamp"](self.controller).get_parameter_schema()
        snake_schema = self.plugins["snake"](self.controller).get_parameter_schema()
        self.assertEqual(lava_schema["palette"]["options"], list(PALETTES))
        self.assertEqual(
            snake_schema["visual_style"]["options"], list(SnakeAnimation.STYLES)
        )

    def test_default_and_stress_full_wall_performance_and_changed_ratio(self) -> None:
        cases = (
            (
                "lava_lamp",
                self.plugins["lava_lamp"],
                {"seed": 313},
                {
                    "seed": 313,
                    "speed": 4.0,
                    "blob_count": 12,
                    "blob_scale": 1.8,
                    "viscosity": 0.0,
                    "heat": 1.0,
                    "turbulence": 1.0,
                    "glow": 1.0,
                    "background": "ember",
                },
            ),
            (
                "snake",
                self.plugins["snake"],
                {"seed": 313},
                {
                    "seed": 313,
                    "speed": 4.0,
                    "render_fps": 90.0,
                    "moves_per_second": 30.0,
                    "snake_count": 12,
                    "initial_length": 30,
                    "max_length": 800,
                    "food_count": 30,
                    "ruleset": "battle",
                    "wall_pattern": "zigzag",
                    "visual_style": "prism",
                    "background": "aurora",
                    "trail_strength": 1.0,
                    "glow": 1.0,
                },
            ),
        )
        for plugin_id, animation_class, default, stress in cases:
            for profile, config in (("default", default), ("stress", stress)):
                animation = animation_class(self.controller, config)
                authored_speed = float(animation.get_authored_parameter("speed", 1.0))
                timings = []
                changed = 0
                warmup = 20
                frames = 120
                for index in range(warmup + frames):
                    context = runtime_context(
                        "vivid",
                        unscaled=index / 200.0,
                        scaled=index / 200.0 * authored_speed,
                        frame_index=index,
                    )
                    started = time.perf_counter_ns()
                    rendered = animation.generate_frame_with_context(context)
                    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
                    if index >= warmup:
                        timings.append(elapsed_ms)
                        changed += int(rendered.changed)
                ordered = sorted(timings)
                p95 = ordered[math.ceil(0.95 * frames) - 1]
                ratio = changed / frames
                with self.subTest(plugin=plugin_id, profile=profile):
                    self.assertLess(p95, 4.0)
                    self.assertLess(max(timings), 25.0)
                    if plugin_id == "lava_lamp":
                        self.assertAlmostEqual(ratio, 0.5, delta=0.02)
                    elif profile == "default":
                        self.assertGreaterEqual(ratio, 0.25)
                        self.assertLessEqual(ratio, 0.35)
                    else:
                        self.assertGreaterEqual(ratio, 0.30)
                        self.assertLessEqual(ratio, 0.36)


if __name__ == "__main__":
    unittest.main()
