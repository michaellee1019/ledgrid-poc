# Fluid Tank Animation – Physics & Instrumentation Plan

## Objectives
1. **Maintain a believable hourglass scene** – single-pixel drops create ripples, surface foam, and a fill curve that reaches ~94% in 60 seconds (configurable) before draining through a puncture.
2. **Make reactive elements obvious** – bubble plumes spawn from the floor and rise, punctures flash/spray, and drops remain visible as they impact.
3. **Unify diagnostics** – simulation tooling, automated tests, and the live animation all leverage the same runtime stats payload that `/api/stats` exposes so hardware debugging matches headless sims.
4. **Interactive controls** – front-end exposes well-described sliders/toggles, rotated preview matches the panel orientation, and clicking the preview can inject scene events (e.g., random holes) for quick testing.
5. **Regression guardrails** – deterministically simulate multiple minutes of animation in CI to verify fill timing, bubble travel, hole behaviour, and stat reporting.

## Recent/Planned Changes

| Area | Notes |
| --- | --- |
| Fill guidance | Spawn rate derived from `(width * height) / target_fill_time` with adaptive deficit-correction. No drops spawn while a puncture is active or we are ahead of schedule. |
| Bubble physics | Floor bubbles animate upward with tracked rise distance so stats/tests can verify they surface properly. |
| Drop visibility | Fresh drops now leave transient “drop glow” markers so impacts remain visible even in dense fill states. |
| Hole spray | Draining spawns spray particles that shoot toward the top for ~1s, and timestamps are reported to `/api/stats`. Manual triggers (via UI clicks) reuse the same logic. |
| Instrumentation | `FluidTankAnimation.get_runtime_stats()` mirrors `/api/stats`, exposing fill ratios, spawn gating, bubble/spray previews, and manual hole timestamps. |
| Simulation harness | `tools/diagnostics/fluid_tank_simulation.py` offers `run_simulation()` for pytest and CLI runs; stats samples share the same schema as the live status API. |
| UI/Preview | Canvas renderer is rotated to match the physical orientation, pinned to the left column, supports click-to-hole events, and the right-hand tabs surface animations, controls, and a live `/api/status` JSON view (with copy + animation hash) so hardware + sims stay in sync. |

## Testing Strategy
1. **Headless run** – simulate 90 seconds at 30 FPS, asserting:
   - Fill ratio reaches ≥ 0.9 by 60 s when `drop_rate=1`.
   - At least one bubble travels ≥ 10 pixels upward before surfacing.
   - A puncture opens once the fill crosses the threshold and drains ≥ 10% of the volume within the configured `target_drain_time`.
   - Stats samples mirror the `/api/stats` schema (`current_animation`, `is_running`, `stats`, etc.).
2. **Custom configs** – allow targeted tests for faster fills (e.g., `drop_rate=3`) or longer tanks to guard against regressions on larger layouts.
3. **UI parity** – smoke test the preview renderer (rotated orientation + click-to-hole) with mocked data and ensure the `/api/hole` endpoint commands the running animation.

## Next Steps
- Add parameterized fixtures that cover various panel sizes (8×140, 16×140).
- Pipe select stat deltas (fill ratio & expected ratio) into the `/api/stats` endpoint as sparklines for the web UI.
- Capture short GIFs from the simulator once ASCII/CLI viz is stable to document expected behaviour for future regressions.
