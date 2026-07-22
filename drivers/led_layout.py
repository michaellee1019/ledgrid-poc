"""Central LED layout defaults so the entire stack stays in sync."""

import os

DEFAULT_STRIP_COUNT = 32  # 4 boards x 8 strips each
HAT_STRIP_COUNT = 16  # LED Grid Wall HAT: 2 ESP32 modules x 8 strips
# Camera-verified physical height of the installed strips.
DEFAULT_LEDS_PER_STRIP = 138


def is_hat_layout() -> bool:
    return os.environ.get("LEDGRID_HAT", "").lower() in ("1", "true", "yes")


def default_strip_count() -> int:
    if is_hat_layout():
        return HAT_STRIP_COUNT
    return DEFAULT_STRIP_COUNT


def total_leds(strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP) -> int:
    """Compute total LED count for a layout."""
    return strips * leds_per_strip
