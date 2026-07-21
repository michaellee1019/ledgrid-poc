from pathlib import Path
import unittest

from animation.core.manager import AnimationManager
from web.app import create_app


class PluginRegistryTests(unittest.TestCase):
    def test_allowlist_matches_shipped_plugins(self):
        plugins_dir = Path(__file__).resolve().parents[2] / "animation" / "plugins"
        shipped_plugins = {
            path.stem
            for path in plugins_dir.glob("*.py")
            if not path.name.startswith("__")
        }

        self.assertSetEqual(AnimationManager.ALLOWED_PLUGINS, shipped_plugins)

    def test_live_global_speed_scale_preserves_relative_animation_speed(self):
        class _Animation:
            def __init__(self):
                self.params = {'speed': 0.4}

            def update_parameters(self, params):
                self.params.update(params)

        manager = AnimationManager.__new__(AnimationManager)
        manager.animation_speed_scale = 0.2
        manager.current_animation = _Animation()

        applied = manager.set_animation_speed_scale(0.3)

        self.assertAlmostEqual(applied, 0.3)
        self.assertAlmostEqual(manager.current_animation.params['speed'], 0.6)

    def test_live_global_speed_scale_rejects_non_finite_values(self):
        manager = AnimationManager.__new__(AnimationManager)
        manager.animation_speed_scale = 0.3
        manager.current_animation = None

        with self.assertRaises(ValueError):
            manager.set_animation_speed_scale(float('inf'))

    def test_default_animation_starts_with_saved_parameters(self):
        class _Controller:
            strip_count = 1
            leds_per_strip = 4
            total_leds = 4
            debug = False

            def configure(self):
                pass

            def set_all_pixels(self, _frame):
                pass

            def show(self):
                pass

            def clear(self):
                pass

        manager = AnimationManager(
            _Controller(),
            default_animation='solid',
            default_animation_config={
                'red': 12, 'green': 34, 'blue': 56, 'brightness': 0.7,
            },
        )
        self.addCleanup(manager.stop_animation)

        self.assertEqual(manager.current_animation_name, 'solid')
        self.assertEqual(manager.current_animation.params['red'], 12)
        self.assertEqual(manager.current_animation.params['green'], 34)
        self.assertEqual(manager.current_animation.params['blue'], 56)

    def test_preview_manager_can_load_plugins_without_starting_a_thread(self):
        class _Controller:
            strip_count = 1
            leds_per_strip = 4
            total_leds = 4
            debug = False

        manager = AnimationManager(_Controller(), auto_start=False)

        self.assertIsNone(manager.current_animation)
        self.assertIsNone(manager.animation_thread)
        self.assertFalse(manager.is_running)

    def test_web_factory_keeps_preview_manager_idle(self):
        interface = create_app(strips=1, leds_per_strip=4)

        self.assertIsNone(interface.preview_manager.current_animation)
        self.assertIsNone(interface.preview_manager.animation_thread)
        self.assertFalse(interface.preview_manager.is_running)
