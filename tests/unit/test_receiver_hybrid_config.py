import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
import io
from unittest.mock import patch

from tools.deployment.receiver_hybrid_config import (
    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
    DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY,
    PRODUCTION_FIRMWARE_ENVIRONMENT,
    RECEIVER_HYBRID_CONFIG_RELATIVE_PATH,
    RECEIVER_HYBRID_CONFIG_SCHEMA,
    RECEIVER_HYBRID_CONFIG_VERSION,
    RECEIVER_HYBRID_TRANSPORT_OFF,
    ReceiverHybridConfigError,
    main,
    resolve_receiver_hybrid_config,
    write_receiver_hybrid_config,
)


class ReceiverHybridConfigTests(unittest.TestCase):
    @staticmethod
    def payload(**overrides):
        payload = {
            "schema": RECEIVER_HYBRID_CONFIG_SCHEMA,
            "schema_version": RECEIVER_HYBRID_CONFIG_VERSION,
            "enabled": True,
            "transport_policy": DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY,
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def write(root: Path, payload) -> Path:
        path = root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_absent_config_is_exact_feature_off_production_selection(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config = resolve_receiver_hybrid_config(Path(temporary_dir))

        self.assertFalse(config.enabled)
        self.assertEqual(config.transport_policy, RECEIVER_HYBRID_TRANSPORT_OFF)
        self.assertEqual(config.firmware_environment, PRODUCTION_FIRMWARE_ENVIRONMENT)
        self.assertEqual(dict(config), config.to_dict())
        self.assertRegex(config.selection_digest, r"^[0-9a-f]{64}$")

    def test_atomic_writer_selects_only_allowlisted_degraded_environment(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config = write_receiver_hybrid_config(root, enabled=True)
            path = root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH

            self.assertTrue(config.enabled)
            self.assertEqual(
                config.transport_policy,
                DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY,
            )
            self.assertEqual(
                config.firmware_environment,
                DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
            )
            self.assertEqual(resolve_receiver_hybrid_config(root), config)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

            disabled = write_receiver_hybrid_config(root, enabled=False)
            self.assertFalse(disabled.enabled)
            self.assertEqual(
                disabled.firmware_environment, PRODUCTION_FIRMWARE_ENVIRONMENT
            )

    def test_lane_order_round_trips_and_legacy_config_defaults_to_identity(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            legacy_path = self.write(root, self.payload())
            legacy = resolve_receiver_hybrid_config(root)
            self.assertEqual(legacy.physical_lane_order, (0, 1, 2, 3))

            configured = write_receiver_hybrid_config(
                root, enabled=True, physical_lane_order=(0, 1, 3, 2)
            )
            self.assertEqual(configured.physical_lane_order, (0, 1, 3, 2))
            self.assertEqual(
                json.loads(legacy_path.read_text())["physical_lane_order"],
                [0, 1, 3, 2],
            )
            self.assertNotEqual(configured.selection_digest, legacy.selection_digest)

            preserved = write_receiver_hybrid_config(root, enabled=True)
            self.assertEqual(preserved.physical_lane_order, (0, 1, 3, 2))

    def test_failed_atomic_replace_keeps_prior_complete_config(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            original = write_receiver_hybrid_config(root, enabled=False)
            path = root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH
            original_bytes = path.read_bytes()

            with (
                patch(
                    "tools.deployment.receiver_hybrid_config.os.replace",
                    side_effect=OSError("interrupted"),
                ),
                self.assertRaisesRegex(OSError, "interrupted"),
            ):
                write_receiver_hybrid_config(root, enabled=True)

            self.assertEqual(path.read_bytes(), original_bytes)
            self.assertEqual(resolve_receiver_hybrid_config(root), original)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_present_config_is_strict_and_fails_closed(self):
        cases = (
            ([], "JSON object"),
            ({}, "keys are not exact"),
            (self.payload(extra=True), "keys are not exact"),
            (self.payload(schema="other"), "unsupported.*schema"),
            (self.payload(schema_version=2), "schema version"),
            (self.payload(schema_version=True), "schema version"),
            (self.payload(enabled=1), "enabled must be boolean"),
            (self.payload(transport_policy="strict"), "unsupported transport"),
            (
                self.payload(enabled=False),
                "unsupported transport",
            ),
            (
                self.payload(
                    enabled=False,
                    transport_policy=DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY,
                ),
                "unsupported transport",
            ),
        )
        for payload, error in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                self.write(root, payload)
                with self.assertRaisesRegex(ReceiverHybridConfigError, error):
                    resolve_receiver_hybrid_config(root)

    def test_invalid_lane_orders_fail_closed(self):
        for lane_order in (
            [0, 1, 2],
            [0, 1, 2, 2],
            [0, 1, 2, 4],
            [0, 1, 2, True],
            "0,1,2,3",
        ):
            with self.subTest(lane_order=lane_order), tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                self.write(root, self.payload(physical_lane_order=lane_order))
                with self.assertRaisesRegex(
                    ReceiverHybridConfigError, "physical_lane_order"
                ):
                    resolve_receiver_hybrid_config(root)

    def test_malformed_oversized_and_symlink_configs_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = self.write(root, self.payload())
            path.write_text("{", encoding="utf-8")
            with self.assertRaisesRegex(ReceiverHybridConfigError, "cannot read"):
                resolve_receiver_hybrid_config(root)

            path.write_text(" " * 4097, encoding="utf-8")
            with self.assertRaisesRegex(ReceiverHybridConfigError, "large"):
                resolve_receiver_hybrid_config(root)

            path.unlink()
            target = root / "config-target.json"
            target.write_text(json.dumps(self.payload()), encoding="utf-8")
            path.symlink_to(os.path.relpath(target, start=path.parent))
            with self.assertRaisesRegex(ReceiverHybridConfigError, "non-symlink"):
                resolve_receiver_hybrid_config(root)

    def test_writer_rejects_policy_or_type_drift_before_touching_disk(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            with self.assertRaisesRegex(TypeError, "must be boolean"):
                write_receiver_hybrid_config(root, enabled=1)  # type: ignore[arg-type]
            with self.assertRaisesRegex(ReceiverHybridConfigError, "disagree"):
                write_receiver_hybrid_config(
                    root, enabled=True, transport_policy=RECEIVER_HYBRID_TRANSPORT_OFF
                )
            self.assertFalse((root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH).exists())

    def test_cli_enable_show_and_disable_report_exact_selection(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            for action, enabled, environment in (
                (
                    "enable-degraded", True,
                    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
                ),
                (
                    "show", True,
                    DEGRADED_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
                ),
                ("disable", False, PRODUCTION_FIRMWARE_ENVIRONMENT),
            ):
                output = io.StringIO()
                with (
                    patch(
                        "sys.argv",
                        ["receiver_hybrid_config.py", "--root", str(root), action],
                    ),
                    redirect_stdout(output),
                ):
                    self.assertEqual(main(), 0)
                payload = json.loads(output.getvalue())
                self.assertIs(payload["enabled"], enabled)
                self.assertEqual(payload["firmware_environment"], environment)
                self.assertRegex(payload["config_digest"], r"^[0-9a-f]{64}$")

    def test_cli_persists_explicit_left_to_right_lane_order(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            output = io.StringIO()
            with (
                patch(
                    "sys.argv",
                    [
                        "receiver_hybrid_config.py",
                        "--root", str(root),
                        "--physical-lane-order", "0,1,3,2",
                        "enable-degraded",
                    ],
                ),
                redirect_stdout(output),
            ):
                self.assertEqual(main(), 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["physical_lane_order"], [0, 1, 3, 2])
            self.assertEqual(
                resolve_receiver_hybrid_config(root).physical_lane_order,
                (0, 1, 3, 2),
            )


if __name__ == "__main__":
    unittest.main()
