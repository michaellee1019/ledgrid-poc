#!/usr/bin/env python3
"""
Helpers for compressing/decompressing frame data payloads.

The goal is to keep status.json smaller so it can be shared or snapshotted
without megabytes of raw RGB tuples. The encoding packs the RGB list as a
compact JSON string, compresses it with zlib, and base64-encodes the result so
it remains JSON friendly.
"""

from __future__ import annotations

import base64
import json
import zlib
from typing import Any, List

FRAME_ENCODING_NAME = "json-zlib-base64"


def encode_frame_data(frame_data: List[Any]) -> str:
    """
    Compress a frame (list of RGB tuples) into a base64 string.

    Args:
        frame_data: List of RGB tuples/lists.

    Returns:
        Base64 string representing the compressed payload. Empty string when
        there is nothing to encode.
    """
    if not frame_data:
        return ""

    packed = json.dumps(frame_data, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(packed)
    return base64.b64encode(compressed).decode("ascii")


def decode_frame_data(encoded: str) -> List[Any]:
    """
    Decode a compressed frame data string back into the list representation.

    Args:
        encoded: Base64 string produced by encode_frame_data.

    Returns:
        List of RGB tuples (lists).
    """
    if not encoded:
        return []

    try:
        compressed = base64.b64decode(encoded)
        unpacked = zlib.decompress(compressed).decode("utf-8")
        return json.loads(unpacked)
    except Exception:
        # Bad payloads should not crash the UI; treat them as empty.
        return []
