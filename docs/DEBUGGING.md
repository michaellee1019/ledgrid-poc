# Debugging and Diagnostics

This guide covers the fastest ways to inspect system health without changing
running code.

## Quick Checks

1. Verify controller status:
   - `run_state/status.json` should update at the configured interval.
   - Check `is_running`, `current_animation`, and `actual_fps`.

2. Fetch current controller and receiver evidence:
   - `GET /api/v1/composer/operations/telemetry`
   - `GET /api/v1/scene`
   - `GET /api/v1/composer/settings/observed`
   - `GET /api/v1/scene/activations/<activation_id>` for the exact failed request.

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
- `led_controller_spi_test.py`: SPI smoke test.
- `fluid_tank_simulation.py`: Offline sim helper.

## Where Metrics Are Produced

- Controller metrics: `animation/core/manager.py`
- Status file writer: `scripts/start_server.py`
- API normalization: `web/app.py`

## Remote Diagnostics (Deploy Host)

Preferred entry points:
- `just diagnose-remote`
- `just diagnose-remote-restart` (also clears port 5000 and restarts web)

Output is written to `diagnostics/remote_diagnostics.out`.

## Native activation failures

Capture the failing request before another activation or restoration replaces
the latest status. This observer only performs HTTP GET requests, creates a new
file, and flushes each endpoint result immediately. Unavailable endpoints are
recorded as errors; capture failure is not discarded or reported as success.

```bash
.venv/bin/python tools/diagnostics/capture_native_activation.py \
  --base-url http://ledgridwall.local:5000 \
  --activation-id THE_REQUEST_ACTIVATION_ID \
  --samples 5 --interval 1 \
  --output run_state/qualification/native-failure-UNIQUE_TIMESTAMP.jsonl
```

The raw telemetry preserves controller release/session identity, receiver
identity and status version, native operation/error/digests, profile proof, frame
counts, and transport counters when exposed by that firmware. Missing/null
fields are unavailable evidence, not zero errors. Samples are sequential HTTP
observations, not an atomic wall snapshot. A successful capture proves neither
activation nor visual output. Compare the exact activation receipt, requested
and observed Scene identities, all five receivers, and counter advancement.
Counter decreases or identity/session changes invalidate a rate interval.

The native loader emits activation-time `native-loader` ESP log messages with
the failed stage and underlying return code where available. They distinguish
cache-root/path validation, file open, allocation, initialization, relocation,
entrypoint lookup, and ABI validation. They do not add per-frame logging or
change wire status. These receiver serial messages are not automatically
retained by the controller's systemd journal; preserve an authorized capture
separately. Opening USB serial can reset a receiver even when a reader requests
inactive DTR/RTS. A `USB_UART_CHIP_RESET` observed during connection does not
prove a native-module crash. Verify the MAC-to-port mapping from the current
flash receipt rather than assuming `/dev/ttyACM0` is logical receiver zero.

Use the dedicated key for any authorized remote observation:

```bash
ssh -i /Users/rtimmons/Projects/ledgrid-poc/.gpt-key \
  -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 \
  ledgridwall@ledgridwall.local -- \
  'journalctl -u ledgrid.service --since "10 minutes ago" --no-pager'
```

Keep the deployment receipt alongside diagnostic captures: code commit,
firmware digest, installation digest, receiver mapping, strict-health result,
and restore result are separate facts. An application rollback does not imply
that flashed receiver firmware was reverted. Do not retry firmware flashes to
mask a failed health gate.

### Established native-loader boundaries

The cache owns `/profilecache/n<64 lowercase SHA-256 hex>.bin`. Its full digest
must remain intact. The pinned ESP loader's `dlopen` registry rejects that
65-character stem, and `esp_elf_open` prepends its configured filesystem root.
The backend therefore validates the absolute cache path, passes the complete
relative filename to the public ELF API, owns that ELF object, and resolves the
entrypoint from its own symbol table. Global import lookup is not module export
lookup. Avoid filename truncation, cache aliases, or dependency patches when
diagnosing this boundary.

Native command processing also requires the native SDK main-task stack budget.
The old effective 4,096-byte allocation was smaller than a verified 4,720-byte
nested command/parameter/SHA call chain. The native default is now 16,384 bytes
plus the SDK's task overhead, guarded against stale effective configuration.
That establishes a concrete stack defect; it does not establish the cause of
every historical receiver reset.

For current status, known remaining defects, accepted checks, and the next
work boundary, use `bd prime` and `bd show ledgrid-poc-1ni`. Beads is the
handoff authority; historical captures and this troubleshooting explanation
must not be interpreted as unfinished claims or a request to repeat old work.
