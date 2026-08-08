from __future__ import annotations

import hashlib
import ctypes
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from firmware_animations.manifest import validate_parameter_schema, validate_parameters
from firmware_animations.constants import DEFAULT_IMPORT_ALLOWLIST
from firmware_animations.native import (
    _Api,
    _Context,
    _Helpers,
    _Parameter,
    _ParameterValue,
    native_build_commands,
    render_host_frames,
    render_host_preview,
)
from firmware_animations.tracks import load_image_frames
from tools.benchmarks.native_animations import load_catalog, stress_parameters


REPO_ROOT = Path(__file__).parents[2]


class NativeAnimationExampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory(
            prefix="ledgrid-native-examples-test-"
        )
        cls.output_root = Path(cls.temporary.name)
        cls.examples = load_catalog()
        cls.libraries: dict[str, Path] = {}
        for example in cls.examples:
            output = cls.output_root / f"{example['id']}.so"
            commands = native_build_commands(
                [example["source"]],
                sdk_include=REPO_ROOT / "firmware_animations/sdk/include",
                module_output=output.with_suffix(".esp32.so"),
                host_output=output,
            )
            completed = subprocess.run(
                commands.host_preview, cwd=REPO_ROOT,
                env={**os.environ, "LC_ALL": "C", "SOURCE_DATE_EPOCH": "0"},
                check=False, capture_output=True, text=True,
            )
            if completed.returncode:
                raise AssertionError(
                    f"host compile failed for {example['id']}: "
                    f"{completed.stderr or completed.stdout}"
                )
            cls.libraries[example["id"]] = output

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_catalog_sources_and_metadata_are_strict_and_unique(self) -> None:
        self.assertEqual(len(self.examples), 3)
        ids = {example["id"] for example in self.examples}
        self.assertEqual(ids, {
            "startup-rainbow-native",
            "aurora-ribbons-native",
            "meteor-shower-native",
        })
        for example in self.examples:
            metadata = example["metadata"]
            schema = validate_parameter_schema(metadata["parameter_schema"])
            validate_parameters(schema, {}, require_all=True)
            self.assertEqual(metadata["preferred_fps"], 60)
            self.assertIn(str(example["source"].relative_to(REPO_ROOT)),
                          metadata["provenance"]["source"])

    def test_default_and_stress_controls_render_motion_without_collisions(self) -> None:
        default_fingerprints: set[str] = set()
        for example in self.examples:
            schema = example["metadata"]["parameter_schema"]
            default = render_host_frames(
                self.libraries[example["id"]], example["metadata"],
                frame_count=6, duration_ms=80,
            )
            stress = render_host_frames(
                self.libraries[example["id"]], example["metadata"],
                frame_count=6, duration_ms=80,
                parameters=stress_parameters(schema),
            )
            self.assertEqual(len(default.frames), 6)
            self.assertTrue(all(len(frame) == 32 * 138 * 3 for frame in default.frames))
            self.assertNotEqual(default.frames[0], default.frames[-1])
            self.assertTrue(any(default.frames[0]))
            self.assertNotEqual(default.frames, stress.frames)
            default_fingerprints.add(
                hashlib.sha256(b"".join(default.frames)).hexdigest()
            )
        self.assertEqual(len(default_fingerprints), len(self.examples))

    def test_zero_is_success_for_preview_and_preview_is_animated_webp(self) -> None:
        startup = next(
            item for item in self.examples
            if item["id"] == "startup-rainbow-native"
        )
        preview = render_host_preview(
            self.libraries[startup["id"]], startup["metadata"],
            frame_count=4, duration_ms=80,
        )
        self.assertEqual(preview[:4], b"RIFF")
        preview_path = self.output_root / "startup-preview.webp"
        preview_path.write_bytes(preview)
        frames = load_image_frames(preview_path)
        self.assertEqual(len(frames.frames_rgb), 4)
        self.assertEqual(frames.durations_ms, (80, 80, 80, 80))

    def test_authoring_and_receiver_headers_publish_the_same_return_contract(self) -> None:
        headers = [
            REPO_ROOT / "firmware/esp32/include/ledgrid/animation_abi.h",
            REPO_ROOT / "firmware_animations/sdk/include/lga/animation_v1.h",
        ]
        for header in headers:
            text = header.read_text()
            self.assertIn("#define LEDGRID_ANIMATION_OK 0", text)
            self.assertIn("#define LEDGRID_ANIMATION_ERROR -1", text)
            self.assertIn("any nonzero", text)
            for helper in ("random_u32", "hsv_to_rgb", "rgb_to_565", "sin_f32", "cos_f32"):
                self.assertIn(helper, text)
        self.assertEqual(headers[0].read_bytes(), headers[1].read_bytes())
        self.assertEqual(DEFAULT_IMPORT_ALLOWLIST, frozenset())

    def test_host_preview_ctypes_layout_is_pinned_to_the_shared_abi_header(self) -> None:
        self.assertEqual(ctypes.sizeof(ctypes.c_void_p), 8)
        expected = {
            _ParameterValue: (8, {"integer": 0, "real": 0, "boolean": 0,
                                  "enum_value": 0, "color": 0}),
            _Parameter: (24, {"name": 0, "type": 8, "reserved": 9, "value": 16}),
            _Context: (72, {"abi_version": 0, "local_strips": 4,
                            "leds_per_strip": 6, "global_strip_offset": 8,
                            "elapsed_us": 16, "scaled_elapsed_us": 24,
                            "frame_index": 32, "parameters": 40,
                            "parameter_count": 48, "rgb_output": 56,
                            "rgb_output_size": 64}),
            _Helpers: (48, {"abi_version": 0, "random_u32": 8,
                            "hsv_to_rgb": 16, "rgb_to_565": 24,
                            "sin_f32": 32, "cos_f32": 40}),
            _Api: (32, {"abi_version": 0, "initialize": 8,
                        "render": 16, "cleanup": 24}),
        }
        for structure, (size, offsets) in expected.items():
            self.assertEqual(ctypes.sizeof(structure), size)
            self.assertEqual(
                {name: getattr(structure, name).offset for name in offsets},
                offsets,
            )


if __name__ == "__main__":
    unittest.main()
