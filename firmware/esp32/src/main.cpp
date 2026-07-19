#include <Arduino.h>

#include <algorithm>
#include <atomic>
#include <cstring>

#include "driver/gpio.h"
#include "driver/spi_common.h"
#include "driver/spi_slave.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "ledgrid/frame_mailbox.hpp"
#include "ledgrid/parallel_led_driver.hpp"
#include "ledgrid/protocol.hpp"
#include "ledgrid/ws2812_encoder.hpp"

namespace {

constexpr gpio_num_t kSpiMosi = GPIO_NUM_11;
constexpr gpio_num_t kSpiMiso = GPIO_NUM_13;
constexpr gpio_num_t kSpiClock = GPIO_NUM_12;
constexpr gpio_num_t kSpiChipSelect = GPIO_NUM_10;
constexpr std::uint8_t kStatusLed = 48;

constexpr std::uint8_t kMaxStrips = 8;
// The installed wall is eight fixed 140-pixel lanes. Keeping the transport,
// mailbox, and LCD DMA allocations sized to the physical receiver avoids
// spending internal SRAM and memory bandwidth on an unsupported geometry.
constexpr std::uint16_t kMaxLedsPerStrip = 140;
constexpr std::size_t kMaxTotalLeds = kMaxStrips * kMaxLedsPerStrip;
constexpr std::size_t kMaxRgbBytes = kMaxTotalLeds * 3;
constexpr std::uint8_t kDefaultStrips = 8;
constexpr std::uint16_t kDefaultLedsPerStrip = 140;
constexpr int kLedPins[kMaxStrips] = {18, 17, 16, 15, 7, 6, 5, 4};

constexpr std::uint8_t kCmdSetPixel = 0x01;
constexpr std::uint8_t kCmdSetBrightness = 0x02;
constexpr std::uint8_t kCmdShow = 0x03;
constexpr std::uint8_t kCmdClear = 0x04;
constexpr std::uint8_t kCmdSetRange = 0x05;
constexpr std::uint8_t kCmdSetAll = 0x06;
constexpr std::uint8_t kCmdConfig = 0x07;
constexpr std::uint8_t kCmdPing = 0xFF;

constexpr std::size_t kCrcBytes = 2;
constexpr std::size_t kSpiFrameBytes = 1 + kMaxRgbBytes + kCrcBytes;
constexpr std::size_t kSpiBufferSize =
    ((kSpiFrameBytes + 63U) / 64U) * 64U;
constexpr std::size_t kSpiQueueDepth = 2;

DMA_ATTR std::uint8_t spi_rx_buffers[kSpiQueueDepth][kSpiBufferSize] = {};
DMA_ATTR std::uint8_t spi_tx_buffers[kSpiQueueDepth][kSpiBufferSize] = {};
spi_slave_transaction_t spi_transactions[kSpiQueueDepth] = {};

std::uint8_t working_frame[kMaxRgbBytes] = {};
std::uint8_t mailbox_frames[ledgrid::kFrameMailboxSlots][kMaxRgbBytes] = {};
ledgrid::LatestFrameMailbox frame_mailbox;
portMUX_TYPE mailbox_mux = portMUX_INITIALIZER_UNLOCKED;
TaskHandle_t display_task_handle = nullptr;
ledgrid::ParallelLedDriver led_driver;

std::uint8_t active_strips = kDefaultStrips;
std::uint16_t leds_per_strip = kDefaultLedsPerStrip;
std::uint8_t brightness = 50;
std::uint32_t next_sequence = 1;

std::atomic<std::uint32_t> packets_received{0};
std::atomic<std::uint32_t> crc_errors{0};
std::atomic<std::uint32_t> crc_ok_packets{0};
std::atomic<std::uint32_t> spi_queue_errors{0};
std::atomic<std::uint32_t> display_errors{0};
std::atomic<std::uint16_t> queued_transactions{0};
std::atomic<std::uint16_t> last_crc_us{0};
std::atomic<std::uint16_t> last_copy_us{0};
std::atomic<std::uint32_t> last_accepted_sequence{0};
std::atomic<std::uint32_t> last_displayed_sequence{0};

constexpr std::uint16_t kCrc16NibbleTable[16] = {
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
};

std::uint16_t duration_u16(std::uint32_t value) {
  return value > UINT16_MAX ? UINT16_MAX : static_cast<std::uint16_t>(value);
}

std::size_t total_leds() {
  return static_cast<std::size_t>(active_strips) * leds_per_strip;
}

std::size_t active_rgb_bytes() { return total_leds() * 3U; }

std::uint16_t crc16_ccitt(const std::uint8_t* data, std::size_t length) {
  std::uint16_t crc = 0xFFFF;
  for (std::size_t i = 0; i < length; ++i) {
    crc ^= static_cast<std::uint16_t>(data[i]) << 8;
    crc = static_cast<std::uint16_t>(
        (crc << 4) ^ kCrc16NibbleTable[crc >> 12]);
    crc = static_cast<std::uint16_t>(
        (crc << 4) ^ kCrc16NibbleTable[crc >> 12]);
  }
  return crc;
}

ledgrid::FrameMailboxCounters mailbox_counters() {
  portENTER_CRITICAL(&mailbox_mux);
  const auto counters = frame_mailbox.counters();
  portEXIT_CRITICAL(&mailbox_mux);
  return counters;
}

bool publish_working_frame() {
  int slot = -1;
  portENTER_CRITICAL(&mailbox_mux);
  slot = frame_mailbox.begin_write();
  portEXIT_CRITICAL(&mailbox_mux);
  if (slot < 0) return false;

  const std::size_t bytes = active_rgb_bytes();
  const std::uint32_t copy_started =
      static_cast<std::uint32_t>(esp_timer_get_time());
  std::memcpy(mailbox_frames[slot], working_frame, bytes);
  last_copy_us = duration_u16(
      static_cast<std::uint32_t>(esp_timer_get_time()) - copy_started);

  ledgrid::FrameMetadata metadata{};
  metadata.sequence = next_sequence++;
  metadata.byte_count = bytes;
  metadata.strip_count = active_strips;
  metadata.leds_per_strip = leds_per_strip;
  metadata.brightness = brightness;

  portENTER_CRITICAL(&mailbox_mux);
  const bool committed = frame_mailbox.commit_write(slot, metadata);
  portEXIT_CRITICAL(&mailbox_mux);
  if (!committed) return false;

  last_accepted_sequence = metadata.sequence;
  if (display_task_handle != nullptr) xTaskNotifyGive(display_task_handle);
  return true;
}

void display_task(void*) {
  while (true) {
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
    while (true) {
      ledgrid::FrameMetadata metadata{};
      int slot = -1;
      portENTER_CRITICAL(&mailbox_mux);
      slot = frame_mailbox.begin_read(&metadata);
      portEXIT_CRITICAL(&mailbox_mux);
      if (slot < 0) break;

      const bool submitted = led_driver.submit(
          mailbox_frames[slot],
          metadata.byte_count,
          metadata.strip_count,
          metadata.leds_per_strip,
          metadata.brightness,
          metadata.sequence);
      const bool completed =
          submitted && led_driver.wait_for_done(pdMS_TO_TICKS(100));

      portENTER_CRITICAL(&mailbox_mux);
      if (completed) {
        frame_mailbox.finish_read(slot);
      } else {
        frame_mailbox.cancel_read(slot);
      }
      portEXIT_CRITICAL(&mailbox_mux);

      if (completed) {
        last_displayed_sequence = metadata.sequence;
      } else {
        ++display_errors;
      }
    }
  }
}

ledgrid::ReceiverStatusV2 status_snapshot() {
  const auto counters = mailbox_counters();
  ledgrid::ReceiverStatusV2 status{};
  status.flags = 0x01U | (led_driver.in_flight() ? 0x02U : 0U);
  status.active_strips = active_strips;
  status.leds_per_strip = leds_per_strip;
  status.queued_transactions = queued_transactions.load(std::memory_order_relaxed);
  status.packets = packets_received.load(std::memory_order_relaxed);
  status.crc_errors = crc_errors.load(std::memory_order_relaxed);
  status.crc_ok_packets = crc_ok_packets.load(std::memory_order_relaxed);
  status.frames_accepted = counters.accepted;
  status.frames_displayed = counters.displayed;
  status.frames_superseded = counters.superseded;
  status.publish_drops = counters.publish_drops;
  status.spi_queue_errors = spi_queue_errors;
  status.last_crc_us = last_crc_us.load(std::memory_order_relaxed);
  status.last_copy_us = last_copy_us.load(std::memory_order_relaxed);
  status.last_encode_us = led_driver.last_encode_us();
  status.last_show_us = led_driver.last_show_us();
  status.last_accepted_sequence =
      last_accepted_sequence.load(std::memory_order_relaxed);
  status.last_displayed_sequence =
      last_displayed_sequence.load(std::memory_order_relaxed);
  status.display_errors = display_errors.load(std::memory_order_relaxed);
  return status;
}

bool queue_spi_transaction(std::size_t index) {
  ledgrid::encode_receiver_status_v2(
      status_snapshot(), spi_tx_buffers[index], kSpiBufferSize);
  auto& transaction = spi_transactions[index];
  transaction = {};
  transaction.length = kSpiBufferSize * 8U;
  transaction.tx_buffer = spi_tx_buffers[index];
  transaction.rx_buffer = spi_rx_buffers[index];
  transaction.user = reinterpret_cast<void*>(index);
  const esp_err_t result =
      spi_slave_queue_trans(SPI2_HOST, &transaction, pdMS_TO_TICKS(10));
  if (result != ESP_OK) {
    ++spi_queue_errors;
    return false;
  }
  ++queued_transactions;
  return true;
}

void process_command(const std::uint8_t* data, std::size_t length) {
  if (data == nullptr || length == 0) return;

  switch (data[0]) {
    case kCmdPing:
      digitalWrite(kStatusLed, !digitalRead(kStatusLed));
      break;

    case kCmdSetPixel: {
      if (length != 6) break;
      const std::uint16_t pixel =
          (static_cast<std::uint16_t>(data[1]) << 8) | data[2];
      if (pixel >= total_leds()) break;
      const std::size_t offset = static_cast<std::size_t>(pixel) * 3U;
      std::memcpy(working_frame + offset, data + 3, 3);
      break;
    }

    case kCmdSetBrightness:
      if (length == 2) {
        brightness = data[1];
        publish_working_frame();
      }
      break;

    case kCmdShow:
      if (length == 1) publish_working_frame();
      break;

    case kCmdClear:
      if (length == 1) {
        std::memset(working_frame, 0, active_rgb_bytes());
        publish_working_frame();
      }
      break;

    case kCmdSetRange: {
      if (length < 4) break;
      const std::uint16_t start =
          (static_cast<std::uint16_t>(data[1]) << 8) | data[2];
      std::uint16_t count = data[3];
      if (start >= total_leds()) break;
      count = std::min<std::uint16_t>(count, total_leds() - start);
      const std::size_t expected = 4U + static_cast<std::size_t>(count) * 3U;
      if (length != expected) break;
      std::memcpy(
          working_frame + static_cast<std::size_t>(start) * 3U,
          data + 4,
          static_cast<std::size_t>(count) * 3U);
      break;
    }

    case kCmdSetAll: {
      const std::size_t expected = 1U + active_rgb_bytes();
      if (length != expected) break;
      std::memcpy(working_frame, data + 1, active_rgb_bytes());
      publish_working_frame();
      break;
    }

    case kCmdConfig: {
      if (length < 4 || length > 5) break;
      const std::uint8_t new_strips = data[1];
      const std::uint16_t new_leds =
          (static_cast<std::uint16_t>(data[2]) << 8) | data[3];
      if (new_strips != kMaxStrips || new_leds != kMaxLedsPerStrip) {
        break;
      }
      if (new_strips != active_strips || new_leds != leds_per_strip) {
        active_strips = new_strips;
        leds_per_strip = new_leds;
        std::memset(working_frame, 0, sizeof(working_frame));
        publish_working_frame();
      }
      break;
    }

    default:
      break;
  }
}

void initialize_spi() {
  gpio_reset_pin(kSpiChipSelect);
  gpio_reset_pin(kSpiClock);
  gpio_reset_pin(kSpiMosi);
  gpio_set_direction(kSpiChipSelect, GPIO_MODE_INPUT);
  gpio_set_direction(kSpiClock, GPIO_MODE_INPUT);
  gpio_set_direction(kSpiMosi, GPIO_MODE_INPUT);
  gpio_set_pull_mode(kSpiChipSelect, GPIO_PULLUP_ONLY);
  gpio_set_pull_mode(kSpiClock, GPIO_FLOATING);
  gpio_set_pull_mode(kSpiMosi, GPIO_FLOATING);

  spi_bus_config_t bus_config = {};
  bus_config.mosi_io_num = kSpiMosi;
  bus_config.miso_io_num = kSpiMiso;
  bus_config.sclk_io_num = kSpiClock;
  bus_config.quadwp_io_num = -1;
  bus_config.quadhd_io_num = -1;
  bus_config.max_transfer_sz = kSpiBufferSize;
  bus_config.flags =
      SPICOMMON_BUSFLAG_SCLK | SPICOMMON_BUSFLAG_MOSI | SPICOMMON_BUSFLAG_MISO;

  spi_slave_interface_config_t slave_config = {};
  slave_config.mode = 0;
  slave_config.spics_io_num = kSpiChipSelect;
  slave_config.queue_size = kSpiQueueDepth;

  const esp_err_t result = spi_slave_initialize(
      SPI2_HOST, &bus_config, &slave_config, SPI_DMA_CH_AUTO);
  if (result != ESP_OK) {
    Serial.printf("SPI initialization failed: %d\n", result);
    while (true) delay(1000);
  }

  for (std::size_t i = 0; i < kSpiQueueDepth; ++i) {
    if (!queue_spi_transaction(i)) {
      Serial.printf("SPI queue initialization failed for slot %u\n",
                    static_cast<unsigned>(i));
      while (true) delay(1000);
    }
  }
}

}  // namespace

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(kStatusLed, OUTPUT);
  digitalWrite(kStatusLed, LOW);

  Serial.println("LED Grid native ESP32-S3 parallel receiver v2");
  if (!led_driver.begin(kLedPins, kMaxStrips, kMaxLedsPerStrip)) {
    Serial.println("LCD/I80 parallel LED driver initialization failed");
    while (true) delay(1000);
  }

  if (xTaskCreatePinnedToCore(
          display_task,
          "led-display",
          8192,
          nullptr,
          3,
          &display_task_handle,
          0) != pdPASS) {
    Serial.println("Display task creation failed");
    while (true) delay(1000);
  }

  // Publish a black startup frame before accepting transport data.
  publish_working_frame();
  initialize_spi();
  Serial.printf(
      "Ready: %u strips x %u LEDs, SPI queue=%u, encoded frame=%u bytes\n",
      active_strips,
      leds_per_strip,
      static_cast<unsigned>(kSpiQueueDepth),
      static_cast<unsigned>(ledgrid::ws2812_encoded_size(leds_per_strip)));
}

void loop() {
  spi_slave_transaction_t* completed = nullptr;
  const esp_err_t result = spi_slave_get_trans_result(
      SPI2_HOST, &completed, pdMS_TO_TICKS(100));
  if (result == ESP_ERR_TIMEOUT) return;
  if (result != ESP_OK || completed == nullptr) {
    ++spi_queue_errors;
    return;
  }

  if (queued_transactions > 0) --queued_transactions;
  ++packets_received;
  const std::size_t index = reinterpret_cast<std::size_t>(completed->user);
  const std::size_t bytes = completed->trans_len / 8U;
  const std::uint8_t* packet = spi_rx_buffers[index];

  if (bytes < 1U + kCrcBytes) {
    ++crc_errors;
  } else {
    const std::size_t payload_bytes = bytes - kCrcBytes;
    const std::uint16_t received_crc =
        (static_cast<std::uint16_t>(packet[bytes - 2]) << 8) |
        packet[bytes - 1];
    const std::uint32_t crc_started =
        static_cast<std::uint32_t>(esp_timer_get_time());
    const std::uint16_t computed_crc = crc16_ccitt(packet, payload_bytes);
    last_crc_us = duration_u16(
        static_cast<std::uint32_t>(esp_timer_get_time()) - crc_started);
    if (received_crc != computed_crc) {
      ++crc_errors;
    } else {
      ++crc_ok_packets;
      process_command(packet, payload_bytes);
    }
  }

  queue_spi_transaction(index);
}
