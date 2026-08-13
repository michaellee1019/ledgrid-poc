"""Focused safety coverage for live hardware acceptance cleanup."""

from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import Mock, patch

from tools.benchmarks import live_animation_sweep, output_rate_sweep, receiver_acceptance
from tools.benchmarks.live_display_state import (
    DisplayStateError,
    SceneSnapshot,
    capture_scene,
    capture_target_fps,
    restore_scene,
    restore_target_fps,
)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class LiveDisplayStateTests(unittest.TestCase):
    def test_capture_copies_active_scene_and_canonicalizes_idle(self):
        scene = {"schema": "ledgrid.scene-state", "background": {"plugin_id": "solid"}}
        active = capture_scene("http://wall", lambda _url: {"active": True, "scene": scene})
        scene["background"]["plugin_id"] = "changed"
        self.assertEqual(active.scene["background"]["plugin_id"], "solid")
        self.assertEqual(
            capture_scene(
                "http://wall", lambda _url: {"active": False, "scene": {"stale": True}}
            ),
            SceneSnapshot(active=False, scene=None),
        )

    def test_capture_rejects_unrestorable_state(self):
        for payload in ({}, {"active": "yes"}, {"active": True, "scene": None}):
            with self.subTest(payload=payload), self.assertRaises(DisplayStateError):
                capture_scene("http://wall", lambda _url, value=payload: value)

    def test_restore_active_scene_posts_exact_snapshot_and_waits_for_observation(self):
        snapshot = SceneSnapshot(True, {"schema": "scene", "revision": 7})
        posted = []
        responses = iter([
            {"active": True, "scene": {"schema": "scene", "revision": 6}},
            {"active": True, "scene": snapshot.scene},
        ])
        clock = FakeClock()
        restore_scene(
            "http://wall", snapshot,
            get_json=lambda _url: next(responses),
            post_json=lambda url, body: posted.append((url, body)),
            delete_json=lambda _url: self.fail("active restore must not delete"),
            clock=clock, sleeper=clock.sleep,
        )
        self.assertEqual(posted, [("http://wall/api/v1/scene", snapshot.scene)])

    def test_restore_idle_deletes_and_timeout_fails_closed(self):
        deleted = []
        restore_scene(
            "http://wall", SceneSnapshot(False, None),
            get_json=lambda _url: {"active": False, "scene": None},
            post_json=lambda _url, _body: self.fail("idle restore must not post"),
            delete_json=deleted.append,
        )
        self.assertEqual(deleted, ["http://wall/api/v1/scene"])

        clock = FakeClock()
        with self.assertRaisesRegex(DisplayStateError, "not observed"):
            restore_scene(
                "http://wall", SceneSnapshot(False, None),
                get_json=lambda _url: {"active": True, "scene": {"wrong": True}},
                post_json=lambda _url, _body: None,
                delete_json=lambda _url: None,
                timeout=0.2, poll_interval=0.1, clock=clock, sleeper=clock.sleep,
            )

    def test_target_fps_capture_restore_and_timeout(self):
        self.assertEqual(
            capture_target_fps(
                "http://wall", lambda _url: {"animation": {"target_fps": 160}}
            ),
            160,
        )
        for value in (True, 0, 201, "160", None):
            with self.subTest(value=value), self.assertRaises(DisplayStateError):
                capture_target_fps(
                    "http://wall", lambda _url, item=value: {"animation": {"target_fps": item}}
                )

        posts = []
        restore_target_fps(
            "http://wall", 144,
            get_json=lambda _url: {"animation": {"target_fps": 144}},
            post_json=lambda url, body: posts.append((url, body)),
        )
        self.assertEqual(posts, [
            ("http://wall/api/config/target-fps", {"target_fps": 144})
        ])

        clock = FakeClock()
        with self.assertRaisesRegex(DisplayStateError, "not observed"):
            restore_target_fps(
                "http://wall", 144,
                get_json=lambda _url: {"animation": {"target_fps": 160}},
                post_json=lambda _url, _body: None,
                timeout=0.2, poll_interval=0.1, clock=clock, sleeper=clock.sleep,
            )


class AcceptanceCommandCleanupTests(unittest.TestCase):
    @staticmethod
    def _receiver_metrics(sequence):
        return {
            "driver": {"devices": [{
                "receiver_status_version": 3,
                "receiver_frames_accepted": sequence,
                "receiver_frames_displayed": sequence,
                "receiver_frames_superseded": 0,
                "receiver_crc_errors": 0,
                "receiver_publish_drops": 0,
                "receiver_spi_queue_errors": 0,
                "receiver_display_errors": 0,
                "receiver_status_misses": 0,
                "receiver_last_encode_us": 500,
                "receiver_last_show_us": 4400,
            }]}
        }

    def _run_receiver_acceptance(
        self, scene_restore_side_effect=None, fps_restore_side_effect=None
    ):
        snapshot = SceneSnapshot(True, {"schema": "scene"})
        times = iter([0.0, 0.0, 0.005, 0.02, 0.02])
        metrics = iter([self._receiver_metrics(0), self._receiver_metrics(4)])
        restore = Mock(side_effect=scene_restore_side_effect)
        restore_fps = Mock(side_effect=fps_restore_side_effect)
        argv = [
            "receiver_acceptance.py", "--device", "0", "--duration", "0.01",
            "--interval", "0.001", "--warmup", "0", "--min-displayed-fps", "1",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch.object(receiver_acceptance, "capture_scene", return_value=snapshot),
            patch.object(receiver_acceptance, "capture_target_fps", return_value=160),
            patch.object(receiver_acceptance, "restore_scene", restore),
            patch.object(receiver_acceptance, "restore_target_fps", restore_fps),
            patch.object(receiver_acceptance, "_post_json", return_value={}),
            patch.object(receiver_acceptance, "_get_json", side_effect=lambda _url: next(metrics)),
            patch.object(receiver_acceptance.time, "monotonic", side_effect=lambda: next(times)),
            patch.object(receiver_acceptance.time, "sleep", return_value=None),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            with self.assertRaises(SystemExit) as exited:
                receiver_acceptance.main()
        return exited.exception.code, stdout.getvalue(), restore, restore_fps

    def test_receiver_acceptance_restores_scene_on_success(self):
        code, output, restore, restore_fps = self._run_receiver_acceptance()
        self.assertEqual(code, 0, output)
        self.assertIn('"scene_restored": true', output)
        self.assertIn('"target_fps_restored": true', output)
        restore.assert_called_once()
        self.assertEqual(
            [call.args[1] for call in restore_fps.call_args_list], [200, 160]
        )

    def test_receiver_acceptance_cleanup_failure_overrides_measurement_pass(self):
        code, output, restore, restore_fps = self._run_receiver_acceptance(
            scene_restore_side_effect=DisplayStateError("restore rejected")
        )
        self.assertEqual(code, 1)
        self.assertIn("cleanup failed: scene: restore rejected", output)
        self.assertIn('"scene_restored": false', output)
        self.assertIn('"target_fps_restored": true', output)
        restore.assert_called_once()
        self.assertEqual(
            [call.args[1] for call in restore_fps.call_args_list], [200, 160]
        )

    def test_receiver_acceptance_fps_cleanup_failure_does_not_misreport_scene(self):
        code, output, restore, restore_fps = self._run_receiver_acceptance(
            fps_restore_side_effect=[None, DisplayStateError("FPS restore rejected")]
        )
        self.assertEqual(code, 1)
        self.assertIn("cleanup failed: target FPS: FPS restore rejected", output)
        self.assertIn('"scene_restored": true', output)
        self.assertIn('"target_fps_restored": false', output)
        restore.assert_called_once()
        self.assertEqual(
            [call.args[1] for call in restore_fps.call_args_list], [200, 160]
        )

    def test_live_sweep_restores_scene_after_body_failure(self):
        snapshot = SceneSnapshot(True, {"schema": "scene"})
        restore = Mock()
        with (
            patch.object(sys, "argv", ["live_animation_sweep.py", "--seconds", "0.1"]),
            patch.object(live_animation_sweep, "capture_scene", return_value=snapshot),
            patch.object(live_animation_sweep, "restore_scene", restore),
            patch.object(live_animation_sweep, "_get_json", side_effect=OSError("metrics down")),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            with self.assertRaises(SystemExit) as exited:
                live_animation_sweep.main()
        self.assertEqual(exited.exception.code, 1)
        self.assertIn("metrics down", stdout.getvalue())
        self.assertIn('"scene_restored": true', stdout.getvalue())
        restore.assert_called_once()

    def test_output_rate_sweep_restores_scene_and_fps_after_start_failure(self):
        snapshot = SceneSnapshot(True, {"schema": "scene"})
        restore = Mock()
        restore_fps = Mock()
        with (
            patch.object(
                sys, "argv",
                ["output_rate_sweep.py", "--rates", "200", "--seconds", "0.1"],
            ),
            patch.object(output_rate_sweep, "capture_scene", return_value=snapshot),
            patch.object(output_rate_sweep, "capture_target_fps", return_value=160),
            patch.object(output_rate_sweep, "restore_scene", restore),
            patch.object(output_rate_sweep, "restore_target_fps", restore_fps),
            patch.object(output_rate_sweep, "_post_json", side_effect=OSError("start failed")),
            patch("sys.stdout", new_callable=io.StringIO) as stdout,
        ):
            with self.assertRaises(SystemExit) as exited:
                output_rate_sweep.main()
        self.assertEqual(exited.exception.code, 1)
        self.assertIn("start failed", stdout.getvalue())
        self.assertIn('"target_fps_restored": true', stdout.getvalue())
        self.assertIn('"scene_restored": true', stdout.getvalue())
        restore_fps.assert_called_once()
        restore.assert_called_once()


if __name__ == "__main__":
    unittest.main()
