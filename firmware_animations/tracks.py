"""Bounded RGB565 frame-track encoder, decoder, and image conversion."""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from .constants import LEDS_PER_STRIP, LOCAL_STRIPS, MAX_TRACK_BYTES, RECEIVER_COUNT, WALL_STRIPS
from .errors import PackageValidationError

_HEADER = struct.Struct(">4sBBBBHHII")
_FRAME = struct.Struct(">IIB3x")
_RUN = struct.Struct(">BH")
_MAGIC = b"LGT1"
_VERSION = 1
_KEYFRAME = 1
_TRACK_LOOP = 1
_MAX_FRAMES = 10_000
_MAX_DURATION_MS = 3_600_000
_MAX_TOTAL_DURATION_MS = 86_400_000
_PIXELS_PER_TRACK = LOCAL_STRIPS * LEDS_PER_STRIP


@dataclass(frozen=True)
class DecodedTrack:
    device_index: int
    frames: tuple[bytes, ...]
    durations_ms: tuple[int, ...]
    loop_count: int
    keyframe_interval: int


@dataclass(frozen=True)
class ImageFrames:
    frames_rgb: tuple[bytes, ...]
    durations_ms: tuple[int, ...]
    loop_count: int


def _row_major_to_strip_major(rgb: bytes) -> bytes:
    output = bytearray(len(rgb))
    for x in range(WALL_STRIPS):
        for y in range(LEDS_PER_STRIP):
            source = (y * WALL_STRIPS + x) * 3
            target = (x * LEDS_PER_STRIP + y) * 3
            output[target:target + 3] = rgb[source:source + 3]
    return bytes(output)


def _strip_major_to_row_major(rgb: bytes) -> bytes:
    output = bytearray(len(rgb))
    for x in range(WALL_STRIPS):
        for y in range(LEDS_PER_STRIP):
            source = (x * LEDS_PER_STRIP + y) * 3
            target = (y * WALL_STRIPS + x) * 3
            output[target:target + 3] = rgb[source:source + 3]
    return bytes(output)


def rgb888_to_rgb565(rgb: bytes) -> bytes:
    if len(rgb) % 3:
        raise PackageValidationError("RGB888 frame has a partial pixel")
    output = bytearray((len(rgb) // 3) * 2)
    for source in range(0, len(rgb), 3):
        red, green, blue = rgb[source:source + 3]
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        target = (source // 3) * 2
        output[target:target + 2] = value.to_bytes(2, "big")
    return bytes(output)


def rgb565_to_rgb888(rgb565: bytes) -> bytes:
    if len(rgb565) % 2:
        raise PackageValidationError("RGB565 frame has a partial pixel")
    output = bytearray((len(rgb565) // 2) * 3)
    for source in range(0, len(rgb565), 2):
        value = int.from_bytes(rgb565[source:source + 2], "big")
        red5, green6, blue5 = (value >> 11) & 0x1F, (value >> 5) & 0x3F, value & 0x1F
        target = (source // 2) * 3
        output[target:target + 3] = bytes(
            ((red5 << 3) | (red5 >> 2), (green6 << 2) | (green6 >> 4), (blue5 << 3) | (blue5 >> 2))
        )
    return bytes(output)


def split_wall_frame(frame_rgb: bytes) -> tuple[bytes, bytes, bytes, bytes]:
    expected = WALL_STRIPS * LEDS_PER_STRIP * 3
    if len(frame_rgb) != expected:
        raise PackageValidationError(f"wall frame must contain exactly {expected} RGB bytes")
    track_bytes = LOCAL_STRIPS * LEDS_PER_STRIP * 3
    return tuple(frame_rgb[i * track_bytes:(i + 1) * track_bytes] for i in range(RECEIVER_COUNT))  # type: ignore[return-value]


def _delta_payload(current: bytes, previous: bytes | None) -> bytes:
    pixel_count = len(current) // 2
    output = bytearray()
    pixel = 0
    while pixel < pixel_count:
        offset = pixel * 2
        changed = previous is None or current[offset:offset + 2] != previous[offset:offset + 2]
        end = pixel + 1
        while end < pixel_count:
            next_offset = end * 2
            next_changed = previous is None or current[next_offset:next_offset + 2] != previous[next_offset:next_offset + 2]
            if next_changed != changed:
                break
            end += 1
        length = end - pixel
        if not changed:
            output += _RUN.pack(1, length)  # preserve prior-frame pixels
        else:
            color = current[offset:offset + 2]
            repeated = all(current[index * 2:index * 2 + 2] == color for index in range(pixel + 1, end))
            if repeated and length >= 2:
                output += _RUN.pack(2, length) + color
            else:
                output += _RUN.pack(0, length) + current[offset:end * 2]
        pixel = end
    return bytes(output)


def encode_track(
    frames_rgb: Sequence[bytes],
    durations_ms: Sequence[int],
    *,
    device_index: int,
    loop_count: int = 0,
    keyframe_interval: int = 30,
) -> bytes:
    if not 0 <= device_index < RECEIVER_COUNT:
        raise PackageValidationError("track device index must be in [0, 3]")
    if not 1 <= len(frames_rgb) <= _MAX_FRAMES or len(durations_ms) != len(frames_rgb):
        raise PackageValidationError("track requires 1..10000 frames and one duration per frame")
    if not 1 <= keyframe_interval <= 1000:
        raise PackageValidationError("keyframe interval must be in [1, 1000]")
    if loop_count != 0 or isinstance(loop_count, bool):
        raise PackageValidationError("receiver frame tracks must use loop_count 0")
    total_duration = 0
    expected_frame_size = _PIXELS_PER_TRACK * 3
    encoded_frames = bytearray()
    previous: bytes | None = None
    for frame_index, (frame, duration) in enumerate(zip(frames_rgb, durations_ms)):
        if len(frame) != expected_frame_size:
            raise PackageValidationError("local RGB frame has an invalid size")
        if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= _MAX_DURATION_MS:
            raise PackageValidationError("frame duration must be an integer in [1, 3600000] ms")
        total_duration += duration
        if total_duration > _MAX_TOTAL_DURATION_MS:
            raise PackageValidationError("track duration exceeds 24 hours")
        current = rgb888_to_rgb565(frame)
        keyframe = frame_index % keyframe_interval == 0
        payload = _delta_payload(current, None if keyframe else previous)
        encoded_frames += _FRAME.pack(duration, len(payload), _KEYFRAME if keyframe else 0)
        encoded_frames += payload
        previous = current
    header_flags = _TRACK_LOOP
    result = _HEADER.pack(
        _MAGIC, _VERSION, header_flags, LOCAL_STRIPS, device_index,
        LEDS_PER_STRIP, len(frames_rgb), len(encoded_frames), loop_count,
    ) + encoded_frames
    if len(result) > MAX_TRACK_BYTES:
        raise PackageValidationError("frame track exceeds the 2.5 MiB per-receiver limit")
    return bytes(result)


def decode_track(data: bytes, *, expected_device_index: int | None = None) -> DecodedTrack:
    if len(data) > MAX_TRACK_BYTES:
        raise PackageValidationError("frame track exceeds the 2.5 MiB per-receiver limit")
    if len(data) < _HEADER.size:
        raise PackageValidationError("frame track is truncated")
    magic, version, flags, width, device, height, frame_count, data_size, loop_count = _HEADER.unpack_from(data)
    if magic != _MAGIC or version != _VERSION or flags & ~_TRACK_LOOP:
        raise PackageValidationError("unsupported frame track header")
    if flags != _TRACK_LOOP or loop_count != 0:
        raise PackageValidationError("receiver frame tracks must use infinite-loop metadata")
    if (width, height) != (LOCAL_STRIPS, LEDS_PER_STRIP):
        raise PackageValidationError("frame track geometry is incompatible")
    if not 0 <= device < RECEIVER_COUNT or (expected_device_index is not None and device != expected_device_index):
        raise PackageValidationError("frame track belongs to the wrong logical receiver")
    if not 1 <= frame_count <= _MAX_FRAMES or data_size != len(data) - _HEADER.size:
        raise PackageValidationError("frame track count or encoded data size is invalid")
    cursor = _HEADER.size
    current: bytearray | None = None
    frames: list[bytes] = []
    durations: list[int] = []
    total_duration = 0
    keyframe_indices: list[int] = []
    for frame_index in range(frame_count):
        if cursor + _FRAME.size > len(data):
            raise PackageValidationError("frame record is truncated")
        duration, payload_size, frame_flags = _FRAME.unpack_from(data, cursor)
        cursor += _FRAME.size
        if frame_flags & ~_KEYFRAME or (frame_index == 0 and not frame_flags & _KEYFRAME):
            raise PackageValidationError("frame keyframe flags are invalid")
        if not 1 <= duration <= _MAX_DURATION_MS:
            raise PackageValidationError("frame duration is invalid")
        total_duration += duration
        if total_duration > _MAX_TOTAL_DURATION_MS:
            raise PackageValidationError("track duration exceeds 24 hours")
        if payload_size > len(data) - cursor or payload_size < _RUN.size:
            raise PackageValidationError("frame payload is truncated or invalid")
        payload_end = cursor + payload_size
        keyframe = bool(frame_flags & _KEYFRAME)
        if keyframe:
            current = bytearray(_PIXELS_PER_TRACK * 2)
            keyframe_indices.append(frame_index)
        elif current is None:
            raise PackageValidationError("delta frame appears before a keyframe")
        produced = 0
        run_count = 0
        while cursor < payload_end:
            run_count += 1
            if run_count > _PIXELS_PER_TRACK or cursor + _RUN.size > payload_end:
                raise PackageValidationError("frame run header is truncated")
            opcode, length = _RUN.unpack_from(data, cursor)
            cursor += _RUN.size
            if length == 0 or length > _PIXELS_PER_TRACK - produced:
                raise PackageValidationError("frame run is out of bounds")
            if opcode == 1:
                if keyframe:
                    raise PackageValidationError("keyframe cannot preserve uninitialized pixels")
                byte_count = 0
            elif opcode == 0:
                byte_count = length * 2
            elif opcode == 2:
                byte_count = 2
            else:
                raise PackageValidationError("frame run has an unknown opcode")
            if cursor + byte_count > payload_end:
                raise PackageValidationError("frame run data is truncated")
            assert current is not None
            if opcode == 0:
                current[produced * 2:(produced + length) * 2] = data[cursor:cursor + byte_count]
            elif opcode == 2:
                current[produced * 2:(produced + length) * 2] = data[cursor:cursor + 2] * length
            cursor += byte_count
            produced += length
        if cursor != payload_end:
            raise PackageValidationError("frame payload contains trailing bytes")
        if produced != _PIXELS_PER_TRACK:
            raise PackageValidationError("frame runs do not cover the entire frame")
        assert current is not None
        frames.append(bytes(current))
        durations.append(duration)
    if cursor != len(data):
        raise PackageValidationError("frame track contains trailing bytes")
    interval = keyframe_indices[1] - keyframe_indices[0] if len(keyframe_indices) > 1 else frame_count
    return DecodedTrack(device, tuple(frames), tuple(durations), loop_count, interval)


def _pillow() -> tuple[Any, Any]:
    try:
        from PIL import Image, ImageSequence
    except ImportError as exc:
        raise PackageValidationError("GIF/WebP conversion requires Pillow>=10.0.0") from exc
    return Image, ImageSequence


def load_image_frames(source: str | Path | bytes) -> ImageFrames:
    Image, ImageSequence = _pillow()
    handle: Any = io.BytesIO(source) if isinstance(source, bytes) else Path(source)
    try:
        with Image.open(handle) as image:
            if image.format not in {"GIF", "WEBP"}:
                raise PackageValidationError("frame source must be GIF or WebP")
            # Pillow uses zero for infinite repetition. A missing loop field is
            # a one-shot source, represented as one completed play.
            loop_count = int(image.info["loop"]) if "loop" in image.info else 1
            frames: list[bytes] = []
            durations: list[int] = []
            for frame in ImageSequence.Iterator(image):
                rgb = frame.convert("RGB").resize((WALL_STRIPS, LEDS_PER_STRIP), resample=Image.Resampling.NEAREST)
                frames.append(_row_major_to_strip_major(rgb.tobytes()))
                duration = int(frame.info.get("duration", image.info.get("duration", 100)) or 100)
                durations.append(max(1, duration))
    except PackageValidationError:
        raise
    except (OSError, ValueError) as exc:
        raise PackageValidationError(f"cannot decode frame source: {exc}") from exc
    if not frames:
        raise PackageValidationError("frame source contains no frames")
    return ImageFrames(tuple(frames), tuple(durations), loop_count)


def encode_image_tracks(source: str | Path | bytes, *, keyframe_interval: int = 30) -> tuple[ImageFrames, tuple[bytes, bytes, bytes, bytes]]:
    image = load_image_frames(source)
    device_frames: list[list[bytes]] = [[] for _ in range(RECEIVER_COUNT)]
    for wall_frame in image.frames_rgb:
        for index, local_frame in enumerate(split_wall_frame(wall_frame)):
            device_frames[index].append(local_frame)
    tracks = tuple(
        encode_track(
            device_frames[index], image.durations_ms, device_index=index,
            loop_count=0, keyframe_interval=keyframe_interval,
        )
        for index in range(RECEIVER_COUNT)
    )
    return image, tracks  # type: ignore[return-value]


def generate_preview_webp(image: ImageFrames) -> bytes:
    Image, _ = _pillow()
    frames = [Image.frombytes("RGB", (WALL_STRIPS, LEDS_PER_STRIP), _strip_major_to_row_major(frame)) for frame in image.frames_rgb]
    output = io.BytesIO()
    frames[0].save(
        output,
        format="WEBP",
        save_all=True,
        append_images=frames[1:],
        duration=list(image.durations_ms),
        loop=image.loop_count,
        lossless=True,
        method=6,
        exact=True,
    )
    return output.getvalue()


def assemble_wall_frames(tracks: Sequence[DecodedTrack]) -> tuple[bytes, ...]:
    if len(tracks) != RECEIVER_COUNT or [track.device_index for track in tracks] != list(range(RECEIVER_COUNT)):
        raise PackageValidationError("four ordered receiver tracks are required")
    frame_count = len(tracks[0].frames)
    durations = tracks[0].durations_ms
    loop_count = tracks[0].loop_count
    if any(len(track.frames) != frame_count or track.durations_ms != durations or track.loop_count != loop_count for track in tracks[1:]):
        raise PackageValidationError("receiver tracks disagree on timing or looping")
    return tuple(
        b"".join(rgb565_to_rgb888(track.frames[frame_index]) for track in tracks)
        for frame_index in range(frame_count)
    )
