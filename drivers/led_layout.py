"""Central LED layout defaults so the entire stack stays in sync."""

import os

# Finalized visible geometry: four full 8-strip receivers plus one independent
# 1-strip tail receiver. Receiver transport capacity is deliberately separate.
DEFAULT_STRIP_COUNT = 33
# Alternate HAT compatibility mode: 2 receivers x 8 strips. The carrier's
# schematic/PCB/BOM are not present in this repository.
HAT_STRIP_COUNT = 16
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
