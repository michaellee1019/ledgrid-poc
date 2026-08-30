"""Fail-closed tests for target-owned receiver identity authority."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from drivers.led_layout import WALL_DEVICE_MAP
from tools.deployment.receiver_firmware_inventory import (
    ReceiverUSBDevice,
    inventory_path,
    write_firmware_inventory,
)
from tools.deployment.receiver_hybrid_config import write_receiver_hybrid_config
from tools.deployment.receiver_identity_authority import (
    RECEIVER_IDENTITY_AUTHORITY_SCHEMA,
    RECEIVER_IDENTITY_EVIDENCE_SCHEMA,
    ReceiverIdentityAuthorityError,
    load_receiver_identity_authority,
    provision_receiver_identity_authority,
    receiver_identity_authority_path,
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


if __name__ == "__main__":
    unittest.main()
