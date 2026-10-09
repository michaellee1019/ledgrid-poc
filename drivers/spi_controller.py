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
SPI_INTER_FRAME_DELAY = 0.0  # No delay needed - SPI is stable now

MAX_SPI_TRANSFER = 4096
SPIDEV_BUFFER_SIZE_PATH = Path("/sys/module/spidev/parameters/bufsiz")
CRC_BYTES = 2
SPI_DMA_ALIGNMENT_BYTES = 4
ALIGNED_ENVELOPE_VERSION = 1
ALIGNED_ENVELOPE_HEADER_BYTES = 4
MAX_ALIGNED_SEMANTIC_BYTES = (
    MAX_SPI_TRANSFER - ALIGNED_ENVELOPE_HEADER_BYTES - CRC_BYTES
)
FEC_DATA_BYTES = 50
FEC_PARITY_BYTES = 10
FEC_CODEWORD_BYTES = FEC_DATA_BYTES + FEC_PARITY_BYTES
FEC_MAX_CODEWORDS = 68
FEC_ENVELOPE_VERSION_V5 = 5
FEC_ENVELOPE_VERSION_V6 = 6
FEC_ENVELOPE_VERSION = 7
FEC_ENVELOPE_HEADER_BYTES = 4
FEC_WIRE_HEADER_BYTES = 2 * FEC_ENVELOPE_HEADER_BYTES
FEC_OUTER_PARITY_BYTES = FEC_DATA_BYTES
# V7 reserves the final systematic codeword as an XOR parity shard for all
# preceding data codewords. The installed 3,313-byte frame still uses the exact
# 68-codeword/4,088-byte shape. 3,338 is the largest semantic payload whose
# word-aligned inner envelope leaves that final codeword available.
MAX_FEC_SEMANTIC_BYTES = 3338
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
# write-only frame.  Other explicit status queries and control commands remain
# full duplex.  The installed phases are distinct so one wall frame samples at
# most one receiver.
FULL_FRAME_STATUS_SAMPLE_INTERVAL = 128
FULL_FRAME_STATUS_SAMPLE_RECEIVERS = 5
COMMAND_ACK_MAX_STATUS_QUERIES = 16
SPARSE_COMMAND_RETRY_BUDGET = 1
COMMAND_TRANSFER_DIAGNOSTIC_HISTORY = 32
COMMAND_TRANSFER_DIAGNOSTIC_MAX_SAMPLES = 32
COMMAND_ACK_POLL_INTERVAL_SECONDS = 0.001
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


def _fec_gf_multiply(left, right):
    """Multiply two bytes in GF(256) with primitive polynomial 0x11d."""
    result = 0
    left = int(left) & 0xFF
    right = int(right) & 0xFF
    while right:
        if right & 1:
            result ^= left
        right >>= 1
        left <<= 1
        if left & 0x100:
            left ^= 0x11D
    return result


def _fec_gf_power(value, exponent):
    result = 1
    while exponent:
        if exponent & 1:
            result = _fec_gf_multiply(result, value)
        value = _fec_gf_multiply(value, value)
        exponent >>= 1
    return result


def _fec_gf_inverse(value):
    if value == 0:
        raise ValueError("zero has no GF(256) inverse")
    return _fec_gf_power(value, 254)


def _fec_matrix_inverse(matrix):
    size = len(matrix)
    augmented = [
        list(row) + [int(row_index == column) for column in range(size)]
        for row_index, row in enumerate(matrix)
    ]
    for column in range(size):
        pivot = next(
            (row for row in range(column, size) if augmented[row][column]),
            None,
        )
        if pivot is None:
            raise ValueError("singular GF(256) matrix")
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        inverse = _fec_gf_inverse(augmented[column][column])
        augmented[column] = [
            _fec_gf_multiply(value, inverse) for value in augmented[column]
        ]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column]
            if factor:
                augmented[row] = [
                    value ^ _fec_gf_multiply(factor, pivot_value)
                    for value, pivot_value in zip(
                        augmented[row], augmented[column], strict=True
                    )
                ]
    return tuple(tuple(row[size:]) for row in augmented)


def _fec_gf_dot(left, right):
    result = 0
    for left_value, right_value in zip(left, right, strict=True):
        result ^= _fec_gf_multiply(left_value, right_value)
    return result


_FEC_SYMBOL_EVALUATIONS = tuple(range(1, FEC_CODEWORD_BYTES + 1))
_FEC_PARITY_MATRIX = tuple(
    tuple(
        _fec_gf_power(evaluation, power)
        for evaluation in _FEC_SYMBOL_EVALUATIONS[FEC_DATA_BYTES:]
    )
    for power in range(FEC_PARITY_BYTES)
)
_FEC_PARITY_MATRIX_INVERSE = _fec_matrix_inverse(_FEC_PARITY_MATRIX)
_FEC_DATA_TO_PARITY_COEFFICIENTS = tuple(
    tuple(
        _fec_gf_dot(
            _FEC_PARITY_MATRIX_INVERSE[parity],
            tuple(
                _fec_gf_power(evaluation, power)
                for power in range(FEC_PARITY_BYTES)
            ),
        )
        for parity in range(FEC_PARITY_BYTES)
    )
    for evaluation in _FEC_SYMBOL_EVALUATIONS[:FEC_DATA_BYTES]
)
_FEC_PARITY_TABLES = np.asarray(
    [
        [
            [
                _fec_gf_multiply(value, coefficient)
                for coefficient in coefficients
            ]
            for value in range(256)
        ]
        for coefficients in _FEC_DATA_TO_PARITY_COEFFICIENTS
    ],
    dtype=np.uint8,
)
_FEC_DATA_SYMBOL_INDICES = np.arange(FEC_DATA_BYTES, dtype=np.intp)[:, None]
_FEC_V7_LAYOUTS = {}


def _fec_v7_layout(codewords):
    """Return immutable vector indices for one exact v7 wire shape."""
    cached = _FEC_V7_LAYOUTS.get(codewords)
    if cached is not None:
        return cached
    symbols = np.arange(FEC_CODEWORD_BYTES, dtype=np.intp)[:, None]
    blocks = np.arange(codewords, dtype=np.intp)[None, :]
    cached = (symbols, (blocks + symbols) % codewords)
    _FEC_V7_LAYOUTS[codewords] = cached
    return cached


def _fec_envelope_wire_size(semantic_length):
    """Return exact DMA-safe v7 FEC wire bytes for one semantic packet."""
    if isinstance(semantic_length, bool) or not isinstance(semantic_length, int):
        raise TypeError("semantic_length must be an integer")
    if semantic_length < 1 or semantic_length > MAX_FEC_SEMANTIC_BYTES:
        raise ValueError(
            f"FEC semantic packet must contain 1..{MAX_FEC_SEMANTIC_BYTES} bytes"
        )
    inner_size = _aligned_envelope_wire_size(semantic_length)
    protected_size = FEC_ENVELOPE_HEADER_BYTES + inner_size
    codewords = (protected_size + FEC_DATA_BYTES - 1) // FEC_DATA_BYTES
    codewords += 1  # Outer XOR parity occupies the final data codeword.
    codewords += (-codewords) % 4
    if codewords > FEC_MAX_CODEWORDS:
        raise ValueError("FEC packet exceeds the 4096-byte SPI transfer limit")
    return FEC_WIRE_HEADER_BYTES + codewords * FEC_CODEWORD_BYTES


def _encode_fec_envelope(payload, output=None, inner_output=None):
    """Encode v7 with inner RS protection plus one outer XOR parity shard."""
    try:
        semantic = memoryview(payload).cast("B")
    except (TypeError, ValueError) as exc:
        raise TypeError("payload must be a contiguous bytes-like object") from exc
    semantic_length = len(semantic)
    wire_size = _fec_envelope_wire_size(semantic_length)
    inner_size = _aligned_envelope_wire_size(semantic_length)
    if inner_output is not None and (
        not isinstance(inner_output, bytearray) or len(inner_output) != inner_size
    ):
        raise ValueError("inner_output must be a bytearray of the exact v1 wire size")
    inner = _encode_aligned_envelope(semantic, output=inner_output)
    if output is None:
        wire = bytearray(wire_size)
    else:
        if not isinstance(output, bytearray) or len(output) != wire_size:
            raise ValueError("output must be a bytearray of the exact FEC wire size")
        wire = output
    header = bytes((
        CMD_ALIGNED_ENVELOPE,
        FEC_ENVELOPE_VERSION,
        (inner_size >> 8) & 0xFF,
        inner_size & 0xFF,
    ))
    wire[:FEC_ENVELOPE_HEADER_BYTES] = header
    wire[-FEC_ENVELOPE_HEADER_BYTES:] = header
    codewords = (wire_size - FEC_WIRE_HEADER_BYTES) // FEC_CODEWORD_BYTES
    data = np.zeros((codewords, FEC_DATA_BYTES), dtype=np.uint8)
    protected = data[:-1].reshape(-1)
    protected[:FEC_ENVELOPE_HEADER_BYTES] = np.frombuffer(
        header, dtype=np.uint8
    )
    protected[
        FEC_ENVELOPE_HEADER_BYTES:FEC_ENVELOPE_HEADER_BYTES + inner_size
    ] = np.frombuffer(inner, dtype=np.uint8)
    np.bitwise_xor.reduce(data[:-1], axis=0, out=data[-1])

    # Each data symbol contributes one precomputed ten-byte GF(256) parity
    # vector.  Gather the complete 50 x codeword contribution matrix in C and
    # reduce it there instead of executing 3,400 Python byte iterations on the
    # Pi for every wall frame.
    contributions = _FEC_PARITY_TABLES[
        _FEC_DATA_SYMBOL_INDICES, data.T, :
    ]
    parity = np.bitwise_xor.reduce(contributions, axis=0)
    words = np.empty((codewords, FEC_CODEWORD_BYTES), dtype=np.uint8)
    words[:, :FEC_DATA_BYTES] = data
    words[:, FEC_DATA_BYTES:] = parity

    # V7 diagonal interleaving rotates logical block positions by symbol row.
    # The cached scatter indices preserve the byte-for-byte wire contract while
    # avoiding another 4,080 Python-level assignments per frame.
    symbols, wire_blocks = _fec_v7_layout(codewords)
    body = np.frombuffer(wire, dtype=np.uint8)[
        FEC_ENVELOPE_HEADER_BYTES:-FEC_ENVELOPE_HEADER_BYTES
    ].reshape(FEC_CODEWORD_BYTES, codewords)
    body[symbols, wire_blocks] = words.T
    return wire

CAPABILITY_STATUS_V3 = 1 << 2
CAPABILITY_STATUS_V5 = 1 << 7
CAPABILITY_STATUS_V6 = 1 << 8
CAPABILITY_ALIGNED_ENVELOPE_V1 = 1 << 14
CAPABILITY_FEC_ENVELOPE_V2 = 1 << 15
CAPABILITY_FEC_ENVELOPE_V3 = 1 << 16
CAPABILITY_FEC_ENVELOPE_V4 = 1 << 17
CAPABILITY_FEC_ENVELOPE_V5 = 1 << 18
CAPABILITY_FEC_ENVELOPE_V6 = 1 << 19
CAPABILITY_FEC_ENVELOPE_V7 = 1 << 20
CAPABILITY_STATUS_CRC32_V8 = 1 << 21

ALL_LANES_MASK = 0xFF
STAGGER_OFF = 1
MAX_STAGGER_PHASES = 3

class LEDController:
    """One full RGB SPI receiver with CRC and optional Reed-Solomon FEC."""

    def __init__(self, bus=SPI_BUS, device=SPI_DEVICE, speed=SPI_SPEED, mode=SPI_MODE,
                 strips=DEFAULT_NUM_STRIPS, leds_per_strip=DEFAULT_LED_PER_STRIP,
                 debug=False, logical_device_id=None,
                 global_strip_offset=None, fec_transport=False,
                 **_unused):
        if type(fec_transport) is not bool:
            raise TypeError("fec_transport must be a boolean")
        self.debug = debug
        self.bus = bus
        self.device = device
        self.logical_device_id = self._optional_logical_device_id(logical_device_id)
        self.global_strip_offset = self._optional_global_strip_offset(
            global_strip_offset
        )
        self._fec_transport_requested = fec_transport
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
        self._last_brightness_refresh = 0.0
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
        self._receiver_fec_packets_received = 0
        self._receiver_fec_packets_accepted = 0
        self._receiver_fec_corrected_packets = 0
        self._receiver_fec_corrected_codewords = 0
        self._receiver_fec_uncorrectable_packets = 0
        self._receiver_fec_semantic_crc_errors = 0
        self._receiver_fec_framing_errors = 0
        self._receiver_fec_terminal_baseline = None
        self._receiver_fec_terminal_baseline_finalized = False
        self._receiver_fec_terminal_baseline_invalid = False
        self._receiver_fec_terminal_counter_resets = 0
        self._receiver_fec_uncorrectable_packets_process_delta = 0
        self._receiver_fec_semantic_crc_errors_process_delta = 0
        self._receiver_fec_framing_errors_process_delta = 0
        self._receiver_fec_last_decode_us = 0
        self._receiver_fec_max_decode_us = 0
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
        self._transport_envelope_enabled = True
        self._fec_transport_enabled = False
        self._fec_frames_sent = 0
        self._fec_codewords_sent = 0
        self._fec_parity_bytes_sent = 0
        self._fec_data_padding_bytes_sent = 0
        self._fec_sparse_packets_sent = 0
        self._fec_sparse_codewords_sent = 0
        self._fec_sparse_parity_bytes_sent = 0
        self._fec_sparse_data_padding_bytes_sent = 0
        self._writebytes2_supported = None
        self._last_transfer_captured_response = False
        self._last_transfer_status_sampled = False
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
        self._command_transfer_diagnostic_id = 0
        self._command_transfer_diagnostics = []
        self._full_frame_sequence = 0
        self._monotonic_ns = time.monotonic_ns
        self._frame_packet = bytearray(1 + self.total_leds * 3 + CRC_BYTES)
        self._aligned_frame_packet = bytearray(
            _aligned_envelope_wire_size(1 + self.total_leds * 3)
        )
        fec_semantic_size = 1 + self.total_leds * 3
        self._fec_frame_packet = (
            bytearray(_fec_envelope_wire_size(fec_semantic_size))
            if self._fec_transport_requested
            and fec_semantic_size <= MAX_FEC_SEMANTIC_BYTES
            else None
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
            if payload_length and int(buf[0]) != CMD_STATUS_QUERY:
                self._require_current_receiver_protocol()
            envelope_enabled = True
            fec_sparse_command = bool(
                payload_length
                and False
            )
            fec_enabled = bool(
                envelope_enabled
                and getattr(self, "_fec_transport_requested", False)
                and getattr(self, "_fec_transport_enabled", False)
                and (
                    buf is getattr(self, "_frame_packet", None)
                    or fec_sparse_command
                )
            )
            maximum_payload = (
                MAX_FEC_SEMANTIC_BYTES if fec_enabled else MAX_ALIGNED_SEMANTIC_BYTES
            )
            if payload_length < 1 or payload_length > maximum_payload:
                raise ValueError(
                    f"SPI semantic transaction must be 1..{maximum_payload} bytes"
                )
            if len(buf) != payload_length + CRC_BYTES:
                raise ValueError("packet buffer must contain exactly payload plus CRC storage")
            if fec_enabled:
                reusable = getattr(self, "_fec_frame_packet", None)
                expected = _fec_envelope_wire_size(payload_length)
                if not isinstance(reusable, bytearray) or len(reusable) != expected:
                    reusable = None
                wire = _encode_fec_envelope(
                    memoryview(buf)[:payload_length], output=reusable,
                    inner_output=(
                        getattr(self, "_aligned_frame_packet", None)
                        if buf is getattr(self, "_frame_packet", None)
                        else None
                    ),
                )
                codewords = (
                    len(wire) - FEC_WIRE_HEADER_BYTES
                ) // FEC_CODEWORD_BYTES
                inner_size = _aligned_envelope_wire_size(payload_length)
                fec_parity_bytes = (
                    codewords * FEC_PARITY_BYTES + FEC_OUTER_PARITY_BYTES
                )
                fec_data_padding_bytes = (
                    codewords * FEC_DATA_BYTES
                    - FEC_OUTER_PARITY_BYTES
                    - FEC_ENVELOPE_HEADER_BYTES
                    - inner_size
                )
                envelope_bytes = (
                    ALIGNED_ENVELOPE_HEADER_BYTES
                    + FEC_ENVELOPE_HEADER_BYTES
                    + FEC_WIRE_HEADER_BYTES
                )
                inner_padding_bytes = (
                    inner_size
                    - ALIGNED_ENVELOPE_HEADER_BYTES
                    - payload_length
                    - CRC_BYTES
                )
                padding_bytes = fec_data_padding_bytes + inner_padding_bytes
            elif envelope_enabled:
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
                padding_bytes = (
                    len(wire)
                    - ALIGNED_ENVELOPE_HEADER_BYTES
                    - payload_length
                    - CRC_BYTES
                )
            # Preserve the established transport-counter contract: these
            # counters describe the one kernel transfer attempt, including an
            # ambiguous ioctl failure that must never be retried.  FEC's
            # narrower ``*_sent`` counters are committed separately only once
            # that attempt returns successfully.
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

            def record_successful_fec_transfer():
                if not fec_enabled:
                    return
                if fec_sparse_command:
                    self._fec_sparse_packets_sent = (
                        getattr(self, "_fec_sparse_packets_sent", 0) + 1
                    )
                    self._fec_sparse_codewords_sent = (
                        getattr(self, "_fec_sparse_codewords_sent", 0) + codewords
                    )
                    self._fec_sparse_parity_bytes_sent = (
                        getattr(self, "_fec_sparse_parity_bytes_sent", 0)
                        + fec_parity_bytes
                    )
                    self._fec_sparse_data_padding_bytes_sent = (
                        getattr(self, "_fec_sparse_data_padding_bytes_sent", 0)
                        + fec_data_padding_bytes
                    )
                else:
                    self._fec_frames_sent = (
                        getattr(self, "_fec_frames_sent", 0) + 1
                    )
                    self._fec_codewords_sent = (
                        getattr(self, "_fec_codewords_sent", 0) + codewords
                    )
                    self._fec_parity_bytes_sent = (
                        getattr(self, "_fec_parity_bytes_sent", 0)
                        + fec_parity_bytes
                    )
                    self._fec_data_padding_bytes_sent = (
                        getattr(self, "_fec_data_padding_bytes_sent", 0)
                        + fec_data_padding_bytes
                    )
            try:
                if not response_required and fec_enabled:
                    # Keep protected full frames on the SPI_IOC_MESSAGE path.
                    # The spidev write(2) path used by writebytes2 has shown
                    # repeatable, speed-independent multi-bit corruption on
                    # the installed receiver-3 route.  xfer2 still clocks one
                    # exact transaction; its unrelated raw MISO bytes are
                    # intentionally discarded rather than treated as status.
                    self.spi.xfer2(wire)
                    record_successful_fec_transfer()
                    self._last_transfer_captured_response = False
                    self._last_transfer_status_sampled = False
                    return None
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
                            record_successful_fec_transfer()
                            self._last_transfer_captured_response = False
                            self._last_transfer_status_sampled = False
                            return None
                    elif not callable(writer):
                        self._writebytes2_supported = False
                response = self.spi.xfer2(wire)
                record_successful_fec_transfer()
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

    def _fec_full_frame_enabled(self):
        return bool(
            getattr(self, "_fec_transport_requested", False)
            and getattr(self, "_fec_transport_enabled", False)
        )

    def _selected_full_frame_wire_size(self):
        packet = (
            getattr(self, "_fec_frame_packet", ())
            if self._fec_full_frame_enabled()
            else getattr(self, "_aligned_frame_packet", ())
        )
        return len(packet) if packet is not None else 0

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
        self._receiver_status_integrity_verified = True
        self._receiver_status_observed_at = time.time()
        fresh = bool(self._update_receiver_status_v7(response))
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

    def _update_receiver_status_v7(self, response):
        """Parse exact FEC transport outcomes after the complete v6 prefix."""
        fec_enabled_before_observation = bool(
            getattr(self, "_fec_transport_enabled", False)
        )
        fresh = self._update_receiver_status_base(response)
        fec_enabled_after_observation = bool(
            getattr(self, "_fec_transport_enabled", False)
        )
        values = {}
        for name, offset in (
            ("packets_received", 1216),
            ("packets_accepted", 1220),
            ("corrected_packets", 1224),
            ("corrected_codewords", 1228),
            ("uncorrectable_packets", 1232),
            ("semantic_crc_errors", 1236),
            ("framing_errors", 1240),
        ):
            value = self._response_u32(response, offset)
            values[name] = value
            setattr(self, f"_receiver_fec_{name}", value)
        terminal_names = (
            "uncorrectable_packets",
            "semantic_crc_errors",
            "framing_errors",
        )
        baseline = getattr(self, "_receiver_fec_terminal_baseline", None)
        baseline_finalized = bool(
            getattr(self, "_receiver_fec_terminal_baseline_finalized", False)
        )
        current = {name: values[name] for name in terminal_names}
        counter_reset = baseline is not None and any(
            current[name] < baseline[name] for name in terminal_names
        )
        if counter_reset:
            self._receiver_fec_terminal_counter_resets = (
                getattr(self, "_receiver_fec_terminal_counter_resets", 0) + 1
            )
            self._receiver_fec_terminal_baseline_invalid = True
        elif fec_enabled_before_observation and not baseline_finalized:
            # Reaching v7 only after FEC was already active cannot establish
            # which lifetime outcomes predate this Host process.
            self._receiver_fec_terminal_baseline_invalid = True
        elif fresh and not baseline_finalized:
            # SPI responses are queued.  Keep advancing the lifetime snapshot
            # throughout current-protocol establishment so an early queued
            # response cannot hide historical pre-enable outcomes.
            baseline = dict(current)
            self._receiver_fec_terminal_baseline = baseline

        if (
            fresh
            and not baseline_finalized
            and fec_enabled_after_observation
            and not getattr(self, "_receiver_fec_terminal_baseline_invalid", False)
        ):
            if baseline is None:
                self._receiver_fec_terminal_baseline_invalid = True
            else:
                baseline_finalized = True
                self._receiver_fec_terminal_baseline_finalized = True
        for name in terminal_names:
            setattr(
                self,
                f"_receiver_fec_{name}_process_delta",
                (
                    max(0, current[name] - baseline[name])
                    if baseline is not None else 0
                ),
            )
        self._receiver_fec_last_decode_us = self._response_u16(response, 1244)
        self._receiver_fec_max_decode_us = self._response_u16(response, 1246)
        return fresh

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
            for query_index in range(SPI_RESPONSE_QUEUE_DEPTH + 1):
                if query_index:
                    # Give the receiver task time to parse the preceding query
                    # and queue its requested extended snapshot.  Sending all
                    # three transfers back-to-back can drain the two old slots
                    # before the new v7 response exists, especially while FEC
                    # decoding competes for the receiver core.
                    time.sleep(FRESH_STATUS_DRAIN_INTERVAL_SECONDS)
                self._clock_receiver_status_snapshot()

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
        self._refresh_configuration(force=True)
        self._xfer([CMD_SET_BRIGHTNESS, level])
        self._last_brightness_refresh = time.time()
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
        fec_semantic_size = 1 + self.total_leds * 3
        if (
            getattr(self, "_fec_transport_requested", False)
            and fec_semantic_size > MAX_FEC_SEMANTIC_BYTES
        ):
            raise ValueError(
                "configured SET_ALL exceeds the negotiated FEC semantic limit"
            )
        expected_packet_size = 1 + self.total_leds * 3 + CRC_BYTES
        if len(self._frame_packet) != expected_packet_size:
            self._frame_packet = bytearray(expected_packet_size)
        expected_aligned_size = _aligned_envelope_wire_size(1 + self.total_leds * 3)
        if len(getattr(self, "_aligned_frame_packet", ())) != expected_aligned_size:
            self._aligned_frame_packet = bytearray(expected_aligned_size)
        if (
            getattr(self, "_fec_transport_requested", False)
            and fec_semantic_size <= MAX_FEC_SEMANTIC_BYTES
        ):
            expected_fec_size = _fec_envelope_wire_size(fec_semantic_size)
            if len(getattr(self, "_fec_frame_packet", ())) != expected_fec_size:
                self._fec_frame_packet = bytearray(expected_fec_size)
        else:
            self._fec_frame_packet = None
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
                    and (
                        getattr(self, "_fec_transport_requested", False)
                        or self._selected_full_frame_wire_size()
                        < status_query_bytes
                    )
                )
                if separate_status_query:
                    # A short aligned SET_ALL cannot clock the complete v8
                    # status snapshot. FEC protects host-to-receiver bytes only,
                    # so its frame transfer also needs a separate v8 status
                    # query. Sample first, then retain the write-only fast path.
                    transport_lock = getattr(self, "_transport_lock", None)
                    if transport_lock is None:
                        transport_lock = self._transport_lock = threading.RLock()
                    with transport_lock:
                        self._drain_fresh_receiver_status()
                        captured_response = True
                        status_sampled = bool(getattr(
                            self, "_last_transfer_status_sampled", False
                        ))
                        self._xfer_packet(
                            buf,
                            payload_length,
                            response_required=False,
                        )
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
                        _fec_envelope_wire_size(payload_length)
                        if self._fec_full_frame_enabled()
                        else _aligned_envelope_wire_size(payload_length)
                        if aligned_frame
                        else payload_length + CRC_BYTES
                    )
                )
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
        fec_advertised = bool(
            self._receiver_capabilities & CAPABILITY_ALIGNED_ENVELOPE_V1
            and self._receiver_capabilities & CAPABILITY_FEC_ENVELOPE_V7
        )
        self._fec_transport_enabled = bool(
            getattr(self, "_fec_transport_requested", False) and fec_advertised
        )
        self._receiver_last_processed_command = int(response[313])
        self._receiver_stagger_phases = int(response[314])
        self._receiver_operation_sequence = self._response_u32(response, 316)
        self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V8
        return fresh

    def _require_current_receiver_protocol(self):
        """The transport is matched-current; connectivity never blocks a send."""
        if self._fec_transport_requested and not self._fec_transport_enabled:
            self._drain_fresh_receiver_status()
            if not self._fec_transport_enabled:
                raise RuntimeError("receiver does not advertise protected full RGB transport")

    def get_stats(self):
        stats = {name[1:]: value for name, value in vars(self).items()
                 if name.startswith(("_receiver_", "_fec_", "_full_frame_"))
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
