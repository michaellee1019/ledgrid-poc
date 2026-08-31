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
RECEIVER_STATUS_BYTES = 29
RECEIVER_STATUS_BYTES_V2 = 68
RECEIVER_STATUS_BYTES_V3 = 320
RECEIVER_STATUS_BYTES_V4 = 416
RECEIVER_STATUS_BYTES_V5 = 768
RECEIVER_STATUS_BYTES_V6 = 1216
RECEIVER_STATUS_BYTES_V7 = 1248
# The ESP32 slave keeps two response buffers queued. A command's result is
# therefore observable after two complete status-query transfers.
SPI_RESPONSE_QUEUE_DEPTH = 2
TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS = 3
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
COMMAND_ACK_POLL_INTERVAL_SECONDS = 0.001
# A streamed receiver can be inside a roughly 4.5 ms parallel-LED presentation
# when its completed SPI transaction becomes available to the receiver task.
# Pace queue-drain queries beyond that installed display cycle so the third
# transfer can actually clock the requested extended snapshot instead of three
# already-queued legacy-safe v3 responses.
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
CMD_NATIVE_PROBE = 0x50
CMD_NATIVE_PREFLIGHT = 0x51
CMD_NATIVE_BEGIN = 0x52
CMD_NATIVE_CHUNK = 0x53
CMD_NATIVE_FINALIZE = 0x54
CMD_NATIVE_VERIFY = 0x55
CMD_NATIVE_ACTIVATE = 0x56
CMD_NATIVE_STOP = 0x57
CMD_NATIVE_PARAMETERS = 0x58
CMD_NATIVE_REMOVE = 0x59
CMD_NATIVE_ABORT = 0x5A
CMD_NATIVE_RESTORE = 0x5B
CMD_NATIVE_QUARANTINE_CLEAR = 0x5C
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
    MAX_ALIGNED_SEMANTIC_BYTES - OVERLAY_PATCH_HEADER_BYTES
) // 4
MAX_RGBA_PIXELS_PER_BATCH_SPAN = (
    MAX_ALIGNED_SEMANTIC_BYTES
    - OVERLAY_PATCH_BATCH_HEADER_BYTES
    - OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES
) // 4
LEGACY_MAX_RGBA_PIXELS_PER_PATCH = (
    MAX_SPI_TRANSFER - OVERLAY_PATCH_HEADER_BYTES - CRC_BYTES
) // 4
LEGACY_MAX_RGBA_PIXELS_PER_BATCH_SPAN = (
    MAX_SPI_TRANSFER
    - OVERLAY_PATCH_BATCH_HEADER_BYTES
    - OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES
    - CRC_BYTES
) // 4
PROFILE_BINDING_BYTES = 64
PROFILE_PREFLIGHT_BYTES = 69
PROFILE_BEGIN_BYTES = 81
PROFILE_CHUNK_HEADER_BYTES = 5
MAX_PROFILE_CHUNK_BYTES = MAX_ALIGNED_SEMANTIC_BYTES - PROFILE_CHUNK_HEADER_BYTES
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
NATIVE_BINDING_BYTES = 64
NATIVE_DESCRIPTOR_BYTES = 85
NATIVE_PROBE_BYTES = 33
NATIVE_PREFLIGHT_BYTES = 86
NATIVE_BEGIN_BYTES = 94
NATIVE_CHUNK_HEADER_BYTES = 5
MAX_NATIVE_CHUNK_BYTES = MAX_ALIGNED_SEMANTIC_BYTES - NATIVE_CHUNK_HEADER_BYTES
NATIVE_BINDING_COMMAND_BYTES = 65
NATIVE_ACTIVATE_HEADER_BYTES = 87
NATIVE_PARAMETERS_HEADER_BYTES = 71
NATIVE_RESTORE_BYTES = 204
NATIVE_MAX_PARAMETER_BYTES = 1024
NATIVE_TYPED_PARAMETER_VERSION = 1
NATIVE_TARGET_ESP32_S3 = 1
NATIVE_RESULT_NAMES = {
    0: "none",
    1: "ok",
    2: "unsupported",
    3: "invalid_size",
    4: "invalid_command",
    5: "invalid_state",
    6: "digest_mismatch",
    7: "wrong_abi",
    8: "wrong_target",
    9: "wrong_geometry",
    10: "storage_error",
    11: "no_space",
    12: "not_found",
    13: "conflict",
    14: "invalid_token",
    15: "pinned",
    16: "integrity_error",
    17: "invalid_parameters",
    18: "quarantined",
    19: "load_failed",
    20: "entrypoint_failed",
    21: "initialize_failed",
    22: "context_failed",
    23: "render_failed",
    24: "cleanup_failed",
    25: "unload_failed",
    26: "watchdog",
}
NATIVE_TRANSFER_STATE_NAMES = {
    0: "idle",
    1: "preflight_ready",
    2: "receiving",
    3: "finalizing",
    4: "staged",
    5: "active",
    6: "failed",
    7: "quarantined",
}
NATIVE_PHASE_NAMES = {
    0: "none",
    1: "load",
    2: "entrypoint",
    3: "initialize",
    4: "context_update",
    5: "render",
    6: "cleanup",
    7: "unload",
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
CAPABILITY_STATUS_V6 = 1 << 8
CAPABILITY_NATIVE_MODULE_V2 = 1 << 9
CAPABILITY_NATIVE_CACHE_V1 = 1 << 10
CAPABILITY_NATIVE_TYPED_PARAMETERS_V1 = 1 << 11
CAPABILITY_NATIVE_QUARANTINE_V1 = 1 << 12
CAPABILITY_NATIVE_GUARDED_LOADER_V1 = 1 << 13
CAPABILITY_ALIGNED_ENVELOPE_V1 = 1 << 14
CAPABILITY_FEC_ENVELOPE_V2 = 1 << 15
CAPABILITY_FEC_ENVELOPE_V3 = 1 << 16
CAPABILITY_FEC_ENVELOPE_V4 = 1 << 17
CAPABILITY_FEC_ENVELOPE_V5 = 1 << 18
CAPABILITY_FEC_ENVELOPE_V6 = 1 << 19
CAPABILITY_FEC_ENVELOPE_V7 = 1 << 20

ALL_LANES_MASK = 0xFF
STAGGER_OFF = 1
MAX_STAGGER_PHASES = 3

# The receiver's MISO buffer is zero-initialized and firmware only writes the
# snapshot bytes it knows about, so a field added past a previously complete
# layout reads back as zero on a board that was flashed before the field
# existed. Zero is outside every legal phase count, which makes it a usable
# sentinel for "flashed firmware predates stagger_phases".
LEGACY_SNAPSHOT_SENTINEL = 0


class LEDController:
    """Control LED strips via SPI"""
    
    def __init__(self, bus=SPI_BUS, device=SPI_DEVICE, speed=SPI_SPEED, mode=SPI_MODE,
                 strips=DEFAULT_NUM_STRIPS, leds_per_strip=DEFAULT_LED_PER_STRIP,
                 debug=False, logical_device_id=None,
                 reverse_native_strip_order=False,
                 global_strip_offset=None, fec_transport=False,
                 hardware_serial=None, firmware_sha256=None,
                 receiver_identity_authority_digest=None):
        if type(reverse_native_strip_order) is not bool:
            raise TypeError("reverse_native_strip_order must be a boolean")
        if type(fec_transport) is not bool:
            raise TypeError("fec_transport must be a boolean")
        self.debug = debug
        self.bus = bus
        self.device = device
        self.logical_device_id = self._optional_logical_device_id(logical_device_id)
        self._set_receiver_identity_binding(
            hardware_serial=hardware_serial,
            firmware_sha256=firmware_sha256,
            authority_digest=receiver_identity_authority_digest,
        )
        self.reverse_native_strip_order = reverse_native_strip_order
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
        self._receiver_status_legacy = False
        self._legacy_snapshot_warned = False
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
        self._clear_receiver_native_status()
        self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V3
        # Rolling-deployment safety: emit legacy packets until this exact
        # receiver proves aligned-envelope support across three fresh,
        # counter-advancing status snapshots. New firmware continues decoding
        # both wire formats.
        self._transport_envelope_enabled = False
        self._transport_envelope_candidate = None
        self._transport_envelope_candidate_streak = 0
        self._transport_envelope_last_receiver_packets = None
        self._transport_envelope_fresh_observations = 0
        self._transport_envelope_stale_observations = 0
        self._transport_envelope_counter_resets = 0
        self._transport_envelope_invalid_resets = 0
        self._transport_envelope_transitions = 0
        self._fec_transport_enabled = False
        self._fec_transport_candidate = None
        self._fec_transport_candidate_streak = 0
        self._fec_transport_last_receiver_packets = None
        self._fec_transport_fresh_observations = 0
        self._fec_transport_stale_observations = 0
        self._fec_transport_counter_resets = 0
        self._fec_transport_invalid_resets = 0
        self._fec_transport_transitions = 0
        self._fec_frames_sent = 0
        self._fec_codewords_sent = 0
        self._fec_parity_bytes_sent = 0
        self._fec_data_padding_bytes_sent = 0
        self._writebytes2_supported = None
        self._last_transfer_captured_response = False
        self._last_transfer_status_sampled = False
        self._full_frame_sequence = 0
        self._presentation_commit_context_cache = {}
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

    @staticmethod
    def _identity_digest(value, label):
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ValueError(f"{label} must be a lowercase SHA-256 digest")
        return value

    def _set_receiver_identity_binding(
        self, *, hardware_serial, firmware_sha256, authority_digest
    ):
        """Freeze an injected identity; this controller never discovers it."""
        supplied = (hardware_serial, firmware_sha256, authority_digest)
        if all(value is None for value in supplied):
            self._hardware_serial = None
            self._firmware_sha256 = None
            self._receiver_identity_authority_digest = None
            return
        if any(value is None for value in supplied):
            raise ValueError("receiver identity binding must be complete")
        if (
            not isinstance(hardware_serial, str)
            or len(hardware_serial.split(":")) != 6
            or any(
                len(part) != 2 or any(char not in "0123456789abcdef" for char in part)
                for part in hardware_serial.split(":")
            )
        ):
            raise ValueError("hardware_serial must be a canonical MAC address")
        self._hardware_serial = hardware_serial
        self._firmware_sha256 = self._identity_digest(
            firmware_sha256, "firmware_sha256"
        )
        self._receiver_identity_authority_digest = self._identity_digest(
            authority_digest, "receiver_identity_authority_digest"
        )

    @property
    def hardware_serial(self):
        return self._hardware_serial

    @property
    def firmware_sha256(self):
        return self._firmware_sha256

    @property
    def receiver_identity_authority_digest(self):
        return self._receiver_identity_authority_digest
    
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
            envelope_enabled = bool(
                getattr(self, "_transport_envelope_enabled", False)
            )
            fec_enabled = bool(
                envelope_enabled
                and getattr(self, "_fec_transport_requested", False)
                and getattr(self, "_fec_transport_enabled", False)
                and buf is getattr(self, "_frame_packet", None)
            )
            maximum_payload = (
                MAX_ALIGNED_SEMANTIC_BYTES
                if envelope_enabled
                else MAX_SPI_TRANSFER - CRC_BYTES
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
                    inner_output=getattr(self, "_aligned_frame_packet", None),
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
            else:
                crc = _crc16_ccitt(memoryview(buf)[:payload_length])
                buf[payload_length] = (crc >> 8) & 0xFF
                buf[payload_length + 1] = crc & 0xFF
                wire = buf
                envelope_bytes = 0
                padding_bytes = 0
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
                status_sampled = bool(self._update_receiver_status(response))
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
            getattr(self, "_transport_envelope_enabled", False)
            and getattr(self, "_fec_transport_requested", False)
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
        return wall_frame_sequence % FULL_FRAME_STATUS_SAMPLE_INTERVAL == phase

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

    @classmethod
    def _native_binding(cls, binding, *, field):
        if binding is None:
            return b"\0" + bytes(NATIVE_BINDING_BYTES)
        if isinstance(binding, tuple) and len(binding) == 2:
            bundle_digest, payload_digest = binding
        else:
            bundle_digest = getattr(binding, "bundle_digest", None)
            payload_digest = getattr(binding, "payload_digest", None)
        return (
            b"\1"
            + cls._profile_digest(f"{field}.bundle_digest", bundle_digest)
            + cls._profile_digest(f"{field}.payload_digest", payload_digest)
        )

    @staticmethod
    def _native_parameter_blob(value):
        if not isinstance(value, bytes):
            raise TypeError("native parameter blob must be immutable bytes")
        if not 2 <= len(value) <= NATIVE_MAX_PARAMETER_BYTES:
            raise ValueError(
                f"native parameter blob must contain 2..{NATIVE_MAX_PARAMETER_BYTES} bytes"
            )
        if value[0] != NATIVE_TYPED_PARAMETER_VERSION:
            raise ValueError(
                f"native parameter blob version must be {NATIVE_TYPED_PARAMETER_VERSION}"
            )
        count = value[1]
        if count > 31:
            raise ValueError("native parameter blob may contain at most 31 entries")
        cursor = 2
        for expected_id in range(count):
            if cursor + 4 > len(value):
                raise ValueError("native parameter blob has a truncated entry header")
            parameter_id, kind, reserved = struct.unpack_from(">HBB", value, cursor)
            cursor += 4
            if parameter_id != expected_id:
                raise ValueError(
                    "native parameter IDs must be canonical zero-based positions"
                )
            if reserved != 0:
                raise ValueError("native parameter reserved bytes must be zero")
            encoded_size = {1: 4, 2: 4, 3: 1, 4: 2}.get(kind)
            if encoded_size is None:
                raise ValueError("native parameter blob contains an unknown type")
            if cursor + encoded_size > len(value):
                raise ValueError("native parameter blob contains a truncated value")
            if kind == 2 and not math.isfinite(struct.unpack_from(">f", value, cursor)[0]):
                raise ValueError("native float parameters must be finite float32 values")
            if kind == 3 and value[cursor] not in (0, 1):
                raise ValueError("native boolean parameters must be canonical 0 or 1")
            cursor += encoded_size
        if cursor != len(value):
            raise ValueError("native parameter blob contains trailing bytes")
        return value

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
            self._reset_transport_envelope_candidate(invalid=False)
            self._reset_fec_transport_candidate(invalid=False)
            return False
        magic = tuple(int(response[index]) for index in range(4))
        indicated_status = {
            RECEIVER_STATUS_MAGIC_V2: (2, RECEIVER_STATUS_BYTES_V2),
            RECEIVER_STATUS_MAGIC_V3: (3, RECEIVER_STATUS_BYTES_V3),
            RECEIVER_STATUS_MAGIC_V4: (4, RECEIVER_STATUS_BYTES_V4),
            RECEIVER_STATUS_MAGIC_V5: (5, RECEIVER_STATUS_BYTES_V5),
            RECEIVER_STATUS_MAGIC_V6: (6, RECEIVER_STATUS_BYTES_V6),
            RECEIVER_STATUS_MAGIC_V7: (7, RECEIVER_STATUS_BYTES_V7),
        }.get(magic)
        indicated_status_bytes = None
        if indicated_status is not None:
            indicated_status_version, indicated_status_bytes = indicated_status
            if (
                len(response) < indicated_status_bytes
                or int(response[4]) != indicated_status_version
            ):
                self._reset_transport_envelope_candidate(invalid=False)
                self._reset_fec_transport_candidate(invalid=False)
                return False
        known_status_bytes = {
            2: RECEIVER_STATUS_BYTES_V2,
            3: RECEIVER_STATUS_BYTES_V3,
            4: RECEIVER_STATUS_BYTES_V4,
            5: RECEIVER_STATUS_BYTES_V5,
            6: RECEIVER_STATUS_BYTES_V6,
            7: RECEIVER_STATUS_BYTES_V7,
        }.get(getattr(self, '_receiver_status_version', 0), RECEIVER_STATUS_BYTES)
        if indicated_status_bytes is None and len(response) < known_status_bytes:
            # The Host clocked an ordinary command shorter than the known
            # atomic status snapshot. This breaks a pending consecutive streak
            # but is neither corruption nor a telemetry miss.
            self._reset_transport_envelope_candidate(invalid=False)
            self._reset_fec_transport_candidate(invalid=False)
            return False
        if magic == RECEIVER_STATUS_MAGIC_V7 and len(response) >= RECEIVER_STATUS_BYTES_V7:
            return bool(self._update_receiver_status_v7(response))
        if magic == RECEIVER_STATUS_MAGIC_V6 and len(response) >= RECEIVER_STATUS_BYTES_V6:
            return bool(self._update_receiver_status_v6(response))
        if magic == RECEIVER_STATUS_MAGIC_V5 and len(response) >= RECEIVER_STATUS_BYTES_V5:
            return bool(self._update_receiver_status_v5(response))
        if magic == RECEIVER_STATUS_MAGIC_V4 and len(response) >= RECEIVER_STATUS_BYTES_V4:
            return bool(self._update_receiver_status_v4(response))
        if magic == RECEIVER_STATUS_MAGIC_V3 and len(response) >= RECEIVER_STATUS_BYTES_V3:
            return bool(self._update_receiver_status_v3(response))

        if magic == RECEIVER_STATUS_MAGIC_V2 and len(response) >= RECEIVER_STATUS_BYTES_V2:
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
            # Zero means the receiver predates the field; leave it as read so
            # callers can tell that apart from a legal phase count.
            self._receiver_stagger_phases = int(response[64])
            self._note_legacy_snapshot(
                self._receiver_stagger_phases == LEGACY_SNAPSHOT_SENTINEL
            )
            fresh = self._observe_transport_envelope_capability(
                False, self._receiver_packets
            )
            self._observe_fec_transport_capability(False, self._receiver_packets)
            return fresh

        if magic != RECEIVER_STATUS_MAGIC:
            self._reset_transport_envelope_candidate(invalid=True)
            self._reset_fec_transport_candidate(invalid=True)
            if getattr(self, '_receiver_status_seen', False):
                self._receiver_status_misses = getattr(self, '_receiver_status_misses', 0) + 1
            return False

        self._receiver_status_seen = True
        self._note_receiver_status_version(1)
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
        fresh = self._observe_transport_envelope_capability(
            False, self._receiver_packets
        )
        self._observe_fec_transport_capability(False, self._receiver_packets)
        return fresh

    def _note_receiver_status_version(self, version):
        """Record the actual latest response and sticky per-process maximum."""
        version = int(version)
        self._receiver_status_version = version
        self._receiver_status_max_version_seen = max(
            getattr(self, "_receiver_status_max_version_seen", 0),
            version,
        )

    def _reset_transport_envelope_candidate(self, *, invalid=False):
        """Discard an unproven transition without changing active framing."""
        self._transport_envelope_candidate = None
        self._transport_envelope_candidate_streak = 0
        if invalid:
            self._transport_envelope_invalid_resets = (
                getattr(self, "_transport_envelope_invalid_resets", 0) + 1
            )

    def _reset_fec_transport_candidate(self, *, invalid=False):
        """Discard an unproven FEC transition without changing active framing."""
        self._fec_transport_candidate = None
        self._fec_transport_candidate_streak = 0
        if invalid:
            self._fec_transport_invalid_resets = (
                getattr(self, "_fec_transport_invalid_resets", 0) + 1
            )

    def _observe_fec_transport_capability(self, advertised, receiver_packets):
        """Enable v7 only after opt-in and three fresh capability snapshots."""
        advertised = bool(
            getattr(self, "_fec_transport_requested", False) and advertised
        )
        receiver_packets = int(receiver_packets)
        last_packets = getattr(self, "_fec_transport_last_receiver_packets", None)
        if last_packets is not None and receiver_packets == last_packets:
            self._fec_transport_stale_observations = (
                getattr(self, "_fec_transport_stale_observations", 0) + 1
            )
            self._reset_fec_transport_candidate()
            return False
        if last_packets is not None and receiver_packets < last_packets:
            self._fec_transport_counter_resets = (
                getattr(self, "_fec_transport_counter_resets", 0) + 1
            )
            self._reset_fec_transport_candidate()
        self._fec_transport_last_receiver_packets = receiver_packets
        self._fec_transport_fresh_observations = (
            getattr(self, "_fec_transport_fresh_observations", 0) + 1
        )
        active = bool(getattr(self, "_fec_transport_enabled", False))
        if advertised == active:
            self._reset_fec_transport_candidate()
            return True
        candidate = getattr(self, "_fec_transport_candidate", None)
        if candidate is advertised:
            self._fec_transport_candidate_streak = (
                getattr(self, "_fec_transport_candidate_streak", 0) + 1
            )
        else:
            self._fec_transport_candidate = advertised
            self._fec_transport_candidate_streak = 1
        if (
            self._fec_transport_candidate_streak
            >= TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS
        ):
            self._fec_transport_enabled = advertised
            self._fec_transport_transitions = (
                getattr(self, "_fec_transport_transitions", 0) + 1
            )
            self._reset_fec_transport_candidate()
        return True

    def _observe_transport_envelope_capability(
        self, advertised, receiver_packets
    ):
        """Commit a framing transition after three fresh receiver observations."""
        advertised = bool(advertised)
        receiver_packets = int(receiver_packets)
        last_packets = getattr(
            self, "_transport_envelope_last_receiver_packets", None
        )
        if last_packets is not None and receiver_packets == last_packets:
            self._transport_envelope_stale_observations = (
                getattr(self, "_transport_envelope_stale_observations", 0) + 1
            )
            self._reset_transport_envelope_candidate()
            return False
        if last_packets is not None and receiver_packets < last_packets:
            # Receiver reboot, counter reset, or uint32 wrap starts a new
            # evidence epoch. The first post-reset observation may seed, but
            # can never by itself change active framing.
            self._transport_envelope_counter_resets = (
                getattr(self, "_transport_envelope_counter_resets", 0) + 1
            )
            self._reset_transport_envelope_candidate()
        self._transport_envelope_last_receiver_packets = receiver_packets
        self._transport_envelope_fresh_observations = (
            getattr(self, "_transport_envelope_fresh_observations", 0) + 1
        )

        active = bool(getattr(self, "_transport_envelope_enabled", False))
        if advertised == active:
            self._reset_transport_envelope_candidate()
            return True
        candidate = getattr(self, "_transport_envelope_candidate", None)
        if candidate is advertised:
            self._transport_envelope_candidate_streak = (
                getattr(self, "_transport_envelope_candidate_streak", 0) + 1
            )
        else:
            self._transport_envelope_candidate = advertised
            self._transport_envelope_candidate_streak = 1
        if (
            self._transport_envelope_candidate_streak
            >= TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS
        ):
            self._transport_envelope_enabled = advertised
            self._transport_envelope_transitions = (
                getattr(self, "_transport_envelope_transitions", 0) + 1
            )
            self._reset_transport_envelope_candidate()
        return True

    def _update_receiver_status_v3(self, response):
        """Parse status v3 after the firmware-defined layout is available."""
        # Phase 3A deliberately retains the complete v2 prefix so old counters
        # and operational dashboards do not disappear when local playback is
        # enabled. The extension offsets below are synchronized with
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
        fresh = self._observe_transport_envelope_capability(
            self._receiver_capabilities & CAPABILITY_ALIGNED_ENVELOPE_V1,
            self._receiver_packets,
        )
        fec_advertised = bool(
            self._receiver_capabilities & CAPABILITY_ALIGNED_ENVELOPE_V1
            and self._receiver_capabilities & CAPABILITY_FEC_ENVELOPE_V7
        )
        fec_terminal_telemetry_available = bool(
            len(response) >= RECEIVER_STATUS_BYTES_V7
            and tuple(int(response[index]) for index in range(4))
            == RECEIVER_STATUS_MAGIC_V7
            and int(response[4]) == 7
        )
        defer_fec_observation = (
            getattr(self, "_fec_transport_requested", False)
            and fec_advertised
            and not fec_terminal_telemetry_available
        )
        if defer_fec_observation:
            # The legacy status-v3 capability prefix can arrive several queued responses
            # before and between v7 terminal snapshots.  It neither advances
            # nor contradicts the v7-only negotiation evidence because no
            # process lifetime baseline can be captured from the prefix.
            pass
        else:
            self._observe_fec_transport_capability(
                fec_advertised,
                self._receiver_packets,
            )
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
        self._receiver_stagger_phases = int(response[314])
        self._note_legacy_snapshot(
            self._receiver_stagger_phases == LEGACY_SNAPSHOT_SENTINEL
        )
        self._receiver_operation_sequence = self._response_u32(response, 316)
        if self._receiver_capabilities & (
            CAPABILITY_FEC_ENVELOPE_V2
            | CAPABILITY_FEC_ENVELOPE_V3
            | CAPABILITY_FEC_ENVELOPE_V4
            | CAPABILITY_FEC_ENVELOPE_V5
            | CAPABILITY_FEC_ENVELOPE_V6
            | CAPABILITY_FEC_ENVELOPE_V7
        ):
            self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V7
        elif self._receiver_capabilities & CAPABILITY_STATUS_V6:
            self._receiver_status_query_bytes = RECEIVER_STATUS_BYTES_V6
        elif (
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
            self._clear_receiver_native_status()
        return fresh

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
        fresh = self._update_receiver_status_v3(response)
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
            self._clear_receiver_native_status()
        return fresh

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
        fresh = self._update_receiver_status_v4(response)
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
        if tuple(int(response[index]) for index in range(4)) == RECEIVER_STATUS_MAGIC_V5:
            self._clear_receiver_native_status()
        return fresh

    def _clear_receiver_native_status(self):
        """Drop status-v6-only module telemetry after a real downgrade."""
        self._receiver_native_result = 0
        self._receiver_native_transfer_state = 0
        self._receiver_native_watchdog_phase = 0
        self._receiver_native_flags = 0
        for name in (
            "capacity_bytes", "used_bytes", "free_bytes", "reserve_bytes",
            "reclaimable_bytes", "received_bytes", "total_bytes",
            "state_generation", "preflight_token", "active_schema_revision",
            "active_cadence_hz", "active_local_strips", "active_target",
            "active_global_strips", "active_leds_per_strip",
            "active_global_strip_offset", "active_parameter_size",
            "last_load_us", "last_initialize_us", "last_context_us",
            "last_render_us", "max_phase_us", "watchdog_events", "writes",
            "evictions", "stages", "verifies", "activations", "restores",
            "quarantines",
        ):
            setattr(self, f"_receiver_native_{name}", 0)
        for name in (
            "last_probe_payload_digest", "transfer_bundle_digest",
            "transfer_payload_digest", "active_bundle_digest",
            "active_payload_digest", "staged_bundle_digest",
            "staged_payload_digest", "rollback_bundle_digest",
            "rollback_payload_digest", "quarantine_payload_digest",
            "active_parameter_digest",
        ):
            setattr(self, f"_receiver_native_{name}", None)

    def _update_receiver_status_v6(self, response):
        """Parse the exact status-v6 native-module extension."""
        fresh = self._update_receiver_status_v5(response)
        flags = int(response[771])
        self._receiver_native_result = int(response[768])
        self._receiver_native_transfer_state = int(response[769])
        self._receiver_native_watchdog_phase = int(response[770])
        self._receiver_native_flags = flags
        u32_fields = (
            ("capacity_bytes", 772),
            ("used_bytes", 776),
            ("free_bytes", 780),
            ("reserve_bytes", 784),
            ("reclaimable_bytes", 788),
            ("received_bytes", 792),
            ("total_bytes", 796),
        )
        for name, offset in u32_fields:
            setattr(
                self, f"_receiver_native_{name}", self._response_u32(response, offset)
            )
        self._receiver_native_state_generation = self._response_u64(response, 800)
        self._receiver_native_preflight_token = self._response_u64(response, 808)
        self._receiver_native_last_probe_payload_digest = (
            self._optional_digest_from_response(
                response,
                816,
                present=any(int(value) for value in response[816:848]),
            )
        )
        digest_fields = (
            ("transfer_bundle_digest", 848, None),
            ("transfer_payload_digest", 880, None),
            ("active_bundle_digest", 912, 0x08),
            ("active_payload_digest", 944, 0x08),
            ("staged_bundle_digest", 976, 0x10),
            ("staged_payload_digest", 1008, 0x10),
            ("rollback_bundle_digest", 1040, 0x20),
            ("rollback_payload_digest", 1072, 0x20),
            ("quarantine_payload_digest", 1104, 0x40),
        )
        transfer_present = self._receiver_native_transfer_state in (1, 2, 3)
        for name, offset, bit in digest_fields:
            present = transfer_present if bit is None else bool(flags & bit)
            setattr(
                self,
                f"_receiver_native_{name}",
                self._optional_digest_from_response(response, offset, present=present),
            )
        self._receiver_native_active_schema_revision = self._response_u32(response, 1136)
        self._receiver_native_active_cadence_hz = self._response_u16(response, 1140)
        self._receiver_native_active_local_strips = int(response[1142])
        self._receiver_native_active_target = int(response[1143])
        self._receiver_native_active_global_strips = self._response_u16(response, 1144)
        self._receiver_native_active_leds_per_strip = self._response_u16(response, 1146)
        self._receiver_native_active_global_strip_offset = self._response_u16(
            response, 1148
        )
        self._receiver_native_active_parameter_size = self._response_u16(response, 1150)
        self._receiver_native_active_parameter_digest = self._optional_digest_from_response(
            response, 1152, present=bool(flags & 0x08)
        )
        u16_fields = (
            ("last_load_us", 1184),
            ("last_initialize_us", 1186),
            ("last_context_us", 1188),
            ("last_render_us", 1190),
            ("max_phase_us", 1192),
            ("watchdog_events", 1194),
        )
        for name, offset in u16_fields:
            setattr(
                self, f"_receiver_native_{name}", self._response_u16(response, offset)
            )
        self._receiver_native_writes = self._response_u32(response, 1196)
        self._receiver_native_evictions = self._response_u32(response, 1200)
        for name, offset in (
            ("stages", 1204), ("verifies", 1206), ("activations", 1208),
            ("restores", 1210), ("quarantines", 1212),
        ):
            setattr(
                self, f"_receiver_native_{name}", self._response_u16(response, offset)
            )
        return fresh

    def _update_receiver_status_v7(self, response):
        """Parse exact FEC transport outcomes after the complete v6 prefix."""
        fec_enabled_before_observation = bool(
            getattr(self, "_fec_transport_enabled", False)
        )
        fresh = self._update_receiver_status_v6(response)
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
            # throughout the three-observation negotiation so an early queued
            # v7 response cannot hide historical pre-enable outcomes.
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
        payload = bytearray(
            getattr(self, "_receiver_status_query_bytes", RECEIVER_STATUS_BYTES_V3)
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
            if required_version < 3 or required_version > 6:
                raise ValueError("required_status_version must be 3, 4, 5, or 6")
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
        logical = cls._bounded_uint("logical_receiver_id", logical_receiver_id, 0xFE)
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
    def _native_descriptor_bytes(
        cls,
        *,
        bundle_digest,
        payload_digest,
        payload_size,
        abi_version,
        target,
        global_strips,
        local_strips,
        leds_per_strip,
        global_strip_offset,
        cadence_hz,
        parameter_schema_revision,
        flags=0,
    ):
        size = cls._bounded_uint("payload_size", payload_size, 0xFFFFFFFF)
        if size == 0:
            raise ValueError("payload_size must be positive")
        abi = cls._bounded_uint("abi_version", abi_version, 0xFFFF)
        target_code = cls._bounded_uint("target", target, 0xFF)
        global_width = cls._bounded_uint("global_strips", global_strips, 0xFFFF)
        local_width = cls._bounded_uint("local_strips", local_strips, 0xFF)
        height = cls._bounded_uint("leds_per_strip", leds_per_strip, 0xFFFF)
        origin = cls._bounded_uint(
            "global_strip_offset", global_strip_offset, 0xFFFF
        )
        cadence = cls._bounded_uint("cadence_hz", cadence_hz, 0xFFFF)
        schema_revision = cls._bounded_uint(
            "parameter_schema_revision", parameter_schema_revision, 0xFFFFFFFF
        )
        descriptor_flags = cls._bounded_uint("flags", flags, 0xFF)
        if not abi:
            raise ValueError("abi_version must be positive")
        if target_code != NATIVE_TARGET_ESP32_S3:
            raise ValueError(f"target must be {NATIVE_TARGET_ESP32_S3} (ESP32-S3)")
        if not global_width or not local_width or not height:
            raise ValueError("native geometry dimensions must be positive")
        if local_width > 8:
            raise ValueError("native local_strips must be between 1 and 8")
        if origin + local_width > global_width:
            raise ValueError("native receiver geometry exceeds the global wall")
        if not cadence:
            raise ValueError("cadence_hz must be positive")
        if descriptor_flags:
            raise ValueError("native descriptor flags must be zero for v1")
        descriptor = struct.pack(
            ">32s32sIHBHBHHHIB",
            cls._profile_digest("bundle_digest", bundle_digest),
            cls._profile_digest("payload_digest", payload_digest),
            size,
            abi,
            target_code,
            global_width,
            local_width,
            height,
            origin,
            cadence,
            schema_revision,
            descriptor_flags,
        )
        if len(descriptor) != NATIVE_DESCRIPTOR_BYTES:
            raise AssertionError("native descriptor wire size drifted")
        return descriptor

    @classmethod
    def serialize_native_probe(cls, *, payload_digest):
        return bytes((CMD_NATIVE_PROBE,)) + cls._profile_digest(
            "payload_digest", payload_digest
        )

    @classmethod
    def serialize_native_preflight(cls, **descriptor):
        payload = bytes((CMD_NATIVE_PREFLIGHT,)) + cls._native_descriptor_bytes(
            **descriptor
        )
        if len(payload) != NATIVE_PREFLIGHT_BYTES:
            raise AssertionError("native preflight wire size drifted")
        return payload

    @classmethod
    def serialize_native_begin(cls, *, preflight_token, **descriptor):
        token = cls._bounded_uint(
            "preflight_token", preflight_token, 0xFFFFFFFFFFFFFFFF
        )
        if not token:
            raise ValueError("preflight_token must be positive")
        payload = (
            bytes((CMD_NATIVE_BEGIN,))
            + struct.pack(">Q", token)
            + cls._native_descriptor_bytes(**descriptor)
        )
        if len(payload) != NATIVE_BEGIN_BYTES:
            raise AssertionError("native begin wire size drifted")
        return payload

    @classmethod
    def serialize_native_chunk(cls, *, offset, data):
        normalized_offset = cls._bounded_uint("offset", offset, 0xFFFFFFFF)
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("native chunk data must be bytes-like")
        chunk = bytes(data)
        if not 1 <= len(chunk) <= MAX_NATIVE_CHUNK_BYTES:
            raise ValueError(
                f"native chunk data must contain 1..{MAX_NATIVE_CHUNK_BYTES} bytes"
            )
        if normalized_offset + len(chunk) > 0x100000000:
            raise ValueError("native chunk range exceeds uint32 address space")
        return bytes((CMD_NATIVE_CHUNK,)) + struct.pack(">I", normalized_offset) + chunk

    @classmethod
    def _serialize_native_binding_command(
        cls, command, *, bundle_digest, payload_digest
    ):
        payload = (
            bytes((command,))
            + cls._profile_digest("bundle_digest", bundle_digest)
            + cls._profile_digest("payload_digest", payload_digest)
        )
        if len(payload) != NATIVE_BINDING_COMMAND_BYTES:
            raise AssertionError("native binding command wire size drifted")
        return payload

    @classmethod
    def serialize_native_finalize(cls, **binding):
        return cls._serialize_native_binding_command(
            CMD_NATIVE_FINALIZE, **binding
        )

    @classmethod
    def serialize_native_verify(cls, **binding):
        return cls._serialize_native_binding_command(CMD_NATIVE_VERIFY, **binding)

    @classmethod
    def serialize_native_remove(cls, **binding):
        return cls._serialize_native_binding_command(CMD_NATIVE_REMOVE, **binding)

    @classmethod
    def serialize_native_activate(
        cls,
        *,
        expected_generation,
        bundle_digest,
        payload_digest,
        scene_epoch,
        deterministic_seed,
        parameter_blob,
    ):
        generation = cls._bounded_uint(
            "expected_generation", expected_generation, 0xFFFFFFFFFFFFFFFF
        )
        epoch = cls._bounded_uint("scene_epoch", scene_epoch, 0xFFFFFFFFFFFFFFFF)
        seed = cls._bounded_uint("deterministic_seed", deterministic_seed, 0xFFFFFFFF)
        parameters = cls._native_parameter_blob(parameter_blob)
        payload = (
            bytes((CMD_NATIVE_ACTIVATE,))
            + struct.pack(">Q", generation)
            + cls._profile_digest("bundle_digest", bundle_digest)
            + cls._profile_digest("payload_digest", payload_digest)
            + struct.pack(">QIH", epoch, seed, len(parameters))
            + parameters
        )
        if len(payload) != NATIVE_ACTIVATE_HEADER_BYTES + len(parameters):
            raise AssertionError("native activation wire size drifted")
        return payload

    @classmethod
    def serialize_native_parameters(
        cls,
        *,
        bundle_digest,
        payload_digest,
        parameter_schema_revision,
        parameter_blob,
    ):
        revision = cls._bounded_uint(
            "parameter_schema_revision", parameter_schema_revision, 0xFFFFFFFF
        )
        parameters = cls._native_parameter_blob(parameter_blob)
        payload = (
            bytes((CMD_NATIVE_PARAMETERS,))
            + cls._profile_digest("bundle_digest", bundle_digest)
            + cls._profile_digest("payload_digest", payload_digest)
            + struct.pack(">IH", revision, len(parameters))
            + parameters
        )
        if len(payload) != NATIVE_PARAMETERS_HEADER_BYTES + len(parameters):
            raise AssertionError("native parameter wire size drifted")
        return payload

    @classmethod
    def serialize_native_restore(
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
        payload = (
            bytes((CMD_NATIVE_RESTORE,))
            + struct.pack(">Q", generation)
            + cls._native_binding(active_binding, field="active_binding")
            + cls._native_binding(staged_binding, field="staged_binding")
            + cls._native_binding(rollback_binding, field="rollback_binding")
        )
        if len(payload) != NATIVE_RESTORE_BYTES:
            raise AssertionError("native restore wire size drifted")
        return payload

    @classmethod
    def serialize_native_quarantine_clear(cls, *, payload_digest):
        return bytes((CMD_NATIVE_QUARANTINE_CLEAR,)) + cls._profile_digest(
            "payload_digest", payload_digest
        )

    def native_probe(self, **kwargs):
        return self._native_command_status(self.serialize_native_probe(**kwargs))

    def native_preflight(self, **kwargs):
        return self._native_command_status(self.serialize_native_preflight(**kwargs))

    def native_begin(self, **kwargs):
        return self._native_command_status(self.serialize_native_begin(**kwargs))

    def native_chunk(self, **kwargs):
        return self._native_command_status(self.serialize_native_chunk(**kwargs))

    def native_finalize(self, **kwargs):
        return self._native_command_status(self.serialize_native_finalize(**kwargs))

    def native_verify(self, **kwargs):
        return self._native_command_status(self.serialize_native_verify(**kwargs))

    def native_activate(self, **kwargs):
        return self._native_command_status(self.serialize_native_activate(**kwargs))

    def native_stop(self):
        return self._native_command_status(bytes((CMD_NATIVE_STOP,)))

    def native_parameters(self, **kwargs):
        return self._native_command_status(self.serialize_native_parameters(**kwargs))

    def native_remove(self, **kwargs):
        return self._native_command_status(self.serialize_native_remove(**kwargs))

    def native_abort(self):
        return self._native_command_status(bytes((CMD_NATIVE_ABORT,)))

    def native_restore(self, **kwargs):
        return self._native_command_status(self.serialize_native_restore(**kwargs))

    def native_quarantine_clear(self, **kwargs):
        return self._native_command_status(
            self.serialize_native_quarantine_clear(**kwargs)
        )

    def _native_command_status(self, payload):
        # Status-v6 is appended without changing the exact queued-operation
        # acknowledgement contract. Keep this method fail-closed until that
        # negotiated extension is available.
        status = self._command_status(payload, required_status_version=6)
        result = int(status.get("receiver_native_result", 0) or 0)
        if result != 1:
            result_name = NATIVE_RESULT_NAMES.get(result, f"unknown_{result}")
            raise RuntimeError(
                f"receiver rejected native command 0x{payload[0]:02x} "
                f"with result {result_name} ({result})"
            )
        return status

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
        rgba, count = cls._premultiplied_rgba_bytes(
            premultiplied_rgba, maximum=LEGACY_MAX_RGBA_PIXELS_PER_PATCH
        )
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
                item[1], maximum=LEGACY_MAX_RGBA_PIXELS_PER_BATCH_SPAN
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
        packet_bytes = OVERLAY_PATCH_BATCH_HEADER_BYTES
        for span in normalized:
            span_bytes = OVERLAY_PATCH_BATCH_SPAN_HEADER_BYTES + len(span[1])
            if (
                packet_spans
                and packet_bytes + span_bytes > MAX_ALIGNED_SEMANTIC_BYTES
            ):
                packets.append(cls.serialize_overlay_patch_batch(
                    controller_session_id=controller_session_id,
                    generation=generation,
                    spans=packet_spans,
                ))
                packet_spans = []
                packet_bytes = OVERLAY_PATCH_BATCH_HEADER_BYTES
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
        payload = self.serialize_overlay_patch(**kwargs)
        if (
            getattr(self, "_transport_envelope_enabled", False)
            and len(payload) > MAX_ALIGNED_SEMANTIC_BYTES
        ):
            raise ValueError(
                "overlay patch exceeds the aligned transport semantic limit"
            )
        return self._overlay_command_status(payload)

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
        payload = self.serialize_overlay_patch_batch(**kwargs)
        if (
            getattr(self, "_transport_envelope_enabled", False)
            and len(payload) > MAX_ALIGNED_SEMANTIC_BYTES
        ):
            raise ValueError(
                "overlay patch batch exceeds the aligned transport semantic limit"
            )
        return self._overlay_command_status(payload)

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
            # packets. Re-split them to the final negotiated transport limit
            # and materialize every packet before I/O; otherwise an aligned
            # host could stage an early span and only then reject a later
            # legacy-sized span.
            maximum_pixels = (
                MAX_RGBA_PIXELS_PER_PATCH
                if getattr(self, "_transport_envelope_enabled", False)
                else LEGACY_MAX_RGBA_PIXELS_PER_PATCH
            )
            fallback_packets = []
            for start, value in patch_items:
                rgba, count = self._premultiplied_rgba_bytes(
                    value, maximum=OVERLAY_LOCAL_PIXELS
                )
                for offset in range(0, count, maximum_pixels):
                    span_count = min(maximum_pixels, count - offset)
                    byte_start = offset * 4
                    byte_end = byte_start + span_count * 4
                    fallback_packets.append(self.serialize_overlay_patch(
                        controller_session_id=controller_session_id,
                        generation=generation,
                        start=start + offset,
                        premultiplied_rgba=rgba[byte_start:byte_end],
                    ))
            packets = tuple(fallback_packets)
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

    def _note_legacy_snapshot(self, is_legacy):
        """Record, and announce once, a snapshot missing its newest field.

        Nothing about this combination fails: the older bytes still parse, the
        counters stay correct, and the receiver keeps reporting. That silence
        is the hazard, because a phase count of zero otherwise looks like a
        stuck receiver rather than a host that is newer than the flash.
        """
        self._receiver_status_legacy = is_legacy
        if not is_legacy or getattr(self, '_legacy_snapshot_warned', False):
            return

        self._legacy_snapshot_warned = True
        print(
            f"Warning: /dev/spidev{getattr(self, 'bus', '?')}."
            f"{getattr(self, 'device', '?')} returned a status "
            f"snapshot with no stagger_phases field, so its flashed firmware "
            f"predates the {RECEIVER_STATUS_BYTES_V2}-byte layout this host "
            "expects. Every other counter is still valid. Reflash with "
            "'just deploy' before trusting the reported phase count.",
            file=sys.stderr,
        )

    def _refresh_configuration(self, force=False):
        now = time.time()
        
        # Only send config if it's actually different or forced
        current_config = (
            self.strip_count,
            self.leds_per_strip,
            getattr(self, "logical_device_id", None),
            getattr(self, "global_strip_offset", None),
            getattr(self, "reverse_native_strip_order", False),
        )
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
                global_strip_offset = self._optional_global_strip_offset(
                    getattr(self, "global_strip_offset", None)
                )
                if (
                    global_strip_offset is not None
                ):
                    cfg.extend(struct.pack(">H", global_strip_offset))
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
    
    def set_lane_mask(self, lane_mask):
        """Restrict which WS2812 lanes emit edges.

        Diagnostic aid for isolating per-lane signal integrity faults from
        faults caused by all eight lanes switching simultaneously. Masked lanes
        receive no edges at all, so their pixels hold the last latched frame.
        """
        self._refresh_configuration()
        self._xfer([CMD_SET_LANE_MASK, int(lane_mask) & 0xFF])

    def set_lane_mask_acknowledged(self, lane_mask):
        """Set a lane mask and prove the receiver processed this command.

        ``set_lane_mask`` intentionally remains the ordinary fire-and-forget
        configuration path.  The guarded maintenance transaction uses this
        stricter form, then separately waits for the display task to report
        that the requested mask has become physically applied.
        """
        if isinstance(lane_mask, bool) or not isinstance(lane_mask, int):
            raise ValueError("lane_mask must be an integer")
        self._refresh_configuration()
        return self._command_status(bytes((CMD_SET_LANE_MASK, lane_mask & 0xFF)))

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
        self._refresh_configuration(force=True)
        if self.debug:
            print(f"✓ Configuration sent (strips={self.strip_count}, leds/strip={self.leds_per_strip})")

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
                aligned_frame = bool(
                    getattr(self, "_transport_envelope_enabled", False)
                )
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
                    RECEIVER_STATUS_BYTES_V3,
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
                    # A one-strip aligned SET_ALL is too short to clock a
                    # complete status snapshot. A receiver selected for FEC
                    # also needs v7-only lifetime counters before FEC can be
                    # enabled; its ordinary pre-FEC SET_ALL response is the
                    # deliberately legacy-safe v3 prefix and cannot advance
                    # that negotiation. Once enabled, the protected
                    # host-to-receiver envelope has no corresponding
                    # receiver-to-host FEC payload. In all three cases, sample
                    # first with the established fresh status-query drain,
                    # then keep the actual frame on the write-only path. The
                    # logical frame is still classified exactly once below.
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

    def present_complete_frame(self, colors, *, wall_frame_sequence, frame_digest):
        """Write a frame then demand a causally post-write acknowledgement."""
        frame_digest = self._identity_digest(frame_digest, "frame_digest")
        if (
            isinstance(wall_frame_sequence, bool)
            or not isinstance(wall_frame_sequence, int)
            or wall_frame_sequence < 0
        ):
            raise ValueError("wall_frame_sequence must be a non-negative integer")
        transport_lock = getattr(self, "_transport_lock", None)
        if transport_lock is None:
            transport_lock = self._transport_lock = threading.RLock()
        with transport_lock:
            before = self.get_stats()
            before_responses = before.get("receiver_status_responses")
            before_accepted = before.get("receiver_frames_accepted")
            before_sequence = before.get("receiver_last_accepted_sequence")
            if (
                isinstance(before_responses, bool)
                or not isinstance(before_responses, int)
                or isinstance(before_accepted, bool)
                or not isinstance(before_accepted, int)
                or not 0 <= before_accepted <= 0xFFFFFFFF
                or isinstance(before_sequence, bool)
                or not isinstance(before_sequence, int)
                or not 0 <= before_sequence <= 0xFFFFFFFF
            ):
                raise RuntimeError("complete-frame acknowledgement has invalid baseline counters")
            self.set_all_pixels(colors, wall_frame_sequence=wall_frame_sequence)
            status = self.query_fresh_receiver_status()
            after = status.get("receiver_status_responses")
            accepted = status.get("receiver_frames_accepted")
            receiver_sequence = status.get("receiver_last_accepted_sequence")
            if (
                isinstance(after, bool)
                or not isinstance(after, int)
                or after <= before_responses
            ):
                raise RuntimeError("complete-frame acknowledgement used a stale status")
            if (
                isinstance(accepted, bool)
                or not isinstance(accepted, int)
                or not 0 <= accepted <= 0xFFFFFFFF
                or accepted != (before_accepted + 1) & 0xFFFFFFFF
            ):
                raise RuntimeError(
                    "complete-frame acknowledgement did not advance the accepted-frame counter"
                )
            if (
                isinstance(receiver_sequence, bool)
                or not isinstance(receiver_sequence, int)
                or not 0 <= receiver_sequence <= 0xFFFFFFFF
            ):
                raise RuntimeError(
                    "complete-frame acknowledgement has an invalid receiver-assigned sequence"
                )
            if receiver_sequence != (before_sequence + 1) & 0xFFFFFFFF:
                raise RuntimeError(
                    "complete-frame acknowledgement did not advance the receiver-assigned sequence"
                )
            if int(status.get("receiver_status_version", 0) or 0) < 3:
                raise RuntimeError("complete-frame acknowledgement requires receiver status v3")
            if status.get("receiver_logical_device") != self.logical_device_id:
                raise RuntimeError("complete-frame acknowledgement has the wrong receiver")
        return {
            "logical_device": self.logical_device_id,
            "wall_frame_sequence": wall_frame_sequence,
            "receiver_accepted_sequence": receiver_sequence,
            "frame_digest": frame_digest,
            "status": dict(status),
        }
    
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
            'semantic_bytes_sent': getattr(self, '_semantic_bytes_sent', 0),
            'transport_envelope_enabled': bool(
                getattr(self, '_transport_envelope_enabled', False)
            ),
            'transport_envelope_negotiation_required': (
                TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS
            ),
            'transport_envelope_negotiation_candidate': getattr(
                self, '_transport_envelope_candidate', None
            ),
            'transport_envelope_negotiation_streak': getattr(
                self, '_transport_envelope_candidate_streak', 0
            ),
            'transport_envelope_last_receiver_packets': getattr(
                self, '_transport_envelope_last_receiver_packets', None
            ),
            'transport_envelope_fresh_observations': getattr(
                self, '_transport_envelope_fresh_observations', 0
            ),
            'transport_envelope_stale_observations': getattr(
                self, '_transport_envelope_stale_observations', 0
            ),
            'transport_envelope_counter_resets': getattr(
                self, '_transport_envelope_counter_resets', 0
            ),
            'transport_envelope_invalid_resets': getattr(
                self, '_transport_envelope_invalid_resets', 0
            ),
            'transport_envelope_transitions': getattr(
                self, '_transport_envelope_transitions', 0
            ),
            'transport_envelope_bytes_sent': getattr(
                self, '_transport_envelope_bytes_sent', 0
            ),
            'transport_padding_bytes_sent': getattr(
                self, '_transport_padding_bytes_sent', 0
            ),
            'fec_transport_requested': bool(
                getattr(self, '_fec_transport_requested', False)
            ),
            'fec_transport_enabled': self._fec_full_frame_enabled(),
            'fec_transport_negotiation_candidate': getattr(
                self, '_fec_transport_candidate', None
            ),
            'fec_transport_negotiation_streak': getattr(
                self, '_fec_transport_candidate_streak', 0
            ),
            'fec_transport_negotiation_required': (
                TRANSPORT_ENVELOPE_NEGOTIATION_OBSERVATIONS
            ),
            'fec_transport_fresh_observations': getattr(
                self, '_fec_transport_fresh_observations', 0
            ),
            'fec_transport_stale_observations': getattr(
                self, '_fec_transport_stale_observations', 0
            ),
            'fec_transport_counter_resets': getattr(
                self, '_fec_transport_counter_resets', 0
            ),
            'fec_transport_invalid_resets': getattr(
                self, '_fec_transport_invalid_resets', 0
            ),
            'fec_transport_transitions': getattr(
                self, '_fec_transport_transitions', 0
            ),
            'fec_frames_sent': getattr(self, '_fec_frames_sent', 0),
            'fec_codewords_sent': getattr(self, '_fec_codewords_sent', 0),
            'fec_parity_bytes_sent': getattr(
                self, '_fec_parity_bytes_sent', 0
            ),
            'fec_data_padding_bytes_sent': getattr(
                self, '_fec_data_padding_bytes_sent', 0
            ),
            'full_frame_transfers': getattr(self, '_full_frame_transfers', 0),
            'full_frame_status_transfers': getattr(
                self, '_full_frame_status_transfers', 0
            ),
            'full_frame_status_samples': getattr(
                self, '_full_frame_status_samples', 0
            ),
            'full_frame_status_sample_misses': getattr(
                self, '_full_frame_status_sample_misses', 0
            ),
            'full_frame_write_only_transfers': getattr(
                self, '_full_frame_write_only_transfers', 0
            ),
            'full_frame_frames_since_status_sample': getattr(
                self, '_full_frame_frames_since_status_sample', 0
            ),
            'full_frame_max_status_sample_gap': getattr(
                self, '_full_frame_max_status_sample_gap', 0
            ),
            'spidev_buffer_size': getattr(self, '_spidev_buffer_size', None),
            'full_frame_write_only_supported': (
                self._full_frame_write_only_supported()
            ),
            'full_frame_semantic_bytes_sent': getattr(
                self, '_full_frame_semantic_bytes_sent', 0
            ),
            'full_frame_wire_bytes_sent': getattr(
                self, '_full_frame_wire_bytes_sent', 0
            ),
            'crc_bytes_sent': self._crc_bytes_sent,
            'errors': self._errors,
            'receiver_status_seen': self._receiver_status_seen,
            'receiver_status_version': self._receiver_status_version,
            'receiver_status_max_version_seen': getattr(
                self, '_receiver_status_max_version_seen', 0
            ),
            'receiver_status_legacy': getattr(self, '_receiver_status_legacy', False),
            'receiver_status_responses': self._receiver_status_responses,
            'receiver_status_misses': self._receiver_status_misses,
            'receiver_packets': self._receiver_packets,
            'receiver_crc_errors': self._receiver_crc_errors,
            'receiver_crc_ok_packets': self._receiver_crc_ok_packets,
            'receiver_fec_packets_received': getattr(
                self, '_receiver_fec_packets_received', 0
            ),
            'receiver_fec_packets_accepted': getattr(
                self, '_receiver_fec_packets_accepted', 0
            ),
            'receiver_fec_corrected_packets': getattr(
                self, '_receiver_fec_corrected_packets', 0
            ),
            'receiver_fec_corrected_codewords': getattr(
                self, '_receiver_fec_corrected_codewords', 0
            ),
            'receiver_fec_uncorrectable_packets': getattr(
                self, '_receiver_fec_uncorrectable_packets', 0
            ),
            'receiver_fec_semantic_crc_errors': getattr(
                self, '_receiver_fec_semantic_crc_errors', 0
            ),
            'receiver_fec_framing_errors': getattr(
                self, '_receiver_fec_framing_errors', 0
            ),
            'receiver_fec_uncorrectable_packets_process_delta': getattr(
                self, '_receiver_fec_uncorrectable_packets_process_delta', 0
            ),
            'receiver_fec_semantic_crc_errors_process_delta': getattr(
                self, '_receiver_fec_semantic_crc_errors_process_delta', 0
            ),
            'receiver_fec_framing_errors_process_delta': getattr(
                self, '_receiver_fec_framing_errors_process_delta', 0
            ),
            'receiver_fec_uncorrectable_packets_process_baseline': (
                (getattr(self, '_receiver_fec_terminal_baseline', None) or {})
                .get('uncorrectable_packets', 0)
            ),
            'receiver_fec_semantic_crc_errors_process_baseline': (
                (getattr(self, '_receiver_fec_terminal_baseline', None) or {})
                .get('semantic_crc_errors', 0)
            ),
            'receiver_fec_framing_errors_process_baseline': (
                (getattr(self, '_receiver_fec_terminal_baseline', None) or {})
                .get('framing_errors', 0)
            ),
            'receiver_fec_terminal_baseline_established': (
                getattr(self, '_receiver_fec_terminal_baseline', None) is not None
            ),
            'receiver_fec_terminal_baseline_invalid': getattr(
                self, '_receiver_fec_terminal_baseline_invalid', False
            ),
            'receiver_fec_terminal_counter_resets': getattr(
                self, '_receiver_fec_terminal_counter_resets', 0
            ),
            'receiver_fec_last_decode_us': getattr(
                self, '_receiver_fec_last_decode_us', 0
            ),
            'receiver_fec_max_decode_us': getattr(
                self, '_receiver_fec_max_decode_us', 0
            ),
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
            'receiver_lane_mask': getattr(self, '_receiver_lane_mask', ALL_LANES_MASK),
            'receiver_stagger_phases': getattr(
                self, '_receiver_stagger_phases', STAGGER_OFF
            ),
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
            'receiver_native_result': getattr(self, '_receiver_native_result', 0),
            'receiver_native_result_name': NATIVE_RESULT_NAMES.get(
                getattr(self, '_receiver_native_result', 0), 'unknown'
            ),
            'receiver_native_transfer_state': getattr(
                self, '_receiver_native_transfer_state', 0
            ),
            'receiver_native_transfer_state_name': NATIVE_TRANSFER_STATE_NAMES.get(
                getattr(self, '_receiver_native_transfer_state', 0), 'unknown'
            ),
            'receiver_native_watchdog_phase': getattr(
                self, '_receiver_native_watchdog_phase', 0
            ),
            'receiver_native_watchdog_phase_name': NATIVE_PHASE_NAMES.get(
                getattr(self, '_receiver_native_watchdog_phase', 0), 'unknown'
            ),
            'receiver_native_flags': getattr(self, '_receiver_native_flags', 0),
            'receiver_native_ready': bool(
                getattr(self, '_receiver_native_flags', 0) & 0x01
            ),
            'receiver_native_probe_found': bool(
                getattr(self, '_receiver_native_flags', 0) & 0x02
            ),
            'receiver_native_cache_integrity_ok': bool(
                getattr(self, '_receiver_native_flags', 0) & 0x04
            ),
            'receiver_native_executing': bool(
                getattr(self, '_receiver_native_flags', 0) & 0x80
            ),
            **{
                f'receiver_native_{name}': getattr(
                    self, f'_receiver_native_{name}', None
                )
                for name in (
                    'capacity_bytes', 'used_bytes', 'free_bytes', 'reserve_bytes',
                    'reclaimable_bytes', 'received_bytes', 'total_bytes',
                    'state_generation', 'preflight_token',
                    'last_probe_payload_digest', 'transfer_bundle_digest',
                    'transfer_payload_digest', 'active_bundle_digest',
                    'active_payload_digest', 'staged_bundle_digest',
                    'staged_payload_digest', 'rollback_bundle_digest',
                    'rollback_payload_digest', 'quarantine_payload_digest',
                    'active_schema_revision', 'active_cadence_hz',
                    'active_local_strips', 'active_target',
                    'active_global_strips', 'active_leds_per_strip',
                    'active_global_strip_offset', 'active_parameter_size',
                    'active_parameter_digest', 'last_load_us',
                    'last_initialize_us', 'last_context_us', 'last_render_us',
                    'max_phase_us', 'watchdog_events', 'writes', 'evictions',
                    'stages', 'verifies', 'activations', 'restores', 'quarantines',
                )
            },
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
