# Rendering acceptance

Validate the current host RGB path at 33×138 pixels. Preserve calibrated physical
mapping, plant masks, global brightness, browser previews, and retained animation
semantics. Receiver-native and sparse-overlay acceptance programs are retired.

For renderer changes, exercise the production frame boundary and inspect changed
visuals. Measure performance when per-frame work or cadence changes. Report the
machine and geometry; desktop timings do not establish installed output rate.

`just test-rendering` covers the selectable Scene catalog and representative
stress scenes at 33×138. It checks a 4 ms render p95 budget at 200 simulated
host ticks per second. The benchmark also reports tail latency, changed frames
and distinct pixels. It exercises the host renderer without SPI.

For firmware/transport changes, build `esp32-s3-devkitc-1`, run native transport
and LED encoding tests, and independently review the host/receiver contract.
Retain payload and status CRC validation, bounded packets, lane order and dark boot.

After a reviewed deployment, check the affected phone/desktop workflow and wall
output. Preserve current brightness. Verify smooth playback and prepared switches
within five seconds. Record host cadence and receiver display-counter deltas on
all five routes. The 150 FPS output goal applies to continuously changing Scenes.

Source readiness, successful deployment, and installed acceptance are distinct.
