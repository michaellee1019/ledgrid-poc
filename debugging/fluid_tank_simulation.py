"""Reusable headless simulator for the Fluid Tank animation."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional
import time

from animation.plugins.fluid_tank import FluidTankAnimation
from drivers.led_layout import DEFAULT_STRIP_COUNT, DEFAULT_LEDS_PER_STRIP


@dataclass
class SimulationConfig:
    duration_s: float = 90.0
    fps: float = 30.0
    sample_every_s: float = 2.0
    strips: int = DEFAULT_STRIP_COUNT
    leds_per_strip: int = DEFAULT_LEDS_PER_STRIP
    animation_config: Optional[Dict[str, Any]] = None


class _SimulationController:
    """Minimal controller stub so the animation can run headless."""

    def __init__(self, strips: int, leds_per_strip: int):
        self.strip_count = strips
        self.leds_per_strip = leds_per_strip
        self.total_leds = strips * leds_per_strip
        self.inline_show = True

    def set_all_pixels(self, *_args, **_kwargs):
        pass

    def show(self):
        pass

    def clear(self):
        pass


def run_simulation(config: SimulationConfig) -> List[Dict[str, Any]]:
    """Execute the animation for `duration_s` seconds and sample stats along the way."""
    controller = _SimulationController(config.strips, config.leds_per_strip)
    animation = FluidTankAnimation(controller, config.animation_config or {})
    animation.start()

    dt = 1.0 / max(1.0, config.fps)
    frame_count = 0
    samples: List[Dict[str, Any]] = []
    sample_interval_frames = max(1, int(round(config.sample_every_s * config.fps)))
    total_frames = int(round(config.duration_s * config.fps))

    for frame in range(total_frames + 1):
        t = frame * dt
        animation.generate_frame(t, frame_count)
        frame_count += 1
        if frame == 0 or frame % sample_interval_frames == 0 or frame >= total_frames:
            samples.append(_snapshot(animation, t, frame_count, config.fps))

    return samples


def _snapshot(animation: FluidTankAnimation, time_elapsed: float, frame_count: int, fps: float) -> Dict[str, Any]:
    """Build a structure that mirrors `/api/stats`."""
    stats = animation.get_runtime_stats()
    led_info = {
        'total_leds': animation.controller.total_leds,
        'strip_count': animation.controller.strip_count,
        'leds_per_strip': animation.controller.leds_per_strip,
    }
    return {
        'current_animation': animation.ANIMATION_NAME,
        'is_running': True,
        'frame_count': frame_count,
        'target_fps': fps,
        'actual_fps': fps,
        'timestamp': time_elapsed,
        'led_info': led_info,
        'stats': stats,
    }


def run_and_print(config: Optional[SimulationConfig] = None):
    """Helper for manual CLI runs."""
    cfg = config or SimulationConfig()
    samples = run_simulation(cfg)
    print(f"Ran {cfg.duration_s}s simulation ({len(samples)} samples)")
    for sample in samples:
        stats = sample['stats']
        fill = stats.get('fill_ratio', 0.0) * 100.0
        hole = "yes" if stats.get('hole_active') else "no"
        spray = stats.get('spray_particle_count', 0)
        bubbles = stats.get('bubble_count', 0)
        print(
            f"t={sample['timestamp']:6.1f}s fill={fill:5.1f}% "
            f"hole={hole} bubble_count={bubbles} spray={spray} "
            f"spawn_allowed={stats.get('spawn_allowed')}"
        )


if __name__ == "__main__":
    run_and_print()
