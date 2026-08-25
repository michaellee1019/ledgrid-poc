from __future__ import annotations

import json
import hashlib
from pathlib import Path
import subprocess
import unittest

from animation.native.builder import build_plugin
from animation.native.bundle import inspect_bundle
from animation.native.errors import NativeBuildError, NativePreviewError
from animation.native.constants import RECEIVER_VIEWS
from animation.native.preview import render_host_frames, stress_parameters

from .native_background_test_support import (
    PLUGIN_ID,
    create_repo,
    deterministic_pair,
    toolchain_available,
)


@unittest.skipUnless(toolchain_available(), "pinned PlatformIO Xtensa toolchain unavailable")
class NativeBackgroundBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.first, cls.second = deterministic_pair()

    def test_two_repository_roots_produce_identical_artifacts(self) -> None:
        assert self.first.bundle_path is not None
        assert self.second.bundle_path is not None
        assert self.first.payload_path is not None
        assert self.second.payload_path is not None
        assert self.first.host_library_path is not None
        assert self.second.host_library_path is not None
        assert self.first.preview_path is not None
        assert self.second.preview_path is not None
        self.assertEqual(self.first.bundle_digest, self.second.bundle_digest)
        self.assertEqual(self.first.payload_digest, self.second.payload_digest)
        self.assertEqual(self.first.bundle_path.read_bytes(), self.second.bundle_path.read_bytes())
        self.assertEqual(self.first.payload_path.read_bytes(), self.second.payload_path.read_bytes())
        self.assertEqual(
            self.first.host_library_path.read_bytes(),
            self.second.host_library_path.read_bytes(),
        )
        self.assertEqual(self.first.preview_path.read_bytes(), self.second.preview_path.read_bytes())

    def test_result_and_canonical_bundle_contract(self) -> None:
        assert self.first.bundle_path is not None
        verified = inspect_bundle(self.first.bundle_path)
        self.assertEqual(verified.bundle_digest, self.first.bundle_digest)
        self.assertEqual(verified.payload_digest, self.first.payload_digest)
        self.assertEqual(verified.manifest["plugin_id"], PLUGIN_ID)
        self.assertEqual(set(verified.members), {"manifest.json", "payload/module.so", "preview/preview.webp"})
        self.assertEqual(verified.raw, self.first.bundle_path.read_bytes())
        self.assertLess(len(verified.payload), 512 * 1024)

    def test_default_and_stress_acceptance_and_repeatability(self) -> None:
        assert self.first.host_library_path is not None
        self.assertIsNotNone(self.first.default_timing)
        self.assertIsNotNone(self.first.stress_timing)
        assert self.first.default_timing is not None
        assert self.first.stress_timing is not None
        self.assertLess(self.first.default_timing.p95_ms, 4.0)
        self.assertLess(self.first.stress_timing.p95_ms, 4.0)
        self.assertEqual(self.first.default_missed_deadlines, 0)
        self.assertEqual(self.first.stress_missed_deadlines, 0)
        self.assertEqual(
            self.first.default_changed_frames, self.first.default_total_frames
        )
        self.assertEqual(
            self.first.stress_changed_frames, self.first.stress_total_frames
        )
        manifest = self.first.manifest
        first = render_host_frames(self.first.host_library_path, manifest)
        second = render_host_frames(self.first.host_library_path, manifest)
        stress = render_host_frames(
            self.first.host_library_path,
            manifest,
            parameters=stress_parameters(manifest["parameter_schema"]),
        )
        self.assertEqual(first.frames, second.frames)
        self.assertGreater(len(set(first.frames)), 1)
        self.assertNotEqual(first.frames, stress.frames)
        self.assertTrue(any(first.frames[0]))
        self.assertEqual(
            RECEIVER_VIEWS,
            (
                (0, 0, 8, False),
                (1, 8, 8, False),
                (3, 16, 8, True),
                (2, 24, 8, True),
                (4, 32, 1, False),
            ),
        )
        # Canonical full-frame fingerprints make direction handling observable:
        # ignoring reverse_local_strip_order mirrors lanes 3/2 and changes these.
        self.assertEqual(
            [hashlib.sha256(frame).hexdigest() for frame in first.frames[:3]],
            [
                "3b1018e7a5ebaea4b614c4f199da563b2a497c9c2d9cf363615222b0ed05e635",
                "6d9882177918cd70a002cfab64e2dcb0ea7f158529d90c7c15543ab57ac1e07f",
                "e3734ed441e2beb4e74381bb70fb8f4806430fafa03e4300404d3b60d27d78af",
            ],
        )

        strip_bytes = 138 * 3
        adjacent_mae = []
        for strip in range(32):
            left = first.frames[0][strip * strip_bytes : (strip + 1) * strip_bytes]
            right = first.frames[0][(strip + 1) * strip_bytes : (strip + 2) * strip_bytes]
            adjacent_mae.append(
                sum(abs(a - b) for a, b in zip(left, right)) / strip_bytes
            )
        # These indices are the four receiver boundaries after reversing the
        # installed native strip order for physical lanes 3 and 2. The final
        # boundary covers the heterogeneous one-strip fifth receiver.
        self.assertTrue(all(adjacent_mae[index] < 10.0 for index in (7, 15, 23, 31)))

    def test_plan_still_enforces_tracked_source_policy(self) -> None:
        root = create_repo()
        subprocess.run(
            ("git", "-C", str(root), "rm", "--cached", "--quiet", f"animation/plugins/{PLUGIN_ID}/native/background.cpp"),
            check=True,
        )
        with self.assertRaisesRegex(NativeBuildError, "untracked"):
            build_plugin(root, PLUGIN_ID, root / "build", execute=False)

    def test_missing_and_symlinked_native_sources_are_rejected(self) -> None:
        for symlink in (False, True):
            root = create_repo()
            source = root / f"animation/plugins/{PLUGIN_ID}/native/background.cpp"
            if symlink:
                replacement = source.parent / "replacement.cpp"
                replacement.write_bytes(source.read_bytes())
                source.unlink()
                source.symlink_to(replacement.name)
            else:
                source.unlink()
            with self.subTest(symlink=symlink), self.assertRaises(NativeBuildError):
                build_plugin(root, PLUGIN_ID, root / "build", execute=False)

    def test_tracked_extra_compiler_dependency_is_rejected(self) -> None:
        root = create_repo()
        source = root / f"animation/plugins/{PLUGIN_ID}/native/background.cpp"
        extra = source.parent / "extra.h"
        extra.write_text("#define LEDGRID_UNSAFE_EXTRA 1\n", encoding="utf-8")
        source.write_text('#include "extra.h"\n' + source.read_text(encoding="utf-8"), encoding="utf-8")
        subprocess.run(("git", "-C", str(root), "add", str(extra), str(source)), check=True)
        with self.assertRaisesRegex(NativeBuildError, "outside the allowlist"):
            build_plugin(root, PLUGIN_ID, root / "build")

    def test_isolated_preview_detects_output_overwrite(self) -> None:
        root = create_repo()
        source = root / f"animation/plugins/{PLUGIN_ID}/native/background.cpp"
        text = source.read_text(encoding="utf-8")
        marker = "  result->status = LEDGRID_NATIVE_BACKGROUND_OK;"
        self.assertIn(marker, text)
        source.write_text(
            text.replace(
                marker,
                "  request->rgb_output[request->rgb_output_size] = 0xffU;\n"
                + marker,
            ),
            encoding="utf-8",
        )
        subprocess.run(("git", "-C", str(root), "add", str(source)), check=True)
        with self.assertRaisesRegex(NativePreviewError, "canary"):
            build_plugin(root, PLUGIN_ID, root / "build")

    def test_target_rejects_missing_extra_exports_imports_and_initializers(self) -> None:
        cases = (
            ("ledgrid_native_background_v2", "ledgrid_native_background_wrong_v2"),
            (
                "\nextern \"C\" __attribute__((visibility(\"default\"))) int ledgrid_extra_export(void) { return 1; }\n",
                None,
            ),
            (
                "\nextern \"C\" int ledgrid_forbidden_import(void);\n"
                "extern \"C\" __attribute__((visibility(\"default\"))) int* ledgrid_force_import(void) {\n"
                "  static int value = ledgrid_forbidden_import(); return &value;\n}\n",
                None,
            ),
            (
                "\nstatic volatile int ledgrid_initializer_sink = 0;\n"
                "__attribute__((constructor)) static void ledgrid_forbidden_initializer(void) {\n"
                "  ledgrid_initializer_sink = 1;\n}\n",
                None,
            ),
        )
        for index, (addition_or_old, replacement) in enumerate(cases):
            root = create_repo()
            source = root / f"animation/plugins/{PLUGIN_ID}/native/background.cpp"
            text = source.read_text(encoding="utf-8")
            text = (
                text.replace(addition_or_old, replacement)
                if replacement is not None
                else text + addition_or_old
            )
            source.write_text(text, encoding="utf-8")
            subprocess.run(("git", "-C", str(root), "add", str(source)), check=True)
            with self.subTest(index=index), self.assertRaises(NativeBuildError):
                build_plugin(root, PLUGIN_ID, root / "build")

    def test_pilot_has_no_python_package_and_preview_schedule_matches_fps(self) -> None:
        component = self.first.manifest
        captures = component["preview"]["capture_seconds"]
        self.assertEqual(component["preview"]["duration_ms"], 17)
        deltas = [round((right - left) * 1_000_000) for left, right in zip(captures, captures[1:])]
        self.assertEqual(set(deltas), {16667})
        source_root = Path(__file__).resolve().parents[2]
        self.assertFalse((source_root / f"animation/plugins/{PLUGIN_ID}/__init__.py").exists())

    def test_receipt_retains_p99_and_max_host_proxy_evidence(self) -> None:
        assert self.first.receipt_path is not None
        receipt = json.loads(self.first.receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(receipt["performance_scope"], "host preview proxy; not ESP32 hardware")
        for profile in ("default", "stress"):
            self.assertIn("mean_ms", receipt["performance"][profile])
            self.assertIn("p95_ms", receipt["performance"][profile])
            self.assertIn("p99_ms", receipt["performance"][profile])
            self.assertIn("max_ms", receipt["performance"][profile])
            self.assertEqual(receipt["performance"][profile]["missed_deadlines"], 0)
            self.assertEqual(receipt["performance"][profile]["changed_frame_ratio"], 1.0)


if __name__ == "__main__":
    unittest.main()
