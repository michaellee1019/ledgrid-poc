"""Shared Scene v2 Look palettes, including holiday color sets.

Existing animations keep their authored Neutral/Mist/Spectrum/Ember colors.
Holiday IDs and Rainbow helpers are shared so Composer, automation, and the
listed instruments stay in lockstep.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

import numpy as np


SCENE_PALETTE_IDS = (
    "neutral",
    "mist",
    "ember",
    "spectrum",
    "rainbow",
    "xmas",
    "fall",
    "easter",
    "july4",
)
HOLIDAY_IDS = ("xmas", "fall", "easter", "july4")

# Deep / mid / highlight triples used by ambient, fireworks, snake, and aurora.
HOLIDAY_TRIPLES: Mapping[str, tuple[tuple[float, float, float], ...]] = MappingProxyType({
    "xmas": ((8.0, 18.0, 10.0), (200.0, 16.0, 28.0), (255.0, 196.0, 58.0)),
    "fall": ((22.0, 8.0, 4.0), (232.0, 92.0, 18.0), (255.0, 186.0, 64.0)),
    "easter": ((28.0, 12.0, 36.0), (255.0, 140.0, 186.0), (186.0, 255.0, 140.0)),
    "july4": ((4.0, 8.0, 36.0), (220.0, 28.0, 36.0), (240.0, 244.0, 255.0)),
})

# Saturated festive stops for per-entity coloring (shells, snakes, fish).
HOLIDAY_STOPS: Mapping[str, tuple[tuple[float, float, float], ...]] = MappingProxyType({
    "xmas": ((220.0, 16.0, 28.0), (18.0, 140.0, 48.0), (255.0, 196.0, 58.0)),
    "fall": ((232.0, 92.0, 18.0), (168.0, 42.0, 12.0), (255.0, 186.0, 64.0)),
    "easter": ((255.0, 140.0, 186.0), (186.0, 140.0, 255.0), (186.0, 255.0, 140.0)),
    "july4": ((220.0, 28.0, 36.0), (240.0, 244.0, 255.0), (40.0, 80.0, 220.0)),
})

HOLIDAY_ROLE_PALETTES: Mapping[str, Mapping[str, tuple[float, float, float]]] = MappingProxyType({
    name: MappingProxyType({
        "background_low": triple[0],
        "primary": triple[1],
        "accent": triple[2],
    })
    for name, triple in HOLIDAY_TRIPLES.items()
})


def holiday_stop(palette_id: str, fraction: float) -> tuple[float, float, float] | None:
    """Pick a holiday RGB stop from a unit interval without consuming RNG."""
    stops = HOLIDAY_STOPS.get(str(palette_id))
    if stops is None:
        return None
    index = int(np.floor((float(fraction) % 1.0) * len(stops))) % len(stops)
    return stops[index]


def hsv_to_rgb_field(hue, saturation, value) -> np.ndarray:
    """Vectorized HSV to RGB in 0–255. Hue is wrapped to [0, 1)."""
    wrapped = np.mod(np.asarray(hue, dtype=np.float32), np.float32(1.0))
    sat = np.asarray(saturation, dtype=np.float32)
    val = np.clip(np.asarray(value, dtype=np.float32), 0.0, 1.0)
    h6 = wrapped * np.float32(6.0)
    sector = np.floor(h6).astype(np.int32) % 6
    frac = h6 - np.floor(h6)
    primary = val
    faded = val * (1.0 - sat)
    falling = val * (1.0 - frac * sat)
    rising = val * (1.0 - (1.0 - frac) * sat)
    red = np.empty(wrapped.shape, dtype=np.float32)
    green = np.empty(wrapped.shape, dtype=np.float32)
    blue = np.empty(wrapped.shape, dtype=np.float32)
    channels = (
        (primary, rising, faded),
        (falling, primary, faded),
        (faded, primary, rising),
        (faded, falling, primary),
        (rising, faded, primary),
        (primary, faded, falling),
    )
    for index, (red_choice, green_choice, blue_choice) in enumerate(channels):
        mask = sector == index
        red = np.where(mask, red_choice, red)
        green = np.where(mask, green_choice, green)
        blue = np.where(mask, blue_choice, blue)
    return np.stack((red, green, blue), axis=-1) * np.float32(255.0)


def nearest_stop_color(energy: np.ndarray, palette_id: str) -> np.ndarray | None:
    """Recolor trail energy with the nearest holiday stop, scaled by luma."""
    stops = HOLIDAY_STOPS.get(str(palette_id))
    if stops is None:
        return None
    samples = np.asarray(stops, dtype=np.float32)
    luma = np.max(energy, axis=-1, keepdims=True)
    flat = energy.reshape(-1, 3)
    energy_norm = np.linalg.norm(flat, axis=1, keepdims=True) + np.float32(1.0e-6)
    stop_norm = np.linalg.norm(samples, axis=1, keepdims=True) + np.float32(1.0e-6)
    scores = (flat / energy_norm) @ (samples / stop_norm).T
    chosen = samples[np.argmax(scores, axis=1)].reshape(energy.shape)
    return chosen * (luma / np.float32(255.0))
