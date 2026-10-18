"""Regression checks for PlatformIO invocation helper."""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class RunPlatformIOTests(unittest.TestCase):
    def test_wrapper_prefers_platformio_penv_before_uv(self):
        script = (ROOT / "tools/deployment/run_platformio.sh").read_text(encoding="utf-8")
        self.assertIn(".platformio/penv/bin/pio", script)
        self.assertIn("--python 3.12", script)
        self.assertNotIn("uv run --with platformio pio", script)


if __name__ == "__main__":
    unittest.main()
