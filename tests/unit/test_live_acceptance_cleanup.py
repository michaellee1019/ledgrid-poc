"""Focused safety coverage for retired live mutation tooling."""

from __future__ import annotations

import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import capture_webcam_wall
from tools.benchmarks import live_animation_sweep, output_rate_sweep, receiver_acceptance
from tools.benchmarks.live_display_state import (
    DisplayStateError,
    canonical_scene_digest,
    require_active_scene,
)


ROOT = Path(__file__).resolve().parents[2]
SCENE = {
    "schema": "ledgrid.scene-state",
    "schema_version": 1,
    "revision": 7,
    "background": {
        "plugin_id": "rainbow",
        "provider": "python",
        "resolved_parameters": {"speed": 0.5},
    },
    "overlays": [],
}
DIGEST = canonical_scene_digest(SCENE)


class LiveDisplayIdentityTests(unittest.TestCase):
    def test_exact_complete_scene_digest_is_required(self):
        observed = []
        identity = require_active_scene(
            "http://wall", DIGEST,
            lambda url: observed.append(url) or {"scene_state": SCENE},
            expected_plugin="rainbow", expected_provider="python",
        )
        self.assertEqual(identity.scene_digest, DIGEST)
        self.assertEqual(identity.scene_revision, 7)
        self.assertEqual(observed, ["http://wall/api/status"])

        changed = {**SCENE, "background": {**SCENE["background"], "resolved_parameters": {"speed": 0.6}}}
        with self.assertRaisesRegex(DisplayStateError, "does not match"):
            require_active_scene(
                "http://wall", DIGEST, lambda _url: {"scene_state": changed}
            )

    def test_invalid_receipt_digest_rejects_before_status_request(self):
        get_json = Mock()
        with self.assertRaisesRegex(DisplayStateError, "activation receipt"):
            require_active_scene("http://wall", "not-a-digest", get_json)
        get_json.assert_not_called()


class RetiredMutationCommandTests(unittest.TestCase):
    def test_calibration_capture_rejects_before_files_camera_or_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "must-not-exist"
            with patch.object(
                sys, "argv", ["capture_webcam_wall.py", "--output-dir", str(output)]
            ), patch("sys.stderr", new_callable=io.StringIO) as stderr:
                with self.assertRaises(SystemExit) as exited:
                    capture_webcam_wall.main()
            self.assertEqual(exited.exception.code, 2)
            self.assertIn("made no wall or camera changes", stderr.getvalue())
            self.assertFalse(output.exists())

    def test_multi_scene_sweep_rejects_before_any_callback(self):
        sleep = Mock()
        with patch.object(
            sys, "argv", ["live_animation_sweep.py", "--seconds", "0.1"]
        ), patch.object(live_animation_sweep, "main", wraps=live_animation_sweep.main), patch(
            "tools.benchmarks.live_animation_sweep.math.isfinite", return_value=True
        ), patch("time.sleep", sleep), patch(
            "sys.stderr", new_callable=io.StringIO
        ) as stderr:
            with self.assertRaises(SystemExit) as exited:
                live_animation_sweep.main()
        self.assertEqual(exited.exception.code, 2)
        self.assertIn("multi-scene live sweep is retired", stderr.getvalue())
        sleep.assert_not_called()

    def test_output_observer_identity_rejection_never_sleeps(self):
        sleep = Mock()
        get_json = Mock(return_value={"scene_state": SCENE})
        with patch.object(
            sys, "argv", [
                "output_rate_sweep.py", "--seconds", "0.1",
                "--expected-scene-digest", "0" * 64,
            ]
        ), patch.object(output_rate_sweep, "_get_json", get_json), patch.object(
            output_rate_sweep.time, "sleep", sleep
        ), patch("sys.stdout", new_callable=io.StringIO) as stdout:
            with self.assertRaises(SystemExit) as exited:
                output_rate_sweep.main()
        self.assertEqual(exited.exception.code, 1)
        self.assertIn("does not match", stdout.getvalue())
        self.assertEqual(get_json.call_count, 1)
        sleep.assert_not_called()

    def test_receiver_observer_identity_rejection_makes_no_post_or_sleep(self):
        post = Mock()
        sleep = Mock()
        get_json = Mock(return_value={"scene_state": SCENE})
        with patch.object(
            sys, "argv", [
                "receiver_acceptance.py", "--duration", "0.1", "--warmup", "0",
                "--expected-scene-digest", "0" * 64,
            ]
        ), patch.object(receiver_acceptance, "_get_json", get_json), patch.object(
            receiver_acceptance, "_post_json", post
        ), patch.object(receiver_acceptance.time, "sleep", sleep), patch(
            "sys.stdout", new_callable=io.StringIO
        ) as stdout:
            with self.assertRaises(SystemExit) as exited:
                receiver_acceptance.main()
        self.assertEqual(exited.exception.code, 1)
        self.assertIn("does not match", stdout.getvalue())
        post.assert_not_called()
        sleep.assert_not_called()

    def test_remote_diagnostics_animation_rejects_before_output_or_ssh(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "diagnostics.out"
            env = dict(os.environ, ANIMATION="rainbow", OUT_FILE=str(output))
            result = subprocess.run(
                [str(ROOT / "tools/diagnostics/remote_diagnostics.sh")],
                cwd=ROOT, env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("no files or remote state were changed", result.stderr)
            self.assertFalse(output.exists())


class MaintainedToolDebtScanTests(unittest.TestCase):
    def test_no_retired_scene_mutation_alias_remains_in_maintained_tools(self):
        # Receipt-bound observers may read one correlated activation resource.
        # Direct scene/check/activation submission and the retired start/stop
        # aliases remain forbidden in maintained command-line tooling.
        forbidden = re.compile(
            r"/api/start/|/api/stop\b|/api/v1/scene(?!/activations/)"
        )
        maintained = sorted(
            path
            for root in (ROOT / "scripts", ROOT / "tools")
            for suffix in ("*.py", "*.sh")
            for path in root.rglob(suffix)
            if "tools/browser_qualification" not in path.as_posix()
        )
        failures = []
        for path in maintained:
            source = path.read_text(encoding="utf-8")
            if forbidden.search(source):
                failures.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
