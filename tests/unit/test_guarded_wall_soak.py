"""Fail-closed WALL-02 soak coverage without target or wall access."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from tools.benchmarks import guarded_wall_soak as soak


RELEASE = "a" * 64
IDENTITY = "b" * 64
BASIS = "c" * 64
GLOBAL_DIGEST = "d" * 64
PROFILE_DIGEST = "e" * 64

SCENE = {
    "schema": "ledgrid.scene-state",
    "schema_version": 1,
    "revision": 7,
    "background": {
        "plugin_id": "rainbow",
        "provider": "python",
        "resolved_parameters": {"speed": 0.3},
        "parameter_overrides": {},
    },
    "overlays": [],
    "known_python_fallback": {
        "plugin_id": "rainbow",
        "provider": "python",
        "resolved_parameters": {"speed": 0.3},
        "parameter_overrides": {},
    },
}
SCENE_DIGEST = soak.canonical_scene_digest(SCENE)


class _Clock:
    def __init__(self) -> None:
        self.value = 2_000_000_000.0

    def monotonic(self) -> float:
        return self.value - 2_000_000_000.0

    def time(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def _config(**changes) -> soak.WallSoakConfig:
    values = {
        "activation_id": "activation-1",
        "expected_scene_digest": SCENE_DIGEST,
        "expected_release_id": RELEASE,
        "expected_basis_digest": BASIS,
        "duration_seconds": 1800.0,
        "sample_interval_seconds": 900.0,
    }
    values.update(changes)
    return soak.WallSoakConfig(**values)


def _device(receiver_id: int, elapsed: float) -> dict:
    expected = soak.EXPECTED_TOPOLOGY_BY_ID[receiver_id]
    frames = int(elapsed * 155)
    base = 1000 + receiver_id * 100
    full_frame_semantic = 1 + expected["local_strip_count"] * 138 * 3
    fec_enabled = receiver_id == 3
    full_frame_wire = soak.EXPECTED_FULL_FRAME_WIRE_BYTES[receiver_id]
    full_frame_total = base + frames
    status_transfers = full_frame_total // 16
    return {
        "receiver_status_version": 7,
        "receiver_status_max_version_seen": 7,
        "receiver_status_seen": True,
        "receiver_capabilities": 0xC00C,
        "transport_envelope_enabled": True,
        "transport_envelope_negotiation_candidate": None,
        "transport_envelope_negotiation_streak": 0,
        "transport_envelope_negotiation_required": 3,
        "fec_transport_requested": fec_enabled,
        "fec_transport_enabled": fec_enabled,
        "fec_transport_negotiation_candidate": None,
        "fec_transport_negotiation_streak": 0,
        "fec_transport_negotiation_required": 3,
        "receiver_logical_device": receiver_id,
        "receiver_active_strips": expected["local_strip_count"],
        "receiver_global_strip_offset": expected["global_strip_offset"],
        "receiver_lane_mask": expected["physical_output_lane_mask"],
        "receiver_leds_per_strip": 138,
        "receiver_base_mode": 2,
        "receiver_last_encode_us": 800,
        "receiver_last_show_us": 4450,
        "errors": 0,
        "frames_sent": base + frames,
        "spi_transfers": base + frames,
        "bytes_sent": base + frames * full_frame_wire,
        "semantic_bytes_sent": base + frames * full_frame_semantic,
        "transport_envelope_bytes_sent": base + frames * (8 if fec_enabled else 4),
        "transport_padding_bytes_sent": base + frames * (
            5 if fec_enabled else (-(full_frame_semantic + 6)) % 4
        ),
        "full_frame_transfers": full_frame_total,
        "full_frame_semantic_bytes_sent": base + frames * full_frame_semantic,
        "full_frame_wire_bytes_sent": base + frames * full_frame_wire,
        "full_frame_status_transfers": status_transfers,
        "full_frame_status_samples": status_transfers - receiver_id,
        "full_frame_status_sample_misses": receiver_id,
        "full_frame_write_only_transfers": full_frame_total - status_transfers,
        "full_frame_frames_since_status_sample": full_frame_total % 16,
        "full_frame_max_status_sample_gap": 15,
        "spidev_buffer_size": 4096,
        "full_frame_write_only_supported": True,
        "crc_bytes_sent": base + frames * 2,
        "fec_frames_sent": full_frame_total if fec_enabled else 0,
        "fec_codewords_sent": 26 * full_frame_total if fec_enabled else 0,
        "fec_parity_bytes_sent": 52 * full_frame_total if fec_enabled else 0,
        "fec_data_padding_bytes_sent": 4 * full_frame_total if fec_enabled else 0,
        "receiver_operation_sequence": base + frames,
        "receiver_packets": base + frames,
        "receiver_crc_ok_packets": base + frames,
        "receiver_frames_accepted": base + frames,
        "receiver_frames_displayed": base + frames - 1,
        "receiver_frames_superseded": 0,
        "receiver_status_responses": base + frames,
        "receiver_fec_packets_received": full_frame_total if fec_enabled else 0,
        "receiver_fec_packets_accepted": full_frame_total if fec_enabled else 0,
        "receiver_fec_corrected_packets": frames // 1000 if fec_enabled else 0,
        "receiver_fec_corrected_codewords": frames // 1000 if fec_enabled else 0,
        "receiver_fec_uncorrectable_packets": 0,
        "receiver_fec_semantic_crc_errors": 0,
        "receiver_fec_framing_errors": 0,
        "receiver_fec_last_decode_us": 80 if fec_enabled else 0,
        "receiver_fec_max_decode_us": 100 if fec_enabled else 0,
        "receiver_crc_errors": receiver_id,
        "receiver_publish_drops": 0,
        "receiver_spi_queue_errors": 0,
        "receiver_display_errors": 0,
        "receiver_status_misses": receiver_id,
    }


def _status(elapsed: float) -> dict:
    devices = [_device(receiver_id, elapsed) for receiver_id in range(5)]
    frames = int(elapsed * 155)
    return {
        "schema": "ledgrid.controller-status",
        "schema_version": 1,
        "release_id": RELEASE,
        "controller_release_id": RELEASE,
        "release_consistent": True,
        "controller_session_id": "session-1",
        "controller_state_revision": 4,
        "current_identity_digest": IDENTITY,
        "active_identity": _identity(),
        "is_running": True,
        "mode": "scene",
        "target_fps": 150,
        "actual_fps": 155.0,
        "pipeline_fps": 155.0,
        "written_at": 1_900_000_000.0 + elapsed,
        "scene_state": deepcopy(SCENE),
        "led_info": deepcopy(soak.EXPECTED_GEOMETRY),
        "driver_stats": {
            "aggregate": {
                "device_map": [deepcopy(item) for item in soak.EXPECTED_TOPOLOGY],
                "transport_envelope_devices": 5,
                "fec_transport_requested_devices": 1,
                "fec_transport_enabled_devices": 1,
                "receiver_status_version": min(
                    item["receiver_status_version"] for item in devices
                ),
                "receiver_status_max_version_seen": min(
                    item["receiver_status_max_version_seen"] for item in devices
                ),
                "errors": 0,
                "frames_sent": 5000 + frames,
                "logical_frames_sent": 5000 + frames,
                "spi_transfers": sum(item["spi_transfers"] for item in devices),
                "bytes_sent": sum(item["bytes_sent"] for item in devices),
                "semantic_bytes_sent": sum(
                    item["semantic_bytes_sent"] for item in devices
                ),
                "transport_envelope_bytes_sent": sum(
                    item["transport_envelope_bytes_sent"] for item in devices
                ),
                "transport_padding_bytes_sent": sum(
                    item["transport_padding_bytes_sent"] for item in devices
                ),
                "fec_frames_sent": sum(item["fec_frames_sent"] for item in devices),
                "fec_codewords_sent": sum(item["fec_codewords_sent"] for item in devices),
                "fec_parity_bytes_sent": sum(item["fec_parity_bytes_sent"] for item in devices),
                "fec_data_padding_bytes_sent": sum(item["fec_data_padding_bytes_sent"] for item in devices),
                "full_frame_transfers": sum(
                    item["full_frame_transfers"] for item in devices
                ),
                "full_frame_semantic_bytes_sent": sum(
                    item["full_frame_semantic_bytes_sent"] for item in devices
                ),
                "full_frame_wire_bytes_sent": sum(
                    item["full_frame_wire_bytes_sent"] for item in devices
                ),
                "full_frame_status_transfers": sum(
                    item["full_frame_status_transfers"] for item in devices
                ),
                "full_frame_status_samples": sum(
                    item["full_frame_status_samples"] for item in devices
                ),
                "full_frame_status_sample_misses": sum(
                    item["full_frame_status_sample_misses"] for item in devices
                ),
                "full_frame_write_only_transfers": sum(
                    item["full_frame_write_only_transfers"] for item in devices
                ),
                "full_frame_frames_since_status_sample": max(
                    item["full_frame_frames_since_status_sample"] for item in devices
                ),
                "full_frame_max_status_sample_gap": max(
                    item["full_frame_max_status_sample_gap"] for item in devices
                ),
                "spidev_buffer_size": min(
                    item["spidev_buffer_size"] for item in devices
                ),
                "full_frame_write_only_supported": all(
                    item["full_frame_write_only_supported"] for item in devices
                ),
                "crc_bytes_sent": sum(item["crc_bytes_sent"] for item in devices),
                "receiver_crc_errors": sum(range(5)),
                "receiver_fec_packets_received": sum(item["receiver_fec_packets_received"] for item in devices),
                "receiver_fec_packets_accepted": sum(item["receiver_fec_packets_accepted"] for item in devices),
                "receiver_fec_corrected_packets": sum(item["receiver_fec_corrected_packets"] for item in devices),
                "receiver_fec_corrected_codewords": sum(item["receiver_fec_corrected_codewords"] for item in devices),
                "receiver_fec_uncorrectable_packets": 0,
                "receiver_fec_semantic_crc_errors": 0,
                "receiver_fec_framing_errors": 0,
                "receiver_fec_last_decode_us": max(item["receiver_fec_last_decode_us"] for item in devices),
                "receiver_fec_max_decode_us": max(item["receiver_fec_max_decode_us"] for item in devices),
                "receiver_packets": sum(
                    item["receiver_packets"] for item in devices
                ),
                "receiver_crc_ok_packets": sum(
                    item["receiver_crc_ok_packets"] for item in devices
                ),
                "receiver_publish_drops": 0,
                "receiver_spi_queue_errors": 0,
                "receiver_display_errors": 0,
                "receiver_status_misses": sum(range(5)),
                "receiver_frames_accepted": sum(
                    item["receiver_frames_accepted"] for item in devices
                ),
                "receiver_frames_displayed": sum(
                    item["receiver_frames_displayed"] for item in devices
                ),
                "receiver_frames_superseded": 0,
            },
            "devices": devices,
        },
    }


def _identity() -> dict:
    component = {
        "provider": "python",
        "component_id": "rainbow",
        "component_digest": "1" * 64,
        "browser_runtime_digest": "2" * 64,
        "controller_runtime_digest": "3" * 64,
        "parameter_schema_version": 1,
    }
    return {
        "scene_identity": {"revision": 7, "digest": SCENE_DIGEST},
        "component_identities": [
            {"slot_id": "background", **component},
            {"slot_id": "known_python_fallback", **component},
        ],
        "global_settings_identity": {
            "revision": 5,
            "digest": GLOBAL_DIGEST,
        },
        "installation_profile_digest": PROFILE_DIGEST,
    }


def _activation(*, complete: bool = True, fresh: bool = True) -> dict:
    return {
        "schema": "ledgrid.scene-activation-status",
        "schema_version": 1,
        "activation_id": "activation-1",
        "basis_digest": BASIS,
        "phase": "active",
        "requested_identity": _identity(),
        "normalized_identity": _identity(),
        "observed_identity": _identity(),
        "controller": {
            "session_id": "session-1",
            "state_revision_before": 3,
            "state_revision_after": 4,
        },
        "telemetry": {
            "complete": complete,
            "fresh": fresh,
            "observed_at": 2_000_000_000_000,
        },
        "rollback": {
            "available": True,
            "snapshot_id": "snapshot-1",
            "result": None,
            "error": None,
        },
    }


class _API:
    def __init__(self, clock: _Clock, mutation=None) -> None:
        self.clock = clock
        self.mutation = mutation

    def get(self, path: str, *, timeout: float):
        del timeout
        elapsed = self.clock.monotonic()
        if path == "/api/status":
            result = _status(elapsed)
            if self.mutation is not None:
                self.mutation(result, None, elapsed)
            return result
        if path == "/api/v1/scene/activations/activation-1":
            result = _activation()
            if self.mutation is not None:
                self.mutation(None, result, elapsed)
            return result
        raise AssertionError(path)


def _run(config: soak.WallSoakConfig, api: _API, clock: _Clock) -> dict:
    return soak.run_soak(
        config,
        api,
        monotonic=clock.monotonic,
        wall_time=clock.time,
        sleep=clock.sleep,
    )


class GuardedWallSoakTests(unittest.TestCase):
    def test_exact_release_identity_topology_and_1800_second_soak_pass(self) -> None:
        clock = _Clock()
        report = _run(_config(), _API(clock), clock)

        self.assertTrue(report["passed"], report["failures"])
        self.assertEqual(report["evaluation"]["observed_seconds"], 1800.0)
        self.assertEqual(report["evaluation"]["sample_count"], 3)
        self.assertTrue(
            all(item["passed"] for item in report["evaluation"]["receivers"].values())
        )
        self.assertTrue(report["observation_only"])
        self.assertEqual(report["activation_basis_digest"], BASIS)
        self.assertEqual(report["activation_identity"], _identity())
        self.assertGreater(
            report["evaluation"]["receivers"]["3"]["deltas"][
                "receiver_fec_corrected_packets"
            ],
            0,
        )

    def test_status_v7_observation_survives_later_v3_samples(self) -> None:
        def scheduled_v3(status, _activation_status, _elapsed):
            if status is None:
                return
            for device in status["driver_stats"]["devices"]:
                device["receiver_status_version"] = 3
            status["driver_stats"]["aggregate"]["receiver_status_version"] = 3

        clock = _Clock()
        report = _run(_config(), _API(clock, scheduled_v3), clock)
        self.assertTrue(report["passed"], report["failures"])
        self.assertTrue(
            all(
                device["receiver_status_version"] == 3
                and device["receiver_status_max_version_seen"] == 7
                for device in report["samples"][-1]["devices"]
            )
        )

    def test_soak_fails_when_current_process_never_observed_status_v7(self) -> None:
        def never_v7(status, _activation_status, _elapsed):
            if status is None:
                return
            status["driver_stats"]["devices"][0].update({
                "receiver_status_version": 3,
                "receiver_status_max_version_seen": 6,
            })
            status["driver_stats"]["aggregate"].update({
                "receiver_status_version": 3,
                "receiver_status_max_version_seen": 6,
            })

        clock = _Clock()
        report = _run(_config(), _API(clock, never_v7), clock)
        self.assertFalse(report["passed"])
        self.assertTrue(
            any("required observed>=v7" in failure for failure in report["failures"]),
            report["failures"],
        )

    def test_fec_terminal_faults_each_fail_while_corrections_are_allowed(self) -> None:
        for field in (
            "receiver_fec_uncorrectable_packets",
            "receiver_fec_semantic_crc_errors",
            "receiver_fec_framing_errors",
        ):
            with self.subTest(field=field):
                def terminal(status, _activation_status, elapsed, field=field):
                    if status is None or elapsed < 900:
                        return
                    device = status["driver_stats"]["devices"][3]
                    aggregate = status["driver_stats"]["aggregate"]
                    device[field] += 1
                    device["receiver_fec_packets_received"] += 1
                    aggregate[field] += 1
                    aggregate["receiver_fec_packets_received"] += 1

                clock = _Clock()
                report = _run(_config(), _API(clock, terminal), clock)
                self.assertFalse(report["passed"])
                self.assertTrue(
                    any("terminal faults" in failure for failure in report["failures"]),
                    report["failures"],
                )

    def test_identity_drift_fails_at_the_first_changed_sample(self) -> None:
        def drift(status, _activation_status, elapsed):
            if status is not None and elapsed >= 900:
                status["scene_state"]["background"]["resolved_parameters"]["speed"] = 0.4

        clock = _Clock()
        report = _run(_config(), _API(clock, drift), clock)
        self.assertFalse(report["passed"])
        self.assertIn("active scene digest drifted", report["failures"][0])

    def test_stale_or_incomplete_activation_telemetry_fails_closed(self) -> None:
        for key in ("complete", "fresh"):
            with self.subTest(key=key):
                def incomplete(_status, activation_status, _elapsed, field=key):
                    if activation_status is not None:
                        activation_status["telemetry"][field] = False

                clock = _Clock()
                report = _run(_config(), _API(clock, incomplete), clock)
                self.assertFalse(report["passed"])
                self.assertIn("incomplete or stale", report["failures"][0])

    def test_basis_digest_drift_fails_closed(self) -> None:
        def drift(_status, activation_status, elapsed):
            if activation_status is not None and elapsed >= 900:
                activation_status["basis_digest"] = "f" * 64

        clock = _Clock()
        report = _run(_config(), _API(clock, drift), clock)
        self.assertFalse(report["passed"])
        self.assertIn("basis digest differs", report["failures"][0])

        with self.assertRaisesRegex(ValueError, "expected_basis_digest"):
            _config(expected_basis_digest="not-a-digest")

    def test_global_identity_drift_cannot_hide_in_normalized_receipt(self) -> None:
        def drift(_status, activation_status, elapsed):
            if activation_status is not None and elapsed >= 900:
                activation_status["normalized_identity"][
                    "global_settings_identity"
                ]["digest"] = "f" * 64

        clock = _Clock()
        report = _run(_config(), _API(clock, drift), clock)
        self.assertFalse(report["passed"])
        self.assertIn("identities are not unanimous", report["failures"][0])

    def test_profile_identity_drift_cannot_hide_in_unanimous_receipt(self) -> None:
        def drift(_status, activation_status, elapsed):
            if activation_status is None or elapsed < 900:
                return
            for key in (
                "requested_identity",
                "normalized_identity",
                "observed_identity",
            ):
                activation_status[key]["installation_profile_digest"] = "f" * 64

        clock = _Clock()
        report = _run(_config(), _API(clock, drift), clock)
        self.assertFalse(report["passed"])
        self.assertIn("full active identity mismatch", report["failures"][0])

    def test_counter_reset_and_error_delta_fail_immediately(self) -> None:
        cases = (
            ("reset", "receiver_frames_accepted", -200_000, "reset"),
            ("error", "receiver_crc_errors", 1, "increased"),
        )
        for _name, field, change, expected in cases:
            with self.subTest(field=field):
                def mutate(status, _activation_status, elapsed, field=field, change=change):
                    if status is None or elapsed < 900:
                        return
                    status["driver_stats"]["devices"][2][field] += change
                    if field in status["driver_stats"]["aggregate"]:
                        status["driver_stats"]["aggregate"][field] += change

                clock = _Clock()
                report = _run(_config(), _API(clock, mutate), clock)
                self.assertFalse(report["passed"])
                self.assertIn(expected, report["failures"][0])

    def test_missing_authoritative_aggregate_counter_fails_closed(self) -> None:
        def missing(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["aggregate"].pop("crc_bytes_sent")

        clock = _Clock()
        report = _run(_config(), _API(clock, missing), clock)
        self.assertFalse(report["passed"])
        self.assertIn(
            "aligned transport counter crc_bytes_sent is unavailable",
            report["failures"][0],
        )

    def test_exact_five_device_topology_is_required(self) -> None:
        def missing_tail(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["aggregate"]["device_map"].pop()
                status["driver_stats"]["devices"].pop()

        clock = _Clock()
        report = _run(_config(), _API(clock, missing_tail), clock)
        self.assertFalse(report["passed"])
        self.assertIn("expected 5", report["failures"][0])

    def test_aligned_transport_capability_is_required(self) -> None:
        def legacy_capabilities(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["devices"][3]["receiver_capabilities"] = 0x000C

        clock = _Clock()
        report = _run(_config(), _API(clock, legacy_capabilities), clock)
        self.assertFalse(report["passed"])
        self.assertIn("aligned transport capabilities", report["failures"][0])

    def test_aligned_transport_host_state_and_exact_aggregate_are_required(self) -> None:
        def host_disabled(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["devices"][3][
                    "transport_envelope_enabled"
                ] = False

        def aggregate_short(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["aggregate"][
                    "transport_envelope_devices"
                ] = 4

        def negotiation_pending(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["devices"][1].update({
                    "transport_envelope_negotiation_candidate": False,
                    "transport_envelope_negotiation_streak": 1,
                })

        def negotiation_missing(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["devices"][1].pop(
                    "transport_envelope_negotiation_candidate"
                )

        for label, mutate, expected in (
            ("host disabled", host_disabled, "host aligned transport is not enabled"),
            ("aggregate short", aggregate_short, "exactly 5 receivers"),
            ("negotiation pending", negotiation_pending, "negotiation is not settled"),
            ("negotiation missing", negotiation_missing, "negotiation is not settled"),
        ):
            with self.subTest(label=label):
                clock = _Clock()
                report = _run(_config(), _API(clock, mutate), clock)
                self.assertFalse(report["passed"])
                self.assertIn(expected, report["failures"][0])

    def test_aligned_transport_accounting_rejects_stall_and_wire_drift(self) -> None:
        transport_fields = (
            "spi_transfers", "bytes_sent", "semantic_bytes_sent",
            "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
            "crc_bytes_sent",
        )

        def stalled(status, _activation_status, elapsed):
            if status is None or elapsed < 900:
                return
            baseline = _device(2, 0)
            device = status["driver_stats"]["devices"][2]
            for field in transport_fields:
                device[field] = baseline[field]
                status["driver_stats"]["aggregate"][field] = sum(
                    item[field] for item in status["driver_stats"]["devices"]
                )

        def drifted(status, _activation_status, elapsed):
            if status is None or elapsed < 900:
                return
            status["driver_stats"]["devices"][1]["bytes_sent"] += 1
            status["driver_stats"]["aggregate"]["bytes_sent"] += 1

        def status_only(status, _activation_status, elapsed):
            if status is None or elapsed < 900:
                return
            fields = (
                "full_frame_transfers", "full_frame_semantic_bytes_sent",
                "full_frame_wire_bytes_sent",
                "full_frame_status_transfers", "full_frame_status_samples",
                "full_frame_status_sample_misses", "full_frame_write_only_transfers",
            )
            for receiver_id, device in enumerate(
                status["driver_stats"]["devices"]
            ):
                baseline = _device(receiver_id, 0)
                for field in fields:
                    device[field] = baseline[field]
            for field in fields:
                status["driver_stats"]["aggregate"][field] = sum(
                    item[field] for item in status["driver_stats"]["devices"]
                )

        for label, mutate, expected in (
            ("stalled", stalled, "did not advance"),
            ("drifted", drifted, "wire-byte accounting is inconsistent"),
            ("status-only", status_only, "full_frame_transfers did not advance"),
        ):
            with self.subTest(label=label):
                clock = _Clock()
                report = _run(_config(), _API(clock, mutate), clock)
                self.assertFalse(report["passed"])
                self.assertTrue(any(expected in failure for failure in report["failures"]))

    def test_full_frame_status_sampling_missing_invariant_miss_gap_and_reset_fail(self) -> None:
        def sync(status, field):
            status["driver_stats"]["aggregate"][field] = sum(
                item[field] for item in status["driver_stats"]["devices"]
            )

        def missing(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["devices"][0].pop(
                    "full_frame_status_samples"
                )

        def broken(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["devices"][0][
                    "full_frame_write_only_transfers"
                ] += 1
                sync(status, "full_frame_write_only_transfers")

        def unclassified(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["devices"][0][
                    "full_frame_status_samples"
                ] -= 1
                sync(status, "full_frame_status_samples")

        def excessive_gap(status, _activation_status, _elapsed):
            if status is not None:
                status["driver_stats"]["devices"][0].update({
                    "full_frame_frames_since_status_sample": 257,
                    "full_frame_max_status_sample_gap": 257,
                })
                status["driver_stats"]["aggregate"].update({
                    "full_frame_frames_since_status_sample": 257,
                    "full_frame_max_status_sample_gap": 257,
                })

        def sample_miss(status, _activation_status, elapsed):
            if status is not None and elapsed >= 900:
                status["driver_stats"]["devices"][0][
                    "full_frame_status_sample_misses"
                ] += 1
                status["driver_stats"]["devices"][0][
                    "full_frame_status_transfers"
                ] += 1
                status["driver_stats"]["devices"][0][
                    "full_frame_write_only_transfers"
                ] -= 1
                sync(status, "full_frame_status_sample_misses")
                sync(status, "full_frame_status_transfers")
                sync(status, "full_frame_write_only_transfers")

        def sample_reset(status, _activation_status, elapsed):
            if status is not None and elapsed >= 900:
                device = status["driver_stats"]["devices"][0]
                device["full_frame_status_samples"] = (
                    _device(0, 0)["full_frame_status_samples"] - 1
                )
                device["full_frame_status_transfers"] = (
                    device["full_frame_status_samples"]
                    + device["full_frame_status_sample_misses"]
                )
                device["full_frame_write_only_transfers"] = (
                    device["full_frame_transfers"]
                    - device["full_frame_status_transfers"]
                )
                sync(status, "full_frame_status_samples")
                sync(status, "full_frame_status_transfers")
                sync(status, "full_frame_write_only_transfers")

        def gap_reset(status, _activation_status, elapsed):
            if status is not None and elapsed >= 900:
                for device in status["driver_stats"]["devices"]:
                    device["full_frame_max_status_sample_gap"] = 14
                    device["full_frame_frames_since_status_sample"] = min(
                        device["full_frame_frames_since_status_sample"], 14
                    )
                status["driver_stats"]["aggregate"][
                    "full_frame_max_status_sample_gap"
                ] = 14
                status["driver_stats"]["aggregate"][
                    "full_frame_frames_since_status_sample"
                ] = max(
                    device["full_frame_frames_since_status_sample"]
                    for device in status["driver_stats"]["devices"]
                )

        for label, mutate, expected in (
            ("missing", missing, "sampling counters are unavailable"),
            ("invariant", broken, "transfer invariant is broken"),
            ("unclassified", unclassified, "status transfer classification is broken"),
            ("gap", excessive_gap, "gap is outside 0..256"),
            ("miss", sample_miss, "sample misses increased"),
            ("reset", sample_reset, "sampling counter reset"),
            ("gap reset", gap_reset, "maximum status sample gap reset"),
        ):
            with self.subTest(label=label):
                clock = _Clock()
                report = _run(_config(), _API(clock, mutate), clock)
                self.assertFalse(report["passed"])
                self.assertTrue(any(expected in failure for failure in report["failures"]))

    def test_short_diagnostic_cannot_claim_wall_02(self) -> None:
        clock = _Clock()
        config = _config(duration_seconds=60.0, sample_interval_seconds=30.0)
        report = _run(config, _API(clock), clock)
        self.assertFalse(report["passed"])
        self.assertIn("below the 1800s release minimum", report["evaluation"]["failures"][0])

    def test_status_publication_must_advance(self) -> None:
        def stale(status, _activation_status, elapsed):
            if status is not None:
                status["written_at"] = 1_900_000_000.0

        clock = _Clock()
        report = _run(_config(), _API(clock, stale), clock)
        self.assertFalse(report["passed"])
        self.assertIn("publication did not advance", report["failures"][0])

    def test_evidence_is_append_only_digestable_and_rejects_symlink_paths(self) -> None:
        report = {"schema": soak.SCHEMA, "schema_version": 1, "passed": True}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "evidence" / "wall-02.json"
            path, digest = soak.write_report(report, destination)
            self.assertEqual(json.loads(path.read_text()), report)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            with self.assertRaisesRegex(soak.WallSoakError, "refusing to overwrite"):
                soak.write_report(report, destination)

            real = root / "real"
            real.mkdir()
            link = root / "linked"
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(soak.WallSoakError, "symbolic link"):
                soak.write_report(report, link / "wall-02.json")


if __name__ == "__main__":
    unittest.main()
