"""Acceptance tests for the sparse premultiplied Clock overlay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from animation.core.compositing import coverage_dirty_union
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import NEXT_DEADLINE_SEMANTICS
from animation.core.presentation_contracts import AnimationRuntimeContext, OverlayFrame
from animation.plugins.clock import ClockAnimation
from animation.plugins.clock_overlay import ClockOverlayAnimation


class _Controller:
    strip_count = 32
    leds_per_strip = 138
    total_leds = strip_count * leds_per_strip
    debug = False


class _ShortController:
    strip_count = 32
    leds_per_strip = 40
    total_leds = strip_count * leds_per_strip
    debug = False


class _FixedOverlay(ClockOverlayAnimation):
    fixed_now = datetime(2026, 8, 12, 13, 47, 10, tzinfo=timezone.utc)

    def _clock_now(self):
        return self.fixed_now + timedelta(
            minutes=int(self.params.get("clock_offset_minutes", 0))
        )


class _FixedClock(ClockAnimation):
    def _clock_now(self):
        return datetime(2026, 8, 12, 13, 47, 10, tzinfo=timezone.utc)


# Frozen immediately before extracting ClockFaceRenderer. These are the
# installed-geometry neutral frames for every shipped compatibility preset at
# the fixed wall time above and elapsed=1.25.
_CLOCK_PRESET_SHA256 = {
    "amber-afterglow.json": "2a2790763990d9a6b22b6312146c8eccc849360984abfd005f6ee7ab06d4db9e",
    "aurora-hourglass.json": "63bdfd76cf82d3e82a1223aea4371ba8e963ffb3c725cd1ae21858b1fd24f79b",
    "aurora-orbits.json": "61fd76fbaa47988f657056635a03f5b0b8d6e8d5d52fb8323b43091d8bb8d512",
    "bedside-amber.json": "73fa9b12b4b687b9bd9fbd695d368a3aecbf65cba1b50b72380ad771f48568ce",
    "binary-constellation.json": "c0f9b2b6878fab690eea22c5043172b7facaf1c68666596cb47962a030b52ba2",
    "bioluminescent-tide.json": "3b6af8b71760cf346bf6b3fd3fa6414e1fa4491464214b16af471eed01f32e0e",
    "calendar-glance.json": "655465cd4d19701f40dbc0e1355c921c5ff8e94c6beb1841d8743f966895f52e",
    "chrono-scan.json": "c70cae20fc2dfd90470e51ab884203a4a2b8ccf208136367229915bfba0ec938",
    "classic-wall-clock.json": "45391e6babdba597c424acd7f8b552542262cd00c9f691222462b0a97e435132",
    "daypart-horizon.json": "b745abfbc54896676207599f6ef10bcdc193072d308c9606b0c2c4a22ccd8028",
    "forest-night-almanac.json": "374b9fc97fcd7e5aea97347b604528107e7858f4f6740d86e4780bb6d9d036d9",
    "last-light-horizon.json": "5a6f1a2ad084c13a83a7578155fb02e8f427075f436df71ec37193ccf814652d",
    "lunar-binary-constellation.json": "3b6e329d70eb73bb11795578225940e847f53a1a8f379c1073dcccc901722402",
    "midnight-planetarium.json": "b3638500b213ae5a696392e26161a7d815df7246492999276904afe263ad7fba",
    "night-shift.json": "b285890c70abdcf1dab14e17ae86d7aaaa4f0bc09d53deab8013a4a660e99dda",
    "office-24-hour.json": "a869b4726473492ef87a6a8a208990a635519267e58100a079d16c46f59d24bd",
    "precision-seconds.json": "ae741fed96c06c3025bc59ebb23208f88189321447e28e3a54ed508dd6f947d8",
    "remote-team-minus-eight.json": "01aa88812eb97a7950b792a06b1f4ddbe6fe2f46931c6bdc5c46943f91b0d613",
    "remote-team-plus-six.json": "ac77bc3811befc55519675ac221f1f7d3ef2de0326d762613bec2eb5e0b0ac0b",
    "temporal-monoliths.json": "2e166101e23a8add01c09186e29f18bd542fcaa7e6bc7a74e57f7104be98dd01",
    "three-quiet-signals.json": "63ee4c406e803735e230f03476b1a7842a2eeb10cb1df5dcf7d32edfca4181a2",
    "tidal-orrery.json": "c136b156a5371ac5e55151a5d35bca48f5bcd3fdae42027340325e148fdaff0f",
    "violet-drift-grid.json": "91f40e91f43a147f2b5629a693a957b89a0763f7c7a9c3e910f62321f13422f2",
    "violet-sandstorm.json": "c97880d69328bdd78f1b55e6f2c910c1d4c4c3dfdea59a645b9435605b342eb9",
}


def _context(
    *,
    wall_time: float = 1_786_543_210.0,
    unscaled: float = 1.0,
    scaled: float = 1.0,
    frame: int = 0,
    vibe_id: str = "neutral",
    luminance: float = 1.0,
    tempo: float = 1.0,
    roles=None,
) -> AnimationRuntimeContext:
    return AnimationRuntimeContext(
        wall_time=wall_time,
        unscaled_elapsed=unscaled,
        scaled_elapsed=scaled,
        frame_index=frame,
        scene_epoch=9,
        global_width=_Controller.strip_count,
        height=_Controller.leds_per_strip,
        local_strip_offset=0,
        local_width=_Controller.strip_count,
        vibe_id=vibe_id,
        vibe_profile_version=1,
        palette_roles=roles or {
            "background_low": (1, 2, 4),
            "background_mid": (6, 8, 12),
            "accent": (220, 60, 20),
            "hud": (240, 250, 230),
        },
        capability_values={},
        installation_profile_view={},
        plant_modifiers={},
        tempo_scale=tempo,
        luminance_scale=luminance,
        operator_tempo_scale=tempo,
        authored_speed=1.0,
        effective_time_scale=tempo,
    )


class ClockOverlayTests(unittest.TestCase):
    def test_discovery_manifest_and_authored_schema_are_overlay_specific(self):
        loader = AnimationPluginLoader()
        self.assertIn("clock_overlay", loader.scan_plugins())
        manifest = loader.plugin_manifests["clock_overlay"]
        self.assertEqual(manifest["provider"], "python")
        self.assertEqual(manifest["role"], "overlay")
        self.assertEqual(
            manifest["entrypoint"],
            "animation.plugins.clock_overlay:ClockOverlayAnimation",
        )
        self.assertEqual(manifest["cadence"], {
            "mode": "event_driven",
            "next_deadline_semantics": NEXT_DEADLINE_SEMANTICS,
        })
        self.assertEqual(manifest["vibe"]["timing_adapter"], "wall_clock")
        self.assertSetEqual(
            set(manifest["vibe"]["capabilities"]),
            {"palette_roles", "luminance"},
        )
        loaded = loader.load_plugin("clock_overlay")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.__name__, ClockOverlayAnimation.__name__)

        schema = ClockOverlayAnimation(_Controller()).get_parameter_schema()
        for control in (
            "face", "palette", "format_24h", "show_seconds",
            "clock_offset_minutes", "position_y", "scale", "glow",
            "brightness", "opacity",
        ):
            self.assertIn(control, schema)
        for background_only in ("background", "motion", "density", "speed"):
            self.assertNotIn(background_only, schema)
        self.assertEqual(schema["brightness"]["min"], 0.0)
        self.assertEqual(schema["opacity"]["min"], 0.0)

    def test_every_face_returns_canonical_contiguous_premultiplied_rgba(self):
        fingerprints = set()
        for face in ClockOverlayAnimation.FACE_OPTIONS:
            with self.subTest(face=face):
                animation = _FixedOverlay(_Controller(), {
                    "face": face, "palette": "ice", "glow": 0.75,
                })
                rendered = animation.generate_frame(0.0, 0)
                self.assertIsInstance(rendered, OverlayFrame)
                self.assertTrue(rendered.changed)
                self.assertEqual(rendered.revision, 1)
                self.assertEqual(rendered.pixels.shape, (_Controller.total_leds, 4))
                self.assertEqual(rendered.pixels.dtype, np.uint8)
                self.assertTrue(rendered.pixels.flags.c_contiguous)
                self.assertTrue(np.all(
                    rendered.pixels[:, :3] <= rendered.pixels[:, 3:4]
                ))
                self.assertGreater(np.count_nonzero(rendered.pixels[:, 3]), 0)
                fingerprints.add(rendered.pixels.tobytes())
        self.assertEqual(len(fingerprints), len(ClockOverlayAnimation.FACE_OPTIONS))

    def test_fixed_wall_time_ignores_scene_time_and_honors_offset(self):
        wall_time = 1_786_543_210.0
        animation = ClockOverlayAnimation(_Controller(), {
            "face": "digital", "show_seconds": True,
            "clock_offset_minutes": 90,
        })
        first = animation.generate_frame_with_context(_context(
            wall_time=wall_time, unscaled=1.0, scaled=400.0, frame=1,
        ))
        duplicate = animation.generate_frame_with_context(_context(
            wall_time=wall_time, unscaled=999.0, scaled=0.5, frame=200,
        ))

        self.assertAlmostEqual(
            animation._clock_now().timestamp(), wall_time + 90 * 60, delta=0.001
        )
        self.assertTrue(first.changed)
        self.assertFalse(duplicate.changed)
        self.assertEqual(duplicate.revision, first.revision)
        self.assertIs(duplicate.pixels, first.pixels)

    def test_seconds_and_minute_cadences_return_unchanged_subticks(self):
        seconds = _FixedOverlay(_Controller(), {
            "face": "digital", "show_seconds": True,
        })
        first = seconds.generate_frame(0.0, 0)
        cached = seconds.generate_frame(999.0, 999)
        seconds.fixed_now += timedelta(seconds=1)
        advanced = seconds.generate_frame(999.0, 1000)
        self.assertTrue(first.changed)
        self.assertFalse(cached.changed)
        self.assertTrue(advanced.changed)
        self.assertEqual((first.revision, cached.revision, advanced.revision), (1, 1, 2))

        minutes = _FixedOverlay(_Controller(), {
            "face": "orbit", "show_seconds": False,
        })
        minute_first = minutes.generate_frame(0.0, 0)
        minutes.fixed_now += timedelta(seconds=49)
        minute_cached = minutes.generate_frame(50_000.0, 1)
        minutes.fixed_now += timedelta(seconds=1)
        minute_advanced = minutes.generate_frame(50_001.0, 2)
        self.assertTrue(minute_first.changed)
        self.assertFalse(minute_cached.changed)
        self.assertTrue(minute_advanced.changed)

    def test_second_rollover_dirty_union_covers_old_and_new_clock_pixels(self):
        animation = _FixedOverlay(_Controller(), {
            "face": "digital", "show_seconds": True, "glow": 0.0,
        })
        first = animation.generate_frame(0.0, 0)
        previous = first.pixels.copy()
        animation.fixed_now += timedelta(seconds=1)

        rolled = animation.generate_frame(0.0, 1)

        self.assertTrue(rolled.changed)
        self.assertEqual(rolled.revision, first.revision + 1)
        self.assertEqual(
            rolled.dirty_ranges,
            coverage_dirty_union(previous, rolled.pixels),
        )

    def test_revision_and_dirty_ranges_cover_movement_and_complete_clear(self):
        animation = _FixedOverlay(_Controller(), {
            "face": "digital", "glow": 0.0, "position_y": 0.25,
        })
        first = animation.generate_frame(0.0, 0)
        first_pixels = first.pixels.copy()
        animation.update_parameters({"position_y": 0.75})
        moved = animation.generate_frame(0.0, 1)
        self.assertTrue(moved.changed)
        self.assertEqual(moved.revision, first.revision + 1)
        self.assertEqual(
            moved.dirty_ranges,
            coverage_dirty_union(first_pixels, moved.pixels),
        )

        moved_pixels = moved.pixels.copy()
        animation.update_parameters({"opacity": 0.0})
        cleared = animation.generate_frame(0.0, 2)
        self.assertTrue(cleared.changed)
        self.assertEqual(cleared.revision, moved.revision + 1)
        self.assertFalse(np.any(cleared.pixels))
        self.assertEqual(
            cleared.dirty_ranges,
            coverage_dirty_union(moved_pixels, cleared.pixels),
        )

        animation.update_parameters({"brightness": 0.4})
        still_clear = animation.generate_frame(0.0, 3)
        self.assertFalse(still_clear.changed)
        self.assertEqual(still_clear.revision, cleared.revision)
        self.assertFalse(still_clear.dirty_ranges)

    def test_opaque_black_is_distinct_from_transparency(self):
        black = _FixedOverlay(_Controller(), {
            "brightness": 0.0, "opacity": 1.0, "glow": 0.0,
        }).generate_frame(0.0, 0)
        covered = black.pixels[:, 3] > 0
        self.assertGreater(np.count_nonzero(covered), 0)
        self.assertFalse(np.any(black.pixels[covered, :3]))
        self.assertFalse(np.any(black.pixels[~covered]))

        transparent = _FixedOverlay(_Controller(), {
            "brightness": 0.0, "opacity": 0.0,
        }).generate_frame(0.0, 0)
        self.assertFalse(transparent.changed)
        self.assertEqual(transparent.revision, 0)
        self.assertFalse(np.any(transparent.pixels))

    def test_live_parameters_change_authored_presentation_without_restart(self):
        animation = _FixedOverlay(_Controller(), {
            "face": "digital", "format_24h": False, "palette": "amber",
        })
        original = animation.generate_frame(0.0, 0)
        original_pixels = original.pixels.copy()
        animation.update_parameters({
            "face": "analog", "palette": "ice", "format_24h": True,
            "scale": 3, "glow": 0.9, "brightness": 0.4, "opacity": 0.6,
            "position_y": 0.7, "clock_offset_minutes": 60,
        })
        changed = animation.generate_frame(0.0, 1)
        self.assertTrue(changed.changed)
        self.assertEqual(changed.revision, original.revision + 1)
        self.assertFalse(np.array_equal(changed.pixels, original_pixels))
        self.assertEqual(animation.authored_params["face"], "analog")
        self.assertEqual(animation.authored_params["opacity"], 0.6)

    def test_plant_aware_placement_moves_face_without_applying_optics(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = _FixedOverlay(_ShortController(), {
                "glow": 0.0, "show_seconds": True,
            }).generate_frame(0.0, 0)
            occupied = np.flatnonzero(baseline.pixels[:, 3]).tolist()
            foliage_path = Path(directory) / "foliage.json"
            globe_path = Path(directory) / "globes.json"
            foliage_path.write_text(json.dumps({"covered_indices": occupied}))
            globe_path.write_text(json.dumps({"globe_indices": occupied[:8]}))

            animation = _FixedOverlay(_ShortController(), {
                "glow": 0.0,
                "plant_aware": True,
                "plant_clearance": 1,
                "plant_mask_path": str(foliage_path),
                "plant_globe_mask_path": str(globe_path),
            })
            placed = animation.generate_frame(0.0, 0)
            masks = animation.get_plant_masks()
            alpha = placed.pixels[:, 3] > 0
            self.assertNotEqual(animation._plant_layout_offset, (0, 0))
            self.assertEqual(np.count_nonzero(alpha & masks.obstacle.ravel()), 0)
            # No framework optics were invoked: every non-transparent core mark
            # remains valid premultiplied authored RGBA rather than post-graded RGB.
            self.assertTrue(np.all(placed.pixels[:, :3] <= placed.pixels[:, 3:4]))

    def test_presentation_context_invalidation_does_not_advance_content_revision(self):
        animation = _FixedOverlay(_Controller(), {"palette": "ice"})
        neutral = animation.generate_frame_with_context(_context())
        # Tempo is unsupported and cannot alter a wall-clock overlay. The
        # identity change invalidates the cache, but identical content is not a
        # new revision.
        tempo_only = animation.generate_frame_with_context(_context(tempo=3.0, frame=1))
        self.assertFalse(tempo_only.changed)
        self.assertEqual(tempo_only.revision, neutral.revision)

        vivid = animation.generate_frame_with_context(_context(
            vibe_id="vivid", luminance=0.5, frame=2,
        ))
        self.assertTrue(vivid.changed)
        self.assertEqual(vivid.revision, neutral.revision + 1)
        vivid_pixels = vivid.pixels.copy()

        # Luminance is declared by the component but applied exactly once by
        # the framework after composition. Changing only its context value must
        # not independently dim this premultiplied plane.
        luminance_only = animation.generate_frame_with_context(_context(
            vibe_id="vivid", luminance=1.0, frame=3,
        ))
        self.assertFalse(luminance_only.changed)
        self.assertEqual(luminance_only.revision, vivid.revision)
        np.testing.assert_array_equal(luminance_only.pixels, vivid_pixels)
        self.assertEqual(animation.fixed_now.second, 10)

    def test_existing_clock_curated_presets_remain_full_scene_and_cached(self):
        preset_dir = Path(__file__).resolve().parents[2] / "clock" / "presets"
        preset_files = sorted(preset_dir.glob("*.json"))
        self.assertSetEqual(
            {path.name for path in preset_files}, set(_CLOCK_PRESET_SHA256)
        )
        for preset_path in preset_files:
            with self.subTest(preset=preset_path.name):
                payload = json.loads(preset_path.read_text())
                animation = _FixedClock(_Controller(), payload["params"])
                first = animation.generate_frame(1.25, 0)
                cached = animation.generate_frame(1.25, 1)
                self.assertEqual(first.pixels.shape, (_Controller.total_leds, 3))
                self.assertGreater(int(first.pixels.max()), 0)
                self.assertEqual(
                    hashlib.sha256(first.pixels.tobytes()).hexdigest(),
                    _CLOCK_PRESET_SHA256[preset_path.name],
                )
                self.assertFalse(cached.changed)
                self.assertIs(cached.pixels, first.pixels)


if __name__ == "__main__":
    unittest.main()
