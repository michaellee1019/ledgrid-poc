# Debugging and diagnostics

Start with `just diagnose-remote`, service logs, and `data/run_state/status.json`.
The status file should keep advancing. Composer reports requested Scene,
controller playback, receiver connectivity, and errors separately. Status older
than five seconds is unavailable; playback is not exact display confirmation.

Use `/api/v1/composer/operations/telemetry`, `/api/v1/composer/operations/telemetry`, and
`/api/v1/composer/settings/observed?request_id=<UUID>` for current status and the
result of one specific command. Do not infer a failed command's result from a
later unrelated command. Manual selection supersedes the playlist.

For low output rate, inspect controller `actual_fps`, frame timing, and receiver
transport counters. `just output-rate-observation 10` reports controller cadence and receiver
display-counter rates separately; neither proves physical smoothness. Keep 150 FPS as a goal and inspect visible
stutter separately. Receiver 3's known CRC/FEC degradation is accepted and must
still be reported truthfully.

For missing LEDs, verify the five routes and widths in
[hardware documentation](HARDWARE.md), then inspect CRC/FEC and connectivity.
Use physical color/direction patterns only during an authorized maintenance
window. Opening receiver serial ports may reset them.

`just diagnose-remote-restart` explicitly restarts the service. Failed operations
may leave playback stopped. Rerun deployment for a broken application or partial
firmware flash; see [recovery](DEPLOYMENT.md). Personal data lives outside `app/`.

All remote commands use the dedicated `.gpt-key` with `IdentitiesOnly=yes` and
`BatchMode=yes`; never fall back to the human SSH agent or another identity.
