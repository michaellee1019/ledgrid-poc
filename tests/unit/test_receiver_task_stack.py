"""Compile the production task policy against effective SDK configurations."""

from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
FIRMWARE = ROOT / "firmware/esp32"


class ReceiverTaskStackTests(unittest.TestCase):
    def test_native_effective_stack_guard_and_ordinary_build_compatibility(self):
        compiler = shutil.which("clang++") or shutil.which("g++")
        self.assertIsNotNone(compiler, "C++ compiler required for firmware policy checks")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "policy.cpp"
            source.write_text('#include "ledgrid/receiver_task_policy.hpp"\n')
            for native, esp, size, succeeds in (
                (1, True, 3584, False),
                (1, True, 16384, True),
                (0, True, 3584, True),
                (1, False, 3584, True),
            ):
                with self.subTest(native=native, esp=esp, size=size):
                    (root / "sdkconfig.h").write_text(
                        f"#define CONFIG_ESP_MAIN_TASK_STACK_SIZE {size}\n"
                    )
                    result = subprocess.run(
                        [compiler, "-std=c++17", "-fsyntax-only",
                         f"-DLEDGRID_ENABLE_RECEIVER_NATIVE_MODULES={native}",
                         *(["-DESP_PLATFORM=1"] if esp else []),
                         "-I", str(root), "-I", str(FIRMWARE / "include"), str(source)],
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode == 0, succeeds, result.stderr)
                    if not succeeds:
                        self.assertIn("requires a 16 KiB main task stack", result.stderr)

    def test_stack_override_is_native_only_and_selected_by_native_canary(self):
        native = (FIRMWARE / "sdkconfig.native.defaults").read_text()
        self.assertIn("CONFIG_ESP_MAIN_TASK_STACK_SIZE=16384\n", native)
        self.assertNotIn("CONFIG_ESP_MAIN_TASK_STACK_SIZE", (
            FIRMWARE / "sdkconfig.defaults").read_text())
        # Verify the actual environment selection, not just an unreferenced file.
        import configparser

        config = configparser.ConfigParser(interpolation=None)
        config.read(FIRMWARE / "platformio.ini")
        for environment in ("esp32-s3-devkitc-1", "esp32-s3-devkitc-1-local-canary",
                            "esp32-s3-devkitc-1-native-canary"):
            with self.subTest(environment=environment):
                options = config[f"env:{environment}"]
                selected = options.get("board_build.cmake_extra_args", "")
                self.assertEqual("sdkconfig.native.defaults" in selected,
                                 environment.endswith("-native-canary"))
