"""Deterministic production target-evidence capture coverage."""

from __future__ import annotations

from copy import deepcopy
import tempfile
from pathlib import Path
import unittest

from tools.qualification.target_evidence import (
    TargetEvidenceError,
    atomic_write_json,
    build_target_evidence,
    metric_stats,
    validate_active_activation,
    validate_installed_topology,
)


BINDING = "a" * 64
BASIS = "b" * 64
SCENE = "c" * 64
GLOBALS = "d" * 64
PROFILE = "0" * 64


def _device(logical_id: int, displayed: int) -> dict:
    widths = (8, 8, 8, 8, 1)
    offsets = (0, 8, 16, 24, 32)
    return {
        "receiver_logical_device": logical_id,
        "receiver_status_version": 3,
        "receiver_active_strips": widths[logical_id],
        "receiver_global_strip_offset": offsets[logical_id],
        "receiver_leds_per_strip": 138,
        "total_leds": widths[logical_id] * 138,
        "spi_mode": 0,
        "spi_speed_hz": 20_000_000,
        "receiver_last_encode_us": 900 + logical_id,
        "receiver_last_show_us": 4400 + logical_id,
        "receiver_frames_displayed": displayed,
        "receiver_crc_errors": 10 if logical_id == 3 else 0,
        "receiver_publish_drops": 0,
        "receiver_spi_queue_errors": 0,
        "receiver_display_errors": 0,
        "receiver_status_misses": 2 if logical_id == 4 else 0,
    }


def _metrics(*, final: bool) -> dict:
    displayed = 1150 if final else 1000
    routes = ((0, 0), (0, 1), (1, 1), (1, 0), (1, 2))
    widths = (8, 8, 8, 8, 1)
    offsets = (0, 8, 16, 24, 32)
    native_reversed = (False, False, True, True, False)
    return {
        "animation": {"target_fps": 150, "actual_fps": 150.4},
        "performance": {
            "samples": 300,
            "avg_frame_ms": 2.0,
            "p95_frame_ms": 3.0,
            "p99_frame_ms": 4.0,
            "max_frame_ms": 5.0,
            "deadline_miss_ratio": 0.0,
            "frames_presented": 1150 if final else 1000,
            "unchanged_frames_skipped": 20,
        },
        "driver": {
            "aggregate": {
                "num_devices": 5,
                "strip_count": 33,
                "total_leds": 4554,
                "device_map": [
                    {
                        "logical_device": logical_id,
                        "bus": routes[logical_id][0],
                        "chip_select": routes[logical_id][1],
                        "local_strip_count": widths[logical_id],
                        "global_strip_offset": offsets[logical_id],
                        "physical_output_lane_mask": 255,
                        "reverse_host_strip_order": False,
                        "reverse_native_strip_order": native_reversed[logical_id],
                        "spi_mode": 0,
                        "spi_speed_hz": 20_000_000,
                    }
                    for logical_id in range(5)
                ],
            },
            "devices": [_device(index, displayed) for index in range(5)],
        },
    }


def _receipt() -> dict:
    identity = {
        "scene_identity": {"revision": 7, "digest": SCENE},
        "component_identities": [],
        "global_settings_identity": {"revision": 8, "digest": GLOBALS},
        "installation_profile_digest": PROFILE,
    }
    return {
        "activation_id": "perf-canary",
        "basis_digest": BASIS,
        "phase": "active",
        "requested_identity": identity,
        "observed_identity": deepcopy(identity),
        "telemetry": {"complete": True, "fresh": True, "observed_at": 123},
    }


class TargetQualificationCaptureTests(unittest.TestCase):
    def test_metric_statistics_use_nearest_rank_and_preserve_max(self) -> None:
        self.assertEqual(metric_stats([1.0, 2.0, 3.0, 10.0]), {
            "mean": 4.0,
            "p95": 10.0,
            "p99": 10.0,
            "max": 10.0,
        })

    def test_builds_separate_controller_and_exact_five_receiver_evidence(self) -> None:
        result = build_target_evidence(
            [_metrics(final=False), _metrics(final=True)],
            elapsed_seconds=1.0,
            binding_digest=BINDING,
            captured_at=2_000_000,
            target_fps=150,
            brightness=50,
            environment="Raspberry Pi 4; test window; 33x138; 150 FPS",
        )

        by_source = {item["source"]: item for item in result["evidence"]}
        self.assertEqual(set(by_source), {"controller_pi", "receiver"})
        self.assertEqual(by_source["controller_pi"]["sample_count"], 2)
        self.assertEqual(by_source["controller_pi"]["frame_time_ms"]["max"], 5.0)
        self.assertEqual(by_source["controller_pi"]["cadence"]["changed_frame_ratio"], 1.0)
        self.assertEqual(by_source["receiver"]["sample_count"], 10)
        self.assertEqual(by_source["receiver"]["cadence"]["observed_fps"], 150.0)
        self.assertLess(by_source["receiver"]["frame_time_ms"]["max"], 4.5)
        self.assertIsNone(by_source["receiver"]["electrical"])

    def test_integrity_delta_and_missing_max_fail_without_evidence(self) -> None:
        corrupted = _metrics(final=True)
        corrupted["driver"]["devices"][3]["receiver_crc_errors"] += 1
        with self.assertRaisesRegex(TargetEvidenceError, "crc_errors increased"):
            build_target_evidence(
                [_metrics(final=False), corrupted],
                elapsed_seconds=1.0,
                binding_digest=BINDING,
                captured_at=2_000_000,
                target_fps=150,
                brightness=50,
                environment="Raspberry Pi 4 test",
            )

        missing = _metrics(final=True)
        missing["performance"].pop("max_frame_ms")
        with self.assertRaisesRegex(TargetEvidenceError, "max_frame_ms"):
            build_target_evidence(
                [_metrics(final=False), missing],
                elapsed_seconds=1.0,
                binding_digest=BINDING,
                captured_at=2_000_000,
                target_fps=150,
                brightness=50,
                environment="Raspberry Pi 4 test",
            )

    def test_earlier_controller_spike_survives_later_clean_rolling_window(self) -> None:
        earlier_spike = _metrics(final=False)
        earlier_spike["performance"].update({
            "avg_frame_ms": 3.0,
            "p95_frame_ms": 6.2,
            "p99_frame_ms": 7.1,
            "max_frame_ms": 9.4,
        })
        clean = _metrics(final=True)

        result = build_target_evidence(
            [earlier_spike, clean],
            elapsed_seconds=1.0,
            binding_digest=BINDING,
            captured_at=2_000_000,
            target_fps=150,
            brightness=50,
            environment="Raspberry Pi 4 test",
        )

        controller = next(
            item for item in result["evidence"]
            if item["source"] == "controller_pi"
        )
        self.assertEqual(controller["sample_count"], 2)
        self.assertEqual(controller["frame_time_ms"], {
            "mean": 3.0,
            "p95": 6.2,
            "p99": 7.1,
            "max": 9.4,
        })
        self.assertIn("2 sampled rolling controller windows", controller["environment"])

    def test_heavy_tail_mean_above_p95_is_retained_and_ordered_upward(self) -> None:
        heavy_tail = _metrics(final=False)
        heavy_tail["performance"].update({
            "avg_frame_ms": 5.0,
            "p95_frame_ms": 1.0,
            "p99_frame_ms": 100.0,
            "max_frame_ms": 100.0,
        })
        clean = _metrics(final=True)
        source_windows = (heavy_tail["performance"], clean["performance"])

        result = build_target_evidence(
            [heavy_tail, clean],
            elapsed_seconds=1.0,
            binding_digest=BINDING,
            captured_at=2_000_000,
            target_fps=150,
            brightness=50,
            environment="Raspberry Pi 4 test",
        )

        controller = next(
            item for item in result["evidence"]
            if item["source"] == "controller_pi"
        )
        retained = controller["frame_time_ms"]
        self.assertEqual(controller["sample_count"], 2)
        self.assertEqual(retained, {
            "mean": 5.0,
            "p95": 5.0,
            "p99": 100.0,
            "max": 100.0,
        })
        source_fields = {
            "mean": "avg_frame_ms",
            "p95": "p95_frame_ms",
            "p99": "p99_frame_ms",
            "max": "max_frame_ms",
        }
        for name, source_field in source_fields.items():
            self.assertGreaterEqual(
                retained[name],
                max(window[source_field] for window in source_windows),
            )

    def test_invalid_controller_statistics_are_rejected(self) -> None:
        for label, updates in (
            (
                "p95 above p99",
                {"p95_frame_ms": 4.1, "p99_frame_ms": 4.0},
            ),
            (
                "p99 above max",
                {"p99_frame_ms": 5.1, "max_frame_ms": 5.0},
            ),
            (
                "mean above max",
                {"avg_frame_ms": 5.1, "max_frame_ms": 5.0},
            ),
        ):
            with self.subTest(label=label):
                invalid = _metrics(final=True)
                invalid["performance"].update(updates)
                with self.assertRaisesRegex(
                    TargetEvidenceError, "statistics are invalid"
                ):
                    build_target_evidence(
                        [_metrics(final=False), invalid],
                        elapsed_seconds=1.0,
                        binding_digest=BINDING,
                        captured_at=2_000_000,
                        target_fps=150,
                        brightness=50,
                        environment="Raspberry Pi 4 test",
                    )

    def test_installed_topology_binds_widths_routes_and_offsets(self) -> None:
        metrics = _metrics(final=False)
        validate_installed_topology(metrics)

        mutations = (
            ("route", ("device_map", 2, "chip_select", 0)),
            ("width", ("device_map", 4, "local_strip_count", 8)),
            ("offset", ("devices", 3, "receiver_global_strip_offset", 16)),
        )
        for label, (collection, index, field, replacement) in mutations:
            with self.subTest(label=label):
                changed = deepcopy(metrics)
                if collection == "device_map":
                    changed["driver"]["aggregate"][collection][index][field] = replacement
                else:
                    changed["driver"][collection][index][field] = replacement
                with self.assertRaisesRegex(TargetEvidenceError, field):
                    validate_installed_topology(changed)

    def test_activation_receipt_must_match_every_retained_identity(self) -> None:
        validate_active_activation(
            _receipt(),
            activation_id="perf-canary",
            basis_digest=BASIS,
            scene_digest=SCENE,
            global_settings_digest=GLOBALS,
            profile_digest=PROFILE,
        )
        for label, mutate in (
            ("not active", lambda value: value.__setitem__("phase", "failed")),
            (
                "scene drift",
                lambda value: value["observed_identity"]["scene_identity"].__setitem__(
                    "digest", "9" * 64
                ),
            ),
            ("basis drift", lambda value: value.__setitem__("basis_digest", "9" * 64)),
        ):
            with self.subTest(label=label):
                changed = deepcopy(_receipt())
                mutate(changed)
                with self.assertRaises(TargetEvidenceError):
                    validate_active_activation(
                        changed,
                        activation_id="perf-canary",
                        basis_digest=BASIS,
                        scene_digest=SCENE,
                        global_settings_digest=GLOBALS,
                        profile_digest=PROFILE,
                    )

    def test_atomic_write_replaces_only_after_complete_serialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            atomic_write_json(path, {"revision": 1, "complete": True})
            self.assertEqual(
                path.read_text(encoding="utf-8"),
                '{\n  "complete": true,\n  "revision": 1\n}\n',
            )
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
