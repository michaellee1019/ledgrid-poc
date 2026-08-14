"""Portable acceptance coverage for the degraded Phase 3B0 showcase."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import sys
import tempfile
from pathlib import Path
import types
import unittest

import numpy as np

if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from animation.core.presentation_contracts import OverlayFrame
from drivers.spi_controller import (
    CMD_CONFIG,
    COMMAND_ACK_POLL_INTERVAL_SECONDS,
    MAX_RGBA_PIXELS_PER_BATCH_SPAN,
    OVERLAY_UPDATE_FULL_SNAPSHOT,
)
from tools.benchmarks.phase3b_degraded_showcase import (
    ClockForegroundSource,
    CONFIRMATION_SCHEMA,
    CONFIRMATION_VERSION,
    DegradedHybridTransport,
    DESIRED_DISPLAY_SCHEMA,
    EXPECTED_CAPABILITIES,
    EXPECTED_DEVICE_MAP,
    EXPECTED_STATUS_VERSION,
    FileVisualConfirmation,
    LOCAL_PIXELS,
    Phase3BDegradedShowcase,
    RestorationSnapshot,
    ShowcaseConfig,
    ShowcaseFailure,
    WALL_PIXELS,
    evaluate_preflight,
    evaluate_write_only_host_evidence,
    validate_visual_confirmation,
)


def readable_status(logical_id: int, **changes):
    status = {
        "receiver_status_seen": True,
        "receiver_status_version": EXPECTED_STATUS_VERSION,
        "receiver_capabilities": EXPECTED_CAPABILITIES,
        "receiver_logical_device": logical_id,
        "receiver_last_result": 1,
        "receiver_last_processed_command": 0,
        "receiver_overlay_operation_result": 1,
        "receiver_overlay_committed_coverage_pixels": 0,
    }
    status.update(changes)
    return status


def write_only_status(**changes):
    status = {
        "receiver_status_seen": False,
        "receiver_status_version": 0,
        "receiver_capabilities": 0,
        "receiver_logical_device": None,
    }
    status.update(changes)
    return status


def topology():
    return [readable_status(0), readable_status(1), write_only_status(), write_only_status()]


def desired_display():
    return {
        "schema": DESIRED_DISPLAY_SCHEMA,
        "schema_version": 1,
        "revision": 9,
        "scene": {"preserved": True},
        "output": {"power": True},
    }


def confirmation(base_challenge: str, **changes):
    payload = {
        "schema": CONFIRMATION_SCHEMA,
        "schema_version": CONFIRMATION_VERSION,
        "challenge": base_challenge,
        "verdict": "pass",
        "operator": "wall observer",
        "observed_logical_devices": [0, 1, 2, 3],
        "acknowledged_unverified_devices": [2, 3],
    }
    payload.update(changes)
    return payload


RAW_SHOWCASE_STAGES = frozenset((
    "logical identity CONFIG",
    "presentation begin",
    "presentation set",
    "presentation commit",
    "local background start",
    "foreground session",
    "foreground begin",
    "foreground patch batch",
    "foreground commit",
    "foreground renew",
    "foreground clear",
))


class PreflightPolicyTests(unittest.TestCase):
    def test_exact_degraded_topology_is_loud_and_incomplete(self):
        result = evaluate_preflight(topology())
        self.assertTrue(result["passed"], result)
        self.assertFalse(result["acceptance_policy"]["telemetry_complete"])
        self.assertEqual(result["acceptance_policy"]["readable_devices"], [0, 1])
        self.assertEqual(result["acceptance_policy"]["unverified_devices"], [2, 3])
        self.assertFalse(result["receivers"]["2"]["physical_display_verified"])

    def test_wrong_receiver_count_and_non_sequence_are_rejected(self):
        for statuses in (None, topology()[:-1], topology() + [write_only_status()]):
            with self.subTest(statuses=statuses):
                result = evaluate_preflight(statuses)
                self.assertFalse(result["passed"])
                self.assertTrue(any("expected exactly 4" in item for item in result["failures"]))

    def test_every_readable_preflight_field_is_strict_for_both_receivers(self):
        mutations = (
            ("receiver_status_seen", False, "seen"),
            ("receiver_status_version", 3, "version"),
            ("receiver_capabilities", EXPECTED_CAPABILITIES & ~(1 << 5), "capabilities"),
            ("receiver_capabilities", EXPECTED_CAPABILITIES | (1 << 8), "capabilities"),
            ("receiver_logical_device", 3, "identity"),
        )
        for logical_id in (0, 1):
            for key, value, expected in mutations:
                with self.subTest(logical_id=logical_id, key=key, value=value):
                    statuses = topology()
                    statuses[logical_id] = readable_status(logical_id, **{key: value})
                    result = evaluate_preflight(statuses)
                    self.assertFalse(result["passed"])
                    self.assertTrue(any(expected in item for item in result["failures"]))

    def test_every_write_only_shape_field_is_exact_for_both_receivers(self):
        mutations = (
            ("receiver_status_seen", True),
            ("receiver_status_version", 4),
            ("receiver_capabilities", EXPECTED_CAPABILITIES),
            ("receiver_logical_device", 2),
        )
        for logical_id in (2, 3):
            for key, value in mutations:
                with self.subTest(logical_id=logical_id, key=key):
                    statuses = topology()
                    statuses[logical_id] = write_only_status(**{key: value})
                    result = evaluate_preflight(statuses)
                    self.assertFalse(result["passed"])
                    self.assertTrue(any("exact status-v0" in item for item in result["failures"]))

    def test_partial_recovery_of_one_spi1_return_path_is_not_accepted(self):
        statuses = topology()
        statuses[3] = readable_status(3)
        result = evaluate_preflight(statuses)
        self.assertFalse(result["passed"])
        self.assertTrue(any("write-only pair 2/3" in item for item in result["failures"]))


class ShowcaseConfigTests(unittest.TestCase):
    def test_foreground_poll_interval_cannot_exceed_renewal_schedule(self):
        for foreground_poll_hz in (0.5, 1.0):
            with self.subTest(foreground_poll_hz=foreground_poll_hz):
                with self.assertRaisesRegex(ValueError, "lease-renewal interval"):
                    ShowcaseConfig(
                        foreground_poll_hz=foreground_poll_hz,
                        lease_ms=3_000,
                    )

        config = ShowcaseConfig(foreground_poll_hz=2.0, lease_ms=3_000)
        self.assertEqual(config.foreground_poll_hz, 2.0)


class ConfirmationAndEvidenceTests(unittest.TestCase):
    def test_confirmation_requires_nonce_operator_all_lanes_and_unverified_ack(self):
        challenge = "fresh-nonce"
        self.assertTrue(validate_visual_confirmation(
            confirmation(challenge), challenge
        )["confirmed"])
        cases = (
            {"challenge": "stale"},
            {"verdict": "fail"},
            {"operator": ""},
            {"observed_logical_devices": [0, 1]},
            {"acknowledged_unverified_devices": []},
            {"schema_version": 2},
        )
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ShowcaseFailure):
                validate_visual_confirmation(confirmation(challenge, **changes), challenge)

    def test_write_only_evidence_requires_both_counters_and_zero_error_delta(self):
        before = {
            2: {"spi_transfers": 10, "bytes_sent": 100, "errors": 1},
            3: {"spi_transfers": 20, "bytes_sent": 200, "errors": 2},
        }
        after = {
            2: {"spi_transfers": 11, "bytes_sent": 120, "errors": 1},
            3: {"spi_transfers": 22, "bytes_sent": 240, "errors": 2},
        }
        self.assertTrue(evaluate_write_only_host_evidence(before, after)["passed"])
        failures = (
            (2, "spi_transfers", 10, "transfers did not advance"),
            (2, "bytes_sent", 100, "bytes did not advance"),
            (3, "errors", 3, "errors changed"),
        )
        for logical_id, key, value, expected in failures:
            with self.subTest(logical_id=logical_id, key=key):
                changed = deepcopy(after)
                changed[logical_id][key] = value
                result = evaluate_write_only_host_evidence(before, changed)
                self.assertFalse(result["passed"])
                self.assertTrue(any(expected in item for item in result["failures"]))

    def test_real_showcase_clock_has_nonzero_alpha_coverage_on_every_lane(self):
        clock = FakeClock()
        controller = FakeController([], clock)
        source = ClockForegroundSource(controller)
        source.animation._clock_now = lambda: datetime(
            2026, 8, 12, 13, 47, 10, tzinfo=timezone.utc
        )

        frame = source.render(0.0, 0)
        coverage = [
            int(np.count_nonzero(
                frame.pixels[index * LOCAL_PIXELS:(index + 1) * LOCAL_PIXELS, 3]
            ))
            for index in range(4)
        ]

        self.assertEqual(coverage, [19, 65, 62, 12])
        self.assertTrue(all(count > 0 for count in coverage))

    def test_delta_patch_split_advances_once_without_duplicates_or_gaps(self):
        pixels = np.arange(LOCAL_PIXELS * 4, dtype=np.uint16).reshape(
            LOCAL_PIXELS, 4
        ).astype(np.uint8)
        wall = np.zeros((WALL_PIXELS, 4), dtype=np.uint8)
        wall[:LOCAL_PIXELS] = pixels

        patches = DegradedHybridTransport._local_patches(
            wall,
            0,
            full_snapshot=False,
            dirty_ranges=((0, LOCAL_PIXELS),),
        )

        self.assertEqual(
            [start for start, _pixels in patches],
            [0, MAX_RGBA_PIXELS_PER_BATCH_SPAN],
        )
        self.assertEqual(sum(len(data) for _start, data in patches), LOCAL_PIXELS)
        np.testing.assert_array_equal(
            np.concatenate([data for _start, data in patches]), pixels
        )


class FileVisualConfirmationTests(unittest.TestCase):
    def test_poll_interval_must_be_positive_and_finite(self):
        for value in (True, 0.0, -0.1, float("inf"), float("nan")):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with self.assertRaisesRegex(ValueError, "poll interval"):
                    FileVisualConfirmation(
                        root / "challenge.json",
                        root / "response.json",
                        timeout=2.0,
                        poll_interval=value,
                    )

    def test_begin_publishes_challenge_and_poll_is_nonblocking(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exchange = FileVisualConfirmation(
                root / "challenge.json",
                root / "response.json",
                timeout=2.0,
                clock=clock,
            )

            exchange.begin("live-challenge")

            challenge = (root / "challenge.json").read_text(encoding="utf-8")
            self.assertIn("live-challenge", challenge)
            self.assertIsNone(exchange.poll())
            self.assertEqual(clock.now, 10.0)

    def test_poll_rejects_malformed_response_and_times_out_boundedly(self):
        for malformed in (True, False):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory() as temporary:
                clock = FakeClock()
                root = Path(temporary)
                response = root / "response.json"
                exchange = FileVisualConfirmation(
                    root / "challenge.json",
                    response,
                    timeout=0.2,
                    clock=clock,
                )
                exchange.begin("live-challenge")
                if malformed:
                    response.write_text("{", encoding="utf-8")
                else:
                    clock.sleep(0.2)

                with self.assertRaises(ShowcaseFailure) as raised:
                    exchange.poll()

                self.assertIn(
                    "malformed" if malformed else "timed out",
                    str(raised.exception),
                )

    def test_response_at_deadline_is_rejected_even_when_file_exists(self):
        clock = FakeClock()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            response = root / "response.json"
            exchange = FileVisualConfirmation(
                root / "challenge.json",
                response,
                timeout=0.2,
                clock=clock,
            )
            exchange.begin("live-challenge")
            response.write_text(
                json.dumps(confirmation("live-challenge")), encoding="utf-8"
            )
            clock.sleep(0.2)

            with self.assertRaisesRegex(ShowcaseFailure, "timed out"):
                exchange.poll()


class FakeClock:
    def __init__(self):
        self.now = 10.0
        self.sleep_calls = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        self.now += seconds


class FakeDevice:
    def __init__(
        self, logical_id: int, events: list, clock: FakeClock, *, write_only: bool
    ):
        self.device_index = logical_id
        self.logical_id = logical_id
        self.configured_logical_id = 0xFF if write_only else logical_id
        self.bus, self.device = EXPECTED_DEVICE_MAP[logical_id]
        self.events = events
        self.clock = clock
        self.write_only = write_only
        self.status_version = EXPECTED_STATUS_VERSION
        self.fail = set()
        self.track_writes = True
        self.spi_transfers = 0
        self.bytes_sent = 0
        self.errors = 0
        self.base_mode = 2
        self.context = None
        self.session = None
        self.foreground_state = 0
        self.committed_generation = 0
        self.staged_generation = 0
        self.scene_revision = 0
        self.scene_epoch = 0
        self.base_revision = 0
        self.present_at = 0
        self.last_processed_command = 0
        self.runtime_rejections = 0
        self.overlay_plane = np.zeros((LOCAL_PIXELS, 4), dtype=np.uint8)
        self.staging_plane = self.overlay_plane.copy()
        self.committed_coverage = 0
        self.staged_lease_ms = 0
        self.lease_deadline = None
        self.renewals = 0

    def _touch(self, stage: str, byte_count: int = 1):
        self.events.append((stage, self.device_index))
        if stage in self.fail:
            raise OSError(f"{stage} failed")
        if self.track_writes:
            self.spi_transfers += 1
            self.bytes_sent += byte_count

    def _expire_if_due(self):
        if (
            self.foreground_state == 2
            and self.lease_deadline is not None
            and self.clock.now >= self.lease_deadline
        ):
            self.foreground_state = 0
            self.committed_coverage = 0
            self.overlay_plane.fill(0)
            self.lease_deadline = None

    def _status(self):
        self._expire_if_due()
        if self.write_only:
            return write_only_status(
                spi_transfers=self.spi_transfers,
                bytes_sent=self.bytes_sent,
                errors=self.errors,
            )
        lease_remaining_ms = 0
        if self.lease_deadline is not None:
            lease_remaining_ms = max(
                0, int(round((self.lease_deadline - self.clock.now) * 1000.0))
            )
        result = readable_status(
            self.logical_id,
            receiver_status_version=self.status_version,
        )
        result.update({
            "receiver_base_mode": self.base_mode,
            "receiver_component_id": 1,
            "receiver_declared_cadence_hz": 30,
            "receiver_global_strip_offset": self.logical_id * 8,
            "receiver_common_seed": 0x3B00CAFE,
            "receiver_scene_epoch": self.context.scene_epoch if self.context else 0,
            "receiver_active_scene_revision": self.context.scene_revision if self.context else 0,
            "receiver_active_context_digest": self.context.context_digest.hex() if self.context else None,
            "receiver_active_session_id": self.context.controller_session_id.hex() if self.context else None,
            "receiver_foreground_state": self.foreground_state,
            "receiver_overlay_committed_generation": self.committed_generation,
            "receiver_overlay_staged_generation": self.staged_generation,
            "receiver_overlay_session_id": self.session.hex() if self.session else None,
            "receiver_foreground_scene_revision": self.scene_revision,
            "receiver_foreground_scene_epoch": self.scene_epoch,
            "receiver_foreground_base_revision": self.base_revision,
            "receiver_foreground_present_at_scene_time_us": self.present_at,
            "receiver_last_processed_command": self.last_processed_command,
            "receiver_overlay_committed_coverage_pixels": self.committed_coverage,
            "receiver_overlay_lease_ms": self.staged_lease_ms,
            "receiver_overlay_lease_remaining_ms": lease_remaining_ms,
        })
        return result

    def query_receiver_status(self):
        self._touch("query")
        return self._status()

    def get_stats(self):
        result = self._status()
        result.update({
            "spi_transfers": self.spi_transfers,
            "bytes_sent": self.bytes_sent,
            "errors": self.errors,
        })
        return result

    def _command_status(self, payload, *, required_status_version):
        packet = bytes(payload)
        self._touch("logical identity CONFIG", len(packet))
        if (
            required_status_version != EXPECTED_STATUS_VERSION
            or len(packet) != 6
            or packet[0] != CMD_CONFIG
            or packet[1:5] != bytes((8, 0, 138, 0))
            or packet[5] != self.device_index
        ):
            raise RuntimeError("malformed explicit identity CONFIG")
        self.configured_logical_id = packet[5]
        self.logical_id = packet[5]
        self.last_processed_command = CMD_CONFIG
        return self._status()

    def begin_presentation_context(self, context):
        self._touch("presentation begin")
        self.context = context
        return self._status()

    def set_presentation_context(self, context):
        self._touch("presentation set")
        self.context = context
        return self._status()

    def commit_presentation_context(self, context, host_monotonic_anchor_ns):
        del host_monotonic_anchor_ns
        self._touch("presentation commit")
        self.context = context
        return self._status()

    def start_local_background(self, **kwargs):
        self._touch("local background start")
        self.base_mode = 1
        return self._status()

    def begin_controller_session(self, **kwargs):
        self._touch("foreground session")
        self.session = kwargs["controller_session_id"]
        return self._status()

    def begin_overlay(self, **kwargs):
        self._touch("foreground begin")
        self.foreground_state = 1
        self.scene_revision = kwargs["scene_revision"]
        self.scene_epoch = kwargs["scene_epoch"]
        self.base_revision = kwargs["base_revision"]
        self.staged_lease_ms = kwargs["lease_ms"]
        self.staging_plane = (
            np.zeros_like(self.overlay_plane)
            if kwargs["update_kind"] == OVERLAY_UPDATE_FULL_SNAPSHOT
            else self.overlay_plane.copy()
        )
        return self._status()

    def send_overlay_patches(self, **kwargs):
        self._touch("foreground patch batch")
        for start, pixels in kwargs["patches"]:
            data = np.asarray(pixels, dtype=np.uint8)
            self.staging_plane[start:start + len(data)] = data
        return [self._status()]

    def commit_overlay(self, **kwargs):
        self._touch("foreground commit")
        self.foreground_state = 2
        self.committed_generation = kwargs["generation"]
        self.staged_generation = 0
        self.present_at = kwargs["present_at_scene_time_us"]
        self.overlay_plane = self.staging_plane.copy()
        self.committed_coverage = int(
            np.count_nonzero(self.overlay_plane[:, 3])
        )
        self.lease_deadline = self.clock.now + self.staged_lease_ms / 1000.0
        return self._status()

    def renew_overlay(self, **kwargs):
        self._touch("foreground renew")
        self._expire_if_due()
        if self.foreground_state != 2:
            raise RuntimeError("foreground already expired")
        self.lease_deadline = self.clock.now + kwargs["lease_ms"] / 1000.0
        self.renewals += 1
        return self._status()

    def clear_overlay(self, **kwargs):
        self._touch("foreground clear")
        self.foreground_state = 0
        self.committed_generation = kwargs["generation"]
        self.committed_coverage = 0
        self.overlay_plane.fill(0)
        return self._status()

    def write_only_packet(self, stage, payload):
        packet = bytes(payload)
        self._touch(stage, len(packet))
        if stage == "logical identity CONFIG":
            if (
                len(packet) != 6
                or packet[0] != CMD_CONFIG
                or packet[1:5] != bytes((8, 0, 138, 0))
                or packet[5] != self.device_index
            ):
                raise RuntimeError("malformed raw identity CONFIG")
            self.configured_logical_id = packet[5]
            return
        if self.configured_logical_id not in range(4):
            self.runtime_rejections += 1
            self.events.append(("runtime rejected", self.device_index, stage))
            return
        if stage == "local background start":
            self.base_mode = 1
        elif stage == "foreground session":
            self.session = packet[2:18]
        elif stage == "foreground begin":
            self.foreground_state = 1
            self.staged_lease_ms = int.from_bytes(packet[-4:], "big")
            update_kind = packet[-7]
            self.staging_plane = (
                np.zeros_like(self.overlay_plane)
                if update_kind == OVERLAY_UPDATE_FULL_SNAPSHOT
                else self.overlay_plane.copy()
            )
        elif stage == "foreground patch batch":
            span_count = int.from_bytes(packet[26:28], "big")
            offset = 28
            for _ in range(span_count):
                start = int.from_bytes(packet[offset:offset + 2], "big")
                count = int.from_bytes(packet[offset + 2:offset + 4], "big")
                offset += 4
                byte_count = count * 4
                rgba = np.frombuffer(
                    packet[offset:offset + byte_count], dtype=np.uint8
                ).reshape(count, 4)
                self.staging_plane[start:start + count] = rgba
                offset += byte_count
            if offset != len(packet):
                raise RuntimeError("malformed raw foreground batch")
        elif stage == "foreground commit":
            self.foreground_state = 2
            self.overlay_plane = self.staging_plane.copy()
            self.committed_coverage = int(
                np.count_nonzero(self.overlay_plane[:, 3])
            )
            self.lease_deadline = self.clock.now + self.staged_lease_ms / 1000.0
        elif stage == "foreground renew":
            self._expire_if_due()
            if self.foreground_state != 2:
                self.runtime_rejections += 1
                return
            lease_ms = int.from_bytes(packet[-4:], "big")
            self.lease_deadline = self.clock.now + lease_ms / 1000.0
            self.renewals += 1
        elif stage == "foreground clear":
            self.foreground_state = 0
            self.committed_coverage = 0
            self.overlay_plane.fill(0)

    def set_all_pixels(self, frame):
        self._touch("set_all", np.asarray(frame).nbytes)
        self.base_mode = 2
        self.foreground_state = 0
        self.committed_coverage = 0
        self.overlay_plane.fill(0)
        self.lease_deadline = None


class FakeController:
    num_devices = 4
    strips_per_device = 8
    leds_per_strip = 138
    strip_count = 32
    total_leds = WALL_PIXELS
    device_map = list(EXPECTED_DEVICE_MAP)

    def __init__(self, events, clock):
        self.events = events
        self.devices = [
            FakeDevice(index, events, clock, write_only=index >= 2)
            for index in range(4)
        ]
        self.fail_close = False

    def close(self):
        self.events.append(("controller close",))
        if self.fail_close:
            raise OSError("close failed")


class FakeSource:
    def __init__(self, controller, events, *, fail_start=False, fail_render=False,
                 fail_stop=False, fail_cleanup=False, visibility_loss=None):
        self.controller = controller
        self.events = events
        self.fail_start = fail_start
        self.fail_render = fail_render
        self.fail_stop = fail_stop
        self.fail_cleanup = fail_cleanup
        self.visibility_loss = visibility_loss
        self.pixels = np.zeros((WALL_PIXELS, 4), dtype=np.uint8)
        for logical_id in range(4):
            self.pixels[logical_id * LOCAL_PIXELS] = (8, 4, 2, 8)

    def start(self):
        self.events.append(("source start",))
        if self.fail_start:
            raise RuntimeError("source start failed")

    def render(self, elapsed, frame_count):
        del elapsed
        self.events.append(("source render", frame_count))
        if self.fail_render:
            raise RuntimeError("source render failed")
        if frame_count >= 1 and self.visibility_loss is not None:
            readable = self.controller.devices[0]
            if self.visibility_loss == "state":
                readable.foreground_state = 0
            elif self.visibility_loss == "coverage":
                readable.committed_coverage = 0
            elif self.visibility_loss == "status":
                readable.status_version = 3
            elif self.visibility_loss == "identity":
                readable.logical_id = 3
            elif self.visibility_loss == "session":
                readable.session = b"x" * 16
            elif self.visibility_loss == "generation":
                readable.committed_generation += 1
            elif self.visibility_loss == "lease":
                readable.lease_deadline = readable.clock.now
            else:
                raise AssertionError("unknown visibility loss")
        return OverlayFrame(
            self.pixels,
            revision=1,
            changed=frame_count == 0,
            dirty_ranges=None if frame_count == 0 else (),
        )

    def stop(self):
        self.events.append(("source stop",))
        if self.fail_stop:
            raise RuntimeError("source stop failed")

    def cleanup(self):
        self.events.append(("source cleanup",))
        if self.fail_cleanup:
            raise RuntimeError("source cleanup failed")


class ShowcaseRunnerTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.clock = FakeClock()
        self.controller = FakeController(self.events, self.clock)
        self.frame = np.arange(WALL_PIXELS * 3, dtype=np.uint16).reshape(
            WALL_PIXELS, 3
        ).astype(np.uint8)
        self.snapshot = RestorationSnapshot(desired_display(), self.frame)
        self.restored_states = []

    def runner(self, *, source_options=None, confirmation_provider=None,
               restorer=None, controller_factory=None):
        options = dict(source_options or {})
        return Phase3BDegradedShowcase(
            ShowcaseConfig(duration_seconds=0.21, foreground_poll_hz=10.0),
            self.snapshot,
            controller_factory=controller_factory or (lambda: self.controller),
            restore_desired_display=restorer or self._restore,
            confirmation_provider=confirmation_provider or (
                lambda challenge: confirmation(challenge)
            ),
            frame_source_factory=lambda controller: FakeSource(
                controller, self.events, **options
            ),
            clock=self.clock,
            monotonic_ns=lambda: 123456,
            sleeper=self._sleep,
            session_factory=lambda count: bytes(range(count)),
            challenge_factory=lambda _count: "fresh-challenge",
        )

    def _sleep(self, seconds):
        self.events.append(("sleep", seconds))
        self.clock.sleep(seconds)

    def _restore(self, state):
        self.events.append(("desired restore",))
        self.restored_states.append(deepcopy(state))

    def assert_exact_cleanup(self, result):
        self.assertTrue(result["complete_host_frame_restored"], result)
        self.assertTrue(result["desired_display_restored"], result)
        self.assertEqual(self.restored_states, [desired_display()])
        for device in self.controller.devices:
            self.assertEqual(device.base_mode, 2)
            self.assertEqual(device.foreground_state, 0)
            self.assertEqual(device.committed_coverage, 0)
        set_all_positions = [
            index for index, event in enumerate(self.events) if event[0] == "set_all"
        ]
        restore_position = self.events.index(("desired restore",))
        self.assertEqual(len(set_all_positions), 4)
        self.assertLess(max(set_all_positions), restore_position)

    def test_success_writes_all_four_but_claims_only_readable_receiver_proof(self):
        result = self.runner().run()
        self.assertTrue(result["passed"], result)
        self.assertFalse(result["acceptance_policy"]["telemetry_complete"])
        self.assertEqual(result["acceptance_policy"]["unverified_devices"], [2, 3])
        self.assertTrue(result["visual_confirmation"]["confirmed"])
        self.assertTrue(result["initial_write_only_host_evidence"]["passed"])
        self.assertTrue(result["write_only_host_evidence"]["passed"])
        self.assertFalse(
            result["artifact_policy"]["cached_artifact_operations_allowed"]
        )
        identity = result["identity_configuration"]
        self.assertTrue(identity["devices"]["0"]["logical_identity_verified"])
        self.assertFalse(identity["devices"]["2"]["logical_identity_verified"])
        self.assertIn("unverified", identity["devices"]["2"]["warning"])
        visibility = result["foreground_visibility_at_confirmation"]
        self.assertTrue(visibility["all_lanes_expected_nonzero"])
        self.assertEqual(
            visibility["expected_alpha_coverage_by_receiver"],
            {"0": 1, "1": 1, "2": 1, "3": 1},
        )
        self.assertTrue(visibility["sampled_at_pass_boundary"])
        for logical_id in ("0", "1"):
            readable = visibility["readable_receivers"][logical_id]
            self.assertTrue(readable["status_v4_exact"])
            self.assertTrue(readable["logical_identity_exact"])
            self.assertTrue(readable["session_exact"])
            self.assertTrue(readable["generation_exact"])
            self.assertTrue(readable["lease_remaining_positive"])
            self.assertTrue(readable["coverage_exact"])
        for logical_id in (0, 1, 2, 3):
            self.assertIn(("logical identity CONFIG", logical_id), self.events)
            self.assertIn(("local background start", logical_id), self.events)
            self.assertIn(("foreground commit", logical_id), self.events)
            self.assertLess(
                self.events.index(("logical identity CONFIG", logical_id)),
                self.events.index(("presentation begin", logical_id)),
            )
        for position, event in enumerate(self.events[:-1]):
            if (
                len(event) >= 2
                and event[0] in RAW_SHOWCASE_STAGES
                and event[1] in (2, 3)
            ):
                self.assertEqual(
                    self.events[position + 1],
                    ("sleep", COMMAND_ACK_POLL_INTERVAL_SECONDS),
                    (position, event, self.events[position + 1]),
                )
        self.assert_exact_cleanup(result)

    def test_raw_runtime_is_rejected_until_explicit_identity_config(self):
        transport = DegradedHybridTransport(
            self.controller, sleeper=self._sleep
        )
        device = self.controller.devices[2]

        transport._write_only_packet(
            device, "local background start", bytes((0x30,))
        )

        self.assertEqual(device.runtime_rejections, 1)
        self.assertEqual(device.base_mode, 2)
        configured = transport.configure_logical_identities()
        transport._write_only_packet(
            device, "local background start", bytes((0x30,))
        )
        self.assertEqual(device.configured_logical_id, 2)
        self.assertEqual(device.runtime_rejections, 1)
        self.assertEqual(device.base_mode, 1)
        self.assertFalse(configured["devices"]["2"]["logical_identity_verified"])

    def test_delayed_live_confirmation_keeps_foreground_renewed_past_lease(self):
        clock = self.clock
        controller = self.controller

        class DelayedConfirmation:
            def __init__(self):
                self.challenge = None
                self.started = None
                self.active_at_confirmation = None

            def begin(self, challenge):
                self.challenge = challenge
                self.started = clock.now

            def poll(self):
                if clock.now - self.started < 3.5:
                    return None
                for device in controller.devices:
                    device._expire_if_due()
                self.active_at_confirmation = [
                    device._status()["receiver_foreground_state"]
                    if not device.write_only
                    else device.foreground_state
                    for device in controller.devices
                ]
                return confirmation(self.challenge)

        exchange = DelayedConfirmation()

        result = self.runner(confirmation_provider=exchange).run()

        self.assertTrue(result["passed"], result)
        self.assertEqual(
            result["confirmation_exchange"]["mode"], "live_nonblocking_exchange"
        )
        self.assertGreaterEqual(
            result["confirmation_exchange"]["confirmed_elapsed_seconds"], 3.5
        )
        self.assertEqual(exchange.active_at_confirmation, [2, 2, 2, 2])
        renewals = [device.renewals for device in controller.devices]
        self.assertTrue(all(count >= 2 for count in renewals), renewals)

    def test_early_confirmation_cannot_hide_pass_boundary_visibility_loss(self):
        for visibility_loss, expected in (
            ("state", "foreground_active"),
            ("coverage", "coverage_exact"),
            ("status", "status_v4_exact"),
            ("identity", "logical_identity_exact"),
            ("session", "session_exact"),
            ("generation", "generation_exact"),
            ("lease", "lease_remaining_positive"),
        ):
            with self.subTest(visibility_loss=visibility_loss):
                self.setUp()
                result = self.runner(source_options={
                    "visibility_loss": visibility_loss,
                }).run()

                self.assertFalse(result["passed"], result)
                self.assertTrue(result["visual_confirmation"]["confirmed"])
                self.assertIn("pass boundary", result["failure"])
                self.assertIn(expected, result["failure"])
                self.assertGreaterEqual(
                    result["confirmation_exchange"][
                        "pass_boundary_elapsed_seconds"
                    ],
                    0.21,
                )
                self.assert_exact_cleanup(result)

    def test_preflight_failure_is_observation_only(self):
        self.controller.devices[1].logical_id = 2
        result = self.runner().run()
        self.assertFalse(result["passed"])
        self.assertIn("topology preflight", result["failure"])
        self.assertNotIn(("source start",), self.events)
        self.assertTrue(result["preflight_non_mutating"])
        self.assertFalse(result["complete_host_frame_restored"])
        self.assertFalse(result["desired_display_restored"])
        self.assertEqual(self.restored_states, [])
        self.assertFalse(any(event[0] == "set_all" for event in self.events))

    def test_each_mutating_body_boundary_fails_closed_and_restores(self):
        stages = (
            (0, "logical identity CONFIG"),
            (2, "logical identity CONFIG"),
            (0, "presentation begin"),
            (1, "presentation set"),
            (2, "presentation commit"),
            (3, "local background start"),
            (0, "foreground session"),
            (1, "foreground begin"),
            (2, "foreground patch batch"),
            (3, "foreground commit"),
        )
        for logical_id, stage in stages:
            with self.subTest(logical_id=logical_id, stage=stage):
                self.setUp()
                self.controller.devices[logical_id].fail.add(stage)
                result = self.runner().run()
                self.assertFalse(result["passed"], result)
                self.assertIn("failed", result["failure"])
                self.assert_exact_cleanup(result)

    def test_source_start_and_render_failures_restore(self):
        for option in ("fail_start", "fail_render"):
            with self.subTest(option=option):
                self.setUp()
                result = self.runner(source_options={option: True}).run()
                self.assertFalse(result["passed"])
                self.assertIn("source", result["failure"])
                self.assert_exact_cleanup(result)

    def test_missing_write_only_host_evidence_fails_before_confirmation(self):
        self.controller.devices[2].track_writes = False
        confirmation_calls = []

        def confirm(challenge):
            confirmation_calls.append(challenge)
            return confirmation(challenge)

        result = self.runner(confirmation_provider=confirm).run()
        self.assertFalse(result["passed"])
        self.assertIn("did not advance", result["failure"])
        self.assertFalse(result["initial_write_only_host_evidence"]["passed"])
        self.assertEqual(confirmation_calls, [])
        self.assertNotIn("confirmation_exchange", result)
        self.assertNotIn("visual_confirmation", result)
        self.assert_exact_cleanup(result)

    def test_malformed_or_timed_out_live_confirmation_restores_exactly(self):
        for failure_mode in ("malformed", "timeout"):
            with self.subTest(failure_mode=failure_mode), tempfile.TemporaryDirectory() as temporary:
                self.setUp()
                root = Path(temporary)
                response = root / "response.json"

                class Exchange(FileVisualConfirmation):
                    def begin(inner_self, challenge):
                        super().begin(challenge)
                        if failure_mode == "malformed":
                            response.write_text("{", encoding="utf-8")

                exchange = Exchange(
                    root / "challenge.json",
                    response,
                    timeout=0.15,
                    clock=self.clock,
                )

                result = self.runner(confirmation_provider=exchange).run()

                self.assertFalse(result["passed"], result)
                self.assertIn(
                    "malformed" if failure_mode == "malformed" else "timed out",
                    result["failure"],
                )
                self.assert_exact_cleanup(result)

    def test_live_confirmation_waits_respect_poll_and_deadline_caps(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            exchange = FileVisualConfirmation(
                root / "challenge.json",
                root / "response.json",
                timeout=0.055,
                poll_interval=0.02,
                clock=self.clock,
            )

            result = self.runner(confirmation_provider=exchange).run()

        self.assertFalse(result["passed"], result)
        self.assertIn("timed out", result["failure"])
        runner_waits = [
            event[1]
            for event in self.events
            if event[0] == "sleep"
            and event[1] != COMMAND_ACK_POLL_INTERVAL_SECONDS
        ]
        self.assertTrue(runner_waits)
        self.assertLessEqual(max(runner_waits), 0.02)
        self.assertTrue(any(wait < 0.02 for wait in runner_waits), runner_waits)
        self.assert_exact_cleanup(result)

    def test_rejected_visual_confirmation_fails_and_restores(self):
        result = self.runner(
            confirmation_provider=lambda challenge: confirmation(
                challenge, verdict="fail"
            )
        ).run()
        self.assertFalse(result["passed"])
        self.assertIn("visual confirmation rejected", result["failure"])
        self.assert_exact_cleanup(result)

    def test_source_stop_and_cleanup_failures_cannot_report_pass(self):
        for option, expected in (
            ("fail_stop", "clock source stop"),
            ("fail_cleanup", "clock source cleanup"),
        ):
            with self.subTest(option=option):
                self.setUp()
                result = self.runner(source_options={option: True}).run()
                self.assertFalse(result["passed"])
                self.assertTrue(any(expected in item for item in result["cleanup_failures"]))
                self.assert_exact_cleanup(result)

    def test_complete_frame_failure_still_attempts_desired_state_restore(self):
        self.controller.devices[2].fail.add("set_all")
        result = self.runner().run()
        self.assertFalse(result["passed"])
        self.assertFalse(result["complete_host_frame_restored"])
        self.assertTrue(result["desired_display_restored"])
        self.assertEqual(self.restored_states, [desired_display()])

    def test_desired_state_restore_failure_is_reported_after_complete_takeover(self):
        def fail_restore(_state):
            self.events.append(("desired restore",))
            raise OSError("state restore failed")

        result = self.runner(restorer=fail_restore).run()
        self.assertFalse(result["passed"])
        self.assertTrue(result["complete_host_frame_restored"])
        self.assertFalse(result["desired_display_restored"])
        self.assertTrue(any("desired display restore" in item
                            for item in result["cleanup_failures"]))

    def test_controller_close_failure_cannot_report_pass(self):
        self.controller.fail_close = True
        result = self.runner().run()
        self.assertFalse(result["passed"])
        self.assertTrue(any("controller close" in item for item in result["cleanup_failures"]))
        self.assertEqual(self.restored_states, [desired_display()])

    def test_controller_open_failure_does_not_rewrite_persisted_state(self):
        def fail_open():
            raise OSError("SPI unavailable")

        result = self.runner(controller_factory=fail_open).run()
        self.assertFalse(result["passed"])
        self.assertIn("SPI unavailable", result["failure"])
        self.assertFalse(result["complete_host_frame_restored"])
        self.assertFalse(result["desired_display_restored"])
        self.assertEqual(self.restored_states, [])


class RestorationValidationTests(unittest.TestCase):
    def test_snapshot_rejects_wrong_schema_geometry_and_dtype(self):
        frame = np.zeros((WALL_PIXELS, 3), dtype=np.uint8)
        cases = (
            ({**desired_display(), "schema_version": 2}, frame, ValueError),
            (desired_display(), frame[:-1], ValueError),
            (desired_display(), frame.astype(np.int16), TypeError),
        )
        for state, candidate, exception in cases:
            with self.subTest(exception=exception), self.assertRaises(exception):
                RestorationSnapshot(state, candidate)

    def test_snapshot_detaches_state_and_frame(self):
        state = desired_display()
        frame = np.zeros((WALL_PIXELS, 3), dtype=np.uint8)
        snapshot = RestorationSnapshot(state, frame)
        state["revision"] = 99
        frame[0] = 255
        self.assertEqual(snapshot.desired_display["revision"], 9)
        np.testing.assert_array_equal(snapshot.complete_host_frame[0], (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
