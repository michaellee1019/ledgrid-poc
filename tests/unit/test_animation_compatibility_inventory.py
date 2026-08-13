"""Regression tests for the generated Phase 1 plugin inventory."""

from pathlib import Path
import unittest

from animation.core.base import AnimationBase, StatefulAnimationBase
from animation.core.manager import AnimationManager
from animation.core.plugin_loader import AnimationPluginLoader
from tools.generate_animation_compatibility_inventory import (
    COMPATIBILITY_FULL_SCENE,
    DEFAULT_OUTPUT,
    ORDINARY_BACKGROUND,
    PYTHON_OVERLAY,
    UNSUPPORTED_DIRECT_HARDWARE_STATEFUL,
    build_inventory,
    classify_plugin,
    direct_controller_mutations,
    render_markdown,
)


class _Controller:
    strip_count = 1
    leds_per_strip = 1
    total_leds = 1


class _Ordinary(AnimationBase):
    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()


class _DirectHardware(AnimationBase):
    def generate_frame(self, time_elapsed, frame_count):
        frame = self.next_frame_buffer()
        self.controller.set_all_pixels(frame)
        self.controller.show()
        return frame


class _Stateful(StatefulAnimationBase):
    def run_animation(self):
        return None


class AnimationCompatibilityInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = build_inventory()
        cls.by_id = {entry.plugin_id: entry for entry in cls.entries}

    def test_inventory_covers_each_shipped_manifest_and_manager_entry_once(self):
        shipped = AnimationPluginLoader.shipped_plugin_ids()

        self.assertEqual([entry.plugin_id for entry in self.entries], shipped)
        self.assertEqual(len(self.entries), len(set(shipped)))
        self.assertSetEqual(set(shipped), AnimationManager.ALLOWED_PLUGINS)

    def test_current_compatibility_baseline_is_explicit(self):
        self.assertEqual(len(self.entries), 51)
        self.assertEqual(
            self.by_id["clock"].classification, COMPATIBILITY_FULL_SCENE
        )
        ordinary = [
            entry for entry in self.entries
            if entry.classification == ORDINARY_BACKGROUND
        ]
        unsupported = [
            entry for entry in self.entries
            if entry.classification == UNSUPPORTED_DIRECT_HARDWARE_STATEFUL
        ]
        self.assertEqual(len(ordinary), 49)
        overlays = [
            entry for entry in self.entries
            if entry.classification == PYTHON_OVERLAY
        ]
        self.assertEqual([entry.plugin_id for entry in overlays], ["clock_overlay"])
        self.assertEqual(unsupported, [])

    def test_classification_fails_closed_for_direct_or_stateful_ownership(self):
        self.assertEqual(direct_controller_mutations(_Ordinary), ())
        self.assertEqual(
            direct_controller_mutations(_DirectHardware),
            ("set_all_pixels", "show"),
        )
        self.assertEqual(
            classify_plugin("direct", _DirectHardware, "show").classification,
            UNSUPPORTED_DIRECT_HARDWARE_STATEFUL,
        )
        self.assertEqual(
            classify_plugin("stateful", _Stateful, "test").classification,
            UNSUPPORTED_DIRECT_HARDWARE_STATEFUL,
        )
        self.assertEqual(
            classify_plugin(
                "overlay", _Ordinary, "show", role="overlay"
            ).classification,
            PYTHON_OVERLAY,
        )

    def test_committed_human_inventory_is_regeneration_equal(self):
        self.assertEqual(
            Path(DEFAULT_OUTPUT).read_text(encoding="utf-8"),
            render_markdown(self.entries),
        )


if __name__ == "__main__":
    unittest.main()
