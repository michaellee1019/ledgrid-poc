#!/usr/bin/env python3
"""
LED Grid Controller - SPI version
Controls one ESP32-S3 LED receiver over SPI.
"""

import time
import colorsys
import argparse
import binascii
import math
from pathlib import Path
import struct
import spidev
import sys
import threading

import numpy as np

from drivers.led_layout import DEFAULT_LEDS_PER_STRIP

# LED Configuration defaults
DEFAULT_LED_PER_STRIP = DEFAULT_LEDS_PER_STRIP
# One LEDController addresses one receiver, not the global wall. The finalized
# roster has four 8-wide receivers and a separately configured 1-wide tail.
DEFAULT_NUM_STRIPS = 8

# SPI Configuration
SPI_BUS = 0  # SPI bus number (0 = /dev/spidev0.X)
SPI_DEVICE = 0  # CE0 on the selected Raspberry Pi SPI bus
SPI_SPEED = 20000000  # 20 MHz - CRC-16 protects against corruption
SPI_MODE = 0  # CPOL=0, CPHA=0 - universal mode supported by all Pi SPI buses

MAX_SPI_TRANSFER = 4096
SPIDEV_BUFFER_SIZE_PATH = Path("/sys/module/spidev/parameters/bufsiz")
CRC_BYTES = 2
SPI_DMA_ALIGNMENT_BYTES = 4
ALIGNED_ENVELOPE_VERSION = 1
ALIGNED_ENVELOPE_HEADER_BYTES = 4
MAX_ALIGNED_SEMANTIC_BYTES = (
    MAX_SPI_TRANSFER - ALIGNED_ENVELOPE_HEADER_BYTES - CRC_BYTES
)
RECEIVER_STATUS_MAGIC = (ord('L'), ord('G'), ord('S'), ord('1'))
RECEIVER_STATUS_MAGIC_V2 = (ord('L'), ord('G'), ord('S'), ord('2'))
RECEIVER_STATUS_MAGIC_V3 = (ord('L'), ord('G'), ord('S'), ord('3'))
RECEIVER_STATUS_MAGIC_V4 = (ord('L'), ord('G'), ord('S'), ord('4'))
RECEIVER_STATUS_MAGIC_V5 = (ord('L'), ord('G'), ord('S'), ord('5'))
RECEIVER_STATUS_MAGIC_V6 = (ord('L'), ord('G'), ord('S'), ord('6'))
RECEIVER_STATUS_MAGIC_V7 = (ord('L'), ord('G'), ord('S'), ord('7'))
RECEIVER_STATUS_MAGIC_V8 = (ord('L'), ord('G'), ord('S'), ord('8'))
RECEIVER_STATUS_BYTES = 29
RECEIVER_STATUS_BYTES_V2 = 68
RECEIVER_STATUS_BYTES_V3 = 320
RECEIVER_STATUS_BYTES_V4 = 416
RECEIVER_STATUS_BYTES_V5 = 768
RECEIVER_STATUS_BYTES_V6 = 1216
RECEIVER_STATUS_BYTES_V7 = 1248
RECEIVER_STATUS_BYTES_V8 = 1252
# The ESP32 slave keeps two response buffers queued. A command's result is
# therefore observable after two complete status-query transfers.
SPI_RESPONSE_QUEUE_DEPTH = 2
# Full-frame streaming does not consume a command acknowledgement, but parsing
# the several-kilobyte full-duplex response from every SET_ALL forces spidev to
# materialize thousands of Python integers per receiver and frame.  Keep an
# ordinary fresh status sample from every receiver at least once per 128 frames.
# Receivers 0-3 capture it in-band on their broad SET_ALL transaction.  The
# one-strip tail cannot clock the full status block with its shorter SET_ALL, so
# its scheduled phase uses one status-length query immediately before the
# write-only frame.  That query must count a CRC-valid v8 snapshot as a sample
# even when STATUS_QUERY does not advance receiver_packets; otherwise the gap
# fallback drains SPI0 on every later frame and caps wall send time around 16 ms.
# Other explicit status queries and control commands remain full duplex.  The
# installed phases are distinct so one wall frame samples at most one receiver.
FULL_FRAME_STATUS_SAMPLE_INTERVAL = 128
FULL_FRAME_STATUS_SAMPLE_RECEIVERS = 5
# A streamed receiver can be inside a roughly 4.5 ms parallel-LED presentation
# when its completed SPI transaction becomes available to the receiver task.
# Pace queue-drain queries beyond that installed display cycle so the third
# transfer can clock a causally current protected snapshot instead of an older
# queued v8 response.
FRESH_STATUS_DRAIN_INTERVAL_SECONDS = 0.005
MAX_PIXELS_SET_ALL = (MAX_ALIGNED_SEMANTIC_BYTES - 1) // 3
MAX_PIXELS_PER_RANGE = min(255, (MAX_ALIGNED_SEMANTIC_BYTES - 4) // 3)

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


def _read_spidev_buffer_size(path=SPIDEV_BUFFER_SIZE_PATH):
    """Return the proven kernel spidev transfer capacity, or ``None``."""
    try:
        value = Path(path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if not value.isdecimal():
        return None
    capacity = int(value)
    return capacity if capacity > 0 else None

# Command definitions
CMD_SET_PIXEL = 0x01
CMD_SET_BRIGHTNESS = 0x02
CMD_SHOW = 0x03
CMD_CLEAR = 0x04
CMD_SET_RANGE = 0x05
CMD_SET_ALL = 0x06
CMD_CONFIG = 0x07
CMD_STATUS_QUERY = 0x08
CMD_SET_LANE_MASK = 0x09
CMD_SET_STAGGER = 0x0A
CMD_ALIGNED_ENVELOPE = 0x0B
CMD_PING = 0xFF


def _aligned_envelope_wire_size(semantic_length):
    """Return the exact word-aligned wire size for one semantic packet."""
    if isinstance(semantic_length, bool) or not isinstance(semantic_length, int):
        raise TypeError("semantic_length must be an integer")
    if semantic_length < 1 or semantic_length > MAX_ALIGNED_SEMANTIC_BYTES:
        raise ValueError(
            f"aligned semantic packet must contain 1..{MAX_ALIGNED_SEMANTIC_BYTES} bytes"
        )
    unpadded = ALIGNED_ENVELOPE_HEADER_BYTES + semantic_length + CRC_BYTES
    padding = (-unpadded) % SPI_DMA_ALIGNMENT_BYTES
    return unpadded + padding


def _encode_aligned_envelope(payload, output=None):
    """Encode one semantic command into the CRC-covered DMA-safe envelope."""
    try:
        semantic = memoryview(payload).cast("B")
    except (TypeError, ValueError) as exc:
        raise TypeError("payload must be a contiguous bytes-like object") from exc
    semantic_length = len(semantic)
    wire_size = _aligned_envelope_wire_size(semantic_length)
    if output is None:
        wire = bytearray(wire_size)
    else:
        if not isinstance(output, bytearray) or len(output) != wire_size:
            raise ValueError("output must be a bytearray of the exact aligned wire size")
        wire = output
    wire[0] = CMD_ALIGNED_ENVELOPE
    wire[1] = ALIGNED_ENVELOPE_VERSION
    wire[2] = (semantic_length >> 8) & 0xFF
    wire[3] = semantic_length & 0xFF
    semantic_end = ALIGNED_ENVELOPE_HEADER_BYTES + semantic_length
    wire[ALIGNED_ENVELOPE_HEADER_BYTES:semantic_end] = semantic
    wire[semantic_end:-CRC_BYTES] = b"\x00" * (
        wire_size - semantic_end - CRC_BYTES
    )
    crc = _crc16_ccitt(memoryview(wire)[:-CRC_BYTES])
    wire[-2] = (crc >> 8) & 0xFF
    wire[-1] = crc & 0xFF
    return wire


CAPABILITY_STATUS_V3 = 1 << 2
CAPABILITY_STATUS_V5 = 1 << 7
CAPABILITY_STATUS_V6 = 1 << 8
CAPABILITY_ALIGNED_ENVELOPE_V1 = 1 << 14
CAPABILITY_STATUS_CRC32_V8 = 1 << 21

ALL_LANES_MASK = 0xFF
STAGGER_OFF = 1
MAX_STAGGER_PHASES = 3

class LEDController:
    """One full RGB SPI receiver with aligned CRC-protected transport."""

    def __init__(self, bus=SPI_BUS, device=SPI_DEVICE, speed=SPI_SPEED, mode=SPI_MODE,
                 strips=DEFAULT_NUM_STRIPS, leds_per_strip=DEFAULT_LED_PER_STRIP,
                 debug=False, logical_device_id=None,
                 global_strip_offset=None):
        self.debug = debug
        self.bus = bus
        self.device = device
        self.logical_device_id = self._optional_logical_device_id(logical_device_id)
        self.global_strip_offset = self._optional_global_strip_offset(
            global_strip_offset
        )
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
        # spidev.writebytes2 splits writes larger than the kernel module's
        # bufsiz across multiple write(2) operations. That would deassert chip
        # select between pieces, so the fast path is permitted only when this
        # exact capacity is readable and covers the complete wire packet.
        self._spidev_buffer_size = _read_spidev_buffer_size()

        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = self.strip_count * self.leds_per_strip
        # When True, set_all_pixels already issues CMD_SHOW so callers must not call show()
        self.inline_show = True
        self.current_brightness = None
        self._last_config_refresh = 0.0
        self._config_refresh_interval = 30.0  # seconds - reduced frequency to avoid LED blanking
        self._last_sent_config = None  # Track last config to avoid unnecessary refreshes
        self._frames_sent = 0
        self._spi_transfers = 0
        self._bytes_sent = 0
        self._semantic_bytes_sent = 0
        self._transport_envelope_bytes_sent = 0
        self._transport_padding_bytes_sent = 0
        self._full_frame_transfers = 0
        self._full_frame_status_transfers = 0
        self._full_frame_status_samples = 0
        self._full_frame_status_sample_misses = 0
        self._full_frame_write_only_transfers = 0
        self._full_frame_frames_since_status_sample = 0
        self._full_frame_max_status_sample_gap = 0
        self._full_frame_semantic_bytes_sent = 0
        self._full_frame_wire_bytes_sent = 0
        self._crc_bytes_sent = 0
        self._errors = 0
        self._last_frame_duration = 0.0
        self._total_frame_duration = 0.0
        self._receiver_status_seen = False
        self._receiver_status_version = 0
        self._receiver_status_max_version_seen = 0
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
        self._receiver_lane_mask = ALL_LANES_MASK
        self._receiver_stagger_phases = STAGGER_OFF
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
        self._receiver_stagger_phases = STAGGER_OFF
        self._receiver_last_processed_command = 0
        self._receiver_operation_sequence = 0
        self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V8
        # The installed pair uses one current framing and status contract.
        self._writebytes2_supported = None
        self._last_transfer_captured_response = False
        self._last_transfer_status_sampled = False
        self._last_transfer_status_valid = False
        self._receiver_status_integrity_required = True
        self._receiver_status_integrity_verified = False
        self._receiver_status_integrity_established = False
        self._receiver_status_integrity_fresh_count = 0
        self._receiver_status_integrity_last_packets = None
        self._receiver_status_last_packets = None
        self._receiver_status_integrity_errors = 0
        self._receiver_status_empty_responses = 0
        self._receiver_status_integrity_last_failure = None
        self._receiver_status_unprotected_rejections = 0
        self._receiver_status_current_peer_rejected = False
        self._full_frame_sequence = 0
        self._frame_packet = bytearray(1 + self.total_leds * 3 + CRC_BYTES)
        self._aligned_frame_packet = bytearray(
            _aligned_envelope_wire_size(1 + self.total_leds * 3)
        )
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

    def _xfer_packet(self, buf, payload_length, *, response_required=True):
        """Finalize and transfer a packet whose CRC storage is preallocated."""
        transport_lock = getattr(self, "_transport_lock", None)
        if transport_lock is None:
            transport_lock = self._transport_lock = threading.RLock()
        with transport_lock:
            maximum_payload = MAX_ALIGNED_SEMANTIC_BYTES
            if payload_length < 1 or payload_length > maximum_payload:
                raise ValueError(
                    f"SPI semantic transaction must be 1..{maximum_payload} bytes"
                )
            if len(buf) != payload_length + CRC_BYTES:
                raise ValueError("packet buffer must contain exactly payload plus CRC storage")
            reusable = None
            aligned_frame = getattr(self, "_aligned_frame_packet", None)
            if buf is getattr(self, "_frame_packet", None):
                expected = _aligned_envelope_wire_size(payload_length)
                if isinstance(aligned_frame, bytearray) and len(aligned_frame) == expected:
                    reusable = aligned_frame
            wire = _encode_aligned_envelope(
                memoryview(buf)[:payload_length], output=reusable
            )
            envelope_bytes = ALIGNED_ENVELOPE_HEADER_BYTES
            padding_bytes = len(wire) - envelope_bytes - payload_length - CRC_BYTES
            # Preserve the established transport-counter contract: these
            # counters describe the one kernel transfer attempt, including an
            # ambiguous ioctl failure that must never be retried.
            self._bytes_sent += len(wire)
            self._semantic_bytes_sent = (
                getattr(self, "_semantic_bytes_sent", 0) + payload_length
            )
            self._transport_envelope_bytes_sent = (
                getattr(self, "_transport_envelope_bytes_sent", 0)
                + envelope_bytes
            )
            self._transport_padding_bytes_sent = (
                getattr(self, "_transport_padding_bytes_sent", 0)
                + padding_bytes
            )
            self._crc_bytes_sent += CRC_BYTES
            self._spi_transfers += 1

            try:
                if not response_required:
                    writer = getattr(self.spi, "writebytes2", None)
                    if self._write_only_fast_path_supported(len(wire)):
                        try:
                            writer(wire)
                        except (AttributeError, NotImplementedError, TypeError):
                            # These failures mean the binding rejected the API
                            # before issuing an ioctl. Permanently fall back to
                            # the full-duplex path and send this packet once.
                            self._writebytes2_supported = False
                        else:
                            self._writebytes2_supported = True
                            self._last_transfer_captured_response = False
                            self._last_transfer_status_sampled = False
                            self._last_transfer_status_valid = False
                            return None
                    elif not callable(writer):
                        self._writebytes2_supported = False
                response = self.spi.xfer2(wire)
                status_sampled = bool(self._update_receiver_status(
                    response, full_status_expected=len(wire) >= RECEIVER_STATUS_BYTES_V8,
                    transfer_bytes=len(wire),
                ))
                self._last_transfer_captured_response = True
                self._last_transfer_status_sampled = status_sampled
                return response
            except Exception:
                self._errors += 1
                raise

    def _write_only_fast_path_supported(self, wire_length):
        """Return whether one unsplit writebytes2 transfer is proven safe."""
        capacity = getattr(self, "_spidev_buffer_size", None)
        if type(capacity) is not int or capacity < int(wire_length):
            return False
        if getattr(self, "_writebytes2_supported", None) is False:
            return False
        return callable(getattr(self.spi, "writebytes2", None))

    def _full_frame_write_only_supported(self):
        """Report support for this receiver's selected full-frame wire size."""
        wire_length = self._selected_full_frame_wire_size()
        return wire_length > 0 and self._write_only_fast_path_supported(wire_length)

    def _selected_full_frame_wire_size(self):
        return len(getattr(self, "_aligned_frame_packet", ()))

    def _claim_full_frame_sequence(self, wall_frame_sequence):
        """Claim a local sequence or adopt the manager's shared wall sequence."""
        next_sequence = getattr(self, "_full_frame_sequence", 0)
        if wall_frame_sequence is None:
            sequence = next_sequence
        else:
            if type(wall_frame_sequence) is not int or wall_frame_sequence < 0:
                raise ValueError("wall_frame_sequence must be a non-negative integer")
            sequence = wall_frame_sequence
        self._full_frame_sequence = max(next_sequence, sequence + 1)
        return sequence

    def _full_frame_status_response_required(self, wall_frame_sequence):
        """Return whether one aligned SET_ALL should retain its MISO sample."""
        wall_frame_sequence = int(wall_frame_sequence)
        logical_id = self.logical_device_id
        if type(logical_id) is not int or not 0 <= logical_id < (
            FULL_FRAME_STATUS_SAMPLE_RECEIVERS
        ):
            logical_id = 0
        phase = (
            logical_id * FULL_FRAME_STATUS_SAMPLE_INTERVAL
            // FULL_FRAME_STATUS_SAMPLE_RECEIVERS
        )
        # A rate-limited receiver can skip every sequence at its shared-wall
        # phase. Bound the gap using frames actually sent on this route too.
        return (wall_frame_sequence % FULL_FRAME_STATUS_SAMPLE_INTERVAL == phase
                or getattr(self, "_full_frame_frames_since_status_sample", 0)
                >= FULL_FRAME_STATUS_SAMPLE_INTERVAL - 1)

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
        return cls._bounded_uint("logical_device_id", value, 0xFE)

    @classmethod
    def _optional_global_strip_offset(cls, value):
        if value is None:
            return None
        return cls._bounded_uint("global_strip_offset", value, 0xFFFF)

    def _record_status_integrity_failure(self, response, reason, *, transfer_bytes=None,
                                         computed_crc32=None):
        """Retain one diagnostic only; rejected wire fields never become authority."""
        size = len(response) if response is not None else 0
        self._receiver_status_integrity_errors = getattr(
            self, "_receiver_status_integrity_errors", 0
        ) + 1
        self._receiver_status_integrity_last_failure = {
            "reason": reason,
            "transfer_index": getattr(self, "_spi_transfers", 0),
            "transfer_bytes": transfer_bytes,
            "received_bytes": size,
            "required_snapshot_bytes": RECEIVER_STATUS_BYTES_V8,
            "all_zero": not any(response) if response is not None else True,
            "untrusted_header_hex": bytes(response[:5]).hex() if size else "",
            "untrusted_claimed_crc32": self._response_u32(response, 1248) if size >= 1252 else None,
            "computed_crc32": computed_crc32,
            "untrusted_packets": self._response_u32(response, 12) if size >= 16 else None,
            "untrusted_command": int(response[313]) if size >= 314 else None,
            "untrusted_operation_sequence": self._response_u32(response, 316) if size >= 320 else None,
        }

    def _update_receiver_status(self, response, *, full_status_expected=False,
                                transfer_bytes=None):
        """Accept only the complete CRC-protected current status snapshot."""
        self._last_transfer_status_valid = False
        magic = tuple(response[:4]) if response is not None else ()
        if response is None or len(response) < RECEIVER_STATUS_BYTES_V8:
            if full_status_expected:
                self._record_status_integrity_failure(
                    response, "truncated_snapshot", transfer_bytes=transfer_bytes
                )
            return False
        if magic != RECEIVER_STATUS_MAGIC_V8:
            legacy = magic in (
                RECEIVER_STATUS_MAGIC, RECEIVER_STATUS_MAGIC_V2,
                RECEIVER_STATUS_MAGIC_V3, RECEIVER_STATUS_MAGIC_V4,
                RECEIVER_STATUS_MAGIC_V5, RECEIVER_STATUS_MAGIC_V6,
                RECEIVER_STATUS_MAGIC_V7,
            )
            if legacy:
                self._receiver_status_unprotected_rejections = getattr(
                    self, "_receiver_status_unprotected_rejections", 0
                ) + 1
                self._receiver_status_current_peer_rejected = True
            elif (full_status_expected
                  and (transfer_bytes is None or len(response) == transfer_bytes)
                  and not any(response)):
                # A full-length, entirely empty MISO reply remains an invalid
                # status. Count this observed shape separately without making
                # any untrusted status field authoritative.
                self._receiver_status_empty_responses = getattr(
                    self, "_receiver_status_empty_responses", 0
                ) + 1
            self._record_status_integrity_failure(
                response,
                "unsupported_status_version" if legacy else "invalid_header",
                transfer_bytes=transfer_bytes,
            )
            return False
        checksum = binascii.crc32(
            bytes(response[:RECEIVER_STATUS_BYTES_V7])
        ) & 0xFFFFFFFF
        if (
            int(response[4]) != 8
            or self._response_u32(response, RECEIVER_STATUS_BYTES_V7) != checksum
        ):
            self._record_status_integrity_failure(
                response,
                "invalid_version" if int(response[4]) != 8 else "crc32_mismatch",
                transfer_bytes=transfer_bytes,
                computed_crc32=checksum,
            )
            return False
        self._last_transfer_status_valid = True
        self._receiver_status_integrity_verified = True
        self._receiver_status_observed_at = time.time()
        fresh = bool(self._update_receiver_status_base(response))
        if not getattr(self, "_receiver_status_integrity_established", False):
            packets = self._receiver_packets
            previous = getattr(self, "_receiver_status_integrity_last_packets", None)
            count = getattr(self, "_receiver_status_integrity_fresh_count", 0)
            if previous is not None and packets <= previous:
                count = 0
            if fresh:
                count += 1
            self._receiver_status_integrity_last_packets = packets
            self._receiver_status_integrity_fresh_count = count
            self._receiver_status_integrity_established = (
                count > SPI_RESPONSE_QUEUE_DEPTH
            )
        return fresh

    def _note_receiver_status_version(self, version):
        """Record the actual latest response and sticky per-process maximum."""
        version = int(version)
        self._receiver_status_version = version
        self._receiver_status_max_version_seen = max(
            getattr(self, "_receiver_status_max_version_seen", 0),
            version,
        )

    def _observe_current_receiver_packets(self, receiver_packets):
        """Return whether this protected snapshot advances the receiver epoch."""
        receiver_packets = int(receiver_packets)
        previous = getattr(self, "_receiver_status_last_packets", None)
        self._receiver_status_last_packets = receiver_packets
        return previous is None or receiver_packets != previous

    def _clock_receiver_status_snapshot(self):
        """Transfer one status-length query and parse its returned snapshot."""
        transport_lock = getattr(self, "_transport_lock", None)
        if transport_lock is None:
            transport_lock = self._transport_lock = threading.RLock()
        with transport_lock:
            payload = bytearray(
                getattr(self, "_receiver_status_query_bytes", RECEIVER_STATUS_BYTES_V8)
            )
            payload[0] = CMD_STATUS_QUERY
            self._xfer(payload)

    def query_receiver_status(self):
        """Clock out the newest discovered status snapshot without changing ownership."""
        self._clock_receiver_status_snapshot()
        return self.get_stats()

    def _drain_fresh_receiver_status(self):
        """Drain the slave queue through a causally post-request snapshot."""
        transport_lock = getattr(self, "_transport_lock", None)
        if transport_lock is None:
            transport_lock = self._transport_lock = threading.RLock()
        with transport_lock:
            sampled = False
            valid = False
            for query_index in range(SPI_RESPONSE_QUEUE_DEPTH + 1):
                if query_index:
                    # Give the receiver task time to parse the preceding query
                    # and queue its requested extended snapshot.  Sending all
                    # three transfers back-to-back can drain the two old slots
                    # before the new protected response exists.
                    time.sleep(FRESH_STATUS_DRAIN_INTERVAL_SECONDS)
                self._clock_receiver_status_snapshot()
                sampled = sampled or bool(
                    getattr(self, "_last_transfer_status_sampled", False)
                )
                valid = valid or bool(
                    getattr(self, "_last_transfer_status_valid", False)
                )
            self._last_transfer_status_sampled = sampled
            self._last_transfer_status_valid = valid

    def query_fresh_receiver_status(self):
        """Drain the slave queue and return a causally post-request snapshot."""
        self._drain_fresh_receiver_status()
        return self.get_stats()

    def _refresh_configuration(self, force=False, *, acknowledged=False):
        transport_lock = getattr(self, "_transport_lock", None)
        if transport_lock is None:
            transport_lock = self._transport_lock = threading.RLock()
        with transport_lock:
            return self._refresh_configuration_locked(force, acknowledged=acknowledged)

    def _refresh_configuration_locked(self, force=False, *, acknowledged=False):
        now = time.time()

        # Only send config if it's actually different or forced
        current_config = (
            self.strip_count,
            self.leds_per_strip,
            getattr(self, "logical_device_id", None),
            getattr(self, "global_strip_offset", None),
        )
        config_changed = (self._last_sent_config != current_config)

        if force or config_changed or (now - self._last_config_refresh) > self._config_refresh_interval:
            logical_device_id = self._optional_logical_device_id(
                getattr(self, "logical_device_id", None)
            )
            global_strip_offset = self._optional_global_strip_offset(
                getattr(self, "global_strip_offset", None)
            )
            cfg = [
                CMD_CONFIG,
                self.strip_count & 0xFF,
                (self.leds_per_strip >> 8) & 0xFF,
                self.leds_per_strip & 0xFF,
                (1 if self.debug else 0)
                ,
                0 if logical_device_id is None else logical_device_id,
            ]
            cfg.extend(struct.pack(">H", 0 if global_strip_offset is None else global_strip_offset))
            self._xfer(cfg)
            self._last_config_refresh = now
            self._last_sent_config = current_config
            if self.debug:
                print(f"✓ Configuration refresh (strips={self.strip_count}, leds/strip={self.leds_per_strip})")
            return None

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
        # Brightness is an explicit 2-byte command; CONFIG does not carry it.
        # Forcing CONFIG here queued an extra mutation per receiver and could
        # overflow the ESP32 command FIFO during whole-wall brightness updates.
        self._xfer([CMD_SET_BRIGHTNESS, level])
        if self.debug:
            print(f"✓ Brightness set ({level})")

    def set_lane_mask(self, lane_mask):
        """Restrict which WS2812 lanes emit edges.

        Diagnostic aid for isolating per-lane signal integrity faults from
        faults caused by all eight lanes switching simultaneously. Masked lanes
        receive no edges at all, so their pixels hold the last latched frame.
        """
        self._refresh_configuration()
        self._xfer([CMD_SET_LANE_MASK, int(lane_mask) & 0xFF])

    def set_stagger_phases(self, phases):
        """Spread the lanes' WS2812 rising edges over this many samples.

        One phase is the original waveform with all lanes rising together.
        Higher values delay lane L by (L % phases) samples, which cuts the
        simultaneous switching current through the level shifter's supply pins
        without altering T0H, T1H, or the bit period.
        """
        value = int(phases)
        if not STAGGER_OFF <= value <= MAX_STAGGER_PHASES:
            raise ValueError(
                f"stagger phases must be {STAGGER_OFF}-{MAX_STAGGER_PHASES}"
            )
        self._refresh_configuration()
        self._xfer([CMD_SET_STAGGER, value])
        self.current_stagger_phases = value

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

    def configure(self, *, acknowledged=False):
        self.total_leds = self.strip_count * self.leds_per_strip
        expected_packet_size = 1 + self.total_leds * 3 + CRC_BYTES
        if len(self._frame_packet) != expected_packet_size:
            self._frame_packet = bytearray(expected_packet_size)
        expected_aligned_size = _aligned_envelope_wire_size(1 + self.total_leds * 3)
        if len(getattr(self, "_aligned_frame_packet", ())) != expected_aligned_size:
            self._aligned_frame_packet = bytearray(expected_aligned_size)
        status = self._refresh_configuration(force=True, acknowledged=acknowledged)
        if self.debug:
            print(f"✓ Configuration sent (strips={self.strip_count}, leds/strip={self.leds_per_strip})")
        return status

    def set_all_pixels(self, colors, *, wall_frame_sequence=None):
        """Send all pixels in one SPI transaction.

        Accepts a list of (r,g,b) tuples or a numpy uint8 array of shape (N,3).
        Multi-receiver callers pass one shared ``wall_frame_sequence`` so
        staggered samples remain aligned even after partial sends or failures.
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
                aligned_frame = True
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
                frame_sequence = self._claim_full_frame_sequence(
                    wall_frame_sequence
                )
                scheduled_status_sample = (
                    aligned_frame
                    and self._full_frame_status_response_required(frame_sequence)
                )
                status_query_bytes = int(getattr(
                    self,
                    "_receiver_status_query_bytes",
                    RECEIVER_STATUS_BYTES_V8,
                ))
                separate_status_query = (
                    scheduled_status_sample
                    and self._selected_full_frame_wire_size() < status_query_bytes
                )
                if separate_status_query:
                    # The one-strip tail cannot clock v8 status inside SET_ALL.
                    # One status-length query, then the short write-only frame.
                    transport_lock = getattr(self, "_transport_lock", None)
                    if transport_lock is None:
                        transport_lock = self._transport_lock = threading.RLock()
                    with transport_lock:
                        self._clock_receiver_status_snapshot()
                        status_sampled = bool(
                            getattr(self, "_last_transfer_status_valid", False)
                            or getattr(self, "_last_transfer_status_sampled", False)
                        )
                        self._xfer_packet(buf, payload_length, response_required=False)
                        captured_response = True
                else:
                    self._xfer_packet(
                        buf,
                        payload_length,
                        response_required=(
                            not aligned_frame or scheduled_status_sample
                        ),
                    )
                    captured_response = bool(getattr(
                        self, "_last_transfer_captured_response", False
                    ))
                    status_sampled = bool(getattr(
                        self, "_last_transfer_status_sampled", False
                    ))
                self._full_frame_transfers = (
                    getattr(self, "_full_frame_transfers", 0) + 1
                )
                if captured_response:
                    self._full_frame_status_transfers = (
                        getattr(self, "_full_frame_status_transfers", 0) + 1
                    )
                else:
                    self._full_frame_write_only_transfers = (
                        getattr(self, "_full_frame_write_only_transfers", 0) + 1
                    )
                if status_sampled:
                    self._full_frame_status_samples = (
                        getattr(self, "_full_frame_status_samples", 0) + 1
                    )
                    self._full_frame_frames_since_status_sample = 0
                else:
                    if captured_response:
                        self._full_frame_status_sample_misses = (
                            getattr(
                                self, "_full_frame_status_sample_misses", 0
                            ) + 1
                        )
                    gap = getattr(
                        self, "_full_frame_frames_since_status_sample", 0
                    ) + 1
                    self._full_frame_frames_since_status_sample = gap
                    self._full_frame_max_status_sample_gap = max(
                        getattr(self, "_full_frame_max_status_sample_gap", 0),
                        gap,
                    )
                self._full_frame_semantic_bytes_sent = (
                    getattr(self, "_full_frame_semantic_bytes_sent", 0)
                    + payload_length
                )
                self._full_frame_wire_bytes_sent = (
                    getattr(self, "_full_frame_wire_bytes_sent", 0)
                    + (
                        _aligned_envelope_wire_size(payload_length)
                        if aligned_frame
                        else payload_length + CRC_BYTES
                    )
                )
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

    def _update_receiver_status_base(self, response):
        """Parse status v3 after the firmware-defined layout is available."""
        # Status v8 retains this prefix; offsets stay synchronized with
        # firmware/esp32/include/ledgrid/protocol.hpp.
        self._receiver_status_seen = True
        self._note_receiver_status_version(int(response[4]))
        self._receiver_status_responses = getattr(self, '_receiver_status_responses', 0) + 1
        self._receiver_active_strips = int(response[6])
        self._receiver_lane_mask = int(response[7])
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
        fresh = self._observe_current_receiver_packets(self._receiver_packets)
        self._receiver_last_processed_command = int(response[313])
        self._receiver_stagger_phases = int(response[314])
        self._receiver_operation_sequence = self._response_u32(response, 316)
        self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V8
        return fresh

    def get_stats(self):
        stats = {name[1:]: value for name, value in vars(self).items()
                 if name.startswith(("_receiver_", "_full_frame_"))
                 and isinstance(value, (int, float, bool, str, type(None)))}
        last_seen = getattr(self, '_receiver_status_observed_at', None)
        stats.update({
            'frames_sent': self._frames_sent, 'spi_transfers': self._spi_transfers,
            'bytes_sent': self._bytes_sent, 'crc_bytes_sent': self._crc_bytes_sent,
            'errors': self._errors, 'total_leds': self.total_leds,
            'spi_speed_hz': self.spi.max_speed_hz, 'spi_mode': self.spi.mode,
            'last_frame_duration_ms': self._last_frame_duration * 1000,
            'receiver_connected': last_seen is not None and time.time() - last_seen < 10,
            'receiver_status_observed_at': last_seen,
            'receiver_status_integrity_last_failure': dict(self._receiver_status_integrity_last_failure) if self._receiver_status_integrity_last_failure else None,
        })
        return stats
