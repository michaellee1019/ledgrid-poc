#include <algorithm>
#include <atomic>
#include <cstring>
#include <new>

#include "driver/gpio.h"
#include "driver/spi_common.h"
#include "driver/spi_slave.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "esp_attr.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "ledgrid/frame_mailbox.hpp"
#include "ledgrid/esp_backends.hpp"
#include "ledgrid/parallel_led_driver.hpp"
#include "ledgrid/protocol.hpp"
#include "ledgrid/receiver_control.hpp"
#include "ledgrid/startup_animation.hpp"
#include "ledgrid/ws2812_encoder.hpp"

namespace {

constexpr gpio_num_t kSpiMosi = GPIO_NUM_11;
constexpr gpio_num_t kSpiMiso = GPIO_NUM_13;
constexpr gpio_num_t kSpiClock = GPIO_NUM_12;
constexpr gpio_num_t kSpiChipSelect = GPIO_NUM_10;
constexpr gpio_num_t kStatusLed = GPIO_NUM_48;

constexpr std::uint8_t kMaxStrips = 8;
// Keep capacity at 140 for transport/mailbox/DMA buffers while allowing the
// host to configure the camera-verified installed length (currently 138).
constexpr std::uint16_t kMaxLedsPerStrip = 140;
constexpr std::size_t kMaxTotalLeds = kMaxStrips * kMaxLedsPerStrip;
constexpr std::size_t kMaxRgbBytes = kMaxTotalLeds * 3;
constexpr std::uint8_t kDefaultStrips = 8;
constexpr std::uint16_t kDefaultLedsPerStrip = 138;
constexpr std::uint8_t kDefaultBrightness = 50;
constexpr int kLedPins[kMaxStrips] = {18, 17, 16, 15, 7, 6, 5, 4};

constexpr std::size_t kCrcBytes = ledgrid::kSpiCrcBytes;
constexpr std::size_t kSpiBufferSize = ledgrid::kMaxSpiTransactionBytes;
constexpr std::size_t kSpiQueueDepth = 2;
constexpr const char* kLogTag = "ledgrid";

DMA_ATTR std::uint8_t spi_rx_buffers[kSpiQueueDepth][kSpiBufferSize] = {};
DMA_ATTR std::uint8_t spi_tx_buffers[kSpiQueueDepth][kSpiBufferSize] = {};
spi_slave_transaction_t spi_transactions[kSpiQueueDepth] = {};

std::uint8_t working_frame[kMaxRgbBytes] = {};
std::uint8_t startup_frame[kMaxRgbBytes] = {};
std::uint8_t animation_frame[kMaxRgbBytes] = {};
std::uint8_t mailbox_frames[ledgrid::kFrameMailboxSlots][kMaxRgbBytes] = {};
ledgrid::LatestFrameMailbox frame_mailbox;
portMUX_TYPE mailbox_mux = portMUX_INITIALIZER_UNLOCKED;
TaskHandle_t display_task_handle = nullptr;
ledgrid::ParallelLedDriver led_driver;
ledgrid::SpiffsAssetStore asset_store;
ledgrid::MbedtlsAssetVerifier signature_verifier;
ledgrid::NvsReceiverPersistence receiver_persistence;
ledgrid::EspAnimationBackend animation_backend(&asset_store);
ledgrid::ReceiverController* receiver_controller = nullptr;
SemaphoreHandle_t controller_mutex = nullptr;
esp_timer_handle_t render_watchdog_timer = nullptr;

constexpr std::uint32_t kRtcRenderCrashMagic = 0x4C475743U;
RTC_NOINIT_ATTR std::uint32_t rtc_render_crash_magic;
RTC_NOINIT_ATTR std::uint8_t rtc_render_crash_digest[32];
std::uint8_t pending_render_digest[32] = {};

std::uint8_t active_strips = kDefaultStrips;
std::uint16_t leds_per_strip = kDefaultLedsPerStrip;
std::uint8_t brightness = kDefaultBrightness;
std::atomic<std::uint32_t> next_sequence{1};
std::atomic<ledgrid::DisplayMode> display_mode{
    ledgrid::DisplayMode::StartupFallback};
std::atomic<ledgrid::OperationResult> last_operation_result{
    ledgrid::OperationResult::None};

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

void lock_controller() {
  if (controller_mutex != nullptr) xSemaphoreTake(controller_mutex, portMAX_DELAY);
}

void unlock_controller() {
  if (controller_mutex != nullptr) xSemaphoreGive(controller_mutex);
}

void synchronize_controller_state_locked() {
  if (receiver_controller == nullptr) return;
  display_mode.store(receiver_controller->modes().mode(), std::memory_order_release);
  last_operation_result.store(receiver_controller->modes().last_result(),
                              std::memory_order_release);
}

void render_watchdog_callback(void*) {
  std::memcpy(rtc_render_crash_digest, pending_render_digest,
              sizeof(rtc_render_crash_digest));
  rtc_render_crash_magic = kRtcRenderCrashMagic;
  esp_restart();
}

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

bool publish_working_frame(bool take_display_ownership = false) {
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
  metadata.sequence = next_sequence.fetch_add(1, std::memory_order_relaxed);
  metadata.byte_count = bytes;
  metadata.strip_count = active_strips;
  metadata.leds_per_strip = leds_per_strip;
  metadata.brightness = brightness;

  portENTER_CRITICAL(&mailbox_mux);
  const bool committed = frame_mailbox.commit_write(slot, metadata);
  portEXIT_CRITICAL(&mailbox_mux);
  if (!committed) return false;

  last_accepted_sequence = metadata.sequence;
  if (take_display_ownership) {
    lock_controller();
    if (receiver_controller != nullptr) receiver_controller->host_frame_received();
    synchronize_controller_state_locked();
    unlock_controller();
  }
  if (display_task_handle != nullptr) xTaskNotifyGive(display_task_handle);
  return true;
}

void display_task(void*) {
  const std::uint64_t animation_started_us = esp_timer_get_time();
  TickType_t animation_wake = xTaskGetTickCount();
  while (true) {
    const ledgrid::DisplayMode mode =
        display_mode.load(std::memory_order_acquire);
    if (mode == ledgrid::DisplayMode::StartupFallback) {
      const std::uint64_t now_us = esp_timer_get_time();
      if (!ledgrid::render_startup_rainbow(
              now_us - animation_started_us, kDefaultStrips,
              kDefaultLedsPerStrip, startup_frame, sizeof(startup_frame))) {
        ++display_errors;
        vTaskDelay(pdMS_TO_TICKS(10));
        continue;
      }
      const std::uint32_t sequence =
          next_sequence.fetch_add(1, std::memory_order_relaxed);
      const bool submitted = led_driver.submit(
          startup_frame,
          static_cast<std::size_t>(kDefaultStrips) * kDefaultLedsPerStrip * 3U,
          kDefaultStrips, kDefaultLedsPerStrip, kDefaultBrightness, sequence);
      const bool completed =
          submitted && led_driver.wait_for_done(pdMS_TO_TICKS(100));
      if (completed) last_displayed_sequence = sequence;
      else ++display_errors;
      continue;
    }
    if (mode == ledgrid::DisplayMode::FirmwareAnimation) {
      const std::uint64_t now_us = esp_timer_get_time();
      bool changed = false;
      bool rendered = false;
      std::uint32_t render_us = 0;
      lock_controller();
      if (receiver_controller != nullptr &&
          receiver_controller->modes().mode() ==
              ledgrid::DisplayMode::FirmwareAnimation) {
        std::memcpy(pending_render_digest,
                    receiver_controller->modes().active_digest(), 32);
        const std::uint32_t started = static_cast<std::uint32_t>(esp_timer_get_time());
        if (render_watchdog_timer != nullptr)
          esp_timer_start_once(render_watchdog_timer,
                               ledgrid::kAnimationRenderWatchdogUs);
        rendered = animation_backend.render(
            now_us, animation_frame, active_rgb_bytes(), &changed);
        if (render_watchdog_timer != nullptr) esp_timer_stop(render_watchdog_timer);
        render_us = static_cast<std::uint32_t>(esp_timer_get_time()) - started;
        receiver_controller->render_completed(rendered, render_us);
        synchronize_controller_state_locked();
      }
      unlock_controller();
      if (rendered && changed) {
        const std::uint32_t sequence =
            next_sequence.fetch_add(1, std::memory_order_relaxed);
        const bool submitted = led_driver.submit(
            animation_frame, active_rgb_bytes(), active_strips, leds_per_strip,
            brightness, sequence);
        const bool completed =
            submitted && led_driver.wait_for_done(pdMS_TO_TICKS(100));
        if (completed) last_displayed_sequence = sequence;
        else ++display_errors;
      }
      vTaskDelayUntil(&animation_wake, pdMS_TO_TICKS(5));
      continue;
    }
    if (mode != ledgrid::DisplayMode::HostFrames) {
      // Maintenance freezes the last completed frame.
      ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(10));
      continue;
    }
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100));
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

ledgrid::ReceiverStatus status_snapshot() {
  const auto counters = mailbox_counters();
  ledgrid::ReceiverStatus status{};
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
  lock_controller();
  if (receiver_controller != nullptr) receiver_controller->populate_status(&status);
  else {
    status.capabilities = ledgrid::kCapabilityTypedParameters |
                          ledgrid::kCapabilityQuarantine;
    status.display_mode = display_mode.load(std::memory_order_relaxed);
    status.last_result = last_operation_result.load(std::memory_order_relaxed);
  }
  unlock_controller();
  return status;
}

bool queue_spi_transaction(std::size_t index) {
  ledgrid::encode_receiver_status(
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

bool process_command(const std::uint8_t* data, std::size_t length) {
  if (data == nullptr || length == 0) return false;

  switch (static_cast<ledgrid::Command>(data[0])) {
    case ledgrid::Command::Ping:
      if (length != 1) return false;
      gpio_set_level(kStatusLed, !gpio_get_level(kStatusLed));
      return true;

    case ledgrid::Command::SetPixel: {
      if (length != 6) return false;
      const std::uint16_t pixel =
          (static_cast<std::uint16_t>(data[1]) << 8) | data[2];
      if (pixel >= total_leds()) return false;
      const std::size_t offset = static_cast<std::size_t>(pixel) * 3U;
      std::memcpy(working_frame + offset, data + 3, 3);
      return true;
    }

    case ledgrid::Command::SetBrightness:
      if (length != 2) return false;
      brightness = data[1];
      publish_working_frame();
      return true;

    case ledgrid::Command::Show:
      if (length != 1) return false;
      publish_working_frame(true);
      return true;

    case ledgrid::Command::Clear:
      if (length != 1) return false;
      std::memset(working_frame, 0, active_rgb_bytes());
      publish_working_frame(true);
      return true;

    case ledgrid::Command::SetRange: {
      if (length < 4) return false;
      const std::uint16_t start =
          (static_cast<std::uint16_t>(data[1]) << 8) | data[2];
      std::uint16_t count = data[3];
      if (start >= total_leds()) return false;
      count = std::min<std::uint16_t>(count, total_leds() - start);
      const std::size_t expected = 4U + static_cast<std::size_t>(count) * 3U;
      if (length != expected) return false;
      std::memcpy(
          working_frame + static_cast<std::size_t>(start) * 3U,
          data + 4,
          static_cast<std::size_t>(count) * 3U);
      return true;
    }

    case ledgrid::Command::SetAll: {
      const std::size_t expected = 1U + active_rgb_bytes();
      if (length != expected) return false;
      std::memcpy(working_frame, data + 1, active_rgb_bytes());
      publish_working_frame(true);
      return true;
    }

    case ledgrid::Command::Config: {
      if (length < 4 || length > 5) return false;
      const std::uint8_t new_strips = data[1];
      const std::uint16_t new_leds =
          (static_cast<std::uint16_t>(data[2]) << 8) | data[3];
      if (new_strips != kMaxStrips || new_leds == 0 || new_leds > kMaxLedsPerStrip) {
        return false;
      }
      if (new_strips != active_strips || new_leds != leds_per_strip) {
        active_strips = new_strips;
        leds_per_strip = new_leds;
        lock_controller();
        if (receiver_controller != nullptr)
          receiver_controller->configure_geometry(active_strips, leds_per_strip);
        synchronize_controller_state_locked();
        unlock_controller();
        std::memset(working_frame, 0, sizeof(working_frame));
        publish_working_frame();
      }
      return true;
    }

    case ledgrid::Command::CapabilitiesQuery:
    case ledgrid::Command::AssetProbe:
    case ledgrid::Command::AssetBegin:
    case ledgrid::Command::AssetChunk:
    case ledgrid::Command::AssetCommit:
    case ledgrid::Command::AssetRemove:
    case ledgrid::Command::AnimationStart:
    case ledgrid::Command::AnimationStop:
    case ledgrid::Command::AnimationRestart:
    case ledgrid::Command::AnimationParameters:
    case ledgrid::Command::AssetAbort:
      lock_controller();
      if (receiver_controller == nullptr) {
        last_operation_result = ledgrid::OperationResult::Unsupported;
      } else {
        receiver_controller->process(data, length);
        synchronize_controller_state_locked();
      }
      unlock_controller();
      if (display_task_handle != nullptr) xTaskNotifyGive(display_task_handle);
      return true;

    default:
      return false;
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
    ESP_LOGE(kLogTag, "SPI initialization failed: %d", result);
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }

  for (std::size_t i = 0; i < kSpiQueueDepth; ++i) {
    if (!queue_spi_transaction(i)) {
      ESP_LOGE(kLogTag, "SPI queue initialization failed for slot %u",
               static_cast<unsigned>(i));
      while (true) vTaskDelay(pdMS_TO_TICKS(1000));
    }
  }
}

}  // namespace

extern "C" void app_main() {
  gpio_reset_pin(kStatusLed);
  gpio_set_direction(kStatusLed, GPIO_MODE_OUTPUT);
  gpio_set_level(kStatusLed, 0);

  controller_mutex = xSemaphoreCreateMutex();
  if (controller_mutex == nullptr) {
    ESP_LOGE(kLogTag, "Controller mutex allocation failed");
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }
  const bool persistence_ready = receiver_persistence.begin();
  const esp_reset_reason_t reset_reason = esp_reset_reason();
  receiver_persistence.record_reset_reason(static_cast<std::uint32_t>(reset_reason));
  const bool store_ready = asset_store.begin();
  signature_verifier.begin();
  const bool runtime_ready = animation_backend.begin();
  receiver_controller = new (std::nothrow) ledgrid::ReceiverController(
      &asset_store, kDefaultStrips, kDefaultLedsPerStrip,
      CONFIG_LEDGRID_LOGICAL_DEVICE, &signature_verifier, &animation_backend,
      &receiver_persistence);
  if (receiver_controller == nullptr) {
    ESP_LOGE(kLogTag, "Receiver controller allocation failed");
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }
  std::uint8_t quarantine[32] = {};
  if (persistence_ready && receiver_persistence.quarantined_digest(quarantine))
    receiver_controller->restore_quarantine(quarantine);
  std::uint8_t prior_active[32] = {};
  const bool had_active =
      persistence_ready && receiver_persistence.active_digest(prior_active);
  const bool render_watchdog_reset = rtc_render_crash_magic == kRtcRenderCrashMagic;
  if (render_watchdog_reset) {
    receiver_controller->restore_quarantine(rtc_render_crash_digest);
    rtc_render_crash_magic = 0;
  } else if (had_active &&
             (reset_reason == ESP_RST_PANIC || reset_reason == ESP_RST_TASK_WDT ||
              reset_reason == ESP_RST_INT_WDT)) {
    receiver_controller->restore_quarantine(prior_active);
  }
  receiver_persistence.clear_active();
  synchronize_controller_state_locked();
  const esp_timer_create_args_t watchdog_args = {
      .callback = render_watchdog_callback,
      .arg = nullptr,
      .dispatch_method = ESP_TIMER_TASK,
      .name = "anim-watchdog",
      .skip_unhandled_events = true,
  };
  if (esp_timer_create(&watchdog_args, &render_watchdog_timer) != ESP_OK)
    ESP_LOGE(kLogTag, "Animation watchdog timer unavailable; native capability disabled");
  animation_backend.set_native_watchdog_ready(render_watchdog_timer != nullptr);
  ESP_LOGI(kLogTag,
           "Backends: store=%d trust=%d unsigned_dev=%d runtime=%d device=%d",
           store_ready, signature_verifier.available(),
           signature_verifier.unsigned_development(), runtime_ready,
           CONFIG_LEDGRID_LOGICAL_DEVICE);

  if (!led_driver.begin(kLedPins, kMaxStrips, kMaxLedsPerStrip)) {
    ESP_LOGE(kLogTag, "LCD/I80 parallel LED driver initialization failed");
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }

  if (xTaskCreatePinnedToCore(
          display_task,
          "led-display",
          8192,
          nullptr,
          3,
          &display_task_handle,
          0) != pdPASS) {
    ESP_LOGE(kLogTag, "Display task creation failed");
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }

  ESP_LOGI(kLogTag, "LED Grid native ESP32-S3 parallel receiver v3");
  initialize_spi();
  ESP_LOGI(
      kLogTag,
      "Ready: %u strips x %u LEDs, SPI queue=%u, encoded frame=%u bytes",
      active_strips,
      leds_per_strip,
      static_cast<unsigned>(kSpiQueueDepth),
      static_cast<unsigned>(ledgrid::ws2812_encoded_size(leds_per_strip)));
  while (true) {
    spi_slave_transaction_t* completed = nullptr;
    const esp_err_t result = spi_slave_get_trans_result(
        SPI2_HOST, &completed, pdMS_TO_TICKS(100));
    if (result == ESP_ERR_TIMEOUT) continue;
    if (result != ESP_OK || completed == nullptr) {
      ++spi_queue_errors;
      continue;
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
}
