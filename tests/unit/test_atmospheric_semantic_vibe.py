"""Phase 2D semantic-palette acceptance for procedural atmospheres."""

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
    AnimationRuntimeContext,
    component_preset_fingerprint,
    resolve_vibe,
)
from animation.libraries.atmospheric_palette import ATMOSPHERIC_SEMANTIC_ROLES

PLUGIN_IDS = (
    "aurora_curtains",
    "cloud_canyon",
    "desert_wind",
    "moonlit_fog_banks",
    "rain_on_glass",
    "tidal_bioluminescence",
    "waterfall_veil",
    "wind_in_the_reeds",
)
ATMOSPHERE_IDS = frozenset((
    "aurora_curtains",
    "cloud_canyon",
    "rain_on_glass",
    "tidal_bioluminescence",
    "waterfall_veil",
))
LONGFORM_IDS = frozenset(("desert_wind", "moonlit_fog_banks"))
CANONICAL_VIBES = ("neutral", "quiet", "cozy", "vivid", "celebration")
NEUTRAL_DIGESTS = {
    "aurora_curtains": "ec717274fd98f4a55fe9755c693e8674b8da6331b17ce2ceda4ca532b70dcb0c",
    "cloud_canyon": "bfebea00696eb2e112f154199a0a07d60a63784022fcef44bc759654a24d120f",
    "desert_wind": "044da2e891325c8aa7d524b019e7f326d36f6adc8209b51f9686ca4aff4d5aff",
    "moonlit_fog_banks": "07dfdbab892b10ae1c29d2331b0dcb43dfddb235094883de9ee806fcb7987db5",
    "rain_on_glass": "839c378d100a6d551c89949a66f22d99dd1208a0ef9d0a6ef065f32aafa79c68",
    "tidal_bioluminescence": "c196696eaf8c604e1e582300dde52f89a21f310262ac1036845532b7816d0d18",
    "waterfall_veil": "513bbff343a0a4d4933a287cfee41101fba939e852cd2916f1f563f8d99475e3",
    "wind_in_the_reeds": "04caeaeb13ae051c8322f80e9b1f5a0196d9986ff93c436a9731129865a53cf5",
}


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
    authored_speed: float,
) -> AnimationRuntimeContext:
    resolved = resolve_vibe(vibe_id)
    return AnimationRuntimeContext(
        wall_time=1_776_032_262.0 + elapsed,
        unscaled_elapsed=elapsed,
        scaled_elapsed=elapsed * authored_speed,
        frame_index=frame_index,
        scene_epoch=29,
        global_width=32,
        height=138,
        local_strip_offset=0,
        local_width=32,
        vibe_id=vibe_id,
        vibe_profile_version=resolved.state.profile_version,
        resolved_profile_digest=resolved.state.resolved_profile_digest,
        palette_roles=resolved.profile.palette_roles,
        capability_values=resolved.profile.capability_values,
        tempo_scale=1.0,
        luminance_scale=resolved.profile.luminance_scale,
        operator_tempo_scale=1.0,
        authored_speed=authored_speed,
        effective_time_scale=authored_speed,
        installation_profile_view={},
        plant_modifiers={},
    )


def semantic_snapshot(plugin_id: str, animation) -> tuple:
    authored = json.dumps(
        animation.authored_params_snapshot(),
        sort_keys=True,
        separators=(",", ":"),
    )
    if plugin_id in ATMOSPHERE_IDS:
        return (
            authored,
            animation._phase.tobytes(),
            animation._offset.tobytes(),
            animation._frequency.tobytes(),
            animation._simulation_time,
            animation._last_elapsed,
        )
    if plugin_id in LONGFORM_IDS:
        return (
            authored,
            animation._phases.tobytes(),
            json.dumps(
                animation._rng.bit_generator.state,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    return (
        authored,
        animation.logical_state(),
        json.dumps(
            animation.rng.bit_generator.state,
            sort_keys=True,
            separators=(",", ":"),
        ),
        animation._logical_generation,
        animation._sim_time,
        animation._accumulator,
    )


class AtmosphericSemanticVibeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
        cls.plugins = cls.loader.load_all_plugins()
        cls.controller = Controller()

    def test_manifests_declare_the_exact_semantic_contract(self) -> None:
        self.assertEqual(set(self.plugins), set(PLUGIN_IDS))
        for plugin_id, animation_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                vibe = self.loader.plugin_manifests[plugin_id]["vibe"]
                self.assertEqual(vibe["color_policy"], "semantic")
                self.assertEqual(vibe["timing_adapter"], "legacy_speed_param")
                self.assertEqual(vibe["capabilities"], ["luminance", "palette_roles"])
                self.assertEqual(
                    vibe["semantic_roles"], sorted(ATMOSPHERIC_SEMANTIC_ROLES)
                )
                self.assertNotIn("legacy_parameter_mappings", vibe)
                self.assertEqual(animation_class.VIBE_COLOR_POLICY, "semantic")
                self.assertEqual(
                    animation_class.VIBE_CAPABILITIES,
                    frozenset(("luminance", "palette_roles")),
                )

    def test_neutral_frames_match_the_pinned_authored_baseline_byte_for_byte(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            direct = animation_class(self.controller, {"seed": 2468})
            neutral = animation_class(self.controller, {"seed": 2468})
            authored_speed = float(neutral.get_authored_parameter("speed", 1.0))
            final_frame = None
            for index, elapsed in enumerate((0.0, 0.08, 0.17)):
                expected = pixels(direct.generate_frame(elapsed, index)).copy()
                actual = pixels(neutral.generate_frame_with_context(runtime_context(
                    "neutral",
                    elapsed=elapsed,
                    frame_index=index,
                    authored_speed=authored_speed,
                ))).copy()
                with self.subTest(plugin=plugin_id, elapsed=elapsed):
                    np.testing.assert_array_equal(actual, expected)
                final_frame = actual
            assert final_frame is not None
            self.assertEqual(
                hashlib.sha256(final_frame.tobytes()).hexdigest(),
                NEUTRAL_DIGESTS[plugin_id],
            )

    def test_every_canonical_vibe_has_a_distinct_semantic_frame(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            fingerprints = set()
            for vibe_id in CANONICAL_VIBES:
                animation = animation_class(self.controller, {"seed": 771})
                authored_speed = float(animation.get_authored_parameter("speed", 1.0))
                rendered = None
                for index, elapsed in enumerate((0.0, 0.08, 0.17)):
                    rendered = animation.generate_frame_with_context(runtime_context(
                        vibe_id,
                        elapsed=elapsed,
                        frame_index=index,
                        authored_speed=authored_speed,
                    ))
                assert rendered is not None
                fingerprints.add(hashlib.sha256(pixels(rendered).tobytes()).digest())
            with self.subTest(plugin=plugin_id):
                self.assertEqual(len(fingerprints), len(CANONICAL_VIBES))

            animation = animation_class(self.controller, {"seed": 771})
            context = runtime_context(
                "vivid", elapsed=0.0, frame_index=0, authored_speed=1.0
            )
            animation.set_presentation_context(context)
            palette = (
                animation.palette()
                if plugin_id in LONGFORM_IDS
                else animation._palette()
            )
            self.assertEqual(
                tuple(tuple(int(channel) for channel in color) for color in palette),
                tuple(context.palette_roles[role] for role in ATMOSPHERIC_SEMANTIC_ROLES),
            )

    def test_framework_applies_only_the_declared_luminance_after_semantic_color(self) -> None:
        quiet = resolve_vibe("quiet").profile
        for plugin_id, animation_class in self.plugins.items():
            animation = animation_class(self.controller, {"seed": 827})
            authored_speed = float(animation.get_authored_parameter("speed", 1.0))
            rendered = animation.generate_frame_with_context(runtime_context(
                "quiet", elapsed=0.17, frame_index=3, authored_speed=authored_speed
            ))
            source = pixels(rendered).copy()
            presented, _changed = AnimationManager._apply_vibe_presentation(
                animation,
                source,
                profile=quiet,
                changed=True,
                state=AnimationManager._empty_presentation_state(),
            )
            expected = np.rint(source.astype(np.float32) * quiet.luminance_scale)
            with self.subTest(plugin=plugin_id):
                np.testing.assert_array_equal(presented, expected.astype(np.uint8))

    def test_vibe_changes_preserve_semantic_state_rng_cadence_and_authored_params(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            neutral = animation_class(self.controller, {"seed": 9917})
            vivid = animation_class(self.controller, {"seed": 9917})
            authored_speed = float(neutral.get_authored_parameter("speed", 1.0))
            neutral_frame = None
            vivid_frame = None
            for index, elapsed in enumerate((0.0, 0.04, 0.09, 0.17, 0.26)):
                neutral_frame = neutral.generate_frame_with_context(runtime_context(
                    "neutral",
                    elapsed=elapsed,
                    frame_index=index,
                    authored_speed=authored_speed,
                ))
                vivid_frame = vivid.generate_frame_with_context(runtime_context(
                    "vivid",
                    elapsed=elapsed,
                    frame_index=index,
                    authored_speed=authored_speed,
                ))
                with self.subTest(plugin=plugin_id, elapsed=elapsed):
                    self.assertEqual(
                        semantic_snapshot(plugin_id, neutral),
                        semantic_snapshot(plugin_id, vivid),
                    )
            assert neutral_frame is not None and vivid_frame is not None
            self.assertFalse(np.array_equal(pixels(neutral_frame), pixels(vivid_frame)))

            before = semantic_snapshot(plugin_id, neutral)
            before_frame = pixels(neutral_frame).copy()
            refreshed = neutral.generate_frame_with_context(runtime_context(
                "cozy",
                elapsed=0.26,
                frame_index=6,
                authored_speed=authored_speed,
            ))
            self.assertTrue(refreshed.changed)
            self.assertEqual(before, semantic_snapshot(plugin_id, neutral))
            self.assertFalse(np.array_equal(before_frame, pixels(refreshed)))

    def test_curated_presets_remain_valid_distinct_and_identity_stable(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            paths = list(self.loader.iter_curated_preset_files(plugin_id))
            self.assertGreaterEqual(len(paths), 3)
            fingerprints = set()
            for path in paths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                original = deepcopy(payload)
                identity = component_preset_fingerprint(
                    plugin_id, payload["preset_id"], payload["params"]
                )
                validated = self.loader.validate_component_parameters(
                    plugin_id, payload["params"]
                )
                animation = animation_class(self.controller, validated)
                rendered = None
                for index, elapsed in enumerate((0.0, 0.08, 0.17)):
                    rendered = animation.generate_frame(elapsed, index)
                assert rendered is not None
                fingerprints.add(hashlib.sha256(pixels(rendered).tobytes()).digest())
                self.assertEqual(payload, original)
                self.assertEqual(
                    component_preset_fingerprint(
                        plugin_id, payload["preset_id"], payload["params"]
                    ),
                    identity,
                )
            with self.subTest(plugin=plugin_id):
                self.assertEqual(len(fingerprints), len(paths))

    def test_default_and_stress_32x138_timing_and_changed_ratio(self) -> None:
        for plugin_id, animation_class in self.plugins.items():
            if plugin_id in ATMOSPHERE_IDS:
                stress = {
                    "motion": 2.0,
                    "density": 1.0,
                    "brightness": 1.0,
                    "background": "radiant",
                    "background_level": 1.0,
                    "source_fps": 40.0,
                    "seed": 313,
                }
            elif plugin_id in LONGFORM_IDS:
                stress = {
                    "speed": 3.0,
                    "motion": 1.0,
                    "density": 1.0,
                    "brightness": 1.0,
                    "background": "radiant",
                    "background_level": 1.0,
                    "render_fps": 40,
                    "seed": 313,
                }
            else:
                stress = {
                    "wind": 2.0,
                    "gustiness": 2.0,
                    "stem_density": 2.0,
                    "density": 2.0,
                    "motes": 2.0,
                    "brightness": 1.0,
                    "background": "radiant",
                    "background_level": 1.0,
                    "render_fps": 40.0,
                    "simulation_hz": 20.0,
                    "seed": 313,
                }
            for profile, config in (("default", {"seed": 313}), ("stress", stress)):
                animation = animation_class(self.controller, config)
                authored_speed = float(animation.get_authored_parameter("speed", 1.0))
                timings = []
                changed = 0
                warmup = 20
                frames = 120
                for index in range(warmup + frames):
                    context = runtime_context(
                        "vivid",
                        elapsed=index / 200.0,
                        frame_index=index,
                        authored_speed=authored_speed,
                    )
                    started = time.perf_counter_ns()
                    rendered = animation.generate_frame_with_context(context)
                    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
                    if index >= warmup:
                        timings.append(elapsed_ms)
                        changed += int(rendered.changed)
                ordered = sorted(timings)
                p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
                ratio = changed / frames
                with self.subTest(plugin=plugin_id, profile=profile):
                    self.assertLess(p95, 4.0)
                    self.assertLess(max(timings), 25.0)
                    self.assertGreaterEqual(ratio, 0.08)
                    self.assertLessEqual(ratio, 0.30)


if __name__ == "__main__":
    unittest.main()
