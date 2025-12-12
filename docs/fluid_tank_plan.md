# Fluid Tank Animation – Physics & Instrumentation Plan

## Objectives
1. **Maintain a believable hourglass scene** – single-pixel drops create ripples, surface foam, and a fill curve that reaches ~94% in 60 seconds (configurable) before draining through a puncture.
2. **Make reactive elements obvious** – bubble plumes must spawn from the bottom and travel upwards, and punctures should eject water visibly and adjust the volume.
3. **Unify diagnostics** – simulation tooling, automated tests, and the live animation all leverage the same runtime stats payload that `/api/stats` exposes so hardware debugging matches headless sims.
4. **Regression guardrails** – deterministically simulate multiple minutes of animation in CI to verify fill timing, bubble travel, hole behaviour, and stat reporting.

## Recent/Planned Changes

| Area | Notes |
| --- | --- |
| Fill guidance | Spawn rate derived from `(width * height) / target_fill_time` with adaptive deficit-correction. No drops spawn while a puncture is active or we are ahead of schedule. |
| Bubble physics | Bottom spawns require a minimum fill ratio, bubble positions integrate velocity upward, and renderer highlights a full-body shimmer to keep them visible. Tests assert they rise several pixels before popping. |
| Hole spray | When a puncture drains cells we generate “spray particles” that shoot toward the top for ~1s while the fill ratio drops, creating the requested “water sent to the top” effect. |
| Instrumentation | `FluidTankAnimation.get_runtime_stats()` snapshots the same structure (`animation_stats`) that `/api/stats` returns, including spawn gating flags, fill ratios, and hole timers. |
| Simulation harness | `debugging/fluid_tank_simulation.py` offers `run_simulation()` which both the pytest (`tests/test_fluid_tank_simulation.py`) and ad-hoc CLI scripts can use. It returns API-shaped stats samples over time for quick inspection. |

## Testing Strategy
1. **Headless run** – simulate 90 seconds at 30 FPS, asserting:
   - Fill ratio reaches ≥ 0.9 by 60 s when `drop_rate=1`.
   - At least one bubble travels ≥ 10 pixels upward before surfacing.
   - A puncture opens once the fill crosses the threshold and drains ≥ 10% of the volume within the configured `target_drain_time`.
   - Stats samples mirror the `/api/stats` schema (`current_animation`, `is_running`, `stats`, etc.).
2. **Custom configs** – allow targeted tests for faster fills (e.g., `drop_rate=3`) or longer tanks to guard against regressions on larger layouts.

## Next Steps
- Add parameterized fixtures that cover various panel sizes (8×140, 16×140).
- Pipe select stat deltas (fill ratio & expected ratio) into the `/api/stats` endpoint as sparklines for the web UI.
- Capture short GIFs from the simulator once ASCII/CLI viz is stable to document expected behaviour for future regressions.
