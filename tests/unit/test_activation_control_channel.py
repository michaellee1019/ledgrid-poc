"""Durability and secrecy contracts for guarded scene activation."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
import uuid

from ipc.control_channel import (
    ACTIVATION_COMMAND_SCHEMA,
    ACTIVATION_STATUS_SCHEMA,
    FileControlChannel,
)
from web.activation_token_store import (
    ActivationTokenConflict,
    ActivationTokenExpired,
    ActivationTokenStore,
    canonical_digest,
)


ACTIVATION_A = "11111111-1111-4111-8111-111111111111"
ACTIVATION_B = "22222222-2222-4222-8222-222222222222"


class _Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _command(activation_id: str, marker: str = "a") -> dict:
    return {
        "schema": ACTIVATION_COMMAND_SCHEMA,
        "schema_version": 1,
        "activation_id": activation_id,
        "basis_digest": marker * 64,
        "basis": {"marker": marker},
        "desired": {"scene": {"marker": marker}},
    }


def _status(activation_id: str) -> dict:
    components = [
        {
            "slot_id": slot_id,
            "provider": "python",
            "component_id": component_id,
            "component_digest": digest * 64,
            "browser_runtime_digest": digest * 64,
            "controller_runtime_digest": digest * 64,
            "parameter_schema_version": 1,
        }
        for slot_id, component_id, digest in (
            ("background", "gradient", "1"),
            ("known_python_fallback", "gradient", "1"),
        )
    ]
    identity = {
        "scene_identity": {"revision": 1, "digest": "c" * 64},
        "component_identities": components,
        "global_settings_identity": {"revision": 1, "digest": "d" * 64},
        "installation_profile_digest": "0" * 64,
    }
    return {
        "schema": ACTIVATION_STATUS_SCHEMA,
        "schema_version": 1,
        "activation_id": activation_id,
        "basis_digest": "a" * 64,
        "command_id": activation_id,
        "phase": "queued",
        "requested_identity": identity,
        "normalized_identity": identity,
        "observed_identity": None,
        "controller": {
            "session_id": "e" * 32,
            "state_revision_before": 0,
            "state_revision_after": None,
        },
        "telemetry": {"complete": False, "fresh": False, "observed_at": None},
        "rollback": {
            "available": False,
            "snapshot_id": None,
            "result": None,
            "error": None,
        },
        "camera_observation": None,
        "error": None,
    }


class ActivationTokenStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.clock = _Clock()
        self.path = Path(self.temporary.name) / "tokens.sqlite3"
        self.store = ActivationTokenStore(self.path, clock=self.clock)

    def test_raw_token_is_never_retained_and_expiry_is_120_seconds(self) -> None:
        issued = self.store.issue({"schema": "basis", "value": 1})

        self.assertGreaterEqual(len(issued.token), 43)
        self.assertEqual(issued.expires_at - issued.issued_at, 120)
        retained = b"".join(
            path.read_bytes() for path in self.path.parent.iterdir() if path.is_file()
        )
        self.assertNotIn(issued.token.encode("utf-8"), retained)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

        self.clock.value = issued.expires_at
        with self.assertRaises(ActivationTokenExpired):
            self.store.inspect(issued.token)

    def test_first_use_binds_and_exact_retry_returns_same_activation(self) -> None:
        issued = self.store.issue({"schema": "basis", "value": 1})
        request_digest = canonical_digest({"request": 1})
        bound = self.store.bind(
            issued.token,
            basis_digest=issued.basis_digest,
            idempotency_key="retry-key",
            request_digest=request_digest,
            activation_id_factory=lambda: ACTIVATION_A,
        )
        retried = self.store.bind(
            issued.token,
            basis_digest=issued.basis_digest,
            idempotency_key="retry-key",
            request_digest=request_digest,
            activation_id_factory=lambda: ACTIVATION_B,
        )

        self.assertEqual(bound.activation_id, ACTIVATION_A)
        self.assertFalse(bound.exact_retry)
        self.assertEqual(retried.activation_id, ACTIVATION_A)
        self.assertTrue(retried.exact_retry)

        with self.assertRaises(ActivationTokenConflict):
            self.store.bind(
                issued.token,
                basis_digest=issued.basis_digest,
                idempotency_key="different-key",
                request_digest=request_digest,
                activation_id_factory=lambda: ACTIVATION_B,
            )


class ActivationControlChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.channel = FileControlChannel(
            str(root / "control.json"),
            str(root / "status.json"),
            str(root / "activations"),
        )

    def test_concurrent_activation_records_never_overwrite(self) -> None:
        first = _command(ACTIVATION_A, "a")
        second = _command(ACTIVATION_B, "b")

        self.channel.enqueue_activation(first)
        self.channel.enqueue_activation(second)

        self.assertEqual(self.channel.read_activation_command(ACTIVATION_A), first)
        self.assertEqual(self.channel.read_activation_command(ACTIVATION_B), second)
        self.assertEqual(len(self.channel.list_activation_commands()), 2)

    def test_exact_queue_retry_is_idempotent_but_changed_retry_conflicts(self) -> None:
        command = _command(ACTIVATION_A)
        self.assertEqual(self.channel.enqueue_activation(command), command)
        self.assertEqual(self.channel.enqueue_activation(command), command)
        changed = _command(ACTIVATION_A, "b")
        with self.assertRaises(FileExistsError):
            self.channel.enqueue_activation(changed)
        self.assertEqual(len(self.channel.list_activation_commands()), 1)

    def test_activation_json_fails_closed_and_status_is_correlated(self) -> None:
        status = _status(ACTIVATION_A)
        self.channel.write_activation_status(status)
        self.assertEqual(self.channel.read_activation_status(ACTIVATION_A), status)

        path = self.channel.activation_status_file(ACTIVATION_A)
        path.write_text(json.dumps(status) + json.dumps(status), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "malformed"):
            self.channel.read_activation_status(ACTIVATION_A)

    def test_cancel_requests_are_durable_and_idempotent(self) -> None:
        first = self.channel.request_activation_cancel(ACTIVATION_A)
        second = self.channel.request_activation_cancel(ACTIVATION_A)
        self.assertEqual(first, second)
        self.assertEqual(first["activation_id"], ACTIVATION_A)
        self.assertEqual(str(uuid.UUID(first["request_id"])), first["request_id"])
        self.assertEqual(
            self.channel.read_activation_cancel(ACTIVATION_A), first
        )

        result = self.channel.write_activation_cancel_result(
            ACTIVATION_A,
            request_id=first["request_id"],
            outcome="succeeded",
            status_phase="failed",
        )
        self.assertEqual(
            self.channel.write_activation_cancel_result(
                ACTIVATION_A,
                request_id=first["request_id"],
                outcome="succeeded",
                status_phase="failed",
            ),
            result,
        )
        self.assertEqual(
            self.channel.read_activation_cancel_result(ACTIVATION_A), result
        )

    def test_rollback_request_result_is_correlated_and_terminal(self) -> None:
        request = self.channel.request_activation_rollback(
            ACTIVATION_A,
            snapshot_id="snapshot-1",
            expected_controller_session_id="a" * 32,
            expected_controller_state_revision=7,
        )
        retried = self.channel.request_activation_rollback(
            ACTIVATION_A,
            snapshot_id="snapshot-1",
            expected_controller_session_id="a" * 32,
            expected_controller_state_revision=7,
        )
        self.assertEqual(retried, request)
        result = self.channel.write_activation_rollback_result(
            ACTIVATION_A,
            request_id=request["request_id"],
            outcome="rejected",
            status_phase="active",
            error="controller revision changed",
        )
        self.assertEqual(result["request_id"], request["request_id"])
        self.assertEqual(result["outcome"], "rejected")
        self.assertEqual(
            self.channel.read_activation_rollback_result(ACTIVATION_A), result
        )
        with self.assertRaisesRegex(FileExistsError, "different terminal result"):
            self.channel.write_activation_rollback_result(
                ACTIVATION_A,
                request_id=request["request_id"],
                outcome="succeeded",
                status_phase="rolled_back",
            )


if __name__ == "__main__":
    unittest.main()
