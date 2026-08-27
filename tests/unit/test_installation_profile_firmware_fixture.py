"""Drift and semantic checks for the firmware receiver-profile fixtures."""

from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from animation.core.installation_profile import decode_installation_profile
from tools.fixtures.generate_installation_profile_golden import (
    DEFAULT_RECEIVER_HEADER,
    build_receiver_fixtures,
    build_receiver_header,
)


ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "tools" / "fixtures" / "generate_installation_profile_golden.py"
GLOBAL_FIXTURE = ROOT / "tests" / "fixtures" / "installation_profile_v1.bin"


class InstallationProfileFirmwareFixtureTests(unittest.TestCase):
    def test_checked_in_header_is_regeneration_equal(self) -> None:
        self.assertEqual(DEFAULT_RECEIVER_HEADER.read_bytes(), build_receiver_header())
        subprocess.run(
            [
                sys.executable,
                str(GENERATOR),
                "--check",
                "--receiver-header",
                str(DEFAULT_RECEIVER_HEADER),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )

    def test_all_five_payloads_have_exact_installed_receiver_identity(self) -> None:
        fixtures = build_receiver_fixtures()
        self.assertEqual(tuple(map(len, fixtures)), (10264,) * 4 + (1570,))
        self.assertEqual(len({hashlib.sha256(payload).digest() for payload in fixtures}), 5)
        expected = (
            (0, 8, False), (8, 8, False), (16, 8, True),
            (24, 8, True), (32, 1, False),
        )
        calibration_digests = set()
        for logical_id, (payload, (origin, width, reversed_order)) in enumerate(
            zip(fixtures, expected)
        ):
            with self.subTest(logical_id=logical_id):
                profile = decode_installation_profile(payload)
                self.assertEqual(
                    (profile.strip_origin, profile.reversed_strip_order),
                    (origin, reversed_order),
                )
                self.assertEqual(
                    (profile.global_strip_count, profile.strip_count, profile.leds_per_strip),
                    (33, width, 138),
                )
                calibration_digests.add(profile.calibration_digest)
        self.assertEqual(len(calibration_digests), 1)

    def test_receiver_header_check_is_read_only_and_rejects_stale_or_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            header = Path(temp_dir) / "receiver.hpp"
            header.write_bytes(build_receiver_header())
            subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--check",
                    "--output",
                    str(GLOBAL_FIXTURE),
                    "--receiver-header",
                    str(header),
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            )
            header.write_bytes(b"stale")
            result = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR),
                    "--check",
                    "--output",
                    str(GLOBAL_FIXTURE),
                    "--receiver-header",
                    str(header),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(header.read_bytes(), b"stale")


if __name__ == "__main__":
    unittest.main()
