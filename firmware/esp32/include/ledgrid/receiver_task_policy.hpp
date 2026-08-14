#pragma once

namespace ledgrid {

// ESP-IDF's app_main task owns the SPI slave dequeue loop on CPU0. Receiver
// rendering must remain on the other ESP32-S3 core so continuous local playback
// cannot starve control packets, acknowledgements, or host takeover.
constexpr int kReceiverSpiTaskCore = 0;
constexpr int kReceiverDisplayTaskCore = 1;
constexpr unsigned kReceiverDisplayTaskPriority = 3;

static_assert(kReceiverSpiTaskCore != kReceiverDisplayTaskCore,
              "receiver SPI and display tasks require separate cores");

}  // namespace ledgrid
