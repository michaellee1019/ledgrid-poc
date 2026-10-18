"""Tests for PlatformIO Core selection."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tools.deployment import platformio_cli


class PlatformIOCLITests(unittest.TestCase):
    def test_parse_version_from_core_banner(self):
        self.assertEqual(
            platformio_cli._parse_version("PlatformIO Core, version 6.2.0"),
            (6, 2, 0),
        )

    def test_selects_newest_eligible_core(self):
        old = Path("/tmp/pio-old")
        new = Path("/tmp/pio-new")

        def inspect(path: Path):
            if path == old:
                return (6, 1, 18), "PlatformIO Core, version 6.1.18"
            return (6, 2, 0), "PlatformIO Core, version 6.2.0"

        with patch.object(platformio_cli, "_candidate_paths", return_value=[old, new]):
            with patch.object(platformio_cli, "_inspect", side_effect=inspect):
                self.assertEqual(platformio_cli.select_platformio(), new)

    def test_rejects_only_obsolete_cores(self):
        old = Path("/tmp/pio-old")
        with patch.object(platformio_cli, "_candidate_paths", return_value=[old]):
            with patch.object(
                platformio_cli,
                "_inspect",
                return_value=((6, 1, 18), "PlatformIO Core, version 6.1.18"),
            ):
                with self.assertRaises(SystemExit):
                    platformio_cli.select_platformio()


if __name__ == "__main__":
    unittest.main()
