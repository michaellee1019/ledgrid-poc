"""Browser-Wasm parity and composition coverage for the firmware builtin."""

from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

import numpy as np

from animation.core.compositing import HostSceneCompositor, PlacedOverlay
from animation.core.feature_flags import AnimationPipelineFeatureFlags
from animation.core.presentation_contracts import BaseFrame, OverlayFrame
from animation.core.receiver_static_component import (
    receiver_static_component_descriptor,
    render_compiled_rainbow_preview,
)
from animation.plugins.clock_overlay import ClockOverlayAnimation
from ipc.scene_contract import SceneProviderPolicy
from tools.build_browser_native import build_compiled_rainbow
from web.app import AnimationWebInterface


ROOT = Path(__file__).resolve().parents[2]
GENERATED = ROOT / "web/static/generated/composer/compiled_rainbow.wasm"
COMPOSITOR = ROOT / "web/static/js/composer_compositor.js"
WORKER = ROOT / "web/static/js/composer_native_worker.js"
WIDTH = 33
HEIGHT = 138
PIXELS = WIDTH * HEIGHT


class _Controller:
    strip_count = WIDTH
    leds_per_strip = HEIGHT
    total_leds = PIXELS


def _node(script: str, *arguments: str) -> dict:
    executable = shutil.which("node")
    if executable is None:
        raise unittest.SkipTest("node unavailable; browser-Wasm tests require Node")
    completed = subprocess.run(
        (executable, "-e", script, *arguments),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _render_wasm(
    wasm_path: Path,
    scene_times_us: list[int],
    *,
    preferred_cadence_hz: int = 30,
    common_seed: int = 0,
) -> list[dict]:
    script = r"""
const fs = require('fs');
const wasm = new WebAssembly.Instance(
  new WebAssembly.Module(fs.readFileSync(process.argv[1])), {}
).exports;
const fn = name => wasm[name] || wasm[`_${name}`];
const times = JSON.parse(process.argv[2]);
if (fn('lg_browser_init')(33, 138) !== 0) throw new Error('init');
if (fn('lg_browser_set_parameters')(Number(process.argv[3]), Number(process.argv[4])) !== 0) {
  throw new Error(`parameters ${fn('lg_browser_last_error')()}`);
}
const frames = times.map((time, index) => {
  const high = Math.floor(time / 0x100000000);
  const low = time - high * 0x100000000;
  if (fn('lg_browser_render')(low >>> 0, high >>> 0, index, 0) !== 0) {
    throw new Error(`render ${fn('lg_browser_last_error')()}`);
  }
  const frame = new Uint8Array(
    wasm.memory.buffer, fn('lg_browser_pixels')(), fn('lg_browser_pixels_size')()
  ).slice();
  return {pixels: Buffer.from(frame).toString('base64'), changed: Boolean(fn('lg_browser_changed')())};
});
process.stdout.write(JSON.stringify(frames));
"""
    return _node(
        script,
        str(wasm_path),
        json.dumps(scene_times_us, separators=(",", ":")),
        str(preferred_cadence_hz),
        str(common_seed),
    )


def _clock_overlay() -> bytes:
    clock = ClockOverlayAnimation(
        _Controller(),
        {
            "face": "digital",
            "format_24h": True,
            "show_seconds": False,
            "position_y": 0.5,
            "brightness": 0.8,
            "opacity": 0.72,
            "backdrop_opacity": 0.35,
        },
    )
    clock._clock_now = lambda: datetime(2026, 8, 26, 21, 37, 0)  # type: ignore[method-assign]
    return clock.generate_frame(0.0, 0).pixels.tobytes(order="C")


class _HybridManager:
    controller = _Controller()
    preview_controller = controller
    feature_flags = AnimationPipelineFeatureFlags(
        receiver_local_background=True,
        receiver_sparse_overlay=True,
        receiver_native_modules=True,
    )

    def __init__(self) -> None:
        descriptor = receiver_static_component_descriptor(self.feature_flags)
        assert descriptor is not None
        self._components = [descriptor]

    def list_components(self) -> list[dict]:
        return deepcopy(self._components)

    def list_animations(self) -> list[dict]:
        return []

    def scene_provider_policy(self) -> SceneProviderPolicy:
        return SceneProviderPolicy(True, True, True)


class _Channel:
    def read_status(self) -> dict:
        return {}


class BrowserCompiledRainbowContractTests(unittest.TestCase):
    def test_worker_routes_aurora_and_compiled_rainbow_without_conflating_parameters(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        self.assertIn("aurora_curtains_native", source)
        self.assertIn("compiled_rainbow", source)
        self.assertIn("preferred_cadence_hz", source)
        self.assertIn("common_seed", source)
        self.assertIn("curtain_width", source)
        self.assertIn("canonical strip-major RGB", source)

    def test_hybrid_scene_selectable_builtin_has_verified_browser_runtime(self) -> None:
        interface = AnimationWebInterface(_Channel(), _HybridManager(), local_mode=True)
        response = interface.app.test_client().get("/api/v1/composer/bootstrap")
        self.assertEqual(response.status_code, 200)
        selectable = [
            component
            for component in response.get_json()["components"]
            if component["scene_compatibility"].get("selectable")
            and "background" in component["scene_compatibility"].get("slots", [])
        ]
        self.assertEqual(
            [component["key"] for component in selectable],
            ["receiver_native:compiled_rainbow"],
        )
        self.assertTrue(all(component["browser_runtime"]["supported"] for component in selectable))
        self.assertEqual(
            selectable[0]["browser_runtime"]["asset_url"],
            "/static/generated/composer/compiled_rainbow.wasm",
        )

    def test_committed_artifact_is_webassembly(self) -> None:
        self.assertTrue(GENERATED.is_file())
        self.assertEqual(GENERATED.read_bytes()[:4], b"\0asm")
        self.assertEqual(stat.S_IMODE(GENERATED.stat().st_mode), 0o644)


@unittest.skipUnless(shutil.which("em++"), "em++ unavailable")
@unittest.skipUnless(shutil.which("node"), "node unavailable")
class BrowserCompiledRainbowParityTests(unittest.TestCase):
    def test_deterministic_build_matches_host_contract_for_times_and_seeds(self) -> None:
        times = [0, 33_333, 250_000, 999_999]
        with tempfile.TemporaryDirectory(prefix="ledgrid-compiled-rainbow-wasm-") as name:
            directory = Path(name)
            first = build_compiled_rainbow(directory / "first.wasm")
            second = build_compiled_rainbow(directory / "second.wasm")
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o644)
            self.assertEqual(stat.S_IMODE(second.stat().st_mode), 0o644)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first.read_bytes(), GENERATED.read_bytes())
            for seed in (0, 91, 0xFFFF_FFFF):
                browser = _render_wasm(first, times, common_seed=seed)
                host = [
                    render_compiled_rainbow_preview(
                        elapsed,
                        {"preferred_cadence_hz": 30, "common_seed": seed},
                        strip_count=WIDTH,
                        leds_per_strip=HEIGHT,
                    ).tobytes(order="C")
                    for elapsed in times
                ]
                self.assertEqual(
                    [base64.b64decode(frame["pixels"]) for frame in browser],
                    host,
                )

    def test_changed_flag_and_cadence_parameter_contract(self) -> None:
        repeated = _render_wasm(GENERATED, [0, 0, 33_333], common_seed=7)
        self.assertEqual([frame["changed"] for frame in repeated], [True, False, True])
        slow = _render_wasm(
            GENERATED, [250_000], preferred_cadence_hz=1, common_seed=7
        )[0]["pixels"]
        fast = _render_wasm(
            GENERATED, [250_000], preferred_cadence_hz=200, common_seed=7
        )[0]["pixels"]
        self.assertEqual(slow, fast)

    def test_compiled_rainbow_composes_clock_exactly_like_host(self) -> None:
        base = base64.b64decode(
            _render_wasm(GENERATED, [250_000], common_seed=73)[0]["pixels"]
        )
        overlay = _clock_overlay()
        script = r"""
const crypto = require('crypto');
const {composeLayers} = require(process.argv[1]);
const base = Uint8Array.from(Buffer.from(process.argv[2], 'base64'));
const overlay = Uint8Array.from(Buffer.from(process.argv[3], 'base64'));
const output = composeLayers({width: 33, height: 138, layers: [
  {id: 'compiled_rainbow', pixels: base, format: 'rgb', blend: 'replace'},
  {id: 'clock_overlay', pixels: overlay, format: 'premultiplied-rgba', blend: 'source-over'},
]});
process.stdout.write(JSON.stringify({
  length: output.length,
  sha256: crypto.createHash('sha256').update(output).digest('hex'),
}));
"""
        browser = _node(
            script,
            str(COMPOSITOR),
            base64.b64encode(base).decode("ascii"),
            base64.b64encode(overlay).decode("ascii"),
        )
        reference = HostSceneCompositor(WIDTH, HEIGHT).compose(
            BaseFrame(np.frombuffer(base, dtype=np.uint8).reshape((PIXELS, 3))),
            (
                PlacedOverlay(
                    OverlayFrame(
                        np.frombuffer(overlay, dtype=np.uint8).reshape((PIXELS, 4)),
                        revision=1,
                    )
                ),
            ),
        )
        self.assertEqual(browser["length"], PIXELS * 3)
        self.assertEqual(
            browser["sha256"],
            hashlib.sha256(reference.pixels.tobytes(order="C")).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()
