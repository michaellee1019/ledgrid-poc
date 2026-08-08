#!/usr/bin/env python3
"""
LED Grid Controller - SPI version
Controls multiple SCORPIO boards via SPI
"""

import time
import colorsys
import argparse
import binascii
import spidev
import sys
import struct

import numpy as np

from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP

# LED Configuration defaults
DEFAULT_LED_PER_STRIP = DEFAULT_LEDS_PER_STRIP
DEFAULT_NUM_STRIPS = DEFAULT_STRIP_COUNT

# SPI Configuration
SPI_BUS = 0  # SPI bus number (0 = /dev/spidev0.X)
SPI_DEVICE = 0  # CE0 matches wiring to XIAO GPIO2 (D1)
SPI_SPEED = 20000000  # 20 MHz - CRC-16 protects against corruption
SPI_MODE = 0  # CPOL=0, CPHA=0 - universal mode supported by all Pi SPI buses
SPI_INTER_FRAME_DELAY = 0.0  # No delay needed - SPI is stable now

MAX_SPI_TRANSFER = 4096
CRC_BYTES = 2
# The ESP32 slave keeps two DMA transactions queued. A command result is
# therefore returned by the second complete transfer after that command.
SPI_RESPONSE_QUEUE_DEPTH = 2
RECEIVER_STATUS_MAGIC = (ord('L'), ord('G'), ord('S'), ord('3'))
RECEIVER_STATUS_VERSION = 3
RECEIVER_STATUS_BYTES = 128
MAX_PIXELS_SET_ALL = (MAX_SPI_TRANSFER - 1 - CRC_BYTES) // 3
MAX_PIXELS_PER_RANGE = min(255, (MAX_SPI_TRANSFER - 4 - CRC_BYTES) // 3)

GLOBAL_OPTS_WITH_VALUE = {"--bus", "--device", "--spi-speed", "--mode", "--brightness", "--strips", "--leds-per-strip"}
GLOBAL_BOOL_OPTS = {"--debug"}


def _normalize_global_args(argv):
    """Move global options ahead of subcommand to appease argparse."""
    if not argv:
        return []

    front = []
    rest = []
    i = 0
    prefixes = tuple(f"{opt}=" for opt in GLOBAL_OPTS_WITH_VALUE)

    while i < len(argv):
        token = argv[i]
        if token in GLOBAL_OPTS_WITH_VALUE:
            front.append(token)
            if i + 1 < len(argv):
                front.append(argv[i + 1])
                i += 2
            else:
                i += 1
            continue

        if token in GLOBAL_BOOL_OPTS:
            front.append(token)
            i += 1
            continue

        matched_prefix = False
        for prefix in prefixes:
            if token.startswith(prefix):
                front.append(token)
                matched_prefix = True
                break

        if matched_prefix:
            i += 1
            continue

        rest.append(token)
        i += 1

    return front + rest


def _crc16_ccitt(data):
    """CRC-16/CCITT-FALSE using CPython's native implementation."""
    return binascii.crc_hqx(data, 0xFFFF)

# Command definitions
CMD_SET_PIXEL = 0x01
CMD_SET_BRIGHTNESS = 0x02
CMD_SHOW = 0x03
CMD_CLEAR = 0x04
CMD_SET_RANGE = 0x05
CMD_SET_ALL = 0x06
CMD_CONFIG = 0x07
CMD_CAPABILITIES_QUERY = 0x20
CMD_ASSET_PROBE = 0x21
CMD_ASSET_BEGIN = 0x22
CMD_ASSET_CHUNK = 0x23
CMD_ASSET_COMMIT = 0x24
CMD_ASSET_REMOVE = 0x25
CMD_ANIMATION_START = 0x26
CMD_ANIMATION_STOP = 0x27
CMD_ANIMATION_RESTART = 0x28
CMD_ANIMATION_PARAMETERS = 0x29
CMD_ASSET_ABORT = 0x2A
CMD_PING = 0xFF

MAX_ASSET_CHUNK_BYTES = MAX_SPI_TRANSFER - CRC_BYTES - 1 - 4
ASSET_KIND_CODES = {'native': 1, 'frames': 2}
RESULT_NAMES = {
    0: 'none', 1: 'ok', 2: 'invalid_command', 3: 'invalid_state',
    4: 'bad_size', 5: 'bad_digest', 6: 'bad_signature', 7: 'wrong_abi',
    8: 'wrong_geometry', 9: 'wrong_device', 10: 'storage_error',
    11: 'unsupported', 12: 'quarantined', 13: 'render_failed',
    14: 'watchdog', 15: 'not_found', 16: 'unknown_key',
    17: 'wrong_target', 18: 'bad_envelope',
}
CAPABILITY_NATIVE = 1 << 0
CAPABILITY_FRAME_TRACK = 1 << 1
CAPABILITY_SIGNED_PACKAGES = 1 << 2
CAPABILITY_ASSET_UPLOAD = 1 << 3
CAPABILITY_TYPED_PARAMETERS = 1 << 6
CAPABILITY_LOGICAL_DEVICE_SHIFT = 16
CAPABILITY_LOGICAL_DEVICE_MASK = 0x3 << CAPABILITY_LOGICAL_DEVICE_SHIFT
CAPABILITY_LOGICAL_DEVICE_IDENTITY = 1 << 18


class LEDController:
    """Control LED strips via SPI"""

    MAX_ASSET_CHUNK_BYTES = MAX_ASSET_CHUNK_BYTES
    
    def __init__(self, bus=SPI_BUS, device=SPI_DEVICE, speed=SPI_SPEED, mode=SPI_MODE,
                 strips=DEFAULT_NUM_STRIPS, leds_per_strip=DEFAULT_LED_PER_STRIP,
                 debug=False):
        self.debug = debug
        self.bus = bus
        self.device = device
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = speed
        try:
            self.spi.mode = mode
        except OSError as exc:
            raise OSError(
                f"Failed to set SPI mode {mode} on /dev/spidev{bus}.{device}. "
                "If this is SPI1, try setting LEDGRID_SPI1_MODE to a different value and restart."
            ) from exc
        self.spi.bits_per_word = 8

        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = self.strip_count * self.leds_per_strip
        # When True, set_all_pixels already issues CMD_SHOW so callers must not call show()
        self.inline_show = True
        self.current_brightness = None
        self._last_config_refresh = 0.0
        self._last_brightness_refresh = 0.0
        self._config_refresh_interval = 30.0  # seconds - reduced frequency to avoid LED blanking
        self._last_sent_config = None  # Track last config to avoid unnecessary refreshes
        self._frames_sent = 0
        self._spi_transfers = 0
        self._bytes_sent = 0
        self._crc_bytes_sent = 0
        self._errors = 0
        self._last_frame_duration = 0.0
        self._total_frame_duration = 0.0
        self._receiver_status_seen = False
        self._receiver_status_version = 0
        self._receiver_status_responses = 0
        self._receiver_status_misses = 0
        self._receiver_packets = 0
        self._receiver_crc_errors = 0
        self._receiver_crc_ok_packets = 0
        self._receiver_frames_rendered = 0
        self._receiver_last_crc_us = 0
        self._receiver_last_copy_us = 0
        self._receiver_last_show_us = 0
        self._receiver_active_strips = 0
        self._receiver_leds_per_strip = 0
        self._receiver_queued_transactions = 0
        self._receiver_frames_accepted = 0
        self._receiver_frames_displayed = 0
        self._receiver_frames_superseded = 0
        self._receiver_publish_drops = 0
        self._receiver_spi_queue_errors = 0
        self._receiver_display_errors = 0
        self._receiver_last_encode_us = 0
        self._receiver_last_accepted_sequence = 0
        self._receiver_last_displayed_sequence = 0
        self._receiver_capabilities = 0
        self._receiver_logical_device = None
        self._receiver_display_mode = 0
        self._receiver_asset_kind = 0
        self._receiver_upload_state = 0
        self._receiver_last_result = 0
        self._receiver_active_digest = None
        self._receiver_cache_free_bytes = 0
        self._receiver_cache_used_bytes = 0
        self._receiver_upload_received_bytes = 0
        self._receiver_upload_total_bytes = 0
        self._receiver_last_render_or_decode_us = 0
        self._receiver_max_render_or_decode_us = 0
        self._receiver_missed_deadlines = 0
        self._receiver_watchdog_events = 0
        self._receiver_quarantine_state = 0
        self._frame_packet = bytearray(1 + self.total_leds * 3 + CRC_BYTES)
        
        if self.debug:
            print("SPI Controller initialized")
            print(f"  Bus: {bus}, Device: {device}")
            print(f"  Speed: {speed/1000000:.1f} MHz")
            print(f"  Mode: {mode}")
            print(f"  Device: /dev/spidev{bus}.{device}")
            print(f"  Number of strips: {self.strip_count}")
            print(f"  LEDs per strip: {self.leds_per_strip}")
            print(f"  Total LEDs: {self.total_leds}")
        
        # Test ping
        try:
            self._xfer([CMD_PING])
            time.sleep(0.01)
            if self.debug:
                print("✓ SPI connection OK\n")
        except Exception as e:
            print(f"Warning: SPI test failed: {e}\n", file=sys.stderr)
    
    def _xfer(self, payload):
        try:
            payload_view = memoryview(payload)
        except TypeError:
            payload_view = memoryview(bytes(payload))
        buf = bytearray(len(payload_view) + CRC_BYTES)
        buf[:len(payload_view)] = payload_view
        return self._xfer_packet(buf, len(payload_view))

    def _xfer_packet(self, buf, payload_length):
        """Finalize and transfer a packet whose CRC storage is preallocated."""
        if payload_length < 1 or payload_length + CRC_BYTES > MAX_SPI_TRANSFER:
            raise ValueError(
                f"SPI transaction must be 1..{MAX_SPI_TRANSFER} bytes including CRC"
            )
        if len(buf) != payload_length + CRC_BYTES:
            raise ValueError("packet buffer must contain exactly payload plus CRC storage")
        crc = _crc16_ccitt(memoryview(buf)[:payload_length])
        buf[payload_length] = (crc >> 8) & 0xFF
        buf[payload_length + 1] = crc & 0xFF
        self._bytes_sent += len(buf)
        self._crc_bytes_sent += CRC_BYTES
        self._spi_transfers += 1
        try:
            response = self.spi.xfer2(buf)
            self._update_receiver_status(response)
            return response
        except Exception:
            self._errors += 1
            raise

    @staticmethod
    def _response_u16(response, offset):
        return (int(response[offset]) << 8) | int(response[offset + 1])

    @staticmethod
    def _response_u32(response, offset):
        return (
            (int(response[offset]) << 24)
            | (int(response[offset + 1]) << 16)
            | (int(response[offset + 2]) << 8)
            | int(response[offset + 3])
        )

    def _update_receiver_status(self, response):
        """Parse the ESP32 status snapshot returned alongside an SPI write."""
        # SPI is full duplex, so the response can only be as long as the
        # command. Short control/configuration transfers cannot carry the
        # complete status structure and therefore are not telemetry misses.
        if response is None or len(response) < RECEIVER_STATUS_BYTES:
            return

        magic = tuple(int(response[index]) for index in range(4))
        if magic != RECEIVER_STATUS_MAGIC or int(response[4]) != RECEIVER_STATUS_VERSION:
            if getattr(self, '_receiver_status_seen', False):
                self._receiver_status_misses = getattr(self, '_receiver_status_misses', 0) + 1
            return

        self._receiver_status_seen = True
        self._receiver_status_version = RECEIVER_STATUS_VERSION
        self._receiver_status_responses = getattr(self, '_receiver_status_responses', 0) + 1
        self._receiver_active_strips = int(response[6])
        self._receiver_leds_per_strip = self._response_u16(response, 8)
        self._receiver_queued_transactions = self._response_u16(response, 10)
        self._receiver_packets = self._response_u32(response, 12)
        self._receiver_crc_errors = self._response_u32(response, 16)
        self._receiver_crc_ok_packets = self._response_u32(response, 20)
        self._receiver_frames_accepted = self._response_u32(response, 24)
        self._receiver_frames_displayed = self._response_u32(response, 28)
        self._receiver_frames_rendered = self._receiver_frames_displayed
        self._receiver_frames_superseded = self._response_u32(response, 32)
        self._receiver_publish_drops = self._response_u32(response, 36)
        self._receiver_spi_queue_errors = self._response_u32(response, 40)
        self._receiver_last_crc_us = self._response_u16(response, 44)
        self._receiver_last_copy_us = self._response_u16(response, 46)
        self._receiver_last_encode_us = self._response_u16(response, 48)
        self._receiver_last_show_us = self._response_u16(response, 50)
        self._receiver_last_accepted_sequence = self._response_u32(response, 52)
        self._receiver_last_displayed_sequence = self._response_u32(response, 56)
        self._receiver_display_errors = self._response_u32(response, 60)
        self._receiver_capabilities = self._response_u32(response, 64)
        self._receiver_logical_device = (
            (self._receiver_capabilities & CAPABILITY_LOGICAL_DEVICE_MASK)
            >> CAPABILITY_LOGICAL_DEVICE_SHIFT
            if self._receiver_capabilities & CAPABILITY_LOGICAL_DEVICE_IDENTITY
            else None
        )
        self._receiver_display_mode = int(response[68])
        self._receiver_asset_kind = int(response[69])
        self._receiver_upload_state = int(response[70])
        self._receiver_last_result = int(response[71])
        digest = bytes(response[72:104])
        self._receiver_active_digest = digest.hex() if any(digest) else None
        self._receiver_cache_free_bytes = self._response_u32(response, 104)
        self._receiver_cache_used_bytes = self._response_u32(response, 108)
        self._receiver_upload_received_bytes = self._response_u32(response, 112)
        self._receiver_upload_total_bytes = self._response_u32(response, 116)
        self._receiver_last_render_or_decode_us = self._response_u16(response, 120)
        self._receiver_max_render_or_decode_us = self._response_u16(response, 122)
        self._receiver_missed_deadlines = self._response_u16(response, 124)
        self._receiver_watchdog_events = int(response[126])
        self._receiver_quarantine_state = int(response[127])

    @staticmethod
    def _digest_bytes(digest):
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("digest must be 64 hexadecimal characters")
        try:
            return bytes.fromhex(digest)
        except ValueError as exc:
            raise ValueError("digest must be 64 hexadecimal characters") from exc

    def query_receiver_status(self):
        """Clock out one complete queued LGS3 snapshot."""
        payload = bytearray(RECEIVER_STATUS_BYTES - CRC_BYTES)
        payload[0] = CMD_CAPABILITIES_QUERY
        self._xfer(payload)
        return self.get_stats()

    def _command_status(self, payload):
        """Send a command and drain the receiver's two-deep response queue."""
        self._xfer(payload)
        status = None
        for _ in range(SPI_RESPONSE_QUEUE_DEPTH):
            status = self.query_receiver_status()
        return status

    def asset_probe(self, digest):
        return self._command_status(
            bytes([CMD_ASSET_PROBE]) + self._digest_bytes(digest)
        )

    @staticmethod
    def _asset_begin_command(envelope):
        """Validate the SDK trust envelope before any receiver I/O."""
        required = (
            'payload_size', 'payload_digest', 'kind', 'device_index', 'key_id',
            'signed_index', 'signature', 'asset_begin_command',
        )
        if envelope is None or any(not hasattr(envelope, name) for name in required):
            raise ValueError("signed receiver verification envelope is required")
        try:
            command = bytes(envelope.asset_begin_command())
            key_id = envelope.key_id.encode('ascii')
        except (AttributeError, TypeError, UnicodeError, ValueError) as exc:
            raise ValueError("receiver verification envelope is malformed") from exc
        kind_code = ASSET_KIND_CODES.get(envelope.kind)
        if (
            len(command) != 313 or command[0:2] != bytes((CMD_ASSET_BEGIN, 1))
            or int.from_bytes(command[2:4], 'big') != 309
            or int.from_bytes(command[4:8], 'big') != int(envelope.payload_size)
            or command[8:40] != bytes(envelope.payload_digest)
            or command[40] != kind_code
            or int.from_bytes(command[41:43], 'big') != 1
            or int.from_bytes(command[43:45], 'big') != 1
            or command[45] != 8
            or int.from_bytes(command[46:48], 'big') != 138
            or command[48] != int(envelope.device_index)
            or command[49] != 20 or len(key_id) != 20 or command[50:70] != key_id
            or int.from_bytes(command[70:72], 'big') != 176
            or len(bytes(envelope.signed_index)) != 176
            or command[72:248] != bytes(envelope.signed_index)
            or command[248] != 64 or len(bytes(envelope.signature)) != 64
            or command[249:313] != bytes(envelope.signature)
        ):
            raise ValueError("receiver verification envelope is not canonical")
        return command

    def asset_begin(self, envelope):
        payload = self._asset_begin_command(envelope)
        return self._command_status(payload)

    def asset_chunk(self, offset, chunk):
        data = bytes(chunk)
        if len(data) > MAX_ASSET_CHUNK_BYTES:
            raise ValueError(f"asset chunk exceeds {MAX_ASSET_CHUNK_BYTES} bytes")
        return self._command_status(
            bytes([CMD_ASSET_CHUNK]) + struct.pack(">I", int(offset)) + data
        )

    def asset_commit(self, digest):
        return self._command_status(
            bytes([CMD_ASSET_COMMIT]) + self._digest_bytes(digest)
        )

    def asset_abort(self):
        return self._command_status([CMD_ASSET_ABORT])

    def asset_remove(self, digest):
        return self._command_status(
            bytes([CMD_ASSET_REMOVE]) + self._digest_bytes(digest)
        )

    @staticmethod
    def _parameter_blob(parameters):
        """Encode the stable typed-parameter v1 wire representation."""
        if not isinstance(parameters, dict) or len(parameters) > 32:
            raise ValueError("parameters must be an object with at most 32 values")
        blob = bytearray((1, len(parameters)))
        for name in sorted(parameters):
            name_bytes = str(name).encode('utf-8')
            if not 1 <= len(name_bytes) <= 63:
                raise ValueError("parameter names must encode to 1..63 bytes")
            value = parameters[name]
            blob.append(len(name_bytes))
            blob.extend(name_bytes)
            if isinstance(value, bool):
                blob.extend((3, int(value)))
            elif isinstance(value, int):
                if not -(2 ** 31) <= value < 2 ** 31:
                    raise ValueError(f"integer parameter {name} is out of int32 range")
                blob.append(1)
                blob.extend(struct.pack(">i", value))
            elif isinstance(value, float):
                blob.append(2)
                blob.extend(struct.pack(">f", value))
            elif isinstance(value, str) and len(value) == 7 and value.startswith('#'):
                try:
                    color = bytes.fromhex(value[1:])
                except ValueError as exc:
                    raise ValueError(f"invalid color parameter {name}") from exc
                blob.append(5)
                blob.extend(color)
            elif isinstance(value, str):
                value_bytes = value.encode('utf-8')
                if not 1 <= len(value_bytes) <= 63:
                    raise ValueError("enum values must encode to 1..63 bytes")
                blob.extend((4, len(value_bytes)))
                blob.extend(value_bytes)
            else:
                raise ValueError(f"unsupported parameter type for {name}")
        if len(blob) > 1024:
            raise ValueError("parameter blob exceeds 1024 bytes")
        return bytes(blob)

    def start_firmware_animation(self, digest, global_strip_offset, parameters=None):
        blob = self._parameter_blob(parameters)
        payload = (bytes([CMD_ANIMATION_START]) + self._digest_bytes(digest)
                   + struct.pack(">HH", int(global_strip_offset), len(blob)) + blob)
        return self._command_status(payload)

    def stop_firmware_animation(self):
        return self._command_status([CMD_ANIMATION_STOP])

    def restart_firmware_animation(self):
        return self._command_status([CMD_ANIMATION_RESTART])

    def update_firmware_parameters(self, parameters):
        blob = self._parameter_blob(parameters)
        return self._command_status(
            bytes([CMD_ANIMATION_PARAMETERS]) + struct.pack(">H", len(blob)) + blob
        )

    def _refresh_configuration(self, force=False):
        now = time.time()
        
        # Only send config if it's actually different or forced
        current_config = (self.strip_count, self.leds_per_strip)
        config_changed = (self._last_sent_config != current_config)
        
        if force or config_changed or (now - self._last_config_refresh) > self._config_refresh_interval:
            cfg = [
                CMD_CONFIG,
                self.strip_count & 0xFF,
                (self.leds_per_strip >> 8) & 0xFF,
                self.leds_per_strip & 0xFF,
                1 if self.debug else 0,
            ]
            self._xfer(cfg)
            self._last_config_refresh = now
            self._last_sent_config = current_config
            if self.debug:
                print(f"✓ Configuration refresh (strips={self.strip_count}, leds/strip={self.leds_per_strip})")

        # Disabled periodic brightness refresh to reduce SPI corruption opportunities
        # Brightness commands will only be sent when explicitly set via set_brightness()
        # if self.current_brightness is not None and (force or (now - self._last_brightness_refresh) > self._config_refresh_interval):
        #     self._xfer([CMD_SET_BRIGHTNESS, self.current_brightness & 0xFF])
        #     self._last_brightness_refresh = now
        #     if self.debug:
        #         print(f"✓ Brightness refresh ({self.current_brightness})")
    
    def set_pixel(self, pixel, r, g, b):
        """Set a single pixel color"""
        if pixel >= self.total_leds:
            return
        
        self._refresh_configuration()

        data = [
            CMD_SET_PIXEL,
            (pixel >> 8) & 0xFF,
            pixel & 0xFF,
            int(r) & 0xFF,
            int(g) & 0xFF,
            int(b) & 0xFF
        ]
        self._xfer(data)
    
    def set_brightness(self, brightness):
        """Set global brightness (0-255)"""
        level = int(brightness) & 0xFF
        self.current_brightness = level
        self._refresh_configuration(force=True)
        self._xfer([CMD_SET_BRIGHTNESS, level])
        self._last_brightness_refresh = time.time()
        if self.debug:
            print(f"✓ Brightness set ({level})")
    
    def show(self):
        """Update the LED display"""
        self._refresh_configuration()
        self._xfer([CMD_SHOW])
    
    def clear(self):
        """Clear all LEDs"""
        self._refresh_configuration()
        self._xfer([CMD_CLEAR])
    
    def set_range(self, start_pixel, colors):
        """
        Set a range of pixels efficiently
        colors: list of (r, g, b) tuples
        """
        count = min(len(colors), MAX_PIXELS_PER_RANGE)
        
        if start_pixel >= self.total_leds:
            return

        count = min(count, self.total_leds - start_pixel)

        self._refresh_configuration()

        data = [
            CMD_SET_RANGE,
            (start_pixel >> 8) & 0xFF,
            start_pixel & 0xFF,
            count
        ]
        
        if isinstance(colors, np.ndarray):
            arr = colors[:count]
            if arr.dtype != np.uint8:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
            data.extend(arr.tobytes())
        else:
            for i in range(count):
                r, g, b = colors[i]
                data.extend([int(r) & 0xFF, int(g) & 0xFF, int(b) & 0xFF])
        
        self._xfer(data)

    def set_partial_frame(self, colors, dirty_ranges):
        """Apply changed half-open pixel ranges and latch one partial frame."""
        start_time = time.perf_counter()
        success = False
        try:
            for start, end in dirty_ranges:
                start = max(0, int(start))
                end = min(self.total_leds, int(end))
                while start < end:
                    chunk_end = min(end, start + MAX_PIXELS_PER_RANGE)
                    self.set_range(start, colors[start:chunk_end])
                    start = chunk_end
            self.show()
            success = True
        finally:
            if success:
                duration = time.perf_counter() - start_time
                self._frames_sent += 1
                self._last_frame_duration = duration
                self._total_frame_duration += duration

    def configure(self):
        self.total_leds = self.strip_count * self.leds_per_strip
        expected_packet_size = 1 + self.total_leds * 3 + CRC_BYTES
        if len(self._frame_packet) != expected_packet_size:
            self._frame_packet = bytearray(expected_packet_size)
        self._refresh_configuration(force=True)
        if self.debug:
            print(f"✓ Configuration sent (strips={self.strip_count}, leds/strip={self.leds_per_strip})")

    def set_all_pixels(self, colors):
        """Send all pixels in one SPI transaction.

        Accepts a list of (r,g,b) tuples or a numpy uint8 array of shape (N,3).
        """
        self._refresh_configuration()
        start_time = time.perf_counter()

        total_pixels = self.total_leds
        is_ndarray = isinstance(colors, np.ndarray)

        if is_ndarray:
            arr = colors
            if arr.shape[0] < total_pixels:
                arr = np.concatenate([arr, np.zeros((total_pixels - arr.shape[0], 3), dtype=np.uint8)])
            elif arr.shape[0] > total_pixels:
                arr = arr[:total_pixels]
            if arr.dtype != np.uint8:
                arr = np.clip(arr, 0, 255).astype(np.uint8)
            rgb_bytes = arr.tobytes()
        else:
            rgb_bytes = None

        success = False
        try:
            if total_pixels <= MAX_PIXELS_SET_ALL:
                payload_length = 1 + total_pixels * 3
                buf = self._frame_packet
                buf[0] = CMD_SET_ALL
                if rgb_bytes is not None:
                    buf[1:payload_length] = rgb_bytes
                else:
                    idx = 1
                    for r, g, b in colors:
                        buf[idx] = int(r) & 0xFF
                        buf[idx + 1] = int(g) & 0xFF
                        buf[idx + 2] = int(b) & 0xFF
                        idx += 3
                self._xfer_packet(buf, payload_length)
                if SPI_INTER_FRAME_DELAY > 0:
                    time.sleep(SPI_INTER_FRAME_DELAY)
            else:
                start = 0
                while start < total_pixels:
                    count = min(MAX_PIXELS_PER_RANGE, total_pixels - start)
                    buf = bytearray(4 + count * 3)
                    buf[0] = CMD_SET_RANGE
                    buf[1] = (start >> 8) & 0xFF
                    buf[2] = start & 0xFF
                    buf[3] = count
                    if rgb_bytes is not None:
                        offset = start * 3
                        buf[4:] = rgb_bytes[offset:offset + count * 3]
                    else:
                        idx = 4
                        for r, g, b in colors[start:start + count]:
                            buf[idx] = int(r) & 0xFF
                            buf[idx + 1] = int(g) & 0xFF
                            buf[idx + 2] = int(b) & 0xFF
                            idx += 3
                    self._xfer(buf)
                    start += count

                self._xfer(bytearray([CMD_SHOW]))
            success = True
        finally:
            if success:
                duration = time.perf_counter() - start_time
                self._frames_sent += 1
                self._last_frame_duration = duration
                self._total_frame_duration += duration
    
    def close(self):
        """Close SPI connection"""
        self.spi.close()

    def get_stats(self):
        """Return controller performance statistics."""
        avg_ms = 0.0
        if self._frames_sent:
            avg_ms = (self._total_frame_duration / self._frames_sent) * 1000.0
        return {
            'spi_speed_hz': getattr(self.spi, 'max_speed_hz', None),
            'spi_mode': getattr(self.spi, 'mode', None),
            'total_leds': self.total_leds,
            'last_frame_duration_ms': self._last_frame_duration * 1000.0,
            'avg_frame_duration_ms': avg_ms,
            'frames_sent': self._frames_sent,
            'spi_transfers': self._spi_transfers,
            'bytes_sent': self._bytes_sent,
            'crc_bytes_sent': self._crc_bytes_sent,
            'errors': self._errors,
            'receiver_status_seen': self._receiver_status_seen,
            'receiver_status_version': self._receiver_status_version,
            'receiver_status_responses': self._receiver_status_responses,
            'receiver_status_misses': self._receiver_status_misses,
            'receiver_packets': self._receiver_packets,
            'receiver_crc_errors': self._receiver_crc_errors,
            'receiver_crc_ok_packets': self._receiver_crc_ok_packets,
            'receiver_frames_rendered': self._receiver_frames_rendered,
            'receiver_frames_accepted': self._receiver_frames_accepted,
            'receiver_frames_displayed': self._receiver_frames_displayed,
            'receiver_frames_superseded': self._receiver_frames_superseded,
            'receiver_publish_drops': self._receiver_publish_drops,
            'receiver_spi_queue_errors': self._receiver_spi_queue_errors,
            'receiver_display_errors': self._receiver_display_errors,
            'receiver_queued_transactions': self._receiver_queued_transactions,
            'receiver_last_crc_us': self._receiver_last_crc_us,
            'receiver_last_copy_us': self._receiver_last_copy_us,
            'receiver_last_encode_us': self._receiver_last_encode_us,
            'receiver_last_show_us': self._receiver_last_show_us,
            'receiver_last_accepted_sequence': self._receiver_last_accepted_sequence,
            'receiver_last_displayed_sequence': self._receiver_last_displayed_sequence,
            'receiver_active_strips': self._receiver_active_strips,
            'receiver_leds_per_strip': self._receiver_leds_per_strip,
            'receiver_capabilities': self._receiver_capabilities,
            'receiver_logical_device': self._receiver_logical_device,
            'receiver_display_mode': self._receiver_display_mode,
            'receiver_asset_kind': self._receiver_asset_kind,
            'receiver_upload_state': self._receiver_upload_state,
            'receiver_last_result': self._receiver_last_result,
            'receiver_last_result_name': RESULT_NAMES.get(
                self._receiver_last_result, 'unknown'
            ),
            'receiver_active_digest': self._receiver_active_digest,
            'receiver_cache_free_bytes': self._receiver_cache_free_bytes,
            'receiver_cache_used_bytes': self._receiver_cache_used_bytes,
            'receiver_upload_received_bytes': self._receiver_upload_received_bytes,
            'receiver_upload_total_bytes': self._receiver_upload_total_bytes,
            'receiver_last_render_or_decode_us': self._receiver_last_render_or_decode_us,
            'receiver_max_render_or_decode_us': self._receiver_max_render_or_decode_us,
            'receiver_missed_deadlines': self._receiver_missed_deadlines,
            'receiver_watchdog_events': self._receiver_watchdog_events,
            'receiver_quarantine_state': self._receiver_quarantine_state,
        }


def hsv_to_rgb(h, s, v):
    """Convert HSV to RGB (0-255)"""
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return int(r * 255), int(g * 255), int(b * 255)


def rainbow_animation(controller, duration=None, speed=0.3, span=None):
    """Rainbow cycle animation"""
    if controller.debug:
        print("Starting rainbow animation...")
        print("Press Ctrl+C to stop\n")

    start_time = time.time()
    frame_count = 0
    span_pixels = span if span else max(controller.leds_per_strip, 30)
    hue_offset = 0.0
    hue_step = 0.01 * speed

    try:
        while True:
            if duration and (time.time() - start_time) > duration:
                break

            # Calculate colors for all pixels
            pixel_colors = [(0, 0, 0)] * controller.total_leds

            for led in range(controller.leds_per_strip):
                hue = (hue_offset + (led / span_pixels)) % 1.0
                color = hsv_to_rgb(hue, 1.0, 1.0)
                for strip in range(controller.strip_count):
                    idx = strip * controller.leds_per_strip + led
                    pixel_colors[idx] = color

            controller.set_all_pixels(pixel_colors)

            hue_offset += hue_step
            if hue_offset >= 1.0:
                hue_offset -= 1.0

            frame_count += 1

            if controller.debug and frame_count % 100 == 0:
                elapsed = time.time() - start_time
                fps = frame_count / elapsed
                print(f"FPS: {fps:.1f} | Frames: {frame_count}")
                # Reset counters to report instantaneous rate
                frame_count = 0
                start_time = time.time()

            time.sleep(0.02)

    except KeyboardInterrupt:
        if controller.debug:
            print("\nAnimation stopped")


def solid_color(controller, r, g, b):
    """Set all LEDs to a solid color"""
    if controller.debug:
        print(f"Setting all LEDs to RGB({r}, {g}, {b})")
    controller.set_all_pixels([(r, g, b)] * controller.total_leds)


def test_strips(controller):
    """Test each strip individually"""
    if controller.debug:
        print("Testing each strip individually...")
    
    colors = [
        (255, 0, 0),
        (255, 127, 0),
        (255, 255, 0),
        (0, 255, 0),
        (0, 255, 255),
        (0, 0, 255),
        (255, 0, 255),
    ]
    
    pixel_buffer = [(0, 0, 0)] * controller.total_leds

    for strip in range(controller.strip_count):
        if controller.debug:
            print(f"Testing strip {strip}...")
        r, g, b = colors[strip % len(colors)]

        for pixel in range(controller.leds_per_strip):
            pixel_index = strip * controller.leds_per_strip + pixel
            pixel_buffer[pixel_index] = (r, g, b)

        controller.set_all_pixels(pixel_buffer)
        time.sleep(0.5)

        # Clear this strip in the local buffer for the next iteration
        for pixel in range(controller.leds_per_strip):
            pixel_index = strip * controller.leds_per_strip + pixel
            pixel_buffer[pixel_index] = (0, 0, 0)
    
    if controller.debug:
        print("Test complete!")


def main():
    parser = argparse.ArgumentParser(description='LED Grid Controller (SPI)')
    parser.add_argument('--bus', type=int, default=SPI_BUS,
                        help=f'SPI bus number (default: {SPI_BUS})')
    parser.add_argument('--device', type=int, default=SPI_DEVICE,
                        help=f'SPI device/CS number (default: {SPI_DEVICE})')
    parser.add_argument('--spi-speed', type=int, default=SPI_SPEED,
                        help=f'SPI bus speed in Hz (default: {SPI_SPEED})')
    parser.add_argument('--mode', type=int, default=SPI_MODE,
                        choices=[0, 1, 2, 3],
                        help=f'SPI mode (default: {SPI_MODE})')
    parser.add_argument('--brightness', type=int, default=50,
                        help='LED brightness 0-255 (default: 50)')
    parser.add_argument('--strips', type=int, default=DEFAULT_NUM_STRIPS,
                        help=f'Number of strips (default: {DEFAULT_NUM_STRIPS})')
    parser.add_argument('--leds-per-strip', type=int, default=DEFAULT_LED_PER_STRIP,
                        help=f'LEDs per strip (default: {DEFAULT_LED_PER_STRIP})')
    parser.add_argument('--debug', action='store_true', help='Enable verbose controller output')
    
    subparsers = parser.add_subparsers(dest='command', help='Command to execute')
    
    rainbow_parser = subparsers.add_parser('rainbow', help='Rainbow animation')
    rainbow_parser.add_argument('--speed', type=float, default=0.3, dest='anim_speed')
    rainbow_parser.add_argument('--duration', type=float, default=None)
    
    solid_parser = subparsers.add_parser('solid', help='Solid color')
    solid_parser.add_argument('r', type=int, help='Red (0-255)')
    solid_parser.add_argument('g', type=int, help='Green (0-255)')
    solid_parser.add_argument('b', type=int, help='Blue (0-255)')
    
    subparsers.add_parser('test', help='Test each strip')
    subparsers.add_parser('clear', help='Clear all LEDs')
    
    parse_fn = getattr(parser, 'parse_known_intermixed_args', None)
    norm_argv = _normalize_global_args(sys.argv[1:])

    if parse_fn is None:
        args = parser.parse_args(norm_argv)
    else:
        try:
            args, extras = parse_fn(norm_argv)
            if extras:
                parser.error(f"unrecognized arguments: {' '.join(extras)}")
        except TypeError:
            args = parser.parse_args(norm_argv)
    
    controller = None
    try:
        controller = LEDController(bus=args.bus, device=args.device,
                                  speed=args.spi_speed, mode=args.mode,
                                  strips=args.strips, leds_per_strip=args.leds_per_strip,
                                  debug=args.debug)

        controller.set_brightness(args.brightness)
        if controller.debug:
            print(f"Brightness set to {args.brightness}\n")
        controller.configure()

        if args.command == 'rainbow':
            rainbow_animation(controller,
                               duration=args.duration,
                               speed=args.anim_speed)
        elif args.command == 'solid':
            solid_color(controller, args.r, args.g, args.b)
        elif args.command == 'test':
            test_strips(controller)
        elif args.command == 'clear':
            controller.clear()
            if controller.debug:
                print("All LEDs cleared")
        else:
            rainbow_animation(controller)

    except Exception as e:
        print(f"\nError: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if controller:
            controller.close()
            if controller.debug:
                print("\nSPI connection closed")


if __name__ == '__main__':
    main()
