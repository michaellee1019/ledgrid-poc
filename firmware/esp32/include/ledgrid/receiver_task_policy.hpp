#pragma once

#if defined(ESP_PLATFORM) && LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
#include "sdkconfig.h"
#endif

namespace ledgrid {

// ESP-IDF's app_main task owns the SPI slave dequeue loop on CPU0. Receiver
// rendering must remain on the other ESP32-S3 core so continuous local playback
// cannot starve control packets, acknowledgements, or host takeover.
constexpr int kReceiverSpiTaskCore = 0;
constexpr int kReceiverDisplayTaskCore = 1;
constexpr unsigned kReceiverDisplayTaskPriority = 3;

#if defined(ESP_PLATFORM) && LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
// Enforce the resolved SDK configuration too: an old generated sdkconfig must
// not silently retain the undersized default after native defaults change.
static_assert(CONFIG_ESP_MAIN_TASK_STACK_SIZE >= 16384,
              "native SPI command execution requires a 16 KiB main task stack");
#endif

static_assert(kReceiverSpiTaskCore != kReceiverDisplayTaskCore,
              "receiver SPI and display tasks require separate cores");

}  // namespace ledgrid
