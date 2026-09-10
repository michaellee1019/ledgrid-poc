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


EVENT_RGBA_MATRIX_SCRIPT = r'''
import json
import pathlib
import sys

bundle_root = pathlib.Path(sys.argv[1])
repo_root = pathlib.Path(sys.argv[2])
profile_path = pathlib.Path(sys.argv[3])
profile_digest = sys.argv[4]
sys.path.insert(0, str(bundle_root))
from ledgrid_browser_runtime import BrowserPreviewRuntime, PLUGIN_SPECS

cases = {
    "fireworks": [
        ("default", {}),
        ("grand-finale", json.loads((repo_root / "animation/plugins/fireworks/presets/grand-finale.json").read_text())["params"]),
    ],
    "flame_burst": [
        ("default", {}),
        ("afterburner", json.loads((repo_root / "animation/plugins/flame_burst/presets/afterburner.json").read_text())["params"]),
    ],
}
result = []
for plugin_id, plugin_cases in cases.items():
    spec = PLUGIN_SPECS[plugin_id]
    assert spec.role == "animation", spec
    assert spec.frame_format == "premultiplied-rgba", spec
    for label, params in plugin_cases:
        runtime = BrowserPreviewRuntime()
        runtime.bind_installation_profile_path(str(profile_path), profile_digest)
        ready = runtime.initialize(
            plugin_id, spec.class_name, {"width": 33, "height": 138}, params,
            installation_profile_digest=profile_digest,
        )
        assert ready["role"] == "animation", ready
        assert ready["frameFormat"] == "premultiplied-rgba", ready
        rendered = runtime.render(1.0, 1, wall_time=1787774401.0)
        assert rendered["frameFormat"] == "premultiplied-rgba", rendered
        assert len(runtime.frame_bytes) == 33 * 138 * 4
        result.append(f"{plugin_id}:{label}")
print("BROWSER_EVENT_RGBA_RESULT=" + json.dumps(result))
'''


MATRIX_SCRIPT = r'''
import json
import pathlib
import sys
import traceback

bundle_root = pathlib.Path(sys.argv[1])
repo_root = pathlib.Path(sys.argv[2])
profile_path = pathlib.Path(sys.argv[3])
profile_digest = sys.argv[4]
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
        runtime.bind_installation_profile_path(str(profile_path), profile_digest)
        ready = runtime.initialize(
            plugin_id, class_name, {"width": 33, "height": 138}, params,
            installation_profile_digest=profile_digest,
        )
        spec = PLUGIN_SPECS[plugin_id]
        assert ready["role"] == spec.role
        assert ready["frameFormat"] == spec.frame_format
        channels = 4 if spec.frame_format == "premultiplied-rgba" else 3
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

for plugin_id in ("fireworks", "flame_burst"):
    spec = PLUGIN_SPECS.get(plugin_id)
    if spec is None or spec.role != "animation":
        failures.append({
            "case": f"catalog:{plugin_id}", "plugin": plugin_id,
            "error": f"{plugin_id} must be published as an animation", "traceback": "",
        })
    elif spec.frame_format != "premultiplied-rgba":
        failures.append({
            "case": f"catalog:{plugin_id}", "plugin": plugin_id,
            "error": f"{plugin_id} must transfer premultiplied RGBA", "traceback": "",
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
    def test_event_rgba_defaults_and_representative_presets_render_from_bundle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(BUNDLE_PATH, "r") as archive:
                archive.extractall(temp_dir)
            completed = subprocess.run(
                [
                    sys.executable, "-c", EVENT_RGBA_MATRIX_SCRIPT, temp_dir,
                    str(REPO_ROOT),
                    str(REPO_ROOT / "tests/fixtures/installation_profile_v1.bin"),
                    (REPO_ROOT / "tests/fixtures/installation_profile_v1.bin")
                    .read_bytes()[68:100].hex(),
                ],
                cwd=temp_dir,
                text=True,
                capture_output=True,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn(
            'BROWSER_EVENT_RGBA_RESULT=["fireworks:default", "fireworks:grand-finale", '
            '"flame_burst:default", "flame_burst:afterburner"]',
            completed.stdout,
        )

    def test_every_authoritative_python_animation_and_preset_renders_two_frames(self):
        loader = AnimationPluginLoader()
        authoritative_plugins = loader.scan_plugins()
        authoritative_presets = list(loader.iter_curated_preset_files())

        with tempfile.TemporaryDirectory() as temp_dir:
            with zipfile.ZipFile(BUNDLE_PATH, "r") as archive:
                archive.extractall(temp_dir)
            completed = subprocess.run(
                [
                    sys.executable, "-c", MATRIX_SCRIPT, temp_dir, str(REPO_ROOT),
                    str(REPO_ROOT / "tests/fixtures/installation_profile_v1.bin"),
                    (REPO_ROOT / "tests/fixtures/installation_profile_v1.bin")
                    .read_bytes()[68:100].hex(),
                ],
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
        self.assertGreaterEqual(result["roleCounts"].get("animation", 0), 2)
        self.assertGreaterEqual(result["fixedWallClockFrames"], 6)


if __name__ == "__main__":
    unittest.main()
