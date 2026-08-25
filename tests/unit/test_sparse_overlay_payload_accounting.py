"""Acceptance tests for deterministic Phase 3B0 payload accounting."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import types
import unittest


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from drivers import spi_controller as protocol
from tools.benchmarks import sparse_overlay_payload as accounting


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "benchmarks" / "sparse_overlay_payload.py"


class SparseOverlayWireAccountingTests(unittest.TestCase):
    def test_accounting_constants_match_the_live_host_protocol(self):
        pairs = (
            (accounting.CRC_BYTES, protocol.CRC_BYTES),
            (
                accounting.CONTROLLER_SESSION_BEGIN_BYTES,
                protocol.CONTROLLER_SESSION_BEGIN_BYTES,
            ),
            (accounting.OVERLAY_BEGIN_BYTES, protocol.OVERLAY_BEGIN_BYTES),
            (
                accounting.OVERLAY_PATCH_HEADER_BYTES,
                protocol.OVERLAY_PATCH_HEADER_BYTES,
            ),
            (
                accounting.OVERLAY_PATCH_BATCH_HEADER_BYTES,
                protocol.OVERLAY_PATCH_BATCH_HEADER_BYTES,
            ),
            (
                accounting.OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES,
                protocol.OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES,
            ),
            (accounting.OVERLAY_COMMIT_BYTES, protocol.OVERLAY_COMMIT_BYTES),
            (accounting.OVERLAY_RENEW_BYTES, protocol.OVERLAY_RENEW_BYTES),
            (
                accounting.RECEIVER_STATUS_BYTES_V3,
                protocol.RECEIVER_STATUS_BYTES_V3,
            ),
            (
                accounting.RECEIVER_STATUS_BYTES_V4,
                protocol.RECEIVER_STATUS_BYTES_V4,
            ),
            (
                accounting.SPI_RESPONSE_QUEUE_DEPTH,
                protocol.SPI_RESPONSE_QUEUE_DEPTH,
            ),
            (
                accounting.MAX_RGBA_PIXELS_PER_PATCH,
                protocol.MAX_RGBA_PIXELS_PER_PATCH,
            ),
            (
                accounting.MAX_RGBA_PIXELS_PER_BATCH_SPAN,
                protocol.MAX_RGBA_PIXELS_PER_BATCH_SPAN,
            ),
        )
        for benchmark_value, driver_value in pairs:
            with self.subTest(value=benchmark_value):
                self.assertEqual(benchmark_value, driver_value)
        self.assertEqual(accounting.MAX_RGBA_PIXELS_PER_PATCH, 1016)
        self.assertEqual(accounting.MAX_RGBA_PIXELS_PER_BATCH_SPAN, 1015)
        self.assertEqual(accounting.LOCAL_RGB_PACKET_BYTES, 3315)
        self.assertEqual(
            accounting.RECEIVER_RGB_PACKET_BYTES,
            (3315, 3315, 3315, 3315, 417),
        )
        self.assertEqual(accounting.FULL_WALL_RGB_PACKET_BYTES, 13677)
        self.assertEqual(accounting.STATUS_V3_QUERY_TRANSFER_BYTES, 322)
        self.assertEqual(accounting.STATUS_V4_QUERY_TRANSFER_BYTES, 418)

    def test_one_acknowledged_command_counts_command_crc_queries_and_responses(self):
        result = accounting.WireAccount()
        # One premultiplied RGBA pixel: 30-byte header + 4-byte body pre-CRC.
        result.add_acknowledged_command("overlay_patch", 34)
        self.assertEqual(result.command_packet_bytes, 36)
        self.assertEqual(result.status_query_count, 5)
        self.assertEqual(result.status_query_transfer_bytes, 5 * 418)
        self.assertEqual(result.meaningful_status_response_bytes, 5 * 416)
        self.assertEqual(result.spi_clocked_bytes, 36 + 5 * 418)
        self.assertEqual(result.to_dict()["bidirectional_endpoint_bytes"], 4252)
        self.assertEqual(result.acknowledgement_count, 1)

    def test_full_snapshot_uses_exact_heterogeneous_receiver_spans(self):
        ranges = accounting._full_snapshot_ranges()
        self.assertEqual(ranges, ((0, 1015), (1015, 1104)))
        self.assertEqual(accounting.batch_packet_bytes((ranges[0],)), 4094)
        self.assertEqual(accounting.batch_packet_bytes((ranges[1],)), 390)
        self.assertEqual(accounting.batch_packet_bytes(ranges), 4484)
        self.assertEqual(accounting.patch_packet_bytes(ranges), 4480)
        self.assertEqual(
            sum(
                accounting.batch_packet_bytes(
                    accounting._full_snapshot_ranges(local_pixels)
                )
                for local_pixels in accounting.RECEIVER_PIXELS
            ),
            18522,
        )

    def test_invalid_patch_accounting_cannot_hide_zero_or_oversized_body(self):
        for ranges in (((0, 0),), ((0, 1017),), ((4, 3),)):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                accounting.patch_packet_bytes(ranges)

    def test_invalid_batch_accounting_cannot_hide_zero_or_oversized_body(self):
        for ranges in (((0, 0),), ((0, 1016),), ((4, 3),)):
            with self.subTest(ranges=ranges), self.assertRaises(ValueError):
                accounting.batch_packet_bytes(ranges)


class SparseOverlayPayloadTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = accounting.build_report()
        accounting.validate_report(cls.report)

    def test_real_fixed_clock_tick_batches_exact_changed_pixels_below_ten_percent(self):
        tick = self.report["ordinary_changed_tick"]
        self.assertEqual(tick["second"], 1)
        self.assertEqual(tick["dirty_ranges"], 5)
        self.assertEqual(tick["patches"], 5)
        self.assertEqual(tick["batch_packets"], 1)
        self.assertEqual(tick["patch_pixels"], 16)
        self.assertEqual(tick["rgba_body_bytes"], 64)
        self.assertEqual(tick["batch_overhead_bytes"], 50)
        self.assertEqual(tick["legacy_single_span_packet_bytes"], 224)
        self.assertEqual(tick["patch_packet_bytes_including_headers_crc"], 114)
        self.assertEqual(tick["full_wall_rgb_packet_bytes"], 13677)
        self.assertAlmostEqual(tick["patch_ratio"], 0.008335, places=6)
        self.assertTrue(tick["below_10_percent"])
        # The patch-only gate is distinct from total acknowledged traffic: the
        # changed receiver batch still receives a complete queued-response proof.
        acknowledged = tick["acknowledged_generation_plus_renewal"]
        self.assertEqual(acknowledged["command_count"], 16)
        self.assertEqual(acknowledged["status_query_count"], 80)
        self.assertEqual(acknowledged["spi_clocked_bytes"], 34314)
        self.assertAlmostEqual(
            tick["acknowledged_ratio_to_one_full_wall_packet"],
            2.508884,
            places=6,
        )

    def test_sixty_second_trace_counts_repairs_renewals_retry_and_every_ack(self):
        trace = self.report["trace"]
        sparse = trace["sparse"]
        events = sparse["event_counts"]
        self.assertEqual(trace["clock_changed_ticks"], 60)
        self.assertEqual(trace["repair_snapshots"], 2)
        self.assertEqual(events["controller_session_begin"], 5)
        self.assertEqual(events["repair_patch_batch"], 18)
        self.assertEqual(events["delta_patch_batch"], 62)
        self.assertEqual(events["lease_renew"], 300)
        self.assertEqual(events["exact_batch_retry"], 2)
        self.assertEqual(self.report["policy"]["retry_receivers"], [1, 2])
        self.assertEqual(events["publish_preflight_query"], 300)
        self.assertEqual(events["publish_verification_query"], 300)
        self.assertEqual(events["renewal_preflight_query"], 300)
        self.assertEqual(sparse["command_count"], 987)
        self.assertEqual(sparse["acknowledgement_count"], 987)
        self.assertEqual(sparse["status_query_count"], 5835)
        self.assertEqual(events["status_v3_query"], 5)
        self.assertEqual(events["status_v4_query"], 5830)
        self.assertEqual(sparse["command_packet_bytes"], 87908)
        self.assertEqual(sparse["status_query_transfer_bytes"], 2438550)
        self.assertEqual(sparse["meaningful_status_response_bytes"], 2426880)
        self.assertEqual(sparse["spi_clocked_bytes"], 2526458)
        self.assertEqual(sparse["mosi_bytes"], 2526458)
        self.assertEqual(sparse["miso_bytes"], 2526458)
        self.assertEqual(sparse["bidirectional_endpoint_bytes"], 5052916)
        self.assertEqual(trace["baseline_full_rgb_frames"], 3600)
        self.assertEqual(trace["baseline_spi_clocked_bytes"], 49237200)
        self.assertEqual(trace["baseline_bidirectional_endpoint_bytes"], 98474400)
        self.assertAlmostEqual(trace["savings_ratio"], 0.948688, places=6)
        self.assertTrue(trace["at_least_90_percent_savings"])
        cause = self.report["architectural_cause"]
        self.assertAlmostEqual(cause["ordinary_header_crc_fraction"], 0.438596, places=6)
        self.assertAlmostEqual(cause["trace_status_query_fraction"], 0.965205, places=6)
        self.assertIn("implemented: batch multiple sorted spans", cause["required_direction"])

    def test_passing_acceptance_is_stable_evidence(self):
        acceptance = self.report["acceptance"]
        self.assertEqual(acceptance, {
            "ordinary_changed_tick_below_10_percent": True,
            "sixty_second_trace_at_least_90_percent_savings": True,
            "all_gates_pass": True,
            "check_enforces_acceptance_gates": True,
        })
        self.assertIn("one 28-byte batch header", self.report["diagnosis"])
        self.assertIn("one command/result proof", self.report["diagnosis"])

    def test_accounting_validator_detects_internal_category_drift(self):
        mutated = json.loads(json.dumps(self.report))
        mutated["trace"]["sparse"]["status_query_count"] -= 1
        with self.assertRaisesRegex(RuntimeError, "queued acknowledgements"):
            accounting.validate_report(mutated)

    def test_accounting_validator_detects_omitted_retry_or_query(self):
        for event in ("exact_batch_retry", "publish_verification_query"):
            mutated = json.loads(json.dumps(self.report))
            mutated["trace"]["sparse"]["event_counts"][event] -= 1
            with self.subTest(event=event), self.assertRaisesRegex(
                RuntimeError, "omitted or added|queued acknowledgements"
            ):
                accounting.validate_report(mutated)

    def test_check_cli_returns_success_and_reports_both_green_gates(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--check"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload["acceptance"]["all_gates_pass"])
        self.assertTrue(payload["acceptance"]["check_enforces_acceptance_gates"])


if __name__ == "__main__":
    unittest.main()
