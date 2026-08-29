import unittest

from tools.diagnostics.receiver_dispatch_order import (
    DispatchDiagnosticError,
    apply_dispatch_order,
    counter_deltas,
    parse_args,
    validate_dispatch_order,
)


class _Controller:
    device_map = [(0, 0), (0, 1), (1, 1), (1, 0), (1, 2)]

    def __init__(self):
        self._devices_by_bus = {0: [0, 1], 1: [2, 3, 4]}


class ReceiverDispatchOrderDiagnosticTests(unittest.TestCase):
    def test_susceptible_route_first_changes_schedule_not_mapping(self):
        controller = _Controller()
        original_map = list(controller.device_map)

        applied = apply_dispatch_order(controller, (0, 1, 3, 2, 4))

        self.assertEqual(applied, (0, 1, 3, 2, 4))
        self.assertEqual(controller._devices_by_bus, {0: [0, 1], 1: [3, 2, 4]})
        self.assertEqual(controller.device_map, original_map)

    def test_order_validation_rejects_missing_duplicate_and_crossed_bus_groups(self):
        for invalid in (
            (0, 1, 2, 3),
            (0, 1, 2, 3, 3),
            (0, 2, 1, 3, 4),
            (True, 1, 2, 3, 4),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_dispatch_order(invalid)

    def test_counter_deltas_are_exact_and_fail_closed_on_reset(self):
        fields = {
            "frames_sent": 10,
            "full_frame_transfers": 10,
            "full_frame_status_transfers": 1,
            "full_frame_status_sample_misses": 0,
            "fec_frames_sent": 10,
            "receiver_packets": 20,
            "receiver_crc_errors": 2,
            "receiver_frames_accepted": 8,
            "receiver_frames_displayed": 8,
            "receiver_publish_drops": 0,
            "receiver_spi_queue_errors": 0,
            "receiver_display_errors": 0,
            "receiver_status_misses": 1,
            "receiver_fec_packets_received": 10,
            "receiver_fec_packets_accepted": 8,
            "receiver_fec_corrected_packets": 0,
            "receiver_fec_corrected_codewords": 0,
            "receiver_fec_uncorrectable_packets": 2,
            "receiver_fec_semantic_crc_errors": 0,
            "receiver_fec_framing_errors": 0,
        }
        before = [{name: 0 for name in fields}]
        self.assertEqual(counter_deltas(before, [fields])[0]["receiver_crc_errors"], 2)

        reset = dict(fields, receiver_crc_errors=-1)
        with self.assertRaisesRegex(DispatchDiagnosticError, "counter"):
            counter_deltas(before, [reset])

    def test_cli_defaults_to_aba_at_120_and_160(self):
        args = parse_args([])
        self.assertEqual(args.target_fps, [120, 160])
        self.assertEqual(
            args.orders,
            [(0, 1, 2, 3, 4), (0, 1, 3, 2, 4), (0, 1, 2, 3, 4)],
        )


if __name__ == "__main__":
    unittest.main()
