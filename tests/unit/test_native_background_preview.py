from __future__ import annotations

from pathlib import Path
import ctypes
import shutil
import subprocess
import sys
import tempfile
import unittest

from animation.native.constants import (
    HOST_IDENTITY_FLAGS,
    HOST_LINK_FLAGS,
    MAX_STATE_ALIGNMENT,
    MAX_STATE_BYTES,
)
from animation.native.errors import NativePreviewError
from animation.native.preview import render_host_frames
from animation.native.preview_worker import _helpers

from .native_background_test_support import (
    PLUGIN_ID,
    SOURCE_ROOT,
    deterministic_pair,
    toolchain_available,
)


def _compile_host(source_text: str, directory: Path) -> Path:
    source = directory / "background.cpp"
    output = directory / "host-preview.so"
    source.write_text(source_text, encoding="utf-8")
    compiler = shutil.which("c++")
    if compiler is None:
        raise unittest.SkipTest("host C++ compiler unavailable")
    host_platform = "darwin" if sys.platform == "darwin" else "linux"
    subprocess.run(
        (
            compiler,
            *HOST_IDENTITY_FLAGS,
            *HOST_LINK_FLAGS[host_platform],
            "-o",
            str(output),
            str(source),
        ),
        cwd=SOURCE_ROOT,
        check=True,
        capture_output=True,
    )
    return output


@unittest.skipUnless(toolchain_available(), "pinned PlatformIO Xtensa toolchain unavailable")
class NativeBackgroundPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = deterministic_pair()[0]
        cls.manifest = cls.result.manifest
        cls.source = (
            SOURCE_ROOT
            / f"animation/plugins/{PLUGIN_ID}/native/background.cpp"
        ).read_text(encoding="utf-8")

    def _run_corrupt(
        self,
        source: str,
        *,
        duration_ms: int | None = None,
        timeout: float = 3.0,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="ledgrid-native-corrupt-") as name:
            library = _compile_host(source, Path(name))
            render_host_frames(
                library,
                self.manifest,
                frame_count=2,
                duration_ms=duration_ms,
                timeout_seconds=timeout,
            )

    def test_header_state_limits_and_helper_semantics_are_frozen(self) -> None:
        header = (
            SOURCE_ROOT
            / "firmware/esp32/include/ledgrid/native_background_abi_v2.h"
        ).read_text(encoding="utf-8")
        self.assertEqual(MAX_STATE_BYTES, 64 * 1024)
        self.assertEqual(MAX_STATE_ALIGNMENT, 64)
        self.assertIn(
            "#define LEDGRID_NATIVE_BACKGROUND_MAX_STATE_BYTES 65536U", header
        )
        self.assertIn(
            "#define LEDGRID_NATIVE_BACKGROUND_MAX_STATE_ALIGNMENT 64U", header
        )

        helpers, keepalive = _helpers()
        self.assertEqual(len(keepalive), 4)
        random_state = ctypes.c_uint32(0x12345678)
        expected = random_state.value
        expected ^= (expected << 13) & 0xFFFFFFFF
        expected ^= expected >> 17
        expected ^= (expected << 5) & 0xFFFFFFFF
        self.assertEqual(helpers.random_u32(ctypes.byref(random_state)), expected)
        self.assertEqual(random_state.value, expected)
        self.assertEqual(
            [helpers.sin_q15(phase) for phase in (0, 16384, 32768, 49152)],
            [0, 32767, 0, -32767],
        )
        self.assertEqual(
            [helpers.cos_q15(phase) for phase in (0, 16384, 32768, 49152)],
            [32767, 0, -32767, 0],
        )
        rgb = (ctypes.c_uint8 * 3)()
        helpers.hsv_to_rgb(0, 255, 255, rgb)
        self.assertEqual(list(rgb), [255, 0, 0])
        helpers.hsv_to_rgb(21845, 255, 255, rgb)
        self.assertEqual(list(rgb), [0, 255, 0])

    def test_worker_contains_crash_and_hang(self) -> None:
        marker = "  auto* state = static_cast<AuroraState*>(opaque);\n  if (state->initialized != 1U) {"
        self.assertIn(marker, self.source)
        crash = self.source.replace(marker, "  __builtin_trap();\n" + marker)
        with self.assertRaisesRegex(NativePreviewError, "isolated host preview failed"):
            self._run_corrupt(crash)
        hang = self.source.replace(marker, "  for (;;) {}\n" + marker)
        with self.assertRaisesRegex(NativePreviewError, "timeout"):
            self._run_corrupt(hang, timeout=0.2)

    def test_worker_rejects_result_overwrite_and_input_mutation(self) -> None:
        marker = "  result->status = LEDGRID_NATIVE_BACKGROUND_OK;"
        self.assertIn(marker, self.source)
        overwrite = self.source.replace(
            marker,
            "  reinterpret_cast<uint8_t*>(result)[sizeof(ledgrid_native_render_result_v2)] = 1U;\n"
            + marker,
        )
        with self.assertRaisesRegex(NativePreviewError, "render result.*canary"):
            self._run_corrupt(overwrite)

        retained = self.source.replace(
            "  uint8_t initialized;",
            "  uint8_t initialized;\n  const ledgrid_native_parameter_v2* retained_parameters;",
        ).replace(
            "  state->shimmer = parameters[4].value.boolean;",
            "  state->shimmer = parameters[4].value.boolean;\n"
            "  state->retained_parameters = context->parameters;",
        ).replace(
            marker,
            "  const_cast<ledgrid_native_parameter_v2*>(state->retained_parameters)[0].reserved_zero = 1U;\n"
            + marker,
        )
        with self.assertRaisesRegex(NativePreviewError, "mutated read-only parameter"):
            self._run_corrupt(retained)

    def test_worker_rejects_deadline_beyond_component_cadence(self) -> None:
        expected = "request->unscaled_scene_time_us + kFramePeriodUs - remainder;"
        self.assertIn(expected, self.source)
        late = self.source.replace(
            expected,
            "request->unscaled_scene_time_us + kFramePeriodUs * 2U;",
        )
        with self.assertRaisesRegex(NativePreviewError, "deadline exceeds"):
            self._run_corrupt(late)

    def test_worker_rejects_stale_and_backward_deadlines(self) -> None:
        expected = "request->unscaled_scene_time_us + kFramePeriodUs - remainder;"
        self.assertIn(expected, self.source)
        stale = self.source.replace(expected, "request->unscaled_scene_time_us;")
        with self.assertRaisesRegex(NativePreviewError, "stale/nonfuture"):
            self._run_corrupt(stale)

        backward = self.source.replace(
            expected,
            "request->frame_index == 0U\n"
            "          ? request->unscaled_scene_time_us + kFramePeriodUs\n"
            "          : request->unscaled_scene_time_us + 1U;",
        )
        with self.assertRaisesRegex(NativePreviewError, "deadlines move backwards"):
            self._run_corrupt(backward, duration_ms=1)

    def test_worker_rejects_unchanged_first_render(self) -> None:
        expected = "  result->changed = 1U;"
        self.assertIn(expected, self.source)
        unchanged_first = self.source.replace(expected, "  result->changed = 0U;")
        with self.assertRaisesRegex(NativePreviewError, "unchanged before"):
            self._run_corrupt(unchanged_first)

    def test_worker_rejects_helper_table_and_function_pointer_mutation(self) -> None:
        render_marker = "  result->status = LEDGRID_NATIVE_BACKGROUND_OK;"
        self.assertIn(render_marker, self.source)
        table_mutation = self.source.replace(
            render_marker,
            "  const_cast<ledgrid_native_helpers_v2*>(state->helpers)->struct_size = 0U;\n"
            + render_marker,
        )
        with self.assertRaisesRegex(NativePreviewError, "mutated read-only helper table"):
            self._run_corrupt(table_mutation)

        cleanup_marker = "  state->initialized = 0U;"
        self.assertIn(cleanup_marker, self.source)
        function_mutation = self.source.replace(
            cleanup_marker,
            "  const_cast<ledgrid_native_helpers_v2*>(state->helpers)->sin_q15 = nullptr;\n"
            + cleanup_marker,
        )
        with self.assertRaisesRegex(NativePreviewError, "mutated read-only helper table"):
            self._run_corrupt(function_mutation)

    def test_unchanged_retains_previous_complete_receiver_frames(self) -> None:
        expected = "  result->changed = 1U;"
        self.assertIn(expected, self.source)
        unchanged_after_first = self.source.replace(
            expected,
            "  result->changed = request->frame_index == 0U ? 1U : 0U;",
        )
        with tempfile.TemporaryDirectory(prefix="ledgrid-native-unchanged-") as name:
            library = _compile_host(unchanged_after_first, Path(name))
            run = render_host_frames(library, self.manifest, frame_count=3)
        self.assertEqual(run.changed_frames, 1)
        self.assertTrue(any(run.frames[0]))
        self.assertEqual(run.frames, (run.frames[0], run.frames[0], run.frames[0]))

    def test_palette_changes_but_framework_owned_luminance_is_not_double_applied(self) -> None:
        assert self.result.host_library_path is not None
        normal = render_host_frames(
            self.result.host_library_path, self.manifest, frame_count=2
        )
        dim = render_host_frames(
            self.result.host_library_path,
            self.manifest,
            frame_count=2,
            vibe_luminance_q8_8=0,
        )
        red_palette = [(2, 0, 0)] * 8
        recolored = render_host_frames(
            self.result.host_library_path,
            self.manifest,
            frame_count=2,
            vibe_palette=red_palette,
        )
        self.assertEqual(dim.frames[0], normal.frames[0])
        self.assertNotEqual(recolored.frames[0], normal.frames[0])
        with self.assertRaisesRegex(NativePreviewError, "luminance"):
            render_host_frames(
                self.result.host_library_path,
                self.manifest,
                frame_count=2,
                vibe_luminance_q8_8=257,
            )


if __name__ == "__main__":
    unittest.main()
