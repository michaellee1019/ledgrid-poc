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
