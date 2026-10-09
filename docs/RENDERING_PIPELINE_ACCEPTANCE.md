# Rendering acceptance

Validate the current host RGB path at 33×138 pixels. Preserve calibrated physical
mapping, plant masks, global brightness, browser previews, and retained animation
semantics. Receiver-native and sparse-overlay acceptance programs are retired.

For renderer changes, exercise the production frame boundary and inspect changed
visuals. Measure performance when per-frame work or cadence changes. Report the
machine and geometry; desktop timings do not establish installed output rate.

For firmware/transport changes, build `esp32-s3-devkitc-1`, run native transport
and LED encoding tests, and independently review the host/receiver contract.
Retain CRC/FEC corruption handling, bounded packets, lane order and dark boot.

After a reviewed deployment, check the affected phone/desktop workflow and wall
output. Preserve current brightness. Verify smooth playback and prepared switches
within five seconds. Record actual frame rate; 150 FPS remains a goal. Accepted
receiver-3 degradation alone does not justify rollback or physical investigation.

Source readiness, successful deployment, and installed acceptance are distinct.
