"""Validate every animation preset that is present in the repository checkout."""

import json
from pathlib import Path
import unittest

import numpy as np

from animation.core.base import RenderedFrame
from animation.core.manager import AnimationManager, PreviewLEDController
from animation.core.plugin_loader import AnimationPluginLoader
from animation.core.presentation_contracts import OverlayFrame
from drivers.led_layout import DEFAULT_LEDS_PER_STRIP, DEFAULT_STRIP_COUNT


ROOT = Path(__file__).resolve().parents[2]
CLOCK_CONVERSION_MANIFEST = (
    ROOT / "animation/plugins/clock_overlay/clock_preset_conversion.v1.json"
)
CLOCK_OVERLAY_PRESET_DIR = ROOT / "animation/plugins/clock_overlay/presets"
LEGACY_CLOCK_PRESET_DIR = ROOT / "animation/plugins/clock/presets"


class CuratedAnimationPresetTests(unittest.TestCase):
    _CLOCK_OVERLAY_PARAMETERS = frozenset(
        {
            "brightness",
            "clock_offset_minutes",
            "face",
            "format_24h",
            "glow",
            "palette",
            "plant_aware",
            "position_y",
            "scale",
            "show_seconds",
        }
    )
    _REJECTED_CLOCK_PARAMETERS = (
        "background",
        "motion",
        "density",
        "speed",
    )

    @classmethod
    def setUpClass(cls):
        cls.loader = AnimationPluginLoader(
            allowed_plugins=AnimationManager.ALLOWED_PLUGINS
        )
        cls.plugins = cls.loader.load_all_plugins()

    def test_all_present_presets_match_plugin_schemas_and_render(self):
        paths = list(self.loader.iter_curated_preset_files())
        self.assertGreaterEqual(len(paths), 70)

        controller = PreviewLEDController(
            strips=DEFAULT_STRIP_COUNT,
            leds_per_strip=DEFAULT_LEDS_PER_STRIP,
        )
        for path in paths:
            with self.subTest(preset=str(path)):
                payload = json.loads(path.read_text(encoding="utf-8"))
                animation_name = payload["animation"]
                self.assertEqual(payload["preset_id"], path.stem)
                self.assertEqual(animation_name, path.parents[1].name)
                self.assertIsInstance(payload.get("params"), dict)
                self.assertIs(payload["params"].get("plant_aware"), True)
                self.assertIn(animation_name, self.plugins)

                animation = self.plugins[animation_name](
                    controller, payload["params"]
                )
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
                pixels = (
                    rendered.pixels
                    if isinstance(rendered, (RenderedFrame, OverlayFrame))
                    else rendered
                )
                self.assertIsInstance(pixels, np.ndarray)
                if isinstance(rendered, OverlayFrame):
                    self.assertEqual(
                        pixels.shape,
                        (DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP, 4),
                    )
                    self.assertTrue(np.all(pixels[:, :3] <= pixels[:, 3:4]))
                    continue
                self.assertEqual(
                    pixels.shape,
                    (DEFAULT_STRIP_COUNT * DEFAULT_LEDS_PER_STRIP, 3),
                )
                self.assertEqual(pixels.dtype, np.uint8)

    def test_clock_conversion_manifest_maps_each_legacy_preset_to_overlay(self):
        manifest = json.loads(CLOCK_CONVERSION_MANIFEST.read_text(encoding="utf-8"))

        self.assertEqual(manifest["schema"], "ledgrid.clock-preset-conversion")
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["policy"]["source_component"], "clock")
        self.assertEqual(manifest["policy"]["target_component"], "clock_overlay")
        self.assertEqual(
            manifest["policy"]["rejected_legacy_parameters"],
            list(self._REJECTED_CLOCK_PARAMETERS),
        )

        legacy_paths = sorted(LEGACY_CLOCK_PRESET_DIR.glob("*.json"))
        entries = manifest["entries"]
        self.assertEqual(len(entries), 24)
        self.assertEqual(
            [entry["source_preset_id"] for entry in entries],
            [path.stem for path in legacy_paths],
        )

        for entry, source_path in zip(entries, legacy_paths):
            with self.subTest(source=source_path.name):
                source = json.loads(source_path.read_text(encoding="utf-8"))
                target_path = (
                    CLOCK_OVERLAY_PRESET_DIR
                    / f"{entry['target_preset_id']}.json"
                )
                target = json.loads(target_path.read_text(encoding="utf-8"))

                self.assertEqual(entry["status"], "converted")
                self.assertEqual(entry["source_preset_id"], source["preset_id"])
                self.assertEqual(entry["target_preset_id"], source["preset_id"])
                self.assertEqual(
                    entry["preserved_parameters"],
                    sorted(self._CLOCK_OVERLAY_PARAMETERS),
                )
                self.assertEqual(
                    [
                        item["parameter"]
                        for item in entry["rejected_legacy_parameters"]
                    ],
                    list(self._REJECTED_CLOCK_PARAMETERS),
                )
                self.assertTrue(
                    all(
                        item["reason"].endswith("selected by Composer")
                        or "background" in item["reason"]
                        for item in entry["rejected_legacy_parameters"]
                    )
                )

                self.assertEqual(target["animation"], "clock_overlay")
                self.assertEqual(target["preset_id"], source["preset_id"])
                self.assertEqual(
                    set(target["params"]), self._CLOCK_OVERLAY_PARAMETERS
                )
                for name in self._CLOCK_OVERLAY_PARAMETERS:
                    self.assertEqual(target["params"][name], source["params"][name])
                self.assertTrue(
                    set(source["params"]).issuperset(
                        self._REJECTED_CLOCK_PARAMETERS
                    )
                )


if __name__ == "__main__":
    unittest.main()
