"""Phase 2D acceptance for the final presentation-palette family wave."""

from __future__ import annotations

import json
import random
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
    component_preset_fingerprint,
    resolve_vibe,
)
from animation.plugins.fluid_tank import Hole

PLUGIN_IDS = (
    "ascii_drop",
    "conway_life",
    "fluid_tank",
    "plant_glow",
    "spiral_single",
)
MIGRATED_IDS = frozenset(
    ("ascii_drop", "fluid_tank", "plant_glow", "spiral_single")
)
EXPECTED_ROLES = {
    "ascii_drop": {"background_low", "primary", "secondary", "accent"},
    "fluid_tank": {"background_low", "primary", "secondary", "accent"},
    "plant_glow": {"background_low", "primary", "secondary", "accent"},
    "spiral_single": {"primary", "secondary", "accent"},
}
PRESET_COUNTS = {
    "ascii_drop": 5,
    "conway_life": 15,
    "fluid_tank": 6,
    "plant_glow": 15,
    "spiral_single": 0,
}
BASELINE_CONFIGS = {
    "ascii_drop": {
        "random_seed": 731,
        "phrase": "CODEX",
        "character_red": 17,
        "character_green": 203,
        "character_blue": 91,
        "background_red": 2,
        "background_green": 3,
        "background_blue": 11,
        "spawn_rate": 4.0,
        "drop_speed": 27.0,
    },
    "conway_life": {
        "random_seed": 733,
        "random_density": 0.19,
        "seed_pattern": "random",
        "palette": "natural",
        "background": "earth",
        "background_brightness": 0.24,
        "glider_interval": 0.0,
        "destruct_on_loop": False,
    },
    "fluid_tank": {
        "auto_hole": False,
        "target_fill_time": 5.0,
        "drop_rate": 2.0,
        "max_drop_rate": 90.0,
        "bubble_interval": 0.5,
        "brightness": 0.81,
    },
    "plant_glow": {
        "background_source": "color",
        "background_red": 3,
        "background_green": 7,
        "background_blue": 19,
        "foliage_red": 61,
        "foliage_green": 211,
        "foliage_blue": 101,
        "foliage_halo_red": 11,
        "foliage_halo_green": 73,
        "foliage_halo_blue": 173,
        "globe_red": 229,
        "globe_green": 69,
        "globe_blue": 201,
        "globe_halo_red": 91,
        "globe_halo_green": 29,
        "globe_halo_blue": 237,
    },
    "spiral_single": {
        "red": 211,
        "green": 83,
        "blue": 29,
        "pixels_per_second": 137.0,
        "brightness": 0.73,
    },
}
NEUTRAL_BASELINE_SHA256 = {
    "ascii_drop": "c4fab46ec4137e14ee3aa30ccfc354bdecfb993a12bd80790673ef9097a1b89b",
    "conway_life": "46df3cfecc6320d38b031d277a473ef6b13311d486bf447c5f903d9281d12ac5",
    "fluid_tank": "60d796b3c5291a6330eb3b8b42861d2447c4ae8aed0897a924d811c2986c8a02",
    "plant_glow": "1d2db4ec2a3449c4169f0528efcef3e1a8a3cdca21d94497b618cffd309113e8",
    "spiral_single": "510541f07f9b7272817a9f6bb318acc3a0928d6660622334fcc3fbf10bbd3662",
}
CONWAY_OPTIMIZATION_BASELINES = {
    "default_seeded": {
        "config": {"random_seed": 4421},
        "frames": "9e816abbecf4be8224578091b076b3da3395f24c612ec549aecfc2d0ceb5004d",
        "state": "cc3246b8a0387fa17c93af351a53cd9631b9c7ae539552a7d7769ca7cb0e9f40",
    },
    "maximum_chaos": {
        "config": None,
        "frames": "612b9412fb7bc35baae26c2314bfdfc7aad9a39f750226ed875158f309a157cb",
        "state": "52317706853169940a979726b46407c2e08c837ca302c62dfb0725d63a70c4e5",
    },
}
SAMPLE_TIMES = (0.0, 0.041, 0.083, 0.167, 0.333, 0.667, 1.25)
STRESS_CONFIGS = {
    "ascii_drop": {
        "random_seed": 9901,
        "phrase": "ABCDEFGHIJKLMNOPQRSTUVWXYZ 0123456789",
        "drop_speed": 80.0,
        "spawn_rate": 10.0,
        "clear_fill_ratio": 0.9,
        "plant_aware": True,
    },
    "conway_life": {
        "random_seed": 9902,
        "random_density": 0.28,
        "phase_frames": 4,
        "generations_per_second": 12.0,
        "glider_interval": 3.0,
        "glider_count": 8,
        "destruct_on_loop": False,
        "background": "aurora",
        "background_brightness": 0.14,
        "background_fps": 30.0,
    },
    "fluid_tank": {
        "auto_hole": False,
        "drop_rate": 15.0,
        "target_fill_time": 8.0,
        "flow_steps": 8,
        "max_drop_rate": 500.0,
        "bubble_interval": 0.3,
        "surface_shimmer": 1.5,
        "caustic_strength": 0.5,
    },
    "plant_glow": {
        "background_source": "pinball",
        "background_strength": 0.72,
        "background_speed": 3.0,
        "background_seed": 9090,
        "glow_radius": 5,
        "glow_strength": 1.2,
        "shimmer": 0.3,
    },
    "spiral_single": {
        "pixels_per_second": 1000.0,
        "plant_aware": True,
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
    profile = resolve_vibe(vibe_id).profile
    return AnimationRuntimeContext(
        wall_time=1_787_318_400.0 + elapsed,
        unscaled_elapsed=elapsed,
        scaled_elapsed=elapsed,
        frame_index=frame_index,
        scene_epoch=43,
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
        installation_profile_view={},
        plant_modifiers={},
    )


def percentile(samples: list[float], ratio: float) -> float:
    ordered = sorted(samples)
    return ordered[round((len(ordered) - 1) * ratio)]


def _mapping_tuple(items) -> tuple:
    return tuple(tuple(sorted(item.items())) for item in items)


def semantic_state(plugin_id: str, animation) -> tuple:
    if plugin_id == "ascii_drop":
        return (
            animation._settled.tobytes(),
            _mapping_tuple(animation._pieces),
            animation._settled_revision,
            animation._phrase_index,
            animation._next_spawn_time,
            animation._last_time,
        )
    if plugin_id == "conway_life":
        return (
            tuple(tuple(row) for row in animation.grid),
            tuple(tuple(row) for row in animation.next_grid),
            tuple(tuple(row) for row in animation.natural_grid),
            tuple(tuple(row) for row in animation.next_natural_grid),
            animation.phase,
            animation.phase_frame,
            animation.frame_progress,
            animation.generation,
            animation.last_step_elapsed,
            animation.last_glider_time,
            animation.births_last_generation,
            animation.deaths_last_generation,
            animation.plant_emitter_events,
        )
    if plugin_id == "fluid_tank":
        return (
            animation.volume_cells,
            animation.surface_offset.tobytes(),
            animation.surface_velocity.tobytes(),
            animation.water.tobytes(),
            animation.last_time,
            animation.inlet_reservoir_cells,
            _mapping_tuple(animation.inlet_particles),
            _mapping_tuple(animation.bubbles),
            tuple(
                (hole.x, hole.y, hole.radius, hole.opened_at, hole.manual, hole.dry_time)
                for hole in animation.holes
            ),
            _mapping_tuple(animation.patch_flashes),
            _mapping_tuple(animation.spray_particles),
            animation.bubble_accumulator,
            animation.hole_cooldown_timer,
            animation.total_inflow_cells,
            animation.total_landed_cells,
            animation.total_drained_cells,
        )
    if plugin_id == "plant_glow":
        return (
            tuple(sorted(animation.foliage_indices)),
            tuple(sorted(animation.globe_indices)),
            animation.globe_region_count,
            animation._background_key,
        )
    if plugin_id == "spiral_single":
        return (
            animation.step_index,
            animation._last_output_pixel,
            tuple(animation._plant_spiral_indices),
            tuple(animation._plant_spiral_semantics),
        )
    raise AssertionError(f"unhandled plugin: {plugin_id}")


def rng_state(plugin_id: str, animation):
    if plugin_id == "ascii_drop":
        return deepcopy(animation._rng.bit_generator.state)
    if plugin_id == "conway_life":
        return animation.random.getstate()
    if plugin_id == "fluid_tank":
        return random.getstate()
    return None


class FinalPaletteFamilySemanticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loader = AnimationPluginLoader(allowed_plugins=PLUGIN_IDS)
        cls.plugins = cls.loader.load_all_plugins()
        cls.controller = Controller()

    def make_animation(self, plugin_id: str, config=None):
        if plugin_id == "fluid_tank":
            random.seed(739)
        return self.plugins[plugin_id](self.controller, config or {})

    def test_manifests_bind_exact_policies_roles_and_capabilities(self):
        self.assertEqual(set(self.plugins), set(PLUGIN_IDS))
        for plugin_id in MIGRATED_IDS:
            with self.subTest(plugin=plugin_id):
                vibe = self.loader.plugin_manifests[plugin_id]["vibe"]
                animation_class = self.plugins[plugin_id]
                self.assertEqual(vibe["color_policy"], "semantic")
                self.assertEqual(
                    TimingAdapter(vibe["timing_adapter"]),
                    TimingAdapter.LEGACY_SPEED_PARAM,
                )
                self.assertSetEqual(set(vibe["capabilities"]), {"palette_roles"})
                self.assertSetEqual(
                    set(vibe["semantic_roles"]), EXPECTED_ROLES[plugin_id]
                )
                self.assertEqual(animation_class.VIBE_COLOR_POLICY, "semantic")
                self.assertSetEqual(
                    set(animation_class.VIBE_CAPABILITIES), {"palette_roles"}
                )

        conway = self.loader.plugin_manifests["conway_life"]["vibe"]
        self.assertEqual(conway["color_policy"], "grade")
        self.assertEqual(conway["capabilities"], [])
        self.assertNotIn("semantic_roles", conway)

    def test_conway_retains_grade_because_rgb_lineage_is_inherited_state(self):
        animation = self.make_animation(
            "conway_life",
            {
                "random_seed": 733,
                "random_density": 0.0,
                "seed_cells": [],
                "wrap_edges": False,
                "plant_aware": False,
            },
        )
        animation.grid = [[0 for _ in range(animation.width)] for _ in range(animation.height)]
        animation.natural_grid = [
            [None for _ in range(animation.width)] for _ in range(animation.height)
        ]
        inherited = ((60, 90, 120), (120, 150, 180), (180, 210, 240))
        for (x, y), color in zip(((1, 2), (2, 1), (3, 2)), inherited):
            animation.grid[y][x] = 1
            animation.natural_grid[y][x] = color
        animation._compute_next_state()

        self.assertEqual(animation.next_grid[2][2], 1)
        self.assertEqual(animation.next_natural_grid[2][2], (120, 150, 180))
        self.assertNotEqual(
            animation.next_natural_grid[2][2], animation.PALETTE_ENDPOINTS["classic"][0]
        )

    def test_neutral_output_is_frozen_to_pre_migration_bytes(self):
        for plugin_id, animation_class in self.plugins.items():
            with self.subTest(plugin=plugin_id):
                if plugin_id == "fluid_tank":
                    random.seed(739)
                animation = animation_class(
                    self.controller, BASELINE_CONFIGS[plugin_id]
                )
                digest = sha256()
                for frame_index, elapsed in enumerate(SAMPLE_TIMES):
                    rendered = animation.generate_frame_with_context(
                        context("neutral", elapsed, frame_index)
                    )
                    digest.update(pixels(rendered).tobytes())
                self.assertEqual(
                    digest.hexdigest(), NEUTRAL_BASELINE_SHA256[plugin_id]
                )

    def test_conway_optimized_default_and_maximum_sequences_are_byte_exact(self):
        maximum_path = (
            self.loader.plugins_dir
            / "conway_life"
            / "presets"
            / "maximum-chaos.json"
        )
        maximum_config = json.loads(maximum_path.read_text(encoding="utf-8"))[
            "params"
        ]
        for name, baseline in CONWAY_OPTIMIZATION_BASELINES.items():
            config = baseline["config"] or maximum_config
            animation = self.plugins["conway_life"](self.controller, config)
            frame_digest = sha256()
            for frame_index in range(160):
                elapsed = frame_index / 200.0
                rendered = animation.generate_frame_with_context(
                    context("neutral", elapsed, frame_index)
                )
                frame_digest.update(pixels(rendered).tobytes())
            state_digest = sha256(
                repr(
                    (
                        animation.grid,
                        animation.next_grid,
                        animation.natural_grid,
                        animation.next_natural_grid,
                        animation.phase,
                        animation.phase_frame,
                        animation.frame_progress,
                        animation.generation,
                        animation.random.getstate(),
                        animation._render_cells,
                    )
                ).encode()
            )
            with self.subTest(scenario=name):
                self.assertEqual(frame_digest.hexdigest(), baseline["frames"])
                self.assertEqual(state_digest.hexdigest(), baseline["state"])

    def test_every_canonical_vibe_is_visible_with_state_rng_and_cadence_parity(self):
        for plugin_id in PLUGIN_IDS:
            animation_class = self.plugins[plugin_id]
            frames_by_vibe = {}
            states_by_vibe = {}
            rng_by_vibe = {}
            cadence_by_vibe = {}
            for vibe_id in CANONICAL_VIBE_IDS:
                if plugin_id == "fluid_tank":
                    random.seed(739)
                animation = animation_class(
                    self.controller, BASELINE_CONFIGS[plugin_id]
                )
                digest = sha256()
                changed = []
                for frame_index, elapsed in enumerate(SAMPLE_TIMES):
                    rendered = animation.generate_frame_with_context(
                        context(vibe_id, elapsed, frame_index)
                    )
                    digest.update(pixels(rendered).tobytes())
                    changed.append(
                        rendered.changed if isinstance(rendered, RenderedFrame) else True
                    )
                frames_by_vibe[vibe_id] = digest.hexdigest()
                states_by_vibe[vibe_id] = semantic_state(plugin_id, animation)
                rng_by_vibe[vibe_id] = rng_state(plugin_id, animation)
                cadence_by_vibe[vibe_id] = tuple(changed)

            with self.subTest(plugin=plugin_id):
                expected_vibes = 5 if plugin_id in MIGRATED_IDS else 1
                self.assertEqual(len(set(frames_by_vibe.values())), expected_vibes)
                neutral_state = states_by_vibe["neutral"]
                neutral_rng = rng_by_vibe["neutral"]
                neutral_cadence = cadence_by_vibe["neutral"]
                for vibe_id in CANONICAL_VIBE_IDS[1:]:
                    self.assertEqual(states_by_vibe[vibe_id], neutral_state)
                    self.assertEqual(rng_by_vibe[vibe_id], neutral_rng)
                    self.assertEqual(cadence_by_vibe[vibe_id], neutral_cadence)

    def test_manifest_role_sets_exactly_match_behavioral_consumption(self):
        profile_roles = dict(resolve_vibe("vivid").profile.palette_roles)
        for plugin_id in MIGRATED_IDS:
            baseline = self._role_render_digest(plugin_id, profile_roles)
            consumed_roles = set()
            for role in VIBE_PALETTE_ROLES:
                mutated_roles = dict(profile_roles)
                mutated_roles[role] = tuple(
                    255 - channel for channel in profile_roles[role]
                )
                if self._role_render_digest(plugin_id, mutated_roles) != baseline:
                    consumed_roles.add(role)
            with self.subTest(plugin=plugin_id):
                self.assertSetEqual(consumed_roles, EXPECTED_ROLES[plugin_id])

    def _role_render_digest(self, plugin_id: str, roles) -> bytes:
        config = dict(BASELINE_CONFIGS[plugin_id])
        times = SAMPLE_TIMES
        if plugin_id == "ascii_drop":
            config.update(
                {
                    "plant_aware": True,
                    "plant_modifiers": {
                        "version": 1,
                        "active": ["illuminate"],
                        "strengths": {"illuminate": 1.0},
                    },
                }
            )
        animation = self.make_animation(plugin_id, config)
        if plugin_id == "fluid_tank":
            animation.volume_cells = animation.capacity_cells * 0.62
            animation.holes = [
                Hole(16.0, animation.height - 12.0, 2.0, 0.0, manual=True)
            ]
        elif plugin_id == "spiral_single":
            animation.params["pixels_per_second"] = 1.0
            animation._authored_params["pixels_per_second"] = 1.0
            animation._active_route = lambda: (
                [0, 1, 2],
                [0, 1, 2],
                ("role-test",),
            )
            times = (0.0, 1.0, 2.0)

        digest = sha256()
        for frame_index, elapsed in enumerate(times):
            rendered = animation.generate_frame_with_context(
                context("vivid", elapsed, frame_index, palette_roles=roles)
            )
            digest.update(pixels(rendered).tobytes())
        return digest.digest()

    def test_live_vibe_switch_redraws_without_state_rng_or_authored_changes(self):
        for plugin_id in MIGRATED_IDS:
            with self.subTest(plugin=plugin_id):
                animation = self.make_animation(
                    plugin_id, BASELINE_CONFIGS[plugin_id]
                )
                authored = animation.authored_params_snapshot()
                quiet = animation.generate_frame_with_context(
                    context("quiet", 0.333, 67)
                )
                quiet_pixels = pixels(quiet).copy()
                before = (
                    semantic_state(plugin_id, animation),
                    rng_state(plugin_id, animation),
                )

                vivid = animation.generate_frame_with_context(
                    context("vivid", 0.333, 67)
                )
                after = (
                    semantic_state(plugin_id, animation),
                    rng_state(plugin_id, animation),
                )

                self.assertFalse(np.array_equal(quiet_pixels, pixels(vivid)))
                if isinstance(vivid, RenderedFrame):
                    self.assertTrue(vivid.changed)
                self.assertEqual(before, after)
                self.assertEqual(animation.authored_params_snapshot(), authored)

    def test_all_curated_presets_validate_render_and_keep_identity(self):
        for plugin_id, animation_class in self.plugins.items():
            paths = tuple(self.loader.iter_curated_preset_files(plugin_id))
            self.assertEqual(len(paths), PRESET_COUNTS[plugin_id])
            neutral_fingerprints = set()
            for preset_path in paths:
                payload = json.loads(preset_path.read_text(encoding="utf-8"))
                original = deepcopy(payload)
                identity = component_preset_fingerprint(
                    plugin_id, payload["preset_id"], payload["params"]
                )
                validated = self.loader.validate_component_parameters(
                    plugin_id, payload["params"]
                )
                vibe_fingerprints = set()
                for vibe_id in CANONICAL_VIBE_IDS:
                    if plugin_id == "fluid_tank":
                        random.seed(10_000)
                    animation = animation_class(self.controller, validated)
                    if plugin_id == "fluid_tank":
                        animation.volume_cells = animation.capacity_cells * 0.45
                    rendered = None
                    for frame_index, elapsed in enumerate((0.0, 0.08, 0.2)):
                        rendered = animation.generate_frame_with_context(
                            context(vibe_id, elapsed, frame_index)
                        )
                    assert rendered is not None
                    frame = pixels(rendered)
                    self.assertEqual(frame.shape, (Controller.total_leds, 3))
                    self.assertEqual(frame.dtype, np.uint8)
                    self.assertTrue(frame.flags.c_contiguous)
                    vibe_fingerprints.add(sha256(frame.tobytes()).digest())
                    if vibe_id == "neutral":
                        neutral_fingerprints.add(sha256(frame.tobytes()).digest())
                    animation.cleanup()

                with self.subTest(plugin=plugin_id, preset=preset_path.stem):
                    self.assertEqual(payload, original)
                    self.assertEqual(payload["preset_id"], preset_path.stem)
                    self.assertEqual(
                        component_preset_fingerprint(
                            plugin_id, payload["preset_id"], payload["params"]
                        ),
                        identity,
                    )
                    expected_vibes = 5 if plugin_id in MIGRATED_IDS else 1
                    self.assertEqual(len(vibe_fingerprints), expected_vibes)
            self.assertEqual(len(neutral_fingerprints), len(paths))

    def test_real_wall_default_and_stress_p95_budget_and_changed_ratio(self):
        host_fps = 200.0
        sample_count = 100
        runtimes = tuple(
            context("vivid", frame_index / host_fps, frame_index)
            for frame_index in range(sample_count)
        )
        for plugin_id, animation_class in self.plugins.items():
            for scenario, config in (
                ("default", BASELINE_CONFIGS[plugin_id]),
                ("stress", STRESS_CONFIGS[plugin_id]),
            ):
                if plugin_id == "fluid_tank":
                    random.seed(739)
                animation = animation_class(self.controller, config)
                timings = []
                changed = 0
                for runtime in runtimes:
                    started = time.perf_counter()
                    rendered = animation.generate_frame_with_context(runtime)
                    timings.append((time.perf_counter() - started) * 1000.0)
                    changed += int(
                        rendered.changed if isinstance(rendered, RenderedFrame) else True
                    )

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
                    self.assertLess(mean_ms, 4.0)
                    self.assertLess(p95_ms, 4.0)
                    self.assertLess(p99_ms, 50.0)
                    self.assertGreaterEqual(changed_ratio, 0.05)
                    self.assertLessEqual(changed_ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
