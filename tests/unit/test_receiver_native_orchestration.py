"""Five-receiver native install, compensation, and transport-lock tests."""

from __future__ import annotations

from dataclasses import dataclass
import sys
import threading
import types
import unittest
from unittest import mock


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from animation.core.native_background_operation import NativeBackgroundBinding
from animation.core.plant_awareness import PlantModifierState
from animation.core.presentation_contracts import resolve_vibe
from animation.core.receiver_presentation import ReceiverPresentationContext
from drivers.multi_device import (
    MultiDeviceLEDController,
    NATIVE_BACKGROUND_REQUIRED_CAPABILITIES,
)


WIDTHS = (8, 8, 8, 8, 1)
OFFSETS = (0, 8, 24, 16, 32)
BUNDLE = "31" * 32
PAYLOAD = "42" * 32
OLD_BUNDLE = "51" * 32
OLD_PAYLOAD = "62" * 32


@dataclass(frozen=True)
class _Candidate:
    payload: bytes
    binding: NativeBackgroundBinding = NativeBackgroundBinding(BUNDLE, PAYLOAD)

    def descriptor_for(self, receiver_id):
        return {
            "bundle_digest": BUNDLE,
            "payload_digest": PAYLOAD,
            "payload_size": len(self.payload),
            "abi_version": 2,
            "target": 1,
            "global_strips": 33,
            "local_strips": WIDTHS[receiver_id],
            "leds_per_strip": 138,
            "global_strip_offset": OFFSETS[receiver_id],
            "cadence_hz": 30,
            "parameter_schema_revision": 7,
            "flags": 0,
        }


class _Receiver:
    def __init__(self, receiver_id, *, fail_finalize=False, block_chunk=None):
        self.receiver_id = receiver_id
        self.strip_count = WIDTHS[receiver_id]
        self.fail_finalize = fail_finalize
        self.block_chunk = block_chunk
        self.active = NativeBackgroundBinding(OLD_BUNDLE, OLD_PAYLOAD)
        self.staged = None
        self.rollback = None
        self.quarantine = None
        self.cached_payloads = {OLD_PAYLOAD}
        self.probe_found = False
        self.last_probe_payload = None
        self.probe_echo_override = None
        self.transfer_state = 0
        self.received = 0
        self.token = 100 + receiver_id
        self.generation = 10 + receiver_id
        self.payload = bytearray()
        self.restore_calls = []
        self.abort_calls = 0
        self.pixel_calls = []
        self.presentation_context = None
        self.fail_activate = False
        self.remove_calls = []
        self.quarantine_clear_calls = []

    def _status(self):
        flags = 0x01 | 0x04
        if self.active is not None:
            flags |= 0x08
        if self.staged is not None:
            flags |= 0x10
        if self.rollback is not None:
            flags |= 0x20
        if self.quarantine is not None:
            flags |= 0x40
        status = {
            "receiver_status_version": 6,
            "receiver_capabilities": NATIVE_BACKGROUND_REQUIRED_CAPABILITIES,
            "receiver_logical_device": self.receiver_id,
            "receiver_native_result": 1,
            "receiver_native_result_name": "ok",
            "receiver_native_flags": flags,
            "receiver_native_ready": True,
            "receiver_native_cache_integrity_ok": True,
            "receiver_native_transfer_state": self.transfer_state,
            "receiver_native_received_bytes": self.received,
            "receiver_native_preflight_token": self.token,
            "receiver_native_state_generation": self.generation,
            "receiver_native_probe_found": self.probe_found,
            "receiver_native_last_probe_payload_digest": self.last_probe_payload,
            "receiver_native_quarantine_payload_digest": self.quarantine,
            "receiver_last_result": 1,
            "receiver_base_mode": 1 if self.presentation_context is not None else 0,
            "receiver_vibe_revision": (
                self.presentation_context.vibe.state.revision
                if self.presentation_context is not None else None
            ),
            "receiver_vibe_digest": (
                self.presentation_context.vibe.state.resolved_profile_digest
                if self.presentation_context is not None else None
            ),
            "receiver_plant_modifier_revision": (
                self.presentation_context.plant_revision
                if self.presentation_context is not None else None
            ),
            "receiver_plant_modifier_digest": (
                self.presentation_context.plant_digest.hex()
                if self.presentation_context is not None else None
            ),
        }
        for prefix, binding in (
            ("active", self.active),
            ("staged", self.staged),
            ("rollback", self.rollback),
        ):
            status[f"receiver_native_{prefix}_bundle_digest"] = (
                binding.bundle_digest if binding else None
            )
            status[f"receiver_native_{prefix}_payload_digest"] = (
                binding.payload_digest if binding else None
            )
        return status

    def query_receiver_status(self):
        return self._status()

    def native_probe(self, *, payload_digest):
        self.probe_found = payload_digest in self.cached_payloads
        self.last_probe_payload = self.probe_echo_override or payload_digest
        return self._status()

    def native_preflight(self, **descriptor):
        if descriptor["payload_digest"] == self.quarantine:
            raise RuntimeError("quarantined")
        self.transfer_state = 1
        return self._status()

    def native_begin(self, **_descriptor):
        self.transfer_state = 2
        self.received = 0
        self.payload.clear()
        return self._status()

    def native_chunk(self, *, offset, data):
        if self.block_chunk is not None:
            entered, release = self.block_chunk
            entered.set()
            release.wait(2)
        if offset != self.received:
            raise RuntimeError("wrong offset")
        self.payload.extend(data)
        self.received += len(data)
        return self._status()

    def native_finalize(self, **_binding):
        if self.fail_finalize:
            raise OSError("injected receiver finalize failure")
        self.staged = NativeBackgroundBinding(BUNDLE, PAYLOAD)
        self.cached_payloads.add(PAYLOAD)
        self.transfer_state = 4
        self.generation += 1
        return self._status()

    def native_verify(self, **_binding):
        return self._status()

    def native_abort(self):
        self.abort_calls += 1
        self.transfer_state = 0
        self.received = 0
        return self._status()

    def native_restore(
        self, *, expected_generation, active_binding, staged_binding,
        rollback_binding
    ):
        self.restore_calls.append(expected_generation)
        self.active = active_binding
        self.staged = staged_binding
        self.rollback = rollback_binding
        self.transfer_state = 0
        self.generation += 1
        return self._status()

    def begin_presentation_context(self, context):
        self.pending_context = context
        return self._status()

    def set_presentation_context(self, context):
        self.pending_context = context
        return self._status()

    def commit_presentation_context(self, context, *, host_monotonic_anchor_ns):
        self.presentation_context = context
        return self._status()

    def native_activate(self, **_kwargs):
        if self.fail_activate:
            raise OSError("injected native activation failure")
        self.rollback = self.active
        self.active = self.staged
        self.staged = None
        self.generation += 1
        return self._status()

    def native_stop(self):
        self.active = None
        self.staged = None
        self.rollback = None
        self.generation += 1
        return self._status()

    def native_remove(self, *, bundle_digest, payload_digest):
        self.remove_calls.append((bundle_digest, payload_digest))
        self.cached_payloads.discard(payload_digest)
        return self._status()

    def native_quarantine_clear(self, *, payload_digest):
        self.quarantine_clear_calls.append(payload_digest)
        if self.quarantine != payload_digest:
            raise RuntimeError("not found")
        self.quarantine = None
        return self._status()

    def set_pixel(self, *value):
        self.pixel_calls.append(value)


def _wall(receivers):
    wall = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
    wall.devices = list(receivers)
    wall.num_devices = 5
    wall.receiver_strip_counts = WIDTHS
    wall.receiver_global_strip_offsets = OFFSETS
    wall.receiver_pixel_counts = tuple(width * 138 for width in WIDTHS)
    wall.receiver_pixel_offsets = tuple(offset * 138 for offset in OFFSETS)
    wall.receiver_lane_masks = (0xFF, 0xFF, 0xFF, 0xFF, 0x01)
    wall.device_map = [(0, 0), (0, 1), (1, 1), (1, 0), (1, 2)]
    wall.reverse_host_strips_by_logical_receiver = (
        False, False, True, True, False
    )
    wall.reverse_native_strips_by_logical_receiver = (
        False, False, True, True, False
    )
    wall.strip_count = 33
    wall.leds_per_strip = 138
    wall.total_leds = 33 * 138
    wall._receiver_native_modules_enabled = True
    wall._native_background_active = False
    wall._local_background_active = False
    wall._display_ownership_known = False
    wall._native_background_context = None
    wall._native_background_binding = None
    wall._native_background_candidate = None
    wall._native_background_parameter_set = None
    wall._native_background_parameters = {}
    wall._local_background_status = {"state": "idle"}
    wall._controller_lock()
    return wall


def _context():
    return ReceiverPresentationContext(
        controller_session_id=bytes(range(16)),
        scene_revision=5,
        scene_epoch=11,
        present_at_scene_time_us=1000,
        vibe=resolve_vibe("vivid", revision=7),
        plant_modifiers=PlantModifierState.from_payload({
            "active": ["illuminate"], "strengths": {"illuminate": 0.5}
        }),
        plant_revision=9,
    )


class ReceiverNativeOrchestrationTests(unittest.TestCase):
    def test_probe_cache_miss_is_a_successful_negative_result_on_exact_roster(self):
        receivers = [_Receiver(index) for index in range(5)]
        wall = _wall(receivers)
        candidate = mock.Mock(binding=NativeBackgroundBinding(BUNDLE, PAYLOAD))
        with mock.patch.object(
            wall, "_managed_native_background", return_value=candidate
        ):
            result = wall.probe_native_background(object())
        self.assertEqual(result["state"], "missing")
        self.assertEqual(
            [item["logical_device"] for item in result["devices"]],
            [0, 1, 2, 3, 4],
        )
        self.assertTrue(all(not item["found"] for item in result["devices"]))
        self.assertTrue(all(
            item["payload_digest"] == PAYLOAD for item in result["devices"]
        ))

    def test_probe_rejects_wrong_echo_before_accepting_a_cache_hit(self):
        receivers = [_Receiver(index) for index in range(5)]
        for receiver in receivers:
            receiver.cached_payloads.add(PAYLOAD)
        receivers[2].probe_echo_override = "ff" * 32
        wall = _wall(receivers)
        candidate = mock.Mock(binding=NativeBackgroundBinding(BUNDLE, PAYLOAD))
        with (
            mock.patch.object(
                wall, "_managed_native_background", return_value=candidate
            ),
            self.assertRaisesRegex(Exception, "echoed payload digest"),
        ):
            wall.probe_native_background(object())

    def test_shared_payload_removal_is_blocked_by_different_pinned_bundle(self):
        receivers = [_Receiver(index) for index in range(5)]
        receivers[3].active = NativeBackgroundBinding(OLD_BUNDLE, PAYLOAD)
        receivers[3].cached_payloads.add(PAYLOAD)
        wall = _wall(receivers)
        candidate = mock.Mock(binding=NativeBackgroundBinding(BUNDLE, PAYLOAD))
        with (
            mock.patch.object(
                wall, "_managed_native_background", return_value=candidate
            ),
            self.assertRaisesRegex(Exception, "protects the payload"),
        ):
            wall.remove_native_background(object())
        self.assertTrue(all(not receiver.remove_calls for receiver in receivers))
        self.assertIn(PAYLOAD, receivers[3].cached_payloads)

    def test_exact_roster_quarantine_clear_allows_clean_reinstall(self):
        receivers = [_Receiver(index) for index in range(5)]
        for receiver_id in (0, 2, 4):
            receivers[receiver_id].quarantine = PAYLOAD
        wall = _wall(receivers)
        payload = bytes.fromhex(PAYLOAD)
        candidate = mock.Mock(
            payload=payload,
            binding=NativeBackgroundBinding(BUNDLE, PAYLOAD),
        )
        candidate.descriptor_for.side_effect = _Candidate(payload).descriptor_for
        with mock.patch.object(
            wall, "_managed_native_background", return_value=candidate
        ):
            cleared = wall.clear_native_background_quarantine(object())
            installed = wall.install_native_background(object())
        self.assertEqual(cleared["state"], "ready")
        self.assertEqual(cleared["quarantined_devices"], [0, 2, 4])
        self.assertEqual(cleared["cleared_devices"], [0, 2, 4])
        self.assertTrue(cleared["agreement"]["exact_roster"])
        self.assertEqual(installed["state"], "ready")
        self.assertTrue(all(receiver.quarantine is None for receiver in receivers))
        self.assertEqual(
            [receiver.quarantine_clear_calls for receiver in receivers],
            [[PAYLOAD], [], [PAYLOAD], [], [PAYLOAD]],
        )

    def test_quarantine_clear_rejects_conflicting_identity_before_mutation(self):
        receivers = [_Receiver(index) for index in range(5)]
        receivers[0].quarantine = PAYLOAD
        receivers[4].quarantine = OLD_PAYLOAD
        wall = _wall(receivers)
        candidate = mock.Mock(binding=NativeBackgroundBinding(BUNDLE, PAYLOAD))
        with (
            mock.patch.object(
                wall, "_managed_native_background", return_value=candidate
            ),
            self.assertRaisesRegex(Exception, "do not unanimously match"),
        ):
            wall.clear_native_background_quarantine(object())
        self.assertTrue(
            all(not receiver.quarantine_clear_calls for receiver in receivers)
        )

    def test_tail_failure_restores_exact_five_receiver_snapshots(self):
        receivers = [_Receiver(index) for index in range(5)]
        receivers[4].fail_finalize = True
        wall = _wall(receivers)
        # The focused fake constants use PAYLOAD; align its bytes to the
        # candidate without weakening controller digest checks.
        candidate = mock.Mock(
            payload=b"x" * 5000,
            binding=NativeBackgroundBinding(BUNDLE, PAYLOAD),
        )
        candidate.descriptor_for.side_effect = _Candidate(b"x" * 5000).descriptor_for
        with mock.patch.object(
            wall, "_managed_native_background", return_value=candidate
        ):
            with self.assertRaisesRegex(Exception, "compensated=True"):
                wall.install_native_background(object())
        self.assertEqual([receiver.active for receiver in receivers], [
            NativeBackgroundBinding(OLD_BUNDLE, OLD_PAYLOAD)
        ] * 5)
        self.assertTrue(all(receiver.staged is None for receiver in receivers))
        self.assertTrue(all(receiver.restore_calls for receiver in receivers))
        self.assertEqual(receivers[4].abort_calls, 1)
        self.assertEqual(wall._native_background_status["state"], "compensated")

    def test_native_install_holds_controller_lock_against_host_pixel_traffic(self):
        # One single-chunk payload keeps the fake digest constant simple.
        payload = bytes.fromhex(PAYLOAD)
        candidate = mock.Mock(
            payload=payload,
            binding=NativeBackgroundBinding(BUNDLE, PAYLOAD),
        )
        base = _Candidate(payload)
        candidate.descriptor_for.side_effect = base.descriptor_for
        entered = threading.Event()
        release = threading.Event()
        receivers = [_Receiver(index) for index in range(5)]
        receivers[2].block_chunk = (entered, release)
        wall = _wall(receivers)

        install_result = []
        with mock.patch.object(
            wall, "_managed_native_background", return_value=candidate
        ):
            installer = threading.Thread(
                target=lambda: install_result.append(
                    wall.install_native_background(object())
                )
            )
            installer.start()
            self.assertTrue(entered.wait(1))
            pixel = threading.Thread(
                target=lambda: wall.set_pixel(32 * 138, 1, 2, 3)
            )
            pixel.start()
            pixel.join(0.05)
            self.assertTrue(pixel.is_alive())
            release.set()
            installer.join(2)
            pixel.join(2)
        self.assertEqual(install_result[0]["state"], "ready")
        self.assertEqual(receivers[4].pixel_calls, [(0, 1, 2, 3)])

    def test_failed_first_activation_commits_known_host_full_scene_fallback(self):
        receivers = [_Receiver(index) for index in range(5)]
        for receiver in receivers:
            receiver.staged = NativeBackgroundBinding(BUNDLE, PAYLOAD)
        receivers[4].fail_activate = True
        wall = _wall(receivers)
        parameter_set = types.SimpleNamespace(
            schema_revision=7,
            blob=b"\x01\x00",
            digest="71" * 32,
            values={},
        )
        candidate = mock.Mock(binding=NativeBackgroundBinding(BUNDLE, PAYLOAD))
        candidate.descriptor_for.side_effect = _Candidate(b"payload").descriptor_for
        candidate.encode_parameters.return_value = parameter_set
        takeover_frames = []

        def host_takeover(colors):
            takeover_frames.append(colors)
            wall._display_ownership_known = True
            wall._local_background_status = {"state": "host_full_scene"}

        with (
            mock.patch.object(
                wall, "_managed_native_background", return_value=candidate
            ),
            mock.patch.object(wall, "set_all_pixels", side_effect=host_takeover),
        ):
            activated = wall.activate_native_background(
                object(), context=_context()
            )

        self.assertFalse(activated)
        self.assertEqual(len(takeover_frames), 1)
        self.assertEqual(len(takeover_frames[0]), 33 * 138)
        self.assertTrue(wall._display_ownership_known)
        self.assertFalse(wall._native_background_active)
        self.assertEqual(wall._native_background_status["state"], "fallback")
        self.assertTrue(
            wall._native_background_status["host_full_scene_authority"]
        )
        self.assertTrue(all(
            receiver.presentation_context is not None for receiver in receivers
        ))


if __name__ == "__main__":
    unittest.main()
