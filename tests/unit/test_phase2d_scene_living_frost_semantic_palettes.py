"""Phase 2D semantic-palette acceptance for scenes, living systems, and frost."""

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

PLUGIN_IDS = (
    "circadian_window",
    "night_train_windows",
    "firefly_synchrony",
    "physarum_network",
    "frostwork",
)
SEMANTIC_ROLES = ("background_low", "primary", "accent")
NEUTRAL_TIMES = (0.0, 0.025, 0.05, 0.1, 0.17)
NEUTRAL_CONFIGS = {
    "circadian_window": {"hour": 6.25, "seed": 2468, "mood": "ember"},
    "night_train_windows": {"seed": 2468, "mood": "ember"},
    "firefly_synchrony": {"seed": 2468, "mood": "ember"},
    "physarum_network": {"seed": 2468, "mood": "ember"},
    "frostwork": {"seed": 2468, "mood": "synthwave"},
}
NEUTRAL_SEQUENCE_DIGESTS = {
    "circadian_window": "7677910a1d78e7e94c444d7365d90ad214ac7fb5e96131c64bcc25322df22155",
    "night_train_windows": "1e4e0eed30879277f6b0b71627c99837bc88ec37e55d7a26c933105e901c591d",
    "firefly_synchrony": "0254c345106c2c63bb04e5d4e540638d691e8f8c85dd64b244bea0960f9139e7",
    "physarum_network": "a54b6aca1251e02993027a665c38b803c041fae782821300c2dcc7772680f0d8",
    "frostwork": "95d74e2b5d39d1591a34665cc85af7215aabf451c1fb77b9613a07a5f47766d4",
}
LIVING_IDS = frozenset(("firefly_synchrony", "physarum_network"))
LONGFORM_IDS = frozenset(("circadian_window", "night_train_windows"))


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
    elapsed: float,
    frame_index: int,
    authored_speed: float = 1.0,
    palette_roles=None,
) -> AnimationRuntimeContext:
    resolved = resolve_vibe(vibe_id)
    return AnimationRuntimeContext(
        wall_time=1_776_032_262.0 + elapsed,
        unscaled_elapsed=elapsed,
        scaled_elapsed=elapsed * authored_speed,
        frame_index=frame_index,
        scene_epoch=43,
        global_width=32,
        height=138,
        local_strip_offset=0,
        local_width=32,
        vibe_id=vibe_id,
        vibe_profile_version=resolved.state.profile_version,
        resolved_profile_digest=resolved.state.resolved_profile_digest,
        palette_roles=palette_roles or resolved.profile.palette_roles,
        capability_values=resolved.profile.capability_values,
        tempo_scale=1.0,
        luminance_scale=resolved.profile.luminance_scale,
        operator_tempo_scale=1.0,
        authored_speed=authored_speed,
        effective_time_scale=authored_speed,
        installation_profile_view={},
        plant_modifiers={},
    )


def authored_snapshot(animation) -> str:
    return json.dumps(
        animation.authored_params_snapshot(), sort_keys=True, separators=(",", ":")
    )


def semantic_snapshot(plugin_id: str, animation) -> tuple:
    if plugin_id in LONGFORM_IDS:
        return (
            authored_snapshot(animation),
            animation._phases.tobytes(),
            deepcopy(animation._rng.bit_generator.state),
            animation._last_tick,
        )
    if plugin_id in LIVING_IDS:
        return (
            authored_snapshot(animation),
            animation.logical_state(),
            deepcopy(animation.rng.bit_generator.state),
            animation._logical_generation,
            animation._sim_time,
            animation._accumulator,
            animation._last_elapsed,
        )
    return (
        authored_snapshot(animation),
        animation.occupied.tobytes(),
        animation.age.tobytes(),
        deepcopy(animation.rng.bit_generator.state),
        animation._last_sim_tick,
    )


def configured_speed(animation) -> float:
    return float(animation.get_authored_parameter("speed", 1.0))


class SceneLivingFrostSemanticPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
        cls.plugins = cls.loader.load_all_plugins()
        cls.controller = Controller()

    def test_manifests_bind_the_exact_bridge_free_semantic_contract(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            vibe = self.loader.plugin_manifests[plugin_id]["vibe"]
            with self.subTest(plugin=plugin_id):
                self.assertEqual(vibe["color_policy"], "semantic")
                self.assertEqual(vibe["timing_adapter"], "legacy_speed_param")
                self.assertEqual(vibe["capabilities"], ["luminance", "palette_roles"])
                self.assertEqual(vibe["semantic_roles"], sorted(SEMANTIC_ROLES))
                self.assertNotIn("legacy_parameter_mappings", vibe)
                self.assertEqual(animation_class.VIBE_COLOR_POLICY, "semantic")
                self.assertEqual(
                    animation_class.VIBE_CAPABILITIES,
                    frozenset(("luminance", "palette_roles")),
                )
                self.assertEqual(animation_class.VIBE_PARAMETER_MAPPINGS, {})

    def test_neutral_frames_match_historical_authored_bytes(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            direct = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
            neutral = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
            speed = configured_speed(neutral)
            direct_bytes = []
            neutral_bytes = []
            for index, elapsed in enumerate(NEUTRAL_TIMES):
                expected = pixels(direct.generate_frame(elapsed, index)).copy()
                actual = pixels(neutral.generate_frame_with_context(runtime_context(
                    "neutral",
                    elapsed=elapsed,
                    frame_index=index,
                    authored_speed=speed,
                ))).copy()
                with self.subTest(plugin=plugin_id, elapsed=elapsed):
                    np.testing.assert_array_equal(actual, expected)
                direct_bytes.append(expected.tobytes())
                neutral_bytes.append(actual.tobytes())
            self.assertEqual(direct_bytes, neutral_bytes)
            self.assertEqual(
                hashlib.sha256(b"".join(neutral_bytes)).hexdigest(),
                NEUTRAL_SEQUENCE_DIGESTS[plugin_id],
            )

    def test_each_declared_role_is_directly_and_behaviorally_consumed(self) -> None:
        base_roles = dict(resolve_vibe("vivid").profile.palette_roles)
        for plugin_id, animation_class in self.plugins.items():
            for role_index, role in enumerate(SEMANTIC_ROLES):
                left = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
                right = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
                if plugin_id == "firefly_synchrony":
                    left.phase.fill(0.0)
                    right.phase.fill(0.0)
                elif plugin_id == "physarum_network":
                    gradient = np.linspace(
                        0.0, 1.0, left.height, dtype=np.float32
                    )[:, None]
                    left.trail[:] = gradient
                    right.trail[:] = gradient
                expected_roles = dict(base_roles)
                expected_roles.update({
                    "background_low": (3, 7, 11),
                    "primary": (47, 101, 149),
                    "accent": (181, 213, 239),
                })
                changed_roles = dict(expected_roles)
                changed_roles[role] = (
                    249 - role_index * 13,
                    19 + role_index * 17,
                    37 + role_index * 23,
                )
                baseline = left.generate_frame_with_context(runtime_context(
                    "vivid",
                    elapsed=0.17,
                    frame_index=3,
                    palette_roles=expected_roles,
                ))
                changed = right.generate_frame_with_context(runtime_context(
                    "vivid",
                    elapsed=0.17,
                    frame_index=3,
                    palette_roles=changed_roles,
                ))
                with self.subTest(plugin=plugin_id, role=role):
                    self.assertFalse(np.array_equal(pixels(baseline), pixels(changed)))

            animation = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
            context = runtime_context(
                "vivid", elapsed=0.0, frame_index=0, palette_roles=expected_roles
            )
            animation.set_presentation_context(context)
            if plugin_id in LONGFORM_IDS:
                palette = animation.palette()
            elif plugin_id in LIVING_IDS:
                palette = animation._palette()
            else:
                palette = animation.palette(str(animation.params["mood"]))
            self.assertEqual(
                tuple(tuple(int(channel) for channel in color) for color in palette),
                tuple(expected_roles[role] for role in SEMANTIC_ROLES),
            )

    def test_all_canonical_vibes_produce_distinct_frames(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            fingerprints = set()
            for vibe_id in CANONICAL_VIBE_IDS:
                animation = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
                speed = configured_speed(animation)
                rendered = None
                for index, elapsed in enumerate((0.0, 0.05, 0.1, 0.17, 0.25)):
                    rendered = animation.generate_frame_with_context(runtime_context(
                        vibe_id,
                        elapsed=elapsed,
                        frame_index=index,
                        authored_speed=speed,
                    ))
                assert rendered is not None
                fingerprints.add(hashlib.sha256(pixels(rendered).tobytes()).digest())
            with self.subTest(plugin=plugin_id):
                self.assertEqual(len(fingerprints), len(CANONICAL_VIBE_IDS))

    def test_palette_profiles_preserve_state_rng_events_and_cadence(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            neutral = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
            vivid = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
            speed = configured_speed(neutral)
            neutral_changed = []
            vivid_changed = []
            neutral_frame = vivid_frame = None
            for index, elapsed in enumerate((0.0, 0.025, 0.05, 0.1, 0.17, 0.25)):
                neutral_frame = neutral.generate_frame_with_context(runtime_context(
                    "neutral",
                    elapsed=elapsed,
                    frame_index=index,
                    authored_speed=speed,
                ))
                vivid_frame = vivid.generate_frame_with_context(runtime_context(
                    "vivid",
                    elapsed=elapsed,
                    frame_index=index,
                    authored_speed=speed,
                ))
                neutral_changed.append(neutral_frame.changed)
                vivid_changed.append(vivid_frame.changed)
                with self.subTest(plugin=plugin_id, elapsed=elapsed):
                    self.assertEqual(
                        semantic_snapshot(plugin_id, neutral),
                        semantic_snapshot(plugin_id, vivid),
                    )
            assert neutral_frame is not None and vivid_frame is not None
            self.assertEqual(neutral_changed, vivid_changed)
            self.assertFalse(np.array_equal(pixels(neutral_frame), pixels(vivid_frame)))

    def test_same_time_live_switch_repaints_without_semantic_advancement(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            animation = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
            speed = configured_speed(animation)
            rendered = None
            for index, elapsed in enumerate((0.0, 0.05, 0.1, 0.17)):
                rendered = animation.generate_frame_with_context(runtime_context(
                    "quiet",
                    elapsed=elapsed,
                    frame_index=index,
                    authored_speed=speed,
                ))
            assert rendered is not None
            before_frame = pixels(rendered).copy()
            before_state = semantic_snapshot(plugin_id, animation)
            refreshed = animation.generate_frame_with_context(runtime_context(
                "celebration", elapsed=0.17, frame_index=5, authored_speed=speed
            ))
            with self.subTest(plugin=plugin_id):
                self.assertTrue(refreshed.changed)
                self.assertEqual(semantic_snapshot(plugin_id, animation), before_state)
                self.assertFalse(np.array_equal(pixels(refreshed), before_frame))

    def test_circadian_fixed_hour_semantics_remain_wall_clock_independent(self) -> None:
        animation_class = self.plugins["circadian_window"]
        for vibe_id in CANONICAL_VIBE_IDS:
            midnight = animation_class(
                self.controller, {"hour": 0.0, "time_scale": 0.0, "seed": 51}
            )
            noon = animation_class(
                self.controller, {"hour": 12.0, "time_scale": 0.0, "seed": 51}
            )
            self.assertEqual(midnight._current_hour(99_999.0), 0.0)
            self.assertEqual(noon._current_hour(99_999.0), 12.0)
            midnight_frame = pixels(midnight.generate_frame_with_context(runtime_context(
                vibe_id, elapsed=9.0, frame_index=9
            )))
            noon_frame = pixels(noon.generate_frame_with_context(runtime_context(
                vibe_id, elapsed=9.0, frame_index=9
            )))
            with self.subTest(vibe=vibe_id):
                self.assertGreater(float(noon_frame.mean()), float(midnight_frame.mean()))

    def test_framework_adds_only_the_declared_luminance_pass(self) -> None:
        quiet = resolve_vibe("quiet").profile
        for plugin_id, animation_class in self.plugins.items():
            animation = animation_class(self.controller, NEUTRAL_CONFIGS[plugin_id])
            source = pixels(animation.generate_frame_with_context(runtime_context(
                "quiet", elapsed=0.17, frame_index=3
            ))).copy()
            presented, changed = AnimationManager._apply_vibe_presentation(
                animation,
                source,
                profile=quiet,
                changed=True,
                state=AnimationManager._empty_presentation_state(),
            )
            expected = np.rint(source.astype(np.float32) * quiet.luminance_scale)
            with self.subTest(plugin=plugin_id):
                self.assertTrue(changed)
                np.testing.assert_array_equal(presented, expected.astype(np.uint8))

    def test_every_curated_preset_validates_and_renders_under_every_vibe(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            paths = tuple(self.loader.iter_curated_preset_files(plugin_id))
            self.assertEqual(len(paths), 4)
            fingerprints = {vibe_id: set() for vibe_id in CANONICAL_VIBE_IDS}
            for path in paths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                original = deepcopy(payload)
                identity = component_preset_fingerprint(
                    plugin_id, payload["preset_id"], payload["params"]
                )
                validated = self.loader.validate_component_parameters(
                    plugin_id, payload["params"]
                )
                if plugin_id == "circadian_window" and validated.get("hour", -1) < 0:
                    validated = {**validated, "hour": 12.0}
                for vibe_id in CANONICAL_VIBE_IDS:
                    animation = animation_class(self.controller, validated)
                    speed = configured_speed(animation)
                    rendered = None
                    for index, elapsed in enumerate((0.0, 0.1, 0.25)):
                        rendered = animation.generate_frame_with_context(runtime_context(
                            vibe_id,
                            elapsed=elapsed,
                            frame_index=index,
                            authored_speed=speed,
                        ))
                    assert rendered is not None
                    fingerprints[vibe_id].add(
                        hashlib.sha256(pixels(rendered).tobytes()).digest()
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
            for vibe_id, values in fingerprints.items():
                with self.subTest(plugin=plugin_id, vibe=vibe_id):
                    self.assertEqual(len(values), len(paths))

    def test_default_and_stress_full_wall_p95_and_changed_ratio(self) -> None:
        stress = {
            "circadian_window": {
                "seed": 313,
                "hour": 6.5,
                "time_scale": 3600.0,
                "brightness": 1.0,
                "motion": 1.0,
                "density": 1.0,
                "background": "radiant",
                "background_level": 1.0,
                "render_fps": 40,
            },
            "night_train_windows": {
                "seed": 313,
                "brightness": 1.0,
                "motion": 1.0,
                "density": 1.0,
                "background": "radiant",
                "background_level": 1.0,
                "render_fps": 40,
            },
            "firefly_synchrony": {
                "seed": 313,
                "brightness": 1.0,
                "motion": 2.5,
                "density": 2.0,
                "background": "radiant",
                "background_level": 1.0,
                "simulation_hz": 20.0,
                "render_fps": 40.0,
                "population": 220,
                "coupling_radius": 18.0,
                "synchrony": 2.0,
                "wandering": 2.0,
                "pulse_softness": 0.1,
                "meadow_glow": 0.5,
            },
            "physarum_network": {
                "seed": 313,
                "brightness": 1.0,
                "motion": 2.5,
                "density": 2.0,
                "background": "radiant",
                "background_level": 1.0,
                "simulation_hz": 20.0,
                "render_fps": 40.0,
                "agent_count": 1800,
                "branching": 1.5,
                "diffusion": 0.9,
                "pulse_visibility": 1.0,
            },
            "frostwork": {
                "seed": 313,
                "brightness": 1.0,
                "motion": 1.0,
                "density": 1.0,
                "background": "radiant",
                "background_level": 1.0,
                "temperature": 1.0,
                "melt_cycle": 1.0,
            },
        }
        for plugin_id, animation_class in self.plugins.items():
            default = dict(NEUTRAL_CONFIGS[plugin_id])
            for profile, config in (("default", default), ("stress", stress[plugin_id])):
                animation = animation_class(self.controller, config)
                speed = configured_speed(animation)
                timings = []
                changed = 0
                warmup = 20
                frames = 120
                for index in range(warmup + frames):
                    started = time.perf_counter_ns()
                    rendered = animation.generate_frame_with_context(runtime_context(
                        "vivid",
                        elapsed=index / 200.0,
                        frame_index=index,
                        authored_speed=speed,
                    ))
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
                    if plugin_id == "frostwork":
                        expected_ratio = 20.0 / 200.0
                    elif plugin_id in LONGFORM_IDS:
                        expected_ratio = float(config.get("render_fps", 24)) / 200.0
                    else:
                        expected_ratio = float(config.get("render_fps", 30.0)) / 200.0
                    self.assertAlmostEqual(ratio, expected_ratio, delta=0.035)


if __name__ == "__main__":
    unittest.main()
