import contextlib
from copy import deepcopy
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from types import ModuleType
from unittest import mock

from tools.diagnostics.receiver_phase_lane_isolation import (
    RESET_COUNTER_FIELD,
    DispatchDiagnosticError,
    build_ab_comparisons,
    build_arm_plan,
    _bind_board_inventories,
    _fresh_validated_snapshots,
    _controller,
    _load_board_inventory,
    _post_service_production_observation,
    _preflight_source_and_topology,
    _report,
    _write_report,
    classify_targeted_swap_arms,
    phase_experiments,
    production_state,
    receiver3_lane_experiments,
    require_exact_transfer_deltas,
    strict_counter_deltas,
    three_phase_group_masks,
    validate_snapshot,
    run,
)


def _snapshot(logical_id=0, **overrides):
    from tools.diagnostics.receiver_phase_lane_isolation import STRICT_DELTA_FIELDS

    result = {field: 0 for field in STRICT_DELTA_FIELDS}
    result.update(
        {
            "receiver_status_seen": True,
            "receiver_status_version": 7,
            "receiver_status_max_version_seen": 7,
            "receiver_logical_device": logical_id,
            "receiver_active_strips": 1 if logical_id == 4 else 8,
            "receiver_leds_per_strip": 138,
            "receiver_global_strip_offset": (0, 8, 16, 24, 32)[logical_id],
            "receiver_fec_terminal_baseline_invalid": False,
            "receiver_fec_terminal_baseline_established": logical_id == 3,
            "fec_transport_enabled": logical_id == 3,
            "receiver_stagger_phases": 3,
            "receiver_lane_mask": 0xFF,
        }
    )
    result.update(overrides)
    return result


def _board_inventory(*, swap=False):
    routes = ([0, 0], [0, 1], [1, 1], [1, 0], [1, 2])
    serials = [f"02:00:00:00:00:{index:02x}" for index in range(5)]
    if swap:
        serials[2], serials[3] = serials[3], serials[2]
    return {
        "schema": "ledgrid.receiver-board-route-inventory",
        "schema_version": 1,
        "captured_at_utc": "2020-01-01T12:00:00Z",
        "boards": [
            {
                "logical_device": logical_id,
                "spi_route": list(routes[logical_id]),
                "hardware_serial": serial,
                "physical_label": f"board-{int(serial[-2:], 16)}",
            }
            for logical_id, serial in enumerate(serials)
        ],
    }


def _args(**overrides):
    result = {
        "plan": "phase",
        "target_fps": 120,
        "transfers": 1,
        "warmup_transfers": 1,
        "repeats": 2,
        "output": None,
        "expected_script_sha256": "a" * 64,
        "pre_board_inventory": Path("before.json"),
        "post_board_inventory": Path("after.json"),
        "source_identity": {},
        "physical_board_inventory": {},
        "run_identity": {},
    }
    result.update(overrides)
    return SimpleNamespace(**result)


class ReceiverPhaseLaneIsolationTests(unittest.TestCase):
    def test_repository_root_can_be_pinned_for_staged_target_execution(self):
        expected = Path("/opt/ledgrid/release").resolve()
        with mock.patch.dict(
            "os.environ", {"LEDGRID_REPOSITORY_ROOT": str(expected)}
        ):
            namespace = {"__file__": "/tmp/diagnostics/runner.py"}
            prefix = Path(
                "tools/diagnostics/receiver_phase_lane_isolation.py"
            ).read_text(encoding="utf-8").split("from drivers.led_layout", 1)[0]
            exec(prefix, namespace)
        self.assertEqual(namespace["REPOSITORY_ROOT"], expected)

    def test_phase_plan_changes_exactly_one_receiver_and_brackets_every_arm(self):
        baseline = production_state()
        experiments = phase_experiments()
        self.assertEqual(len(experiments), 5)
        for receiver_id, state in enumerate(experiments):
            self.assertEqual(state.lane_masks, baseline.lane_masks)
            self.assertEqual(
                [index for index, value in enumerate(state.phases) if value != 3],
                [receiver_id],
            )
            self.assertEqual(state.phases[receiver_id], 1)

        plan = build_arm_plan("phase")
        self.assertEqual(len(plan), 15)
        for offset in range(0, len(plan), 3):
            self.assertEqual(
                [arm.role for arm in plan[offset : offset + 3]],
                ["A-pre", "B", "A-post"],
            )
            self.assertEqual(plan[offset].state, baseline)
            self.assertEqual(plan[offset + 2].state, baseline)

    def test_lane_plan_has_none_each_lane_each_phase_group_and_all(self):
        self.assertEqual(three_phase_group_masks(), (0x49, 0x92, 0x24))
        states = receiver3_lane_experiments()
        labels = [state.label for state in states]
        self.assertEqual(len(states), 13)
        self.assertIn("receiver-3-mask-none", labels)
        self.assertIn("receiver-3-mask-lane-7", labels)
        self.assertIn("receiver-3-mask-phase-group-2", labels)
        self.assertIn("receiver-3-mask-all", labels)
        baseline = production_state()
        for state in states:
            self.assertEqual(state.phases, baseline.phases)
            self.assertEqual(state.lane_masks[:3], baseline.lane_masks[:3])
            self.assertEqual(state.lane_masks[4:], baseline.lane_masks[4:])

    def test_snapshot_validation_requires_current_and_sticky_v7_fec_baseline(self):
        state = production_state()
        validate_snapshot(_snapshot(3), 3, state)
        for changes, message in (
            ({"receiver_status_version": 6}, "current status v7"),
            ({"receiver_status_max_version_seen": 6}, "sticky status-v7"),
            ({"receiver_fec_terminal_baseline_established": False}, "baseline"),
            ({"receiver_fec_terminal_baseline_invalid": True}, "invalid"),
            ({RESET_COUNTER_FIELD: 1}, "reset"),
            ({"fec_transport_enabled": False}, "not enabled"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                DispatchDiagnosticError, message
            ):
                validate_snapshot(_snapshot(3, **changes), 3, state)

    def test_deltas_fail_closed_on_counter_reset(self):
        before = [_snapshot(index) for index in range(5)]
        after = [_snapshot(index) for index in range(5)]
        after[3]["receiver_crc_errors"] = 2
        self.assertEqual(
            strict_counter_deltas(before, after)[3]["receiver_crc_errors"], 2
        )
        after[3][RESET_COUNTER_FIELD] = 1
        with self.assertRaisesRegex(DispatchDiagnosticError, "reset"):
            strict_counter_deltas(before, after)

    def test_comparisons_require_complete_triplets_and_retain_fault_vectors(self):
        receiver_rows = []
        for logical_id in range(5):
            row = {"logical_device": logical_id}
            from tools.diagnostics.receiver_phase_lane_isolation import FAULT_FIELDS

            row.update({field: logical_id for field in FAULT_FIELDS})
            receiver_rows.append(row)
        arms = [
            {
                "pair_id": "00-test",
                "role": role,
                "state": {"label": role},
                "receivers": receiver_rows,
            }
            for role in ("A-pre", "B", "A-post")
        ]
        comparisons = build_ab_comparisons(arms)
        self.assertEqual(comparisons[0]["pair_id"], "00-test")
        self.assertEqual(
            comparisons[0]["fault_deltas_by_role"]["B"]["receiver_crc_errors"],
            [0, 1, 2, 3, 4],
        )
        with self.assertRaisesRegex(DispatchDiagnosticError, "incomplete"):
            build_ab_comparisons(arms[:2])

    def test_fresh_status_drain_retries_legacy_reply_until_exact_v7(self):
        controller = mock.Mock()
        snapshots = [
            [_snapshot(index, receiver_status_version=3) for index in range(5)],
            [_snapshot(index) for index in range(5)],
        ]
        with mock.patch(
            "tools.diagnostics.receiver_phase_lane_isolation._fresh_snapshots",
            side_effect=snapshots,
        ), mock.patch(
            "tools.diagnostics.receiver_phase_lane_isolation.time.sleep"
        ):
            result = _fresh_validated_snapshots(controller, production_state())
        self.assertEqual(result, snapshots[-1])

    def test_progress_report_is_pure_atomic_json_and_not_complete(self):
        args = _args(plan="lane", transfers=2000, warmup_transfers=512)
        report = _report(args, [], complete=False)
        self.assertFalse(report["complete"])
        self.assertNotIn("comparisons", report)
        with TemporaryDirectory() as directory:
            output = Path(directory) / "progress.json"
            _write_report(output, report)
            self.assertEqual(__import__("json").loads(output.read_text()), report)

    def test_controller_topology_logs_are_redirected_away_from_stdout(self):
        class FakeController:
            def __init__(self, **_kwargs):
                print("topology log")

        stdout = io.StringIO()
        stderr = io.StringIO()
        fake_module = ModuleType("drivers.multi_device")
        fake_module.MultiDeviceLEDController = FakeController
        with mock.patch.dict(
            sys.modules, {"drivers.multi_device": fake_module}
        ), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            _controller()
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("topology log", stderr.getvalue())

    def test_cleanup_restarts_service_even_when_controller_close_fails(self):
        events = []

        class FakeController:
            device_map = list(__import__(
                "drivers.led_layout", fromlist=["WALL_DEVICE_MAP"]
            ).WALL_DEVICE_MAP)

            def set_brightness(self, _value):
                events.append("brightness")

            def clear(self):
                events.append("clear")

            def close(self):
                events.append("close")
                raise RuntimeError("close failed")

        args = _args()
        with mock.patch.multiple(
            "tools.diagnostics.receiver_phase_lane_isolation",
            _preflight_source_and_topology=mock.Mock(return_value={}),
            _bind_board_inventories=mock.Mock(return_value={}),
            _service_active=mock.Mock(return_value=True),
            _wait_for_safe_idle=mock.Mock(),
            _post_service_production_observation=mock.Mock(),
            _service=mock.Mock(side_effect=lambda action: events.append(action)),
            _controller=mock.Mock(return_value=FakeController()),
            _apply_output_state=mock.Mock(),
            _send_black_transfers=mock.Mock(return_value=0.1),
            require_exact_transfer_deltas=mock.Mock(return_value=[]),
            _fresh_validated_snapshots=mock.Mock(
                return_value=[_snapshot(index) for index in range(5)]
            ),
            build_arm_plan=mock.Mock(return_value=()),
        ), self.assertRaisesRegex(DispatchDiagnosticError, "cleanup failed"):
            run(args)
        self.assertEqual(events[-2:], ["close", "start"])

    def test_cleanup_failure_rewrites_retained_report_as_incomplete(self):
        class FakeController:
            device_map = list(__import__(
                "drivers.led_layout", fromlist=["WALL_DEVICE_MAP"]
            ).WALL_DEVICE_MAP)

            def set_brightness(self, _value):
                pass

            def clear(self):
                pass

            def close(self):
                raise RuntimeError("close failed")

        with TemporaryDirectory() as directory:
            output = Path(directory) / "evidence.json"
            args = _args(output=output)
            with mock.patch.multiple(
                "tools.diagnostics.receiver_phase_lane_isolation",
                _preflight_source_and_topology=mock.Mock(return_value={}),
                _bind_board_inventories=mock.Mock(return_value={}),
                _service_active=mock.Mock(return_value=True),
                _wait_for_safe_idle=mock.Mock(),
                _post_service_production_observation=mock.Mock(),
                _service=mock.Mock(),
                _controller=mock.Mock(return_value=FakeController()),
                _apply_output_state=mock.Mock(),
                _send_black_transfers=mock.Mock(return_value=0.1),
                require_exact_transfer_deltas=mock.Mock(return_value=[]),
                _fresh_validated_snapshots=mock.Mock(
                    return_value=[_snapshot(index) for index in range(5)]
                ),
                build_arm_plan=mock.Mock(return_value=()),
            ), self.assertRaisesRegex(DispatchDiagnosticError, "cleanup failed"):
                run(args)
            retained = json.loads(output.read_text(encoding="utf-8"))
        self.assertFalse(retained["complete"])
        self.assertEqual(retained["run_identity"]["cleanup_status"], "failed")

    def test_cleanup_operations_are_independent_and_post_service_is_authoritative(self):
        events = []

        class FakeController:
            device_map = list(__import__(
                "drivers.led_layout", fromlist=["WALL_DEVICE_MAP"]
            ).WALL_DEVICE_MAP)

            def set_brightness(self, _value):
                events.append("brightness")
                if events.count("brightness") > 1:
                    raise RuntimeError("brightness cleanup failed")

            def clear(self):
                events.append("clear")

            def close(self):
                events.append("close")

        apply_calls = 0

        def apply_state(_controller, _state):
            nonlocal apply_calls
            apply_calls += 1
            events.append("apply")

        args = _args()
        with mock.patch.multiple(
            "tools.diagnostics.receiver_phase_lane_isolation",
            _preflight_source_and_topology=mock.Mock(return_value={}),
            _bind_board_inventories=mock.Mock(return_value={}),
            _service_active=mock.Mock(return_value=True),
            _wait_for_safe_idle=mock.Mock(),
            _post_service_production_observation=mock.Mock(
                side_effect=lambda: events.append("post-observe")
            ),
            _service=mock.Mock(side_effect=lambda action: events.append(action)),
            _controller=mock.Mock(return_value=FakeController()),
            _apply_output_state=mock.Mock(side_effect=apply_state),
            _send_black_transfers=mock.Mock(return_value=0.1),
            require_exact_transfer_deltas=mock.Mock(return_value=[]),
            _fresh_validated_snapshots=mock.Mock(
                return_value=[_snapshot(index) for index in range(5)]
            ),
            build_arm_plan=mock.Mock(return_value=()),
        ), self.assertRaisesRegex(DispatchDiagnosticError, "cleanup failed"):
            run(args)
        self.assertGreaterEqual(apply_calls, 2)
        self.assertIn("clear", events)
        self.assertIn("close", events)
        self.assertLess(events.index("start"), events.index("post-observe"))

    def test_warmup_requires_exact_transfer_deltas(self):
        before = [_snapshot(index) for index in range(5)]
        after = [deepcopy(item) for item in before]
        for item in after:
            item["frames_sent"] = 7
            item["full_frame_transfers"] = 7
        self.assertEqual(
            len(require_exact_transfer_deltas(before, after, transfers=7)), 5
        )
        after[4]["frames_sent"] = 6
        with self.assertRaisesRegex(DispatchDiagnosticError, "expected 7"):
            require_exact_transfer_deltas(before, after, transfers=7)

    def test_board_inventory_binds_duplicate_free_physical_swap(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            before_path = root / "before.json"
            after_path = root / "after.json"
            before_path.write_text(json.dumps(_board_inventory()), encoding="utf-8")
            after_path.write_text(
                json.dumps(_board_inventory(swap=True)), encoding="utf-8"
            )
            binding = _bind_board_inventories(
                before_path, after_path, require_swap=True
            )
            self.assertEqual(len(binding["moved_boards"]), 2)
            with self.assertRaisesRegex(
                DispatchDiagnosticError, "unchanged physical-board mapping"
            ):
                _bind_board_inventories(
                    before_path, after_path, require_swap=False
                )
            duplicate = _board_inventory()
            duplicate["boards"][1]["hardware_serial"] = duplicate["boards"][0][
                "hardware_serial"
            ]
            before_path.write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(DispatchDiagnosticError, "duplicate"):
                _load_board_inventory(before_path, label="pre-swap")

    def test_targeted_swap_plan_is_counterbalanced_and_classifies_conservatively(self):
        with self.assertRaisesRegex(ValueError, "even value"):
            build_arm_plan("swap", repeats=3)
        plans = build_arm_plan("swap", repeats=2)
        self.assertEqual(
            [arm.condition for arm in plans[:4]], ["A", "B", "B", "A"]
        )
        self.assertEqual(
            [arm.condition for arm in plans[4:]], ["B", "A", "A", "B"]
        )
        arms = []
        for index, plan in enumerate(plans):
            receivers = []
            for logical_id in range(5):
                row = {"logical_device": logical_id, "full_frame_transfers": 100}
                row["receiver_crc_errors"] = (
                    20 if logical_id == 3 and plan.condition == "B" else 1
                )
                receivers.append(row)
            arms.append(
                {
                    "block_id": plan.block_id,
                    "condition": plan.condition,
                    "receivers": receivers,
                }
            )
        result = classify_targeted_swap_arms(arms)
        self.assertEqual(
            result["classification"], "experiment_increases_receiver_3_faults"
        )
        arms[4]["receivers"][3]["receiver_crc_errors"] = 0
        arms[7]["receivers"][3]["receiver_crc_errors"] = 0
        self.assertEqual(
            classify_targeted_swap_arms(arms)["classification"], "inconclusive"
        )

    def test_reviewed_script_digest_must_match_before_live_preflight(self):
        actual = hashlib.sha256(
            Path("tools/diagnostics/receiver_phase_lane_isolation.py").read_bytes()
        ).hexdigest()
        self.assertRegex(actual, r"^[0-9a-f]{64}$")
        with self.assertRaisesRegex(DispatchDiagnosticError, "reviewed SHA-256"):
            _preflight_source_and_topology("0" * 64)

    def test_post_service_observation_requires_exact_production_topology(self):
        devices = [_snapshot(index) for index in range(5)]
        telemetry = {
            "schema": "ledgrid.composer-operations-telemetry",
            "schema_version": 1,
            "diagnostics": {"driver_stats": {
                "aggregate": {
                    "receiver_status_refresh": {
                        "request_id": "fresh-1",
                        "passed": True,
                        "errors": [],
                    }
                },
                "devices": devices,
            }},
        }

        def response(url, **_kwargs):
            if url.endswith("/refresh"):
                return {"accepted": True, "request_id": "fresh-1"}
            return telemetry

        with mock.patch(
            "tools.diagnostics.receiver_phase_lane_isolation._json_request",
            side_effect=response,
        ):
            self.assertEqual(_post_service_production_observation(), devices)
        devices[3]["receiver_stagger_phases"] = 1
        with mock.patch(
            "tools.diagnostics.receiver_phase_lane_isolation._json_request",
            side_effect=response,
        ), mock.patch(
            "tools.diagnostics.receiver_phase_lane_isolation.time.sleep"
        ), mock.patch(
            "tools.diagnostics.receiver_phase_lane_isolation.time.monotonic",
            side_effect=[0.0, 0.0, 6.0],
        ), self.assertRaisesRegex(DispatchDiagnosticError, "production receiver state"):
            _post_service_production_observation(timeout=5.0)


if __name__ == "__main__":
    unittest.main()
