# Debugging and Diagnostics

This guide covers the fastest ways to inspect system health without changing
running code.

## Quick Checks

1. Verify controller status:
   - `run_state/status.json` should update at the configured interval.
   - Check `is_running`, `current_animation`, and `actual_fps`.

2. Fetch metrics via the web API:
   - `GET /api/status`
   - `GET /api/metrics`
   - `GET /api/hardware/stats`

3. Confirm SPI devices exist (on Raspberry Pi):
   ```bash
   ls -l /dev/spidev*
   ```

## Status File Fields

The status payload includes:
- `performance`: Timing samples for the animation loop.
- `driver_stats`: Per-device SPI stats (frames, bytes, errors).
- `animation_stats`: Optional plugin-specific stats.

See `docs/METRICS.md` for field descriptions and API payloads.

## Common Symptoms

- **Low FPS**: Inspect `performance.avg_frame_ms` and driver timing.
- **Stale UI**: Confirm the controller is writing `run_state/status.json`.
- **Missing LEDs**: Check `driver_stats.aggregate.total_leds` vs expected.

## Diagnostics Tools

Current tools live in `tools/diagnostics/`:
- `remote_diagnostics.sh`: Remote health check (processes, ports, logs, API status).
- `extract_frame_payload.py`: Decode compressed frame payloads.
- `led_controller_spi_test.py`: SPI smoke test (legacy).
- `fluid_tank_simulation.py`: Offline sim helper.
- `legacy/`: Archived scripts kept for reference only.

## Where Metrics Are Produced

- Controller metrics: `animation/core/manager.py`
- Status file writer: `scripts/start_server.py`
- API normalization: `web/app.py`

## Remote Diagnostics (Deploy Host)

Preferred entry points:
- `just diagnose-remote`
- `just diagnose-remote-restart` (also clears port 5000 and restarts web)

Output is written to `diagnostics/remote_diagnostics.out`.
