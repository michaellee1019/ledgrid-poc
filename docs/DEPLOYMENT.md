# Deployment and recovery

The Raspberry Pi 5 for the installed V6 wall uses
`ledgridwall5@ledgridwall5.local` by default, with
application code under `~/ledgrid-pod/app` and personal data under
`~/ledgrid-pod/data`. Override `PI_HOST` or `DEPLOY_DIR` for another
installation. Deployment may interrupt playback. Failures report the failing
command and may leave the service stopped; rerun deployment after correcting the
problem.

Deployment and remote tools require a dedicated SSH key. They use the repository's
`.gpt-key` by default and accept an explicit path through `SSH_KEY` or
`LEDGRID_SSH_KEY`. A missing or unreadable key stops the operation.

```sh
SSH_KEY="$PWD/.gpt-key" just deploy-python
```

Remote install steps use `sudo -n`. Run `just setup` once for a new Pi user
with sudo access to prepare runtime and firmware tooling. Both deployment modes
apply SPI device-tree settings when needed and may reboot the Pi before the
required `/dev/spidev*` nodes appear.

| Command | Action |
| --- | --- |
| `just setup` | Install Pi runtime/compiler tools including `dtc`, plus pinned PlatformIO 6.1.19 and tool-scons 4.40801.0 for ESP-IDF on Python 3.13 |
| `just deploy-plan` | Print the full deployment steps without contacting the wall |
| `just deploy` | Build browser assets, copy application, stop, migrate data, install dependencies, flash all five mapped receivers, start, check service/HTTP |
| `just deploy-python` | Same application and data path, without firmware flashing |
| `just deploy-no-firmware` | Alias for application-only deployment |
| `just test-deployment` | Focused deployment/data regressions and shell syntax |
| `just fetch-wall-data` | Retrieve current masks and custom presets for local review |

Deployment copies current source changes and required generated browser assets.
The deployer reads the wall’s current calibration, then builds browser assets
in a temporary source tree before stopping playback. A clean checkout can deploy without checked-in generated catalog files. Tracker data,
SSH keys, local runtime files, and personal presets are excluded from the upload.
Detailed command output is retained in ignored `.deploy-logs`.

## Personal data

Only `app/` is replaced. `data/` contains `run_state/` (Looks, playlists, operator
settings and receiver mapping), `presets/`, `config/` JSON (calibration and masks),
`calibration_photos/`, and `logs/`. The application links these locations rather
than embedding them in its replaceable directory. The runtime environment lives
at `~/ledgrid-pod/venv`, outside `app/`.

On the first deployment from the former release layout, existing writable root
paths and selected-release calibration are copied to a timestamped local backup
under `data-backups/` before migration. Selected-release calibration and writable root state take precedence;
files already present in `data/` are retained. An interrupted migration can be
rerun. Old root paths and releases are left available for manual inspection.
Keep `data/` and `data-backups/` when reinstalling or cleaning up old releases.

Operator settings retain current brightness, tempo, frame-rate setting, plant
modifiers, vibe and selected scene. Deployment captures the last published
controller settings after stopping the service. Existing service environment
overrides for cadence, tempo, SPI speed, host and port are retained. Brightness
zero is a valid value and is never replaced with a default because it is zero.
Unsupported saved components stay in personal data; startup reports the problem
and leaves playback stopped instead of substituting another animation.

## Nimbus gamepad

Pair and trust the original Nimbus on the Pi. When pairing with BlueZ 5.82,
use an interactive `bluetoothctl` agent and check that the controller reports
`Bonded: yes` before connecting its HID profile. Use `bluetoothctl info` for the
gamepad address.

Give the wall service account its own device path using
`/etc/udev/rules.d/99-ledgrid-nimbus.rules`. Replace the example address with
the paired Nimbus address in lowercase and use the installed service account.

```udev
SUBSYSTEM=="hidraw", KERNELS=="0005:0111:1420.*", IMPORT{parent}="HID_UNIQ"
SUBSYSTEM=="hidraw", KERNELS=="0005:0111:1420.*", ENV{HID_UNIQ}=="00:11:22:33:44:55", OWNER:="ledgridwall5", MODE:="0600", SYMLINK+="ledgrid-nimbus"
```

Reload the rules with `sudo udevadm control --reload` and reconnect the gamepad.
The wall process picks up `/dev/ledgrid-nimbus` when it appears. Start Tetris in
Composer and press Menu to enter or exit player mode. D-pad left/right move,
down soft drops, Up and A rotate in opposite directions, and B hard drops.
Each large piece carries three smaller tetrominoes. They fall separately
when the large piece lands or when you press Y.
The wall also serves `/play/tetris`. Opening it shows the current wall status.
The Play button starts player mode when the Nimbus is connected and the wall
still matches the status shown on the page.
The rail across the top shows the time left. The clock starts at 15 seconds,
each cleared row adds five seconds, and a round can last up to 40 seconds.
Pieces fall faster as the round progresses. The next round starts automatically
after a short pause.
Disconnecting returns the running Tetris scene to autoplay.

## Firmware

Full deployment builds `esp32-s3-devkitc-1` and uploads sequentially to the
five receivers listed in `data/run_state/receiver_identity_mapping.json`.
The file's `identities` list has one entry per receiver, with `logical_device`,
`hardware_serial` (the USB factory serial), and `spi_route` (the numeric bus
and chip-select pair). The [installed receiver table](HARDWARE.md#spi-routes-and-strip-order)
gives the required route for each logical ID. Verify the serial associated with
each physical receiver when provisioning a fresh installation. Full deployment
requires all five map entries and matching USB devices before the first upload.
Use `just deploy-python` for application-only changes.

A failed upload stops deployment. Already flashed boards may contain the new
firmware while other boards still contain the old version. Correct the cause
and rerun full deployment: all five mapped receivers are flashed again. There
is no image ledger, package installation, or automatic firmware rollback.

The installed service defaults to 33×138 and SPI mode 0 at 20 MHz on all five
routes, with CRC-16 protection. Readiness checks the systemd service and the
Composer telemetry endpoint. Use receiver connectivity and a visual wall
check to verify playback.

## Manual recovery

Inspect service and application logs with the same dedicated identity:

```sh
ssh -F /dev/null -i "$PWD/.gpt-key" -o IdentitiesOnly=yes -o IdentityAgent=none \
  -o BatchMode=yes -o ConnectTimeout=10 ledgridwall5@ledgridwall5.local \
  -- journalctl -u ledgrid.service -n 100 --no-pager
```

Rerunning `just deploy-python` replaces incomplete application code. Rerunning
`just deploy` also rebuilds and reflashes the mapped receivers. If needed, stop
the service and remove **only** `~/ledgrid-pod/app`, then deploy again. Keep
`~/ledgrid-pod/data` and its backups. The deployer never selects an old release
or restores prior playback automatically.

`tools/deployment/stop_remote.sh stop|restart|status` operates
`ledgrid.service`. `just diagnose-remote` collects the service journal,
application logs, and Composer telemetry. `just diagnose-remote-restart`
restarts the systemd service before collecting those diagnostics.

If an old root-owned diagnostic prevents backup, correct ownership of the
affected file or run-state directory with `sudo chown`, then rerun deployment.
If an interrupted old installation left `app/` directories read-only, use
`chmod -R u+w ~/ledgrid-pod/app` before retrying. Do not delete personal data to
bypass a migration failure.
