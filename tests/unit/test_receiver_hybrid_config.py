import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools.deployment.receiver_hybrid_config import (
    DEFAULT_PHYSICAL_LANE_ORDER,
    DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS,
    DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS,
    DEFAULT_RECEIVER_STRIP_COUNTS,
    DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER,
    DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER,
    DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY,
    NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
    PRODUCTION_FIRMWARE_ENVIRONMENT,
    RECEIVER_HYBRID_CONFIG_RELATIVE_PATH,
    RECEIVER_HYBRID_CONFIG_SCHEMA,
    RECEIVER_HYBRID_CONFIG_VERSION,
    RECEIVER_HYBRID_TRANSPORT_OFF,
    STRICT_RECEIVER_HYBRID_TRANSPORT_POLICY,
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
            "enabled": False,
            "transport_policy": RECEIVER_HYBRID_TRANSPORT_OFF,
            "native_modules_enabled": False,
            "physical_lane_order": list(DEFAULT_PHYSICAL_LANE_ORDER),
            "reverse_strips_by_logical_receiver": list(
                DEFAULT_REVERSE_STRIPS_BY_LOGICAL_RECEIVER
            ),
            "reverse_native_strips_by_logical_receiver": list(
                DEFAULT_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER
            ),
            "receiver_strip_counts": list(DEFAULT_RECEIVER_STRIP_COUNTS),
            "receiver_global_strip_offsets": list(
                DEFAULT_RECEIVER_GLOBAL_STRIP_OFFSETS
            ),
            "physical_output_lane_masks": list(
                DEFAULT_PHYSICAL_OUTPUT_LANE_MASKS
            ),
        }
        payload.update(overrides)
        return payload

    @staticmethod
    def write(root: Path, payload) -> Path:
        path = root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_absence_is_feature_off_with_finalized_topology(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            config = resolve_receiver_hybrid_config(Path(temporary_dir))
        self.assertFalse(config.enabled)
        self.assertFalse(config.native_modules_enabled)
        self.assertEqual(config.transport_policy, RECEIVER_HYBRID_TRANSPORT_OFF)
        self.assertEqual(config.firmware_environment, PRODUCTION_FIRMWARE_ENVIRONMENT)
        self.assertEqual(config.receiver_strip_counts, (8, 8, 8, 8, 1))
        self.assertEqual(config.physical_lane_order, (0, 1, 2, 3, 4))
        self.assertEqual(
            config.receiver_global_strip_offsets, (0, 8, 16, 24, 32)
        )
        self.assertEqual(
            config.physical_output_lane_masks, (255, 255, 255, 255, 255)
        )
        self.assertEqual(config.strip_count, 33)
        self.assertRegex(config.selection_digest, r"^[0-9a-f]{64}$")

    def test_writer_selects_local_and_native_environments(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            local = write_receiver_hybrid_config(root, enabled=True)
            self.assertEqual(
                local.transport_policy, STRICT_RECEIVER_HYBRID_TRANSPORT_POLICY
            )
            self.assertFalse(local.native_modules_enabled)

            native = write_receiver_hybrid_config(
                root, enabled=True, native_modules_enabled=True
            )
            self.assertTrue(native.native_modules_enabled)
            self.assertEqual(
                native.firmware_environment,
                NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
            )
            self.assertNotEqual(local.selection_digest, native.selection_digest)

            disabled = write_receiver_hybrid_config(root, enabled=False)
            self.assertEqual(disabled.firmware_environment, PRODUCTION_FIRMWARE_ENVIRONMENT)
            self.assertEqual(
                (root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH).stat().st_mode & 0o777,
                0o600,
            )

    def test_native_cannot_be_enabled_without_hybrid_mode(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            with self.assertRaisesRegex(ReceiverHybridConfigError, "require"):
                write_receiver_hybrid_config(
                    Path(temporary_dir),
                    enabled=False,
                    native_modules_enabled=True,
                )

    def test_topology_requires_exact_nonoverlapping_33_strip_roster(self):
        cases = (
            ({"physical_lane_order": [0, 1, 2, 3]}, "exactly 5"),
            ({"physical_lane_order": [0, 1, 2, 3, 3]}, "permutation"),
            ({"receiver_strip_counts": [8, 8, 8, 8, 0]}, "positive"),
            ({"receiver_global_strip_offsets": [0, 8, 16, 24, 24]}, "overlap"),
            ({"receiver_global_strip_offsets": [0, 8, 16, 24, 33]}, "cover"),
            ({"physical_output_lane_masks": [255, 255, 255, 255, 0]}, "1..255"),
            ({"physical_output_lane_masks": [1, 255, 255, 255, 1]}, "fewer lanes"),
            ({"physical_output_lane_masks": [255, 255, 255, 255, True]}, "ints"),
        )
        for overrides, error in cases:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.write(root, self.payload(**overrides))
                with self.assertRaisesRegex(ReceiverHybridConfigError, error):
                    resolve_receiver_hybrid_config(root)

    def test_reverse_maps_require_five_real_booleans(self):
        for field in (
            "reverse_strips_by_logical_receiver",
            "reverse_native_strips_by_logical_receiver",
        ):
            for value in ([False] * 4, [False, False, False, False, 1]):
                with self.subTest(field=field, value=value), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    self.write(root, self.payload(**{field: value}))
                    with self.assertRaisesRegex(ReceiverHybridConfigError, field):
                        resolve_receiver_hybrid_config(root)

    def test_present_config_is_exact_and_fails_closed(self):
        cases = (
            ([], "JSON object"),
            ({}, "keys are not exact"),
            (self.payload(extra=True), "keys are not exact"),
            (self.payload(schema="other"), "unsupported.*schema"),
            (self.payload(schema_version=True), "schema version"),
            (self.payload(enabled=1), "enabled must be boolean"),
            (self.payload(native_modules_enabled=1), "must be boolean"),
            (self.payload(transport_policy="strict"), "unsupported transport"),
        )
        for payload, error in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                self.write(root, payload)
                with self.assertRaisesRegex(ReceiverHybridConfigError, error):
                    resolve_receiver_hybrid_config(root)

    def test_retired_schema_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = self.write(
                root,
                {
                    **self.payload(),
                    "schema_version": RECEIVER_HYBRID_CONFIG_VERSION - 1,
                },
            )
            before = path.read_bytes()

            with self.assertRaisesRegex(
                ReceiverHybridConfigError, "current schema v5 is required"
            ):
                resolve_receiver_hybrid_config(root)

            self.assertEqual(path.read_bytes(), before)

    def test_failed_atomic_replace_keeps_prior_complete_config(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            original = write_receiver_hybrid_config(root, enabled=False)
            path = root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH
            before = path.read_bytes()
            with (
                patch(
                    "tools.deployment.receiver_hybrid_config.os.replace",
                    side_effect=OSError("interrupted"),
                ),
                self.assertRaisesRegex(OSError, "interrupted"),
            ):
                write_receiver_hybrid_config(root, enabled=True)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(resolve_receiver_hybrid_config(root), original)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

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
            target = root / "target.json"
            target.write_text(json.dumps(self.payload()), encoding="utf-8")
            path.symlink_to(os.path.relpath(target, start=path.parent))
            with self.assertRaisesRegex(ReceiverHybridConfigError, "non-symlink"):
                resolve_receiver_hybrid_config(root)

    def test_cli_local_native_disable_and_show(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            for action, enabled, native in (
                ("enable-local", True, False),
                ("enable-native", True, True),
                ("show", True, True),
                ("disable", False, False),
            ):
                output = io.StringIO()
                with patch("sys.argv", ["receiver_hybrid_config.py", "--root", str(root), action]), redirect_stdout(output):
                    self.assertEqual(main(), 0)
                payload = json.loads(output.getvalue())
                self.assertIs(payload["enabled"], enabled)
                self.assertIs(payload["native_modules_enabled"], native)
                self.assertRegex(payload["config_digest"], r"^[0-9a-f]{64}$")




if __name__ == "__main__":
    unittest.main()
