"""Central LED layout defaults so the entire stack stays in sync."""

import os
from typing import List, Optional, Tuple

# Finalized visible geometry: four full 8-strip receivers plus one independent
# 1-strip tail receiver. Receiver transport capacity is deliberately separate.
DEFAULT_STRIP_COUNT = 33
# Optional one-HAT canvas: left 16 columns on SPI1 CE0/CE1. Production is 33.
HAT_STRIP_COUNT = 16
STRIPS_PER_DEVICE = 8
# Camera-verified physical height of the installed strips.
DEFAULT_LEDS_PER_STRIP = 138

DeviceMapEntry = Tuple[int, int]

# Transport identity stays on the V6 jumper map: logical 0-4 are spidev1.0,
# 1.1, 0.0, 0.1, and 0.3. Physical left-to-right is a different permutation.
# 2026-11-07 column_sequence after the first remap: 8-16 (SPI1 CE0), 26-17
# (SPI0 CE1), 27-32 (SPI0 CE0 reversed), 8-1 (SPI1 CE1 reversed), then 33.
# Physical LTR is SPI1 CE1, SPI1 CE0, SPI0 CE1 reversed, SPI0 CE0 reversed,
# SPI0 CE3. V6 SPI1 CE0 is GPIO22 (header pin 15); CE1 is GPIO17 (pin 11).
# The populated third-HAT module is SJ_SPI0_CE3 on GPIO6 (pin 31). SJ_SPI0_CE2
# on GPIO5 (pin 29) is the empty socket and is not a logical receiver.
WALL_DEVICE_MAP: Tuple[DeviceMapEntry, ...] = (
    (1, 0),
    (1, 1),
    (0, 0),
    (0, 1),
    (0, 3),
)

# One-PCB 16-column canvas is the left HAT: SPI1 CE0/CE1.
HAT_DEVICE_MAP: Tuple[DeviceMapEntry, ...] = (
    (1, 0),
    (1, 1),
)

# Keep every SPI1 route at the same Hz so chip-select alternation does not
# leave the shared controller in the wrong mode. New V6 boards ran SPI1 CE0/CE1
# and SPI0 CE0/CE1/CE2 cleanly at 20 MHz until a measured shortfall.
WALL_RECEIVER_SPI_SPEEDS_HZ = (
    20_000_000,
    20_000_000,
    20_000_000,
    20_000_000,
    20_000_000,
)

# Indexed by logical receiver ID unless the name explicitly says physical order.
# Physical left-to-right logical IDs from the 2026-11-07 column_sequence pass.
WALL_PHYSICAL_LANE_ORDER = (1, 0, 3, 2, 4)
WALL_RECEIVER_STRIP_COUNTS = (8, 8, 8, 8, 1)
WALL_RECEIVER_GLOBAL_STRIP_OFFSETS = (8, 0, 24, 16, 32)
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


def hardware_receiver_count(
    strips: int, leds_per_strip: int = DEFAULT_LEDS_PER_STRIP
) -> int:
    """Return the receiver count for a supported hardware canvas."""
    if type(strips) is not int or type(leds_per_strip) is not int:
        raise ValueError("hardware geometry must be integer strip and LED counts")
    if leds_per_strip != DEFAULT_LEDS_PER_STRIP:
        raise ValueError("hardware mode requires 138 LEDs per strip")
    if strips == DEFAULT_STRIP_COUNT:
        return 5
    if strips == HAT_STRIP_COUNT:
        return 2
    raise ValueError(
        "hardware mode requires the 33x138 wall or 16x138 SPI1 HAT test"
    )


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


def hardware_device_map(num_devices: int) -> List[DeviceMapEntry]:
    """Return SPI routes for the active hardware canvas."""
    if is_hat_layout() and num_devices == 2:
        return list(HAT_DEVICE_MAP)
    return wall_device_map(num_devices)


def total_leds(strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP) -> int:
    """Compute total LED count for a layout."""
    return strips * leds_per_strip
