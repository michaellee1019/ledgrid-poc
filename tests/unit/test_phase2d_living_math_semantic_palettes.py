"""Phase 2D acceptance for the living/math semantic-palette migration lane."""

from __future__ import annotations

import json
import statistics
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
    TimingAdapter,
    resolve_vibe,
)

PLUGIN_IDS = (
    "cellular_tapestry",
    "cyclic_reef",
    "flow_field_silk",
    "living_stained_glass",
    "quasicrystal_bloom",
    "reaction_diffusion_garden",
)
EXPECTED_ROLES = {
    "cellular_tapestry": {"background_low", "primary", "accent"},
    "cyclic_reef": {"background_high", "primary", "secondary", "accent"},
    "flow_field_silk": {"background_low", "primary", "accent"},
    "living_stained_glass": {"background_low", "primary", "accent"},
    "quasicrystal_bloom": {"background_low", "primary", "accent"},
    "reaction_diffusion_garden": {"background_low", "primary", "accent"},
}
BASELINE_CONFIGS = {
    "cellular_tapestry": {"seed": 2801, "mood": "showcase"},
    "cyclic_reef": {"seed": 13101, "mood": "violet"},
    "flow_field_silk": {"seed": 1901, "mood": "showcase"},
    "living_stained_glass": {"seed": 2201, "mood": "synthwave"},
    "quasicrystal_bloom": {"seed": 2701, "mood": "bright"},
    "reaction_diffusion_garden": {"seed": 9101, "mood": "aurora"},
}
NEUTRAL_BASELINE_SHA256 = {
    "cellular_tapestry": "0373992cf78f65fb7c5e09010c5b3c6b8dc5bf7412c6004d768c5603e70b324d",
    "cyclic_reef": "0d86698639855009f4657dd38f2844e0153fe572432e0d3e1371308302739e6f",
    "flow_field_silk": "a2f0cbe2196ef59b089c2b25a59db61a57e97676f51eca4f31a5dcbf518a7918",
    "living_stained_glass": "99471732e922da610e6f910764ca1866da7b5473033e564426c89dd391eaa3f9",
    "quasicrystal_bloom": "e9e9aefbf49cf5d18f290496fc7222bd091245139c65c945bcc8d8cc63027596",
    "reaction_diffusion_garden": "6061996f9dacb4e84f9b3395fa584e2ea903264224b5f21918887829df8716c2",
}
SAMPLE_TIMES = (0.0, 0.041, 0.083, 0.167, 0.333, 0.667)
STRESS_CONFIGS = {
    "cellular_tapestry": {
        "density": 1.0,
        "motion": 1.0,
        "mutation": 0.15,
        "row_interval": 0.1,
    },
    "cyclic_reef": {
        "density": 2.0,
        "motion": 2.5,
        "state_count": 8,
        "mutation": 0.02,
        "grazer_density": 2.0,
        "simulation_hz": 20.0,
        "render_fps": 40.0,
    },
    "flow_field_silk": {
        "density": 1.0,
        "motion": 1.0,
        "turbulence": 1.0,
        "persistence": 1.0,
    },
    "living_stained_glass": {
        "density": 1.0,
        "motion": 1.0,
        "lead_width": 0.05,
        "background": "radiant",
        "background_level": 1.0,
    },
    "quasicrystal_bloom": {
        "motion": 1.0,
        "symmetry": 12,
        "spatial_scale": 5.0,
        "warp": 1.0,
    },
    "reaction_diffusion_garden": {
        "density": 2.0,
        "motion": 2.5,
        "growth_rate": 2.0,
        "edge_glow": 1.5,
        "simulation_hz": 20.0,
        "render_fps": 40.0,
    },
}


class Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


def pixels(rendered) -> np.ndarray:
    return rendered.pixels if isinstance(rendered, RenderedFrame) else rendered


def context(
    vibe_id: str,
    elapsed: float,
    frame_index: int,
    *,
    palette_roles=None,
) -> AnimationRuntimeContext:
    resolved = resolve_vibe(vibe_id)
    profile = resolved.profile
    return AnimationRuntimeContext(
        wall_time=1_787_318_400.0 + elapsed,
        unscaled_elapsed=elapsed,
        scaled_elapsed=elapsed,
        frame_index=frame_index,
        scene_epoch=37,
        global_width=Controller.strip_count,
        height=Controller.leds_per_strip,
        local_strip_offset=0,
        local_width=Controller.strip_count,
        vibe_id=vibe_id,
        vibe_profile_version=profile.profile_version,
        resolved_profile_digest=profile.resolved_profile_digest,
        palette_roles=profile.palette_roles if palette_roles is None else palette_roles,
        capability_values=profile.capability_values,
        tempo_scale=1.0,
        luminance_scale=1.0,
        installation_profile_view={},
        plant_modifiers={},
    )


def rng_state(animation):
    rng = getattr(animation, "rng", None)
    return deepcopy(rng.bit_generator.state) if rng is not None else None


def semantic_state(plugin_id: str, animation):
    common_timing = (
        getattr(animation, "_last_sim_tick", None),
        getattr(animation, "_last_elapsed", None),
        getattr(animation, "_accumulator", None),
        getattr(animation, "_sim_time", None),
        getattr(animation, "_logical_generation", None),
    )
    if plugin_id == "cellular_tapestry":
        state = (
            animation.history.tobytes(),
            animation.current.tobytes(),
            animation.rows_written,
        )
    elif plugin_id == "cyclic_reef":
        state = animation.logical_state()
    elif plugin_id == "flow_field_silk":
        state = (animation.filaments.tobytes(),)
    elif plugin_id == "living_stained_glass":
        state = (animation.seeds.tobytes(), animation.seed_phase.tobytes())
    elif plugin_id == "quasicrystal_bloom":
        state = ()
    elif plugin_id == "reaction_diffusion_garden":
        state = animation.logical_state()
    else:  # pragma: no cover - the closed lane inventory is asserted separately.
        raise AssertionError(f"unhandled plugin {plugin_id}")
    return state, common_timing


def percentile(samples: list[float], ratio: float) -> float:
    ordered = sorted(samples)
    index = round((len(ordered) - 1) * ratio)
    return ordered[index]


class LivingMathSemanticPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
        cls.plugins = cls.loader.load_all_plugins()
        cls.controller = Controller()

    def test_manifests_bind_exact_semantic_contracts(self):
        self.assertEqual(set(self.plugins), set(PLUGIN_IDS))
        for plugin_id, expected_roles in EXPECTED_ROLES.items():
            with self.subTest(plugin=plugin_id):
                vibe = self.loader.plugin_manifests[plugin_id]["vibe"]
                animation_class = self.plugins[plugin_id]
                self.assertEqual(vibe["color_policy"], "semantic")
                self.assertEqual(
                    TimingAdapter(vibe["timing_adapter"]),
                    TimingAdapter.LEGACY_SPEED_PARAM,
                )
                self.assertSetEqual(set(vibe["capabilities"]), {"palette_roles"})
                self.assertSetEqual(set(vibe["semantic_roles"]), expected_roles)
                self.assertEqual(animation_class.VIBE_COLOR_POLICY, "semantic")
                self.assertSetEqual(
                    set(animation_class.VIBE_CAPABILITIES), {"palette_roles"}
                )

    def test_neutral_output_is_frozen_to_pre_migration_bytes(self):
        for plugin_id, animation_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                animation = animation_class(
                    self.controller, BASELINE_CONFIGS[plugin_id]
                )
                digest = sha256()
                for frame_index, elapsed in enumerate(SAMPLE_TIMES):
                    rendered = animation.generate_frame_with_context(
                        context("neutral", elapsed, frame_index)
                    )
                    digest.update(pixels(rendered).tobytes())
                self.assertEqual(digest.hexdigest(), NEUTRAL_BASELINE_SHA256[plugin_id])

    def test_every_canonical_vibe_is_visible_with_state_and_rng_parity(self):
        for plugin_id, animation_class in self.plugins.items():
            frames_by_vibe = {}
            states_by_vibe = {}
            rng_by_vibe = {}
            for vibe_id in CANONICAL_VIBE_IDS:
                animation = animation_class(
                    self.controller, BASELINE_CONFIGS[plugin_id]
                )
                digest = sha256()
                for frame_index, elapsed in enumerate(SAMPLE_TIMES):
                    rendered = animation.generate_frame_with_context(
                        context(vibe_id, elapsed, frame_index)
                    )
                    digest.update(pixels(rendered).tobytes())
                frames_by_vibe[vibe_id] = digest.hexdigest()
                states_by_vibe[vibe_id] = semantic_state(plugin_id, animation)
                rng_by_vibe[vibe_id] = rng_state(animation)

            with self.subTest(plugin=plugin_id, contract="visible"):
                self.assertEqual(len(set(frames_by_vibe.values())), len(CANONICAL_VIBE_IDS))
            with self.subTest(plugin=plugin_id, contract="semantic-state"):
                self.assertEqual(len(set(states_by_vibe.values())), 1)
            with self.subTest(plugin=plugin_id, contract="rng"):
                neutral_rng = rng_by_vibe["neutral"]
                for vibe_id in CANONICAL_VIBE_IDS[1:]:
                    self.assertEqual(rng_by_vibe[vibe_id], neutral_rng)

    def test_manifest_role_sets_exactly_match_behavioral_consumption(self):
        profile_roles = dict(resolve_vibe("vivid").profile.palette_roles)
        for plugin_id, animation_class in self.plugins.items():
            def render_digest(roles):
                animation = animation_class(
                    self.controller, BASELINE_CONFIGS[plugin_id]
                )
                digest = sha256()
                for frame_index, elapsed in enumerate(SAMPLE_TIMES):
                    rendered = animation.generate_frame_with_context(
                        context(
                            "vivid",
                            elapsed,
                            frame_index,
                            palette_roles=roles,
                        )
                    )
                    digest.update(pixels(rendered).tobytes())
                return digest.digest()

            baseline = render_digest(profile_roles)
            consumed_roles = set()
            for role in VIBE_PALETTE_ROLES:
                mutated_roles = dict(profile_roles)
                mutated_roles[role] = tuple(
                    255 - channel for channel in profile_roles[role]
                )
                if render_digest(mutated_roles) != baseline:
                    consumed_roles.add(role)
            with self.subTest(plugin=plugin_id):
                self.assertSetEqual(consumed_roles, EXPECTED_ROLES[plugin_id])

    def test_live_vibe_switch_redraws_without_reset_tick_or_authored_param_change(self):
        for plugin_id, animation_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                animation = animation_class(
                    self.controller, BASELINE_CONFIGS[plugin_id]
                )
                authored = animation.authored_params_snapshot()
                quiet = animation.generate_frame_with_context(
                    context("quiet", 0.333, 67)
                )
                quiet_pixels = pixels(quiet).copy()
                before = semantic_state(plugin_id, animation), rng_state(animation)

                vivid = animation.generate_frame_with_context(
                    context("vivid", 0.333, 67)
                )
                after = semantic_state(plugin_id, animation), rng_state(animation)

                self.assertTrue(vivid.changed)
                self.assertFalse(np.array_equal(quiet_pixels, pixels(vivid)))
                self.assertEqual(before, after)
                self.assertEqual(animation.authored_params_snapshot(), authored)

    def test_curated_presets_validate_and_render_under_every_canonical_vibe(self):
        for plugin_id, animation_class in self.plugins.items():
            preset_paths = tuple(self.loader.iter_curated_preset_files(plugin_id))
            self.assertGreaterEqual(len(preset_paths), 3)
            for preset_path in preset_paths:
                payload = json.loads(preset_path.read_text(encoding="utf-8"))
                self.loader.validate_component_parameters(plugin_id, payload["params"])
                vibe_fingerprints = set()
                for vibe_id in CANONICAL_VIBE_IDS:
                    animation = animation_class(self.controller, payload["params"])
                    for frame_index, elapsed in enumerate((0.0, 0.2, 0.4)):
                        rendered = animation.generate_frame_with_context(
                            context(vibe_id, elapsed, frame_index)
                        )
                    frame = pixels(rendered)
                    self.assertEqual(frame.shape, (Controller.total_leds, 3))
                    self.assertEqual(frame.dtype, np.uint8)
                    self.assertTrue(frame.flags.c_contiguous)
                    vibe_fingerprints.add(frame.tobytes())
                with self.subTest(plugin=plugin_id, preset=preset_path.stem):
                    self.assertEqual(len(vibe_fingerprints), len(CANONICAL_VIBE_IDS))

    def test_real_wall_default_and_stress_render_budget_and_changed_ratio(self):
        host_fps = 200.0
        sample_count = 80
        contexts = tuple(
            context("vivid", frame_index / host_fps, frame_index)
            for frame_index in range(sample_count)
        )
        for plugin_id, animation_class in self.plugins.items():
            for scenario, config in (
                ("default", {}),
                ("stress", STRESS_CONFIGS[plugin_id]),
            ):
                animation = animation_class(self.controller, config)
                timings = []
                changed = 0
                for runtime in contexts:
                    started = time.perf_counter()
                    rendered = animation.generate_frame_with_context(runtime)
                    timings.append((time.perf_counter() - started) * 1000.0)
                    changed += int(rendered.changed)

                mean_ms = statistics.mean(timings)
                p95_ms = percentile(timings, 0.95)
                p99_ms = percentile(timings, 0.99)
                changed_ratio = changed / sample_count
                with self.subTest(
                    plugin=plugin_id,
                    scenario=scenario,
                    mean_ms=round(mean_ms, 3),
                    p95_ms=round(p95_ms, 3),
                    p99_ms=round(p99_ms, 3),
                    changed_ratio=round(changed_ratio, 3),
                ):
                    self.assertLess(mean_ms, 8.0)
                    self.assertLess(p95_ms, 4.0)
                    self.assertLess(p99_ms, 50.0)
                    self.assertGreaterEqual(changed_ratio, 0.08)
                    self.assertLessEqual(changed_ratio, 0.22)


if __name__ == "__main__":
    unittest.main()
