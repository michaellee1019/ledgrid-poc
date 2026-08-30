from __future__ import annotations

import copy
import unittest
import uuid

from web.maintenance_api import (
    MAINTENANCE_SCHEMA,
    MAINTENANCE_SCHEMA_VERSION,
    MaintenanceRequest,
    MaintenanceRequestError,
    MaintenanceRunError,
    MaintenanceRunner,
    build_frame,
    frame_digest,
    identity_from_status,
)


def _status(*, revision: int = 7, connected: bool = True):
    return {
        "controller_session_id": "session-a",
        "controller_state_revision": revision,
        "receiver_roster": [
            {
                "logical_device": index,
                "connected": connected,
                "route": [index // 2, index % 2],
                "hardware_serial": f"aa:bb:cc:dd:ee:{index:02x}",
                "firmware_revision": "v7",
            }
            for index in range(5)
        ],
    }


def _request(status, *, diagnostic="receiver_band", target=None, duration=0.25):
    if target is None:
        target = {"receiver_id": 2}
    return {
        "schema": MAINTENANCE_SCHEMA,
        "schema_version": MAINTENANCE_SCHEMA_VERSION,
        "request_id": str(uuid.UUID("12345678-1234-4234-9234-123456789abc")),
        "diagnostic": diagnostic,
        "target": target,
        "intensity": 32,
        "duration_seconds": duration,
        "expected_identity": identity_from_status(status).to_dict(),
        "provenance": {
            "operator": "test operator",
            "source_revision": "a" * 40,
            "purpose": "receiver wiring verification",
        },
    }


class MaintenanceFramesTests(unittest.TestCase):
    def test_named_frames_are_bounded_and_never_accept_raw_pixels(self):
        status = _status()
        cases = (
            ("receiver_band", {"receiver_id": 1}),
            ("strip_ramp", {"strip": 9}),
            ("direction_sentinel", {"strip": 9}),
            ("sparse_boundary", {"receiver_id": 1}),
            ("tail_lane_probe", {"receiver_id": 4, "lane": 0}),
        )
        for diagnostic, target in cases:
            with self.subTest(diagnostic=diagnostic):
                request = MaintenanceRequest.from_mapping(
                    _request(status, diagnostic=diagnostic, target=target)
                )
                frame = build_frame(request)
                self.assertEqual(len(frame), 33 * 138)
                self.assertTrue(any(pixel != (0, 0, 0) for pixel in frame))
                self.assertEqual(len(frame_digest(request)), 64)

        malicious = _request(status)
        malicious["frame_data"] = [[255, 0, 255]] * (33 * 138)
        with self.assertRaisesRegex(MaintenanceRequestError, "exactly"):
            MaintenanceRequest.from_mapping(malicious)

    def test_rejects_unbounded_or_invalid_target_requests(self):
        status = _status()
        for mutate, message in (
            (lambda value: value.__setitem__("duration_seconds", 30.1), "duration_seconds"),
            (lambda value: value.__setitem__("intensity", 65), "intensity"),
            (lambda value: value.__setitem__("target", {"receiver_id": 4}), "exactly"),
            (lambda value: value.__setitem__("target", {"receiver_id": 3, "lane": 0}), "tail receiver"),
        ):
            value = _request(status, diagnostic="tail_lane_probe", target={"receiver_id": 4, "lane": 0})
            mutate(value)
            with self.subTest(message=message), self.assertRaisesRegex(MaintenanceRequestError, message):
                MaintenanceRequest.from_mapping(value)

    def test_acknowledged_request_restores_exact_prior_state(self):
        current = _status()
        request = MaintenanceRequest.from_mapping(_request(current))
        restored = []
        applied = []

        def apply(parsed, frame):
            applied.append((parsed, frame))
            return {
                "request_id": parsed.request_id,
                **parsed.expected_identity.to_dict(),
                "acknowledged_receivers": [0, 1, 2, 3, 4],
            }

        runner = MaintenanceRunner(
            status=lambda: current,
            apply=apply,
            restore=lambda prior: restored.append(prior),
            safe_idle=lambda: self.fail("safe idle should not be needed"),
            locally_guarded=True,
            sleep=lambda seconds: self.assertEqual(seconds, 0.25),
        )
        receipt = runner.run(request)
        self.assertTrue(receipt["restored"])
        self.assertEqual(restored, [current])
        self.assertEqual(applied[0][0], request)
        self.assertEqual(receipt["target_receivers"], [2])

    def test_stale_disconnect_and_partial_ack_fail_visibly_and_restore(self):
        for kind in ("stale", "disconnect", "partial"):
            with self.subTest(kind=kind):
                before = _status()
                request = MaintenanceRequest.from_mapping(_request(before))
                restored = []
                after = copy.deepcopy(before)
                if kind == "stale":
                    after["controller_state_revision"] += 1
                if kind == "disconnect":
                    after["receiver_roster"][2]["connected"] = False

                def apply(parsed, _frame):
                    acknowledged = [0, 1, 3, 4] if kind == "partial" else [0, 1, 2, 3, 4]
                    return {
                        "request_id": parsed.request_id,
                        **parsed.expected_identity.to_dict(),
                        "acknowledged_receivers": acknowledged,
                    }

                calls = []

                def status():
                    calls.append(True)
                    return before if len(calls) == 1 else after

                runner = MaintenanceRunner(
                    status=status,
                    apply=apply,
                    restore=lambda prior: restored.append(prior),
                    safe_idle=lambda: self.fail("restore should succeed"),
                    locally_guarded=True,
                    sleep=lambda _seconds: None,
                )
                with self.assertRaises(MaintenanceRunError):
                    runner.run(request)
                self.assertEqual(restored, [before])

    def test_restore_failure_falls_back_to_safe_idle_and_unguarded_output_is_rejected(self):
        current = _status()
        request = MaintenanceRequest.from_mapping(_request(current))
        safe_idle = []

        unguarded = MaintenanceRunner(
            status=lambda: current,
            apply=lambda *_args: self.fail("must not apply"),
            restore=lambda _prior: self.fail("must not restore"),
            safe_idle=lambda: self.fail("must not idle"),
            locally_guarded=False,
        )
        with self.assertRaisesRegex(MaintenanceRunError, "requires a local guard"):
            unguarded.run(request)

        runner = MaintenanceRunner(
            status=lambda: current,
            apply=lambda parsed, _frame: {
                "request_id": parsed.request_id,
                **parsed.expected_identity.to_dict(),
                "acknowledged_receivers": [0, 1, 2, 3, 4],
            },
            restore=lambda _prior: (_ for _ in ()).throw(RuntimeError("restore transport lost")),
            safe_idle=lambda: safe_idle.append(True),
            locally_guarded=True,
            sleep=lambda _seconds: None,
        )
        with self.assertRaisesRegex(MaintenanceRunError, "restore failed"):
            runner.run(request)
        self.assertEqual(safe_idle, [True])


if __name__ == "__main__":
    unittest.main()
