"""Exact-basis control and observed-identity contracts for Scene v2."""

from __future__ import annotations

import hashlib
import unittest

from ipc.scene_contract import LocalSceneAdapter, SceneContractError, canonical_json_bytes
from tests.unit.test_scene_activation_contract import _catalog, _request, _scene
from web.activation_token_store import (
    ActivationTokenConflict,
    ActivationTokenStale,
    ActivationTokenStore,
    ActivationTokenUnknown,
)


class _Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


class _ControlChannel:
    def __init__(self) -> None:
        self.commands: list[dict] = []

    def send_command(self, action: str, **data: object) -> dict:
        command = {"action": action, **data}
        self.commands.append(command)
        return command


class ActivationControlChannelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = _Clock()
        self.store = ActivationTokenStore(clock=self.clock, ttl_seconds=10)
        self.checked = self.store.check(_request(), _catalog())
        self.channel = _ControlChannel()
        self.adapter = LocalSceneAdapter(_catalog())

    def test_check_activation_control_and_observed_adapter_have_one_identity(self) -> None:
        command = {
            "action": "activate_scene", "basis": self.checked.identity.to_dict(),
            "scene": self.checked.canonical.scene,
        }
        identity, canonical_bytes = self.adapter.validate_control(command)
        self.assertEqual(identity, self.checked.identity)
        self.assertEqual(canonical_bytes, self.checked.canonical.canonical_bytes)
        self.assertEqual(hashlib.sha256(canonical_bytes).hexdigest(), identity.digest)
        receipt = self.store.activate(
            self.checked.token, basis=self.checked.identity.to_dict(),
            idempotency_key="composer-save-1", control_channel=self.channel,
            local_adapter=self.adapter,
        )

        self.assertEqual(receipt.identity, self.checked.identity)
        self.assertEqual(self.channel.commands[0]["basis"], self.checked.identity.to_dict())
        self.assertEqual(self.adapter.observed_identity(), self.checked.identity)
        self.assertNotIn("receiver", receipt.command)
        self.assertNotIn("topology", receipt.command)

    def test_exact_retry_is_idempotent_and_conflicts_before_another_mutation(self) -> None:
        first = self.store.activate(
            self.checked.token, basis=self.checked.identity.to_dict(),
            idempotency_key="composer-save-1", control_channel=self.channel,
            local_adapter=self.adapter,
        )
        retry = self.store.activate(
            self.checked.token, basis=self.checked.identity.to_dict(),
            idempotency_key="composer-save-1", control_channel=self.channel,
            local_adapter=self.adapter,
        )
        self.assertFalse(first.exact_retry)
        self.assertTrue(retry.exact_retry)
        self.assertEqual(len(self.channel.commands), 1)
        with self.assertRaises(ActivationTokenConflict):
            self.store.activate(
                self.checked.token, basis=self.checked.identity.to_dict(),
                idempotency_key="different-request", control_channel=self.channel,
                local_adapter=self.adapter,
            )
        self.assertEqual(len(self.channel.commands), 1)

    def test_unknown_stale_and_mismatched_inputs_fail_before_control_or_adapter_mutation(self) -> None:
        with self.assertRaises(ActivationTokenUnknown):
            self.store.activate(
                "unknown", basis=self.checked.identity.to_dict(), idempotency_key="x",
                control_channel=self.channel, local_adapter=self.adapter,
            )
        wrong_basis = {"revision": 2, "digest": "0" * 64}
        with self.assertRaises(ActivationTokenConflict):
            self.store.activate(
                self.checked.token, basis=wrong_basis, idempotency_key="x",
                control_channel=self.channel, local_adapter=self.adapter,
            )
        stale = self.store.check(_request(), _catalog())
        self.clock.value += 10
        with self.assertRaises(ActivationTokenStale):
            self.store.activate(
                stale.token, basis=stale.identity.to_dict(), idempotency_key="x",
                control_channel=self.channel, local_adapter=self.adapter,
            )
        self.assertEqual(self.channel.commands, [])
        self.assertIsNone(self.adapter.observed_identity())

    def test_adapter_rejects_self_digested_malformed_scene_before_observation(self) -> None:
        malformed = _scene()
        malformed["animation"] = {**malformed["animation"], "role": "widget"}
        command = {
            "action": "activate_scene",
            "basis": {"revision": 2, "digest": hashlib.sha256(canonical_json_bytes(malformed)).hexdigest()},
            "scene": malformed,
        }
        with self.assertRaises(SceneContractError):
            self.adapter.accept_control(command)
        self.assertIsNone(self.adapter.observed_identity())

    def test_v1_request_rejects_before_the_local_composer_boundary_constructs_state(self) -> None:
        with self.assertRaises(SceneContractError):
            self.store.check({"origin": "composer", "scene": {"schema": "ledgrid.scene.v1"}}, _catalog())
        self.assertIsNone(self.adapter.observed_identity())


if __name__ == "__main__":
    unittest.main()
