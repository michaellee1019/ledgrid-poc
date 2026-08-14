"""Focused lifecycle acceptance for the manager-side sparse publisher."""

from __future__ import annotations

import unittest

import numpy as np

from animation.core.presentation_contracts import OverlayFrame
from animation.core.receiver_sparse_publisher import ReceiverSparsePublisher


class _Sessions:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return bytes((self.value,)) * 16


class _Controller:
    total_leds = 8

    def __init__(self):
        self.calls = []
        self.session = None
        self.generation = 0
        self.publish_behavior = "ok"
        self.renew_behavior = "ok"
        self.clear_behavior = "ok"
        self.status = {"state": "active", "operation": "test"}

    def publish_sparse_overlay(self, pixels, **fields):
        self.calls.append(("publish", np.asarray(pixels).copy(), dict(fields)))
        behavior = self.publish_behavior
        self.publish_behavior = "ok"
        if behavior == "raise":
            raise OSError("publish transport failed")
        if behavior == "repair_required":
            self.status = {
                "state": "foreground_repair_required",
                "operation": "foreground_delta_preflight",
                "error": "receiver lease expired",
                "foreground_generation": self.generation,
            }
            return False
        if behavior == "repair_generation_mismatch":
            self.status = {
                "state": "foreground_repair_required",
                "operation": "foreground_delta_preflight",
                "error": "receiver lease expired",
                "foreground_generation": self.generation + 1,
            }
            return False
        if behavior == "compensated":
            self.session = fields["controller_session_id"]
            self.generation = fields["generation"] + 1
            self.status = {
                "state": "foreground_cleared",
                "operation": "foreground_publish_failed",
                "error": "patch rejected",
                "cleanup_errors": [],
                "foreground_generation": self.generation,
            }
            return False
        if behavior == "compensation_generation_mismatch":
            self.status = {
                "state": "foreground_cleared",
                "operation": "foreground_publish_failed",
                "error": "contradictory compensation status",
                "cleanup_errors": [],
                "foreground_generation": fields["generation"],
            }
            return False
        if behavior == "cleanup_failed":
            self.session = None
            self.generation = 0
            self.status = {
                "state": "degraded",
                "operation": "foreground_publish_failed",
                "error": "partial commit",
                "cleanup_errors": [
                    {"logical_device": 2, "error": "clear failed"}
                ],
            }
            return False

        requested_session = fields["controller_session_id"]
        if requested_session != self.session:
            if fields["prior_generation"] != 0:
                raise AssertionError("new session did not reset prior generation")
            self.session = requested_session
            self.generation = 0
        if fields["prior_generation"] != self.generation:
            raise AssertionError("publisher generation diverged from controller")
        self.generation = fields["generation"]
        self.status = {
            "state": "active",
            "operation": "foreground_publish",
            "foreground_generation": self.generation,
        }
        return True

    def renew_sparse_overlay(self, **fields):
        self.calls.append(("renew", dict(fields)))
        behavior = self.renew_behavior
        self.renew_behavior = "ok"
        if behavior == "raise":
            raise OSError("renew transport failed")
        if behavior == "compensated":
            self.generation = fields["generation"] + 1
            self.status = {
                "state": "foreground_cleared",
                "operation": "foreground_renew_failed",
                "error": "renew rejected",
                "cleanup_errors": [],
                "foreground_generation": self.generation,
            }
            return False
        if behavior == "cleanup_failed":
            self.status = {
                "state": "degraded",
                "operation": "foreground_renew_failed",
                "error": "renew compensation failed",
                "cleanup_errors": [{"logical_device": 1, "error": "lost"}],
            }
            return False
        self.status = {
            "state": "active",
            "operation": "foreground_renew",
            "foreground_generation": self.generation,
        }
        return True

    def clear_sparse_overlay(self, **fields):
        self.calls.append(("clear", dict(fields)))
        behavior = self.clear_behavior
        self.clear_behavior = "ok"
        if behavior == "raise":
            raise OSError("clear transport failed")
        if behavior == "failed":
            self.session = None
            self.generation = 0
            self.status = {
                "state": "degraded",
                "operation": "foreground_clear",
                "error": "clear disagreement",
                "errors": [{"logical_device": 3, "error": "stuck"}],
            }
            return False
        self.generation = fields["generation"]
        self.status = {
            "state": "active",
            "operation": "foreground_clear",
            "foreground_generation": self.generation,
        }
        return True

    def get_stats(self):
        return {"aggregate": {"local_background": dict(self.status)}}


def _pixels(position=0):
    pixels = np.zeros((_Controller.total_leds, 4), dtype=np.uint8)
    pixels[position] = (20, 10, 5, 20)
    return pixels


def _fields(**overrides):
    result = {
        "changed": True,
        "dirty_ranges": ((0, 1),),
        "scene_revision": 1,
        "scene_epoch": 11,
        "base_revision": 1,
        "present_at_scene_time_us": 100,
        "now": 0.0,
    }
    result.update(overrides)
    return result


class ReceiverSparsePublisherTests(unittest.TestCase):
    def make_publisher(self, controller=None, **kwargs):
        controller = controller or _Controller()
        sessions = kwargs.pop("session_factory", _Sessions())
        return controller, ReceiverSparsePublisher(
            controller,
            session_factory=sessions,
            **kwargs,
        )

    def test_initial_frame_is_authoritative_then_clock_tick_is_sparse_delta(self):
        controller, publisher = self.make_publisher()

        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        initial = controller.calls[0][2]
        self.assertTrue(initial["full_snapshot"])
        self.assertIsNone(initial["dirty_ranges"])
        self.assertEqual((initial["prior_generation"], initial["generation"]), (0, 1))

        self.assertTrue(publisher.publish(
            _pixels(3), **_fields(
                dirty_ranges=((0, 1), (3, 4)),
                present_at_scene_time_us=1_000_100,
                now=1.0,
            )
        ))
        delta = controller.calls[-1][2]
        self.assertFalse(delta["full_snapshot"])
        self.assertEqual(delta["dirty_ranges"], ((0, 1), (3, 4)))
        self.assertEqual((delta["prior_generation"], delta["generation"]), (1, 2))
        status = publisher.get_status()
        self.assertTrue(status["healthy"])
        self.assertEqual(status["counts"]["full_snapshots"], 1)
        self.assertEqual(status["counts"]["delta_generations"], 1)

    def test_overlay_frame_boundary_reuses_existing_contract(self):
        controller, publisher = self.make_publisher()
        frame = OverlayFrame(_pixels(), revision=1, dirty_ranges=((0, 1),))
        self.assertTrue(publisher.publish_frame(
            frame,
            scene_revision=1,
            scene_epoch=11,
            base_revision=1,
            present_at_scene_time_us=100,
            now=0,
        ))
        self.assertEqual(controller.calls[0][0], "publish")
        with self.assertRaisesRegex(TypeError, "OverlayFrame"):
            publisher.publish_frame(
                object(), scene_revision=1, scene_epoch=1,
                present_at_scene_time_us=0,
            )

    def test_unchanged_calls_are_idle_then_renew_once_at_bounded_interval(self):
        controller, publisher = self.make_publisher()
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=0.999)
        ))
        self.assertEqual([call[0] for call in controller.calls], ["publish"])

        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=1.0)
        ))
        self.assertEqual([call[0] for call in controller.calls], ["publish", "renew"])
        renew = controller.calls[-1][1]
        self.assertEqual(renew["generation"], 1)
        self.assertEqual(renew["lease_ms"], 3000)

    def test_periodic_repair_preempts_renew_and_sends_complete_snapshot(self):
        controller, publisher = self.make_publisher()
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=30.0)
        ))
        self.assertEqual([call[0] for call in controller.calls], ["publish", "publish"])
        repair = controller.calls[-1][2]
        self.assertTrue(repair["full_snapshot"])
        self.assertEqual(repair["generation"], 2)
        self.assertEqual(publisher.get_status()["last_operation"], "repair_snapshot")

    def test_binding_change_forces_full_snapshot_and_must_advance_revision(self):
        controller, publisher = self.make_publisher()
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        self.assertTrue(publisher.publish(
            _pixels(), **_fields(
                changed=False, dirty_ranges=None, scene_revision=2,
                base_revision=2, now=0.1,
            )
        ))
        self.assertTrue(controller.calls[-1][2]["full_snapshot"])
        self.assertEqual(
            publisher.get_status()["binding"],
            {"scene_revision": 2, "scene_epoch": 11, "base_revision": 2},
        )
        before = len(controller.calls)
        with self.assertRaisesRegex(ValueError, "advance scene_revision"):
            publisher.publish(
                _pixels(), **_fields(
                    changed=False, dirty_ranges=None, scene_revision=2,
                    base_revision=2, scene_epoch=12, now=0.2,
                )
            )
        self.assertEqual(len(controller.calls), before)

    def test_changed_frame_without_dirty_ranges_falls_back_to_full_snapshot(self):
        controller, publisher = self.make_publisher()
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        self.assertTrue(publisher.publish(
            _pixels(2), **_fields(dirty_ranges=None, now=0.5)
        ))
        self.assertTrue(controller.calls[-1][2]["full_snapshot"])
        self.assertEqual(publisher.get_status()["last_operation"], "changed_snapshot")

    def test_publish_failure_tracks_compensation_generation_then_repairs(self):
        controller, publisher = self.make_publisher()
        controller.publish_behavior = "compensated"
        self.assertFalse(publisher.publish(_pixels(), **_fields()))
        failed = publisher.get_status()
        self.assertFalse(failed["healthy"])
        self.assertEqual(failed["generation"], 2)
        self.assertTrue(failed["repair_required"])
        self.assertEqual(failed["last_operation"], "initial_snapshot_failed")

        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=0.1)
        ))
        repair = controller.calls[-1][2]
        self.assertEqual((repair["prior_generation"], repair["generation"]), (2, 3))
        self.assertTrue(repair["full_snapshot"])

    def test_delta_expiry_retains_generation_and_requires_full_repair(self):
        controller, publisher = self.make_publisher()
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        controller.publish_behavior = "repair_required"
        self.assertFalse(publisher.publish(
            _pixels(2), **_fields(dirty_ranges=((0, 1), (2, 3)), now=1.0)
        ))
        self.assertEqual(publisher.get_status()["generation"], 1)
        self.assertTrue(publisher.publish(
            _pixels(2), **_fields(changed=False, dirty_ranges=None, now=1.1)
        ))
        fields = controller.calls[-1][2]
        self.assertEqual((fields["prior_generation"], fields["generation"]), (1, 2))
        self.assertTrue(fields["full_snapshot"])

    def test_contradictory_failure_generation_rotates_to_safe_new_session(self):
        sessions = _Sessions()
        controller, publisher = self.make_publisher(session_factory=sessions)
        first_session = publisher.controller_session_id
        controller.publish_behavior = "compensation_generation_mismatch"

        self.assertFalse(publisher.publish(_pixels(), **_fields()))
        self.assertNotEqual(publisher.controller_session_id, first_session)
        self.assertEqual(publisher.get_status()["generation"], 0)

        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=0.1)
        ))
        self.assertTrue(controller.calls[-1][2]["full_snapshot"])
        safe_session = publisher.controller_session_id
        controller.publish_behavior = "repair_generation_mismatch"
        self.assertFalse(publisher.publish(
            _pixels(2), **_fields(dirty_ranges=((2, 3),), now=0.2)
        ))
        self.assertNotEqual(publisher.controller_session_id, safe_session)
        self.assertEqual(publisher.get_status()["generation"], 0)

    def test_failed_compensation_rotates_session_and_restarts_at_generation_one(self):
        sessions = _Sessions()
        controller, publisher = self.make_publisher(session_factory=sessions)
        first_session = publisher.get_status()["controller_session_id"]
        controller.publish_behavior = "cleanup_failed"
        self.assertFalse(publisher.publish(_pixels(), **_fields()))
        failed = publisher.get_status()
        self.assertNotEqual(failed["controller_session_id"], first_session)
        self.assertEqual(failed["generation"], 0)

        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=0.1)
        ))
        fields = controller.calls[-1][2]
        self.assertEqual((fields["prior_generation"], fields["generation"]), (0, 1))
        self.assertTrue(fields["full_snapshot"])

    def test_renew_failure_synchronizes_compensation_or_rotates_unknown_authority(self):
        controller, publisher = self.make_publisher()
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        controller.renew_behavior = "compensated"
        self.assertFalse(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=1.0)
        ))
        self.assertEqual(publisher.get_status()["generation"], 2)
        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=1.1)
        ))
        self.assertEqual(controller.calls[-1][2]["prior_generation"], 2)

        controller.renew_behavior = "cleanup_failed"
        self.assertFalse(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=2.2)
        ))
        self.assertEqual(publisher.get_status()["generation"], 0)
        self.assertTrue(publisher.get_status()["repair_required"])

    def test_generation_exhaustion_rotates_session_with_compensation_headroom(self):
        sessions = _Sessions()
        controller, publisher = self.make_publisher(
            session_factory=sessions, generation_limit=4,
        )
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        for generation in (2, 3):
            self.assertTrue(publisher.publish(
                _pixels(generation), **_fields(
                    dirty_ranges=((generation, generation + 1),),
                    now=float(generation),
                )
            ))
        exhausted_session = controller.calls[-1][2]["controller_session_id"]

        self.assertTrue(publisher.publish(
            _pixels(4), **_fields(dirty_ranges=((4, 5),), now=4.0)
        ))
        rollover = controller.calls[-1][2]
        self.assertNotEqual(rollover["controller_session_id"], exhausted_session)
        self.assertEqual((rollover["prior_generation"], rollover["generation"]), (0, 1))
        self.assertTrue(rollover["full_snapshot"])

    def test_explicit_new_session_always_restarts_with_authoritative_snapshot(self):
        controller, publisher = self.make_publisher()
        first = publisher.controller_session_id
        self.assertIs(type(first), bytes)
        self.assertEqual(len(first), 16)
        self.assertIsNot(first, publisher.controller_session_id)
        self.assertEqual(first, publisher.controller_session_id)
        self.assertEqual(first.hex(), publisher.get_status()["controller_session_id"])
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        second = publisher.begin_new_session()
        self.assertEqual(second, publisher.controller_session_id)
        self.assertIs(type(publisher.controller_session_id), bytes)
        self.assertNotEqual(publisher.controller_session_id, first)
        self.assertEqual(
            publisher.controller_session_id.hex(),
            publisher.get_status()["controller_session_id"],
        )
        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=0.1)
        ))
        fields = controller.calls[-1][2]
        self.assertTrue(fields["full_snapshot"])
        self.assertEqual((fields["prior_generation"], fields["generation"]), (0, 1))

    def test_clear_is_transparent_semantic_stop_and_close_performs_no_later_io(self):
        controller, publisher = self.make_publisher()
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        self.assertTrue(publisher.clear())
        clear = controller.calls[-1]
        self.assertEqual(clear[0], "clear")
        self.assertEqual(clear[1]["generation"], 2)
        self.assertFalse(publisher.get_status()["active"])

        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=0.1)
        ))
        calls_before_close = len(controller.calls)
        self.assertTrue(publisher.close())
        self.assertEqual(len(controller.calls), calls_before_close + 1)
        self.assertEqual(controller.calls[-1][0], "clear")
        self.assertTrue(publisher.close())
        self.assertEqual(len(controller.calls), calls_before_close + 1)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            publisher.publish(_pixels(), **_fields(now=0.2))
        with self.assertRaisesRegex(RuntimeError, "closed"):
            publisher.clear()
        self.assertEqual(len(controller.calls), calls_before_close + 1)

    def test_force_repair_is_io_free_and_next_frame_is_authoritative(self):
        controller, publisher = self.make_publisher()
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        calls_before_request = len(controller.calls)

        publisher.force_repair()
        self.assertEqual(len(controller.calls), calls_before_request)
        self.assertTrue(publisher.get_status()["repair_required"])
        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=0.1)
        ))
        self.assertTrue(controller.calls[-1][2]["full_snapshot"])

    def test_close_records_clear_failure_and_never_retries_io(self):
        controller, publisher = self.make_publisher()
        self.assertTrue(publisher.publish(_pixels(), **_fields()))
        controller.clear_behavior = "failed"

        self.assertFalse(publisher.close())
        calls_after_failure = len(controller.calls)
        self.assertEqual(controller.calls[-1][0], "clear")
        self.assertFalse(publisher.close())
        self.assertEqual(len(controller.calls), calls_after_failure)
        status = publisher.get_status()
        self.assertTrue(status["closed"])
        self.assertEqual(status["last_operation"], "closed_with_error")
        self.assertIn("clear disagreement", status["last_error"])

    def test_transport_exception_is_unhealthy_and_next_attempt_uses_new_session(self):
        controller, publisher = self.make_publisher()
        first = publisher.get_status()["controller_session_id"]
        controller.publish_behavior = "raise"
        self.assertFalse(publisher.publish(_pixels(), **_fields()))
        failed = publisher.get_status()
        self.assertFalse(failed["healthy"])
        self.assertIn("publish transport failed", failed["last_error"])
        self.assertNotEqual(failed["controller_session_id"], first)
        self.assertTrue(publisher.publish(
            _pixels(), **_fields(changed=False, dirty_ranges=None, now=0.1)
        ))

    def test_invalid_policy_timing_pixels_ranges_and_binding_fail_before_io(self):
        controller = _Controller()
        with self.assertRaisesRegex(ValueError, "clear_after_lease"):
            ReceiverSparsePublisher(controller, stale_policy="hold")
        with self.assertRaisesRegex(ValueError, "shorter"):
            ReceiverSparsePublisher(
                controller, lease_ms=1000, renewal_interval_seconds=1.0
            )

        _, publisher = self.make_publisher(controller)
        bad_alpha = _pixels()
        bad_alpha[0] = (2, 0, 0, 1)
        invalid = (
            (np.zeros((7, 4), np.uint8), _fields()),
            (bad_alpha, _fields()),
            (_pixels(), _fields(base_revision=2)),
            (_pixels(), _fields(scene_revision=-1, base_revision=-1)),
            (_pixels(), _fields(dirty_ranges=((2, 2),))),
        )
        for pixels, fields in invalid:
            with self.subTest(fields=fields), self.assertRaises((TypeError, ValueError)):
                publisher.publish(pixels, **fields)
        self.assertEqual(controller.calls, [])


if __name__ == "__main__":
    unittest.main()
