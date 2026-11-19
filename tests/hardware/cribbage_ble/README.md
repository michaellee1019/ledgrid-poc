# Mac RF bench

This opt-in fixture exercises actual Mac CoreBluetooth and ESP32 GATT traffic
against the production native session, Nimbus routing and renderer. It does not
run as part of pytest and does not add a production macOS BLE backend.

The fixture targets wire protocol 5. `P` selects the native runtime session over
USB, and live GATT reads must confirm that selection before admission.

`radio.cpp` temporarily makes the USB-identified four-screen board a player
peripheral. Serial `N` injects a gesture, which must arrive at the Mac as a GATT
notification. The board prints each complete downlink snapshot after checking
the actual public central address. The harness compares the entire reply with
the native wire bytes, including replies to duplicate and rejected requests.
It never retries a score notification. `S` performs real timed deep sleep;
`D` drops the physical BLE connection. No game rules are implemented in the
fixture. Screens and physical buttons are outside this fixture's acceptance.

After every hardware ownership or firmware change, obtain fresh full flash backups with esptool `read_flash`, retain its
successful transfer digest verification, hash each file, inspect the partition
table and OTA selection, and write a private manifest. Never stage backups,
manifests, logs or generated evidence. `lab.py` requires both inspected backups
to validate and checks the selected board's actual USB MAC before every write.
It overwrites only the active app0 slot for the test, then restores the entire
original flash image with mandatory esptool digest verification. Arduino/BLE
initialization can modify NVS, so app-only restoration is insufficient.

Manifest `boards` entries contain `name`, `port`, `base_mac`, `size`, `backup`,
`sha256`, `read_flash_digest_verified`, `app_offset`, `app_size` and `ota_entries`
(`sequence`, `state`, `crc`). The current bounded installer requires app0 at
0x10000 and the inspected two-slot OTA selection. It refuses changed backups,
wrong USB identity, oversized images and absent digest confirmation. Keep a
single restoration owner; do not nest wrappers.

Build the native bench profile with the observed public central address:

```sh
python tests/hardware/cribbage_ble/build_native.py \
  --output run_state/cribbage-native-bench --leader-mac ACTUAL_PUBLIC_MAC
SCOREBOT_TEST_LEADER_MAC=0xACTUAL_PUBLIC_MAC_WITHOUT_COLONS pio run \
  -d tests/hardware/cribbage_ble -e peripheral
```

`build_native.py` copies the pinned dependency to an ignored test build without
changing identity tables or policies. Native identities and pairing are runtime
data. The optional `--leader-mac` records bench filter metadata only; the radio
fixture's `SCOREBOT_TEST_LEADER_MAC` is an explicit safety filter for the actual
Mac central, separate from production pairing. Neither test identity nor a
patched core is deployed to the Pi.

Run the explicit test under the restoration owner:

```sh
python tests/hardware/cribbage_ble/lab.py --manifest PRIVATE_MANIFEST \
  --board four-screen --image tests/hardware/cribbage_ble/.pio/build/peripheral/firmware.bin -- \
  uv run --no-project --with-requirements requirements-pi.lock \
    --with bleak==3.1.0 --with pytest==9.1.1 \
    python tests/hardware/cribbage_ble/play_mac.py \
      --library run_state/cribbage-native-bench/native/build/libscorebot.dylib \
      --port ACTUAL_USB_PORT --address ACTUAL_PUBLIC_PLAYER_MAC --data FRESH_TEST_DIRECTORY
```

`probe_mac.py` is the shorter identity/read/full-snapshot probe with the same
arguments. Every full run requires a fresh data directory. Test evidence records
native/disk/wire agreement, physical requests, exact received snapshots and USB
diagnostics. Failure evidence is retained too. Pi Linux/BlueZ, matching production
player firmware, player displays/buttons, physical Nimbus and installed-wall
playback still require their own acceptance.

## Installed Pi with USB boards on the Mac

`play_pi.py` drives the installed Pi authority without creating another native
game. `pi_input_site.py` is a validation-only `sitecustomize.py`: it replaces HID
opening with an AF_UNIX datagram socket and retains the production 17-byte
decoder, edge handling and component routing. `pi_input_client.py` sends one
packet per press/release and reads the durable state independently. This proves
controller routing with simulated reports; physical Nimbus acceptance is separate.

Run the same installed controller/web entry point in one temporary systemd unit,
with the private shim directory first on `PYTHONPATH`, `LEDGRID_DATA_DIR` set to
the installed personal-data directory, and `CRIBBAGE_TEST_INPUT` naming its private
socket. Verify the `.shim-loaded` marker. Stop the ordinary service first; stop
the temporary unit before restoring the ordinary service. Do not create a second
authority or replace the real saved game with test state. These scenarios use
confirmed New Game, so use only an explicitly authorized test session.

With `SCOREBOT_BLE=0`, run `play_pi.py --phase local --evidence IGNORED_JSON`.
It completes a two-player match, advances the authority term through restart,
checks review/cancel/confirmed New Game and leaves a fresh lobby. Scene activation
uses the controller playback API; Composer's local publication acknowledgement
alone does not establish controller playback.

For real Pi-central traffic, build `peripheral_uart` for the CH343-connected
16 MiB ESP32-S3 using the observed Pi public address in
`SCOREBOT_TEST_LEADER_MAC`. Obtain a fresh full backup and inspected manifest
after any firmware ownership change. Run `play_pi.py --phase mixed --unit
TEMPORARY_UNIT --port ACTUAL_USB_PORT --address ACTUAL_PUBLIC_PLAYER_MAC
--packet OBSERVED_AUTHORITY_DISCOVERY_HEX --evidence IGNORED_JSON` **under
`lab.py`**, as above, with `--board epaper` and the `peripheral_uart` image.
Join the wrapped command lines when running. The fixture temporarily uses the
e-paper MCU as a player radio; it does not exercise its passive display firmware.

Mixed acceptance compares each received snapshot with the supplied Python
`State.to_wire()` representation of durable Pi state, including session and
controller receipts. It checks public initiator identity, malformed/spoofed
messages, duplicates, sleep/drop/takeover/rejoin, Scene handoff, durable restart,
an 18-second heartbeat interval, delayed old-game traffic and consumed overflow.
Rejoin waits accommodate the bounded ten-second transport backoff. Scores are
sent once; uncertain operations stop the run. `lab.py` restores and verifies the
entire original image on success, failure or timeout. Retain that confirmation
before claiming cleanup complete.

The `C` selection command uses the same radio fixture as a mirrored controller.
After a fresh full four-screen backup, run the `peripheral` image through
`lab.py --board four-screen` with `play_pi.py --phase controller` and explicit
four-screen USB/BLE addresses. It reconciles operations from durable controller
receipts and checks controller lobby/start/score/pass/correction, duplicates,
player-privilege rejection, reverse Nimbus replication and restart/term recovery.
`--phase finish` resumes an already completed 121–106 mixed match for heartbeat,
old-epoch and overflow checks without replaying the match. Both use the same
bounded restoration owner; neither proves physical button or screen behavior.
