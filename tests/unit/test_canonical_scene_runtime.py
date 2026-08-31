"""Lifecycle and composition contracts for the activated Scene-v1 runtime."""

from __future__ import annotations

from datetime import datetime, timezone
import unittest

import numpy as np

from animation.core.component_catalog import ComponentCatalog
from animation.core.manager import PreviewLEDController
from animation.core.scene_runtime import CanonicalSceneRuntime, CanonicalSceneRuntimeError
from animation.plugins.aurora_curtains import AuroraCurtainsAnimation
from animation.plugins.clock_overlay import ClockOverlayAnimation
from ipc.scene_contract import normalize_composer_scene


FIXED_NOW = datetime(2026, 8, 31, 13, 47, 10, tzinfo=timezone.utc)


def _catalog() -> ComponentCatalog:
    return ComponentCatalog([
        AuroraCurtainsAnimation.component_descriptor(),
        ClockOverlayAnimation.component_descriptor(),
    ])


def _clock(
    slot_id: str,
    *,
    color: list[int],
    enabled: bool = True,
    opacity: int = 255,
    strip_translation: int = 0,
    led_translation: int = 0,
    stale_policy: dict | None = None,
) -> dict:
    return {
        "slot_id": slot_id,
        "component": {
            "component_id": "clock_overlay", "version": 1,
            "provider": "python", "role": "overlay",
            "parameters": {"color": color, "show_seconds": True},
        },
        "enabled": enabled,
        "opacity": opacity,
        "placement": {
            "strip_translation": strip_translation,
            "led_translation": led_translation,
            "clip_policy": "clip_to_wall",
        },
        "stale_policy": stale_policy or {"policy": "hold"},
    }


def _canonical(
    *,
    seed: int = 812,
    vibe: str = "neutral",
    overlays: list[dict] | None = None,
) -> object:
    return normalize_composer_scene({
        "origin": "composer",
        "scene": {
            "schema": "ledgrid.scene.v1",
            "background": {
                "component_id": "aurora_curtains", "version": 1,
                "provider": "python", "role": "background",
                "parameters": {"seed": seed, "source_fps": 20.0},
            },
            "overlays": overlays if overlays is not None else [
                _clock("red", color=[255, 0, 0]),
                _clock("blue", color=[0, 0, 255]),
            ],
            "vibe": vibe,
            "master_brightness": 1.0,
        },
    }, _catalog())


class _CountingAurora(AuroraCurtainsAnimation):
    instances: list["_CountingAurora"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.__class__.instances.append(self)


class _CountingClock(ClockOverlayAnimation):
    instances: list["_CountingClock"] = []

    def __init__(self, controller, config, now_source) -> None:
        self._now_source = now_source
        super().__init__(controller, config)
        self.__class__.instances.append(self)

    def _clock_now(self) -> datetime:
        return self._now_source()


class CanonicalSceneRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        _CountingAurora.instances.clear()
        _CountingClock.instances.clear()
        self.controller = PreviewLEDController(strips=33, leds_per_strip=138)
        self.runtime = CanonicalSceneRuntime(
            self.controller,
            _catalog(),
            background_factory=lambda controller, config: _CountingAurora(controller, config),
            clock_factory=lambda controller, config, clock: _CountingClock(controller, config, clock),
            wall_time_source=lambda: FIXED_NOW,
        )

    def test_frame_and_desired_identity_share_the_same_canonical_basis(self) -> None:
        canonical = _canonical()
        self.assertEqual(self.runtime.activate(canonical), canonical.identity)
        rendered = self.runtime.render(1.0)

        self.assertEqual(self.runtime.desired_identity, canonical.identity)
        self.assertEqual(rendered.basis, canonical.identity)
        self.assertEqual(rendered.pixels.shape, (33 * 138, 3))
        self.assertEqual(rendered.pixels.dtype, np.uint8)
        self.assertTrue(rendered.pixels.flags.c_contiguous)

    def test_presentation_and_slot_edits_retain_unaffected_instances(self) -> None:
        initial = _canonical()
        self.runtime.activate(initial)
        self.runtime.render(1.0)
        aurora, red, blue = _CountingAurora.instances[0], *_CountingClock.instances

        variants = (
            _canonical(vibe="vivid"),
            _canonical(overlays=[
                _clock("blue", color=[0, 0, 255]),
                _clock("red", color=[255, 0, 0]),
            ]),
            _canonical(overlays=[
                _clock("red", color=[255, 0, 0], led_translation=3, opacity=128),
                _clock("blue", color=[0, 0, 255], enabled=False, stale_policy={"policy": "clear_after_lease", "lease_ms": 50}),
            ]),
        )
        for index, scene in enumerate(variants, start=2):
            self.runtime.activate(scene)
            self.runtime.render(float(index))
            self.assertIs(self.runtime._background.animation, aurora)
            self.assertIs(self.runtime._overlays["red"].animation, red)
            self.assertIs(self.runtime._overlays["blue"].animation, blue)
        self.assertEqual((len(_CountingAurora.instances), len(_CountingClock.instances)), (1, 2))

    def test_seed_change_resets_only_background_owner(self) -> None:
        self.runtime.activate(_canonical(seed=812))
        self.runtime.render(1.0)
        red, blue = _CountingClock.instances
        self.runtime.activate(_canonical(seed=813))
        self.runtime.render(1.0)

        self.assertEqual(len(_CountingAurora.instances), 2)
        self.assertIs(self.runtime._overlays["red"].animation, red)
        self.assertIs(self.runtime._overlays["blue"].animation, blue)

    def test_aurora_consumes_wall_pace_once_and_clock_ignores_elapsed(self) -> None:
        slow = _canonical(overlays=[_clock("clock", color=[255, 224, 128])])
        self.runtime.activate(slow)
        self.runtime.render(2.0)
        self.assertEqual(_CountingAurora.instances[-1].get_runtime_stats()["source_tick"], 40)
        first_clock_revision = self.runtime._overlays["clock"].animation._revision

        fast = normalize_composer_scene({
            "origin": "composer",
            "scene": {
                "schema": "ledgrid.scene.v1",
                "background": {
                    "component_id": "aurora_curtains", "version": 1,
                    "provider": "python", "role": "background",
                    "parameters": {"seed": 812, "source_fps": 20.0},
                },
                "overlays": [_clock("clock", color=[255, 224, 128])],
                "custom": {"palette_id": "neutral", "wall_pace": 1.5, "presentation_luminance": 1.0},
                "master_brightness": 1.0,
            },
        }, _catalog())
        self.runtime.activate(fast)
        self.runtime.render(2.0)
        self.assertEqual(_CountingAurora.instances[-1].get_runtime_stats()["source_tick"], 60)
        self.assertEqual(self.runtime._overlays["clock"].animation._revision, first_clock_revision)

    def test_order_is_visible_and_move_disable_remove_clear_coverage(self) -> None:
        first = _canonical()
        self.runtime.activate(first)
        bottom_blue = self.runtime.render(1.0).pixels.copy()
        self.runtime.activate(_canonical(overlays=[
            _clock("blue", color=[0, 0, 255]), _clock("red", color=[255, 0, 0]),
        ]))
        top_red = self.runtime.render(1.0).pixels.copy()
        self.assertFalse(np.array_equal(bottom_blue, top_red))

        background_only = _canonical(overlays=[])
        self.runtime.activate(background_only)
        cleared = self.runtime.render(1.0).pixels.copy()
        self.assertFalse(np.array_equal(top_red, cleared))

        moved_scene = _canonical(overlays=[_clock("red", color=[255, 0, 0], led_translation=5)])
        self.runtime.activate(moved_scene)
        moved = self.runtime.render(1.0).pixels.copy()
        self.runtime.activate(_canonical(overlays=[_clock("red", color=[255, 0, 0], led_translation=5, enabled=False)]))
        disabled = self.runtime.render(1.0).pixels.copy()
        self.assertFalse(np.array_equal(moved, disabled))
        np.testing.assert_array_equal(disabled, cleared)

    def test_inconsistent_or_unactivated_basis_fails_without_guessing(self) -> None:
        with self.assertRaisesRegex(CanonicalSceneRuntimeError, "no canonical"):
            self.runtime.render(0.0)
        canonical = _canonical()
        bad = type(canonical)(scene=canonical.scene, canonical_bytes=b"{}", identity=canonical.identity)
        with self.assertRaisesRegex(CanonicalSceneRuntimeError, "bytes"):
            self.runtime.activate(bad)


if __name__ == "__main__":
    unittest.main()
