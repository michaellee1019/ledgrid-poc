"""Small, host-only reference compositor for canonical animation layers.

This deliberately has no manager, receiver, or Scene-v1 integration.  It
defines the frame boundary needed to demonstrate exact RGB/RGBA composition.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np


RGBA8_MAX = 255
DirtyRange = Tuple[int, int]
DirtyRanges = Tuple[DirtyRange, ...]
FRAME_SCHEMA = "ledgrid.layer-frame"
FRAME_VERSION = 1


def _require_u8(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer from 0 to 255")
    value = int(value)
    if not 0 <= value <= RGBA8_MAX:
        raise ValueError(f"{name} must be from 0 to 255, got {value}")
    return value


def _require_geometry(strip_count: int, leds_per_strip: int) -> None:
    for name, value in (("strip_count", strip_count), ("leds_per_strip", leds_per_strip)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be a positive integer")
        if value <= 0:
            raise ValueError(f"{name} must be positive, got {value}")


def normalize_dirty_ranges(
    ranges: Iterable[Sequence[int]], pixel_count: int
) -> DirtyRanges:
    """Validate, sort, and coalesce half-open canonical flat-index ranges."""

    if isinstance(pixel_count, bool) or not isinstance(pixel_count, int):
        raise TypeError("pixel_count must be a non-negative integer")
    if pixel_count < 0:
        raise ValueError("pixel_count must be non-negative")
    validated: list[DirtyRange] = []
    for index, item in enumerate(ranges):
        if isinstance(item, (str, bytes)) or not isinstance(item, Sequence) or len(item) != 2:
            raise ValueError(f"dirty range {index} must be a two-item (start, end) sequence")
        start, end = item
        if any(isinstance(value, bool) or not isinstance(value, (int, np.integer)) for value in (start, end)):
            raise TypeError(f"dirty range {index} bounds must be integers")
        start, end = int(start), int(end)
        if not 0 <= start < end <= pixel_count:
            raise ValueError(
                f"dirty range {index} must satisfy 0 <= start < end <= {pixel_count}; got {(start, end)}"
            )
        validated.append((start, end))

    result: list[DirtyRange] = []
    for start, end in sorted(validated):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return tuple(result)


def _optional_dirty_ranges(
    ranges: Optional[Iterable[Sequence[int]]], pixel_count: int
) -> Optional[DirtyRanges]:
    return None if ranges is None else normalize_dirty_ranges(ranges, pixel_count)


def _union_ranges(*groups: Iterable[Sequence[int]], pixel_count: int) -> DirtyRanges:
    return normalize_dirty_ranges((item for group in groups for item in group), pixel_count)


def _coverage_ranges(rgba: np.ndarray) -> DirtyRanges:
    covered = np.flatnonzero(rgba[:, 3])
    if covered.size == 0:
        return ()
    starts = covered[np.r_[True, np.diff(covered) != 1]]
    ends = covered[np.r_[np.diff(covered) != 1, True]] + 1
    return tuple((int(start), int(end)) for start, end in zip(starts, ends))


def _validate_frame(frame: object, *, channels: int) -> None:
    pixels = getattr(frame, "pixels", None)
    if getattr(frame, "schema", None) != FRAME_SCHEMA:
        raise ValueError(f"frame schema must be {FRAME_SCHEMA!r}")
    if getattr(frame, "contract_version", None) != FRAME_VERSION:
        raise ValueError(f"frame contract_version must be {FRAME_VERSION}")
    if not isinstance(getattr(frame, "changed", None), bool):
        raise TypeError("changed must be a bool")
    if not isinstance(pixels, np.ndarray):
        raise TypeError("pixels must be a numpy.ndarray")
    if pixels.dtype != np.uint8 or pixels.ndim != 2 or pixels.shape[1:] != (channels,):
        raise ValueError(
            f"pixels must have dtype uint8 and shape (total_leds, {channels}); "
            f"got dtype={pixels.dtype}, shape={pixels.shape}"
        )
    if pixels.shape[0] <= 0:
        raise ValueError("pixels must contain at least one LED")
    if not pixels.flags.c_contiguous:
        raise ValueError("pixels must be C-contiguous")


@dataclass(frozen=True)
class BaseFrame:
    """An opaque, canonical ``(total_leds, 3)`` RGB8 host background."""

    pixels: np.ndarray
    changed: bool = True
    dirty_ranges: Optional[DirtyRanges] = None
    contract_version: int = FRAME_VERSION
    schema: str = FRAME_SCHEMA

    def __post_init__(self) -> None:
        _validate_frame(self, channels=3)
        dirty = _optional_dirty_ranges(self.dirty_ranges, self.pixels.shape[0])
        if not self.changed and dirty:
            raise ValueError("changed=False cannot carry non-empty dirty_ranges")
        object.__setattr__(self, "dirty_ranges", dirty)


@dataclass(frozen=True)
class OverlayFrame:
    """A canonical premultiplied ``(total_leds, 4)`` RGBA8 overlay."""

    pixels: np.ndarray
    revision: int
    changed: bool = True
    dirty_ranges: Optional[DirtyRanges] = None
    contract_version: int = FRAME_VERSION
    schema: str = FRAME_SCHEMA

    def __post_init__(self) -> None:
        _validate_frame(self, channels=4)
        if isinstance(self.revision, bool) or not isinstance(self.revision, (int, np.integer)):
            raise TypeError("revision must be an unsigned integer")
        if not 0 <= int(self.revision) <= 2**64 - 1:
            raise ValueError("revision must be an unsigned integer")
        invalid = np.argwhere(self.pixels[:, :3] > self.pixels[:, 3:4])
        if invalid.size:
            pixel, channel = (int(value) for value in invalid[0])
            raise ValueError(
                "OverlayFrame pixels must be premultiplied RGBA8; "
                f"pixel {pixel} channel {channel} exceeds alpha {int(self.pixels[pixel, 3])}"
            )
        dirty = _optional_dirty_ranges(self.dirty_ranges, self.pixels.shape[0])
        if not self.changed and dirty:
            raise ValueError("changed=False cannot carry non-empty dirty_ranges")
        object.__setattr__(self, "dirty_ranges", dirty)


@dataclass(frozen=True)
class PlacedOverlay:
    """One full-wall overlay translated into the strip-major logical scene."""

    frame: OverlayFrame
    strip_offset: int = 0
    led_offset: int = 0
    opacity: int = RGBA8_MAX
    enabled: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.frame, OverlayFrame):
            raise TypeError("frame must be an OverlayFrame")
        for name in ("strip_offset", "led_offset"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
                raise TypeError(f"{name} must be an integer")
            object.__setattr__(self, name, int(value))
        object.__setattr__(self, "opacity", _require_u8("opacity", self.opacity))
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")


def round_u8_product(value: int, factor: int) -> int:
    """Version-1 round-half-up multiplication of two 8-bit quantities."""

    return (_require_u8("value", value) * _require_u8("factor", factor) + 127) // RGBA8_MAX


def scale_premultiplied_rgba(rgba: Sequence[int], opacity: int) -> tuple[int, int, int, int]:
    if isinstance(rgba, (str, bytes)) or not isinstance(rgba, Sequence) or len(rgba) != 4:
        raise ValueError("rgba must be a four-channel integer sequence")
    pixel = tuple(_require_u8(f"rgba[{index}]", value) for index, value in enumerate(rgba))
    if max(pixel[:3]) > pixel[3]:
        raise ValueError("rgba must be premultiplied RGBA8")
    return tuple(round_u8_product(channel, opacity) for channel in pixel)  # type: ignore[return-value]


def source_over_rgb(base_rgb: Sequence[int], overlay_rgba: Sequence[int]) -> tuple[int, int, int]:
    if isinstance(base_rgb, (str, bytes)) or not isinstance(base_rgb, Sequence) or len(base_rgb) != 3:
        raise ValueError("base_rgb must be a three-channel integer sequence")
    base = tuple(_require_u8(f"base_rgb[{index}]", value) for index, value in enumerate(base_rgb))
    top = scale_premultiplied_rgba(overlay_rgba, RGBA8_MAX)
    inverse_alpha = RGBA8_MAX - top[3]
    return tuple(min(RGBA8_MAX, top[index] + round_u8_product(base[index], inverse_alpha)) for index in range(3))  # type: ignore[return-value]


class HostSceneCompositor:
    """Compose a base and overlays in declared bottom-to-top order."""

    def __init__(self, strip_count: int, leds_per_strip: int):
        _require_geometry(strip_count, leds_per_strip)
        self.strip_count = strip_count
        self.leds_per_strip = leds_per_strip
        self.pixel_count = strip_count * leds_per_strip
        self._base = np.empty((self.pixel_count, 3), dtype=np.uint8)
        self._aggregate = np.empty((self.pixel_count, 4), dtype=np.uint8)
        self._outputs = tuple(np.empty((self.pixel_count, 3), dtype=np.uint8) for _ in range(2))
        self._work_rgba = np.empty((self.pixel_count, 4), dtype=np.uint16)
        self._work_rgb = np.empty((self.pixel_count, 3), dtype=np.uint16)
        self._work_alpha = np.empty(self.pixel_count, dtype=np.uint16)
        self._scaled = np.empty((self.pixel_count, 4), dtype=np.uint8)
        self._output_index = -1
        self._has_output = False
        self._placement_signature: Optional[tuple[tuple[int, int, int, bool], ...]] = None
        self._content_signature: Optional[tuple[tuple[int, int], ...]] = None
        self._coverage: DirtyRanges = ()
        self._aggregate_revision = 0
        self._foreground_changed = False
        self._foreground_dirty_ranges: Optional[DirtyRanges] = ()

    def compose(self, base: BaseFrame, overlays: Sequence[PlacedOverlay] = ()) -> BaseFrame:
        """Return the composed RGB frame with exact known stale-pixel coverage."""

        # Validate every input before mutating reusable state or flipping buffers.
        if not isinstance(base, BaseFrame):
            raise TypeError("base must be a BaseFrame")
        self._require_pixel_count("base", base.pixels)
        if isinstance(overlays, (str, bytes)) or not isinstance(overlays, Sequence):
            raise TypeError("overlays must be a sequence of PlacedOverlay values")
        placed = tuple(overlays)
        for index, item in enumerate(placed):
            if not isinstance(item, PlacedOverlay):
                raise TypeError(f"overlays[{index}] must be a PlacedOverlay")
            self._require_pixel_count(f"overlays[{index}].frame", item.frame.pixels)

        placement_signature = tuple(
            (item.strip_offset, item.led_offset, item.opacity, item.enabled) for item in placed
        )
        content_signature = tuple((int(item.frame.revision), id(item.frame.pixels)) for item in placed)
        placement_changed = placement_signature != self._placement_signature
        content_changed = content_signature != self._content_signature
        overlays_changed = placement_changed or content_changed or any(item.frame.changed for item in placed)
        if self._has_output and not base.changed and not overlays_changed:
            self._foreground_changed = False
            self._foreground_dirty_ranges = ()
            return BaseFrame(self._outputs[self._output_index], changed=False, dirty_ranges=())

        first = not self._has_output
        dirty_unknown = first
        dirty_groups: list[DirtyRanges] = []
        foreground_dirty_unknown = first
        foreground_dirty_groups: list[DirtyRanges] = []
        if first or base.changed:
            np.copyto(self._base, base.pixels)
            if not first:
                if base.dirty_ranges is None:
                    dirty_unknown = True
                else:
                    dirty_groups.append(base.dirty_ranges)

        if first or overlays_changed:
            previous_coverage = self._coverage
            self._compose_aggregate(placed)
            current_coverage = _coverage_ranges(self._aggregate)
            if not first and (placement_changed or content_changed):
                dirty_groups.extend((previous_coverage, current_coverage))
                foreground_dirty_groups.extend((previous_coverage, current_coverage))
            if not first:
                for item in placed:
                    if not item.frame.changed or not item.enabled or item.opacity == 0:
                        continue
                    if item.frame.dirty_ranges is None:
                        dirty_unknown = True
                        foreground_dirty_unknown = True
                    else:
                        translated = self._translate_ranges(
                            item.frame.dirty_ranges,
                            strip_offset=item.strip_offset,
                            led_offset=item.led_offset,
                        )
                        dirty_groups.append(translated)
                        foreground_dirty_groups.append(translated)
            self._coverage = current_coverage
            self._placement_signature = placement_signature
            self._content_signature = content_signature
            self._aggregate_revision += 1

        self._foreground_changed = first or overlays_changed
        self._foreground_dirty_ranges = (
            None if foreground_dirty_unknown else _union_ranges(
                *foreground_dirty_groups, pixel_count=self.pixel_count,
            )
        )

        self._output_index = (self._output_index + 1) % len(self._outputs)
        output = self._outputs[self._output_index]
        self._compose_output(output)
        self._has_output = True
        return BaseFrame(
            output,
            changed=True,
            dirty_ranges=None if dirty_unknown else _union_ranges(*dirty_groups, pixel_count=self.pixel_count),
        )

    def aggregate_foreground(self) -> OverlayFrame:
        """Return the existing aggregate premultiplied foreground transport.

        The returned buffer is owned by this compositor and is deliberately not
        another receiver plane. Callers present it only at the foreground
        transport seam and do not retain it across a later ``compose``.
        """

        if not self._has_output:
            raise ValueError("cannot read foreground before the first composition")
        return OverlayFrame(
            self._aggregate,
            revision=self._aggregate_revision,
            changed=self._foreground_changed,
            dirty_ranges=self._foreground_dirty_ranges,
        )

    def _require_pixel_count(self, name: str, pixels: np.ndarray) -> None:
        if pixels.shape[0] != self.pixel_count:
            raise ValueError(f"{name} geometry must contain {self.pixel_count} pixels; got {pixels.shape[0]}")

    def _placement_slices(self, *, strip_offset: int, led_offset: int) -> Optional[tuple[slice, slice, slice, slice]]:
        destination_strip_start, destination_strip_end = max(0, strip_offset), min(self.strip_count, self.strip_count + strip_offset)
        destination_led_start, destination_led_end = max(0, led_offset), min(self.leds_per_strip, self.leds_per_strip + led_offset)
        if destination_strip_start >= destination_strip_end or destination_led_start >= destination_led_end:
            return None
        return (
            slice(destination_strip_start - strip_offset, destination_strip_end - strip_offset),
            slice(destination_led_start - led_offset, destination_led_end - led_offset),
            slice(destination_strip_start, destination_strip_end),
            slice(destination_led_start, destination_led_end),
        )

    def _compose_aggregate(self, overlays: Sequence[PlacedOverlay]) -> None:
        aggregate = self._aggregate.reshape(self.strip_count, self.leds_per_strip, 4)
        scaled = self._scaled.reshape(self.strip_count, self.leds_per_strip, 4)
        work = self._work_rgba.reshape(self.strip_count, self.leds_per_strip, 4)
        alpha = self._work_alpha.reshape(self.strip_count, self.leds_per_strip)
        aggregate.fill(0)
        for item in overlays:
            if not item.enabled or item.opacity == 0:
                continue
            slices = self._placement_slices(strip_offset=item.strip_offset, led_offset=item.led_offset)
            if slices is None:
                continue
            source_strip, source_led, destination_strip, destination_led = slices
            source = item.frame.pixels.reshape(self.strip_count, self.leds_per_strip, 4)[source_strip, source_led]
            destination = aggregate[destination_strip, destination_led]
            if item.opacity == RGBA8_MAX:
                top = source
            else:
                top = scaled[destination_strip, destination_led]
                np.multiply(source, item.opacity, out=work[destination_strip, destination_led], dtype=np.uint16)
                np.add(work[destination_strip, destination_led], 127, out=work[destination_strip, destination_led])
                np.floor_divide(work[destination_strip, destination_led], RGBA8_MAX, out=work[destination_strip, destination_led])
                np.copyto(top, work[destination_strip, destination_led], casting="unsafe")
            np.subtract(RGBA8_MAX, top[..., 3], out=alpha[destination_strip, destination_led])
            np.multiply(destination, alpha[destination_strip, destination_led][..., None], out=work[destination_strip, destination_led], dtype=np.uint16)
            np.add(work[destination_strip, destination_led], 127, out=work[destination_strip, destination_led])
            np.floor_divide(work[destination_strip, destination_led], RGBA8_MAX, out=work[destination_strip, destination_led])
            np.add(work[destination_strip, destination_led], top, out=work[destination_strip, destination_led])
            np.copyto(destination, work[destination_strip, destination_led], casting="unsafe")

    def _compose_output(self, output: np.ndarray) -> None:
        np.subtract(RGBA8_MAX, self._aggregate[:, 3], out=self._work_alpha)
        np.multiply(self._base, self._work_alpha[:, None], out=self._work_rgb, dtype=np.uint16)
        np.add(self._work_rgb, 127, out=self._work_rgb)
        np.floor_divide(self._work_rgb, RGBA8_MAX, out=self._work_rgb)
        np.add(self._work_rgb, self._aggregate[:, :3], out=self._work_rgb)
        np.copyto(output, self._work_rgb, casting="unsafe")

    def _translate_ranges(self, ranges: DirtyRanges, *, strip_offset: int, led_offset: int) -> DirtyRanges:
        slices = self._placement_slices(strip_offset=strip_offset, led_offset=led_offset)
        if slices is None or not ranges:
            return ()
        source_strip, source_led, _, _ = slices
        translated: list[DirtyRange] = []
        for start, end in ranges:
            first_strip = max(int(source_strip.start), start // self.leds_per_strip)
            last_strip = min(int(source_strip.stop) - 1, (end - 1) // self.leds_per_strip)
            for source_strip_index in range(first_strip, last_strip + 1):
                strip_start = source_strip_index * self.leds_per_strip
                segment_start = max(start - strip_start, int(source_led.start))
                segment_end = min(end - strip_start, int(source_led.stop))
                if segment_start < segment_end:
                    destination_start = (source_strip_index + strip_offset) * self.leds_per_strip + segment_start + led_offset
                    translated.append((destination_start, destination_start + segment_end - segment_start))
        return normalize_dirty_ranges(translated, self.pixel_count)
