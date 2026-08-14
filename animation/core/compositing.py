"""Version 1 reference math for animation layers and logical coordinates.

This module is deliberately independent of :mod:`animation.core.manager`.  It
freezes the byte-level contract shared by future host composition and receiver
firmware without activating either path.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from animation.core.presentation_contracts import BaseFrame, OverlayFrame


RGBA8_MAX = 255
DirtyRange = Tuple[int, int]
DirtyRanges = Tuple[DirtyRange, ...]


def _require_u8(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer from 0 to 255")
    result = int(value)
    if not 0 <= result <= RGBA8_MAX:
        raise ValueError(f"{name} must be from 0 to 255, got {result}")
    return result


def _require_pixel(name: str, value: Sequence[int], channels: int) -> Tuple[int, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{name} must be a {channels}-channel integer sequence")
    if len(value) != channels:
        raise ValueError(f"{name} must have {channels} channels, got {len(value)}")
    return tuple(_require_u8(f"{name}[{index}]", channel) for index, channel in enumerate(value))


def _require_premultiplied(name: str, rgba: Sequence[int]) -> Tuple[int, int, int, int]:
    red, green, blue, alpha = _require_pixel(name, rgba, 4)
    if max(red, green, blue) > alpha:
        raise ValueError(
            f"{name} must be premultiplied RGBA8; RGB channels must not exceed "
            f"alpha ({alpha}), got {(red, green, blue)}"
        )
    return red, green, blue, alpha


def round_u8_product(value: int, factor: int) -> int:
    """Scale two RGBA8 quantities using version 1 round-half-up semantics."""

    value = _require_u8("value", value)
    factor = _require_u8("factor", factor)
    return (value * factor + 127) // RGBA8_MAX


def scale_premultiplied_rgba(rgba: Sequence[int], opacity: int) -> Tuple[int, int, int, int]:
    """Apply scene opacity to premultiplied RGBA, scaling all four channels."""

    pixel = _require_premultiplied("rgba", rgba)
    opacity = _require_u8("opacity", opacity)
    return tuple(round_u8_product(channel, opacity) for channel in pixel)  # type: ignore[return-value]


def source_over_rgb(base_rgb: Sequence[int], overlay_rgba: Sequence[int]) -> Tuple[int, int, int]:
    """Blend one premultiplied overlay pixel over an opaque RGB base pixel."""

    base = _require_pixel("base_rgb", base_rgb, 3)
    overlay = _require_premultiplied("overlay_rgba", overlay_rgba)
    inverse_alpha = RGBA8_MAX - overlay[3]
    return tuple(
        min(RGBA8_MAX, overlay[channel] + round_u8_product(base[channel], inverse_alpha))
        for channel in range(3)
    )  # type: ignore[return-value]


def source_over_rgba(
    bottom_rgba: Sequence[int], top_rgba: Sequence[int]
) -> Tuple[int, int, int, int]:
    """Fold premultiplied ``top_rgba`` over ``bottom_rgba`` with one rounding step."""

    bottom = _require_premultiplied("bottom_rgba", bottom_rgba)
    top = _require_premultiplied("top_rgba", top_rgba)
    inverse_alpha = RGBA8_MAX - top[3]
    return tuple(
        min(RGBA8_MAX, top[channel] + round_u8_product(bottom[channel], inverse_alpha))
        for channel in range(4)
    )  # type: ignore[return-value]


def fold_overlays(overlays: Iterable[Sequence[int]]) -> Tuple[int, int, int, int]:
    """Fold logical overlays in declared bottom-to-top order."""

    aggregate = (0, 0, 0, 0)
    for overlay in overlays:
        aggregate = source_over_rgba(aggregate, overlay)
    return aggregate


def normalize_dirty_ranges(
    ranges: Iterable[Sequence[int]], pixel_count: int
) -> DirtyRanges:
    """Validate, sort, and coalesce half-open canonical flat-index ranges."""

    if isinstance(pixel_count, bool) or not isinstance(pixel_count, int):
        raise TypeError("pixel_count must be a non-negative integer")
    if pixel_count < 0:
        raise ValueError(f"pixel_count must be non-negative, got {pixel_count}")

    validated = []
    for index, item in enumerate(ranges):
        if isinstance(item, (str, bytes)) or not isinstance(item, Sequence) or len(item) != 2:
            raise ValueError(f"dirty range {index} must be a two-item (start, end) sequence")
        start, end = item
        if (
            isinstance(start, bool)
            or not isinstance(start, (int, np.integer))
            or isinstance(end, bool)
            or not isinstance(end, (int, np.integer))
        ):
            raise TypeError(f"dirty range {index} bounds must be integers")
        start, end = int(start), int(end)
        if not 0 <= start < end <= pixel_count:
            raise ValueError(
                f"dirty range {index} must satisfy 0 <= start < end <= {pixel_count}; "
                f"got ({start}, {end})"
            )
        validated.append((start, end))

    result: list[DirtyRange] = []
    for start, end in sorted(validated):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return tuple(result)


def union_dirty_ranges(
    *range_groups: Iterable[Sequence[int]], pixel_count: int
) -> DirtyRanges:
    """Return the canonical union used for movement and complete clearing."""

    return normalize_dirty_ranges(
        (item for group in range_groups for item in group), pixel_count
    )


def alpha_coverage_ranges(premultiplied_rgba: np.ndarray) -> DirtyRanges:
    """Convert non-zero alpha coverage into compact canonical ranges."""

    if not isinstance(premultiplied_rgba, np.ndarray):
        raise TypeError("premultiplied_rgba must be a numpy.ndarray")
    if premultiplied_rgba.dtype != np.uint8 or premultiplied_rgba.ndim != 2 or premultiplied_rgba.shape[1:] != (4,):
        raise ValueError(
            "premultiplied_rgba must have dtype uint8 and shape (total_leds, 4); "
            f"got dtype={premultiplied_rgba.dtype}, shape={premultiplied_rgba.shape}"
        )
    covered = np.flatnonzero(premultiplied_rgba[:, 3])
    if covered.size == 0:
        return ()
    starts = covered[np.r_[True, np.diff(covered) != 1]]
    ends = covered[np.r_[np.diff(covered) != 1, True]] + 1
    return tuple((int(start), int(end)) for start, end in zip(starts, ends))


def coverage_dirty_union(
    previous_rgba: np.ndarray, current_rgba: np.ndarray
) -> DirtyRanges:
    """Union previous/new coverage so movement and alpha-zero clears are complete."""

    if not isinstance(previous_rgba, np.ndarray) or not isinstance(current_rgba, np.ndarray):
        raise TypeError("previous_rgba and current_rgba must be numpy.ndarray values")
    if previous_rgba.shape != current_rgba.shape:
        raise ValueError(
            "previous_rgba and current_rgba must have identical shapes; "
            f"got {previous_rgba.shape} and {current_rgba.shape}"
        )
    previous = alpha_coverage_ranges(previous_rgba)
    current = alpha_coverage_ranges(current_rgba)
    return union_dirty_ranges(previous, current, pixel_count=previous_rgba.shape[0])


def logical_flat_index(
    strip: int, led: int, *, strip_count: int, leds_per_strip: int
) -> int:
    """Map global logical ``(strip, led)`` to canonical strip-major index."""

    _validate_geometry(strip_count, leds_per_strip)
    strip = _require_coordinate("strip", strip, strip_count)
    led = _require_coordinate("led", led, leds_per_strip)
    return strip * leds_per_strip + led


def canvas_to_logical_flat_index(
    row: int, column: int, *, strip_count: int, leds_per_strip: int
) -> int:
    """Map image-style ``(row, column)`` to logical ``(led, strip)`` order."""

    return logical_flat_index(
        column, row, strip_count=strip_count, leds_per_strip=leds_per_strip
    )


def receiver_local_index(
    global_strip: int,
    led: int,
    *,
    global_strip_offset: int,
    local_strip_count: int,
    leds_per_strip: int,
) -> int:
    """Map a global logical coordinate into one receiver's local strip-major plane."""

    _validate_geometry(local_strip_count, leds_per_strip)
    if isinstance(global_strip_offset, bool) or not isinstance(global_strip_offset, int):
        raise TypeError("global_strip_offset must be a non-negative integer")
    if global_strip_offset < 0:
        raise ValueError("global_strip_offset must be non-negative")
    global_strip = _require_coordinate(
        "global_strip",
        global_strip,
        global_strip_offset + local_strip_count,
        minimum=global_strip_offset,
    )
    led = _require_coordinate("led", led, leds_per_strip)
    return (global_strip - global_strip_offset) * leds_per_strip + led


def board_flat_slices(
    *, global_strip_count: int, leds_per_strip: int, strips_per_board: int
) -> Tuple[Tuple[int, int], ...]:
    """Return exact half-open canonical slices for equal consecutive receivers."""

    _validate_geometry(global_strip_count, leds_per_strip)
    if isinstance(strips_per_board, bool) or not isinstance(strips_per_board, int):
        raise TypeError("strips_per_board must be a positive integer")
    if strips_per_board <= 0 or global_strip_count % strips_per_board:
        raise ValueError(
            "strips_per_board must be positive and evenly divide global_strip_count"
        )
    pixels_per_board = strips_per_board * leds_per_strip
    return tuple(
        (start, start + pixels_per_board)
        for start in range(0, global_strip_count * leds_per_strip, pixels_per_board)
    )


def _validate_geometry(strip_count: int, leds_per_strip: int) -> None:
    for name, value in (("strip_count", strip_count), ("leds_per_strip", leds_per_strip)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} must be a positive integer")
        if value <= 0:
            raise ValueError(f"{name} must be positive, got {value}")


def _require_coordinate(name: str, value: int, upper: int, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value < upper:
        raise ValueError(f"{name} must satisfy {minimum} <= {name} < {upper}, got {value}")
    return value


def normalize_optional_dirty_ranges(
    ranges: Optional[Iterable[Sequence[int]]], pixel_count: int
) -> Optional[DirtyRanges]:
    """Normalize frame metadata while preserving ``None`` as 'unknown/all'."""

    if ranges is None:
        return None
    return normalize_dirty_ranges(ranges, pixel_count)


def _require_offset(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise TypeError(f"{name} must be an integer")
    return int(value)


@dataclass(frozen=True)
class PlacedOverlay:
    """One full-wall overlay translated into the global logical scene."""

    frame: OverlayFrame
    strip_offset: int = 0
    led_offset: int = 0
    opacity: int = RGBA8_MAX
    enabled: bool = True

    def __post_init__(self) -> None:
        # Import lazily because presentation_contracts imports the reference
        # dirty-range helpers from this module.
        from animation.core.presentation_contracts import OverlayFrame

        if not isinstance(self.frame, OverlayFrame):
            raise TypeError("frame must be an OverlayFrame")
        object.__setattr__(
            self, "strip_offset", _require_offset("strip_offset", self.strip_offset)
        )
        object.__setattr__(self, "led_offset", _require_offset("led_offset", self.led_offset))
        object.__setattr__(self, "opacity", _require_u8("opacity", self.opacity))
        if not isinstance(self.enabled, bool):
            raise TypeError("enabled must be a bool")


class HostForegroundCompositor:
    """Place and opacity-scale one aggregate premultiplied-RGBA plane.

    This is the host-side counterpart to the receiver's sparse foreground
    plane.  It intentionally does not accept an RGB base: the receiver owns
    that base and the host publishes only this authoritative aggregate.
    """

    def __init__(self, strip_count: int, leds_per_strip: int):
        _validate_geometry(strip_count, leds_per_strip)
        self.strip_count = strip_count
        self.leds_per_strip = leds_per_strip
        self.pixel_count = strip_count * leds_per_strip
        self._outputs = (
            np.zeros((self.pixel_count, 4), dtype=np.uint8),
            np.zeros((self.pixel_count, 4), dtype=np.uint8),
        )
        self._output_index = -1
        self._scaled = np.empty((self.pixel_count, 4), dtype=np.uint8)
        self._work = np.empty((self.pixel_count, 4), dtype=np.uint16)
        self._has_output = False
        self._placement_signature: Optional[tuple[tuple[int, int, int, bool], ...]] = None
        self._content_signature: Optional[tuple[tuple[int, int], ...]] = None
        self._coverage: DirtyRanges = ()
        self._revision = 0

    def compose(self, overlays: Sequence[PlacedOverlay] = ()) -> "OverlayFrame":
        """Return a reusable aggregate plane with exact dirty-range metadata."""

        from animation.core.presentation_contracts import OverlayFrame

        if isinstance(overlays, (str, bytes)) or not isinstance(overlays, Sequence):
            raise TypeError("overlays must be a sequence of PlacedOverlay values")
        placed = tuple(overlays)
        if len(placed) > 1:
            raise ValueError("receiver foreground version 1 supports one overlay")
        for index, item in enumerate(placed):
            if not isinstance(item, PlacedOverlay):
                raise TypeError(f"overlays[{index}] must be a PlacedOverlay")
            if item.frame.pixels.shape[0] != self.pixel_count:
                raise ValueError(
                    f"overlays[{index}] geometry must contain {self.pixel_count} pixels"
                )

        placement_signature = tuple(
            (item.strip_offset, item.led_offset, item.opacity, item.enabled)
            for item in placed
        )
        content_signature = tuple(
            (item.frame.revision, id(item.frame.pixels)) for item in placed
        )
        placement_changed = placement_signature != self._placement_signature
        content_changed = content_signature != self._content_signature
        frame_changed = any(item.frame.changed for item in placed)
        if self._has_output and not (placement_changed or content_changed or frame_changed):
            return OverlayFrame(
                self._outputs[self._output_index],
                revision=self._revision,
                changed=False,
                dirty_ranges=(),
            )

        previous_coverage = self._coverage
        self._output_index = (self._output_index + 1) % len(self._outputs)
        output = self._outputs[self._output_index]
        self._compose_output(output, placed)
        current_coverage = alpha_coverage_ranges(output)

        dirty_unknown = not self._has_output
        dirty_groups: list[DirtyRanges] = []
        if self._has_output and (placement_changed or content_changed):
            dirty_groups.extend((previous_coverage, current_coverage))
        if self._has_output:
            for item in placed:
                if not item.frame.changed or not item.enabled or item.opacity == 0:
                    continue
                if item.frame.dirty_ranges is None:
                    dirty_unknown = True
                else:
                    dirty_groups.append(self._translate_ranges(
                        item.frame.dirty_ranges,
                        strip_offset=item.strip_offset,
                        led_offset=item.led_offset,
                    ))

        self._coverage = current_coverage
        self._placement_signature = placement_signature
        self._content_signature = content_signature
        self._has_output = True
        self._revision += 1
        dirty_ranges = (
            None
            if dirty_unknown
            else union_dirty_ranges(*dirty_groups, pixel_count=self.pixel_count)
        )
        return OverlayFrame(
            output,
            revision=self._revision,
            changed=True,
            dirty_ranges=dirty_ranges,
        )

    def _placement_slices(
        self, *, strip_offset: int, led_offset: int
    ) -> Optional[tuple[slice, slice, slice, slice]]:
        destination_strip_start = max(0, strip_offset)
        destination_strip_end = min(self.strip_count, self.strip_count + strip_offset)
        destination_led_start = max(0, led_offset)
        destination_led_end = min(self.leds_per_strip, self.leds_per_strip + led_offset)
        if (
            destination_strip_start >= destination_strip_end
            or destination_led_start >= destination_led_end
        ):
            return None
        return (
            slice(destination_strip_start - strip_offset, destination_strip_end - strip_offset),
            slice(destination_led_start - led_offset, destination_led_end - led_offset),
            slice(destination_strip_start, destination_strip_end),
            slice(destination_led_start, destination_led_end),
        )

    def _compose_output(
        self, output: np.ndarray, overlays: Sequence[PlacedOverlay]
    ) -> None:
        destination = output.reshape((self.strip_count, self.leds_per_strip, 4))
        scaled = self._scaled.reshape((self.strip_count, self.leds_per_strip, 4))
        work = self._work.reshape((self.strip_count, self.leds_per_strip, 4))
        destination.fill(0)
        for item in overlays:
            if not item.enabled or item.opacity == 0:
                continue
            slices = self._placement_slices(
                strip_offset=item.strip_offset, led_offset=item.led_offset
            )
            if slices is None:
                continue
            source_strip, source_led, destination_strip, destination_led = slices
            source = item.frame.pixels.reshape(
                (self.strip_count, self.leds_per_strip, 4)
            )[source_strip, source_led]
            target = destination[destination_strip, destination_led]
            if item.opacity == RGBA8_MAX:
                np.copyto(target, source)
            else:
                scale_work = work[destination_strip, destination_led]
                np.multiply(source, item.opacity, out=scale_work, dtype=np.uint16)
                np.add(scale_work, 127, out=scale_work)
                np.floor_divide(scale_work, RGBA8_MAX, out=scale_work)
                np.copyto(
                    scaled[destination_strip, destination_led],
                    scale_work,
                    casting="unsafe",
                )
                np.copyto(target, scaled[destination_strip, destination_led])

    def _translate_ranges(
        self, ranges: DirtyRanges, *, strip_offset: int, led_offset: int
    ) -> DirtyRanges:
        slices = self._placement_slices(
            strip_offset=strip_offset, led_offset=led_offset
        )
        if slices is None or not ranges:
            return ()
        source_strip, source_led, _, _ = slices
        translated: list[DirtyRange] = []
        for start, end in ranges:
            first_strip = max(int(source_strip.start), start // self.leds_per_strip)
            last_strip = min(
                int(source_strip.stop) - 1, (end - 1) // self.leds_per_strip
            )
            for source_strip_index in range(first_strip, last_strip + 1):
                strip_start = source_strip_index * self.leds_per_strip
                segment_start = max(start - strip_start, int(source_led.start))
                segment_end = min(end - strip_start, int(source_led.stop))
                if segment_start >= segment_end:
                    continue
                destination_start = (
                    (source_strip_index + strip_offset) * self.leds_per_strip
                    + segment_start
                    + led_offset
                )
                translated.append(
                    (destination_start, destination_start + segment_end - segment_start)
                )
        return normalize_dirty_ranges(translated, self.pixel_count)


class HostSceneCompositor:
    """Compose a canonical opaque base and ordered premultiplied overlays.

    The compositor owns and reuses its base, aggregate, output, and arithmetic
    buffers.  Component buffers are read-only inputs.  Overlay order is the
    sequence order, from bottom to top.
    """

    def __init__(self, strip_count: int, leds_per_strip: int):
        _validate_geometry(strip_count, leds_per_strip)
        self.strip_count = strip_count
        self.leds_per_strip = leds_per_strip
        self.pixel_count = strip_count * leds_per_strip

        self._base = np.empty((self.pixel_count, 3), dtype=np.uint8)
        self._aggregate = np.empty((self.pixel_count, 4), dtype=np.uint8)
        # Generation of frame N+1 can overlap presentation of frame N.  Two
        # outputs keep the prior frame stable while bounding buffer ownership.
        self._outputs = (
            np.empty((self.pixel_count, 3), dtype=np.uint8),
            np.empty((self.pixel_count, 3), dtype=np.uint8),
        )
        self._output_index = -1
        self._scaled_overlay = np.empty((self.pixel_count, 4), dtype=np.uint8)
        self._rgba_work = np.empty((self.pixel_count, 4), dtype=np.uint16)
        self._rgb_work = np.empty((self.pixel_count, 3), dtype=np.uint16)
        self._alpha_work = np.empty(self.pixel_count, dtype=np.uint16)

        self._has_output = False
        self._placement_signature: Optional[tuple[tuple[int, int, int, bool], ...]] = None
        self._content_signature: Optional[tuple[tuple[int, int], ...]] = None
        self._aggregate_coverage: DirtyRanges = ()

    def compose(
        self, base: BaseFrame, overlays: Sequence[PlacedOverlay] = ()
    ) -> BaseFrame:
        """Return the reusable opaque scene buffer and exact change metadata."""

        from animation.core.presentation_contracts import BaseFrame

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
            (
                item.strip_offset,
                item.led_offset,
                item.opacity,
                item.enabled,
            )
            for item in placed
        )
        content_signature = tuple(
            (item.frame.revision, id(item.frame.pixels)) for item in placed
        )
        placement_changed = placement_signature != self._placement_signature
        overlay_frame_changed = any(item.frame.changed for item in placed)
        content_changed = content_signature != self._content_signature
        silent_content_changed = content_changed and any(
            not item.frame.changed
            and (
                self._content_signature is None
                or index >= len(self._content_signature)
                or content_signature[index] != self._content_signature[index]
            )
            for index, item in enumerate(placed)
        )
        overlays_changed = placement_changed or content_changed or overlay_frame_changed

        if self._has_output and not base.changed and not overlays_changed:
            return BaseFrame(
                self._outputs[self._output_index], changed=False, dirty_ranges=()
            )

        first_composition = not self._has_output
        dirty_unknown = first_composition
        dirty_groups: list[DirtyRanges] = []

        if first_composition or base.changed:
            np.copyto(self._base, base.pixels)
            if not first_composition:
                if base.dirty_ranges is None:
                    dirty_unknown = True
                else:
                    dirty_groups.append(base.dirty_ranges)

        if first_composition or overlays_changed:
            previous_coverage = self._aggregate_coverage
            self._compose_aggregate(placed)
            current_coverage = alpha_coverage_ranges(self._aggregate)

            if not first_composition and (placement_changed or silent_content_changed):
                dirty_groups.extend((previous_coverage, current_coverage))

            if not first_composition:
                for item in placed:
                    if not item.frame.changed or not item.enabled or item.opacity == 0:
                        continue
                    if item.frame.dirty_ranges is None:
                        dirty_unknown = True
                    else:
                        dirty_groups.append(
                            self._translate_ranges(
                                item.frame.dirty_ranges,
                                strip_offset=item.strip_offset,
                                led_offset=item.led_offset,
                            )
                        )

            self._aggregate_coverage = current_coverage
            self._placement_signature = placement_signature
            self._content_signature = content_signature

        self._output_index = (self._output_index + 1) % len(self._outputs)
        output = self._outputs[self._output_index]
        self._compose_output(output)
        self._has_output = True
        dirty_ranges = (
            None
            if dirty_unknown
            else union_dirty_ranges(*dirty_groups, pixel_count=self.pixel_count)
        )
        return BaseFrame(output, changed=True, dirty_ranges=dirty_ranges)

    def _require_pixel_count(self, name: str, pixels: np.ndarray) -> None:
        if pixels.shape[0] != self.pixel_count:
            raise ValueError(
                f"{name} geometry must contain {self.pixel_count} pixels "
                f"({self.strip_count} strips x {self.leds_per_strip} LEDs); "
                f"got {pixels.shape[0]}"
            )

    def _placement_slices(
        self, *, strip_offset: int, led_offset: int
    ) -> Optional[tuple[slice, slice, slice, slice]]:
        destination_strip_start = max(0, strip_offset)
        destination_strip_end = min(self.strip_count, self.strip_count + strip_offset)
        destination_led_start = max(0, led_offset)
        destination_led_end = min(self.leds_per_strip, self.leds_per_strip + led_offset)
        if (
            destination_strip_start >= destination_strip_end
            or destination_led_start >= destination_led_end
        ):
            return None
        return (
            slice(destination_strip_start - strip_offset, destination_strip_end - strip_offset),
            slice(destination_led_start - led_offset, destination_led_end - led_offset),
            slice(destination_strip_start, destination_strip_end),
            slice(destination_led_start, destination_led_end),
        )

    def _compose_aggregate(self, overlays: Sequence[PlacedOverlay]) -> None:
        aggregate = self._aggregate.reshape(
            (self.strip_count, self.leds_per_strip, 4)
        )
        scaled_buffer = self._scaled_overlay.reshape(
            (self.strip_count, self.leds_per_strip, 4)
        )
        rgba_work = self._rgba_work.reshape(
            (self.strip_count, self.leds_per_strip, 4)
        )
        alpha_work = self._alpha_work.reshape(
            (self.strip_count, self.leds_per_strip)
        )
        aggregate.fill(0)

        for item in overlays:
            if not item.enabled or item.opacity == 0:
                continue
            slices = self._placement_slices(
                strip_offset=item.strip_offset, led_offset=item.led_offset
            )
            if slices is None:
                continue
            source_strip, source_led, destination_strip, destination_led = slices
            source = item.frame.pixels.reshape(
                (self.strip_count, self.leds_per_strip, 4)
            )[source_strip, source_led]
            destination = aggregate[destination_strip, destination_led]

            if item.opacity == RGBA8_MAX:
                top = source
            else:
                top = scaled_buffer[destination_strip, destination_led]
                scale_work = rgba_work[destination_strip, destination_led]
                np.multiply(source, item.opacity, out=scale_work, dtype=np.uint16)
                np.add(scale_work, 127, out=scale_work)
                np.floor_divide(scale_work, RGBA8_MAX, out=scale_work)
                np.copyto(top, scale_work, casting="unsafe")

            product = rgba_work[destination_strip, destination_led]
            inverse_alpha = alpha_work[destination_strip, destination_led]
            np.subtract(RGBA8_MAX, top[..., 3], out=inverse_alpha)
            np.multiply(destination, inverse_alpha[..., None], out=product, dtype=np.uint16)
            np.add(product, 127, out=product)
            np.floor_divide(product, RGBA8_MAX, out=product)
            np.add(product, top, out=product)
            np.copyto(destination, product, casting="unsafe")

    def _compose_output(self, output: np.ndarray) -> None:
        inverse_alpha = self._alpha_work
        np.subtract(RGBA8_MAX, self._aggregate[:, 3], out=inverse_alpha)
        np.multiply(
            self._base, inverse_alpha[:, None], out=self._rgb_work, dtype=np.uint16
        )
        np.add(self._rgb_work, 127, out=self._rgb_work)
        np.floor_divide(self._rgb_work, RGBA8_MAX, out=self._rgb_work)
        np.add(self._rgb_work, self._aggregate[:, :3], out=self._rgb_work)
        np.copyto(output, self._rgb_work, casting="unsafe")

    def _translate_ranges(
        self,
        ranges: DirtyRanges,
        *,
        strip_offset: int,
        led_offset: int,
    ) -> DirtyRanges:
        slices = self._placement_slices(
            strip_offset=strip_offset, led_offset=led_offset
        )
        if slices is None or not ranges:
            return ()
        source_strip, source_led, _, _ = slices
        source_strip_start = int(source_strip.start)
        source_strip_end = int(source_strip.stop)
        source_led_start = int(source_led.start)
        source_led_end = int(source_led.stop)

        translated: list[DirtyRange] = []
        for start, end in ranges:
            first_strip = max(source_strip_start, start // self.leds_per_strip)
            last_strip = min(
                source_strip_end - 1, (end - 1) // self.leds_per_strip
            )
            for source_strip_index in range(first_strip, last_strip + 1):
                strip_start = source_strip_index * self.leds_per_strip
                segment_start = max(start - strip_start, source_led_start)
                segment_end = min(end - strip_start, source_led_end)
                if segment_start >= segment_end:
                    continue
                destination_start = (
                    (source_strip_index + strip_offset) * self.leds_per_strip
                    + segment_start
                    + led_offset
                )
                translated.append(
                    (destination_start, destination_start + segment_end - segment_start)
                )
        return normalize_dirty_ranges(translated, self.pixel_count)
