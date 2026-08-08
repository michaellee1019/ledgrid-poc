from pathlib import Path
import unittest

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT
from web.app import create_app


class PluginRegistryTests(unittest.TestCase):
    def test_allowlist_matches_shipped_plugins(self):
        loader = AnimationPluginLoader()
        shipped_plugins = loader.scan_plugins()

        self.assertEqual(shipped_plugins, sorted(shipped_plugins))
        self.assertSetEqual(AnimationManager.ALLOWED_PLUGINS, set(shipped_plugins))
        for plugin_id in shipped_plugins:
            with self.subTest(plugin=plugin_id):
                plugin_dir = loader.get_plugin_dir(plugin_id)
                self.assertEqual(plugin_dir.name, plugin_id)
                self.assertTrue((plugin_dir / "manifest.json").is_file())
                self.assertTrue((plugin_dir / "tests").is_dir())

    def test_public_plugin_package_imports_remain_compatible(self):
        import importlib

        loader = AnimationPluginLoader()
        for plugin_id in loader.scan_plugins():
            with self.subTest(plugin=plugin_id):
                loaded = loader.load_plugin(plugin_id)
                module = importlib.import_module(f"animation.plugins.{plugin_id}")
                self.assertIsNotNone(loaded)
                self.assertIs(loaded, getattr(module, loaded.__name__))

    def test_manifest_rejects_package_id_drift(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as temporary_dir:
            plugin_dir = Path(temporary_dir) / "right_id"
            plugin_dir.mkdir()
            (plugin_dir / "__init__.py").write_text("", encoding="utf-8")
            (plugin_dir / "manifest.json").write_text(json.dumps({
                "plugin_id": "wrong_id",
                "class": "ExampleAnimation",
                "icon": "✨",
                "gallery": "show",
            }), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "must match package directory"):
                AnimationPluginLoader(temporary_dir).scan_plugins()

    def test_manifest_rejects_animation_class_drift(self):
        import json
        import tempfile

        source = """
from animation import AnimationBase
class ActualAnimation(AnimationBase):
    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()
"""
        with tempfile.TemporaryDirectory() as temporary_dir:
            plugin_dir = Path(temporary_dir) / "example"
            plugin_dir.mkdir()
            (plugin_dir / "__init__.py").write_text(source, encoding="utf-8")
            (plugin_dir / "manifest.json").write_text(json.dumps({
                "plugin_id": "example",
                "class": "DifferentAnimation",
                "icon": "✨",
                "gallery": "show",
            }), encoding="utf-8")
            loader = AnimationPluginLoader(temporary_dir)
            loader.scan_plugins()

            self.assertIsNone(loader.load_plugin("example"))

    def test_external_flat_plugin_files_remain_supported(self):
        import tempfile

        source = """
from animation import AnimationBase
class ExternalAnimation(AnimationBase):
    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()
"""
        with tempfile.TemporaryDirectory() as temporary_dir:
            plugin_path = Path(temporary_dir) / "external.py"
            plugin_path.write_text(source, encoding="utf-8")
            loader = AnimationPluginLoader(temporary_dir)

            self.assertEqual(loader.scan_plugins(), ["external"])
            loaded = loader.load_plugin("external")
            self.assertEqual(loaded.__name__, "ExternalAnimation")
            self.assertEqual(loaded.__module__, "external")

    def test_all_plugins_render_the_installed_32_by_138_geometry(self):
        controller = PreviewLEDController(
            strips=DEFAULT_STRIP_COUNT,
            leds_per_strip=DEFAULT_LEDS_PER_STRIP,
        )
        plugins = AnimationPluginLoader(
            allowed_plugins=AnimationManager.ALLOWED_PLUGINS,
        ).load_all_plugins()

        self.assertEqual(DEFAULT_LEDS_PER_STRIP, 138)
        self.assertSetEqual(set(plugins), AnimationManager.ALLOWED_PLUGINS)
        for name, plugin_class in sorted(plugins.items()):
            with self.subTest(plugin=name):
                rendered = plugin_class(controller).generate_frame(0.0, 0)
                pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
                self.assertIsInstance(pixels, np.ndarray)
                self.assertEqual(pixels.shape, (DEFAULT_STRIP_COUNT * 138, 3))
                self.assertEqual(pixels.dtype, np.uint8)

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

    def test_global_plant_aware_updates_live_and_rejects_non_boolean_values(self):
        class _Animation:
            def __init__(self):
                self.params = {'plant_aware': True}

            def update_parameters(self, params):
                self.params.update(params)

        manager = AnimationManager.__new__(AnimationManager)
        manager.plant_aware = True
        manager.current_animation = _Animation()

        self.assertFalse(manager.set_plant_aware(False))
        self.assertFalse(manager.current_animation.params['plant_aware'])
        with self.assertRaises(ValueError):
            manager.set_plant_aware('yes')

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
        self.assertTrue(manager.current_animation.params['plant_aware'])

        manager.set_plant_aware(False)
        self.assertTrue(manager.start_animation('solid', {'plant_aware': True, 'red': 9}))
        self.assertFalse(manager.current_animation.params['plant_aware'])
        self.assertEqual(manager.current_animation.params['red'], 9)

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

    def test_composable_modifier_authority_overrides_start_and_live_params(self):
        class _Controller:
            strip_count = 4
            leds_per_strip = 8
            total_leds = 32
            debug = False

            def configure(self): pass
            def set_all_pixels(self, _frame): pass
            def show(self): pass
            def clear(self): pass

        state = {"active": ["shadow"], "strengths": {"shadow": 0.7}}
        manager = AnimationManager(
            _Controller(), plant_modifiers=state, auto_start=False
        )
        self.addCleanup(manager.stop_animation)
        self.assertTrue(manager.start_animation("gradient", {
            "plant_aware": True,
            "plant_modifiers": {"active": ["refract"]},
        }))
        self.assertFalse(manager.current_animation.params["plant_aware"])
        self.assertEqual(
            manager.current_animation.plant_modifier_state().active, ("shadow",)
        )

        frame_count = manager.frame_count
        manager.update_animation_parameters({
            "plant_modifiers": {"active": ["refract"]}, "brightness": 0.4,
        })
        self.assertEqual(
            manager.current_animation.plant_modifier_state().active, ("shadow",)
        )
        self.assertEqual(manager.frame_count, frame_count)
        status = manager.get_current_status()
        self.assertEqual(status["plant_modifiers"]["active"], ["shadow"])
        self.assertIn("shadow", status["plant_modifier_support"])

    def test_web_factory_keeps_preview_manager_idle(self):
        interface = create_app(strips=1, leds_per_strip=4)

        self.assertIsNone(interface.preview_manager.current_animation)
        self.assertIsNone(interface.preview_manager.animation_thread)
        self.assertFalse(interface.preview_manager.is_running)

    def test_web_lists_plugin_owned_curated_presets_and_manifest_metadata(self):
        interface = create_app(strips=1, leds_per_strip=4)
        client = interface.app.test_client()

        presets = client.get('/api/animations/clock/presets').get_json()['presets']
        animations = client.get('/api/animations').get_json()
        catalog = {item['plugin_name']: item for item in animations}

        self.assertGreaterEqual(len(presets), 20)
        self.assertEqual(catalog['clock']['emoji'], '🕰️')
        self.assertFalse(catalog['clock']['is_test'])
        self.assertTrue(catalog['strip_order']['is_test'])
