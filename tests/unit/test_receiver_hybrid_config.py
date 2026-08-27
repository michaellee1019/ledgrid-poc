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
    LANE_ZERO_RECEIVER_HYBRID_CONFIG_VERSION,
    NATIVE_RECEIVER_HYBRID_FIRMWARE_ENVIRONMENT,
    PERMUTED_RECEIVER_HYBRID_CONFIG_VERSION,
    PREVIOUS_PHYSICAL_LANE_ORDER,
    PREVIOUS_PHYSICAL_OUTPUT_LANE_MASKS,
    PREVIOUS_RECEIVER_GLOBAL_STRIP_OFFSETS,
    PREVIOUS_RECEIVER_HYBRID_CONFIG_VERSION,
    PREVIOUS_REVERSE_STRIPS_BY_LOGICAL_RECEIVER,
    PRODUCTION_FIRMWARE_ENVIRONMENT,
    RECEIVER_HYBRID_CONFIG_RELATIVE_PATH,
    RECEIVER_HYBRID_CONFIG_SCHEMA,
    RECEIVER_HYBRID_CONFIG_VERSION,
    RECEIVER_HYBRID_TRANSPORT_OFF,
    STRICT_RECEIVER_HYBRID_TRANSPORT_POLICY,
    ReceiverHybridConfigError,
    main,
    migrate_legacy_receiver_hybrid_config,
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
    def legacy_payload(**overrides):
        payload = {
            "schema": RECEIVER_HYBRID_CONFIG_SCHEMA,
            "schema_version": 1,
            "enabled": True,
            "transport_policy": DEGRADED_RECEIVER_HYBRID_TRANSPORT_POLICY,
            "physical_lane_order": [0, 1, 3, 2],
            "reverse_strips_by_logical_receiver": [False, False, True, True],
            "reverse_native_strips_by_logical_receiver": [False, False, True, True],
        }
        payload.update(overrides)
        return payload

    @classmethod
    def previous_payload(cls, **overrides):
        payload = cls.payload(
            schema_version=PREVIOUS_RECEIVER_HYBRID_CONFIG_VERSION,
            reverse_strips_by_logical_receiver=list(
                PREVIOUS_REVERSE_STRIPS_BY_LOGICAL_RECEIVER
            ),
        )
        payload.update(overrides)
        return payload

    @classmethod
    def permuted_payload(cls, **overrides):
        payload = cls.previous_payload(
            schema_version=PERMUTED_RECEIVER_HYBRID_CONFIG_VERSION,
            physical_lane_order=list(PREVIOUS_PHYSICAL_LANE_ORDER),
            receiver_global_strip_offsets=list(
                PREVIOUS_RECEIVER_GLOBAL_STRIP_OFFSETS
            ),
        )
        payload.update(overrides)
        return payload

    @classmethod
    def lane_zero_payload(cls, **overrides):
        payload = cls.permuted_payload(
            schema_version=LANE_ZERO_RECEIVER_HYBRID_CONFIG_VERSION,
            physical_output_lane_masks=list(
                PREVIOUS_PHYSICAL_OUTPUT_LANE_MASKS
            ),
        )
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

    def test_known_legacy_config_migrates_once_to_disabled_current_schema(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = self.write(root, self.legacy_payload())
            bridged = resolve_receiver_hybrid_config(root)
            self.assertFalse(bridged.enabled)
            self.assertEqual(bridged.strip_count, 33)
            config, migrated = migrate_legacy_receiver_hybrid_config(root)
            self.assertTrue(migrated)
            self.assertFalse(config.enabled)
            self.assertEqual(config.strip_count, 33)
            stored = json.loads(path.read_text())
            self.assertEqual(stored["schema_version"], RECEIVER_HYBRID_CONFIG_VERSION)
            self.assertEqual(stored["receiver_strip_counts"], [8, 8, 8, 8, 1])
            self.assertEqual(
                stored["physical_output_lane_masks"],
                [255, 255, 255, 255, 255],
            )
            same, migrated = migrate_legacy_receiver_hybrid_config(root)
            self.assertFalse(migrated)
            self.assertEqual(same, config)

    def test_schema_v2_lane_zero_config_bridges_and_migrates_to_broadcast(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = self.write(root, self.lane_zero_payload())
            bridged = resolve_receiver_hybrid_config(root)
            self.assertFalse(bridged.enabled)
            self.assertEqual(bridged.physical_lane_order, (0, 1, 2, 3, 4))
            self.assertEqual(
                bridged.receiver_global_strip_offsets,
                (0, 8, 16, 24, 32),
            )
            self.assertEqual(
                bridged.physical_output_lane_masks,
                (255, 255, 255, 255, 255),
            )
            self.assertEqual(
                json.loads(path.read_text())["physical_output_lane_masks"],
                [255, 255, 255, 255, 1],
            )

            config, migrated = migrate_legacy_receiver_hybrid_config(root)

            self.assertTrue(migrated)
            self.assertEqual(config, bridged)
            stored = json.loads(path.read_text())
            self.assertEqual(stored["schema_version"], RECEIVER_HYBRID_CONFIG_VERSION)
            self.assertEqual(
                stored["physical_output_lane_masks"],
                [255, 255, 255, 255, 255],
            )

    def test_unknown_schema_v2_config_fails_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = self.write(
                root,
                self.lane_zero_payload(
                    receiver_global_strip_offsets=[0, 8, 16, 24, 32]
                ),
            )
            before = path.read_bytes()
            with self.assertRaisesRegex(ReceiverHybridConfigError, "manual inspection"):
                resolve_receiver_hybrid_config(root)
            with self.assertRaisesRegex(ReceiverHybridConfigError, "manual inspection"):
                migrate_legacy_receiver_hybrid_config(root)
            self.assertEqual(path.read_bytes(), before)

    def test_schema_v4_host_direction_bridges_and_migrates_preserving_selection(self):
        selections = (
            (False, False, RECEIVER_HYBRID_TRANSPORT_OFF),
            (True, False, STRICT_RECEIVER_HYBRID_TRANSPORT_POLICY),
            (True, True, STRICT_RECEIVER_HYBRID_TRANSPORT_POLICY),
        )
        for enabled, native, policy in selections:
            with (
                self.subTest(enabled=enabled, native=native),
                tempfile.TemporaryDirectory() as temporary_dir,
            ):
                root = Path(temporary_dir)
                path = self.write(
                    root,
                    self.previous_payload(
                        enabled=enabled,
                        native_modules_enabled=native,
                        transport_policy=policy,
                    ),
                )

                bridged = resolve_receiver_hybrid_config(root)

                self.assertIs(bridged.enabled, enabled)
                self.assertIs(bridged.native_modules_enabled, native)
                self.assertEqual(bridged.physical_lane_order, (0, 1, 2, 3, 4))
                self.assertEqual(
                    bridged.receiver_global_strip_offsets,
                    (0, 8, 16, 24, 32),
                )
                self.assertEqual(
                    json.loads(path.read_text())["schema_version"],
                    PREVIOUS_RECEIVER_HYBRID_CONFIG_VERSION,
                )

                config, migrated = migrate_legacy_receiver_hybrid_config(root)

                self.assertTrue(migrated)
                self.assertEqual(config, bridged)
                stored = json.loads(path.read_text())
                self.assertEqual(
                    stored["schema_version"], RECEIVER_HYBRID_CONFIG_VERSION
                )
                self.assertEqual(stored["physical_lane_order"], [0, 1, 2, 3, 4])
                self.assertEqual(
                    stored["receiver_global_strip_offsets"],
                    [0, 8, 16, 24, 32],
                )
                self.assertIs(stored["enabled"], enabled)
                self.assertIs(stored["native_modules_enabled"], native)

    def test_schema_v3_permutation_bridges_and_migrates_preserving_selection(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = self.write(root, self.permuted_payload())

            bridged = resolve_receiver_hybrid_config(root)

            self.assertEqual(bridged.physical_lane_order, (0, 1, 2, 3, 4))
            self.assertEqual(
                bridged.reverse_strips_by_logical_receiver,
                (False, False, False, False, False),
            )
            self.assertEqual(
                bridged.reverse_native_strips_by_logical_receiver,
                (False, False, True, True, False),
            )
            self.assertEqual(
                json.loads(path.read_text())["schema_version"],
                PERMUTED_RECEIVER_HYBRID_CONFIG_VERSION,
            )

            config, migrated = migrate_legacy_receiver_hybrid_config(root)

            self.assertTrue(migrated)
            self.assertEqual(config, bridged)
            self.assertEqual(
                json.loads(path.read_text())["schema_version"],
                RECEIVER_HYBRID_CONFIG_VERSION,
            )

    def test_unknown_schema_v3_config_fails_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = self.write(
                root,
                self.permuted_payload(
                    reverse_strips_by_logical_receiver=[False] * 5
                ),
            )
            before = path.read_bytes()
            with self.assertRaisesRegex(ReceiverHybridConfigError, "manual inspection"):
                resolve_receiver_hybrid_config(root)
            with self.assertRaisesRegex(ReceiverHybridConfigError, "manual inspection"):
                migrate_legacy_receiver_hybrid_config(root)
            self.assertEqual(path.read_bytes(), before)

    def test_absent_config_migration_materializes_current_schema(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config, migrated = migrate_legacy_receiver_hybrid_config(root)
            self.assertTrue(migrated)
            self.assertFalse(config.enabled)
            self.assertTrue((root / RECEIVER_HYBRID_CONFIG_RELATIVE_PATH).is_file())

    def test_unknown_legacy_config_fails_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            path = self.write(root, self.legacy_payload(enabled=False))
            before = path.read_bytes()
            with self.assertRaisesRegex(ReceiverHybridConfigError, "manual inspection"):
                resolve_receiver_hybrid_config(root)
            with self.assertRaisesRegex(ReceiverHybridConfigError, "manual inspection"):
                migrate_legacy_receiver_hybrid_config(root)
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

    def test_cli_local_native_disable_show_and_migrate(self):
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

            self.write(root, self.legacy_payload())
            output = io.StringIO()
            with patch("sys.argv", ["receiver_hybrid_config.py", "--root", str(root), "migrate"]), redirect_stdout(output):
                self.assertEqual(main(), 0)
            self.assertTrue(json.loads(output.getvalue())["migrated"])


if __name__ == "__main__":
    unittest.main()
