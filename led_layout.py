"""Central LED layout defaults so the entire stack stays in sync."""

DEFAULT_STRIP_COUNT = 16  # 2 ESP32 devices × 8 strips each
DEFAULT_LEDS_PER_STRIP = 140


def total_leds(strips: int = DEFAULT_STRIP_COUNT,
               leds_per_strip: int = DEFAULT_LEDS_PER_STRIP) -> int:
    """Compute total LED count for a layout."""
    return strips * leds_per_strip
