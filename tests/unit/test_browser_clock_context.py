"""Clock's current preset packet must render from the portable Python bundle."""

from __future__ import annotations

from dataclasses import fields
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from animation.browser_preview.python.shim_presentation_contracts import (
    AnimationRuntimeContext as PortableContext,
)
from animation.core.presentation_contracts import AnimationRuntimeContext
from animation.plugins.clock_overlay import ClockOverlayAnimation
from tools.build_browser_python_bundle import build_bundle
from web.composer_component_presets import ComponentPresetCatalog

ROOT = Path(__file__).resolve().parents[2]

CLOCK_SCRIPT = r'''
import json
from pathlib import Path
import sys
import numpy as np

sys.path.insert(0, sys.argv[1])
from ledgrid_browser_runtime import BrowserPreviewRuntime
from animation.core.presentation_contracts import ResolvedScene

profile = Path(sys.argv[2])
digest = profile.read_bytes()[68:100].hex()
cases = [("default", {})] + [
    (case["preset_id"], case["parameters"]) for case in json.loads(sys.argv[3])
]
for label, params in cases:
    runtime = BrowserPreviewRuntime()
    runtime.bind_installation_profile_path(str(profile), digest)
    ready = runtime.initialize(
        "clock_overlay", "ClockOverlayAnimation", {"width": 33, "height": 138},
        params, installation_profile_digest=digest,
    )
    assert ready["frameFormat"] == "premultiplied-rgba", (label, ready)
    runtime.render(0, 0, wall_time=1787774400.0)
    first = runtime.frame_bytes
    rgba = np.frombuffer(first, dtype=np.uint8).reshape(-1, 4)
    assert rgba.shape == (33 * 138, 4), label
    assert np.any(rgba[:, 3]), label
    assert np.all(rgba[:, :3] <= rgba[:, 3:4]), label
    cached = runtime.render(20, 1, wall_time=1787774400.0)
    assert not cached["changed"] and runtime.frame_bytes == first, label
    tick = runtime.render(21, 2, wall_time=1787774401.0)
    assert tick["changed"] == params.get("show_seconds", True), (label, tick)

    # The renderer accepts the portable resolved Scene palette without host imports.
    palette_frames = []
    for palette_id in ("neutral", "mist", "spectrum", "ember"):
        runtime.animation.set_presentation_context(ResolvedScene(
            canonical_scene={}, canonical_bytes=b"", digest="", descriptor=None,
            parameters=params, palette={"palette_id": palette_id}, phase_time=21,
            plant_inputs={},
        ))
        runtime.render(21, 3, wall_time=1787774401.0)
        palette_frames.append(runtime.frame_bytes)
        assert not runtime.render(22, 4, wall_time=1787774401.0)["changed"], label
    assert len(set(palette_frames)) == 4, label

# Injected browser time must retain the Clock's authored offset. Compare with
# the same clock rendered at an independently shifted timestamp.
def clock_frame(minutes, timestamp):
    runtime = BrowserPreviewRuntime()
    runtime.bind_installation_profile_path(str(profile), digest)
    runtime.initialize("clock_overlay", "ClockOverlayAnimation", {"width": 33, "height": 138},
                       {"format_24h": True, "clock_offset_minutes": minutes},
                       installation_profile_digest=digest)
    runtime.render(0, 0, wall_time=timestamp)
    return runtime.frame_bytes

now = 1787774400.0
assert clock_frame(360, now) != clock_frame(0, now)
assert clock_frame(360, now) == clock_frame(0, now + 360 * 60)
assert "animation.core.scene_runtime" not in sys.modules
assert "ipc.control_channel" not in sys.modules
print(json.dumps([label for label, _ in cases]))
'''


class BrowserClockContextTests(unittest.TestCase):
    def test_portable_context_preserves_host_field_names_and_defaults(self):
        def shape(context):
            return [(field.name, field.default) for field in fields(context)]
        self.assertEqual(shape(PortableContext), shape(AnimationRuntimeContext))

    def test_current_clock_defaults_and_presets_render_from_fresh_bundle(self):
        self._check_bundle(fresh=True)

    def test_current_clock_defaults_and_presets_render_from_published_bundle(self):
        self._check_bundle(fresh=False)

    def _check_bundle(self, *, fresh):
        cases = ComponentPresetCatalog(ROOT, {
            "clock_overlay": ClockOverlayAnimation._normalized_parameters,
        }).choices("clock_overlay")
        self.assertEqual(len(cases), 3)
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            bundle = ROOT / "web/static/generated/composer/ledgrid_python_runtime.zip"
            if fresh:
                bundle = temporary / "runtime.zip"
                payload = build_bundle(ROOT, bundle)
                self.assertEqual(build_bundle(ROOT, bundle), payload)
            with zipfile.ZipFile(bundle) as archive:
                archive.extractall(temporary / "runtime")
            completed = subprocess.run([
                sys.executable, "-c", CLOCK_SCRIPT, str(temporary / "runtime"),
                str(ROOT / "tests/fixtures/installation_profile_v1.bin"), json.dumps(cases),
            ], cwd=temporary, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout), ["default"] + [
            case["preset_id"] for case in cases
        ])
