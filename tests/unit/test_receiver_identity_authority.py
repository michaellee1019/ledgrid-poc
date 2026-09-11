"""Fail-closed tests for target-owned receiver identity authority."""

from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from drivers.led_layout import WALL_DEVICE_MAP
from tools.deployment.receiver_firmware_inventory import (
    ReceiverUSBDevice,
    inventory_path,
    write_firmware_inventory,
)
from tools.deployment.receiver_hybrid_config import write_receiver_hybrid_config
from tools.deployment.receiver_identity_authority import (
    RECEIVER_IDENTITY_AUTHORITY_SCHEMA,
    RECEIVER_IDENTITY_MAPPING_SCHEMA,
    RECEIVER_IDENTITY_EVIDENCE_SCHEMA,
    ReceiverIdentityAuthorityError,
    load_receiver_identity_authority,
    main as receiver_identity_authority_main,
    preflight_receiver_identity_refresh,
    provision_receiver_identity_authority,
    provision_receiver_identity_mapping,
    receiver_identity_authority_path,
    receiver_identity_mapping_path,
    refresh_receiver_identity_authority,
)


def _digest(character: str) -> str:
    return character * 64


def _serial(index: int) -> str:
    # Locally administered test MACs, never installed receiver identities.
    return f"02:aa:bb:cc:dd:{index:02x}"


def _devices() -> tuple[ReceiverUSBDevice, ...]:
    return tuple(
        ReceiverUSBDevice(
            port=f"/dev/ttyACM{logical_id}",
            hardware_serial=_serial(logical_id),
            physical_location=f"test-{logical_id}",
        )
        for logical_id in range(5)
    )


def _evidence(*, serials: tuple[str, ...] | None = None) -> dict[str, object]:
    selected = serials or tuple(_serial(index) for index in range(5))
    return {
        "schema": RECEIVER_IDENTITY_EVIDENCE_SCHEMA,
        "schema_version": 1,
        "identities": [
            {
                "logical_device": logical_id,
                "spi_route": list(WALL_DEVICE_MAP[logical_id]),
                "hardware_serial": selected[logical_id],
                "firmware_sha256": _digest(chr(ord("a") + logical_id)),
            }
            for logical_id in range(5)
        ],
    }


def _mapping(*, serials: tuple[str, ...] | None = None) -> dict[str, object]:
    selected = serials or tuple(_serial(index) for index in range(5))
    return {
        "schema": RECEIVER_IDENTITY_MAPPING_SCHEMA,
        "schema_version": 1,
        "identities": [
            {
                "logical_device": logical_id,
                "spi_route": list(WALL_DEVICE_MAP[logical_id]),
                "hardware_serial": selected[logical_id],
            }
            for logical_id in range(5)
        ],
    }


class ReceiverIdentityAuthorityTests(unittest.TestCase):
    def prepare_target(self, root: Path) -> dict[str, object]:
        write_receiver_hybrid_config(root, enabled=True)
        write_firmware_inventory(
            root,
            _devices(),
            installation_digest=_digest("f"),
            firmware_environment="synthetic-test-environment",
            firmware_sha256=_digest("a"),
        )
        # Deliberately make firmware evidence different for every synthetic board.
        inventory = inventory_path(root)
        payload = json.loads(inventory.read_text(encoding="utf-8"))
        for logical_id, record in enumerate(payload["devices"]):
            record["firmware_sha256"] = _digest(chr(ord("a") + logical_id))
        inventory.write_text(json.dumps(payload), encoding="utf-8")
        return _evidence()

    def test_explicit_evidence_publishes_immutable_canonical_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            authority = provision_receiver_identity_authority(
                root, operator_evidence=self.prepare_target(root)
            )

            self.assertEqual(
                [identity.logical_device for identity in authority.identities],
                [0, 1, 2, 3, 4],
            )
            self.assertEqual(
                [identity.spi_route for identity in authority.identities],
                list(WALL_DEVICE_MAP),
            )
            self.assertEqual(
                receiver_identity_authority_path(root).stat().st_mode & 0o777,
                0o600,
            )
            self.assertEqual(load_receiver_identity_authority(root), authority)
            self.assertEqual(
                json.loads(receiver_identity_authority_path(root).read_text())["schema"],
                RECEIVER_IDENTITY_AUTHORITY_SCHEMA,
            )
            with self.assertRaises(TypeError):
                authority.identities[0] = authority.identities[1]  # type: ignore[index]
            with self.assertRaises(ReceiverIdentityAuthorityError):
                provision_receiver_identity_authority(
                    root, operator_evidence=self.prepare_target(root)
                )

    def test_provisioning_never_infers_route_or_serial_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            evidence = self.prepare_target(root)
            identities = evidence["identities"]
            assert isinstance(identities, list)
            identities[2], identities[3] = identities[3], identities[2]
            with self.assertRaisesRegex(ReceiverIdentityAuthorityError, "logical-device order"):
                provision_receiver_identity_authority(root, operator_evidence=evidence)

            evidence = self.prepare_target(root)
            identities = evidence["identities"]
            assert isinstance(identities, list)
            identities[0]["hardware_serial"] = _serial(4)  # type: ignore[index]
            with self.assertRaisesRegex(ReceiverIdentityAuthorityError, "duplicate hardware serials"):
                provision_receiver_identity_authority(root, operator_evidence=evidence)

    def test_load_rejects_all_stale_or_extra_source_state(self) -> None:
        cases = (
            ("reordered inventory", self._reorder_inventory, "inventory digest is stale"),
            ("extra inventory", self._add_inventory, "exact receiver roster"),
            ("firmware mismatch", self._change_firmware, "inventory digest is stale"),
            ("topology change", self._change_topology, "topology digest is stale"),
        )
        for name, mutate, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                provision_receiver_identity_authority(
                    root, operator_evidence=self.prepare_target(root)
                )
                mutate(root)
                with self.assertRaisesRegex(ReceiverIdentityAuthorityError, expected):
                    load_receiver_identity_authority(root)

    @staticmethod
    def _reorder_inventory(root: Path) -> None:
        path = inventory_path(root)
        payload = json.loads(path.read_text())
        payload["devices"].reverse()
        path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _add_inventory(root: Path) -> None:
        path = inventory_path(root)
        payload = json.loads(path.read_text())
        payload["devices"].append({
            "hardware_serial": _serial(99),
            "installation_digest": _digest("f"),
            "firmware_environment": "synthetic-test-environment",
            "firmware_sha256": _digest("e"),
        })
        path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _change_firmware(root: Path) -> None:
        path = inventory_path(root)
        payload = json.loads(path.read_text())
        payload["devices"][0]["firmware_sha256"] = _digest("f")
        path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def _change_topology(root: Path) -> None:
        write_receiver_hybrid_config(root, enabled=False)

    def test_load_rejects_schema_digest_and_authority_record_tampering(self) -> None:
        mutations = (
            ("unknown field", lambda payload: payload.update(unexpected=True), "keys are not exact"),
            ("digest", lambda payload: payload.update(authority_digest=_digest("0")), "digest mismatches"),
            (
                "route",
                lambda payload: payload["identities"][2].update(spi_route=[1, 0]),
                "configured topology",
            ),
        )
        for name, mutate, expected in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary_dir:
                root = Path(temporary_dir)
                provision_receiver_identity_authority(
                    root, operator_evidence=self.prepare_target(root)
                )
                path = receiver_identity_authority_path(root)
                payload = json.loads(path.read_text())
                mutate(payload)
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ReceiverIdentityAuthorityError, expected):
                    load_receiver_identity_authority(root)

    def test_missing_sources_and_nonregular_authority_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            with self.assertRaisesRegex(ReceiverIdentityAuthorityError, "authority is missing"):
                load_receiver_identity_authority(root)

            provision_receiver_identity_authority(
                root, operator_evidence=self.prepare_target(root)
            )
            path = receiver_identity_authority_path(root)
            path.unlink()
            path.symlink_to(root / "missing-target")
            with self.assertRaisesRegex(ReceiverIdentityAuthorityError, "non-symlink"):
                load_receiver_identity_authority(root)

    def test_firmware_change_requires_mapping_then_rotates_from_verified_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            old = provision_receiver_identity_authority(
                root, operator_evidence=self.prepare_target(root)
            )
            self._change_firmware(root)
            expected_firmware = _digest("f")
            inventory_payload = json.loads(inventory_path(root).read_text())
            for record in inventory_payload["devices"]:
                record["firmware_sha256"] = expected_firmware
            inventory_path(root).write_text(json.dumps(inventory_payload))

            with self.assertRaisesRegex(
                ReceiverIdentityAuthorityError, "identity mapping is missing"
            ):
                preflight_receiver_identity_refresh(
                    root,
                    expected_receiver_hybrid_digest=(
                        write_receiver_hybrid_config(root, enabled=True).selection_digest
                    ),
                    expected_firmware_sha256=expected_firmware,
                )
            self.assertEqual(
                json.loads(receiver_identity_authority_path(root).read_text())["authority_digest"],
                old.authority_digest,
            )

            provision_receiver_identity_mapping(root, operator_mapping=_mapping())
            self.assertEqual(
                receiver_identity_mapping_path(root).stat().st_mode & 0o777,
                0o600,
            )
            config_digest = write_receiver_hybrid_config(root, enabled=True).selection_digest
            preflight = preflight_receiver_identity_refresh(
                root,
                expected_receiver_hybrid_digest=config_digest,
                expected_firmware_sha256=expected_firmware,
            )
            self.assertTrue(preflight["rotation_required"])
            refreshed = refresh_receiver_identity_authority(
                root,
                expected_authority_digest=old.authority_digest,
                expected_receiver_hybrid_digest=config_digest,
                expected_firmware_sha256=expected_firmware,
            )

            self.assertTrue(refreshed["rotated"])
            current = load_receiver_identity_authority(root)
            self.assertNotEqual(current.authority_digest, old.authority_digest)
            self.assertEqual(
                {identity.firmware_sha256 for identity in current.identities},
                {expected_firmware},
            )
            archived = root / str(refreshed["archived_authority"])
            self.assertEqual(
                json.loads(archived.read_text())["authority_digest"],
                old.authority_digest,
            )

    def test_config_only_change_rotates_without_deriving_mapping_from_old_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            old = provision_receiver_identity_authority(
                root, operator_evidence=self.prepare_target(root)
            )
            provision_receiver_identity_mapping(root, operator_mapping=_mapping())
            config_digest = write_receiver_hybrid_config(
                root, enabled=True, native_modules_enabled=True
            ).selection_digest
            preflight = preflight_receiver_identity_refresh(
                root,
                expected_receiver_hybrid_digest=config_digest,
                expected_firmware_sha256=None,
            )
            refreshed = refresh_receiver_identity_authority(
                root,
                expected_authority_digest=preflight["current_authority_digest"],
                expected_receiver_hybrid_digest=config_digest,
                expected_firmware_sha256=None,
            )

            self.assertTrue(refreshed["rotated"])
            self.assertNotEqual(
                load_receiver_identity_authority(root).authority_digest,
                old.authority_digest,
            )

    def test_invalid_mapping_and_failed_cas_leave_authority_unmodified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            old = provision_receiver_identity_authority(
                root, operator_evidence=self.prepare_target(root)
            )
            invalid = _mapping()
            invalid["identities"][1]["hardware_serial"] = _serial(0)  # type: ignore[index]
            with self.assertRaisesRegex(
                ReceiverIdentityAuthorityError, "duplicate hardware serials"
            ):
                provision_receiver_identity_mapping(root, operator_mapping=invalid)
            self.assertFalse(receiver_identity_mapping_path(root).exists())
            self.assertEqual(load_receiver_identity_authority(root), old)

            provision_receiver_identity_mapping(root, operator_mapping=_mapping())
            config_digest = write_receiver_hybrid_config(
                root, enabled=True, native_modules_enabled=True
            ).selection_digest
            with self.assertRaisesRegex(
                ReceiverIdentityAuthorityError,
                "inventory does not match planned firmware",
            ):
                refresh_receiver_identity_authority(
                    root,
                    expected_authority_digest=old.authority_digest,
                    expected_receiver_hybrid_digest=config_digest,
                    expected_firmware_sha256=_digest("f"),
                )
            self.assertEqual(
                json.loads(receiver_identity_authority_path(root).read_text())["authority_digest"],
                old.authority_digest,
            )
            self.assertFalse(
                (root / "run_state/receiver_identity_authority_archive").exists()
            )

            with self.assertRaisesRegex(
                ReceiverIdentityAuthorityError, "changed after refresh preflight"
            ):
                refresh_receiver_identity_authority(
                    root,
                    expected_authority_digest=_digest("0"),
                    expected_receiver_hybrid_digest=config_digest,
                    expected_firmware_sha256=None,
                )
            self.assertEqual(
                json.loads(receiver_identity_authority_path(root).read_text())["authority_digest"],
                old.authority_digest,
            )
            self.assertFalse(
                (root / "run_state/receiver_identity_authority_archive").exists()
            )

    def test_unchanged_valid_authority_needs_no_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            config = write_receiver_hybrid_config(root, enabled=True)
            write_firmware_inventory(
                root,
                _devices(),
                installation_digest=_digest("f"),
                firmware_environment="synthetic-test-environment",
                firmware_sha256=_digest("a"),
            )
            evidence = _evidence()
            for identity in evidence["identities"]:  # type: ignore[union-attr]
                identity["firmware_sha256"] = _digest("a")
            authority = provision_receiver_identity_authority(
                root, operator_evidence=evidence
            )

            preflight = preflight_receiver_identity_refresh(
                root,
                expected_receiver_hybrid_digest=config.selection_digest,
                expected_firmware_sha256=_digest("a"),
            )
            self.assertFalse(preflight["rotation_required"])
            self.assertFalse(receiver_identity_mapping_path(root).exists())
            refreshed = refresh_receiver_identity_authority(
                root,
                expected_authority_digest=authority.authority_digest,
                expected_receiver_hybrid_digest=config.selection_digest,
                expected_firmware_sha256=_digest("a"),
            )
            self.assertEqual(refreshed["outcome"], "skipped")
            self.assertEqual(load_receiver_identity_authority(root), authority)

    def test_provision_mapping_cli_writes_the_strict_target_owned_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            self.prepare_target(root)
            evidence_path = root / "operator-mapping.json"
            evidence_path.write_text(json.dumps(_mapping()), encoding="utf-8")
            output = StringIO()

            with mock.patch(
                "sys.argv",
                [
                    "receiver_identity_authority",
                    "--root",
                    str(root),
                    "provision-mapping",
                    "--evidence",
                    str(evidence_path),
                ],
            ), redirect_stdout(output):
                self.assertEqual(receiver_identity_authority_main(), 0)

            payload = json.loads(output.getvalue())
            self.assertEqual(payload["schema"], RECEIVER_IDENTITY_MAPPING_SCHEMA)
            self.assertEqual(len(payload["identities"]), 5)
            self.assertEqual(
                json.loads(receiver_identity_mapping_path(root).read_text()),
                payload,
            )


if __name__ == "__main__":
    unittest.main()
