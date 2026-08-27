from __future__ import annotations

import base64
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from animation.native.constants import HOST_IDENTITY_FLAGS, HOST_LINK_FLAGS
from animation.native.preview import render_host_frames
from tools.build_browser_native import build


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ID = "aurora_curtains_native"
GENERATED = ROOT / "web/static/generated/composer/aurora_curtains_native.wasm"
WORKER = ROOT / "web/static/js/composer_native_worker.js"


class BrowserNativeContractTests(unittest.TestCase):
    def test_worker_exposes_transferable_strip_major_native_protocol(self) -> None:
        source = WORKER.read_text(encoding="utf-8")
        self.assertIn("receiver-native-cpp-wasm", source)
        self.assertIn("type: 'ready'", source)
        self.assertIn("type: 'frame'", source)
        self.assertIn("type: 'error'", source)
        self.assertIn("[pixels.buffer]", source)
        self.assertIn("canonical strip-major RGB", source)

    def test_committed_artifact_is_webassembly(self) -> None:
        self.assertTrue(GENERATED.is_file())
        self.assertEqual(GENERATED.read_bytes()[:4], b"\0asm")


@unittest.skipUnless(
    shutil.which("em++"),
    "em++ unavailable; browser-native parity requires Emscripten",
)
@unittest.skipUnless(
    shutil.which("node"),
    "node unavailable; browser-native parity requires a WebAssembly runner",
)
@unittest.skipUnless(
    shutil.which("c++"),
    "host C++ compiler unavailable; browser-native parity requires the host preview peer",
)
class BrowserNativeParityTests(unittest.TestCase):
    def _compile_host(self, output: Path) -> Path:
        compiler = shutil.which("c++")
        assert compiler is not None
        platform = "darwin" if sys.platform == "darwin" else "linux"
        library = output / "aurora-host-preview.so"
        subprocess.run(
            (
                compiler,
                *HOST_IDENTITY_FLAGS,
                *HOST_LINK_FLAGS[platform],
                "-o",
                str(library),
                "animation/plugins/aurora_curtains_native/native/background.cpp",
            ),
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        return library

    def _render_wasm(
        self,
        wasm_path: Path,
        scene_times_us: list[int],
        parameters: dict[str, object],
    ) -> tuple[bytes, ...]:
        script = r"""
const fs = require('fs');
const wasmPath = process.argv[1];
const times = JSON.parse(process.argv[2]);
const params = JSON.parse(process.argv[3]);
const bytes = fs.readFileSync(wasmPath);
const moduleValue = new WebAssembly.Module(bytes);
const imports = {};
for (const item of WebAssembly.Module.imports(moduleValue)) {
  if (item.module === 'wasi_snapshot_preview1' && item.name === 'proc_exit') {
    imports.wasi_snapshot_preview1 ||= {};
    imports.wasi_snapshot_preview1.proc_exit = code => { throw new Error(`exit ${code}`); };
  } else {
    throw new Error(`unsupported import ${item.module}.${item.name}`);
  }
}
const instance = new WebAssembly.Instance(moduleValue, imports);
const e = instance.exports;
const fn = name => e[name] || e[`_${name}`];
if (fn('lg_browser_init')(33, 138) !== 0) throw new Error(`init ${fn('lg_browser_last_error')()}`);
if (fn('lg_browser_set_parameters')(
  params.brightness, params.curtain_width, params.layers, params.motion,
  params.shimmer ? 1 : 0,
) !== 0) throw new Error(`params ${fn('lg_browser_last_error')()}`);
const frames = [];
for (let index = 0; index < times.length; ++index) {
  const time = times[index];
  const high = Math.floor(time / 0x100000000);
  const low = time - high * 0x100000000;
  if (fn('lg_browser_render')(low >>> 0, high >>> 0, index >>> 0, 0) !== 0) {
    throw new Error(`render ${fn('lg_browser_last_error')()}`);
  }
  const pointer = fn('lg_browser_pixels')();
  const size = fn('lg_browser_pixels_size')();
  const frame = new Uint8Array(e.memory.buffer, pointer, size).slice();
  frames.push(Buffer.from(frame).toString('base64'));
}
process.stdout.write(JSON.stringify(frames));
"""
        completed = subprocess.run(
            (
                shutil.which("node") or "node",
                "-e",
                script,
                str(wasm_path),
                json.dumps(scene_times_us, separators=(",", ":")),
                json.dumps(parameters, separators=(",", ":")),
            ),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return tuple(base64.b64decode(value) for value in json.loads(completed.stdout))

    def test_wasm_build_is_deterministic_and_matches_host_preview(self) -> None:
        parameters = {
            "brightness": 0.73,
            "curtain_width": 11,
            "layers": 4,
            "motion": 0.61,
            "shimmer": True,
        }
        scene_times_us = [0, 17_000, 34_000, 51_000]
        manifest = json.loads(
            (ROOT / f"animation/plugins/{PLUGIN_ID}/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        with tempfile.TemporaryDirectory(prefix="ledgrid-browser-native-test-") as name:
            directory = Path(name)
            first = build(directory / "first.wasm")
            second = build(directory / "second.wasm")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            host_library = self._compile_host(directory)
            host = render_host_frames(
                host_library,
                manifest,
                parameters=parameters,
                frame_count=len(scene_times_us),
                duration_ms=17,
                repo_root=ROOT,
            )
            browser = self._render_wasm(first, scene_times_us, parameters)
        self.assertEqual(browser, host.frames)


if __name__ == "__main__":
    unittest.main()
