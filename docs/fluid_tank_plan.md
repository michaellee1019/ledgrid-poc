# Fluid Tank Animation – Physics & Instrumentation

## Implemented model (v2)

The tank now treats each diffuser cell as **5 cc** and keeps one authoritative,
fractional water volume. A spring-coupled shallow-water surface supplies waves;
the cells below that surface are derived for rendering rather than simulated as
binary falling sand. This keeps mass exact while allowing a sub-cell waterline.

Inflow is also conserved: supplied water first becomes airborne 5 cc droplets
and is added to tank volume only when those droplets strike the live surface.
Drop acceleration is computed from 9.80665 m/s² and configurable physical cell
height, independently of the global decorative animation-speed multiplier.

Punctures are coordinate-addressable and multiple holes may be open at once.
Outflow is proportional to hole area and the square root of water head. A hole
therefore slows as the level falls and stops when the surface reaches its
vertical position. `target_drain_time` calibrates a default floor hole without
replacing this pressure relationship.

Rendering is optimized for physical diffusers: depth-relative color absorption,
a specular surface band, broad moving caustics, edge meniscus light, rim-lit
bubbles, dark-core punctures, turbulent rims, and short spray particles.

## Objectives
1. **Maintain a believable hourglass scene** – single-pixel drops create ripples, surface foam, and a fill curve that reaches ~94% in 60 seconds (configurable) before draining through a puncture.
2. **Make reactive elements obvious** – bubble plumes spawn from the floor and rise, punctures flash/spray, and drops remain visible as they impact.
3. **Unify diagnostics** – simulation tooling, automated tests, and the live animation all leverage the same runtime stats payload that `/api/stats` exposes so hardware debugging matches headless sims.
4. **Interactive controls** – front-end exposes well-described sliders/toggles, rotated preview matches the panel orientation, and clicking the preview can inject scene events (e.g., random holes) for quick testing.
5. **Regression guardrails** – deterministically simulate multiple minutes of animation in CI to verify fill timing, bubble travel, hole behaviour, and stat reporting.

## Runtime and controls

| Area | Notes |
| --- | --- |
| Fill guidance | Inflow becomes conserved airborne 5 cc droplets; tank volume increases only when those droplets strike the surface. Extreme flow groups mass into larger packets to bound particle count. |
| Bubble physics | Rim-lit bubbles rise at terminal speed, wobble laterally, expand slightly, and impulse the surface when they burst. |
| Hole spray | Draining spawns pressure-scaled spray, while the aperture renders with a dark center and flickering bright rim. |
| Instrumentation | `FluidTankAnimation.get_runtime_stats()` mirrors `/api/stats`, exposing fill ratios, spawn gating, bubble/spray previews, and manual hole timestamps. |
| Simulation harness | `tools/diagnostics/fluid_tank_simulation.py` offers `run_simulation()` for pytest and CLI runs; stats samples share the same schema as the live status API. |
| UI/Preview | Clicking the canvas sends its exact grid coordinate to `/api/hole`; the bolt button retains random-hole behavior. |

## Testing Strategy
1. **Headless run** – simulate 90 seconds at 30 FPS, asserting:
   - Fill ratio reaches ≥ 0.9 by 60 s when `drop_rate=1`.
   - At least one bubble travels ≥ 10 pixels upward before surfacing.
   - A puncture opens once the fill crosses the threshold and drains ≥ 10% of the volume within the configured `target_drain_time`.
   - Stats samples mirror the `/api/stats` schema (`current_animation`, `is_running`, `stats`, etc.).
2. **Physical invariants** – verify 5 cc conversion, exact capacity, deeper-hole pressure, multiple holes, and that a mid-wall hole cannot drain below its elevation.
3. **UI parity** – verify `/api/hole` forwards exact click coordinates and radius through the control channel.

## Next Steps
- Add parameterized fixtures that cover various panel sizes (8×140, 16×140).
- Pipe select stat deltas (fill ratio & expected ratio) into the `/api/stats` endpoint as sparklines for the web UI.
- Measure diffuser-cell face dimensions and tank depth so the dimensionless hole calibration can be replaced with SI-scaled areas and gravity.
- Capture short physical-device videos for color, gamma, caustic, and terminal bubble-speed calibration.
