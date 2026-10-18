"""Regression checks for PlatformIO invocation helper."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RunPlatformIOTests(unittest.TestCase):
    def test_wrapper_delegates_to_platformio_cli(self):
        script = (ROOT / "tools/deployment/run_platformio.sh").read_text(encoding="utf-8")
        self.assertIn("platformio_cli.py", script)
        self.assertNotIn("exec pio ", script)


if __name__ == "__main__":
    unittest.main()
