#!/usr/bin/env python3
"""
LED Grid Controller - SPI version
Controls one ESP32-S3 LED receiver over SPI.
"""

import time
import colorsys
import argparse
import binascii
from dataclasses import replace
import struct
import spidev
import sys
import threading

import numpy as np

from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP

# LED Configuration defaults
DEFAULT_LED_PER_STRIP = DEFAULT_LEDS_PER_STRIP
DEFAULT_NUM_STRIPS = DEFAULT_STRIP_COUNT

# SPI Configuration
SPI_BUS = 0  # SPI bus number (0 = /dev/spidev0.X)
SPI_DEVICE = 0  # CE0 on the selected Raspberry Pi SPI bus
SPI_SPEED = 20000000  # 20 MHz - CRC-16 protects against corruption
SPI_MODE = 0  # CPOL=0, CPHA=0 - universal mode supported by all Pi SPI buses
SPI_INTER_FRAME_DELAY = 0.0  # No delay needed - SPI is stable now

MAX_SPI_TRANSFER = 4096
CRC_BYTES = 2
RECEIVER_STATUS_MAGIC = (ord('L'), ord('G'), ord('S'), ord('1'))
RECEIVER_STATUS_MAGIC_V2 = (ord('L'), ord('G'), ord('S'), ord('2'))
RECEIVER_STATUS_MAGIC_V3 = (ord('L'), ord('G'), ord('S'), ord('3'))
RECEIVER_STATUS_MAGIC_V4 = (ord('L'), ord('G'), ord('S'), ord('4'))
RECEIVER_STATUS_MAGIC_V5 = (ord('L'), ord('G'), ord('S'), ord('5'))
RECEIVER_STATUS_BYTES = 29
RECEIVER_STATUS_BYTES_V2 = 64
RECEIVER_STATUS_BYTES_V3 = 320
RECEIVER_STATUS_BYTES_V4 = 416
RECEIVER_STATUS_BYTES_V5 = 768
# The ESP32 slave keeps two response buffers queued. A command's result is
# therefore observable after two complete status-query transfers.
SPI_RESPONSE_QUEUE_DEPTH = 2
COMMAND_ACK_MAX_STATUS_QUERIES = 16
COMMAND_ACK_POLL_INTERVAL_SECONDS = 0.001
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
CMD_STATUS_QUERY = 0x08
CMD_LOCAL_BACKGROUND_START = 0x10
CMD_LOCAL_BACKGROUND_STOP = 0x11
CMD_LOCAL_BACKGROUND_PARAMS = 0x12
CMD_CONTROLLER_SESSION_BEGIN = 0x20
CMD_PRESENTATION_CONTEXT_BEGIN = 0x21
CMD_PRESENTATION_CONTEXT_SET = 0x22
CMD_PRESENTATION_CONTEXT_COMMIT = 0x23
CMD_OVERLAY_BEGIN = 0x30
CMD_OVERLAY_PATCH = 0x31
CMD_OVERLAY_COMMIT = 0x32
CMD_OVERLAY_CLEAR = 0x33
CMD_OVERLAY_RENEW = 0x34
CMD_OVERLAY_PATCH_BATCH = 0x35
CMD_PROFILE_PREFLIGHT = 0x40
CMD_PROFILE_BEGIN = 0x41
CMD_PROFILE_CHUNK = 0x42
CMD_PROFILE_FINALIZE = 0x43
CMD_PROFILE_VERIFY = 0x44
CMD_PROFILE_ACTIVATE = 0x45
CMD_PROFILE_RESTORE = 0x46
CMD_PROFILE_ABORT = 0x47
CMD_PING = 0xFF

LOCAL_BACKGROUND_RAINBOW = 1
MIN_LOCAL_BACKGROUND_CADENCE_HZ = 1
MAX_LOCAL_BACKGROUND_CADENCE_HZ = 200
PRESENTATION_CONTEXT_VERSION = 1
PRESENTATION_CONTEXT_BEGIN_BYTES = 58
PRESENTATION_CONTEXT_SET_MIN_BYTES = 145
PRESENTATION_CONTEXT_SET_MAX_BYTES = 187
PRESENTATION_CONTEXT_COMMIT_BYTES = 74
SPARSE_OVERLAY_PROTOCOL_VERSION = 1
CONTROLLER_SESSION_BYTES = 16
SNAPSHOT_DIGEST_BYTES = 32
CONTROLLER_SESSION_BEGIN_BYTES = 58
OVERLAY_BEGIN_BYTES = 66
OVERLAY_PATCH_HEADER_BYTES = 30
OVERLAY_COMMIT_BYTES = 50
OVERLAY_CLEAR_BYTES = 34
OVERLAY_RENEW_BYTES = 30
OVERLAY_PATCH_BATCH_HEADER_BYTES = 28
OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES = 4
OVERLAY_FORMAT_PREMULTIPLIED_RGBA8 = 1
OVERLAY_UPDATE_FULL_SNAPSHOT = 1
OVERLAY_UPDATE_DELTA = 2
OVERLAY_LOCAL_PIXELS = 8 * 138
MAX_RGBA_PIXELS_PER_PATCH = (
    MAX_SPI_TRANSFER - OVERLAY_PATCH_HEADER_BYTES - CRC_BYTES
) // 4
MAX_RGBA_PIXELS_PER_BATCH_SPAN = (
    MAX_SPI_TRANSFER
    - OVERLAY_PATCH_BATCH_HEADER_BYTES
    - OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES
    - CRC_BYTES
) // 4
PROFILE_BINDING_BYTES = 64
PROFILE_PREFLIGHT_BYTES = 69
PROFILE_BEGIN_BYTES = 81
PROFILE_CHUNK_HEADER_BYTES = 5
MAX_PROFILE_CHUNK_BYTES = MAX_SPI_TRANSFER - PROFILE_CHUNK_HEADER_BYTES - CRC_BYTES
PROFILE_BINDING_COMMAND_BYTES = 65
PROFILE_ACTIVATE_BYTES = 73
PROFILE_RESTORE_BYTES = 204
PROFILE_RESULT_NAMES = {
    0: "none",
    1: "ok",
    2: "unsupported",
    3: "invalid_size",
    4: "invalid_state",
    5: "invalid_token",
    6: "invalid_offset",
    7: "digest_mismatch",
    8: "invalid_profile",
    9: "wrong_device",
    10: "wrong_geometry",
    11: "storage_error",
    12: "no_space",
    13: "not_found",
    14: "conflict",
    15: "pinned",
    16: "integrity_error",
}
PROFILE_TRANSFER_STATE_NAMES = {
    0: "idle",
    1: "preflight_ready",
    2: "receiving",
    3: "finalizing",
    4: "staged",
    5: "failed",
}
OVERLAY_OPERATION_RESULT_NAMES = {
    0: "none",
    1: "ok",
    2: "idempotent",
    3: "unsupported_version",
    4: "unsupported_format",
    5: "invalid_size",
    6: "out_of_bounds",
    7: "stale_session",
    8: "stale_revision",
    9: "stale_generation",
    10: "generation_conflict",
    11: "prior_generation_mismatch",
    12: "patch_order",
    13: "patch_overlap",
    14: "patch_conflict",
    15: "base_binding_mismatch",
    16: "incomplete",
    17: "lease_expired",
    18: "invalid_state",
    19: "counter_exhausted",
}

# Receiver capability bits. The Phase 3A bits remain independent of sparse
# overlay support so legacy local-background checks do not require Phase 3B.
# coordinator before any local-playback command is issued.
CAPABILITY_STATIC_LOCAL_BACKGROUND = 1 << 0
CAPABILITY_PRESENTATION_CONTEXT_V1 = 1 << 1
CAPABILITY_STATUS_V3 = 1 << 2
CAPABILITY_EXPLICIT_BASE_OWNERSHIP = 1 << 3
CAPABILITY_SPARSE_OVERLAY_V1 = 1 << 4
CAPABILITY_SPARSE_OVERLAY_BATCH_V1 = 1 << 5
CAPABILITY_INSTALLATION_PROFILE_V1 = 1 << 6
CAPABILITY_STATUS_V5 = 1 << 7


class LEDController:
    """Control LED strips via SPI"""
    
    def __init__(self, bus=SPI_BUS, device=SPI_DEVICE, speed=SPI_SPEED, mode=SPI_MODE,
                 strips=DEFAULT_NUM_STRIPS, leds_per_strip=DEFAULT_LED_PER_STRIP,
                 debug=False, logical_device_id=None,
                 reverse_native_strip_order=False):
        if type(reverse_native_strip_order) is not bool:
            raise TypeError("reverse_native_strip_order must be a boolean")
        self.debug = debug
        self.bus = bus
        self.device = device
        self.logical_device_id = self._optional_logical_device_id(logical_device_id)
        self.reverse_native_strip_order = reverse_native_strip_order
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
        self._transport_lock = threading.RLock()

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
        self._receiver_base_mode = 0
        self._receiver_foreground_state = 0
        self._receiver_maintenance_state = 0
        self._receiver_last_result = 0
        self._receiver_transition_reason = 0
        self._receiver_context_state = 0
        self._receiver_component_id = 0
        self._receiver_declared_cadence_hz = 0
        self._receiver_luminance_q8_8 = 256
        self._receiver_global_strip_offset = 0
        self._receiver_common_seed = 0
        self._receiver_scene_epoch = 0
        self._receiver_active_scene_revision = 0
        self._receiver_local_frames_rendered = 0
        self._receiver_local_cadence_deadlines = 0
        self._receiver_local_missed_deadlines = 0
        self._receiver_last_local_render_us = 0
        self._receiver_max_local_render_us = 0
        self._receiver_last_frame_scene_time_us = 0
        self._receiver_active_context_digest = None
        self._receiver_staged_context_digest = None
        self._receiver_staged_scene_revision = 0
        self._receiver_vibe_revision = 0
        self._receiver_vibe_digest = None
        self._receiver_plant_modifier_revision = 0
        self._receiver_plant_modifier_digest = None
        self._receiver_active_session_id = None
        self._receiver_staged_session_id = None
        self._receiver_logical_device = None
        self._receiver_last_processed_command = 0
        self._receiver_operation_sequence = 0
        self._receiver_overlay_operation_result = 0
        self._receiver_overlay_update_kind = 0
        self._receiver_overlay_expected_patches = 0
        self._receiver_overlay_accepted_patches = 0
        self._receiver_overlay_committed_coverage_pixels = 0
        self._receiver_overlay_committed_generation = 0
        self._receiver_overlay_staged_generation = 0
        self._receiver_foreground_scene_revision = 0
        self._receiver_foreground_scene_epoch = 0
        self._receiver_foreground_base_revision = 0
        self._receiver_foreground_present_at_scene_time_us = 0
        self._receiver_overlay_lease_ms = 0
        self._receiver_overlay_lease_remaining_ms = 0
        self._receiver_overlay_session_id = None
        self._receiver_overlay_composite_frames = 0
        self._receiver_overlay_last_composite_us = 0
        self._receiver_overlay_max_composite_us = 0
        self._receiver_overlay_commits = 0
        self._receiver_overlay_expirations = 0
        self._receiver_profile_result = 0
        self._receiver_profile_transfer_state = 0
        self._receiver_profile_decoder_error = 0
        self._receiver_profile_flags = 0
        self._receiver_profile_capacity_bytes = 0
        self._receiver_profile_used_bytes = 0
        self._receiver_profile_free_bytes = 0
        self._receiver_profile_reserve_bytes = 0
        self._receiver_profile_reclaimable_bytes = 0
        self._receiver_profile_received_bytes = 0
        self._receiver_profile_total_bytes = 0
        self._receiver_profile_state_generation = 0
        self._receiver_profile_preflight_token = 0
        self._receiver_profile_last_probe_payload_digest = None
        self._receiver_profile_transfer_global_digest = None
        self._receiver_profile_transfer_payload_digest = None
        self._receiver_profile_active_global_digest = None
        self._receiver_profile_active_payload_digest = None
        self._receiver_profile_staged_global_digest = None
        self._receiver_profile_staged_payload_digest = None
        self._receiver_profile_rollback_global_digest = None
        self._receiver_profile_rollback_payload_digest = None
        self._receiver_profile_writes = 0
        self._receiver_profile_evictions = 0
        self._receiver_profile_stages = 0
        self._receiver_profile_verifies = 0
        self._receiver_profile_activations = 0
        self._receiver_profile_restores = 0
        self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V3
        self._presentation_commit_context_cache = {}
        self._monotonic_ns = time.monotonic_ns
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
        transport_lock = getattr(self, "_transport_lock", None)
        if transport_lock is None:
            transport_lock = self._transport_lock = threading.RLock()
        with transport_lock:
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

    @staticmethod
    def _response_u64(response, offset):
        value = 0
        for index in range(8):
            value = (value << 8) | int(response[offset + index])
        return value

    @staticmethod
    def _bounded_uint(name, value, maximum):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be an integer")
        if value < 0 or value > maximum:
            raise ValueError(f"{name} must be between 0 and {maximum}")
        return value

    @classmethod
    def _optional_logical_device_id(cls, value):
        if value is None:
            return None
        return cls._bounded_uint("logical_device_id", value, 3)

    @staticmethod
    def _fixed_bytes(name, value, size):
        if not isinstance(value, bytes):
            raise TypeError(f"{name} must be bytes")
        if len(value) != size:
            raise ValueError(f"{name} must be exactly {size} bytes")
        return value

    @classmethod
    def _controller_session(cls, value):
        return cls._fixed_bytes("controller_session_id", value, CONTROLLER_SESSION_BYTES)

    @staticmethod
    def _profile_digest(name, value):
        if not isinstance(value, str) or len(value) != 64 or value != value.lower():
            raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
        try:
            decoded = bytes.fromhex(value)
        except ValueError as exc:
            raise ValueError(
                f"{name} must be a lowercase SHA-256 hex digest"
            ) from exc
        if decoded.hex() != value:
            raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")
        return decoded

    @classmethod
    def _profile_binding(cls, binding, *, field):
        if binding is None:
            return b"\0" + bytes(PROFILE_BINDING_BYTES)
        if isinstance(binding, tuple) and len(binding) == 2:
            profile_id, payload_digest = binding
        else:
            profile_id = getattr(binding, "profile_id", None)
            payload_digest = getattr(binding, "payload_digest", None)
        return (
            b"\1"
            + cls._profile_digest(f"{field}.profile_id", profile_id)
            + cls._profile_digest(f"{field}.payload_digest", payload_digest)
        )

    @staticmethod
    def _premultiplied_rgba_bytes(value, *, maximum=MAX_RGBA_PIXELS_PER_PATCH):
        if isinstance(value, np.ndarray):
            if value.dtype != np.uint8:
                raise TypeError("premultiplied_rgba must have dtype uint8")
            if value.ndim != 2 or value.shape[1] != 4:
                raise ValueError("premultiplied_rgba must have shape (N, 4)")
            if not value.flags.c_contiguous:
                raise ValueError("premultiplied_rgba must be C-contiguous")
            rgba = value.tobytes()
        elif isinstance(value, bytes):
            rgba = value
        else:
            raise TypeError("premultiplied_rgba must be bytes or a numpy uint8 array")
        if not rgba or len(rgba) % 4:
            raise ValueError("premultiplied_rgba must contain one or more RGBA pixels")
        count = len(rgba) // 4
        if count > maximum:
            raise ValueError(
                f"premultiplied_rgba may contain at most {maximum} pixels"
            )
        channels = memoryview(rgba).cast("B")
        for offset in range(0, len(channels), 4):
            alpha = channels[offset + 3]
            if (
                channels[offset] > alpha
                or channels[offset + 1] > alpha
                or channels[offset + 2] > alpha
            ):
                raise ValueError("premultiplied RGBA requires every RGB channel <= alpha")
        return rgba, count

    @classmethod
    def _local_background_fields(
        cls, preferred_cadence_hz, global_strip_offset, common_seed
    ):
        cadence = cls._bounded_uint(
            "preferred_cadence_hz", preferred_cadence_hz, 0xFFFF
        )
        if not MIN_LOCAL_BACKGROUND_CADENCE_HZ <= cadence <= MAX_LOCAL_BACKGROUND_CADENCE_HZ:
            raise ValueError(
                "preferred_cadence_hz must be between "
                f"{MIN_LOCAL_BACKGROUND_CADENCE_HZ} and "
                f"{MAX_LOCAL_BACKGROUND_CADENCE_HZ}"
            )
        return (
            cadence,
            cls._bounded_uint("global_strip_offset", global_strip_offset, 0xFFFFFFFF),
            cls._bounded_uint("common_seed", common_seed, 0xFFFFFFFF),
        )

    def _update_receiver_status(self, response):
        """Parse the ESP32 status snapshot returned alongside an SPI write."""
        # SPI is full duplex, so the response can only be as long as the
        # command. Short control/configuration transfers cannot carry either
        # status structure and therefore are not telemetry misses.
        if response is None or len(response) < RECEIVER_STATUS_BYTES:
            return
        if len(response) < RECEIVER_STATUS_BYTES_V2 and getattr(
            self, '_receiver_status_version', 0
        ) >= 2:
            # A v2 receiver needs a 64-byte transaction to return its complete
            # atomic status snapshot. Do not interpret a truncated prefix.
            return

        magic = tuple(int(response[index]) for index in range(4))
        if magic == RECEIVER_STATUS_MAGIC_V5 and len(response) >= RECEIVER_STATUS_BYTES_V5:
            self._update_receiver_status_v5(response)
            return
        if magic == RECEIVER_STATUS_MAGIC_V4 and len(response) >= RECEIVER_STATUS_BYTES_V4:
            self._update_receiver_status_v4(response)
            return
        if magic == RECEIVER_STATUS_MAGIC_V3 and len(response) >= RECEIVER_STATUS_BYTES_V3:
            self._update_receiver_status_v3(response)
            return

        if magic == RECEIVER_STATUS_MAGIC_V2 and len(response) >= RECEIVER_STATUS_BYTES_V2:
            self._receiver_status_seen = True
            self._receiver_status_version = int(response[4])
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
            return

        if magic != RECEIVER_STATUS_MAGIC:
            if getattr(self, '_receiver_status_seen', False):
                self._receiver_status_misses = getattr(self, '_receiver_status_misses', 0) + 1
            return

        self._receiver_status_seen = True
        self._receiver_status_version = 1
        self._receiver_status_responses = getattr(self, '_receiver_status_responses', 0) + 1
        self._receiver_packets = self._response_u32(response, 4)
        self._receiver_crc_errors = self._response_u32(response, 8)
        self._receiver_crc_ok_packets = self._response_u32(response, 12)
        self._receiver_frames_rendered = self._response_u32(response, 16)
        self._receiver_last_crc_us = self._response_u16(response, 20)
        self._receiver_last_copy_us = self._response_u16(response, 22)
        self._receiver_last_show_us = self._response_u16(response, 24)
        self._receiver_active_strips = int(response[26])
        self._receiver_leds_per_strip = self._response_u16(response, 27)

    def _update_receiver_status_v3(self, response):
        """Parse status v3 after the firmware-defined layout is available."""
        # Phase 3A deliberately retains the complete v2 prefix so old counters
        # and operational dashboards do not disappear when local playback is
        # enabled. The extension offsets below are synchronized with
        # firmware/esp32/include/ledgrid/protocol.hpp.
        self._receiver_status_seen = True
        self._receiver_status_version = int(response[4])
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
        self._receiver_base_mode = int(response[68])
        self._receiver_foreground_state = int(response[69])
        self._receiver_maintenance_state = int(response[70])
        self._receiver_transition_reason = int(response[71])
        self._receiver_last_result = int(response[72])
        self._receiver_context_state = int(response[73])
        self._receiver_component_id = self._response_u16(response, 74)
        self._receiver_declared_cadence_hz = self._response_u16(response, 76)
        self._receiver_luminance_q8_8 = self._response_u16(response, 78)
        self._receiver_global_strip_offset = self._response_u32(response, 80)
        self._receiver_common_seed = self._response_u32(response, 84)
        self._receiver_scene_epoch = self._response_u64(response, 88)
        self._receiver_active_scene_revision = self._response_u64(response, 96)
        self._receiver_vibe_revision = self._response_u64(response, 104)
        self._receiver_plant_modifier_revision = self._response_u64(response, 112)
        self._receiver_local_cadence_deadlines = self._response_u32(response, 120)
        self._receiver_local_frames_rendered = self._response_u32(response, 124)
        self._receiver_local_missed_deadlines = self._response_u32(response, 128)
        self._receiver_last_local_render_us = self._response_u16(response, 132)
        self._receiver_max_local_render_us = self._response_u16(response, 134)
        self._receiver_last_frame_scene_time_us = self._response_u64(response, 136)
        digest_fields = (
            ("_receiver_active_context_digest", 144),
            ("_receiver_vibe_digest", 176),
            ("_receiver_plant_modifier_digest", 208),
        )
        for name, offset in digest_fields:
            digest = bytes(response[offset:offset + 32])
            setattr(self, name, digest.hex() if any(digest) else None)
        self._receiver_staged_scene_revision = self._response_u64(response, 240)
        staged_digest = bytes(response[248:280])
        self._receiver_staged_context_digest = (
            staged_digest.hex() if any(staged_digest) else None
        )
        active_session = bytes(response[280:296])
        staged_session = bytes(response[296:312])
        self._receiver_active_session_id = (
            active_session.hex() if any(active_session) else None
        )
        self._receiver_staged_session_id = (
            staged_session.hex() if any(staged_session) else None
        )
        self._receiver_logical_device = int(response[312])
        self._receiver_last_processed_command = int(response[313])
        self._receiver_operation_sequence = self._response_u32(response, 316)
        if (
            self._receiver_capabilities & CAPABILITY_INSTALLATION_PROFILE_V1
            and self._receiver_capabilities & CAPABILITY_STATUS_V5
        ):
            self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V5
        elif self._receiver_capabilities & CAPABILITY_SPARSE_OVERLAY_V1:
            # Status v4 preserves this entire prefix. Discover support through
            # the legacy-safe 320-byte query before asking for the extension.
            self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V4
        else:
            # A receiver may restart into a feature-off image while this host
            # process survives. Return to the universally supported v3 query.
            self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V3

        # A sparse-capable receiver deliberately queues a legacy-safe v3
        # response for every non-status command. Do not combine that fresh v3
        # prefix with an older cached v4 overlay extension: callers must either
        # observe a coherent v3 snapshot with no extension or wait for the next
        # negotiated v4 response.
        response_magic = tuple(int(response[index]) for index in range(4))
        if response_magic == RECEIVER_STATUS_MAGIC_V3:
            self._clear_receiver_overlay_status()
            self._clear_receiver_profile_status()

    def _clear_receiver_overlay_status(self):
        """Drop v4-only telemetry after an actual status-v3 response."""
        self._receiver_overlay_operation_result = 0
        self._receiver_overlay_update_kind = 0
        self._receiver_overlay_expected_patches = 0
        self._receiver_overlay_accepted_patches = 0
        self._receiver_overlay_committed_coverage_pixels = 0
        self._receiver_overlay_committed_generation = 0
        self._receiver_overlay_staged_generation = 0
        self._receiver_foreground_scene_revision = 0
        self._receiver_foreground_scene_epoch = 0
        self._receiver_foreground_base_revision = 0
        self._receiver_foreground_present_at_scene_time_us = 0
        self._receiver_overlay_lease_ms = 0
        self._receiver_overlay_lease_remaining_ms = 0
        self._receiver_overlay_session_id = None
        self._receiver_overlay_composite_frames = 0
        self._receiver_overlay_last_composite_us = 0
        self._receiver_overlay_max_composite_us = 0
        self._receiver_overlay_commits = 0
        self._receiver_overlay_expirations = 0

    def _update_receiver_status_v4(self, response):
        """Parse the status-v4 sparse-overlay extension after its v3 prefix."""
        self._update_receiver_status_v3(response)
        self._receiver_overlay_operation_result = int(response[320])
        self._receiver_overlay_update_kind = int(response[321])
        self._receiver_overlay_expected_patches = self._response_u16(response, 322)
        self._receiver_overlay_accepted_patches = self._response_u16(response, 324)
        self._receiver_overlay_committed_coverage_pixels = self._response_u16(
            response, 326
        )
        self._receiver_overlay_committed_generation = self._response_u64(response, 328)
        self._receiver_overlay_staged_generation = self._response_u64(response, 336)
        self._receiver_foreground_scene_revision = self._response_u64(response, 344)
        self._receiver_foreground_scene_epoch = self._response_u64(response, 352)
        self._receiver_foreground_base_revision = self._response_u64(response, 360)
        self._receiver_foreground_present_at_scene_time_us = self._response_u64(
            response, 368
        )
        self._receiver_overlay_lease_ms = self._response_u32(response, 376)
        self._receiver_overlay_lease_remaining_ms = self._response_u32(response, 380)
        session = bytes(response[384:400])
        # Session IDs are opaque 16-byte values, so the all-zero value is valid
        # and must not be collapsed into the no-status sentinel.
        self._receiver_overlay_session_id = session.hex()
        self._receiver_overlay_composite_frames = self._response_u32(response, 400)
        self._receiver_overlay_last_composite_us = self._response_u16(response, 404)
        self._receiver_overlay_max_composite_us = self._response_u16(response, 406)
        self._receiver_overlay_commits = self._response_u32(response, 408)
        self._receiver_overlay_expirations = self._response_u32(response, 412)
        if tuple(int(response[index]) for index in range(4)) == RECEIVER_STATUS_MAGIC_V4:
            self._clear_receiver_profile_status()

    def _clear_receiver_profile_status(self):
        """Drop status-v5-only profile telemetry after a real downgrade."""
        self._receiver_profile_result = 0
        self._receiver_profile_transfer_state = 0
        self._receiver_profile_decoder_error = 0
        self._receiver_profile_flags = 0
        self._receiver_profile_capacity_bytes = 0
        self._receiver_profile_used_bytes = 0
        self._receiver_profile_free_bytes = 0
        self._receiver_profile_reserve_bytes = 0
        self._receiver_profile_reclaimable_bytes = 0
        self._receiver_profile_received_bytes = 0
        self._receiver_profile_total_bytes = 0
        self._receiver_profile_state_generation = 0
        self._receiver_profile_preflight_token = 0
        for name in (
            "_receiver_profile_last_probe_payload_digest",
            "_receiver_profile_transfer_global_digest",
            "_receiver_profile_transfer_payload_digest",
            "_receiver_profile_active_global_digest",
            "_receiver_profile_active_payload_digest",
            "_receiver_profile_staged_global_digest",
            "_receiver_profile_staged_payload_digest",
            "_receiver_profile_rollback_global_digest",
            "_receiver_profile_rollback_payload_digest",
        ):
            setattr(self, name, None)
        self._receiver_profile_writes = 0
        self._receiver_profile_evictions = 0
        self._receiver_profile_stages = 0
        self._receiver_profile_verifies = 0
        self._receiver_profile_activations = 0
        self._receiver_profile_restores = 0

    @staticmethod
    def _optional_digest_from_response(response, offset, *, present=True):
        digest = bytes(response[offset:offset + 32])
        return digest.hex() if present and any(digest) else None

    def _update_receiver_status_v5(self, response):
        """Parse the status-v5 profile extension after its exact v4 prefix."""
        self._update_receiver_status_v4(response)
        flags = int(response[419])
        self._receiver_profile_result = int(response[416])
        self._receiver_profile_transfer_state = int(response[417])
        self._receiver_profile_decoder_error = int(response[418])
        self._receiver_profile_flags = flags
        self._receiver_profile_capacity_bytes = self._response_u32(response, 420)
        self._receiver_profile_used_bytes = self._response_u32(response, 424)
        self._receiver_profile_free_bytes = self._response_u32(response, 428)
        self._receiver_profile_reserve_bytes = self._response_u32(response, 432)
        self._receiver_profile_reclaimable_bytes = self._response_u32(response, 436)
        self._receiver_profile_received_bytes = self._response_u32(response, 440)
        self._receiver_profile_total_bytes = self._response_u32(response, 444)
        self._receiver_profile_state_generation = self._response_u64(response, 448)
        self._receiver_profile_preflight_token = self._response_u64(response, 456)
        self._receiver_profile_last_probe_payload_digest = (
            self._optional_digest_from_response(response, 464, present=bool(flags & 0x04))
        )
        self._receiver_profile_transfer_global_digest = (
            self._optional_digest_from_response(response, 496, present=bool(flags & 0x40))
        )
        self._receiver_profile_transfer_payload_digest = (
            self._optional_digest_from_response(response, 528, present=bool(flags & 0x40))
        )
        binding_fields = (
            ("active", 0x08, 560, 592),
            ("staged", 0x10, 624, 656),
            ("rollback", 0x20, 688, 720),
        )
        for name, bit, global_offset, payload_offset in binding_fields:
            present = bool(flags & bit)
            setattr(
                self,
                f"_receiver_profile_{name}_global_digest",
                self._optional_digest_from_response(
                    response, global_offset, present=present
                ),
            )
            setattr(
                self,
                f"_receiver_profile_{name}_payload_digest",
                self._optional_digest_from_response(
                    response, payload_offset, present=present
                ),
            )
        self._receiver_profile_writes = self._response_u32(response, 752)
        self._receiver_profile_evictions = self._response_u32(response, 756)
        self._receiver_profile_stages = self._response_u16(response, 760)
        self._receiver_profile_verifies = self._response_u16(response, 762)
        self._receiver_profile_activations = self._response_u16(response, 764)
        self._receiver_profile_restores = self._response_u16(response, 766)

    def query_receiver_status(self):
        """Clock out the newest discovered status snapshot without changing ownership."""
        payload = bytearray(
            getattr(self, "_receiver_status_query_bytes", RECEIVER_STATUS_BYTES_V3)
        )
        payload[0] = CMD_STATUS_QUERY
        self._xfer(payload)
        return self.get_stats()

    def _command_status(
        self, payload, *, command=None, required_status_version=3
    ):
        """Send a command and prove its exact acknowledgement, never a stale OK."""
        transport_lock = getattr(self, "_transport_lock", None)
        if transport_lock is None:
            transport_lock = self._transport_lock = threading.RLock()
        with transport_lock:
            payload_factory = payload if callable(payload) else None
            if payload_factory is None:
                command = int(payload[0])
            elif command is None:
                raise ValueError("deferred command serialization requires a command ID")
            else:
                command = self._bounded_uint("command", command, 0xFF)
            prior = None
            for query_index in range(SPI_RESPONSE_QUEUE_DEPTH):
                if query_index:
                    time.sleep(COMMAND_ACK_POLL_INTERVAL_SECONDS)
                prior = self.query_receiver_status()
            if int(prior.get("receiver_status_version", 0) or 0) < 3:
                raise RuntimeError("receiver status v3 is required for command acknowledgement")
            prior_sequence = int(prior.get("receiver_operation_sequence", 0) or 0)
            if prior_sequence >= 0xFFFFFFFF:
                raise RuntimeError("receiver operation sequence is exhausted")
            if payload_factory is not None:
                payload = payload_factory()
                if not payload or int(payload[0]) != command:
                    raise ValueError("deferred serializer returned the wrong command")
            self._xfer(payload)
            required_version = self._bounded_uint(
                "required_status_version", required_status_version, 0xFF
            )
            if required_version < 3 or required_version > 5:
                raise ValueError("required_status_version must be 3, 4, or 5")
            # The slave has to queue a response before it knows the length of
            # the master's next transfer. A sparse command therefore leaves
            # one legacy-safe v3 snapshot in the two-deep queue; clock one
            # additional query to receive the requested v4 extension. Larger
            # commands can take longer than those minimum queue drains on real
            # hardware, so continue polling within one small fixed bound while
            # still accepting only the exact next operation sequence.
            minimum_post_queries = SPI_RESPONSE_QUEUE_DEPTH + (
                required_version >= 4
            )
            status = None
            expected_sequence = prior_sequence + 1
            for query_index in range(COMMAND_ACK_MAX_STATUS_QUERIES):
                # The receiver validates and dispatches the command before it
                # can refill the consumed slave-DMA slot. In particular, a
                # maximum sparse batch performs CRC, digest, span validation,
                # and RGBA staging work here. Pace the first acknowledgement
                # query as well as every subsequent one so the two-deep queue
                # is never consumed by an unbounded initial burst.
                time.sleep(COMMAND_ACK_POLL_INTERVAL_SECONDS)
                status = self.query_receiver_status()
                if query_index + 1 < minimum_post_queries:
                    continue
                observed_version = int(
                    status.get("receiver_status_version", 0) or 0
                )
                observed_command = int(
                    status.get("receiver_last_processed_command", -1)
                )
                observed_sequence = int(
                    status.get("receiver_operation_sequence", -1)
                )
                if (
                    observed_version >= required_version
                    and observed_command == command
                    and observed_sequence == expected_sequence
                ):
                    return status
                if observed_sequence > expected_sequence or (
                    observed_sequence == expected_sequence
                    and observed_command != command
                ):
                    break
            raise RuntimeError(
                f"receiver did not acknowledge command 0x{command:02x} "
                "with the next operation sequence; last status "
                f"v{int((status or {}).get('receiver_status_version', 0) or 0)}, "
                "command "
                f"0x{int((status or {}).get('receiver_last_processed_command', 0) or 0):02x}, "
                f"sequence {int((status or {}).get('receiver_operation_sequence', -1))} "
                f"(expected {expected_sequence}), CRC errors "
                f"{int((status or {}).get('receiver_crc_errors', 0) or 0)}, "
                "SPI queue errors "
                f"{int((status or {}).get('receiver_spi_queue_errors', 0) or 0)}, "
                "display errors "
                f"{int((status or {}).get('receiver_display_errors', 0) or 0)}"
            )

    @classmethod
    def serialize_local_background_start(
        cls,
        *,
        component_id=LOCAL_BACKGROUND_RAINBOW,
        preferred_cadence_hz,
        global_strip_offset,
        common_seed,
        scene_epoch,
    ):
        component = cls._bounded_uint("component_id", component_id, 0xFFFF)
        if component != LOCAL_BACKGROUND_RAINBOW:
            raise ValueError(
                f"component_id must be {LOCAL_BACKGROUND_RAINBOW} for the static rainbow"
            )
        cadence, offset, seed = cls._local_background_fields(
            preferred_cadence_hz, global_strip_offset, common_seed
        )
        epoch = cls._bounded_uint("scene_epoch", scene_epoch, 0xFFFFFFFFFFFFFFFF)
        return struct.pack(">BHHIIQ", CMD_LOCAL_BACKGROUND_START, component,
                           cadence, offset, seed, epoch)

    @classmethod
    def serialize_local_background_params(
        cls, *, preferred_cadence_hz, global_strip_offset, common_seed
    ):
        cadence, offset, seed = cls._local_background_fields(
            preferred_cadence_hz, global_strip_offset, common_seed
        )
        return struct.pack(">BHII", CMD_LOCAL_BACKGROUND_PARAMS, cadence, offset, seed)

    def start_local_background(self, **kwargs):
        return self._command_status(self.serialize_local_background_start(**kwargs))

    def stop_local_background(self):
        return self._command_status(bytes((CMD_LOCAL_BACKGROUND_STOP,)))

    def update_local_background_params(self, **kwargs):
        return self._command_status(self.serialize_local_background_params(**kwargs))

    @classmethod
    def serialize_profile_preflight(
        cls, *, profile_id, payload_digest, payload_size
    ):
        size = cls._bounded_uint("payload_size", payload_size, 0xFFFFFFFF)
        if size == 0:
            raise ValueError("payload_size must be positive")
        return (
            bytes((CMD_PROFILE_PREFLIGHT,))
            + cls._profile_digest("profile_id", profile_id)
            + cls._profile_digest("payload_digest", payload_digest)
            + struct.pack(">I", size)
        )

    @classmethod
    def serialize_profile_begin(
        cls,
        *,
        preflight_token,
        profile_id,
        payload_digest,
        payload_size,
        logical_receiver_id,
        strip_origin,
        reversed_strip_order,
    ):
        token = cls._bounded_uint(
            "preflight_token", preflight_token, 0xFFFFFFFFFFFFFFFF
        )
        if token == 0:
            raise ValueError("preflight_token must be positive")
        size = cls._bounded_uint("payload_size", payload_size, 0xFFFFFFFF)
        if size == 0:
            raise ValueError("payload_size must be positive")
        logical = cls._bounded_uint("logical_receiver_id", logical_receiver_id, 3)
        origin = cls._bounded_uint("strip_origin", strip_origin, 0xFFFF)
        if type(reversed_strip_order) is not bool:
            raise TypeError("reversed_strip_order must be a boolean")
        return (
            bytes((CMD_PROFILE_BEGIN,))
            + struct.pack(">Q", token)
            + cls._profile_digest("profile_id", profile_id)
            + cls._profile_digest("payload_digest", payload_digest)
            + struct.pack(">I", size)
            + bytes((logical,))
            + struct.pack(">H", origin)
            + bytes((int(reversed_strip_order),))
        )

    @classmethod
    def serialize_profile_chunk(cls, *, offset, data):
        normalized_offset = cls._bounded_uint("offset", offset, 0xFFFFFFFF)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("profile chunk data must be bytes-like")
        chunk = bytes(data)
        if not 1 <= len(chunk) <= MAX_PROFILE_CHUNK_BYTES:
            raise ValueError(
                f"profile chunk data must contain 1..{MAX_PROFILE_CHUNK_BYTES} bytes"
            )
        if normalized_offset + len(chunk) > 0x100000000:
            raise ValueError("profile chunk range exceeds uint32 address space")
        return bytes((CMD_PROFILE_CHUNK,)) + struct.pack(">I", normalized_offset) + chunk

    @classmethod
    def _serialize_profile_binding_command(
        cls, command, *, profile_id, payload_digest
    ):
        return (
            bytes((command,))
            + cls._profile_digest("profile_id", profile_id)
            + cls._profile_digest("payload_digest", payload_digest)
        )

    @classmethod
    def serialize_profile_finalize(cls, **kwargs):
        return cls._serialize_profile_binding_command(
            CMD_PROFILE_FINALIZE, **kwargs
        )

    @classmethod
    def serialize_profile_verify(cls, **kwargs):
        return cls._serialize_profile_binding_command(CMD_PROFILE_VERIFY, **kwargs)

    @classmethod
    def serialize_profile_activate(
        cls, *, expected_generation, profile_id, payload_digest
    ):
        generation = cls._bounded_uint(
            "expected_generation", expected_generation, 0xFFFFFFFFFFFFFFFF
        )
        return (
            bytes((CMD_PROFILE_ACTIVATE,))
            + struct.pack(">Q", generation)
            + cls._profile_digest("profile_id", profile_id)
            + cls._profile_digest("payload_digest", payload_digest)
        )

    @classmethod
    def serialize_profile_restore(
        cls,
        *,
        expected_generation,
        active_binding,
        staged_binding,
        rollback_binding,
    ):
        generation = cls._bounded_uint(
            "expected_generation", expected_generation, 0xFFFFFFFFFFFFFFFF
        )
        return (
            bytes((CMD_PROFILE_RESTORE,))
            + struct.pack(">Q", generation)
            + cls._profile_binding(active_binding, field="active_binding")
            + cls._profile_binding(staged_binding, field="staged_binding")
            + cls._profile_binding(rollback_binding, field="rollback_binding")
        )

    def profile_preflight(self, **kwargs):
        return self._command_status(
            self.serialize_profile_preflight(**kwargs), required_status_version=5
        )

    def profile_begin(self, **kwargs):
        return self._command_status(
            self.serialize_profile_begin(**kwargs), required_status_version=5
        )

    def profile_chunk(self, **kwargs):
        return self._command_status(
            self.serialize_profile_chunk(**kwargs), required_status_version=5
        )

    def profile_finalize(self, **kwargs):
        return self._command_status(
            self.serialize_profile_finalize(**kwargs), required_status_version=5
        )

    def profile_verify(self, **kwargs):
        return self._command_status(
            self.serialize_profile_verify(**kwargs), required_status_version=5
        )

    def profile_activate(self, **kwargs):
        return self._command_status(
            self.serialize_profile_activate(**kwargs), required_status_version=5
        )

    def profile_restore(self, **kwargs):
        return self._command_status(
            self.serialize_profile_restore(**kwargs), required_status_version=5
        )

    def profile_abort(self):
        return self._command_status(
            bytes((CMD_PROFILE_ABORT,)), required_status_version=5
        )

    @classmethod
    def serialize_controller_session_begin(
        cls, *, controller_session_id, desired_revision,
        authoritative_snapshot_digest
    ):
        session = cls._controller_session(controller_session_id)
        revision = cls._bounded_uint(
            "desired_revision", desired_revision, 0xFFFFFFFFFFFFFFFF
        )
        digest = cls._fixed_bytes(
            "authoritative_snapshot_digest",
            authoritative_snapshot_digest,
            SNAPSHOT_DIGEST_BYTES,
        )
        return struct.pack(
            ">BB16sQ32s", CMD_CONTROLLER_SESSION_BEGIN,
            SPARSE_OVERLAY_PROTOCOL_VERSION, session, revision, digest,
        )

    @classmethod
    def serialize_overlay_begin(
        cls, *, controller_session_id, generation, prior_generation,
        scene_revision, scene_epoch, base_revision,
        format=OVERLAY_FORMAT_PREMULTIPLIED_RGBA8,
        update_kind, expected_patches, lease_ms
    ):
        session = cls._controller_session(controller_session_id)
        integers = (
            cls._bounded_uint("generation", generation, 0xFFFFFFFFFFFFFFFF),
            cls._bounded_uint(
                "prior_generation", prior_generation, 0xFFFFFFFFFFFFFFFF
            ),
            cls._bounded_uint(
                "scene_revision", scene_revision, 0xFFFFFFFFFFFFFFFF
            ),
            cls._bounded_uint("scene_epoch", scene_epoch, 0xFFFFFFFFFFFFFFFF),
            cls._bounded_uint("base_revision", base_revision, 0xFFFFFFFFFFFFFFFF),
        )
        wire_format = cls._bounded_uint("format", format, 0xFF)
        if wire_format != OVERLAY_FORMAT_PREMULTIPLIED_RGBA8:
            raise ValueError(
                f"format must be {OVERLAY_FORMAT_PREMULTIPLIED_RGBA8}"
            )
        kind = cls._bounded_uint("update_kind", update_kind, 0xFF)
        if kind not in (OVERLAY_UPDATE_FULL_SNAPSHOT, OVERLAY_UPDATE_DELTA):
            raise ValueError("update_kind must be full snapshot (1) or delta (2)")
        patch_count = cls._bounded_uint("expected_patches", expected_patches, 0xFFFF)
        if kind == OVERLAY_UPDATE_FULL_SNAPSHOT and patch_count == 0:
            raise ValueError("a full snapshot must declare at least one patch")
        lease = cls._bounded_uint("lease_ms", lease_ms, 0xFFFFFFFF)
        return struct.pack(
            ">BB16sQQQQQBBHI", CMD_OVERLAY_BEGIN,
            SPARSE_OVERLAY_PROTOCOL_VERSION, session, *integers,
            wire_format, kind, patch_count, lease,
        )

    @classmethod
    def serialize_overlay_patch(
        cls, *, controller_session_id, generation, start,
        premultiplied_rgba
    ):
        session = cls._controller_session(controller_session_id)
        overlay_generation = cls._bounded_uint(
            "generation", generation, 0xFFFFFFFFFFFFFFFF
        )
        first_pixel = cls._bounded_uint("start", start, 0xFFFF)
        rgba, count = cls._premultiplied_rgba_bytes(premultiplied_rgba)
        if first_pixel + count > OVERLAY_LOCAL_PIXELS:
            raise ValueError(
                f"overlay patch [{first_pixel}, {first_pixel + count}) exceeds "
                f"the {OVERLAY_LOCAL_PIXELS}-pixel receiver"
            )
        return struct.pack(
            ">BB16sQHH", CMD_OVERLAY_PATCH, SPARSE_OVERLAY_PROTOCOL_VERSION,
            session, overlay_generation, first_pixel, count,
        ) + rgba

    @classmethod
    def serialize_overlay_patch_batch(
        cls, *, controller_session_id, generation, spans
    ):
        """Serialize one atomic, ordered multi-span foreground patch packet."""
        session = cls._controller_session(controller_session_id)
        overlay_generation = cls._bounded_uint(
            "generation", generation, 0xFFFFFFFFFFFFFFFF
        )
        try:
            span_items = tuple(spans)
        except TypeError as exc:
            raise TypeError("spans must be an iterable of (start, RGBA) pairs") from exc
        if not span_items:
            raise ValueError("an overlay patch batch must contain at least one span")

        encoded = []
        prior_end = 0
        packet_bytes = OVERLAY_PATCH_BATCH_HEADER_BYTES + CRC_BYTES
        for index, item in enumerate(span_items):
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError("each batch span must be a (start, RGBA) pair")
            first_pixel = cls._bounded_uint("start", item[0], 0xFFFF)
            rgba, count = cls._premultiplied_rgba_bytes(
                item[1], maximum=MAX_RGBA_PIXELS_PER_BATCH_SPAN
            )
            if first_pixel + count > OVERLAY_LOCAL_PIXELS:
                raise ValueError(
                    f"overlay batch span [{first_pixel}, {first_pixel + count}) "
                    f"exceeds the {OVERLAY_LOCAL_PIXELS}-pixel receiver"
                )
            if index and first_pixel < prior_end:
                raise ValueError(
                    "overlay batch spans must be sorted and non-overlapping"
                )
            packet_bytes += OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES + len(rgba)
            if packet_bytes > MAX_SPI_TRANSFER:
                raise ValueError(
                    f"overlay patch batch exceeds the {MAX_SPI_TRANSFER}-byte "
                    "SPI transaction ceiling including CRC"
                )
            encoded.append((first_pixel, count, rgba))
            prior_end = first_pixel + count

        header = struct.pack(
            ">BB16sQH",
            CMD_OVERLAY_PATCH_BATCH,
            SPARSE_OVERLAY_PROTOCOL_VERSION,
            session,
            overlay_generation,
            len(encoded),
        )
        body = bytearray()
        for first_pixel, count, rgba in encoded:
            body.extend(struct.pack(">HH", first_pixel, count))
            body.extend(rgba)
        return header + body

    @classmethod
    def serialize_overlay_patch_batches(
        cls, *, controller_session_id, generation, patches, update_kind
    ):
        """Validate and greedily pack ordered spans into atomic batch packets."""
        kind = cls._bounded_uint("update_kind", update_kind, 0xFF)
        if kind not in (OVERLAY_UPDATE_FULL_SNAPSHOT, OVERLAY_UPDATE_DELTA):
            raise ValueError("update_kind must be full snapshot (1) or delta (2)")
        try:
            patch_items = tuple(patches)
        except TypeError as exc:
            raise TypeError("patches must be an iterable of (start, RGBA) pairs") from exc

        normalized = []
        prior_end = 0
        for index, item in enumerate(patch_items):
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError("each patch must be a (start, RGBA) pair")
            first_pixel = cls._bounded_uint("start", item[0], 0xFFFF)
            rgba, count = cls._premultiplied_rgba_bytes(
                item[1], maximum=OVERLAY_LOCAL_PIXELS
            )
            if first_pixel + count > OVERLAY_LOCAL_PIXELS:
                raise ValueError(
                    f"overlay patch [{first_pixel}, {first_pixel + count}) exceeds "
                    f"the {OVERLAY_LOCAL_PIXELS}-pixel receiver"
                )
            if index and first_pixel < prior_end:
                raise ValueError("overlay patches must be sorted and non-overlapping")
            if kind == OVERLAY_UPDATE_FULL_SNAPSHOT and first_pixel != prior_end:
                raise ValueError(
                    "full-snapshot patches must be contiguous from pixel zero"
                )
            offset = 0
            while offset < count:
                span_count = min(MAX_RGBA_PIXELS_PER_BATCH_SPAN, count - offset)
                byte_start = offset * 4
                byte_end = byte_start + span_count * 4
                normalized.append((
                    first_pixel + offset,
                    rgba[byte_start:byte_end],
                ))
                offset += span_count
            prior_end = first_pixel + count

        if kind == OVERLAY_UPDATE_FULL_SNAPSHOT:
            if not normalized or prior_end != OVERLAY_LOCAL_PIXELS:
                raise ValueError(
                    "full-snapshot patches must cover every receiver pixel exactly"
                )
        if not normalized:
            return ()

        packets = []
        packet_spans = []
        packet_bytes = OVERLAY_PATCH_BATCH_HEADER_BYTES + CRC_BYTES
        for span in normalized:
            span_bytes = OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES + len(span[1])
            if packet_spans and packet_bytes + span_bytes > MAX_SPI_TRANSFER:
                packets.append(cls.serialize_overlay_patch_batch(
                    controller_session_id=controller_session_id,
                    generation=generation,
                    spans=packet_spans,
                ))
                packet_spans = []
                packet_bytes = OVERLAY_PATCH_BATCH_HEADER_BYTES + CRC_BYTES
            packet_spans.append(span)
            packet_bytes += span_bytes
        if packet_spans:
            packets.append(cls.serialize_overlay_patch_batch(
                controller_session_id=controller_session_id,
                generation=generation,
                spans=packet_spans,
            ))
        return tuple(packets)

    @classmethod
    def serialize_overlay_commit(
        cls, *, controller_session_id, generation, scene_epoch,
        base_revision, present_at_scene_time_us
    ):
        return struct.pack(
            ">BB16sQQQQ", CMD_OVERLAY_COMMIT, SPARSE_OVERLAY_PROTOCOL_VERSION,
            cls._controller_session(controller_session_id),
            cls._bounded_uint("generation", generation, 0xFFFFFFFFFFFFFFFF),
            cls._bounded_uint("scene_epoch", scene_epoch, 0xFFFFFFFFFFFFFFFF),
            cls._bounded_uint("base_revision", base_revision, 0xFFFFFFFFFFFFFFFF),
            cls._bounded_uint(
                "present_at_scene_time_us", present_at_scene_time_us,
                0xFFFFFFFFFFFFFFFF,
            ),
        )

    @classmethod
    def serialize_overlay_clear(
        cls, *, controller_session_id, generation, scene_revision
    ):
        return struct.pack(
            ">BB16sQQ", CMD_OVERLAY_CLEAR, SPARSE_OVERLAY_PROTOCOL_VERSION,
            cls._controller_session(controller_session_id),
            cls._bounded_uint("generation", generation, 0xFFFFFFFFFFFFFFFF),
            cls._bounded_uint(
                "scene_revision", scene_revision, 0xFFFFFFFFFFFFFFFF
            ),
        )

    @classmethod
    def serialize_overlay_renew(
        cls, *, controller_session_id, generation, lease_ms
    ):
        return struct.pack(
            ">BB16sQI", CMD_OVERLAY_RENEW, SPARSE_OVERLAY_PROTOCOL_VERSION,
            cls._controller_session(controller_session_id),
            cls._bounded_uint("generation", generation, 0xFFFFFFFFFFFFFFFF),
            cls._bounded_uint("lease_ms", lease_ms, 0xFFFFFFFF),
        )

    def begin_controller_session(self, **kwargs):
        return self._overlay_command_status(
            self.serialize_controller_session_begin(**kwargs)
        )

    def begin_overlay(self, **kwargs):
        return self._overlay_command_status(self.serialize_overlay_begin(**kwargs))

    def send_overlay_patch(self, **kwargs):
        return self._overlay_command_status(self.serialize_overlay_patch(**kwargs))

    def commit_overlay(self, **kwargs):
        return self._overlay_command_status(self.serialize_overlay_commit(**kwargs))

    def clear_overlay(self, **kwargs):
        return self._overlay_command_status(self.serialize_overlay_clear(**kwargs))

    def renew_overlay(self, **kwargs):
        return self._overlay_command_status(self.serialize_overlay_renew(**kwargs))

    def send_overlay_patch_batch(self, **kwargs):
        if not (
            int(getattr(self, "_receiver_capabilities", 0) or 0)
            & CAPABILITY_SPARSE_OVERLAY_BATCH_V1
        ):
            raise RuntimeError(
                "receiver has not advertised sparse-overlay batch-v1 support"
            )
        return self._overlay_command_status(
            self.serialize_overlay_patch_batch(**kwargs)
        )

    def _overlay_command_status(self, payload):
        status = self._command_status(payload, required_status_version=4)
        if int(status.get("receiver_status_version", 0) or 0) < 4:
            raise RuntimeError("receiver status v4 is required for sparse-overlay results")
        result = int(status.get("receiver_overlay_operation_result", 0) or 0)
        if result not in (1, 2):
            result_name = OVERLAY_OPERATION_RESULT_NAMES.get(
                result, f"unknown_{result}"
            )
            raise RuntimeError(
                f"receiver rejected sparse-overlay command 0x{payload[0]:02x} "
                f"with result {result_name} ({result})"
            )
        return status

    def send_overlay_patches(
        self, *, controller_session_id, generation, patches, update_kind
    ):
        """Validate all spans before I/O and acknowledge each atomic batch once."""
        patch_items = tuple(patches)
        packets = self.serialize_overlay_patch_batches(
            controller_session_id=controller_session_id,
            generation=generation,
            patches=patch_items,
            update_kind=update_kind,
        )
        if not (
            int(getattr(self, "_receiver_capabilities", 0) or 0)
            & CAPABILITY_SPARSE_OVERLAY_BATCH_V1
        ):
            # A sparse-v1 receiver can still consume the original single-span
            # packets. The batch serializer above performs the complete
            # ordering/full-coverage preflight before any legacy packet is sent.
            packets = tuple(
                self.serialize_overlay_patch(
                    controller_session_id=controller_session_id,
                    generation=generation,
                    start=start,
                    premultiplied_rgba=rgba,
                )
                for start, rgba in patch_items
            )
        statuses = []
        for packet in packets:
            statuses.append(self._overlay_command_status(packet))
        return statuses

    @staticmethod
    def _validate_presentation_packet(payload, command, minimum, maximum=None):
        try:
            packet = bytes(payload)
        except (TypeError, ValueError) as exc:
            raise TypeError("presentation context packet must be bytes-like") from exc
        maximum = minimum if maximum is None else maximum
        if not minimum <= len(packet) <= maximum:
            expected = str(minimum) if minimum == maximum else f"{minimum}..{maximum}"
            raise ValueError(f"presentation context packet must be {expected} bytes")
        if packet[0] != command or packet[1] != PRESENTATION_CONTEXT_VERSION:
            raise ValueError("presentation context command/version mismatch")
        return packet

    def begin_presentation_context(self, context):
        from animation.core.receiver_presentation import encode_presentation_context_begin

        packet = self._validate_presentation_packet(
            encode_presentation_context_begin(context),
            CMD_PRESENTATION_CONTEXT_BEGIN,
            PRESENTATION_CONTEXT_BEGIN_BYTES,
        )
        return self._command_status(packet)

    def set_presentation_context(self, context):
        from animation.core.receiver_presentation import encode_presentation_context_set

        packet = self._validate_presentation_packet(
            encode_presentation_context_set(context),
            CMD_PRESENTATION_CONTEXT_SET,
            PRESENTATION_CONTEXT_SET_MIN_BYTES,
            PRESENTATION_CONTEXT_SET_MAX_BYTES,
        )
        return self._command_status(packet)

    def commit_presentation_context(
        self, context, *, host_monotonic_anchor_ns=None
    ):
        from animation.core.receiver_presentation import encode_presentation_context_commit

        monotonic_ns = getattr(self, "_monotonic_ns", time.monotonic_ns)
        commit_cache = getattr(self, "_presentation_commit_context_cache", None)
        if commit_cache is None:
            commit_cache = self._presentation_commit_context_cache = {}
        if host_monotonic_anchor_ns is None:
            host_monotonic_anchor_ns = monotonic_ns()
        anchor = self._bounded_uint(
            "host_monotonic_anchor_ns", host_monotonic_anchor_ns, 0xFFFFFFFFFFFFFFFF
        )
        cache_key = (
            context.controller_session_id,
            context.scene_revision,
            context.context_digest,
        )

        def packet_after_ack_drain():
            cached = commit_cache.get(cache_key)
            if cached is None:
                now_ns = monotonic_ns()
                if now_ns < anchor:
                    raise RuntimeError("host monotonic clock moved before the commit anchor")
                elapsed_host_us = (now_ns - anchor) // 1000
                present_at = context.present_at_scene_time_us + elapsed_host_us
                if present_at > 0xFFFFFFFFFFFFFFFF:
                    raise ValueError("compensated presentation scene time exceeds uint64")
                cached = replace(
                    context, present_at_scene_time_us=present_at
                )
                # Only the latest scene can be actively retried. Retaining old
                # compensated schedules would grow once per scene for the
                # controller process lifetime without a valid replay use-case.
                commit_cache.clear()
                commit_cache[cache_key] = cached
            return self._validate_presentation_packet(
                encode_presentation_context_commit(cached),
                CMD_PRESENTATION_CONTEXT_COMMIT,
                PRESENTATION_CONTEXT_COMMIT_BYTES,
            )

        return self._command_status(
            packet_after_ack_drain, command=CMD_PRESENTATION_CONTEXT_COMMIT
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
            logical_device_id = self._optional_logical_device_id(
                getattr(self, "logical_device_id", None)
            )
            if (
                logical_device_id is not None
                and getattr(self, "_receiver_status_version", 0) >= 3
                and getattr(self, "_receiver_capabilities", 0) & CAPABILITY_STATUS_V3
            ):
                # Byte 4 remains the legacy debug byte for four/five-byte
                # CONFIG.  Status-v3 receivers interpret bit 7 only when the
                # logical receiver byte makes this the six-byte form.
                if getattr(self, "reverse_native_strip_order", False):
                    cfg[4] |= 0x80
                cfg.append(logical_device_id)
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
            'receiver_base_mode': self._receiver_base_mode,
            'receiver_foreground_state': self._receiver_foreground_state,
            'receiver_maintenance_state': self._receiver_maintenance_state,
            'receiver_last_result': self._receiver_last_result,
            'receiver_transition_reason': self._receiver_transition_reason,
            'receiver_context_state': self._receiver_context_state,
            'receiver_component_id': self._receiver_component_id,
            'receiver_declared_cadence_hz': self._receiver_declared_cadence_hz,
            'receiver_luminance_q8_8': self._receiver_luminance_q8_8,
            'receiver_global_strip_offset': self._receiver_global_strip_offset,
            'receiver_common_seed': self._receiver_common_seed,
            'receiver_scene_epoch': self._receiver_scene_epoch,
            'receiver_active_scene_revision': self._receiver_active_scene_revision,
            'receiver_local_frames_rendered': self._receiver_local_frames_rendered,
            'receiver_local_cadence_deadlines': self._receiver_local_cadence_deadlines,
            'receiver_local_missed_deadlines': self._receiver_local_missed_deadlines,
            'receiver_last_local_render_us': self._receiver_last_local_render_us,
            'receiver_max_local_render_us': self._receiver_max_local_render_us,
            'receiver_last_frame_scene_time_us': self._receiver_last_frame_scene_time_us,
            'receiver_active_context_digest': self._receiver_active_context_digest,
            'receiver_staged_context_digest': self._receiver_staged_context_digest,
            'receiver_staged_scene_revision': self._receiver_staged_scene_revision,
            'receiver_vibe_revision': self._receiver_vibe_revision,
            'receiver_vibe_digest': self._receiver_vibe_digest,
            'receiver_plant_modifier_revision': self._receiver_plant_modifier_revision,
            'receiver_plant_modifier_digest': self._receiver_plant_modifier_digest,
            'receiver_active_session_id': self._receiver_active_session_id,
            'receiver_staged_session_id': self._receiver_staged_session_id,
            'receiver_logical_device': self._receiver_logical_device,
            'receiver_last_processed_command': self._receiver_last_processed_command,
            'receiver_operation_sequence': self._receiver_operation_sequence,
            'receiver_overlay_operation_result': getattr(
                self, '_receiver_overlay_operation_result', 0
            ),
            'receiver_overlay_operation_result_name': OVERLAY_OPERATION_RESULT_NAMES.get(
                getattr(self, '_receiver_overlay_operation_result', 0), 'unknown'
            ),
            'receiver_overlay_update_kind': getattr(
                self, '_receiver_overlay_update_kind', 0
            ),
            'receiver_overlay_expected_patches': getattr(
                self, '_receiver_overlay_expected_patches', 0
            ),
            'receiver_overlay_accepted_patches': getattr(
                self, '_receiver_overlay_accepted_patches', 0
            ),
            'receiver_overlay_committed_coverage_pixels': getattr(
                self, '_receiver_overlay_committed_coverage_pixels', 0
            ),
            'receiver_overlay_committed_generation': getattr(
                self, '_receiver_overlay_committed_generation', 0
            ),
            'receiver_overlay_staged_generation': getattr(
                self, '_receiver_overlay_staged_generation', 0
            ),
            'receiver_foreground_scene_revision': getattr(
                self, '_receiver_foreground_scene_revision', 0
            ),
            'receiver_foreground_scene_epoch': getattr(
                self, '_receiver_foreground_scene_epoch', 0
            ),
            'receiver_foreground_base_revision': getattr(
                self, '_receiver_foreground_base_revision', 0
            ),
            'receiver_foreground_present_at_scene_time_us': getattr(
                self, '_receiver_foreground_present_at_scene_time_us', 0
            ),
            'receiver_overlay_lease_ms': getattr(
                self, '_receiver_overlay_lease_ms', 0
            ),
            'receiver_overlay_lease_remaining_ms': getattr(
                self, '_receiver_overlay_lease_remaining_ms', 0
            ),
            'receiver_overlay_session_id': getattr(
                self, '_receiver_overlay_session_id', None
            ),
            'receiver_overlay_composite_frames': getattr(
                self, '_receiver_overlay_composite_frames', 0
            ),
            'receiver_overlay_last_composite_us': getattr(
                self, '_receiver_overlay_last_composite_us', 0
            ),
            'receiver_overlay_max_composite_us': getattr(
                self, '_receiver_overlay_max_composite_us', 0
            ),
            'receiver_overlay_commits': getattr(
                self, '_receiver_overlay_commits', 0
            ),
            'receiver_overlay_expirations': getattr(
                self, '_receiver_overlay_expirations', 0
            ),
            'receiver_profile_result': getattr(self, '_receiver_profile_result', 0),
            'receiver_profile_result_name': PROFILE_RESULT_NAMES.get(
                getattr(self, '_receiver_profile_result', 0), 'unknown'
            ),
            'receiver_profile_transfer_state': getattr(
                self, '_receiver_profile_transfer_state', 0
            ),
            'receiver_profile_transfer_state_name': PROFILE_TRANSFER_STATE_NAMES.get(
                getattr(self, '_receiver_profile_transfer_state', 0), 'unknown'
            ),
            'receiver_profile_decoder_error': getattr(
                self, '_receiver_profile_decoder_error', 0
            ),
            'receiver_profile_flags': getattr(self, '_receiver_profile_flags', 0),
            'receiver_profile_cache_integrity_ok': bool(
                getattr(self, '_receiver_profile_flags', 0) & 0x01
            ),
            'receiver_profile_preflight_can_stage': bool(
                getattr(self, '_receiver_profile_flags', 0) & 0x02
            ),
            'receiver_profile_last_probe_found': bool(
                getattr(self, '_receiver_profile_flags', 0) & 0x04
            ),
            'receiver_profile_transfer_active': bool(
                getattr(self, '_receiver_profile_flags', 0) & 0x40
            ),
            'receiver_profile_capacity_bytes': getattr(
                self, '_receiver_profile_capacity_bytes', 0
            ),
            'receiver_profile_used_bytes': getattr(
                self, '_receiver_profile_used_bytes', 0
            ),
            'receiver_profile_free_bytes': getattr(
                self, '_receiver_profile_free_bytes', 0
            ),
            'receiver_profile_reserve_bytes': getattr(
                self, '_receiver_profile_reserve_bytes', 0
            ),
            'receiver_profile_reclaimable_bytes': getattr(
                self, '_receiver_profile_reclaimable_bytes', 0
            ),
            'receiver_profile_received_bytes': getattr(
                self, '_receiver_profile_received_bytes', 0
            ),
            'receiver_profile_total_bytes': getattr(
                self, '_receiver_profile_total_bytes', 0
            ),
            'receiver_profile_state_generation': getattr(
                self, '_receiver_profile_state_generation', 0
            ),
            'receiver_profile_preflight_token': getattr(
                self, '_receiver_profile_preflight_token', 0
            ),
            'receiver_profile_last_probe_payload_digest': getattr(
                self, '_receiver_profile_last_probe_payload_digest', None
            ),
            'receiver_profile_transfer_global_digest': getattr(
                self, '_receiver_profile_transfer_global_digest', None
            ),
            'receiver_profile_transfer_payload_digest': getattr(
                self, '_receiver_profile_transfer_payload_digest', None
            ),
            'receiver_profile_active_global_digest': getattr(
                self, '_receiver_profile_active_global_digest', None
            ),
            'receiver_profile_active_payload_digest': getattr(
                self, '_receiver_profile_active_payload_digest', None
            ),
            'receiver_profile_staged_global_digest': getattr(
                self, '_receiver_profile_staged_global_digest', None
            ),
            'receiver_profile_staged_payload_digest': getattr(
                self, '_receiver_profile_staged_payload_digest', None
            ),
            'receiver_profile_rollback_global_digest': getattr(
                self, '_receiver_profile_rollback_global_digest', None
            ),
            'receiver_profile_rollback_payload_digest': getattr(
                self, '_receiver_profile_rollback_payload_digest', None
            ),
            'receiver_profile_writes': getattr(self, '_receiver_profile_writes', 0),
            'receiver_profile_evictions': getattr(
                self, '_receiver_profile_evictions', 0
            ),
            'receiver_profile_stages': getattr(self, '_receiver_profile_stages', 0),
            'receiver_profile_verifies': getattr(
                self, '_receiver_profile_verifies', 0
            ),
            'receiver_profile_activations': getattr(
                self, '_receiver_profile_activations', 0
            ),
            'receiver_profile_restores': getattr(
                self, '_receiver_profile_restores', 0
            ),
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
