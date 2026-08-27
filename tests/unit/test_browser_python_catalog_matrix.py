"""Executable browser-shim matrix for every shipped Python animation and preset."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from animation.core.plugin_loader import AnimationPluginLoader


REPO_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_PATH = REPO_ROOT / "web/static/generated/composer/ledgrid_python_runtime.zip"
RESULT_PREFIX = "BROWSER_MATRIX_RESULT="


MATRIX_SCRIPT = r'''
import json
import pathlib
import sys
import traceback

bundle_root = pathlib.Path(sys.argv[1])
repo_root = pathlib.Path(sys.argv[2])
sys.path.insert(0, str(bundle_root))
from ledgrid_browser_runtime import BrowserPreviewRuntime, PLUGIN_SPECS

failures = []
defaults_rendered = 0
presets_rendered = 0
role_counts = {}
fixed_wall_clock_frames = 0

def render_case(label, plugin_id, class_name, params):
    global defaults_rendered, presets_rendered, fixed_wall_clock_frames
    try:
        runtime = BrowserPreviewRuntime()
        ready = runtime.initialize(
            plugin_id, class_name, {"width": 33, "height": 138}, params
        )
        spec = PLUGIN_SPECS[plugin_id]
        assert ready["role"] == spec.role
        assert ready["frameFormat"] == spec.frame_format
        channels = 4 if spec.role == "overlay" else 3
        first = runtime.render(0.0, 0, wall_time=1787774400.0)
        expected_length = 33 * 138 * channels
        assert len(runtime.frame_bytes) == expected_length
        # One second is a meaningful source update for even the lowest-rate
        # catalog simulations, and a deterministic wall-clock tick for clocks.
        updated = runtime.render(1.0, 1, wall_time=1787774401.0)
        assert len(runtime.frame_bytes) == expected_length
        assert updated["frameFormat"] == spec.frame_format
        assert updated["wallClockFixed"] is True
        if spec.timing_adapter == "wall_clock":
            fixed_wall_clock_frames += 2
        role_counts[spec.role] = role_counts.get(spec.role, 0) + 1
    except Exception as exc:
        failures.append({
            "case": label,
            "plugin": plugin_id,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        })

for plugin_id, spec in PLUGIN_SPECS.items():
    render_case(f"default:{plugin_id}", plugin_id, spec.class_name, {})
    defaults_rendered += 1

for preset_path in sorted((repo_root / "animation/plugins").glob("*/presets/*.json")):
    payload = json.loads(preset_path.read_text(encoding="utf-8"))
    plugin_id = payload["animation"]
    if plugin_id not in PLUGIN_SPECS:
        # Receiver-native presets are outside the Python provider catalog.
        provider = json.loads(
            (preset_path.parents[1] / "manifest.json").read_text(encoding="utf-8")
        ).get("provider", "python")
        if provider == "receiver_native":
            continue
        failures.append({
            "case": str(preset_path.relative_to(repo_root)),
            "plugin": plugin_id,
            "error": "Python preset is missing from browser catalog",
            "traceback": "",
        })
        continue
    render_case(
        str(preset_path.relative_to(repo_root)), plugin_id,
        PLUGIN_SPECS[plugin_id].class_name, payload["params"],
    )
    presets_rendered += 1

clock_overlay = PLUGIN_SPECS.get("clock_overlay")
if clock_overlay is None or clock_overlay.role != "overlay":
    failures.append({
        "case": "catalog:clock_overlay", "plugin": "clock_overlay",
        "error": "clock_overlay must be present with role=overlay", "traceback": "",
    })
elif clock_overlay.frame_format != "premultiplied-rgba":
    failures.append({
        "case": "catalog:clock_overlay", "plugin": "clock_overlay",
        "error": "clock_overlay must transfer premultiplied RGBA", "traceback": "",
    })

result = {
    "plugins": defaults_rendered,
    "presets": presets_rendered,
    "roleCounts": role_counts,
    "fixedWallClockFrames": fixed_wall_clock_frames,
    "failures": failures,
}
print("BROWSER_MATRIX_RESULT=" + json.dumps(result, sort_keys=True))
if failures:
    raise SystemExit(1)
'''


class BrowserPythonCatalogMatrixTests(unittest.TestCase):
    def test_every_authoritative_python_animation_and_preset_renders_two_frames(self):
        loader = AnimationPluginLoader()
        authoritative_plugins = loader.scan_plugins()
        authoritative_presets = list(loader.iter_curated_preset_files())

        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(BUNDLE_PATH, "r") as archive:
                archive.extractall(temp_dir)
            completed = subprocess.run(
                [sys.executable, "-c", MATRIX_SCRIPT, temp_dir, str(REPO_ROOT)],
                cwd=temp_dir,
                text=True,
                capture_output=True,
            )

        result_lines = [
            line for line in completed.stdout.splitlines()
            if line.startswith(RESULT_PREFIX)
        ]
        self.assertTrue(result_lines, completed.stdout + completed.stderr)
        result = json.loads(result_lines[-1][len(RESULT_PREFIX):])
        self.assertEqual(result["failures"], [], json.dumps(result["failures"], indent=2))
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(result["plugins"], len(authoritative_plugins))
        self.assertEqual(result["presets"], len(authoritative_presets))
        self.assertGreaterEqual(result["roleCounts"].get("overlay", 0), 1)
        self.assertGreaterEqual(result["fixedWallClockFrames"], 6)


if __name__ == "__main__":
    unittest.main()
