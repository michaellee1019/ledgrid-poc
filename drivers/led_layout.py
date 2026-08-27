"""Central LED layout defaults so the entire stack stays in sync."""

from typing import Callable, List, Optional, Tuple

import os

DEFAULT_STRIP_COUNT = 33  # 4 boards x 8 strips + 1 extra strip on a 5th board
HAT_STRIP_COUNT = 16  # LED Grid Wall HAT: 2 ESP32 modules x 8 strips
STRIPS_PER_DEVICE = 8
# Camera-verified physical height of the installed strips.
DEFAULT_LEDS_PER_STRIP = 138

DeviceMapEntry = Tuple[int, int]

# Left-to-right wall mapping. SPI1 CE0/CE1 stay swapped so logical groups 3
# and 4 match physical board order; the fifth receiver is SPI1 CE2, then CE3.
WALL_DEVICE_MAP: Tuple[DeviceMapEntry, ...] = (
    (0, 0),
    (0, 1),
    (1, 1),
    (1, 0),
    (1, 2),
    (1, 3),
)
SPI1_EXTRA_CHIP_SELECTS = (2, 3)

# 33rd column is packed at the start of the fifth receiver's 8-lane buffer.
# Which physical GPIO it uses is still uncertain, so the host mirrors that
# column onto every lane of that board.
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
    """Return enough 8-lane receivers to cover every configured strip."""
    return max(1, (max(1, strip_count) + strips_per_device - 1) // strips_per_device)


def logical_strip_count(
    num_devices: int,
    strips_per_device: int = STRIPS_PER_DEVICE,
    strip_count: Optional[int] = None,
) -> int:
    """Return the visible strip count, which may be smaller than device capacity."""
    capacity = max(1, num_devices) * max(1, strips_per_device)
    logical = capacity if strip_count is None else int(strip_count)
    if logical < 1 or logical > capacity:
        raise ValueError(
            f"strip_count {logical} does not fit on {num_devices} devices "
            f"with {strips_per_device} strips each"
        )
    return logical


def wall_device_map(
    num_devices: int,
    device_exists: Optional[Callable[[int, int], bool]] = None,
) -> List[DeviceMapEntry]:
    """Return the installed (bus, chip-select) map for ``num_devices`` receivers.

    Extra SPI1 chip selects prefer nodes that already exist (CE2, then CE3) so a
    board wired to either overlay node is picked without an env override.
    """
    if num_devices < 1:
        raise ValueError("num_devices must be at least 1")
    if num_devices > len(WALL_DEVICE_MAP):
        raise ValueError(
            f"wall layout supports at most {len(WALL_DEVICE_MAP)} devices"
        )

    entries = list(WALL_DEVICE_MAP[: min(4, num_devices)])
    extra_needed = num_devices - len(entries)
    if extra_needed <= 0:
        return entries

    extras: List[DeviceMapEntry] = []
    if device_exists is not None:
        for chip_select in SPI1_EXTRA_CHIP_SELECTS:
            if device_exists(1, chip_select):
                extras.append((1, chip_select))
    for chip_select in SPI1_EXTRA_CHIP_SELECTS:
        candidate = (1, chip_select)
        if candidate not in extras:
            extras.append(candidate)
    entries.extend(extras[:extra_needed])
    return entries


def total_leds(strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP) -> int:
    """Compute total LED count for a layout."""
    return strips * leds_per_strip
