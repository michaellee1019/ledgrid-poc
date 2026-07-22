import binascii
import colorsys
from collections import deque
import sys
import threading
import time
import types
import unittest
from concurrent.futures import ThreadPoolExecutor

import numpy as np


if "spidev" not in sys.modules:
    spidev_stub = types.ModuleType("spidev")
    spidev_stub.SpiDev = object
    sys.modules["spidev"] = spidev_stub

from animation.core.base import AnimationBase, RenderedFrame
from animation.core.manager import AnimationManager
from animation.plugins.rainbow import RainbowAnimation
from animation.plugins.solid import SolidColorAnimation
from drivers.multi_device import MultiDeviceLEDController
from drivers.spi_controller import CRC_BYTES, LEDController
from drivers.spi_controller import RECEIVER_STATUS_BYTES
from drivers.spi_controller import RECEIVER_STATUS_BYTES_V2
from tools.benchmarks.receiver_acceptance import evaluate_samples
from tools.benchmarks.live_animation_sweep import receiver_failures


class _Controller:
    strip_count = 4
    leds_per_strip = 5
    total_leds = strip_count * leds_per_strip


class _Animation(AnimationBase):
    def generate_frame(self, time_elapsed, frame_count):
        return self.next_frame_buffer()


class FrameContractTests(unittest.TestCase):
    def test_manager_bounds_live_target_fps(self):
        manager = AnimationManager.__new__(AnimationManager)
        self.assertEqual(manager.set_target_fps(160), 160)
        self.assertEqual(manager.set_target_fps(999), 200)
        self.assertEqual(manager.set_target_fps(0), 1)

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

    def test_static_solid_marks_duplicate_frames_unchanged(self):
        animation = SolidColorAnimation(_Controller(), {"red": 7, "green": 8, "blue": 9})
        first = animation.generate_frame(0.0, 0)
        second = animation.generate_frame(0.1, 1)

        self.assertIsInstance(first, RenderedFrame)
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertIs(first.pixels, second.pixels)
        np.testing.assert_array_equal(first.pixels[0], (7, 8, 9))

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

    def test_rainbow_reuses_color_lut_until_color_parameters_change(self):
        class InstrumentedRainbow(RainbowAnimation):
            hsv_conversions = 0

            def hsv_to_rgb_array(self, *args, **kwargs):
                self.hsv_conversions += 1
                return super().hsv_to_rgb_array(*args, **kwargs)

        animation = InstrumentedRainbow(_Controller())
        first = animation.generate_frame(0.0, 0)
        second = animation.generate_frame(0.005, 1)

        self.assertEqual(animation.hsv_conversions, 1)
        self.assertIsNot(first, second)
        self.assertEqual(first.shape, (_Controller.total_leds, 3))
        self.assertEqual(first.dtype, np.uint8)
        self.assertFalse(np.array_equal(first, second))
        np.testing.assert_array_equal(first[:5], first[5:10])

        animation.update_parameters({'color_saturation': 0.5})
        animation.generate_frame(0.01, 2)
        self.assertEqual(animation.hsv_conversions, 2)

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


class DriverBufferTests(unittest.TestCase):
    def _driver(self):
        driver = LEDController.__new__(LEDController)
        driver.total_leds = 4
        driver.strip_count = 1
        driver.leds_per_strip = 4
        driver.spi = _SPI()
        driver._frame_packet = bytearray(1 + driver.total_leds * 3 + CRC_BYTES)
        driver._bytes_sent = 0
        driver._crc_bytes_sent = 0
        driver._spi_transfers = 0
        driver._errors = 0
        driver._frames_sent = 0
        driver._last_frame_duration = 0.0
        driver._total_frame_duration = 0.0
        driver._refresh_configuration = lambda force=False: None
        return driver

    def test_full_frame_packet_is_reused_and_crc_is_valid(self):
        driver = self._driver()
        packet_id = id(driver._frame_packet)
        frame = np.arange(12, dtype=np.uint8).reshape(4, 3)

        driver.set_all_pixels(frame)
        driver.set_all_pixels(frame + 1)

        self.assertEqual(id(driver._frame_packet), packet_id)
        self.assertEqual(len(driver.spi.calls), 2)
        for packet in driver.spi.calls:
            payload, crc_bytes = packet[:-2], packet[-2:]
            self.assertEqual(
                int.from_bytes(crc_bytes, "big"),
                binascii.crc_hqx(payload, 0xFFFF),
            )

    def test_receiver_status_is_parsed_from_miso(self):
        driver = self._driver()
        response = [0] * RECEIVER_STATUS_BYTES
        response[:4] = b"LGS1"
        response[4:8] = (123).to_bytes(4, "big")
        response[8:12] = (2).to_bytes(4, "big")
        response[12:16] = (121).to_bytes(4, "big")
        response[16:20] = (99).to_bytes(4, "big")
        response[20:22] = (41).to_bytes(2, "big")
        response[22:24] = (52).to_bytes(2, "big")
        response[24:26] = (4321).to_bytes(2, "big")
        response[26] = 8
        response[27:29] = (140).to_bytes(2, "big")

        driver._update_receiver_status(response)

        self.assertTrue(driver._receiver_status_seen)
        self.assertEqual(driver._receiver_packets, 123)
        self.assertEqual(driver._receiver_crc_errors, 2)
        self.assertEqual(driver._receiver_frames_rendered, 99)
        self.assertEqual(driver._receiver_last_show_us, 4321)
        self.assertEqual(driver._receiver_leds_per_strip, 140)

    def test_receiver_status_v2_exposes_pipeline_accounting(self):
        driver = self._driver()
        response = [0] * RECEIVER_STATUS_BYTES_V2
        response[:4] = b"LGS2"
        response[4] = 2
        response[5] = 3
        response[6] = 8
        response[8:10] = (140).to_bytes(2, "big")
        response[10:12] = (2).to_bytes(2, "big")
        response[12:16] = (1000).to_bytes(4, "big")
        response[16:20] = (0).to_bytes(4, "big")
        response[20:24] = (999).to_bytes(4, "big")
        response[24:28] = (900).to_bytes(4, "big")
        response[28:32] = (890).to_bytes(4, "big")
        response[32:36] = (9).to_bytes(4, "big")
        response[36:40] = (0).to_bytes(4, "big")
        response[40:44] = (0).to_bytes(4, "big")
        response[44:46] = (310).to_bytes(2, "big")
        response[46:48] = (20).to_bytes(2, "big")
        response[48:50] = (450).to_bytes(2, "big")
        response[50:52] = (4500).to_bytes(2, "big")
        response[52:56] = (901).to_bytes(4, "big")
        response[56:60] = (891).to_bytes(4, "big")
        response[60:64] = (0).to_bytes(4, "big")

        driver._update_receiver_status(response)

        self.assertEqual(driver._receiver_status_version, 2)
        self.assertEqual(driver._receiver_queued_transactions, 2)
        self.assertEqual(driver._receiver_frames_accepted, 900)
        self.assertEqual(driver._receiver_frames_displayed, 890)
        self.assertEqual(driver._receiver_frames_superseded, 9)
        self.assertEqual(driver._receiver_last_encode_us, 450)
        self.assertEqual(driver._receiver_last_show_us, 4500)
        self.assertEqual(driver._receiver_last_displayed_sequence, 891)

        driver._update_receiver_status([0] * RECEIVER_STATUS_BYTES_V2)
        self.assertEqual(driver._receiver_status_misses, 1)

        driver._update_receiver_status([0] * 5)
        self.assertEqual(driver._receiver_status_misses, 1)


class _PartialDevice:
    def __init__(self):
        self.partial = []
        self.full = []

    def set_partial_frame(self, colors, ranges):
        self.partial.append((colors.copy(), tuple(ranges)))

    def set_all_pixels(self, colors):
        self.full.append(colors.copy())


class MultiDevicePartialTests(unittest.TestCase):
    def test_independent_spi_buses_present_concurrently(self):
        started = threading.Barrier(2)
        overlapped = threading.Event()

        class BlockingDevice:
            def set_all_pixels(self, _colors):
                try:
                    started.wait(timeout=0.5)
                    overlapped.set()
                except threading.BrokenBarrierError:
                    pass

        controller = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        controller.num_devices = 2
        controller.strips_per_device = 1
        controller.leds_per_strip = 1
        controller.leds_per_device = 1
        controller.strip_count = 2
        controller.total_leds = 2
        controller.devices = [BlockingDevice(), BlockingDevice()]
        controller._devices_by_bus = {0: [0], 1: [1]}
        controller._executor = ThreadPoolExecutor(max_workers=2)
        controller.debug = False
        controller._logical_frames_sent = 0

        try:
            controller.set_all_pixels(np.zeros((2, 3), dtype=np.uint8))
        finally:
            controller._executor.shutdown(wait=True)

        self.assertTrue(overlapped.is_set())
        self.assertEqual(controller._logical_frames_sent, 1)

    def test_devices_on_one_spi_bus_remain_serialized(self):
        order = []

        class OrderedDevice:
            def __init__(self, index):
                self.index = index

            def set_all_pixels(self, _colors):
                order.append(self.index)

        controller = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        controller.num_devices = 2
        controller.strips_per_device = 1
        controller.leds_per_strip = 1
        controller.leds_per_device = 1
        controller.strip_count = 2
        controller.total_leds = 2
        controller.devices = [OrderedDevice(0), OrderedDevice(1)]
        controller._devices_by_bus = {0: [0, 1]}
        controller._executor = None
        controller.debug = False
        controller._logical_frames_sent = 0

        controller.set_all_pixels(np.zeros((2, 3), dtype=np.uint8))

        self.assertEqual(order, [0, 1])

    def test_global_dirty_ranges_are_mapped_to_affected_devices(self):
        controller = MultiDeviceLEDController.__new__(MultiDeviceLEDController)
        controller.num_devices = 2
        controller.strips_per_device = 1
        controller.leds_per_strip = 4
        controller.leds_per_device = 4
        controller.strip_count = 2
        controller.total_leds = 8
        controller.devices = [_PartialDevice(), _PartialDevice()]
        controller._devices_by_bus = {0: [0, 1]}
        controller._executor = None
        controller.debug = False
        controller._logical_frames_sent = 0

        frame = np.arange(24, dtype=np.uint8).reshape(8, 3)
        controller.set_frame(frame, dirty_ranges=((3, 5),))

        self.assertEqual(controller.devices[0].partial[0][1], ((3, 4),))
        self.assertEqual(controller.devices[1].partial[0][1], ((0, 1),))
        np.testing.assert_array_equal(controller.devices[0].partial[0][0], frame[:4])
        np.testing.assert_array_equal(controller.devices[1].partial[0][0], frame[4:])
        self.assertEqual(controller._logical_frames_sent, 1)


class ReceiverAcceptanceTests(unittest.TestCase):
    def test_live_sweep_evaluator_checks_only_integrity_counter_deltas(self):
        first = {
            'receiver_status_version': 2,
            'receiver_crc_errors': 4,
            'receiver_publish_drops': 0,
            'receiver_spi_queue_errors': 0,
            'receiver_display_errors': 0,
            'receiver_status_misses': 1,
        }
        self.assertEqual(receiver_failures(first, dict(first)), [])

        last = dict(first)
        last['receiver_display_errors'] = 2
        self.assertEqual(
            receiver_failures(first, last),
            ['display errors increased by 2'],
        )

    def test_acceptance_evaluator_passes_accounted_180fps_pipeline(self):
        first = {
            'receiver_status_version': 2,
            'receiver_crc_errors': 1,
            'receiver_publish_drops': 0,
            'receiver_spi_queue_errors': 0,
            'receiver_display_errors': 0,
            'receiver_status_misses': 0,
            'receiver_frames_accepted': 100,
            'receiver_frames_displayed': 98,
            'receiver_frames_superseded': 1,
            'receiver_last_encode_us': 500,
            'receiver_last_show_us': 4500,
        }
        last = dict(first)
        last.update({
            'receiver_frames_accepted': 2000,
            'receiver_frames_displayed': 1948,
            'receiver_frames_superseded': 51,
            'receiver_last_encode_us': 600,
            'receiver_last_show_us': 4550,
        })

        result = evaluate_samples([first, last], 10.0)

        self.assertTrue(result['passed'], result['failures'])
        self.assertEqual(result['displayed_fps'], 185.0)

    def test_acceptance_evaluator_reports_integrity_and_timing_failures(self):
        first = {
            'receiver_status_version': 2,
            'receiver_frames_accepted': 0,
            'receiver_frames_displayed': 0,
            'receiver_frames_superseded': 0,
            'receiver_crc_errors': 0,
        }
        last = dict(first)
        last.update({
            'receiver_frames_accepted': 100,
            'receiver_frames_displayed': 50,
            'receiver_crc_errors': 1,
            'receiver_last_encode_us': 1200,
            'receiver_last_show_us': 5000,
        })

        result = evaluate_samples([first, last], 1.0)

        self.assertFalse(result['passed'])
        self.assertTrue(any('CRC errors' in failure for failure in result['failures']))
        self.assertTrue(any('display DMA' in failure for failure in result['failures']))


if __name__ == "__main__":
    unittest.main()
