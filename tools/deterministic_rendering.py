"""Deterministic frame-capture helpers for visual qualification.

These utilities deliberately produce in-memory frames only. They are shared by
the Composer contact-sheet and Clock baseline checks, not by the web server or
deployment path.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
import random
from types import MethodType
from typing import Any, Mapping

import numpy as np

from animation.core.base import RenderedFrame


DEFAULT_CAPTURE_SECONDS = (0.0, 0.5, 1.0, 2.0, 3.5, 5.5, 8.0, 12.0)
DEFAULT_SIMULATION_FPS = 30
FIXED_CLOCK = datetime(2026, 1, 15, 10, 19, 0, tzinfo=timezone.utc)


def preview_profile(manifest: Mapping[str, Any]) -> tuple[tuple[float, ...], int]:
    """Read deterministic capture timing from a component manifest."""
    configured = manifest.get("preview") if isinstance(manifest, Mapping) else None
    configured = configured if isinstance(configured, Mapping) else {}
    captures = tuple(
        float(value)
        for value in configured.get("capture_seconds", DEFAULT_CAPTURE_SECONDS)
    )
    fps = int(configured.get("simulation_fps", DEFAULT_SIMULATION_FPS))
    if not captures or fps <= 0:
        raise ValueError("preview capture profile must have timestamps and positive fps")
    return captures, fps


def make_deterministic(animation: Any, config: Mapping[str, Any], key: str) -> None:
    """Stabilize common plugin randomness and wall-clock inputs for a capture."""
    stable_seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    random.seed(stable_seed)
    np.random.seed(stable_seed & 0xFFFFFFFF)
    owned_rng = getattr(animation, "random", None)
    if isinstance(owned_rng, random.Random):
        owned_rng.seed(stable_seed)
    schema = animation.get_parameter_schema()
    seed_updates: dict[str, Any] = {}
    for name in ("seed", "random_seed"):
        if name in schema and not config.get(name):
            definition = schema[name]
            maximum = int(definition.get("max", 999999))
            minimum = int(definition.get("min", 1))
            seed_updates[name] = minimum + stable_seed % max(1, maximum - minimum + 1)
    if seed_updates:
        animation.update_parameters(seed_updates)
    if hasattr(animation, "_clock_now"):
        animation._clock_now = MethodType(lambda _self: FIXED_CLOCK, animation)
    if hasattr(animation, "_current_hour"):
        fixed_hour = FIXED_CLOCK.hour + FIXED_CLOCK.minute / 60 + FIXED_CLOCK.second / 3600
        original_current_hour = animation._current_hour

        def deterministic_hour(_self: Any, elapsed: float) -> float:
            fixed = float(_self.params.get("hour", -1.0))
            if fixed >= 0:
                return original_current_hour(elapsed)
            return (
                fixed_hour
                + float(_self.params.get("time_offset", 0.0))
                + elapsed * float(_self.params.get("time_scale", 1.0)) / 3600.0
            ) % 24.0

        animation._current_hour = MethodType(deterministic_hour, animation)


def capture_frames(
    animation: Any,
    *,
    captures: tuple[float, ...],
    simulation_fps: int,
) -> list[np.ndarray]:
    """Capture canonical frames at authored timestamps without writing assets."""
    frames: list[np.ndarray] = []
    capture_index = 0
    final_step = int(np.ceil(captures[-1] * simulation_fps))
    for frame_index in range(final_step + 1):
        elapsed = frame_index / simulation_fps
        rendered = animation.generate_frame(elapsed, frame_index)
        changed = rendered.changed if isinstance(rendered, RenderedFrame) else True
        pixels = rendered.pixels if isinstance(rendered, RenderedFrame) else rendered
        canonical = np.asarray(pixels, dtype=np.uint8)
        canonical = animation.apply_framework_plant_modifiers(canonical, changed=changed)
        while capture_index < len(captures) and elapsed + 1e-9 >= captures[capture_index]:
            frames.append(np.ascontiguousarray(canonical).copy())
            capture_index += 1
    if len(frames) != len(captures):
        raise RuntimeError("deterministic capture did not reach every authored timestamp")
    return frames
