# Metrics and observability

The controller publishes periodic observations to `data/run_state/status.json`.
Use `GET /api/v1/composer/operations/telemetry` for deployment and performance
diagnostics, `GET /api/v1/composer/operations/status` for playback, and
`GET /api/v1/composer/settings/observed?request_id=<UUID>` for one specific
command's result.

The telemetry response groups the useful fields under `controller` and
`diagnostics`:

- `controller.actual_fps` and `controller.target_fps` describe host tick
  cadence. A renderer can return an unchanged frame between source updates.
- `diagnostics.performance` contains host render and send timings.
- `diagnostics.driver_stats.devices` has one entry per logical receiver,
  including its `spi_route`, `spi_speed_hz`, and connectivity.
- A fresh CRC-protected reply supplies receiver accepted/displayed frames,
  superseded frames, CRC errors, queue errors, publish drops, and display
  errors. Host-side SPI errors and rejected status replies are counted
  separately.
- `diagnostics.driver_stats.aggregate` summarizes wall transfers and errors.

Use `just output-rate-observation 15` to compare host cadence and receiver
display counters. Use `--min-fps 150` on
[`output_rate_sweep.py`](../tools/benchmarks/output_rate_sweep.py) when the
current Scene should drive continuously changing frames. The check requires
fresh samples and no new errors on all five receivers. For a static or
source-rate-limited Scene, read the measured cadence in the context of its
authored update rate.

Receiver counters reach the ESP32 output peripheral. Observe the LEDs to
verify physical mapping, smoothness, flicker, and display content.
