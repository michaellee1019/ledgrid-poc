from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from firmware_animations.examples.generate_sources import save_sources
from firmware_animations.native import native_build_commands
from tools.firmware_animation_package import main


class FirmwarePackageCliTests(unittest.TestCase):
    def test_keygen_build_verify_install_and_list_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = root / "private.pem"
            public = root / "public.pem"
            sources = root / "sources"
            save_sources(sources)
            metadata = root / "metadata.json"
            metadata.write_text(json.dumps({
                "id": "cli-frame-loop", "name": "CLI Frame Loop", "version": "1.0.0",
                "description": "End-to-end command-line package fixture.", "preferred_fps": 30,
                "parameter_schema": {}, "provenance": {
                    "author": "ledgrid-poc tests", "license": "MIT",
                    "source": "generated CLI fixture", "generated_by": "test_firmware_package_cli",
                },
            }))
            package = root / "loop.lga"
            library = root / "library"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["keygen", "--private", str(private), "--public", str(public)]), 0)
                self.assertEqual(main([
                    "build-frames", "--source", str(sources / "representative.webp"),
                    "--metadata", str(metadata), "--private-key", str(private), "--output", str(package),
                ]), 0)
                self.assertEqual(main(["verify", str(package), "--trusted-key", str(public)]), 0)
                self.assertEqual(main([
                    "install", str(package), "--library", str(library), "--trusted-key", str(public),
                ]), 0)
                self.assertEqual(main(["list", "--library", str(library), "--trusted-key", str(public)]), 0)
            self.assertTrue(package.is_file())
            self.assertIn("cli-frame-loop", stdout.getvalue())

    def test_native_build_contract_pins_safety_flags_and_host_preview_mode(self) -> None:
        commands = native_build_commands(
            ["example.cpp"], sdk_include="firmware_animations/sdk/include",
            module_output="module.so", host_output="preview.so",
        )
        for flag in ("-fno-exceptions", "-fno-rtti", "-fPIC"):
            self.assertIn(flag, commands.esp32)
            self.assertIn(flag, commands.host_preview)
        self.assertIn("-nostdlib", commands.esp32)
        self.assertIn("-Wl,--build-id=none", commands.esp32)
        self.assertIn("-frandom-seed=ledgrid-animation-v1", commands.esp32)
        self.assertIn("-DLGA_HOST_PREVIEW=1", commands.host_preview)
        self.assertEqual(commands.esp32[0], "xtensa-esp32s3-elf-g++")


if __name__ == "__main__":
    unittest.main()
