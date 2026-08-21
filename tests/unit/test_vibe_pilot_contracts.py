"""Adversarial acceptance for the four Phase 2A Python vibe pilots."""

from __future__ import annotations

import json
import tempfile
import unittest
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import AnimationRuntimeContext, TimingAdapter
from animation.plugins.clock import ClockAnimation
from animation.plugins.lava_lamp import LavaLampAnimation
from animation.plugins.simple_test import SimpleTestAnimation
from animation.plugins.snake import SnakeAnimation

PILOT_IDS = ("clock", "lava_lamp", "snake", "simple_test")
ROLES = {
    "background_low": (1, 2, 4),
    "background_mid": (7, 11, 19),
    "background_high": (20, 35, 54),
    "primary": (30, 180, 110),
    "secondary": (160, 80, 210),
    "accent": (249, 83, 19),
    "hud": (238, 252, 222),
    "warning": (255, 22, 8),
}


class Controller:
    strip_count = 12
    leds_per_strip = 24
    total_leds = strip_count * leds_per_strip
    debug = False


class FixedClock(ClockAnimation):
    FIXED_NOW = datetime(2026, 8, 12, 18, 37, 42, tzinfo=timezone.utc)

    def _clock_now(self):
        return self.FIXED_NOW


def context(
    *,
    vibe_id: str,
    unscaled: float,
    scaled: float,
    frame: int = 0,
    roles=ROLES,
) -> AnimationRuntimeContext:
    return AnimationRuntimeContext(
        wall_time=1_776_032_262.0,
        unscaled_elapsed=unscaled,
        scaled_elapsed=scaled,
        frame_index=frame,
        scene_epoch=19,
        global_width=Controller.strip_count,
        height=Controller.leds_per_strip,
        local_strip_offset=0,
        local_width=Controller.strip_count,
        vibe_id=vibe_id,
        vibe_profile_version=1,
        palette_roles=roles,
        capability_values={},
        installation_profile_view={},
        plant_modifiers={},
    )


def pixels(rendered) -> np.ndarray:
    return rendered.pixels if hasattr(rendered, "pixels") else rendered


def rng_state(animation):
    if hasattr(animation, "rng"):
        return deepcopy(animation.rng.bit_generator.state)
    if hasattr(animation, "random"):
        return animation.random.getstate()
    return None


def snake_logical_state(animation: SnakeAnimation):
    return (
        tuple(
            (
                tuple(snake.body), snake.direction, snake.target_length,
                snake.score, snake.respawn_ticks, snake.portal_exit_region,
                snake.portal_cooldown_ticks,
            )
            for snake in animation.snakes
        ),
        tuple(sorted(animation.food)),
        tuple(sorted(animation.walls)),
        tuple(sorted(animation.portals.items())),
        animation.moves,
        animation.food_eaten,
        animation.deaths,
        animation.plant_contacts,
        animation.plant_teleports,
        animation.plant_hazard_deaths,
        animation._step_accumulator,
    )


def lava_logical_state(animation: LavaLampAnimation):
    return (
        animation.x.tobytes(), animation.y.tobytes(),
        animation.vx.tobytes(), animation.vy.tobytes(),
        animation.radius.tobytes(), animation.temperature.tobytes(),
        animation.cooldown.tobytes(), animation.active.tobytes(),
        animation.simulation_time, animation._accumulator, animation._steps,
        animation._dropped_steps,
        animation._midline_up, animation._midline_down,
        animation._splits, animation._merges,
        animation._interactions_applied, animation._plant_contacts,
        animation._portal_transfers, animation._hazard_recycles,
        animation._emissions,
    )


class VibePilotContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = AnimationPluginLoader()
        cls.loader.load_all_plugins()

    def test_pilot_manifests_are_strict_normalized_and_bound_to_classes(self):
        expected = {
            "clock": ("semantic", TimingAdapter.WALL_CLOCK,
                      {"palette_roles", "luminance"}),
            "lava_lamp": ("semantic", TimingAdapter.SCALED_CONTEXT,
                          {"palette_roles", "tempo", "luminance"}),
            "snake": ("semantic", TimingAdapter.SCALED_CONTEXT,
                      {"palette_roles", "tempo", "luminance"}),
            "simple_test": ("preserve", TimingAdapter.WALL_CLOCK,
                            {"luminance"}),
        }
        for plugin_id in PILOT_IDS:
            with self.subTest(plugin=plugin_id):
                vibe = self.loader.plugin_manifests[plugin_id]["vibe"]
                animation_class = self.loader.get_plugin(plugin_id)
                policy, timing, capabilities = expected[plugin_id]
                self.assertEqual(vibe["color_policy"], policy)
                self.assertEqual(TimingAdapter(vibe["timing_adapter"]), timing)
                self.assertSetEqual(set(vibe["capabilities"]), capabilities)
                self.assertIs(animation_class.TIMING_ADAPTER, timing)
                self.assertEqual(animation_class.VIBE_COLOR_POLICY, policy)
                self.assertSetEqual(set(animation_class.VIBE_CAPABILITIES), capabilities)
                self.assertEqual(
                    self.loader.get_plugin_info(plugin_id)["vibe"], vibe
                )

    def test_manifest_rejects_unknown_capability_and_neutral_override(self):
        source = """
from animation import AnimationBase
class ExampleAnimation(AnimationBase):
    def get_parameter_schema(self):
        return {"palette": {"type": "str", "options": ["ocean"], "default": "ocean"}}
    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()
"""
        base = {
            "plugin_id": "example", "class": "ExampleAnimation",
            "icon": "example", "gallery": "show",
            "vibe": {
                "color_policy": "grade", "timing_adapter": "wall_clock",
                "capabilities": ["luminance"],
            },
        }
        for mutation, message in (
            (("capabilities", ["telepathy"]), "unsupported values"),
            (("mapping", {"palette": {"neutral": "ocean"}}), "neutral must preserve"),
            (("preserve_palette", None), "preserve color policy cannot claim"),
        ):
            with self.subTest(mutation=mutation[0]), tempfile.TemporaryDirectory() as temporary:
                plugin_dir = Path(temporary) / "example"
                plugin_dir.mkdir()
                (plugin_dir / "__init__.py").write_text(source, encoding="utf-8")
                payload = deepcopy(base)
                if mutation[0] == "capabilities":
                    payload["vibe"]["capabilities"] = mutation[1]
                elif mutation[0] == "mapping":
                    payload["vibe"]["capabilities"].append("palette_roles")
                    payload["vibe"]["legacy_parameter_mappings"] = mutation[1]
                else:
                    payload["vibe"]["color_policy"] = "preserve"
                    payload["vibe"]["capabilities"] = ["palette_roles"]
                (plugin_dir / "manifest.json").write_text(
                    json.dumps(payload), encoding="utf-8"
                )
                with self.assertRaisesRegex(ValueError, message):
                    AnimationPluginLoader(temporary).scan_plugins()

    def test_neutral_context_keeps_nondefault_authored_palettes_byte_exact(self):
        cases = (
            (FixedClock, {"palette": "ocean", "background": "aurora"}, 0.5),
            (LavaLampAnimation, {"palette": "ocean", "seed": 88}, 0.05),
        )
        for animation_class, config, elapsed in cases:
            with self.subTest(animation=animation_class.__name__):
                baseline = animation_class(Controller(), config)
                neutral = animation_class(Controller(), config)
                neutral_context = context(
                    vibe_id="neutral", unscaled=elapsed, scaled=elapsed
                )
                np.testing.assert_array_equal(
                    pixels(baseline.generate_frame(elapsed, 0)),
                    pixels(neutral.generate_frame_with_context(neutral_context)),
                )

    def test_clock_uses_semantic_hud_and_accent_but_tempo_cannot_change_wall_time(self):
        clock = FixedClock(Controller(), {
            "palette": "amber", "face": "digital", "background": "solid",
            "show_seconds": True, "glow": 0.0,
        })
        original_now = clock._clock_now()
        vivid = context(
            vibe_id="vivid", unscaled=0.25, scaled=25.0
        )
        frame = pixels(clock.generate_frame_with_context(vivid))

        self.assertEqual(clock._clock_now(), original_now)
        colors = set(map(tuple, frame.tolist()))
        self.assertIn(ROLES["hud"], colors)
        self.assertIn(ROLES["accent"], colors)
        self.assertNotEqual(frame.tobytes(), pixels(
            FixedClock(Controller(), {
                "palette": "amber", "face": "digital", "background": "solid",
                "show_seconds": True, "glow": 0.0,
            }).generate_frame(25.0, 0)
        ).tobytes())

    def test_simple_test_preserves_exact_source_colors_before_luminance(self):
        diagnostic = SimpleTestAnimation(Controller())
        celebration = context(
            vibe_id="celebration", unscaled=0.0, scaled=99.0
        )
        frame = pixels(diagnostic.generate_frame_with_context(celebration))
        np.testing.assert_array_equal(
            frame, np.full((Controller.total_leds, 3), (255, 0, 0), dtype=np.uint8)
        )
        self.assertNotIn("palette_roles", diagnostic.VIBE_CAPABILITIES)

    def test_presentation_only_vibe_change_preserves_seeded_state_rng_and_events(self):
        cases = (
            (
                LavaLampAnimation,
                {"seed": 7331, "palette": "classic", "speed": 1.0},
                lava_logical_state,
            ),
            (
                SnakeAnimation,
                {"seed": 7331, "snake_count": 2, "speed": 1.0},
                snake_logical_state,
            ),
        )
        for animation_class, config, logical_state in cases:
            with self.subTest(animation=animation_class.__name__):
                animation = animation_class(Controller(), config)
                animation.set_presentation_context(context(
                    vibe_id="quiet", unscaled=0.20, scaled=0.20
                ))
                animation.generate_frame(0.20, 0)
                before = (logical_state(animation), rng_state(animation))
                animation.set_presentation_context(context(
                    vibe_id="vivid", unscaled=0.20, scaled=0.20
                ))
                after = (logical_state(animation), rng_state(animation))
                self.assertEqual(before, after)

    def test_paired_seeded_runs_have_logical_rng_and_event_parity(self):
        cases = (
            (
                LavaLampAnimation,
                {"seed": 901, "palette": "classic", "speed": 1.0},
                lava_logical_state,
            ),
            (
                SnakeAnimation,
                {"seed": 901, "snake_count": 2, "speed": 1.0},
                snake_logical_state,
            ),
        )
        for animation_class, config, logical_state in cases:
            with self.subTest(animation=animation_class.__name__):
                quiet = animation_class(Controller(), config)
                vivid = animation_class(Controller(), config)
                for index in range(31):
                    unscaled = index / 100.0
                    scaled = index / 50.0
                    quiet_context = context(
                        vibe_id="quiet", unscaled=unscaled,
                        scaled=scaled, frame=index,
                    )
                    vivid_context = context(
                        vibe_id="vivid", unscaled=unscaled,
                        scaled=scaled, frame=index,
                    )
                    quiet.generate_frame_with_context(quiet_context)
                    vivid.generate_frame_with_context(vivid_context)
                self.assertEqual(logical_state(quiet), logical_state(vivid))
                self.assertEqual(rng_state(quiet), rng_state(vivid))

    def test_scaled_context_keeps_cadence_unscaled_and_applies_tempo_once(self):
        lava = LavaLampAnimation(Controller(), {"seed": 123, "speed": 2.0})
        snake = SnakeAnimation(Controller(), {
            "seed": 123, "speed": 2.0, "render_fps": 30.0,
        })
        snake_unity_authored = SnakeAnimation(Controller(), {
            "seed": 123, "speed": 1.0, "render_fps": 30.0,
        })
        changed = {"lava": 0, "snake": 0}
        for index in range(101):
            unscaled = index / 200.0
            # This represents authored speed 2.0 x vibe tempo 1.5, already
            # resolved by the manager exactly once into scaled elapsed time.
            scaled = unscaled * 3.0
            runtime = context(
                vibe_id="vivid", unscaled=unscaled, scaled=scaled, frame=index
            )
            changed["lava"] += int(lava.generate_frame_with_context(runtime).changed)
            changed["snake"] += int(snake.generate_frame_with_context(runtime).changed)
            snake_unity_authored.generate_frame_with_context(runtime)

        self.assertGreaterEqual(changed["lava"], 49)
        self.assertLessEqual(changed["lava"], 52)
        self.assertGreaterEqual(changed["snake"], 15)
        self.assertLessEqual(changed["snake"], 17)
        self.assertAlmostEqual(lava.simulation_time, 1.5, delta=0.02)
        # The game's four-steps-per-source-frame cap intentionally bounds this
        # high-tempo case. Authored speed is already present in scaled time; if
        # Snake multiplied its authored speed a second time, these paired seeded
        # runs would diverge.
        self.assertGreaterEqual(snake.moves, 52)
        self.assertLessEqual(snake.moves, 60)
        self.assertEqual(snake_logical_state(snake), snake_logical_state(snake_unity_authored))
        self.assertEqual(rng_state(snake), rng_state(snake_unity_authored))


if __name__ == "__main__":
    unittest.main()
