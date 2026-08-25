from __future__ import annotations

import hashlib
import sys
import threading
import types
import unittest

if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from animation.core.installation_profile_transaction import (
    InstallationProfileCacheBinding,
    InstallationProfileCandidate,
    InstallationProfileTransaction,
    InstallationProfileTransactionPhase,
)
from animation.core.installation_profile_topology import (
    RECEIVER_COUNT,
    RECEIVER_IDS,
    INSTALLED_INSTALLATION_PROFILE_TOPOLOGY,
)
from drivers.installation_profile_receiver import SpiInstallationProfileWall
from drivers.installation_profile_receiver import (
    SpiInstallationProfilePreflightPlan,
    SpiInstallationProfileReceiver,
)
from drivers.multi_device import MultiDeviceLEDController
from drivers.spi_controller import (
    CAPABILITY_INSTALLATION_PROFILE_V1,
    CAPABILITY_STATUS_V5,
)


def profile_id(label):
    return hashlib.sha256(f"global:{label}".encode()).hexdigest()


def payload(receiver_id, *, size=9000):
    value = bytearray((65 + receiver_id,) * size)
    value[:4] = b"LGIP"
    reversed_order = (
        INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
        .reverse_native_strips_by_logical_receiver[receiver_id]
    )
    value[8:12] = int(reversed_order).to_bytes(4, "big")
    origin = INSTALLED_INSTALLATION_PROFILE_TOPOLOGY \
        .strip_origin_for_logical_receiver(receiver_id)
    value[16:18] = origin.to_bytes(2, "big")
    return bytes(value)


def candidate(label="candidate", *, size=9000):
    return InstallationProfileCandidate(
        profile_id(label),
        {receiver_id: payload(receiver_id, size=size) for receiver_id in RECEIVER_IDS},
    )


class ProfileDevice:
    def __init__(self, logical_receiver):
        self.logical_receiver = logical_receiver
        self.calls = []
        self.result = 1
        self.transfer_state = 0
        self.decoder_error = 0
        self.can_stage = False
        self.generation = 0
        self.token = 0
        self.token_counter = 100
        self.active = None
        self.staged = None
        self.rollback = None
        self.transfer_binding = None
        self.transfer_size = 0
        self.transfer = bytearray()
        self.cache = {}
        self.writes = self.evictions = 0
        self.stages = self.verifies = self.activations = self.restores = 0
        self.base_mode = 1
        self.fail_before = set()
        self.fail_after = set()
        self.dishonest_preflight = False

    def _fail_before(self, operation):
        if operation in self.fail_before:
            raise OSError(f"{operation} failed before mutation")

    def _fail_after(self, operation):
        if operation in self.fail_after:
            raise TimeoutError(f"{operation} timed out after mutation")

    def _flags(self):
        flags = 0x01
        if self.can_stage:
            flags |= 0x02
        if self.active:
            flags |= 0x08
        if self.staged:
            flags |= 0x10
        if self.rollback:
            flags |= 0x20
        if self.transfer_binding:
            flags |= 0x40
        return flags

    @staticmethod
    def _fields(binding, name):
        return {
            f"receiver_profile_{name}_global_digest": (
                binding.profile_id if binding else None
            ),
            f"receiver_profile_{name}_payload_digest": (
                binding.payload_digest if binding else None
            ),
        }

    def query_receiver_status(self):
        status = {
            "receiver_status_version": 5,
            "receiver_capabilities": (
                CAPABILITY_INSTALLATION_PROFILE_V1 | CAPABILITY_STATUS_V5
            ),
            "receiver_logical_device": self.logical_receiver,
            "receiver_profile_result": self.result,
            "receiver_profile_result_name": "ok" if self.result == 1 else "rejected",
            "receiver_profile_transfer_state": self.transfer_state,
            "receiver_profile_decoder_error": self.decoder_error,
            "receiver_profile_flags": self._flags(),
            "receiver_profile_cache_integrity_ok": True,
            "receiver_profile_preflight_can_stage": (
                False if self.dishonest_preflight else self.can_stage
            ),
            "receiver_profile_last_probe_found": False,
            "receiver_profile_transfer_active": self.transfer_binding is not None,
            "receiver_profile_capacity_bytes": 100_000,
            "receiver_profile_used_bytes": sum(len(value) for value in self.cache.values()),
            "receiver_profile_free_bytes": (
                100_000 - sum(len(value) for value in self.cache.values())
            ),
            "receiver_profile_reserve_bytes": 10_000,
            "receiver_profile_reclaimable_bytes": 0,
            "receiver_profile_received_bytes": len(self.transfer),
            "receiver_profile_total_bytes": self.transfer_size,
            "receiver_profile_state_generation": self.generation,
            "receiver_profile_preflight_token": self.token,
            "receiver_profile_last_probe_payload_digest": None,
            "receiver_profile_transfer_global_digest": (
                self.transfer_binding.profile_id if self.transfer_binding else None
            ),
            "receiver_profile_transfer_payload_digest": (
                self.transfer_binding.payload_digest if self.transfer_binding else None
            ),
            "receiver_profile_writes": self.writes,
            "receiver_profile_evictions": self.evictions,
            "receiver_profile_stages": self.stages,
            "receiver_profile_verifies": self.verifies,
            "receiver_profile_activations": self.activations,
            "receiver_profile_restores": self.restores,
        }
        status.update(self._fields(self.active, "active"))
        status.update(self._fields(self.staged, "staged"))
        status.update(self._fields(self.rollback, "rollback"))
        return status

    def profile_preflight(self, *, profile_id, payload_digest, payload_size):
        self._fail_before("preflight")
        self.calls.append(("preflight", profile_id, payload_digest, payload_size))
        self.token_counter += 1
        self.token = self.token_counter
        self.can_stage = True
        self.transfer_state = 1
        return self.query_receiver_status()

    def profile_begin(self, **fields):
        self._fail_before("begin")
        self.calls.append(("begin", dict(fields)))
        if fields["preflight_token"] != self.token:
            self.result = 5
            return self.query_receiver_status()
        from animation.core.installation_profile_transaction import (
            InstallationProfileCacheBinding,
        )
        binding = InstallationProfileCacheBinding(
            fields["profile_id"], fields["payload_digest"]
        )
        if binding.payload_digest in self.cache:
            self.staged = binding
            self.stages += 1
            self.transfer_state = 4
            self.can_stage = False
        else:
            self.transfer_binding = binding
            self.transfer_size = fields["payload_size"]
            self.transfer = bytearray()
            self.transfer_state = 2
        status = self.query_receiver_status()
        self._fail_after("begin")
        return status

    def profile_chunk(self, *, offset, data):
        self._fail_before("chunk")
        self.calls.append(("chunk", offset, len(data)))
        if offset < len(self.transfer):
            if bytes(self.transfer[offset:offset + len(data)]) != bytes(data):
                self.result = 6
                return self.query_receiver_status()
        elif offset == len(self.transfer):
            self.transfer.extend(data)
        else:
            self.result = 6
            return self.query_receiver_status()
        status = self.query_receiver_status()
        self._fail_after("chunk")
        return status

    def profile_finalize(self, *, profile_id, payload_digest):
        self._fail_before("finalize")
        self.calls.append(("finalize", profile_id, payload_digest))
        if (
            self.transfer_binding is None
            or self.transfer_binding.profile_id != profile_id
            or self.transfer_binding.payload_digest != payload_digest
            or len(self.transfer) != self.transfer_size
            or hashlib.sha256(self.transfer).hexdigest() != payload_digest
        ):
            self.result = 7
            return self.query_receiver_status()
        self.cache[payload_digest] = bytes(self.transfer)
        self.writes += 1
        self.staged = self.transfer_binding
        self.transfer_binding = None
        self.transfer = bytearray()
        self.transfer_size = 0
        self.transfer_state = 4
        self.can_stage = False
        self.stages += 1
        status = self.query_receiver_status()
        self._fail_after("finalize")
        return status

    def profile_verify(self, *, profile_id, payload_digest):
        self._fail_before("verify")
        self.calls.append(("verify", profile_id, payload_digest))
        self.verifies += 1
        if (
            self.staged is None
            or self.staged.profile_id != profile_id
            or self.staged.payload_digest != payload_digest
            or payload_digest not in self.cache
        ):
            self.result = 13
        status = self.query_receiver_status()
        self._fail_after("verify")
        return status

    def profile_activate(self, *, expected_generation, profile_id, payload_digest):
        self._fail_before("activate")
        self.calls.append(("activate", expected_generation, profile_id, payload_digest))
        if expected_generation != self.generation or self.staged is None:
            self.result = 14
            return self.query_receiver_status()
        self.rollback = self.active
        self.active = self.staged
        self.staged = None
        self.generation += 1
        self.activations += 1
        self.transfer_state = 0
        status = self.query_receiver_status()
        self._fail_after("activate")
        return status

    def profile_restore(self, *, expected_generation, active_binding,
                        staged_binding, rollback_binding):
        self._fail_before("restore")
        self.calls.append(("restore", expected_generation))
        if expected_generation != self.generation:
            self.result = 14
            return self.query_receiver_status()
        self.active = active_binding
        self.staged = staged_binding
        self.rollback = rollback_binding
        self.transfer_binding = None
        self.transfer = bytearray()
        self.transfer_size = 0
        self.transfer_state = 0
        self.can_stage = False
        self.generation += 1
        self.restores += 1
        status = self.query_receiver_status()
        self._fail_after("restore")
        return status

    def profile_abort(self):
        self.calls.append(("abort",))
        self.transfer_binding = None
        self.transfer = bytearray()
        self.transfer_size = 0
        return self.query_receiver_status()


class SpiInstallationProfileAdapterTests(unittest.TestCase):
    @staticmethod
    def devices():
        return [ProfileDevice(index) for index in RECEIVER_IDS]

    def test_real_adapter_runs_existing_transaction_in_exact_phase_order(self):
        devices = self.devices()
        wall = SpiInstallationProfileWall(devices, enabled=True)
        selected = candidate(size=10_264)
        result = InstallationProfileTransaction(wall).install(selected)

        self.assertTrue(result.success, result.error)
        self.assertTrue(result.changed)
        self.assertEqual(result.wall_status.active_profile_id, selected.profile_id)
        for receiver_id, device in enumerate(devices):
            names = [call[0] for call in device.calls]
            self.assertEqual(
                names,
                ["preflight", "begin", "chunk", "chunk", "chunk",
                 "finalize", "verify", "activate"],
            )
            begin = device.calls[1][1]
            self.assertEqual(begin["logical_receiver_id"], receiver_id)
            self.assertEqual(
                begin["strip_origin"],
                INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
                .strip_origin_for_logical_receiver(receiver_id),
            )
            self.assertEqual(
                begin["reversed_strip_order"],
                INSTALLED_INSTALLATION_PROFILE_TOPOLOGY
                .reverse_native_strips_by_logical_receiver[receiver_id],
            )
            self.assertEqual(device.base_mode, 1)

    def test_partial_stage_failure_restores_every_board_exactly(self):
        devices = self.devices()
        devices[2].fail_after.add("chunk")
        wall = SpiInstallationProfileWall(devices, enabled=True)
        result = InstallationProfileTransaction(wall).install(candidate())

        self.assertFalse(result.success)
        self.assertEqual(result.failed_phase, InstallationProfileTransactionPhase.STAGE)
        self.assertEqual(result.failed_receiver_id, 2)
        self.assertTrue(result.compensated, result.error)
        self.assertFalse(result.changed)
        self.assertTrue(result.wall_status.no_active)
        self.assertTrue(all(any(call[0] == "restore" for call in device.calls)
                            for device in devices))
        self.assertTrue(all(device.active is None and device.staged is None
                            and device.rollback is None for device in devices))

    def test_timeout_after_activation_reconciles_exact_commit_without_resend(self):
        devices = self.devices()
        devices[2].fail_after.add("activate")
        selected = candidate()
        result = InstallationProfileTransaction(
            SpiInstallationProfileWall(devices, enabled=True)
        ).install(selected)

        self.assertTrue(result.success, result.error)
        self.assertEqual(
            [call[0] for call in devices[2].calls].count("activate"), 1
        )
        self.assertEqual(devices[2].active.profile_id, selected.profile_id)

    def test_dishonest_preflight_fails_before_any_profile_mutation(self):
        devices = self.devices()
        devices[1].dishonest_preflight = True
        result = InstallationProfileTransaction(
            SpiInstallationProfileWall(devices, enabled=True)
        ).install(candidate())

        self.assertFalse(result.success)
        self.assertEqual(result.failed_phase, InstallationProfileTransactionPhase.PREFLIGHT)
        self.assertFalse(result.compensated)
        self.assertFalse(result.changed)
        self.assertFalse(any(call[0] in {"begin", "chunk", "finalize", "activate"}
                             for device in devices for call in device.calls))

    def test_identical_retry_is_noop_and_never_rewrites_cache(self):
        devices = self.devices()
        wall = SpiInstallationProfileWall(devices, enabled=True)
        transaction = InstallationProfileTransaction(wall)
        selected = candidate()
        first = transaction.install(selected)
        before = tuple(device.writes for device in devices)
        call_counts = tuple(len(device.calls) for device in devices)
        second = transaction.install(selected)

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        self.assertFalse(second.changed)
        self.assertEqual(tuple(device.writes for device in devices), before)
        self.assertEqual(tuple(len(device.calls) for device in devices), call_counts)

    def test_controller_lock_integration_preserves_display_authority(self):
        devices = self.devices()
        item = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        item.devices = devices
        item.num_devices = RECEIVER_COUNT
        item._transport_lock = threading.RLock()
        item._installation_profile_wall = None
        item._receiver_geometry_profile_enabled = True
        item._installation_profile_status = {"state": "idle", "operation": "test"}
        item._local_background_active = True
        item._local_background_context_digest = "context"
        item._local_background_parameters = {"common_seed": 7}
        item._display_ownership_known = True

        result = item.install_installation_profile(candidate())

        self.assertTrue(result.success, result.error)
        self.assertTrue(item._local_background_active)
        self.assertEqual(item._local_background_context_digest, "context")
        self.assertEqual(item._local_background_parameters, {"common_seed": 7})
        self.assertTrue(item._display_ownership_known)
        self.assertTrue(all(device.base_mode == 1 for device in devices))
        self.assertEqual(item._installation_profile_status["state"], "active")

    def test_host_rollout_gate_defaults_off_and_emits_no_profile_traffic(self):
        devices = self.devices()
        queries = [0]
        original_query = devices[0].query_receiver_status

        def counted_query():
            queries[0] += 1
            return original_query()

        devices[0].query_receiver_status = counted_query
        receiver = SpiInstallationProfileReceiver(0, devices[0])
        with self.assertRaisesRegex(Exception, "rollout gate is disabled"):
            receiver.refresh()
        selected = candidate().receiver_payloads[0]
        binding = InstallationProfileCacheBinding(
            profile_id("candidate"), hashlib.sha256(selected).hexdigest()
        )
        with self.assertRaisesRegex(Exception, "rollout gate is disabled"):
            receiver.preflight_profile(binding, selected)
        self.assertEqual(queries[0], 0)

        item = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        item.devices = devices
        item.num_devices = RECEIVER_COUNT
        with self.assertRaisesRegex(RuntimeError, "rollout gate is disabled"):
            item.install_installation_profile(candidate())
        self.assertFalse(item._receiver_geometry_profile_enabled)
        self.assertFalse(any(device.calls for device in devices))

    def test_rollout_gate_requires_strict_boolean_inputs(self):
        with self.assertRaisesRegex(
            TypeError, "receiver_geometry_profile must be a boolean"
        ):
            MultiDeviceLEDController(receiver_geometry_profile=1)
        with self.assertRaisesRegex(TypeError, "enabled must be a boolean"):
            SpiInstallationProfileReceiver(0, ProfileDevice(0), enabled=1)
        with self.assertRaisesRegex(TypeError, "enabled must be a boolean"):
            SpiInstallationProfileWall(self.devices(), enabled=1)

    def test_wrong_receiver_identity_fails_closed(self):
        devices = self.devices()
        devices[3].logical_receiver = 2
        with self.assertRaisesRegex(Exception, "logical identity"):
            InstallationProfileTransaction(
                SpiInstallationProfileWall(devices, enabled=True)
            ).install(candidate())

    def test_adapter_rejects_malformed_status_payload_and_plan_inputs(self):
        device = ProfileDevice(0)
        receiver = SpiInstallationProfileReceiver(0, device, enabled=True)
        with self.assertRaises(ValueError):
            SpiInstallationProfileReceiver(RECEIVER_COUNT, device)
        with self.assertRaises(ValueError):
            SpiInstallationProfileWall([device])
        with self.assertRaisesRegex(Exception, "no profile status"):
            receiver._apply_status(None)

        valid = device.query_receiver_status()
        cases = (
            ({**valid, "receiver_status_version": 4}, "status v5"),
            ({**valid, "receiver_capabilities": 0}, "status v5"),
            ({**valid, "receiver_logical_device": 1}, "logical identity"),
            ({**valid, "receiver_profile_state_generation": -1}, "generation"),
            ({**valid, "receiver_profile_active_global_digest": "a" * 64,
              "receiver_profile_active_payload_digest": None}, "incomplete"),
            ({**valid, "receiver_profile_active_global_digest": "z" * 64,
              "receiver_profile_active_payload_digest": "a" * 64}, "invalid"),
            ({**valid, "receiver_profile_flags": 0x08}, "inconsistent active"),
            ({**valid, "receiver_profile_flags": 0x80}, "invalid profile flags"),
        )
        for status, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(Exception, message):
                receiver._apply_status(status)

        selected = candidate().binding_for(0)
        good_payload = candidate().payload_for(0)
        with self.assertRaises(TypeError):
            receiver.preflight_profile(object(), good_payload)
        with self.assertRaisesRegex(Exception, "hash mismatch"):
            receiver.preflight_profile(selected, good_payload + b"x")
        malformed = b"x" * 20
        malformed_binding = type(selected)(
            selected.profile_id, hashlib.sha256(malformed).hexdigest()
        )
        with self.assertRaisesRegex(Exception, "LGIP header"):
            receiver.preflight_profile(malformed_binding, malformed)
        bad_flags = bytearray(good_payload)
        bad_flags[8:12] = (2).to_bytes(4, "big")
        bad_flags = bytes(bad_flags)
        bad_flags_binding = type(selected)(
            selected.profile_id, hashlib.sha256(bad_flags).hexdigest()
        )
        with self.assertRaisesRegex(Exception, "unsupported flags"):
            receiver.preflight_profile(bad_flags_binding, bad_flags)

        with self.assertRaises(TypeError):
            receiver.stage_profile(object(), good_payload, corrupt_payload=False)
        fake_plan = SpiInstallationProfilePreflightPlan(
            receiver_id=1,
            binding=selected,
            payload_size=len(good_payload),
            preflight_token=1,
            state_generation=0,
            strip_origin=0,
            reversed_strip_order=False,
        )
        with self.assertRaisesRegex(Exception, "plan mismatch"):
            receiver.stage_profile(fake_plan, good_payload, corrupt_payload=False)
        with self.assertRaises(ValueError):
            receiver.stage_profile(fake_plan, good_payload, corrupt_payload=True)
        with self.assertRaises(TypeError):
            receiver.compensate_profile(object())

    def test_verify_and_activate_failures_compensate_and_restore_timeout_reconciles(self):
        for operation in ("verify", "activate"):
            with self.subTest(operation=operation):
                devices = self.devices()
                devices[2].fail_before.add(operation)
                if operation == "verify":
                    devices[0].fail_after.add("restore")
                result = InstallationProfileTransaction(
                    SpiInstallationProfileWall(devices, enabled=True)
                ).install(candidate())
                self.assertFalse(result.success)
                self.assertTrue(result.compensated, result.error)
                self.assertFalse(result.changed)
                self.assertTrue(all(device.active is None for device in devices))

    def test_cache_hit_stages_without_chunks_or_rewrite(self):
        devices = self.devices()
        selected = candidate()
        for receiver_id, device in enumerate(devices):
            digest = selected.binding_for(receiver_id).payload_digest
            device.cache[digest] = selected.payload_for(receiver_id)
        result = InstallationProfileTransaction(
            SpiInstallationProfileWall(devices, enabled=True)
        ).install(selected)
        self.assertTrue(result.success, result.error)
        self.assertTrue(all(device.writes == 0 for device in devices))
        self.assertTrue(all(
            not any(call[0] in {"chunk", "finalize"} for call in device.calls)
            for device in devices
        ))


if __name__ == "__main__":
    unittest.main()
