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
    return {
        "receiver_status_version": 3,
        "receiver_status_seen": True,
        "receiver_capabilities": 12,
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
        "bytes_sent": base + frames * 3315,
        "crc_bytes_sent": base + frames * 2,
        "receiver_operation_sequence": base + frames,
        "receiver_packets": base + frames,
        "receiver_crc_ok_packets": base + frames,
        "receiver_frames_accepted": base + frames,
        "receiver_frames_displayed": base + frames - 1,
        "receiver_frames_superseded": 0,
        "receiver_status_responses": base + frames,
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
                "errors": 0,
                "frames_sent": 5000 + frames,
                "logical_frames_sent": 5000 + frames,
                "spi_transfers": 5000 + frames * 5,
                "bytes_sent": 5000 + frames * 4 * 3315,
                "crc_bytes_sent": 5000 + frames * 2 * 5,
                "receiver_crc_errors": sum(range(5)),
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
            "aggregate counter crc_bytes_sent is unavailable",
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
