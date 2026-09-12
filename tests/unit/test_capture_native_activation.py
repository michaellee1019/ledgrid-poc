"""Diagnostic evidence survives endpoint failures without mutating the wall."""

import json
from pathlib import Path
import tempfile
import unittest

from tools.diagnostics.capture_native_activation import capture


class CaptureNativeActivationTests(unittest.TestCase):
    def test_failure_is_retained_and_later_endpoints_are_captured(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if url.endswith("/scene"):
                raise OSError("controller unavailable")
            return {"actual": {"counter": 5}, "unsupported": None}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.jsonl"
            failures = capture("http://wall/", path, samples=2, interval=0,
                               activation_id="failed/id", fetch=fetch,
                               sleep=lambda _: None)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(failures, 2)
            self.assertEqual(len(records), 8)
            self.assertIn("controller unavailable", records[0]["error"])
            self.assertEqual(records[1]["payload"]["unsupported"], None)
            self.assertTrue(calls[-1].endswith("failed%2Fid"))
            self.assertEqual(records[-1]["sample"], 1)
            self.assertTrue(all(r["evidence_kind"].endswith("not_acceptance")
                                for r in records))

    def test_existing_evidence_is_not_overwritten_or_queried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.jsonl"
            path.write_text("original\n")
            with self.assertRaises(FileExistsError):
                capture("http://wall", path,
                        fetch=lambda _: self.fail("must not query"))
            self.assertEqual(path.read_text(), "original\n")

    def test_completed_endpoint_survives_interruption(self):
        calls = 0

        def fetch(_):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise KeyboardInterrupt()
            return {"before_interruption": True}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.jsonl"
            with self.assertRaises(KeyboardInterrupt):
                capture("http://wall", path, fetch=fetch)
            records = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(len(records), 1)
            self.assertTrue(records[0]["payload"]["before_interruption"])
