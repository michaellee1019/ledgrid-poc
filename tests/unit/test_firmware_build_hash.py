from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.deployment.firmware_build_hash import (
    BUILD_DIRECTORIES,
    BUILD_ENVIRONMENT,
    REQUIRED_BUILD_FILES,
    firmware_build_hash,
)


class FirmwareBuildHashTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        for relative in REQUIRED_BUILD_FILES:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"initial {relative}\n")
        for relative in BUILD_DIRECTORIES:
            path = self.root / relative / "nested" / "input.cpp"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"initial {relative}\n")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_every_declared_build_input_changes_the_hash(self) -> None:
        baseline = firmware_build_hash(self.root, {})
        paths = [self.root / relative for relative in REQUIRED_BUILD_FILES]
        paths.extend(
            self.root / relative / "nested" / "input.cpp"
            for relative in BUILD_DIRECTORIES
        )
        for path in paths:
            with self.subTest(path=path.relative_to(self.root)):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                self.assertNotEqual(firmware_build_hash(self.root, {}), baseline)
                path.write_bytes(original)

    def test_compile_time_environment_changes_the_hash(self) -> None:
        baseline = firmware_build_hash(self.root, {})
        for name in BUILD_ENVIRONMENT:
            with self.subTest(name=name):
                self.assertNotEqual(
                    firmware_build_hash(self.root, {name: "configured"}), baseline
                )

    def test_missing_required_input_fails_closed(self) -> None:
        (self.root / "partitions.csv").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "partitions.csv"):
            firmware_build_hash(self.root, {})


if __name__ == "__main__":
    unittest.main()
