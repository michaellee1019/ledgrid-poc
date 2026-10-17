# Native wall migration

This is a coordinated, manually recoverable update. First complete and review
the host API, native controls, content, advanced actions and diagnostics. Do not
run this procedure with a partly implemented candidate. The native integration
and its setup/reload/reconnect are read-only until an explicit control is used.

## Preserve the installation

Capture fresh controller session/revision, current requested and observed Scene,
playlist, brightness (including zero), operator pace, target FPS and errors.
Back up all personal data: calibration, Looks, playlists, custom presets,
settings and the automation installation UUID. Use the existing host deployment
workflow and dedicated wall SSH key. Retain exact source revisions and private
backup paths in the local Bead.

In the smarthome repository, preserve its dirty work and use the dedicated
Home Assistant SSH identity. Back up the current wall package, configuration and
relevant registry metadata. Keep `switch.light_living_ledwall`, its protections,
all-off membership, generated fast-scene scripts and pace-demo automation as-is.
The wall fast scene must retain single-dispatch/no-convergence behavior.

## Install and assign public IDs

1. Deploy the reviewed matching host candidate and verify read-only API readiness.
2. Capture the SHA256 of the live `/config/packages/led_grid_wall.yaml`. Use
   smarthome's bounded migration form:
   `just ha-install-led-grid-wall /absolute/path/to/custom_components/led_grid_wall /absolute/path/to/led_grid_wall_native.yaml captured-package-sha256`.
   The reviewed native package contains only the public compatibility script.
   The installer stops Core, backs up the old component, package, configuration
   and relevant registries, and replaces only the component and wall package.
   It checks the original package hash before stopping and again before replacement;
   a changed package stops migration rather than overwriting new changes.
3. Verify Core readiness after the installer checks configuration and starts it.
   The old template light/number/sensor and REST commands are now retired; the
   public apply script remains. Other packages and live UI automations remain
   intact. A failed installation leaves manual recovery information and does not
   restore playback or apply automatic rollback. Ordinary component updates omit
   the package/hash arguments and preserve all YAML.
4. Add **LED Grid Wall** through HA's integration setup with the controller URL.
   Verify one device and the installation UUID from the host capabilities. Setup
   must issue no playback command. Existing registry records reserve the three
   public IDs, so the native entities may initially receive suffixes.
5. Use the smarthome registry recipe to create a private plan. `source` below is
   the absolute `integrations/home_assistant` directory from this reviewed source.
   `plan` is a new absolute JSON path inside the private deployment backup:

   ```sh
   just ha-led-grid-registry plan source plan installation-uuid
   just ha-led-grid-registry apply source plan
   ```

   The recipe obtains credentials through smarthome's dedicated SSH/API client
   and passes them on standard input, never in command arguments or a saved plan.
   Authentication failure stops the workflow. No `.storage` file is edited.
   The plan records exact registry identities and user metadata; it must not be
   committed. It rejects missing/duplicate records, occupied archival IDs and
   registry-owned visibility settings that the user API cannot reproduce.

The migration archives the three legacy registry entries under
`*.led_grid_wall_legacy_*` and disables them, then assigns their public IDs and
user metadata to the native entries. It preserves names, icons, area, labels,
aliases, categories, visibility and entity options. It does not invoke controls.
The native entities keep their own device/config-entry identity. Verify exactly
`light.led_grid_wall`, `number.led_grid_wall_pace`,
`sensor.led_grid_wall_observation`, and `script.led_grid_wall_apply` afterward.

## Interrupted migration and recovery

Each update checks the saved record and confirms the resulting metadata. A
failure stops immediately with any completed steps retained. Re-running **apply**
with the same plan skips confirmed steps and resumes; it never guesses a new
plan over partially migrated data. A changed record or ID collision stops with
an explicit conflict. Inspect and resolve that conflict before proceeding.

For a deliberate return to the previous package, use
`just ha-led-grid-registry restore source plan` to reverse the exact planned
registry changes. It also refuses to overwrite subsequent manual metadata edits.
Then remove the native integration through HA, restore the backed-up package
through the supported configuration workflow, validate and restart HA. A failed
component installation can be recovered by restoring its backed-up directory
before starting Core. Keep backups until acceptance; no automatic rollback is
provided and no playback restoration is assumed.

## Acceptance

Run `check_registry_migration.py` with the isolated HA 2026.10.1 runtime. It uses
real registry API handlers to cover metadata, collisions, interruption, resume,
restore and manual-change conflicts, plus the native compatibility script's
success/error and zero-brightness behavior.

After all code is independently reviewed, collect installed evidence for device
and entity IDs, representative controls/content/advanced actions/interactions,
playlist takeover, diagnostics/image, interruption and HA restart without writes.
Record controller completion separately from physical observation. Check prepared
switches against the five-second target and observe polling for visible stutter.
Restore the captured settings only after checking for intervening human changes;
report conflicts instead of overwriting newer intent. Keep operational evidence
private and close the migration Bead only after its acceptance is complete.
