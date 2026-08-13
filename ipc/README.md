# IPC Layer

Purpose: file-based communication between web and controller processes.

Key files:
- control_channel.py: FileControlChannel implementation
- scene_contract.py: strict JSON validation for the fixed Phase 2C scene editor,
  scene presets, preview identities, and desired-display persistence envelopes

Files:
- run_state/control.json: Command input (web -> controller)
- run_state/status.json: Status output (controller -> web)

Phase 2C commands are versioned and include `start_scene`, `stop_scene`,
`update_scene_component`, and `restore_display_state`. Live scenes remain
Python-only with one background and the fixed `clock_overlay` slot. The legacy
single-animation commands remain supported through compatibility translation.
