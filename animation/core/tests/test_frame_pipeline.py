import binascii
import colorsys
from collections import deque
import sys
import threading
import time
import types
import unittest
import unittest.mock
from concurrent.futures import ThreadPoolExecutor

import numpy as np


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from animation.core.base import AnimationBase, RenderedFrame
from animation.core.manager import (
    FRAME_DEADLINE_COARSE_WINDOW_SECONDS,
    FRAME_SCHEDULER_HEADROOM_RATIO,
    AnimationManager,
    _plan_frame_deadline,
    _wait_for_frame_deadline,
)
from animation.plugins.rainbow import RainbowAnimation
from animation.plugins.solid import SolidColorAnimation
from drivers.multi_device import MultiDeviceLEDController
from drivers.spi_controller import CRC_BYTES, LEDController
from drivers.spi_controller import RECEIVER_STATUS_BYTES
from drivers.spi_controller import RECEIVER_STATUS_BYTES_V2


class _Controller:
    strip_count = 4
    leds_per_strip = 5
    total_leds = strip_count * leds_per_strip


class _Animation(AnimationBase):
    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()


class FrameContractTests(unittest.TestCase):
    def test_absolute_deadlines_pay_back_repeated_sleep_overshoot(self):
        now = 0.0
        deadline = None
        prior_target = None
        frame_starts = []
        target_fps = 150
        overshoot = 0.0004

        for _ in range(151):
            frame_started = now
            frame_starts.append(frame_started)
            work_finished = frame_started + 0.002
            deadline, sleep_for = _plan_frame_deadline(
                deadline,
                prior_target,
                frame_started=frame_started,
                work_finished=work_finished,
                target_fps=target_fps,
            )
            prior_target = target_fps
            now = work_finished + sleep_for + (overshoot if sleep_for else 0.0)

        elapsed = frame_starts[-1] - frame_starts[0]
        self.assertGreaterEqual(150 / elapsed, 157.0)
        relative_elapsed = 150 * ((1.0 / target_fps) + overshoot)
        self.assertLess(150 / relative_elapsed, 142.0)

    def test_internal_headroom_runs_inside_configured_frame_budget(self):
        deadline, sleep_for = _plan_frame_deadline(
            None,
            None,
            frame_started=0.0,
            work_finished=0.002,
            target_fps=150,
        )

        expected = (1.0 - FRAME_SCHEDULER_HEADROOM_RATIO) / 150
        self.assertAlmostEqual(deadline, expected)
        self.assertAlmostEqual(sleep_for, expected - 0.002)
        self.assertLess(deadline, 1.0 / 150)

    def test_internal_headroom_requests_reliable_150_fps_cadence(self):
        deadline, _ = _plan_frame_deadline(
            None,
            None,
            frame_started=0.0,
            work_finished=0.0,
            target_fps=150,
        )

        self.assertAlmostEqual(FRAME_SCHEDULER_HEADROOM_RATIO, 0.05)
        self.assertAlmostEqual(1.0 / deadline, 150 / 0.95)
        self.assertGreaterEqual(1.0 / deadline, 157.0)

    def test_internal_headroom_never_exceeds_200_fps_ceiling(self):
        deadline, sleep_for = _plan_frame_deadline(
            None,
            None,
            frame_started=1.0,
            work_finished=1.001,
            target_fps=200,
        )

        self.assertAlmostEqual(deadline, 1.005)
        self.assertAlmostEqual(sleep_for, 0.004)

    def test_precision_wait_coarse_sleeps_then_spins_at_boundary(self):
        deadline = 2.0
        readings = iter((
            1.997,
            1.9996,
            1.9998,
            1.99995,
            2.000001,
        ))
        sleeps = []

        observed = _wait_for_frame_deadline(
            deadline,
            clock=lambda: next(readings),
            sleeper=sleeps.append,
        )

        self.assertAlmostEqual(
            sleeps[0], 0.003 - FRAME_DEADLINE_COARSE_WINDOW_SECONDS
        )
        self.assertGreaterEqual(observed, deadline)
        self.assertLess(observed - deadline, 0.00001)

    def test_precision_wait_does_not_sleep_or_spin_when_behind(self):
        calls = []

        observed = _wait_for_frame_deadline(
            2.0,
            clock=lambda: calls.append(True) or 2.001,
            sleeper=lambda _seconds: self.fail("late frame must not sleep"),
        )

        self.assertEqual(observed, 2.001)
        self.assertEqual(calls, [True])

    def test_precision_wait_yields_between_coarse_and_spin_windows(self):
        readings = iter((0.0, 0.0015, 0.0026, 0.003001))
        sleeps = []

        observed = _wait_for_frame_deadline(
            0.003,
            clock=lambda: next(readings),
            sleeper=sleeps.append,
        )

        self.assertEqual(len(sleeps), 2)
        self.assertAlmostEqual(
            sleeps[0], 0.003 - FRAME_DEADLINE_COARSE_WINDOW_SECONDS
        )
        self.assertEqual(sleeps[1], 0)
        self.assertGreaterEqual(observed, 0.003)

    def test_absolute_deadlines_skip_expired_periods_without_bursting(self):
        period = 1.0 / 150
        deadline, sleep_for = _plan_frame_deadline(
            None,
            None,
            frame_started=0.0,
            work_finished=0.030,
            target_fps=150,
        )

        self.assertGreater(deadline, 0.030)
        self.assertGreater(sleep_for, 0.0)
        self.assertLessEqual(sleep_for, period)

    def test_live_target_change_starts_new_cadence_epoch(self):
        deadline, _ = _plan_frame_deadline(
            None,
            None,
            frame_started=1.0,
            work_finished=1.002,
            target_fps=100,
        )
        changed_deadline, sleep_for = _plan_frame_deadline(
            deadline,
            100,
            frame_started=1.01,
            work_finished=1.012,
            target_fps=200,
        )

        self.assertAlmostEqual(changed_deadline, 1.015)
        self.assertAlmostEqual(sleep_for, 0.003)

    def test_manager_bounds_live_target_fps(self):
        manager = AnimationManager.__new__(AnimationManager)
        self.assertEqual(manager.set_target_fps(160), 160)
        self.assertEqual(manager.set_target_fps(999), 200)
        self.assertEqual(manager.set_target_fps(0), 1)

    def test_performance_summary_retains_maximum_for_qualification(self):
        manager = AnimationManager.__new__(AnimationManager)
        manager.target_fps = 100
        manager.controller = type("Controller", (), {"inline_show": True})()
        manager.frames_presented = 3
        manager.unchanged_frames_skipped = 1
        manager.perf_lock = threading.Lock()
        manager.perf_samples = deque((
            {
                "generate": 0.001,
                "send": 0.002,
                "show": 0.003,
                "process": 0.004,
                "target_period": 0.010,
                "deadline_missed": False,
                "sleep": 0.005,
                "requested_sleep": 0.0045,
                "deadline_lateness": 0.0005,
                "frame": 0.006,
            },
            {
                "generate": 0.002,
                "send": 0.003,
                "show": 0.004,
                "process": 0.005,
                "target_period": 0.010,
                "deadline_missed": False,
                "sleep": 0.006,
                "requested_sleep": 0.0055,
                "deadline_lateness": 0.0005,
                "frame": 0.007,
            },
        ))
        manager._last_perf_sample = manager.perf_samples[-1]

        summary = manager._get_perf_summary()

        self.assertEqual(summary["max_generate_ms"], 2.0)
        self.assertEqual(summary["max_frame_ms"], 7.0)
        self.assertAlmostEqual(summary["avg_requested_sleep_ms"], 5.0)
        self.assertEqual(summary["max_deadline_lateness_ms"], 0.5)
        self.assertGreaterEqual(summary["max_frame_ms"], summary["p99_frame_ms"])

    def test_performance_summary_does_not_reclassify_mixed_target_samples(self):
        manager = AnimationManager.__new__(AnimationManager)
        manager.controller = type("Controller", (), {"inline_show": True})()
        manager.frames_presented = 2
        manager.unchanged_frames_skipped = 0
        manager.perf_lock = threading.Lock()

        transitions = (
            (100, 200),
            (200, 100),
        )
        for old_target, current_target in transitions:
            with self.subTest(old_target=old_target, current_target=current_target):
                samples = []
                for target in (old_target, current_target):
                    target_period = 1.0 / target
                    samples.append({
                        "process": 0.007,
                        "target_period": target_period,
                        "deadline_missed": 0.007 > target_period,
                        "frame": 0.007,
                    })
                manager.target_fps = current_target
                manager.perf_samples = deque(samples)
                manager._last_perf_sample = samples[-1]

                summary = manager._get_perf_summary()

                self.assertEqual(summary["deadline_misses"], 1)
                self.assertEqual(summary["deadline_miss_ratio"], 0.5)

    def test_base_rotates_two_canonical_buffers(self):
        animation = _Animation(_Controller())
        first = animation.generate_frame(0.0, 0)
        second = animation.generate_frame(0.1, 1)
        third = animation.generate_frame(0.2, 2)

        self.assertIs(first, third)
        self.assertIsNot(first, second)
        self.assertEqual(first.shape, (_Controller.total_leds, 3))
        self.assertEqual(first.dtype, np.uint8)
        self.assertTrue(first.flags.c_contiguous)

    def test_solid_reuses_cached_frame_within_its_source_cadence(self):
        animation = SolidColorAnimation(_Controller(), {'glow': .68, 'breath': 0.0})
        first = animation.generate_frame(0.0, 0)
        snapshot = first.pixels.copy()
        second = animation.generate_frame(0.005, 1)
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        np.testing.assert_array_equal(snapshot, second.pixels)
        animation.update_parameters({'glow': .5})
        third = animation.generate_frame(0.005, 2)
        self.assertTrue(third.changed)
        self.assertFalse(np.array_equal(snapshot, third.pixels))

    def test_reusable_hsv_conversion_matches_colorsys(self):
        animation = _Animation(_Controller())
        hues = np.linspace(0.0, 0.99, _Controller.total_leds, dtype=np.float32)
        saturation = np.full_like(hues, 0.73)
        value = np.full_like(hues, 0.81)
        output = np.empty((_Controller.total_leds, 3), dtype=np.uint8)

        returned = animation.hsv_to_rgb_array(hues, saturation, value, out=output)
        expected = np.asarray([
            tuple(int(channel * 255) for channel in colorsys.hsv_to_rgb(float(h), 0.73, 0.81))
            for h in hues
        ], dtype=np.uint8)

        self.assertIs(returned, output)
        np.testing.assert_allclose(output, expected, atol=1)

    def test_rainbow_time_and_parameter_edits_change_cached_frames(self):
        animation = RainbowAnimation(_Controller())
        first = animation.generate_frame(0.0, 0).pixels.copy()
        second = animation.generate_frame(0.1, 1).pixels.copy()
        self.assertEqual(first.shape, (_Controller.total_leds, 3))
        self.assertFalse(np.array_equal(first, second))
        animation.update_parameters({'bands': .5})
        third = animation.generate_frame(0.1, 2)
        self.assertTrue(third.changed)
        self.assertFalse(np.array_equal(second, third.pixels))

    def test_manager_does_not_transmit_unchanged_render_results(self):
        class Controller(_Controller):
            inline_show = True

            def __init__(self):
                self.frames = []

            def set_all_pixels(self, frame):
                self.frames.append(frame.copy())

        controller = Controller()
        manager = AnimationManager.__new__(AnimationManager)
        manager.controller = controller
        manager.target_fps = 1000
        manager.is_running = True
        manager.stop_event = threading.Event()
        manager.start_time = time.perf_counter()
        manager.frame_count = 0
        manager.frames_presented = 0
        manager.unchanged_frames_skipped = 0
        manager.current_frame_data = []
        manager.frame_data_lock = threading.Lock()
        manager.frame_timestamps = deque(maxlen=20)
        manager.perf_samples = deque(maxlen=20)
        manager.perf_lock = threading.Lock()
        manager._last_perf_sample = {}

        frame = np.zeros((_Controller.total_leds, 3), dtype=np.uint8)

        class Animation:
            calls = 0

            def generate_frame(self, _elapsed, _frame_count):
                self.calls += 1
                if self.calls >= 3:
                    manager.is_running = False
                return RenderedFrame(frame, changed=self.calls == 1)

        manager.current_animation = Animation()
        manager._animation_loop()

        self.assertEqual(len(controller.frames), 1)
        self.assertEqual(manager.frames_presented, 1)
        self.assertEqual(manager.unchanged_frames_skipped, 2)

    def test_animation_loop_exits_when_presenter_already_shut_down(self):
        manager = AnimationManager.__new__(AnimationManager)
        manager.controller = _Controller()
        manager.target_fps = 1000
        manager.is_running = True
        manager.stop_event = threading.Event()
        manager.start_time = time.perf_counter()
        manager.frame_count = 0
        manager.frames_presented = 0
        manager.unchanged_frames_skipped = 0
        manager.current_frame_data = []
        manager.frame_data_lock = threading.Lock()
        manager.frame_timestamps = deque(maxlen=20)
        manager.perf_samples = deque(maxlen=20)
        manager.perf_lock = threading.Lock()
        manager._last_perf_sample = {}
        manager.current_animation = _Animation(manager.controller)

        class _DeadPresenter:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def submit(self, *_args, **_kwargs):
                raise RuntimeError("cannot schedule new futures after shutdown")

        with unittest.mock.patch(
            "animation.core.manager.ThreadPoolExecutor",
            return_value=_DeadPresenter(),
        ):
            manager._animation_loop()

        self.assertEqual(manager.frames_presented, 0)
        self.assertFalse(manager.is_running)

    def test_manager_generates_next_frame_while_previous_frame_is_presented(self):
        send_started = threading.Event()
        release_send = threading.Event()
        generated_during_send = threading.Event()

        class Controller(_Controller):
            inline_show = True

            def set_all_pixels(self, _frame):
                send_started.set()
                release_send.wait(timeout=1.0)

        manager = AnimationManager.__new__(AnimationManager)
        manager.controller = Controller()
        manager.target_fps = 1000
        manager.is_running = True
        manager.stop_event = threading.Event()
        manager.start_time = time.perf_counter()
        manager.frame_count = 0
        manager.frames_presented = 0
        manager.unchanged_frames_skipped = 0
        manager.current_frame_data = []
        manager.frame_data_lock = threading.Lock()
        manager.frame_timestamps = deque(maxlen=20)
        manager.perf_samples = deque(maxlen=20)
        manager.perf_lock = threading.Lock()
        manager._last_perf_sample = {}

        class Animation(_Animation):
            def generate_frame(self, elapsed, frame_count):
                if frame_count == 1 and send_started.wait(timeout=0.5):
                    generated_during_send.set()
                    release_send.set()
                if frame_count >= 2:
                    manager.is_running = False
                    release_send.set()
                return super().generate_frame(elapsed, frame_count)

        manager.current_animation = Animation(manager.controller)
        manager._animation_loop()

        self.assertTrue(generated_during_send.is_set())
        self.assertEqual(manager.frames_presented, 3)


class _SPI:
    def __init__(self):
        self.calls = []
        self.max_speed_hz = 20_000_000
        self.mode = 0

    def xfer2(self, data):
        self.calls.append(bytes(data))
        return [0] * len(data)




class _PartialDevice:
    def __init__(self):
        self.partial = []
        self.full = []

    def set_partial_frame(self, colors, ranges):
        self.partial.append((colors.copy(), tuple(ranges)))

    def set_all_pixels(self, colors):
        self.full.append(colors.copy())






if __name__ == "__main__":
    unittest.main()
