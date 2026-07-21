"""Validate every animation preset that is present in the repository checkout."""

import json
import unittest
from pathlib import Path

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader


class CuratedAnimationPresetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(__file__).resolve().parents[2]
        loader = AnimationPluginLoader(allowed_plugins=AnimationManager.ALLOWED_PLUGINS)
        cls.plugins = loader.load_all_plugins()

    def test_all_present_presets_match_plugin_schemas_and_render(self):
        paths = sorted((self.root / "presets" / "animations").glob("*/*.json"))
        self.assertGreaterEqual(len(paths), 70)

        controller = PreviewLEDController(strips=32, leds_per_strip=140)
        for path in paths:
            with self.subTest(preset=str(path.relative_to(self.root))):
                payload = json.loads(path.read_text(encoding="utf-8"))
                animation_name = payload["animation"]
                self.assertEqual(payload["preset_id"], path.stem)
                self.assertEqual(animation_name, path.parent.name)
                self.assertIsInstance(payload.get("params"), dict)
                self.assertIn(animation_name, self.plugins)

                animation = self.plugins[animation_name](controller, payload["params"])
                schema = animation.get_parameter_schema()
                for name, value in payload["params"].items():
                    self.assertIn(name, schema, f"unsupported parameter {name}")
                    definition = schema[name]
                    if "options" in definition:
                        self.assertIn(value, definition["options"])
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        if "min" in definition:
                            self.assertGreaterEqual(value, definition["min"])
                        if "max" in definition:
                            self.assertLessEqual(value, definition["max"])

                rendered = animation.generate_frame(0.0, 0)
                pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
                self.assertIsInstance(pixels, np.ndarray)
                self.assertEqual(pixels.shape, (4480, 3))
                self.assertEqual(pixels.dtype, np.uint8)


if __name__ == "__main__":
    unittest.main()
