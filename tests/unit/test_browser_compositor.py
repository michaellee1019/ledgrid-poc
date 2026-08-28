"""Browser-side canonical RGB layer compositor acceptance tests."""

from __future__ import annotations

import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import unittest

import numpy as np

from animation.browser_preview.python.runtime import BrowserPreviewRuntime
from animation.core.compositing import (
    HostSceneCompositor,
    PlacedOverlay,
    scale_premultiplied_rgba,
    source_over_rgb,
)
from animation.core.presentation_contracts import BaseFrame, OverlayFrame
from animation.plugins.clock_overlay import ClockOverlayAnimation


ROOT = Path(__file__).resolve().parents[2]
COMPOSITOR = ROOT / "web/static/js/composer_compositor.js"
NATIVE_WASM = ROOT / "web/static/generated/composer/aurora_curtains_native.wasm"
PROFILE_PATH = ROOT / "tests/fixtures/installation_profile_v1.bin"
PROFILE_DIGEST = PROFILE_PATH.read_bytes()[68:100].hex()
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
        raise unittest.SkipTest(
            "node unavailable; browser compositor tests require a JavaScript runtime"
        )
    completed = subprocess.run(
        (executable, "-e", script, *arguments),
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


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


class BrowserCompositorTests(unittest.TestCase):
    def test_native_wasm_background_composes_real_clock_overlay_exactly(self) -> None:
        overlay = _clock_overlay()
        script = r"""
const fs = require('fs');
const crypto = require('crypto');
const {composeLayers} = require(process.argv[1]);
const wasm = new WebAssembly.Instance(
  new WebAssembly.Module(fs.readFileSync(process.argv[2])), {}
).exports;
const fn = name => wasm[name] || wasm[`_${name}`];
if (fn('lg_browser_init')(33, 138) !== 0) throw new Error('native init');
if (fn('lg_browser_render')(34000, 0, 2, 0) !== 0) throw new Error('native render');
const nativeFrame = new Uint8Array(
  wasm.memory.buffer, fn('lg_browser_pixels')(), fn('lg_browser_pixels_size')()
).slice();
const overlay = Uint8Array.from(Buffer.from(process.argv[3], 'base64'));
const overlayBefore = Buffer.from(overlay);
const nativeBefore = Buffer.from(nativeFrame);
const output = composeLayers({width: 33, height: 138, layers: [
  {id: 'native', pixels: nativeFrame, format: 'rgb', blend: 'replace'},
  {id: 'clock_overlay', pixels: overlay, format: 'premultiplied-rgba', blend: 'source-over'},
]});
let covered = -1;
for (let pixel = 0; pixel < 33 * 138; ++pixel) {
  if (overlay[pixel * 4 + 3] !== 0) { covered = pixel; break; }
}
const rgbOffset = covered * 3;
const rgbaOffset = covered * 4;
process.stdout.write(JSON.stringify({
  covered,
  base: Array.from(nativeFrame.slice(rgbOffset, rgbOffset + 3)),
  overlay: Array.from(overlay.slice(rgbaOffset, rgbaOffset + 4)),
  composed: Array.from(output.slice(rgbOffset, rgbOffset + 3)),
  outputLength: output.length,
  outputSha256: crypto.createHash('sha256').update(output).digest('hex'),
  base64: Buffer.from(nativeFrame).toString('base64'),
  deterministic: Buffer.from(output).equals(composeLayers({width: 33, height: 138, layers: [
    {pixels: nativeFrame, format: 'rgb', blend: 'replace'},
    {pixels: overlay, format: 'premultiplied-rgba', blend: 'source-over'},
  ]})),
  nativeUnchanged: nativeBefore.equals(nativeFrame),
  overlayUnchanged: overlayBefore.equals(overlay),
}));
"""
        result = _node(
            script,
            str(COMPOSITOR),
            str(NATIVE_WASM),
            base64.b64encode(overlay).decode("ascii"),
        )
        self.assertGreaterEqual(result["covered"], 0)
        self.assertEqual(
            result["composed"],
            list(source_over_rgb(result["base"], result["overlay"])),
        )
        self.assertEqual(result["outputLength"], PIXELS * 3)
        native_frame = np.frombuffer(
            base64.b64decode(result["base64"]), dtype=np.uint8
        ).reshape((PIXELS, 3))
        overlay_frame = np.frombuffer(overlay, dtype=np.uint8).reshape((PIXELS, 4))
        reference = HostSceneCompositor(WIDTH, HEIGHT).compose(
            BaseFrame(native_frame),
            (PlacedOverlay(OverlayFrame(overlay_frame, revision=1)),),
        )
        self.assertEqual(
            result["outputSha256"],
            hashlib.sha256(reference.pixels.tobytes(order="C")).hexdigest(),
        )
        self.assertTrue(result["deterministic"])
        self.assertTrue(result["nativeUnchanged"])
        self.assertTrue(result["overlayUnchanged"])

    def test_python_background_and_overlay_match_host_reference_fixture(self) -> None:
        runtime = BrowserPreviewRuntime()
        runtime.bind_installation_profile_path(str(PROFILE_PATH), PROFILE_DIGEST)
        runtime.initialize(
            "gradient",
            "GradientAnimation",
            {"width": WIDTH, "height": HEIGHT},
            {
                "direction": "vertical",
                "animated": False,
                "brightness": 0.83,
                "color1_red": 14,
                "color1_green": 25,
                "color1_blue": 50,
                "color2_red": 180,
                "color2_green": 40,
                "color2_blue": 120,
            },
            installation_profile_digest=PROFILE_DIGEST,
        )
        runtime.render(0.0, 0)
        base = runtime.frame_bytes
        overlay_pixel = (96, 40, 16, 128)
        opacity = 173
        overlay = bytearray(PIXELS * 4)
        target = 17 * HEIGHT + 91
        overlay[target * 4 : target * 4 + 4] = bytes(overlay_pixel)
        script = r"""
const {composeLayers} = require(process.argv[1]);
const base = Uint8Array.from(Buffer.from(process.argv[2], 'base64'));
const overlay = Uint8Array.from(Buffer.from(process.argv[3], 'base64'));
const target = Number(process.argv[4]);
const output = composeLayers({width: 33, height: 138, layers: [
  {pixels: base, format: 'rgb'},
  {pixels: overlay, format: 'premultiplied-rgba', opacity: 173},
]});
process.stdout.write(JSON.stringify({
  target: Array.from(output.slice(target * 3, target * 3 + 3)),
  untouched: Array.from(output.slice(0, 3)),
  length: output.length,
}));
"""
        result = _node(
            script,
            str(COMPOSITOR),
            base64.b64encode(base).decode("ascii"),
            base64.b64encode(overlay).decode("ascii"),
            str(target),
        )
        scaled = scale_premultiplied_rgba(overlay_pixel, opacity)
        expected = source_over_rgb(list(base[target * 3 : target * 3 + 3]), scaled)
        self.assertEqual(result["target"], list(expected))
        self.assertEqual(result["untouched"], list(base[:3]))
        self.assertEqual(result["length"], PIXELS * 3)

    def test_disabled_opacity_order_keying_and_no_mutation(self) -> None:
        script = r"""
const {composeLayers, roundU8Product} = require(process.argv[1]);
const base = Uint8Array.of(10, 20, 30, 40, 50, 60);
const keyed = Uint8Array.of(0, 0, 0, 200, 100, 50);
const red = Uint8Array.of(100, 0, 0, 128, 0, 0, 0, 0);
const blue = Uint8Array.of(0, 0, 100, 128, 0, 0, 0, 0);
const copies = [base, keyed, red, blue].map(value => Buffer.from(value));
const first = composeLayers({width: 1, height: 2, layers: [
  {pixels: base, format: 'rgb'},
  {pixels: keyed, format: 'rgb', blend: 'keyed', opacity: 128},
  {pixels: red, format: 'premultiplied-rgba'},
  {pixels: blue, format: 'premultiplied-rgba'},
]});
const reversed = composeLayers({width: 1, height: 2, layers: [
  {pixels: base, format: 'rgb'},
  {pixels: blue, format: 'premultiplied-rgba'},
  {pixels: red, format: 'premultiplied-rgba'},
]});
const disabled = composeLayers({width: 1, height: 2, layers: [
  {pixels: base, format: 'rgb'},
  {pixels: keyed, format: 'rgb', blend: 'keyed', enabled: false},
  {pixels: red, format: 'premultiplied-rgba', opacity: 0},
]});
const unchanged = [base, keyed, red, blue].every((value, index) => copies[index].equals(value));
process.stdout.write(JSON.stringify({
  first: Array.from(first),
  reversed: Array.from(reversed),
  disabled: Array.from(disabled),
  unchanged,
  half: roundU8Product(255, 128),
}));
"""
        result = _node(script, str(COMPOSITOR))
        self.assertNotEqual(result["first"], result["reversed"])
        self.assertEqual(result["disabled"], [10, 20, 30, 40, 50, 60])
        self.assertEqual(result["first"][3:], [120, 75, 55])
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["half"], 128)

    def test_rejects_shape_and_nonpremultiplied_rgba(self) -> None:
        script = r"""
const {composeLayers} = require(process.argv[1]);
const errors = [];
for (const layers of [
  [{pixels: Uint8Array.of(1, 2), format: 'rgb'}],
  [{pixels: Uint8Array.of(129, 0, 0, 128), format: 'premultiplied-rgba'}],
]) {
  try { composeLayers({width: 1, height: 1, layers}); }
  catch (error) { errors.push(error.message); }
}
process.stdout.write(JSON.stringify({errors}));
"""
        result = _node(script, str(COMPOSITOR))
        self.assertEqual(len(result["errors"]), 2)
        self.assertIn("3 bytes", result["errors"][0])
        self.assertIn("premultiplied RGBA", result["errors"][1])


if __name__ == "__main__":
    unittest.main()
