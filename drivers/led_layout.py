"""Central LED layout defaults so the entire stack stays in sync."""

import os
from typing import List, Optional, Tuple

# Finalized visible geometry: four full 8-strip receivers plus one independent
# 1-strip tail receiver. Receiver transport capacity is deliberately separate.
DEFAULT_STRIP_COUNT = 33
# Alternate HAT compatibility mode: 2 receivers x 8 strips. The carrier's
# schematic/PCB/BOM are not present in this repository.
HAT_STRIP_COUNT = 16
STRIPS_PER_DEVICE = 8
# Camera-verified physical height of the installed strips.
DEFAULT_LEDS_PER_STRIP = 138

DeviceMapEntry = Tuple[int, int]

# Exact logical-to-transport routes for the installed wall. SPI1 CE0/CE1 are
# intentionally crossed; receiver 4 is the independently wired SPI1 CE2 tail.
WALL_DEVICE_MAP: Tuple[DeviceMapEntry, ...] = (
    (0, 0),
    (0, 1),
    (1, 1),
    (1, 0),
    (1, 2),
)

# Camera-measured installed geometry, indexed by logical receiver ID unless the
# name explicitly says physical order. Transport routes above remain a separate
# domain: logical receivers 2 and 3 still use crossed SPI1 chip-select routes,
# but the receivers now appear in logical-ID order from left to right.
WALL_PHYSICAL_LANE_ORDER = (0, 1, 2, 3, 4)
WALL_RECEIVER_STRIP_COUNTS = (8, 8, 8, 8, 1)
WALL_RECEIVER_GLOBAL_STRIP_OFFSETS = (0, 8, 16, 24, 32)
WALL_REVERSE_HOST_STRIPS_BY_LOGICAL_RECEIVER = (
    False, False, True, True, False,
)
WALL_REVERSE_NATIVE_STRIPS_BY_LOGICAL_RECEIVER = (
    False, False, True, True, False,
)
WALL_PHYSICAL_OUTPUT_LANE_MASKS = (0xFF, 0xFF, 0xFF, 0xFF, 0xFF)

# Compatibility names for callers that special-case strip 32. The fifth board
# owns one semantic strip; firmware broadcasts it across the board's outputs
# because the assembled cable lane was not recorded.
EXTRA_STRIP_LANE = 0
MIRROR_EXTRA_STRIP_ON_ALL_LANES = True


def is_hat_layout() -> bool:
    return os.environ.get("LEDGRID_HAT", "").lower() in ("1", "true", "yes")


def default_strip_count() -> int:
    if is_hat_layout():
        return HAT_STRIP_COUNT
    return DEFAULT_STRIP_COUNT


def device_count_for_strips(
    strip_count: int, strips_per_device: int = STRIPS_PER_DEVICE
) -> int:
    """Return enough fixed-capacity receivers to cover every visible strip."""
    if type(strip_count) is not int or strip_count < 1:
        raise ValueError("strip_count must be a positive integer")
    if type(strips_per_device) is not int or strips_per_device < 1:
        raise ValueError("strips_per_device must be a positive integer")
    return (strip_count + strips_per_device - 1) // strips_per_device


def logical_strip_count(
    num_devices: int,
    strips_per_device: int = STRIPS_PER_DEVICE,
    strip_count: Optional[int] = None,
) -> int:
    """Validate and return visible width independently of receiver capacity."""
    if type(num_devices) is not int or num_devices < 1:
        raise ValueError("num_devices must be a positive integer")
    if type(strips_per_device) is not int or strips_per_device < 1:
        raise ValueError("strips_per_device must be a positive integer")
    capacity = num_devices * strips_per_device
    logical = capacity if strip_count is None else strip_count
    if type(logical) is not int or not 1 <= logical <= capacity:
        raise ValueError(
            f"strip_count {logical!r} does not fit on {num_devices} devices "
            f"with {strips_per_device} strips each"
        )
    return logical


def wall_device_map(num_devices: int) -> List[DeviceMapEntry]:
    """Return the exact installed route prefix without transport fallback."""
    if type(num_devices) is not int or not 1 <= num_devices <= len(WALL_DEVICE_MAP):
        raise ValueError(
            f"wall layout supports 1 through {len(WALL_DEVICE_MAP)} devices"
        )
    return list(WALL_DEVICE_MAP[:num_devices])


def total_leds(strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP) -> int:
    """Compute total LED count for a layout."""
    return strips * leds_per_strip
