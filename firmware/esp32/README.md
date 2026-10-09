# Installed full-frame receiver

The ESP32-S3 firmware receives complete host-rendered RGB frames and drives
WS2812 lanes. It has no animation module loader, sparse overlay compositor,
installation-profile storage, or distributed scene transactions.

Build the installed target with `pio run -e esp32-s3-devkitc-1` and run portable
transport/encoder/command-queue checks with `pio test -e native` from this directory.

The installed wall is 33×138: logical receivers 0–3 have eight strips each at
offsets 0, 8, 16, 24; receiver 4 has one strip at offset 32. Host mapping applies
calibrated lane/strip orientation. Receiver 4's one strip broadcasts to the
configured output mask. Brightness initializes to zero and changes only on an
explicit host command.

SPI uses GPIO 11 MOSI, 13 MISO, 12 clock and 10 chip select. LED lanes use GPIOs
18,17,16,15,7,6,5,4. Complete frames pass through an owned command queue and latest
frame mailbox before the parallel LED driver. Queue errors and dropped or
superseded frames are observable counters.

Transport retains aligned envelope v1 and protected FEC v7, bounded to 4096 wire
bytes. Status retains the LGS8 layout and trailing CRC32 so host transport code
can retain the proven parser offsets; retired scene/profile/module fields are
zero and their capability bits are absent. Base counters occupy bytes 5–63,
capabilities 64–67, global strip offset 80–83, receiver id 312, last command 313,
stagger 314, operation sequence 316–319, FEC counters 1216–1247 and CRC32 1248–1251.
Status reports transport/output observations, not exact scene-display identity.
SPI responses are queued before the command they accompany; callers must account
for queue depth rather than treating the immediately returned bytes as an ACK.

CONFIG is exactly eight semantic bytes: command 7, strip count, big-endian LED
count, flags (only bit 0 debug is accepted), logical receiver id, big-endian global strip offset. Only the
installed geometry is accepted. SET_ALL is command 6 followed by the receiver's
complete RGB frame. Brightness, clear, show, lane mask, stagger and ping remain.
Old module/profile/overlay commands are rejected.

Deployment flashes the five explicitly USB-mapped receivers in a stopped
maintenance window. Interrupted flashing is recovered by correcting the fault
and reflashing; no rollback or installed-image ledger is required.
