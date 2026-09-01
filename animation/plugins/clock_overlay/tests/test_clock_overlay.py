"""Focused behavior and first-layered-scene coverage for Clock Overlay."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import MappingProxyType
import unittest

import numpy as np

from animation.core.component_catalog import ComponentCatalog, ComponentDescriptor, TimingPolicy
from animation.core.compositing import BaseFrame, HostSceneCompositor, PlacedOverlay
from animation.core.manager import PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import ResolvedScene
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.clock_overlay import ClockOverlayAnimation
from ipc.scene_contract import normalize_composer_scene


class _FixedClock(ClockOverlayAnimation):
    now = datetime(2026, 8, 31, 13, 47, 10, tzinfo=timezone.utc)

    def _clock_now(self) -> datetime:
        return self.now + timedelta(minutes=int(self.params["clock_offset_minutes"]))


class ClockOverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)

    def test_manifest_declares_python_widget_and_loader_discovers_it(self) -> None:
        loader = AnimationPluginLoader()
        self.assertIn("clock_overlay", loader.scan_plugins())
        manifest = loader.plugin_manifests["clock_overlay"]
        self.assertEqual((manifest["provider"], manifest["role"]), ("python", "widget"))
        loaded = loader.load_plugin("clock_overlay")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.__name__, ClockOverlayAnimation.__name__)

    def test_provider_qualified_descriptor_is_a_wall_clock_widget(self) -> None:
        descriptor = ClockOverlayAnimation.component_descriptor()
        self.assertEqual(
            (descriptor.component_id, descriptor.version, descriptor.provider.value, descriptor.role.value),
            ("clock_overlay", 1, "python", "widget"),
        )
        self.assertIs(descriptor.timing_policy, TimingPolicy.WALL_CLOCK)
        self.assertEqual(dict(descriptor.defaults), {
            "format_24h": False,
            "show_seconds": True,
            "clock_offset_minutes": 0,
        })
        self.assertIs(
            ComponentCatalog([descriptor]).require(
                provider="python", component_id="clock_overlay", version=1,
            ),
            descriptor,
        )

    def test_scene_v2_resolves_json_safe_clock_defaults(self) -> None:
        catalog = ComponentCatalog([
            ComponentDescriptor(
                "native", 1, "receiver_native", "background", "scaled_context", "none", "preserve",
                ("final_optics",), ("native_preview",), defaults={"bundle_digest": "a" * 64},
            ),
            ComponentDescriptor(
                "aurora", 1, "python", "animation", "scaled_context", "premultiplied_rgba",
                "semantic", ("none",), (),
            ),
            ClockOverlayAnimation.component_descriptor(),
        ])
        canonical = normalize_composer_scene({
            "origin": "composer",
            "scene": {
                "schema": "ledgrid.scene.v2",
                "background": {
                    "component_id": "native", "version": 1,
                    "provider": "receiver_native", "role": "background", "parameters": {},
                    "bundle_digest": "a" * 64,
                },
                "animation": {
                    "component_id": "aurora", "version": 1,
                    "provider": "python", "role": "animation", "parameters": {},
                },
                "widgets": [{
                    "id": "clock",
                    "component": {
                        "component_id": "clock_overlay", "version": 1,
                        "provider": "python", "role": "widget", "parameters": {},
                    },
                    "visible": True, "placement": {"mode": "auto"},
                }],
                "plants": {"effects": {"version": 1, "active": [], "strengths": {}}},
                "look": {"palette_id": "mist", "pace": 2.0, "presentation_brightness": 1.0},
            },
        }, catalog)
        parameters = canonical.scene["widgets"][0]["component"]["parameters"]
        self.assertEqual(parameters, {
            "format_24h": False,
            "show_seconds": True,
            "clock_offset_minutes": 0,
        })
        self.assertNotIn("color", _FixedClock(self.controller).params)

    def test_fixed_time_frame_is_canonical_premultiplied_rgba8(self) -> None:
        frame = _FixedClock(self.controller).generate_frame(4_000.0, 800_000)
        self.assertEqual(frame.pixels.shape, (33 * 138, 4))
        self.assertEqual(frame.pixels.dtype, np.uint8)
        self.assertTrue(frame.pixels.flags.c_contiguous)
        self.assertTrue(np.all(frame.pixels[:, :3] <= frame.pixels[:, 3:4]))
        self.assertGreater(np.count_nonzero(frame.pixels[:, 3]), 0)

    def test_current_only_parameters_reject_legacy_and_validate_values(self) -> None:
        clock = _FixedClock(self.controller)
        self.assertEqual(set(clock.params), {"format_24h", "show_seconds", "clock_offset_minutes"})
        self.assertNotIn("speed", clock.params)
        self.assertNotIn("brightness", clock.params)
        self.assertNotIn("plant_aware", clock.params)
        with self.assertRaisesRegex(ValueError, "non-local"):
            _FixedClock(self.controller, {"speed": 2.0})
        with self.assertRaisesRegex(ValueError, "non-local"):
            clock.update_parameters({"brightness": 0.5})
        for config, message in (
            ({"show_seconds": 1}, "show_seconds must be a bool"),
            ({"clock_offset_minutes": 841}, "clock_offset_minutes"),
            ({"color": (255, 0)}, "color must be"),
            ({"color": (256, 0, 0)}, "color must be"),
        ):
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, message):
                _FixedClock(self.controller, config)

    def test_valid_live_update_invalidates_cached_plane_without_legacy_state(self) -> None:
        clock = _FixedClock(self.controller)
        first = clock.generate_frame(0.0, 0)
        clock.update_parameters({"format_24h": True})
        changed = clock.generate_frame(1000.0, 200_000)
        self.assertTrue(changed.changed)
        self.assertGreater(changed.revision, first.revision)
        self.assertEqual(set(clock.params), {"format_24h", "show_seconds", "clock_offset_minutes"})

    def test_rejected_live_update_keeps_the_current_clock_configuration_and_cache(self) -> None:
        clock = _FixedClock(self.controller, {"format_24h": True, "show_seconds": False})
        first = clock.generate_frame(0.0, 0)
        before = dict(clock.params)
        with self.assertRaisesRegex(ValueError, "clock_offset_minutes"):
            clock.update_parameters({"clock_offset_minutes": 841})
        self.assertEqual(clock.params, before)
        cached = clock.generate_frame(500.0, 100_000)
        self.assertFalse(cached.changed)
        self.assertIs(cached.pixels, first.pixels)

    def test_fixed_wall_time_changes_clock_pixels_only_with_the_semantic_palette(self) -> None:
        clock = _FixedClock(self.controller, {"color": [1, 2, 3]})
        descriptor = clock.component_descriptor()

        def context(palette_id: str) -> ResolvedScene:
            return ResolvedScene(
                canonical_scene=MappingProxyType({}), canonical_bytes=b"clock-test", digest="0" * 64,
                descriptor=descriptor, parameters=MappingProxyType({}),
                palette=MappingProxyType({"palette_id": palette_id}), phase_time=0.0,
                plant_inputs=MappingProxyType({}),
            )

        clock.set_presentation_context(context("mist"))
        mist = clock.generate_frame(0.0, 0)
        clock.set_presentation_context(context("ember"))
        ember = clock.generate_frame(0.0, 0)

        np.testing.assert_array_equal(mist.pixels[:, 3], ember.pixels[:, 3])
        self.assertFalse(np.array_equal(mist.pixels[:, :3], ember.pixels[:, :3]))
        self.assertNotIn("color", clock.params)

    def test_seconds_and_minute_caches_follow_only_wall_time_boundaries(self) -> None:
        clock = _FixedClock(self.controller)
        first = clock.generate_frame(0.0, 0)
        cached = clock.generate_frame(9_999.0, 1_999_800)
        self.assertTrue(first.changed)
        self.assertFalse(cached.changed)
        self.assertIs(first.pixels, cached.pixels)

        clock.now += timedelta(seconds=1)
        second = clock.generate_frame(0.0, 0)
        self.assertTrue(second.changed)
        self.assertGreater(second.revision, first.revision)

        minute_clock = _FixedClock(self.controller, {"show_seconds": False})
        minute_first = minute_clock.generate_frame(0.0, 0)
        minute_clock.now += timedelta(seconds=49)
        self.assertFalse(minute_clock.generate_frame(500.0, 100_000).changed)
        minute_clock.now += timedelta(seconds=1)
        minute_next = minute_clock.generate_frame(0.0, 0)
        self.assertTrue(minute_next.changed)
        self.assertGreater(minute_next.revision, minute_first.revision)

    def test_composed_clock_does_not_change_aurora_semantics_or_leave_coverage(self) -> None:
        aurora = AuroraCurtainsAnimation(self.controller, {"seed": 812, "source_fps": 20.0})
        background = aurora.generate_frame(1.0, 0)
        semantic_before = aurora.semantic_snapshot()
        clock = _FixedClock(self.controller)
        first = clock.generate_frame(0.0, 0)
        compositor = HostSceneCompositor(33, 138)
        composed = compositor.compose(BaseFrame(background.pixels), (PlacedOverlay(first),))
        self.assertFalse(np.array_equal(composed.pixels, background.pixels))

        clock.now += timedelta(seconds=1)
        tick = clock.generate_frame(999.0, 9_999)
        self.assertTrue(tick.changed)
        self.assertEqual(semantic_before, aurora.semantic_snapshot())

        moved = compositor.compose(BaseFrame(background.pixels, changed=False), (PlacedOverlay(tick, led_offset=5),))
        previous_coverage = first.pixels[:, 3] > 0
        self.assertTrue(np.array_equal(moved.pixels[previous_coverage & (tick.pixels[:, 3] == 0)], background.pixels[previous_coverage & (tick.pixels[:, 3] == 0)]))

        disabled = compositor.compose(BaseFrame(background.pixels, changed=False), (PlacedOverlay(tick, led_offset=5, enabled=False),))
        np.testing.assert_array_equal(disabled.pixels, background.pixels)
        removed = compositor.compose(BaseFrame(background.pixels, changed=False), ())
        np.testing.assert_array_equal(removed.pixels, background.pixels)


if __name__ == "__main__":
    unittest.main()
