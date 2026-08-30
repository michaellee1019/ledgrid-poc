"""Deterministic production target-evidence capture coverage."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import tempfile
from pathlib import Path
import unittest

from animation.core.activation_qualification import (
    QualificationValidationError,
    canonical_json_sha256,
)
from tools.qualification.target_evidence import (
    TargetEvidenceError,
    atomic_write_json,
    build_target_evidence,
    capture,
    load_calibrated_electrical_measurement,
    metric_stats,
    validate_active_activation,
    validate_installed_topology,
    validate_runtime_identity,
)
from tools.benchmarks.live_display_state import canonical_scene_digest


BINDING = "a" * 64
BASIS = "b" * 64
SCENE = "c" * 64
GLOBALS = "d" * 64
PROFILE = "0" * 64
RELEASE = "e" * 64
SESSION = "f" * 32
BUDGET = "7" * 64


def _artifact_reference(path: Path, *, format_name: str | None = None) -> dict:
    content = path.read_bytes()
    result = {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size_bytes": len(content),
    }
    if format_name is not None:
        result["format"] = format_name
    return result


def _write_electrical_capture(directory: str | Path) -> tuple[Path, dict]:
    root = Path(directory)
    logger = root / "logger.csv"
    rows = ["timestamp_ms,voltage_v,current_a"]
    for index, timestamp in enumerate(range(1_950_000, 2_000_001, 1_000)):
        rows.append(f"{timestamp},{5.0 + index / 1000:.3f},{6.0 + index / 100:.2f}")
    logger.write_text("\n".join(rows) + "\n", encoding="utf-8")
    certificate = root / "calibration-certificate.pdf"
    certificate.write_bytes(b"test calibration certificate CAL-2026-001\n")
    descriptor = {
        "schema": "ledgrid.calibrated-electrical-capture",
        "schema_version": 2,
        "binding_digest": BINDING,
        "budget_digest": BUDGET,
        "activation_id": "perf-canary",
        "brightness": 50,
        "raw_logger_export": _artifact_reference(
            logger, format_name="ledgrid-electrical-csv-v1"
        ),
        "calibration_certificate": _artifact_reference(certificate),
        "instrument": {
            "manufacturer": "Traceable Instruments",
            "model": "VI Logger",
            "serial_number": "VI-001",
        },
        "calibration": {
            "certificate_id": "CAL-2026-001",
            "laboratory": "Accredited Calibration Lab",
            "calibrated_at": 1_800_000,
            "expires_at": 2_200_000,
        },
        "measurement": {
            "acquisition_method": "simultaneous four-wire voltage and shunt current",
            "topology": {
                "mode": "exact_measurement_points",
                "measurement_points": [{
                    "branch_id": "wall_main",
                    "voltage_point": "wall DC bus after branch fuse",
                    "current_point": "wall-exclusive DC feed shunt",
                }],
            },
        },
    }
    descriptor_path = root / "electrical-capture.json"
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    return descriptor_path, descriptor


def _device(logical_id: int, displayed: int) -> dict:
    widths = (8, 8, 8, 8, 1)
    offsets = (0, 8, 16, 24, 32)
    transfers = displayed
    full_frame_semantic = 1 + widths[logical_id] * 138 * 3
    semantic_bytes = transfers * full_frame_semantic
    envelope_bytes = transfers * (16 if logical_id == 3 else 4)
    aligned_padding = (-(full_frame_semantic + 6)) % 4
    fec_enabled = logical_id == 3
    fec_data_padding = 26 * transfers if fec_enabled else 0
    padding_bytes = transfers * aligned_padding + fec_data_padding
    crc_bytes = transfers * 2
    fec_parity = 730 * transfers if fec_enabled else 0
    full_frame_wire = (
        4088 if fec_enabled else ((full_frame_semantic + 9) // 4) * 4
    )
    status_transfers = transfers // 16
    return {
        "receiver_logical_device": logical_id,
        "receiver_status_version": 7,
        "receiver_status_max_version_seen": 7,
        "receiver_capabilities": 0x1FC00C,
        "transport_envelope_enabled": True,
        "transport_envelope_negotiation_candidate": None,
        "transport_envelope_negotiation_streak": 0,
        "transport_envelope_negotiation_required": 3,
        "fec_transport_negotiation_candidate": None,
        "fec_transport_negotiation_streak": 0,
        "fec_transport_negotiation_required": 3,
        "fec_transport_requested": fec_enabled,
        "fec_transport_enabled": fec_enabled,
        "spi_transfers": transfers,
        "semantic_bytes_sent": semantic_bytes,
        "transport_envelope_bytes_sent": envelope_bytes,
        "transport_padding_bytes_sent": padding_bytes,
        "crc_bytes_sent": crc_bytes,
        "fec_frames_sent": transfers if fec_enabled else 0,
        "fec_codewords_sent": 68 * transfers if fec_enabled else 0,
        "fec_parity_bytes_sent": fec_parity,
        "fec_data_padding_bytes_sent": fec_data_padding,
        "bytes_sent": (
            semantic_bytes + envelope_bytes + padding_bytes + crc_bytes + fec_parity
        ),
        "full_frame_transfers": transfers,
        "full_frame_semantic_bytes_sent": transfers * full_frame_semantic,
        "full_frame_wire_bytes_sent": transfers * full_frame_wire,
        "full_frame_status_transfers": status_transfers,
        "full_frame_status_samples": status_transfers - logical_id,
        "full_frame_status_sample_misses": logical_id,
        "full_frame_write_only_transfers": transfers - status_transfers,
        "full_frame_frames_since_status_sample": transfers % 16,
        "full_frame_max_status_sample_gap": 15,
        "spidev_buffer_size": 4096,
        "full_frame_write_only_supported": True,
        "receiver_active_strips": widths[logical_id],
        "receiver_global_strip_offset": offsets[logical_id],
        "receiver_leds_per_strip": 138,
        "total_leds": widths[logical_id] * 138,
        "spi_mode": 0,
        "spi_speed_hz": 20_000_000,
        "receiver_last_encode_us": 900 + logical_id,
        "receiver_last_show_us": 4400 + logical_id,
        "receiver_frames_displayed": displayed,
        "receiver_fec_packets_received": transfers if fec_enabled else 0,
        "receiver_fec_packets_accepted": transfers if fec_enabled else 0,
        "receiver_fec_corrected_packets": transfers // 100 if fec_enabled else 0,
        "receiver_fec_corrected_codewords": transfers // 100 if fec_enabled else 0,
        "receiver_fec_uncorrectable_packets": 0,
        "receiver_fec_semantic_crc_errors": 0,
        "receiver_fec_framing_errors": 0,
        "receiver_fec_last_decode_us": 80 if fec_enabled else 0,
        "receiver_fec_max_decode_us": 100 if fec_enabled else 0,
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
    devices = [_device(index, displayed) for index in range(5)]
    transport_fields = (
        "spi_transfers", "bytes_sent", "semantic_bytes_sent",
        "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
        "crc_bytes_sent", "full_frame_transfers",
        "full_frame_semantic_bytes_sent", "full_frame_wire_bytes_sent",
        "full_frame_status_transfers", "full_frame_status_samples",
        "full_frame_status_sample_misses", "full_frame_write_only_transfers",
        "fec_frames_sent", "fec_codewords_sent", "fec_parity_bytes_sent",
        "fec_data_padding_bytes_sent", "receiver_fec_packets_received",
        "receiver_fec_packets_accepted", "receiver_fec_corrected_packets",
        "receiver_fec_corrected_codewords", "receiver_fec_uncorrectable_packets",
        "receiver_fec_semantic_crc_errors", "receiver_fec_framing_errors",
    )
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
                "device_dispatch_order": [0, 1, 2, 3, 4],
                "transport_envelope_devices": 5,
                "fec_transport_requested_devices": 1,
                "fec_transport_enabled_devices": 1,
                "receiver_status_version": min(
                    device["receiver_status_version"] for device in devices
                ),
                "receiver_status_max_version_seen": min(
                    device["receiver_status_max_version_seen"]
                    for device in devices
                ),
                **{
                    field: sum(device[field] for device in devices)
                    for field in transport_fields
                },
                "full_frame_frames_since_status_sample": max(
                    device["full_frame_frames_since_status_sample"]
                    for device in devices
                ),
                "full_frame_max_status_sample_gap": max(
                    device["full_frame_max_status_sample_gap"]
                    for device in devices
                ),
                "spidev_buffer_size": min(
                    device["spidev_buffer_size"] for device in devices
                ),
                "full_frame_write_only_supported": all(
                    device["full_frame_write_only_supported"]
                    for device in devices
                ),
                "receiver_fec_last_decode_us": max(
                    device["receiver_fec_last_decode_us"] for device in devices
                ),
                "receiver_fec_max_decode_us": max(
                    device["receiver_fec_max_decode_us"] for device in devices
                ),
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
                        "spi_speed_hz": (
                            20_000_000
                        ),
                    }
                    for logical_id in range(5)
                ],
            },
            "devices": devices,
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
        "normalized_identity": deepcopy(identity),
        "observed_identity": deepcopy(identity),
        "controller": {"session_id": SESSION, "state_revision_after": 7},
        "telemetry": {"complete": True, "fresh": True, "observed_at": 123},
    }


def _runtime_identity() -> dict:
    identity = _receipt()["requested_identity"]
    return {
        "release_id": RELEASE,
        "controller_session_id": SESSION,
        "controller_state_revision": 7,
        "current_identity_digest": canonical_json_sha256(identity),
    }


RUNTIME_IDENTITY = _runtime_identity()


def _live_status() -> dict:
    identity = deepcopy(_receipt()["requested_identity"])
    return {
        "release_id": RELEASE,
        "controller_release_id": RELEASE,
        "release_consistent": True,
        "controller_session_id": SESSION,
        "controller_state_revision": 7,
        "active_identity": identity,
        "current_identity_digest": canonical_json_sha256(identity),
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
            runtime_identity=RUNTIME_IDENTITY,
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
        self.assertEqual(result["schema_version"], 3)
        transport = result["transport"]
        self.assertEqual(
            [item["logical_device"] for item in transport["devices"]],
            list(range(5)),
        )
        self.assertEqual(
            transport["devices"][0]["deltas"],
            {
                "full_frame_transfers": 150,
                "full_frame_status_transfers": 9,
                "full_frame_status_samples": 9,
                "full_frame_status_sample_misses": 0,
                "full_frame_write_only_transfers": 141,
            },
        )
        self.assertEqual(
            transport["aggregate"]["deltas"]["full_frame_transfers"], 750
        )
        self.assertEqual(
            transport["aggregate"]["final"]["spidev_buffer_size"], 4096
        )
        self.assertEqual(result["runtime_identity"], RUNTIME_IDENTITY)
        self.assertEqual(
            by_source["receiver"]["transport_digest"],
            canonical_json_sha256(transport),
        )

    def test_ingests_exact_traceable_electrical_artifact_into_controller_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = _write_electrical_capture(directory)
            artifact, electrical = load_calibrated_electrical_measurement(
                path,
                binding_digest=BINDING,
                budget_digest=BUDGET,
                activation_id="perf-canary",
                brightness=50,
                target_window_started_at=1_950_000,
                target_window_ended_at=2_000_000,
            )

            result = build_target_evidence(
                [_metrics(final=False), _metrics(final=True)],
                elapsed_seconds=1.0,
                binding_digest=BINDING,
                captured_at=2_000_000,
                target_fps=150,
                brightness=50,
                environment="Raspberry Pi 4; calibrated target window",
                runtime_identity=RUNTIME_IDENTITY,
                capture_started_at=1_950_000,
                electrical_measurement=artifact,
                electrical=electrical,
            )

            controller = next(
                item for item in result["evidence"] if item["source"] == "controller_pi"
            )
            receiver = next(
                item for item in result["evidence"] if item["source"] == "receiver"
            )
            self.assertEqual(controller["electrical"]["kind"], "calibrated_measurement")
            self.assertEqual(controller["electrical"]["budget_digest"], BUDGET)
            self.assertEqual(controller["electrical"]["provenance"]["sample_count"], 51)
            self.assertEqual(
                controller["electrical"]["provenance"]["descriptor_digest"],
                canonical_json_sha256(artifact),
            )
            self.assertEqual(result["electrical_measurement"], artifact)
            self.assertEqual(result["capture_started_at"], 1_950_000)
            self.assertIsNone(receiver["electrical"])

    def test_calibrated_artifact_fails_closed_on_identity_window_and_provenance(self) -> None:
        mutations = (
            ("binding_digest", lambda value: value.__setitem__("binding_digest", "9" * 64)),
            ("budget_digest", lambda value: value.__setitem__("budget_digest", "9" * 64)),
            ("activation_id", lambda value: value.__setitem__("activation_id", "other")),
            ("brightness", lambda value: value.__setitem__("brightness", 51)),
            (
                "wrong logger digest",
                lambda value: value["raw_logger_export"].__setitem__("sha256", "9" * 64),
            ),
            (
                "wrong logger size",
                lambda value: value["raw_logger_export"].__setitem__("size_bytes", 1),
            ),
            (
                "wrong certificate digest",
                lambda value: value["calibration_certificate"].__setitem__("sha256", "9" * 64),
            ),
            (
                "expired calibration",
                lambda value: value["calibration"].__setitem__("expires_at", 1_999_999),
            ),
            (
                "missing serial",
                lambda value: value["instrument"].pop("serial_number"),
            ),
            (
                "missing measurement branch",
                lambda value: value["measurement"]["topology"].__setitem__(
                    "measurement_points", []
                ),
            ),
            (
                "unknown field",
                lambda value: value.__setitem__("estimated_power_w", 42),
            ),
        )
        for label, mutate in mutations:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                path, artifact = _write_electrical_capture(directory)
                mutate(artifact)
                atomic_write_json(path, artifact)
                with self.assertRaises(TargetEvidenceError):
                    load_calibrated_electrical_measurement(
                        path,
                        binding_digest=BINDING,
                        budget_digest=BUDGET,
                        activation_id="perf-canary",
                        brightness=50,
                        target_window_started_at=1_950_000,
                        target_window_ended_at=2_000_000,
                    )

    def test_source_artifacts_remain_required_after_initial_ingestion(self) -> None:
        for artifact_name in ("raw_logger_export", "calibration_certificate"):
            with self.subTest(artifact=artifact_name), tempfile.TemporaryDirectory() as directory:
                path, _ = _write_electrical_capture(directory)
                descriptor, electrical = load_calibrated_electrical_measurement(
                    path,
                    binding_digest=BINDING,
                    budget_digest=BUDGET,
                    activation_id="perf-canary",
                    brightness=50,
                    target_window_started_at=1_950_000,
                    target_window_ended_at=2_000_000,
                )
                artifact_path = Path(descriptor[artifact_name]["path"])
                artifact_path.write_bytes(artifact_path.read_bytes() + b"tampered")
                with self.assertRaisesRegex(QualificationValidationError, "size"):
                    build_target_evidence(
                        [_metrics(final=False), _metrics(final=True)],
                        elapsed_seconds=1.0,
                        binding_digest=BINDING,
                        captured_at=2_000_000,
                        target_fps=150,
                        brightness=50,
                        environment="Raspberry Pi 4 test",
                        runtime_identity=RUNTIME_IDENTITY,
                        capture_started_at=1_950_000,
                        electrical_measurement=descriptor,
                        electrical=electrical,
                    )

    def test_reviewed_common_distribution_requires_bound_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, descriptor = _write_electrical_capture(directory)
            attestation = Path(directory) / "topology-review.txt"
            attestation.write_text("reviewed common feed covers branches A and B\n", encoding="utf-8")
            descriptor["measurement"]["topology"] = {
                "mode": "reviewed_common_distribution",
                "voltage_point": "common DC bus",
                "current_point": "common wall-exclusive shunt",
                "covered_branches": ["branch_b", "branch_a"],
                "reviewed_by": "Electrical Reviewer",
                "reviewed_at": 1_850_000,
                "topology_attestation": _artifact_reference(attestation),
            }
            atomic_write_json(path, descriptor)
            normalized, _ = load_calibrated_electrical_measurement(
                path,
                binding_digest=BINDING,
                budget_digest=BUDGET,
                activation_id="perf-canary",
                brightness=50,
                target_window_started_at=1_950_000,
                target_window_ended_at=2_000_000,
            )
            self.assertEqual(
                normalized["measurement"]["topology"]["covered_branches"],
                ["branch_a", "branch_b"],
            )
            descriptor["measurement"]["topology"].pop("topology_attestation")
            atomic_write_json(path, descriptor)
            with self.assertRaises(TargetEvidenceError):
                load_calibrated_electrical_measurement(
                    path,
                    binding_digest=BINDING,
                    budget_digest=BUDGET,
                    activation_id="perf-canary",
                    brightness=50,
                    target_window_started_at=1_950_000,
                    target_window_ended_at=2_000_000,
                )

    def test_target_envelope_rejects_detached_or_tampered_electrical_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path, _ = _write_electrical_capture(directory)
            artifact, electrical = load_calibrated_electrical_measurement(
                path,
                binding_digest=BINDING,
                budget_digest=BUDGET,
                activation_id="perf-canary",
                brightness=50,
                target_window_started_at=1_950_000,
                target_window_ended_at=2_000_000,
            )
            with self.assertRaisesRegex(TargetEvidenceError, "supplied together"):
                build_target_evidence(
                    [_metrics(final=False), _metrics(final=True)],
                    elapsed_seconds=1.0,
                    binding_digest=BINDING,
                    captured_at=2_000_000,
                    target_fps=150,
                    brightness=50,
                    environment="Raspberry Pi 4 test",
                    runtime_identity=RUNTIME_IDENTITY,
                    capture_started_at=1_950_000,
                    electrical=electrical,
                )

            tampered = deepcopy(electrical)
            tampered["current_a"]["max"] = 6.9
            with self.assertRaisesRegex(QualificationValidationError, "raw logger"):
                build_target_evidence(
                    [_metrics(final=False), _metrics(final=True)],
                    elapsed_seconds=1.0,
                    binding_digest=BINDING,
                    captured_at=2_000_000,
                    target_fps=150,
                    brightness=50,
                    environment="Raspberry Pi 4 test",
                    runtime_identity=RUNTIME_IDENTITY,
                    capture_started_at=1_950_000,
                    electrical_measurement=artifact,
                    electrical=tampered,
                )

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
                runtime_identity=RUNTIME_IDENTITY,
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
                runtime_identity=RUNTIME_IDENTITY,
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
            runtime_identity=RUNTIME_IDENTITY,
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
            runtime_identity=RUNTIME_IDENTITY,
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
                        runtime_identity=RUNTIME_IDENTITY,
                    )

    def test_installed_topology_binds_widths_routes_and_offsets(self) -> None:
        metrics = _metrics(final=False)
        validate_installed_topology(metrics)

        mutations = (
            ("dispatch", ("aggregate", 0, "device_dispatch_order", [0, 1, 3, 2, 4])),
            ("route", ("device_map", 2, "chip_select", 0)),
            ("width", ("device_map", 4, "local_strip_count", 8)),
            ("offset", ("devices", 3, "receiver_global_strip_offset", 16)),
            ("capabilities", ("devices", 0, "receiver_capabilities", 0x000C)),
            ("host disabled", ("devices", 2, "transport_envelope_enabled", False)),
            ("aggregate disabled", ("aggregate", 0, "transport_envelope_devices", 4)),
        )
        for label, (collection, index, field, replacement) in mutations:
            with self.subTest(label=label):
                changed = deepcopy(metrics)
                if collection == "device_map":
                    changed["driver"]["aggregate"][collection][index][field] = replacement
                elif collection == "aggregate":
                    changed["driver"]["aggregate"][field] = replacement
                else:
                    changed["driver"][collection][index][field] = replacement
                expected_error = (
                    "qualified order"
                    if label == "dispatch"
                    else "aligned transport capabilities"
                    if label == "capabilities"
                    else "not enabled"
                    if label == "host disabled"
                    else "exactly five"
                    if label == "aggregate disabled"
                    else field
                )
                with self.assertRaisesRegex(TargetEvidenceError, expected_error):
                    validate_installed_topology(changed)

    def test_status_v7_observation_is_sticky_across_later_v3_samples(self) -> None:
        raced = _metrics(final=False)
        for device in raced["driver"]["devices"]:
            device["receiver_status_version"] = 3
        raced["driver"]["aggregate"]["receiver_status_version"] = 3
        validate_installed_topology(raced)

        never_v7 = deepcopy(raced)
        never_v7["driver"]["devices"][0][
            "receiver_status_max_version_seen"
        ] = 6
        never_v7["driver"]["aggregate"][
            "receiver_status_max_version_seen"
        ] = 6
        with self.assertRaisesRegex(TargetEvidenceError, "required observed>=v7"):
            validate_installed_topology(never_v7)

    def test_perf_rejects_missing_stalled_and_drifted_transport_accounting(self) -> None:
        cases = []
        missing = _metrics(final=True)
        missing["driver"]["devices"][0].pop("semantic_bytes_sent")
        cases.append(("missing", missing, "semantic_bytes_sent"))
        stalled = _metrics(final=True)
        initial = _metrics(final=False)
        for field in (
            "spi_transfers", "bytes_sent", "semantic_bytes_sent",
            "transport_envelope_bytes_sent", "transport_padding_bytes_sent",
            "crc_bytes_sent",
        ):
            stalled["driver"]["devices"][0][field] = initial["driver"]["devices"][0][field]
            stalled["driver"]["aggregate"][field] = sum(
                device[field] for device in stalled["driver"]["devices"]
            )
        cases.append(("stalled", stalled, "did not advance"))
        status_only = _metrics(final=True)
        for field in (
            "full_frame_transfers", "full_frame_semantic_bytes_sent",
            "full_frame_wire_bytes_sent",
            "full_frame_status_transfers", "full_frame_status_samples",
            "full_frame_status_sample_misses", "full_frame_write_only_transfers",
        ):
            for logical_id in range(5):
                status_only["driver"]["devices"][logical_id][field] = (
                    initial["driver"]["devices"][logical_id][field]
                )
            status_only["driver"]["aggregate"][field] = sum(
                device[field] for device in status_only["driver"]["devices"]
            )
        cases.append(("status-only", status_only, "full_frame_transfers did not advance"))
        drifted = _metrics(final=True)
        drifted["driver"]["devices"][0]["bytes_sent"] += 1
        drifted["driver"]["aggregate"]["bytes_sent"] += 1
        cases.append(("drifted", drifted, "wire-byte accounting"))
        for label, final, expected in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                TargetEvidenceError, expected
            ):
                build_target_evidence(
                    [_metrics(final=False), final],
                    elapsed_seconds=1.0,
                    binding_digest=BINDING,
                    captured_at=2_000_000,
                    target_fps=150,
                    brightness=50,
                    environment="Raspberry Pi 4 test",
                    runtime_identity=RUNTIME_IDENTITY,
                )

    def test_perf_requires_complete_settled_three_observation_negotiation(self) -> None:
        cases = []
        missing = _metrics(final=False)
        missing["driver"]["devices"][0].pop(
            "transport_envelope_negotiation_candidate"
        )
        cases.append(("missing", missing))
        pending = _metrics(final=False)
        pending["driver"]["devices"][0].update({
            "transport_envelope_negotiation_candidate": False,
            "transport_envelope_negotiation_streak": 1,
        })
        cases.append(("pending", pending))
        wrong_required = _metrics(final=False)
        wrong_required["driver"]["devices"][0][
            "transport_envelope_negotiation_required"
        ] = 2
        cases.append(("wrong required", wrong_required))
        fec_pending = _metrics(final=False)
        fec_pending["driver"]["devices"][3].update({
            "fec_transport_negotiation_candidate": True,
            "fec_transport_negotiation_streak": 2,
        })
        cases.append(("FEC pending", fec_pending))
        for label, metrics in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                TargetEvidenceError, "negotiation is not settled"
            ):
                validate_installed_topology(metrics)

    def test_fec_capture_requires_exact_send_receive_accept_and_zero_terminal_faults(self) -> None:
        initial = _metrics(final=False)
        cases = []
        missing = _metrics(final=True)
        for field in (
            "receiver_fec_packets_received",
            "receiver_fec_packets_accepted",
        ):
            missing["driver"]["devices"][3][field] -= 1
            missing["driver"]["aggregate"][field] -= 1
        cases.append(("missing", missing, "exactly match sent"))
        for field in (
            "receiver_fec_uncorrectable_packets",
            "receiver_fec_semantic_crc_errors",
            "receiver_fec_framing_errors",
        ):
            terminal = _metrics(final=True)
            terminal["driver"]["devices"][3][field] += 1
            terminal["driver"]["devices"][3]["receiver_fec_packets_received"] += 1
            terminal["driver"]["aggregate"][field] += 1
            terminal["driver"]["aggregate"]["receiver_fec_packets_received"] += 1
            cases.append((field, terminal, field))
        for label, final, expected in cases:
            with self.subTest(label=label), self.assertRaisesRegex(
                TargetEvidenceError, expected
            ):
                build_target_evidence(
                    [initial, final],
                    elapsed_seconds=1.0,
                    binding_digest=BINDING,
                    captured_at=2_000_000,
                    target_fps=150,
                    brightness=50,
                    environment="Raspberry Pi 4 test",
                    runtime_identity=RUNTIME_IDENTITY,
                )

    def test_perf_rejects_invalid_full_frame_status_sampling_telemetry(self) -> None:
        def sum_field(metrics, field):
            metrics["driver"]["aggregate"][field] = sum(
                device[field] for device in metrics["driver"]["devices"]
            )

        missing = _metrics(final=False)
        missing["driver"]["devices"][0].pop("full_frame_status_samples")

        broken = _metrics(final=False)
        broken["driver"]["devices"][0]["full_frame_write_only_transfers"] += 1
        sum_field(broken, "full_frame_write_only_transfers")

        unclassified = _metrics(final=False)
        unclassified["driver"]["devices"][0]["full_frame_status_samples"] -= 1
        sum_field(unclassified, "full_frame_status_samples")

        excessive_gap = _metrics(final=False)
        excessive_gap["driver"]["devices"][0]["full_frame_frames_since_status_sample"] = 257
        excessive_gap["driver"]["devices"][0]["full_frame_max_status_sample_gap"] = 257
        excessive_gap["driver"]["aggregate"]["full_frame_frames_since_status_sample"] = 257
        excessive_gap["driver"]["aggregate"]["full_frame_max_status_sample_gap"] = 257

        unsupported = _metrics(final=False)
        unsupported["driver"]["devices"][0]["full_frame_write_only_supported"] = False
        unsupported["driver"]["aggregate"]["full_frame_write_only_supported"] = False

        undersized = _metrics(final=False)
        undersized["driver"]["devices"][0]["spidev_buffer_size"] = 3319
        undersized["driver"]["aggregate"]["spidev_buffer_size"] = 3319

        for label, metrics, expected in (
            ("missing", missing, "full_frame_status_samples is unavailable"),
            ("broken invariant", broken, "transfer invariant is broken"),
            ("unclassified", unclassified, "status transfer classification is broken"),
            ("gap", excessive_gap, "gap is outside 0..256"),
            ("unsupported", unsupported, "fast path is unavailable"),
            ("buffer", undersized, "below 3320 bytes"),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                TargetEvidenceError, expected
            ):
                validate_installed_topology(metrics)

        initial = _metrics(final=False)
        miss = _metrics(final=True)
        miss["driver"]["devices"][0]["full_frame_status_sample_misses"] += 1
        miss["driver"]["devices"][0]["full_frame_status_transfers"] += 1
        miss["driver"]["devices"][0]["full_frame_write_only_transfers"] -= 1
        sum_field(miss, "full_frame_status_sample_misses")
        sum_field(miss, "full_frame_status_transfers")
        sum_field(miss, "full_frame_write_only_transfers")

        stalled_samples = _metrics(final=True)
        stalled_samples["driver"]["devices"][0]["full_frame_status_samples"] = (
            initial["driver"]["devices"][0]["full_frame_status_samples"]
        )
        stalled_samples["driver"]["devices"][0]["full_frame_status_transfers"] = (
            initial["driver"]["devices"][0]["full_frame_status_transfers"]
        )
        stalled_samples["driver"]["devices"][0]["full_frame_write_only_transfers"] = (
            stalled_samples["driver"]["devices"][0]["full_frame_transfers"]
            - stalled_samples["driver"]["devices"][0]["full_frame_status_transfers"]
        )
        sum_field(stalled_samples, "full_frame_status_samples")
        sum_field(stalled_samples, "full_frame_status_transfers")
        sum_field(stalled_samples, "full_frame_write_only_transfers")

        reset_samples = _metrics(final=True)
        reset_samples["driver"]["devices"][0]["full_frame_status_samples"] = (
            initial["driver"]["devices"][0]["full_frame_status_samples"] - 1
        )
        reset_samples["driver"]["devices"][0]["full_frame_status_transfers"] = (
            reset_samples["driver"]["devices"][0]["full_frame_status_samples"]
            + reset_samples["driver"]["devices"][0][
                "full_frame_status_sample_misses"
            ]
        )
        reset_samples["driver"]["devices"][0]["full_frame_write_only_transfers"] = (
            reset_samples["driver"]["devices"][0]["full_frame_transfers"]
            - reset_samples["driver"]["devices"][0]["full_frame_status_transfers"]
        )
        sum_field(reset_samples, "full_frame_status_samples")
        sum_field(reset_samples, "full_frame_status_transfers")
        sum_field(reset_samples, "full_frame_write_only_transfers")

        reset_gap = _metrics(final=True)
        for device in reset_gap["driver"]["devices"]:
            device["full_frame_max_status_sample_gap"] = 14
            device["full_frame_frames_since_status_sample"] = min(
                device["full_frame_frames_since_status_sample"], 14
            )
        reset_gap["driver"]["aggregate"]["full_frame_max_status_sample_gap"] = 14
        reset_gap["driver"]["aggregate"]["full_frame_frames_since_status_sample"] = max(
            device["full_frame_frames_since_status_sample"]
            for device in reset_gap["driver"]["devices"]
        )

        for label, final, expected in (
            ("miss", miss, "sample misses increased"),
            ("stalled", stalled_samples, "status samples did not advance"),
            ("counter reset", reset_samples, "sampling counter reset"),
            ("gap reset", reset_gap, "maximum status sample gap reset"),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                TargetEvidenceError, expected
            ):
                build_target_evidence(
                    [initial, final],
                    elapsed_seconds=1.0,
                    binding_digest=BINDING,
                    captured_at=2_000_000,
                    target_fps=150,
                    brightness=50,
                    environment="Raspberry Pi 4 test",
                    runtime_identity=RUNTIME_IDENTITY,
                )

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
            (
                "normalized drift",
                lambda value: value["normalized_identity"]["scene_identity"].__setitem__(
                    "digest", "8" * 64
                ),
            ),
            ("missing normalized", lambda value: value.pop("normalized_identity")),
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

    def test_runtime_identity_binds_release_session_and_active_runtime(self) -> None:
        self.assertEqual(
            validate_runtime_identity(_live_status(), _receipt()),
            RUNTIME_IDENTITY,
        )
        cases = []
        release_mismatch = _live_status()
        release_mismatch["controller_release_id"] = "8" * 64
        cases.append(("release", release_mismatch, _receipt()))
        bad_consistency = _live_status()
        bad_consistency["release_consistent"] = False
        cases.append(("consistent", bad_consistency, _receipt()))
        session_mismatch = _receipt()
        session_mismatch["controller"]["session_id"] = "7" * 32
        cases.append(("session", _live_status(), session_mismatch))
        revision_mismatch = _live_status()
        revision_mismatch["controller_state_revision"] = 8
        cases.append(("revision", revision_mismatch, _receipt()))
        missing_receipt_revision = _receipt()
        missing_receipt_revision["controller"].pop("state_revision_after")
        cases.append(("missing receipt revision", _live_status(), missing_receipt_revision))
        runtime_mismatch = _live_status()
        runtime_mismatch["current_identity_digest"] = "6" * 64
        cases.append(("runtime", runtime_mismatch, _receipt()))
        receipt_runtime_mismatch = _receipt()
        receipt_runtime_mismatch["requested_identity"]["scene_identity"][
            "digest"
        ] = "5" * 64
        cases.append(("receipt runtime", _live_status(), receipt_runtime_mismatch))
        normalized_mismatch = _receipt()
        normalized_mismatch["normalized_identity"]["scene_identity"][
            "digest"
        ] = "4" * 64
        cases.append(("normalized runtime", _live_status(), normalized_mismatch))
        for label, status, receipt in cases:
            with self.subTest(label=label), self.assertRaises(TargetEvidenceError):
                validate_runtime_identity(status, receipt)

    def test_capture_rejects_same_runtime_after_controller_revision_changes(self) -> None:
        scene = {
            "schema": "ledgrid.scene-state",
            "schema_version": 1,
            "revision": 7,
            "background": {
                "provider": "python",
                "plugin_id": "rainbow",
                "resolved_parameters": {"speed": 0.3},
                "parameter_overrides": {},
            },
            "overlays": [],
            "known_python_fallback": {
                "provider": "python",
                "plugin_id": "rainbow",
                "resolved_parameters": {"speed": 0.3},
                "parameter_overrides": {},
            },
        }
        scene_digest = canonical_scene_digest(scene)
        receipt = _receipt()
        for field in (
            "requested_identity", "normalized_identity", "observed_identity"
        ):
            receipt[field]["scene_identity"]["digest"] = scene_digest
        status_calls = 0

        def live_status() -> dict:
            nonlocal status_calls
            status_calls += 1
            identity = deepcopy(receipt["requested_identity"])
            status = _live_status()
            status.update({
                "controller_state_revision": 7 if status_calls <= 2 else 8,
                "active_identity": identity,
                "current_identity_digest": canonical_json_sha256(identity),
                "scene_state": deepcopy(scene),
                "target_fps": 150,
                "brightness": 50,
                "installation_profile_digest": PROFILE,
                "current_animation": "rainbow",
                "is_running": True,
            })
            return status

        metrics = _metrics(final=False)
        metrics["driver"]["aggregate"]["receiver_status_refresh"] = {
            "request_id": "refresh-1",
            "completed_at": 1.0,
            "passed": True,
            "errors": [],
        }

        def get_json(url: str):
            if url.endswith("/api/v1/composer/operations/telemetry"):
                status = live_status()
                return {
                    "schema": "ledgrid.composer-operations-telemetry",
                    "schema_version": 1,
                    "controller": status,
                    "diagnostics": {
                        "performance": metrics.get("performance"),
                        "driver_stats": metrics.get("driver"),
                    },
                    "calibration": {
                        "installation_profile_digest": status.get("installation_profile_digest"),
                        "plant_modifiers": status.get("plant_modifiers"),
                    },
                    "qualification": {
                        key: status.get(key)
                        for key in ("active_identity", "scene", "scene_state", "latest_activation")
                    },
                    "receiver_native": status.get("receiver_hybrid"),
                }
            if "/api/v1/scene/activations/" in url:
                return deepcopy(receipt)
            raise AssertionError(f"unexpected URL {url}")

        with self.assertRaisesRegex(
            TargetEvidenceError, "state revision does not match live status"
        ):
            capture(
                base_url="http://test",
                binding_digest=BINDING,
                basis_digest=BASIS,
                scene_digest=scene_digest,
                global_settings_digest=GLOBALS,
                profile_digest=PROFILE,
                activation_id="perf-canary",
                plugin="rainbow",
                target_fps=150,
                brightness=50,
                warmup=0.0,
                duration=0.0,
                interval=1.0,
                get_json=get_json,
                post_json=lambda _url, _payload: {"request_id": "refresh-1"},
                monotonic=lambda: 0.0,
                sleep=lambda _seconds: None,
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
