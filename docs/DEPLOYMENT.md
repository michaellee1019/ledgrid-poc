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
The wall process picks up `/dev/ledgrid-nimbus` when it appears. Press Menu to
open or close the wall Composer. Use Up and Down to browse cards, A to select,
and B to go back. Left and Right edit values, move one card in short menus, and
jump eight cards in longer lists. Hold Left or Right for larger numeric steps.
Browse keeps your place when you play a Look, starter, or preset. On the playing
card, Y opens Tune. Menu closes the menu for a full view.

The top tiles show L1, L2, R1, and R2. Each held button lights up in the bank
color. Open **Menu → Presets → Save** or **Load** for the 20-slot board.
The D-pad moves across four columns and five rows. Filled tiles have a brighter
background. Press A to save or load the selected slot, or B to go back. Saving
immediately replaces the selected slot. Each slot stores the complete Scene,
including widgets and plant effects. The Pi keeps them in
`data/run_state/numbered_presets/`.

Tune applies changes immediately. **Tune → Animation** puts the current
animation's main settings first, with seed and frame-rate settings at the end.
Use Left and Right on a choice, or press A to open a long list such as GIF
clips. Y undoes the last edit or held adjustment.
Press Y again to redo it. In the character picker, A inserts the focused
character, Y finishes, and B cancels. Save Look adds a named Scene to the
Composer library.

With the HUD closed, D-pad Up and Down cycle through the animation gallery.
The new animation name appears at the top for one second. Left and Right
change the palette. When the current animation has a player mode, the wall
shows **A PLAY**.

Hold a combination of L1, R1, L2, and R2 to open the temporary tuning HUD.
Four cards show left-stick vertical, left-stick horizontal, right-stick
vertical, then right-stick horizontal. The bank color fills each top stripe
and the edge on the stick's side. Idle arrows alternate between the two
directions of each axis. A detected move brightens and holds its arrow,
and the scrolling setting name brightens briefly when its value changes. The cards draw
over the plant mask. Release all shoulder buttons to close the temporary HUD.
If you opened Composer with Menu, release returns its tuning cards to the
base bank.

Move a stick to adjust its setting. Use D-pad Up and Down to select a card,
Left and Right to adjust its value, Y to undo, and B to go back. Right and up
increase a value. Left and down decrease it. You can move diagonally to
adjust both axes at once, and hold a direction to keep adjusting. Return a
held axis to center after changing banks. Palette changes once per flick.

| Hold | Left horizontal | Left vertical | Right horizontal | Right vertical |
| --- | --- | --- | --- | --- |
| None | Palette | | Animation 1 | Scene light |
| L1 | Background light | | | |
| R1 | Animation 5 | Animation 2 | Animation 3 | Animation 4 |
| L1 + R1 | Animation 6 | Animation 7 | Animation 8 | Animation 9 |
| L2 | Widget across | Widget height | Widget 1 | Widget 2 |
| L1 + L2 | Widget 3 | Widget 4 | Widget 5 | Widget 6 |
| R1 + L2 | Animation 10 | Animation 11 | Animation 12 | Animation 13 |
| L1 + R1 + L2 | Animation 14 | Animation 15 | Animation 16 | Animation 17 |
| R2 | Illuminate | Shadow | Hue shift | |
| L1 + R2 | Refraction | | | |
| R1 + R2 | | | | Speed |
| L1 + R1 + R2 | Animation 18 | Animation 19 | Animation 20 | Animation 21 |
| L2 + R2 | Widget 7 | Widget 8 | Widget 9 | Widget 10 |
| L1 + L2 + R2 | Widget 11 | Widget 12 | Widget 13 | Widget 14 |
| R1 + L2 + R2 | Animation 22 | Animation 23 | Animation 24 | Animation 25 |
| All four | Animation 26 | Animation 27 | Animation 28 | Animation 29 |

The base bank owns the main animation setting. R1 has secondary animation
controls, L1 has backlight, and L2 has the first Widget. R1 + R2 puts overall
speed on right-stick Up and Down. Tune → Speed accepts 0–10× in 0.05×
steps. On the Speed row, A freezes scene motion and A again sets 1×.
Composer offers the same exact range and shorter slider ranges where fast
motion skips visible updates. Each setting belongs to one bank. Empty
slots and settings absent from the current scene show dim dashes.
Fireworks uses burst size in the base bank and trails, launch rate, sparks per
burst, and gravity with R1. Changes to a shell's settings apply at launch.
Campfire uses heat in the base bank and sparks, smoke, and flicker with R1.
Scene light is independent of master brightness.

With Flame Burst, Fluid Tank, or Lava Lamp playing and the HUD closed, hold
L1 + R1 to show an aim marker. Move it with the left stick and press A to
ignite, splash, or stir. Fluid Tank aims across the wall using the horizontal
axis. Release the shoulders to return to Scene controls.

The same chord injects a column in Cellular Tapestry, plants a crystal in
Frostwork, seeds Reaction Diffusion or Cyclic Reef, bends Wind in the Reeds,
and toggles a cell in Conway Life. Conway’s local generation rate can be set to
zero while you edit its current grid. These gestures leave the saved seed
settings intact. Cellular Tapestry caps at ten rows per scaled second. For
Reaction Diffusion, set Auto reseed interval to zero to seed by hand. Motion 1.0
and Growth 1.4 retain visible chemistry at 33×138. Combining both legal maxima
can clear the field. At a local pace of
1×, Cyclic Reef evolves at 12 generations per Scene second.

Aim and press A for an Ocean Waves ripple, a Rain on Glass drop, a Waterfall
Veil splash, or a Tidal Bioluminescence wake. Ocean Waves Travel can be set
to zero for still surf. In Plant Glow, aim within four pixels of a calibrated
globe or foliage patch to pulse that target. The HUD reports when the
calibration or an aimable target is missing.

The same aim chord adds an accent to Aurora Curtains, Cloud Canyon, Desert
Wind, Moonlit Fog Banks, and Night Train Windows. The train's Travel speed
can be set to zero. In Circadian Window, aim across the wall to preview a time
of day for three Scene seconds. Aim at the rightmost column and press A to
return immediately to the selected clock or fixed hour. The clock offset
appears in clock mode, and the day rate appears in fixed-hour mode.

In Gradient, Rainbow, Wave, Sparkle, Quasicrystal Bloom, and Living Stained
Glass, aim and press A to add a temporary color, phase, or light accent.
Rainbow and Wave offer Still alongside Forward and Reverse. Set Sparkle
Density to zero to keep only manually triggered clusters. Solid has a dark
setting at Glow zero and uses Tune for its breathing rate.

Use the same gesture to feed nearby fish, extend one train stop at the
nearest Mini Metro station, or nudge nearby fireflies. Flow-Field Silk bends
threads through a short-lived vortex. Living Ecosystem and Physarum accept
one temporary resource point at a time. Physarum rejects points in calibrated
globe cores. Controller status reports the effective herd and agent counts.

Playback starts saved playlists and controls output brightness, target FPS, and
power. A manual edit takes over from a running playlist. With the Pi powered,
Menu resumes the selected Scene if output is off. If brightness is zero, Menu
raises it to 16/255.

Press A with the HUD closed to take control of the current game. Press Menu
to pause and open Composer. Close the HUD with Menu or B: the game stays
paused and shows **PAUSED / A RESUME**. Up and Down browse as usual; choosing
another animation leaves the round. Press A to continue the same round.
Edits to game setup, such as a new seed or course layout, can rebuild the board
while keeping it paused. Disconnect returns the current game to autoplay.

| Game | Controls while playing |
| --- | --- |
| Tetris | Left/Right move, Down soft drops, Up/A rotate, B hard drops, Y releases the inner pieces |
| Snake | D-pad steers the snake with the white head |
| Maze Chase | D-pad steers the hero; a turn is buffered until the next open tile |
| Pinball | A fires the flipper as the ball reaches the bottom, Left/Right nudge |
| Pixel Quest | A attacks, B spends magic during combat |
| Canopy Cup | Left/Right steer Fern, A jumps, B uses a charged ability |

You can also start a Tetris sprint under Playback or at `/play/tetris`. The
web Play button requires a connected Nimbus and a matching controller
revision. The countdown starts at 15 seconds. Each cleared row adds five
seconds, up to 40 seconds total.

To review the menu without the wall, run
`.venv/bin/python scripts/simulate_wall_composer.py` and open
`http://127.0.0.1:8765`. The simulator uses the checked-in plant coverage,
production Scene renderer, and temporary Looks, playlists, and numbered slots.

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
