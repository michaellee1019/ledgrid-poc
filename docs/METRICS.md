# Metrics and Observability

This system exposes runtime metrics through the controller status file and
web API endpoints. These are intended for quick health checks and performance
triage.

## Status Payload

The controller writes `run_state/status.json` at a fixed interval. Key fields:

- `is_running`: Whether an animation is active.
- `current_animation`: Animation name or null.
- `frame_count`: Total frames generated since start.
- `target_fps`: Target frames per second.
- `actual_fps`: Calculated frames per second.
- `uptime`: Controller uptime in seconds.
- `animation_stats`: Optional animation-specific stats from the plugin.
- `performance`: Manager-level performance summary from `AnimationManager`.
- `driver_stats`: Driver stats payload from the controller.
- `updated_at`: Timestamp (epoch seconds).

Notes:
- `performance` and `driver_stats` are present even if empty.
- The web layer normalizes keys and fills defaults when missing.

## Web API

### GET /api/metrics
Returns a summarized performance snapshot.

Example response:
```json
{
  "animation": {
    "target_fps": 40,
    "actual_fps": 39.7,
    "uptime": 123.4
  },
  "performance": {
    "samples": 120,
    "target_frame_ms": 25.0,
    "avg_generate_ms": 4.2,
    "avg_send_ms": 3.1,
    "avg_frame_ms": 8.1,
    "last_generate_ms": 4.0,
    "last_send_ms": 3.0,
    "last_frame_ms": 7.9
  },
  "driver": {
    "devices": [],
    "aggregate": {}
  }
}
```

Notes:
- Single-device controllers return flat fields like `spi_speed_hz` and
  `frames_sent` instead of `devices`/`aggregate`.

### GET /api/hardware/stats
Returns the raw driver stats payload as reported by the controller. The shape
varies by controller type (single device vs multi-device).

Example response:
```json
{
  "devices": [],
  "aggregate": {}
}
```

## Related Code

- Controller status assembly: `animation/core/manager.py`
- Status file writing: `scripts/start_server.py`
- Web normalization and endpoints: `web/app.py`
