"""Tests for concise deployment log capture."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import tempfile
import unittest

from tools.deployment.run_captured import run_captured


class CapturedRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_dir = tempfile.TemporaryDirectory()
        self.log_dir = Path(self.temporary_dir.name)

    def tearDown(self) -> None:
        self.temporary_dir.cleanup()

    def test_success_is_quiet_and_retains_full_log(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status, log = run_captured(
                [sys.executable, "-c", "print('success detail')"],
                phase="tests.run",
                log_dir=self.log_dir,
            )

        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(log.read_text(encoding="utf-8"), "success detail\n")

    def test_failure_reports_phase_tail_and_log_path(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            status, log = run_captured(
                [
                    sys.executable,
                    "-c",
                    "print('old line'); print('cause line'); raise SystemExit(7)",
                ],
                phase="deploy.full",
                log_dir=self.log_dir,
                tail_lines=1,
            )

        message = stderr.getvalue()
        self.assertEqual(status, 7)
        self.assertIn("deploy.full failed (exit 7)", message)
        self.assertNotIn("old line", message)
        self.assertIn("cause line", message)
        self.assertIn(f"full log: {log}", message)

    def test_verbose_mode_streams_and_captures(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            status, log = run_captured(
                [sys.executable, "-c", "print('visible detail')"],
                phase="deploy.python",
                log_dir=self.log_dir,
                verbose=True,
            )

        self.assertEqual(status, 0)
        self.assertEqual(stdout.getvalue(), "visible detail\n")
        self.assertEqual(log.read_text(encoding="utf-8"), "visible detail\n")


if __name__ == "__main__":
    unittest.main()
