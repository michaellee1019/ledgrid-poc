"""Contracts for the in-browser Pyodide animation lane."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np

from animation.browser_preview.python.runtime import (
    BrowserPreviewRuntime,
    ENGINE,
    SUPPORTED_PLUGINS,
)
from animation.core.base import RenderedFrame
from animation.plugins.gradient import GradientAnimation
from animation.plugins.rainbow import RainbowAnimation
from animation.plugins.sparkle import SparkleAnimation
from animation.plugins.wave import WaveAnimation
from tools.build_browser_python_bundle import (
    PYODIDE_VERSION,
    build_archive,
    source_mapping,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_PATH = REPO_ROOT / "web/static/generated/composer/ledgrid_python_runtime.zip"
WORKER_PATH = REPO_ROOT / "web/static/js/composer_python_worker.js"


class _Controller:
    def __init__(self, strip_count: int, leds_per_strip: int):
        self.strip_count = strip_count
        self.leds_per_strip = leds_per_strip
        self.total_leds = strip_count * leds_per_strip


PARITY_CASES = (
    {
        "plugin": "rainbow",
        "class": "RainbowAnimation",
        "params": {
            "plant_aware": True,
            "speed": 0.67,
            "span_ratio": 1.35,
            "direction": -1,
            "brightness": 0.72,
            "color_saturation": 0.81,
            "color_value": 0.93,
        },
        "times": (0.0, 0.125, 0.75),
        "seed": 1201,
    },
    {
        "plugin": "gradient",
        "class": "GradientAnimation",
        "params": {
            "direction": "diagonal",
            "animated": True,
            "speed": -0.38,
            "brightness": 0.64,
            "color1_red": 241,
            "color1_green": 17,
            "color1_blue": 92,
            "color2_red": 8,
            "color2_green": 180,
            "color2_blue": 250,
        },
        "times": (0.0, 0.31, 1.15),
        "seed": 1202,
    },
    {
        "plugin": "wave",
        "class": "WaveAnimation",
        "params": {
            "axis": "horizontal",
            "frequency": 3.7,
            "speed": 0.82,
            "amplitude": 0.73,
            "direction": -1,
            "brightness": 0.58,
            "wave_red": 220,
            "wave_green": 36,
            "wave_blue": 160,
            "background_red": 2,
            "background_green": 9,
            "background_blue": 31,
        },
        "times": (0.0, 0.2, 0.9),
        "seed": 1203,
    },
    {
        "plugin": "sparkle",
        "class": "SparkleAnimation",
        "params": {
            "brightness": 0.77,
            "sparkle_probability": 0.08,
            "fade_speed": 0.83,
            "base_red": 10,
            "base_green": 15,
            "base_blue": 24,
            "sparkle_red": 248,
            "sparkle_green": 190,
            "sparkle_blue": 72,
        },
        "times": (0.0, 0.1, 0.2),
        "seed": 1204,
    },
)

DIRECT_CLASSES = {
    "gradient": GradientAnimation,
    "rainbow": RainbowAnimation,
    "sparkle": SparkleAnimation,
    "wave": WaveAnimation,
}


def _fingerprint(output) -> dict:
    changed = output.changed if isinstance(output, RenderedFrame) else True
    pixels = output.pixels if isinstance(output, RenderedFrame) else output
    canonical = np.asarray(pixels)
    return {
        "changed": bool(changed),
        "dtype": str(canonical.dtype),
        "shape": list(canonical.shape),
        "sha256": hashlib.sha256(canonical.tobytes(order="C")).hexdigest(),
    }


def _direct_fingerprints() -> dict:
    result = {}
    for case in PARITY_CASES:
        np.random.seed(case["seed"])
        animation = DIRECT_CLASSES[case["plugin"]](_Controller(9, 17), case["params"])
        result[case["plugin"]] = [
            _fingerprint(animation.generate_frame(elapsed, frame_index))
            for frame_index, elapsed in enumerate(case["times"])
        ]
    return result


class BrowserPythonBundleTests(unittest.TestCase):
    def test_bundle_is_reproducible_canonical_and_contains_unchanged_plugins(self):
        first = build_archive(REPO_ROOT)
        second = build_archive(REPO_ROOT)
        self.assertEqual(first, second)
        self.assertEqual(BUNDLE_PATH.read_bytes(), first)

        with zipfile.ZipFile(BUNDLE_PATH, "r") as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))
            self.assertTrue(all(info.compress_type == zipfile.ZIP_STORED for info in archive.infolist()))
            for archive_name, source_path in source_mapping(REPO_ROOT).items():
                if archive_name.startswith("animation/plugins/") and archive_name.count("/") == 3:
                    self.assertEqual(archive.read(archive_name), source_path.read_bytes())
            manifest = json.loads(archive.read("ledgrid_browser_manifest.json"))

        self.assertEqual(manifest["pyodideVersion"], PYODIDE_VERSION)
        self.assertEqual(
            {item["pluginId"]: item["className"] for item in manifest["plugins"]},
            dict(SUPPORTED_PLUGINS),
        )
        self.assertTrue(manifest["supportsCalibratedPlantMasks"])
        self.assertTrue(manifest["supportsPlantModifiers"])
        self.assertIn("strip * ledsPerStrip + led", manifest["orientation"])

    def test_worker_protocol_is_pinned_and_has_no_server_frame_endpoint(self):
        worker = WORKER_PATH.read_text(encoding="utf-8")
        self.assertIn(f"const PYODIDE_VERSION = '{PYODIDE_VERSION}'", worker)
        self.assertIn("pyodide.mjs", worker)
        self.assertIn("loadPackage('numpy')", worker)
        self.assertIn("type: 'ready'", worker)
        self.assertIn("type: 'frame'", worker)
        self.assertIn("type: 'error'", worker)
        self.assertIn("pixels: pixels.buffer", worker)
        self.assertIn("[pixels.buffer]", worker)
        self.assertNotIn("/api/preview", worker)

    def test_runtime_reuses_identity_preserves_changed_and_transfers_strip_major(self):
        runtime = BrowserPreviewRuntime()
        params = {
            "direction": "horizontal",
            "animated": False,
            "brightness": 1.0,
            "color1_red": 255,
            "color1_green": 0,
            "color1_blue": 0,
            "color2_red": 0,
            "color2_green": 0,
            "color2_blue": 255,
        }
        ready = runtime.initialize(
            "gradient", "GradientAnimation", {"stripCount": 2, "ledsPerStrip": 3}, params
        )
        identity = id(runtime.animation)
        self.assertTrue(ready["reset"])
        self.assertEqual(ready["engine"], ENGINE)

        first = runtime.render(0.0, 0)
        second = runtime.render(1.0, 1)
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(
            runtime.frame_bytes,
            bytes((255, 0, 0)) * 3 + bytes((0, 0, 255)) * 3,
        )

        same = runtime.initialize(
            "gradient",
            "GradientAnimation",
            {"strip_count": 2, "leds_per_strip": 3},
            {"brightness": 0.5},
        )
        self.assertFalse(same["reset"])
        self.assertEqual(id(runtime.animation), identity)
        self.assertTrue(runtime.render(2.0, 2)["changed"])

        changed_geometry = runtime.initialize(
            "gradient", "GradientAnimation", {"width": 3, "height": 3}, params
        )
        self.assertTrue(changed_geometry["reset"])
        self.assertNotEqual(id(runtime.animation), identity)

    def test_runtime_rejects_unsupported_plugin_and_class(self):
        runtime = BrowserPreviewRuntime()
        with self.assertRaisesRegex(ValueError, "does not support plugin"):
            runtime.initialize("clock", "ClockAnimation", {"width": 3, "height": 4})
        with self.assertRaisesRegex(ValueError, "requires class"):
            runtime.initialize("rainbow", "WaveAnimation", {"width": 3, "height": 4})

    def test_runtime_renders_with_bundled_calibrated_plant_masks(self):
        runtime = BrowserPreviewRuntime()
        runtime.initialize(
            "rainbow",
            "RainbowAnimation",
            {"width": 33, "height": 138},
            {"plant_aware": True, "brightness": 0.7},
        )
        result = runtime.render(0.0, 0)
        self.assertTrue(result["changed"])
        self.assertEqual(len(runtime.frame_bytes), 33 * 138 * 3)

    def test_browser_shim_matches_direct_plugin_frames(self):
        direct = _direct_fingerprints()
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(BUNDLE_PATH, "r") as archive:
                archive.extractall(temp_dir)
            script = r'''
import hashlib
import importlib
import json
import sys

sys.path.insert(0, sys.argv[1])
import numpy as np
from ledgrid_browser_runtime import BrowserPreviewRuntime

cases = json.loads(sys.argv[2])
result = {}
for case in cases:
    np.random.seed(case["seed"])
    runtime = BrowserPreviewRuntime()
    runtime.initialize(
        case["plugin"], case["class"],
        {"stripCount": 9, "ledsPerStrip": 17}, case["params"],
    )
    frames = []
    for frame_index, elapsed in enumerate(case["times"]):
        metadata = runtime.render(elapsed, frame_index)
        frames.append({
            "changed": metadata["changed"],
            "dtype": "uint8",
            "shape": [9 * 17, 3],
            "sha256": hashlib.sha256(runtime.frame_bytes).hexdigest(),
        })
    result[case["plugin"]] = frames
print(json.dumps(result, sort_keys=True))
'''
            completed = subprocess.run(
                [sys.executable, "-c", script, temp_dir, json.dumps(PARITY_CASES)],
                cwd=temp_dir,
                check=True,
                text=True,
                capture_output=True,
            )
        self.assertEqual(json.loads(completed.stdout), direct)


if __name__ == "__main__":
    unittest.main()
