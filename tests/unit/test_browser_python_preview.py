"""Core contracts for the all-catalog in-browser Pyodide animation lane."""

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
    PLUGIN_SPECS,
    SUPPORTED_PLUGINS,
)
from animation.core.base import RenderedFrame
from animation.core.plugin_loader import AnimationPluginLoader
from animation.plugins.gradient import GradientAnimation
from animation.plugins.rainbow import RainbowAnimation
from animation.plugins.sparkle import SparkleAnimation
from animation.plugins.wave import WaveAnimation
from tools.build_browser_python_bundle import (
    PYODIDE_VERSION,
    build_archive,
    discover_python_plugins,
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
        self.debug = False


PARITY_CASES = (
    {
        "plugin": "rainbow", "class": "RainbowAnimation", "seed": 1201,
        "params": {"plant_aware": True, "speed": 0.67, "span_ratio": 1.35,
                   "direction": -1, "brightness": 0.72,
                   "color_saturation": 0.81, "color_value": 0.93},
        "times": (0.0, 0.125, 0.75),
    },
    {
        "plugin": "gradient", "class": "GradientAnimation", "seed": 1202,
        "params": {"direction": "diagonal", "animated": True, "speed": -0.38,
                   "brightness": 0.64, "color1_red": 241, "color1_green": 17,
                   "color1_blue": 92, "color2_red": 8, "color2_green": 180,
                   "color2_blue": 250},
        "times": (0.0, 0.31, 1.15),
    },
    {
        "plugin": "wave", "class": "WaveAnimation", "seed": 1203,
        "params": {"axis": "horizontal", "frequency": 3.7, "speed": 0.82,
                   "amplitude": 0.73, "direction": -1, "brightness": 0.58,
                   "wave_red": 220, "wave_green": 36, "wave_blue": 160,
                   "background_red": 2, "background_green": 9,
                   "background_blue": 31},
        "times": (0.0, 0.2, 0.9),
    },
    {
        "plugin": "sparkle", "class": "SparkleAnimation", "seed": 1204,
        "params": {"brightness": 0.77, "sparkle_probability": 0.08,
                   "fade_speed": 0.83, "base_red": 10, "base_green": 15,
                   "base_blue": 24, "sparkle_red": 248,
                   "sparkle_green": 190, "sparkle_blue": 72},
        "times": (0.0, 0.1, 0.2),
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
    def test_bundle_is_reproducible_complete_and_contains_unchanged_catalog(self):
        first = build_archive(REPO_ROOT)
        second = build_archive(REPO_ROOT)
        self.assertEqual(first, second)
        self.assertEqual(BUNDLE_PATH.read_bytes(), first)

        with zipfile.ZipFile(BUNDLE_PATH, "r") as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertTrue(all(
                info.date_time == (1980, 1, 1, 0, 0, 0)
                for info in archive.infolist()
            ))
            self.assertTrue(all(
                info.compress_type == zipfile.ZIP_DEFLATED
                for info in archive.infolist()
            ))
            for archive_name, source_path in source_mapping(REPO_ROOT).items():
                self.assertEqual(archive.read(archive_name), source_path.read_bytes())
            manifest = json.loads(archive.read("ledgrid_browser_manifest.json"))

        loader = AnimationPluginLoader()
        authoritative_ids = loader.scan_plugins()
        discovered = discover_python_plugins(REPO_ROOT)
        self.assertEqual([item.plugin_id for item in discovered], authoritative_ids)
        self.assertEqual(len(discovered), 51)
        self.assertEqual(
            {item["pluginId"]: item["className"] for item in manifest["plugins"]},
            dict(SUPPORTED_PLUGINS),
        )
        self.assertEqual(manifest["pyodideVersion"], PYODIDE_VERSION)
        self.assertEqual(manifest["formatVersion"], 2)
        self.assertTrue(manifest["supportsCalibratedPlantMasks"])
        self.assertTrue(manifest["supportsPlantModifiers"])
        self.assertTrue(manifest["supportsMultipleInstances"])
        self.assertTrue(manifest["supportsFixedWallTime"])
        self.assertIn("strip * ledsPerStrip + led", manifest["orientation"])
        self.assertEqual(
            len([name for name in names if name.startswith(
                "animation/plugins/gif_animation/assets/"
            )]),
            36,
        )
        gif_spec = next(
            item for item in manifest["plugins"] if item["pluginId"] == "gif_animation"
        )
        self.assertEqual(gif_spec["requiredPackages"], ["pillow"])
        overlay_spec = next(
            item for item in manifest["plugins"] if item["pluginId"] == "clock_overlay"
        )
        self.assertEqual(overlay_spec["role"], "overlay")
        self.assertEqual(overlay_spec["frameFormat"], "premultiplied-rgba")

    def test_worker_protocol_is_pinned_multi_instance_and_has_no_server_endpoint(self):
        worker = WORKER_PATH.read_text(encoding="utf-8")
        self.assertIn(f"const PYODIDE_VERSION = '{PYODIDE_VERSION}'", worker)
        self.assertIn("pyodide.mjs", worker)
        self.assertIn("loadPackage('numpy')", worker)
        self.assertIn("loadPackage('pillow')", worker)
        self.assertIn("message.instanceId || 'primary'", worker)
        self.assertIn("frameFormat: result.frameFormat", worker)
        self.assertIn("wallTime: message.wallTime ?? null", worker)
        self.assertIn("type: 'ready'", worker)
        self.assertIn("type: 'frame'", worker)
        self.assertIn("type: 'error'", worker)
        self.assertIn("type: 'disposed'", worker)
        self.assertIn("message.type === 'dispose'", worker)
        self.assertIn("pixels: pixels.buffer", worker)
        self.assertIn("[pixels.buffer]", worker)
        self.assertNotIn("/api/preview", worker)

    def test_runtime_reuses_identity_and_keeps_instances_independent(self):
        runtime = BrowserPreviewRuntime()
        params = {
            "direction": "horizontal", "animated": False, "brightness": 1.0,
            "color1_red": 255, "color1_green": 0, "color1_blue": 0,
            "color2_red": 0, "color2_green": 0, "color2_blue": 255,
        }
        ready = runtime.initialize(
            "gradient", "GradientAnimation", {"stripCount": 2, "ledsPerStrip": 3},
            params,
        )
        identity = id(runtime.animation)
        self.assertTrue(ready["reset"])
        self.assertEqual(ready["engine"], ENGINE)
        self.assertEqual(ready["instanceId"], "primary")
        self.assertEqual(ready["frameFormat"], "rgb")

        first = runtime.render(0.0, 0)
        second = runtime.render(1.0, 1)
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        self.assertEqual(
            runtime.frame_bytes,
            bytes((255, 0, 0)) * 3 + bytes((0, 0, 255)) * 3,
        )

        overlay = runtime.initialize(
            "clock_overlay", "ClockOverlayAnimation", {"width": 2, "height": 3},
            {"face": "minimal", "show_seconds": True}, instance_id="overlay",
        )
        self.assertTrue(overlay["reset"])
        self.assertEqual(overlay["frameFormat"], "premultiplied-rgba")
        runtime.render(0.0, 0, instance_id="overlay", wall_time=1787774400.0)
        overlay_bytes = runtime.frame_bytes_for("overlay")
        self.assertEqual(len(overlay_bytes), 2 * 3 * 4)
        rgba = np.frombuffer(overlay_bytes, dtype=np.uint8).reshape(-1, 4)
        self.assertTrue(np.all(rgba[:, :3] <= rgba[:, 3:4]))
        self.assertEqual(id(runtime.animation), identity)

        disposed = runtime.dispose_instance("overlay")
        self.assertTrue(disposed["disposed"])
        self.assertEqual(disposed["remainingInstances"], 1)
        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            runtime.frame_bytes_for("overlay")
        self.assertEqual(id(runtime.animation), identity)
        self.assertFalse(runtime.dispose_instance("overlay")["disposed"])

        same = runtime.initialize(
            "gradient", "GradientAnimation", {"strip_count": 2, "leds_per_strip": 3},
            {"brightness": 0.5},
        )
        self.assertFalse(same["reset"])
        self.assertEqual(id(runtime.animation), identity)
        self.assertTrue(runtime.render(2.0, 2)["changed"])

    def test_overlay_fixed_wall_clock_is_deterministic_and_preserves_changed(self):
        runtime = BrowserPreviewRuntime()
        ready = runtime.initialize(
            "clock_overlay", "ClockOverlayAnimation", {"width": 33, "height": 138},
            {"face": "digital", "show_seconds": True},
        )
        self.assertEqual(ready["role"], "overlay")
        first = runtime.render(0.0, 0, wall_time=1787774400.0)
        first_bytes = runtime.frame_bytes
        repeated = runtime.render(1.0, 1, wall_time=1787774400.0)
        self.assertFalse(repeated["changed"])
        self.assertEqual(runtime.frame_bytes, first_bytes)
        advanced = runtime.render(2.0, 2, wall_time=1787774401.0)
        self.assertTrue(advanced["wallClockFixed"])
        self.assertEqual(advanced["frameFormat"], "premultiplied-rgba")
        self.assertTrue(advanced["changed"])

    def test_runtime_rejects_unknown_plugin_class_and_instance(self):
        runtime = BrowserPreviewRuntime()
        with self.assertRaisesRegex(ValueError, "does not support plugin"):
            runtime.initialize("not_catalogued", "X", {"width": 3, "height": 4})
        with self.assertRaisesRegex(ValueError, "requires class"):
            runtime.initialize("rainbow", "WaveAnimation", {"width": 3, "height": 4})
        with self.assertRaisesRegex(RuntimeError, "not initialized"):
            runtime.render(0.0, 0, instance_id="missing")

    def test_browser_shim_matches_direct_plugin_frames(self):
        direct = _direct_fingerprints()
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(BUNDLE_PATH, "r") as archive:
                archive.extractall(temp_dir)
            script = r'''
import hashlib
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
                cwd=temp_dir, check=True, text=True, capture_output=True,
            )
        self.assertEqual(json.loads(completed.stdout), direct)

    def test_browser_shim_matches_host_framework_plant_optics(self):
        params = {
            "plant_aware": True,
            "brightness": 0.8,
            "plant_modifiers": {
                "version": 1,
                "active": ["illuminate", "hue_shift", "liquid_glass"],
                "strengths": {
                    "illuminate": 0.5,
                    "hue_shift": 0.32,
                    "liquid_glass": 0.46,
                },
            },
        }
        direct_animation = RainbowAnimation(_Controller(33, 138), params)
        direct_output = direct_animation.generate_frame(0.25, 1)
        direct_pixels = getattr(direct_output, "pixels", direct_output)
        direct_pixels = direct_animation.apply_framework_plant_modifiers(
            direct_pixels, changed=bool(getattr(direct_output, "changed", True))
        )
        direct_digest = hashlib.sha256(direct_pixels.tobytes(order="C")).hexdigest()

        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(BUNDLE_PATH, "r") as archive:
                archive.extractall(temp_dir)
            script = r'''
import hashlib
import json
import sys
sys.path.insert(0, sys.argv[1])
from ledgrid_browser_runtime import BrowserPreviewRuntime
runtime = BrowserPreviewRuntime()
runtime.initialize(
    "rainbow", "RainbowAnimation", {"width": 33, "height": 138},
    json.loads(sys.argv[2]),
)
runtime.render(0.25, 1)
print(hashlib.sha256(runtime.frame_bytes).hexdigest())
'''
            completed = subprocess.run(
                [sys.executable, "-c", script, temp_dir, json.dumps(params)],
                cwd=temp_dir, check=True, text=True, capture_output=True,
            )
        self.assertEqual(completed.stdout.strip(), direct_digest)


if __name__ == "__main__":
    unittest.main()
