"""Acceptance policy for the installed wall's confirmed SPI1 MISO short."""

from __future__ import annotations

import io
import sys
import unittest
from unittest.mock import patch

from tools.benchmarks import live_animation_sweep, receiver_acceptance


def readable_status(logical_device: int, *, local_canary: bool = False):
    capabilities = (
        receiver_acceptance.CAPABILITY_STATUS_V3
        | receiver_acceptance.CAPABILITY_EXPLICIT_BASE_OWNERSHIP
    )
    if local_canary:
        capabilities |= (
            receiver_acceptance.CAPABILITY_STATIC_LOCAL_BACKGROUND
            | receiver_acceptance.CAPABILITY_PRESENTATION_CONTEXT_V1
        )
    return {
        "receiver_status_seen": True,
        "receiver_status_version": 3,
        "receiver_capabilities": capabilities,
        "receiver_logical_device": logical_device,
    }


def write_only_status(**changes):
    status = {
        "receiver_status_seen": False,
        "receiver_status_version": 0,
        "receiver_capabilities": 0,
        "receiver_logical_device": None,
    }
    status.update(changes)
    return status


def refresh(*, passed=True, errors=None):
    return {
        "request_id": "fresh-1",
        "completed_at": 123.0,
        "passed": passed,
        "errors": list(errors or ()),
    }


class DegradedStatusAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.devices = [
            readable_status(0), readable_status(1),
            write_only_status(), write_only_status(),
        ]

    def evaluate(self, **kwargs):
        return receiver_acceptance.evaluate_phase3a_status(
            self.devices,
            refresh=refresh(),
            expected_refresh_id="fresh-1",
            **kwargs,
        )

    def test_strict_policy_rejects_unreadable_spi1_receivers(self):
        result = self.evaluate()
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["acceptance_policy"]["name"],
            "strict_all_receiver_telemetry",
        )
        self.assertTrue(any("receiver 2 reports status v0" in item
                            for item in result["failures"]))

    def test_explicit_policy_accepts_only_exact_known_write_only_pair_loudly(self):
        result = self.evaluate(allow_degraded_spi1_return_path=True)
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["acceptance_policy"], {
            "name": "temporary_degraded_spi1_return_path",
            "enabled": True,
            "telemetry_complete": False,
            "readable_devices": [0, 1],
            "known_write_only_devices": [2, 3],
            "write_only_streaming_proves_display_output": False,
            "visual_verification_required": True,
            "miso_dependent_gates_deferred": True,
        })
        self.assertIn("DEGRADED ACCEPTANCE", result["warnings"][0])
        self.assertFalse(result["receivers"]["2"]["logical_identity_verified"])
        self.assertEqual(
            result["receivers"]["3"]["telemetry"],
            "known_write_only_no_miso_return",
        )

    def test_policy_never_waives_partial_or_wrong_receiver_telemetry(self):
        mutations = (
            (2, {"receiver_status_version": 2}, "status v2"),
            (2, {"receiver_status_seen": True}, "status v0"),
            (1, write_only_status(), "receiver 1 reports status v0"),
        )
        for index, mutation, expected in mutations:
            with self.subTest(index=index, expected=expected):
                devices = list(self.devices)
                if index == 1:
                    devices[index] = mutation
                else:
                    devices[index] = write_only_status(**mutation)
                result = receiver_acceptance.evaluate_phase3a_status(
                    devices,
                    refresh=refresh(),
                    expected_refresh_id="fresh-1",
                    allow_degraded_spi1_return_path=True,
                )
                self.assertFalse(result["passed"])
                self.assertTrue(any(expected in item for item in result["failures"]))

        one_return_path_recovers = list(self.devices)
        one_return_path_recovers[3] = readable_status(3)
        result = receiver_acceptance.evaluate_phase3a_status(
            one_return_path_recovers,
            refresh=refresh(),
            expected_refresh_id="fresh-1",
            allow_degraded_spi1_return_path=True,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(any("exact write-only logical-device pair" in item
                            for item in result["failures"]))

    def test_refresh_failure_is_accepted_only_when_exactly_the_write_only_pair(self):
        exact = receiver_acceptance.evaluate_phase3a_status(
            self.devices,
            refresh=refresh(passed=False, errors=(
                {"logical_device": 2, "error": "no return"},
                {"logical_device": 3, "error": "no return"},
            )),
            expected_refresh_id="fresh-1",
            allow_degraded_spi1_return_path=True,
        )
        self.assertTrue(exact["passed"], exact)

        unexpected = receiver_acceptance.evaluate_phase3a_status(
            self.devices,
            refresh=refresh(passed=False, errors=(
                {"logical_device": 1, "error": "readable receiver failed"},
                {"logical_device": 2, "error": "no return"},
                {"logical_device": 3, "error": "no return"},
            )),
            expected_refresh_id="fresh-1",
            allow_degraded_spi1_return_path=True,
        )
        self.assertFalse(unexpected["passed"])
        self.assertTrue(any("required readable board" in item
                            for item in unexpected["failures"]))

    def test_local_background_canary_cannot_target_write_only_receiver(self):
        result = self.evaluate(
            allow_degraded_spi1_return_path=True,
            local_canary_device=2,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(any("cannot be used" in item for item in result["failures"]))


class DegradedStreamedAcceptanceTests(unittest.TestCase):
    @staticmethod
    def sample(sequence, *, errors=0, **changes):
        sample = write_only_status()
        sample.update({
            "frames_sent": sequence,
            "spi_transfers": sequence * 2,
            "bytes_sent": sequence * 100,
            "errors": errors,
        })
        sample.update(changes)
        return sample

    def test_write_only_streaming_reports_host_evidence_without_receiver_claims(self):
        result = receiver_acceptance.evaluate_write_only_samples(
            [self.sample(10), self.sample(20)], 1.0,
        )
        self.assertTrue(result["passed"], result)
        self.assertEqual(result["host_frames_delta"], 10)
        self.assertTrue(result["known_write_only_state"])
        self.assertFalse(result["receiver_telemetry_verified"])
        self.assertFalse(result["physical_display_verified"])
        self.assertTrue(result["visual_verification_required"])

    def test_write_only_streaming_fails_on_no_traffic_host_error_or_partial_status(self):
        cases = (
            ([self.sample(10), self.sample(10)], "did not advance"),
            ([self.sample(10), self.sample(20, errors=1)], "errors increased"),
            ([self.sample(10), self.sample(20, receiver_status_version=2)],
             "exact status v0"),
        )
        for samples, expected in cases:
            with self.subTest(expected=expected):
                result = receiver_acceptance.evaluate_write_only_samples(samples, 1.0)
                self.assertFalse(result["passed"])
                self.assertTrue(any(expected in item for item in result["failures"]))

    def test_cli_policy_requires_the_complete_four_receiver_scope(self):
        argv = [
            "receiver_acceptance.py", "--allow-degraded-spi1-return-path",
            "--device", "0", "--device", "1",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaises(SystemExit) as exited,
        ):
            receiver_acceptance.main()
        self.assertEqual(exited.exception.code, 2)
        self.assertIn("requires exactly", stderr.getvalue())


class DegradedLiveSweepTests(unittest.TestCase):
    @staticmethod
    def readable(sequence):
        return {
            "receiver_status_version": 3,
            "receiver_crc_errors": 0,
            "receiver_publish_drops": 0,
            "receiver_spi_queue_errors": 0,
            "receiver_display_errors": 0,
            "receiver_status_misses": 0,
            "frames_sent": sequence,
            "spi_transfers": sequence * 2,
            "bytes_sent": sequence * 100,
            "errors": 0,
        }

    @staticmethod
    def write_only(sequence, **changes):
        result = write_only_status(
            frames_sent=sequence,
            spi_transfers=sequence * 2,
            bytes_sent=sequence * 100,
            errors=0,
        )
        result.update(changes)
        return result

    def samples(self):
        return (
            [self.readable(10), self.readable(10),
             self.write_only(10), self.write_only(10)],
            [self.readable(20), self.readable(20),
             self.write_only(20), self.write_only(20)],
        )

    def test_strict_sweep_never_silently_skips_unreadable_receivers(self):
        first, last = self.samples()
        result = live_animation_sweep.evaluate_receiver_topology(first, last)
        self.assertTrue(any("receiver 2" in item for item in result["failures"]))
        self.assertEqual(result["observable_receivers"], [0, 1])
        self.assertEqual(result["write_only_receivers"], [])

    def test_strict_sweep_accepts_complete_readable_topology(self):
        first = [self.readable(10) for _ in range(4)]
        last = [self.readable(20) for _ in range(4)]
        result = live_animation_sweep.evaluate_receiver_topology(first, last)
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["observable_receivers"], [0, 1, 2, 3])
        self.assertEqual(result["write_only_receivers"], [])

    def test_explicit_sweep_policy_requires_exact_pair_and_host_progress(self):
        first, last = self.samples()
        result = live_animation_sweep.evaluate_receiver_topology(
            first, last, allow_degraded_spi1=True,
        )
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["observable_receivers"], [0, 1])
        self.assertEqual(result["write_only_receivers"], [2, 3])
        self.assertFalse(result["receivers"]["2"]["physical_display_verified"])

        last[3] = self.write_only(10)
        failed = live_animation_sweep.evaluate_receiver_topology(
            first, last, allow_degraded_spi1=True,
        )
        self.assertTrue(any("frame deltas differ" in item
                            for item in failed["failures"]))
        self.assertEqual(failed["write_only_receivers"], [2, 3])

    def test_degraded_sweep_accepts_matching_zero_delta_for_cached_plugin(self):
        first, last = self.samples()
        first = [dict(item, frames_sent=10) for item in first]
        last = [dict(item, frames_sent=10) for item in last]
        for before, after in zip(first, last):
            before["spi_transfers"] = after["spi_transfers"] = 20
            before["bytes_sent"] = after["bytes_sent"] = 1000
        result = live_animation_sweep.evaluate_receiver_topology(
            first, last, allow_degraded_spi1=True,
        )
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["write_only_receivers"], [2, 3])

    def test_degraded_sweep_rejects_mismatched_host_lane_frame_deltas(self):
        first, last = self.samples()
        last[3]["frames_sent"] -= 2
        result = live_animation_sweep.evaluate_receiver_topology(
            first, last, allow_degraded_spi1=True,
        )
        self.assertTrue(any("more than one" in item
                            for item in result["failures"]))

    def test_degraded_sweep_allows_one_inflight_frame_at_sample_boundary(self):
        first, last = self.samples()
        last[3]["frames_sent"] -= 1
        result = live_animation_sweep.evaluate_receiver_topology(
            first, last, allow_degraded_spi1=True,
        )
        self.assertEqual(result["failures"], [])

    def test_sweep_requires_exact_four_device_topology(self):
        first, last = self.samples()
        result = live_animation_sweep.evaluate_receiver_topology(
            first[:-1], last[:-1], allow_degraded_spi1=True,
        )
        self.assertTrue(any("exactly four" in item for item in result["failures"]))


if __name__ == "__main__":
    unittest.main()
