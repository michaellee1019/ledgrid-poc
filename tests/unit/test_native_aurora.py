from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np

from animation.native.aurora import canonical_palette_roles
from animation.native.builder import build_plugin
from animation.native.constants import TARGET_COMPILER_NAME, TARGET_TOOLCHAIN_PACKAGE
from animation.native.errors import NativePreviewError
from animation.native.managed_preview import ManagedNativeHostPreview
import tools.build_browser_composer_bootstrap as browser_bootstrap
from web.composer_final_preview import (
    NATIVE_AURORA_BUNDLE_DIGEST,
    NATIVE_AURORA_HOST_ARTIFACT_DIGEST,
    NATIVE_AURORA_PAYLOAD_DIGEST,
    InstalledFinalSceneRuntime,
    current_component_catalog,
    native_aurora_descriptor,
)


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ID = "native_aurora"


def toolchain_available() -> bool:
    package = Path.home() / ".platformio" / "packages" / TARGET_TOOLCHAIN_PACKAGE
    return shutil.which("platformio") is not None and (
        package / "bin" / TARGET_COMPILER_NAME
    ).is_file()


def _trunc_div(value: int, divisor: int) -> int:
    return value // divisor if value >= 0 else -((-value) // divisor)


def _reference_frame(
    palette_id: str, *, gain_q8: int, seed: int, source_tick: int, source_fps_q8: int
) -> np.ndarray:
    roles = canonical_palette_roles(palette_id)
    low, primary, accent = (
        roles["background_low"], roles["primary"], roles["accent"]
    )
    tick_q8 = source_tick * 256
    tick_seconds, tick_fraction = divmod(tick_q8, source_fps_q8)
    time_phase = (tick_seconds * 4381 + tick_fraction * 4381 // source_fps_q8) & 0xFFFF
    x_coefficient = 38588 + (seed % 5) * 1147
    pixels = np.empty((33 * 138, 3), dtype=np.uint8)
    for strip in range(33):
        for led in range(138):
            phase = (
                time_phase + strip * x_coefficient // 32 + led * 21900 // 137
            ) & 0xFFFF
            wave = round(math.sin(phase * math.tau / 65536.0) * 32767)
            field_q16 = min(65535, ((wave + 32768) * gain_q8) // 256)
            squared_q16 = (field_q16 * field_q16) >> 16
            for channel in range(3):
                value = low[channel]
                value += _trunc_div(
                    (primary[channel] - low[channel]) * field_q16, 65536
                )
                value += _trunc_div(
                    (accent[channel] - primary[channel]) * squared_q16 * 34 + 3276800,
                    6553600,
                )
                pixels[strip * 138 + led, channel] = min(255, max(0, value))
    return pixels


class NativeAuroraContractTests(unittest.TestCase):
    def test_descriptor_preserves_canonical_parameters_and_real_identity(self) -> None:
        descriptor = native_aurora_descriptor()
        self.assertEqual(descriptor.component_id, PLUGIN_ID)
        self.assertEqual(descriptor.provider.value, "receiver_native")
        self.assertEqual(descriptor.role.value, "background")
        self.assertEqual(descriptor.defaults["bundle_digest"], NATIVE_AURORA_BUNDLE_DIGEST)
        self.assertEqual(
            {key: descriptor.defaults[key] for key in ("gain", "source_fps", "seed")},
            {"gain": 0.72, "source_fps": 30.0, "seed": 8012},
        )

    def test_palette_adapter_is_complete_ordered_and_preserves_authored_roles(self) -> None:
        expected = {
            "neutral": ((2, 10, 18), (24, 148, 132), (150, 255, 218)),
            "mist": ((3, 9, 20), (40, 102, 142), (170, 228, 245)),
            "spectrum": ((15, 3, 34), (84, 38, 194), (54, 238, 230)),
            "ember": ((18, 3, 2), (156, 42, 14), (255, 202, 92)),
        }
        for palette_id, authored in expected.items():
            roles = canonical_palette_roles(palette_id)
            self.assertEqual(len(roles), 8)
            self.assertEqual(
                (roles["background_low"], roles["primary"], roles["accent"]),
                authored,
            )

    @unittest.skipUnless(shutil.which("c++"), "host C++ compiler unavailable")
    def test_accepted_q8_conversion_has_no_undefined_shifts(self) -> None:
        source = r"""
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include "animation/plugins/native_aurora/native/background.cpp"
int main() {
  for (uint32_t exponent = 0; exponent <= 132; ++exponent) {
    for (uint32_t fraction : {0U, 1U, 0x7fffffU}) {
      uint32_t bits = (exponent << 23U) | fraction;
      float value;
      std::memcpy(&value, &bits, sizeof(value));
      assert(positive_float_to_q8(bits) == static_cast<uint16_t>(value * 256.0f));
    }
  }
}
"""
        with tempfile.TemporaryDirectory(prefix="native-aurora-q8-") as name:
            directory = Path(name)
            probe = directory / "q8.cpp"
            probe.write_text("#include <initializer_list>\n" + source)
            binary = directory / "q8"
            subprocess.run([shutil.which("c++"), "-std=c++17", "-DLG_HOST_PREVIEW=1", "-fsanitize=undefined", "-fno-sanitize-recover=all",
                "-I", str(ROOT), "-I", str(ROOT / "firmware/esp32/include"), str(probe), "-o", str(binary)],
                check=True, capture_output=True, text=True, timeout=30)
            subprocess.run([str(binary)], check=True, capture_output=True, text=True, timeout=10)

    def test_missing_managed_build_fails_closed(self) -> None:
        with self.assertRaisesRegex(NativePreviewError, "build is unavailable"):
            ManagedNativeHostPreview(ROOT, PLUGIN_ID, "0" * 64)

    def test_foreground_only_receiver_seam_does_not_load_a_host_peer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native-aurora-foreground-only-") as name:
            runtime = InstalledFinalSceneRuntime(
                current_component_catalog(), Path(name), foreground_only=True
            )
        self.assertTrue(runtime.foreground_only)
        self.assertIsNone(runtime._native)


@unittest.skipUnless(toolchain_available(), "pinned PlatformIO Xtensa toolchain unavailable")
class NativeAuroraArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.build = build_plugin(
            ROOT, PLUGIN_ID, ROOT / "run_state" / "native_background_builds"
        )
        cls.preview = ManagedNativeHostPreview(
            ROOT, PLUGIN_ID, NATIVE_AURORA_BUNDLE_DIGEST
        )

    def test_verified_artifact_has_the_source_controlled_exact_identities(self) -> None:
        self.assertEqual(self.build.bundle_digest, NATIVE_AURORA_BUNDLE_DIGEST)
        self.assertEqual(self.build.payload_digest, NATIVE_AURORA_PAYLOAD_DIGEST)
        self.assertEqual(
            self.build.manifest["build"]["host_artifact_sha256"],
            NATIVE_AURORA_HOST_ARTIFACT_DIGEST,
        )
        self.assertEqual(self.preview.bundle_digest, NATIVE_AURORA_BUNDLE_DIGEST)
        self.assertEqual(self.preview.payload_digest, NATIVE_AURORA_PAYLOAD_DIGEST)
        self.assertEqual(
            self.preview.host_artifact_digest, NATIVE_AURORA_HOST_ARTIFACT_DIGEST
        )

    def test_generated_catalog_uses_exact_managed_identity_and_fails_closed(self) -> None:
        available = browser_bootstrap.build_bootstrap(ROOT)
        native = next(
            component
            for component in available["components"]
            if component["plugin_id"] == PLUGIN_ID
        )
        identity = native["browser_capabilities"]["managed_identity"]
        self.assertEqual(identity["bundle_digest"], NATIVE_AURORA_BUNDLE_DIGEST)
        self.assertEqual(
            identity["expected_payload_digest"], NATIVE_AURORA_PAYLOAD_DIGEST
        )

        with mock.patch.object(
            browser_bootstrap, "NATIVE_AURORA_BUNDLE_DIGEST", "0" * 64
        ):
            missing = browser_bootstrap.build_bootstrap(ROOT)
        native = next(
            component
            for component in missing["components"]
            if component["plugin_id"] == PLUGIN_ID
        )
        capability = native["browser_capabilities"]
        # The bundled WASM remains previewable; unpublished target identity
        # must keep physical activation unavailable.
        self.assertTrue(capability["previewable"])
        self.assertFalse(capability["activation_ready"])
        self.assertIsNone(capability["managed_identity"]["bundle_digest"])
        self.assertIsNone(capability["managed_identity"]["expected_payload_digest"])

    def test_actual_host_peer_matches_integer_reference_and_source_cadence(self) -> None:
        parameters = {"gain": 0.72, "source_fps": 30.0, "seed": 8012}
        frame = self.preview.render(
            parameters=parameters,
            palette=tuple(canonical_palette_roles("mist").values()),
            scaled_scene_time=0.2,
            unscaled_scene_time=0.5,
            frame_index=0,
        )
        np.testing.assert_array_equal(
            frame.pixels,
            _reference_frame(
                "mist", gain_q8=184, seed=8012, source_tick=6, source_fps_q8=7680
            ),
        )
        same_tick = self.preview.render(
            parameters=parameters,
            palette=tuple(canonical_palette_roles("mist").values()),
            scaled_scene_time=0.21,
            unscaled_scene_time=0.51,
            frame_index=1,
        )
        self.assertFalse(same_tick.changed)
        np.testing.assert_array_equal(same_tick.pixels, frame.pixels)

    def test_palette_gain_seed_and_scaled_time_are_native_visual_inputs(self) -> None:
        def render(palette: str, gain: float, seed: int, elapsed: float) -> bytes:
            frame = self.preview.render(
                parameters={"gain": gain, "source_fps": 30.0, "seed": seed},
                palette=tuple(canonical_palette_roles(palette).values()),
                scaled_scene_time=elapsed,
                unscaled_scene_time=elapsed,
                frame_index=0,
            )
            return frame.pixels.tobytes()

        baseline = render("neutral", 0.72, 8012, 0.2)
        self.assertNotEqual(render("ember", 0.72, 8012, 0.2), baseline)
        self.assertNotEqual(render("neutral", 0.36, 8012, 0.2), baseline)
        self.assertNotEqual(render("neutral", 0.72, 8013, 0.2), baseline)
        self.assertNotEqual(render("neutral", 0.72, 8012, 0.3), baseline)


if __name__ == "__main__":
    unittest.main()
