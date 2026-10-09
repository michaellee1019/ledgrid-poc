# Deployment and recovery

The installed wall uses `ledgridwall@ledgridwall.local`, with application code
under `~/ledgrid-pod/app` and personal data under `~/ledgrid-pod/data`.
Deployment may interrupt playback. Failures report the failing command and may
leave the service stopped; rerun deployment after correcting the problem.

Use the dedicated SSH key:

```sh
SSH_KEY=/Users/rtimmons/Projects/ledgrid-poc/.gpt-key just deploy-python
```

The key must exist and be readable. Operations use `IdentitiesOnly=yes`,
`BatchMode=yes`, and non-interactive `sudo -n`; they never fall back to another
identity or a password. `PI_HOST` and `DEPLOY_DIR` override the target and its
named directory below the target user's home.

| Command | Action |
| --- | --- |
| `just setup` | Install Pi runtime/compiler tools and pinned PlatformIO; needs existing sudo access |
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

## Firmware

Full deployment builds `esp32-s3-devkitc-1` and uploads sequentially to the
five receivers listed in `data/run_state/receiver_identity_mapping.json`.
Each logical receiver has an explicit USB factory serial and SPI route; USB
port enumeration order is not a physical mapping. Missing or duplicate serials,
incorrect routes, or missing devices fail before flashing.

A failed upload stops deployment. Already flashed boards may contain the new
firmware while other boards still contain the old version. Correct the cause
and rerun full deployment: all five mapped receivers are flashed again. There
is no image ledger, package installation, or automatic firmware rollback.

The service retains receiver 3's FEC setting and installed geometry. Service and
HTTP readiness do not assert that all receivers displayed a specific frame;
use the application's connectivity status and a visual wall check for playback.

## Manual recovery

Inspect service and application logs with the same dedicated identity:

```sh
ssh -i "$SSH_KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 \
  ledgridwall@ledgridwall.local -- journalctl -u ledgrid.service -n 100 --no-pager
```

Rerunning `just deploy-python` replaces incomplete application code. Rerunning
`just deploy` also rebuilds and reflashes the mapped receivers. If needed, stop
the service and remove **only** `/home/ledgridwall/ledgrid-pod/app`, then deploy
again. Keep `/home/ledgridwall/ledgrid-pod/data` and its backups. The deployer
never selects an old release or restores prior playback automatically.

`tools/deployment/stop_remote.sh stop|restart|status` provides the corresponding
service operation using the same SSH policy. If system provisioning, USB access,
or SPI configuration needs repair, fix that specific failure before retrying.

If an old root-owned diagnostic prevents backup, correct ownership of the
affected file or run-state directory with `sudo chown`, then rerun deployment.
If an interrupted old installation left `app/` directories read-only, use
`chmod -R u+w ~/ledgrid-pod/app` before retrying. Do not delete personal data to
bypass a migration failure.
