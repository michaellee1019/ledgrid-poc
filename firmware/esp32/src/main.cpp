#include <algorithm>
#include <atomic>
#include <cstring>

#include "driver/gpio.h"
#include "driver/spi_common.h"
#include "driver/spi_slave.h"
#include "esp_timer.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "ledgrid/frame_mailbox.hpp"
#include "ledgrid/receiver_command_queue.hpp"
#include "ledgrid/parallel_led_driver.hpp"
#include "ledgrid/protocol.hpp"
#include "ledgrid/receiver_task_policy.hpp"
#include "ledgrid/ws2812_encoder.hpp"

namespace {


constexpr gpio_num_t kSpiMosi = GPIO_NUM_11;
constexpr gpio_num_t kSpiMiso = GPIO_NUM_13;
constexpr gpio_num_t kSpiClock = GPIO_NUM_12;
constexpr gpio_num_t kSpiChipSelect = GPIO_NUM_10;
constexpr gpio_num_t kStatusLed = GPIO_NUM_48;
constexpr const char* kLogTag = "ledgrid";

constexpr std::uint8_t kMaxStrips = 8;
// Keep capacity at 140 for transport/mailbox/DMA buffers while allowing the
// host to configure the camera-verified installed length (currently 138).
constexpr std::uint16_t kMaxLedsPerStrip = 140;
constexpr std::size_t kMaxTotalLeds = kMaxStrips * kMaxLedsPerStrip;
constexpr std::size_t kMaxRgbBytes = kMaxTotalLeds * 3;
constexpr std::uint8_t kDefaultStrips = 8;
constexpr std::uint16_t kDefaultLedsPerStrip = 138;
constexpr std::uint16_t kInstalledGlobalStrips = 33;
constexpr int kLedPins[kMaxStrips] = {18, 17, 16, 15, 7, 6, 5, 4};

constexpr std::size_t kCrcBytes = 2;
constexpr std::size_t kSpiFrameBytes = 1 + kMaxRgbBytes + kCrcBytes;
constexpr std::size_t kSpiBufferSize =
    ledgrid::kAnimationPipelineMaxTransactionBytes;
constexpr std::size_t kSpiQueueDepth = 2;
// Four owned mutations cover normal bounded host bursts. Storage commands are
// host-serialized; exceeding this bound is a counted fail-stop, never a drop.
using CommandQueue = ledgrid::ReceiverCommandQueue<4, kSpiBufferSize>;
CommandQueue command_queue;
portMUX_TYPE command_mux = portMUX_INITIALIZER_UNLOCKED;
TaskHandle_t command_task_handle = nullptr;
TaskHandle_t spi_task_handle = nullptr;
struct EncodedStatus {
  std::uint8_t bytes[ledgrid::kStatusBytesV8]{};
};
EncodedStatus status_buffers[2];
unsigned published_status = 0;
std::uint64_t published_frontier = UINT64_MAX;
static_assert(configNUMBER_OF_CORES > ledgrid::kReceiverDisplayTaskCore,
              "receiver firmware requires the ESP32-S3 dual-core scheduler");
static_assert(kSpiBufferSize == 4096, "transport contract changed");
static_assert(kSpiFrameBytes <= kSpiBufferSize,
              "maximum RGB frame plus CRC exceeds transport buffer");
static_assert(1U + kMaxRgbBytes <=
                  ledgrid::kAlignedEnvelopeMaxSemanticBytes,
              "maximum RGB frame exceeds aligned semantic bound");
static_assert(1U + kMaxStrips * kDefaultLedsPerStrip * 3U <=
                  ledgrid::kFecEnvelopeMaxSemanticBytes,
              "installed RGB frame exceeds FEC semantic bound");
static_assert(ledgrid::kStatusBytesV3 + kCrcBytes <= kSpiBufferSize,
              "status query plus CRC exceeds transport buffer");
static_assert(ledgrid::kStatusBytesV5 + kCrcBytes <= kSpiBufferSize,
              "status-v5 query plus CRC exceeds transport buffer");
static_assert(ledgrid::kStatusBytesV6 + kCrcBytes <= kSpiBufferSize,
              "status-v6 query plus CRC exceeds transport buffer");
static_assert(ledgrid::kStatusBytesV6 <=
                  ledgrid::kAlignedEnvelopeMaxSemanticBytes,
              "status-v6 query exceeds aligned semantic bound");
static_assert(ledgrid::kStatusBytesV7 <=
                  ledgrid::kAlignedEnvelopeMaxSemanticBytes,
              "status-v7 query exceeds aligned semantic bound");

DMA_ATTR std::uint8_t spi_rx_buffers[kSpiQueueDepth][kSpiBufferSize] = {};
DMA_ATTR std::uint8_t spi_tx_buffers[kSpiQueueDepth][kSpiBufferSize] = {};
spi_slave_transaction_t spi_transactions[kSpiQueueDepth] = {};

std::uint8_t working_frame[kMaxRgbBytes] = {};
std::uint8_t fec_semantic_buffer[
    ledgrid::kFecScratchBytes] = {};
std::uint8_t mailbox_frames[ledgrid::kFrameMailboxSlots][kMaxRgbBytes] = {};
ledgrid::LatestFrameMailbox frame_mailbox;
portMUX_TYPE mailbox_mux = portMUX_INITIALIZER_UNLOCKED;
SemaphoreHandle_t runtime_mutex = nullptr;
TaskHandle_t display_task_handle = nullptr;
ledgrid::ParallelLedDriver led_driver;
using ledgrid::OutputConfiguration;
OutputConfiguration output_configuration;
std::uint32_t operation_sequence=0;
std::uint8_t last_processed_command=0;
std::uint8_t last_command_result=0;
std::atomic<std::uint32_t> next_sequence{1};
std::atomic<std::uint8_t> logical_receiver_id{0xFF};
std::atomic<std::uint16_t> configured_global_strip_offset{0};
std::atomic<bool> explicit_receiver_topology{false};

std::atomic<std::uint32_t> packets_received{0};
std::atomic<std::uint32_t> crc_errors{0};
std::atomic<std::uint32_t> crc_ok_packets{0};
std::atomic<std::uint32_t> fec_packets_received{0};
std::atomic<std::uint32_t> fec_packets_accepted{0};
std::atomic<std::uint32_t> fec_corrected_packets{0};
std::atomic<std::uint32_t> fec_corrected_codewords{0};
std::atomic<std::uint32_t> fec_uncorrectable_packets{0};
std::atomic<std::uint32_t> fec_semantic_crc_errors{0};
std::atomic<std::uint32_t> fec_framing_errors{0};
std::atomic<std::uint16_t> fec_last_decode_us{0};
std::atomic<std::uint16_t> fec_max_decode_us{0};
std::atomic<std::uint32_t> spi_queue_errors{0};
std::atomic<std::uint32_t> display_errors{0};
std::atomic<std::uint16_t> queued_transactions{0};
std::atomic<std::uint16_t> last_crc_us{0};
std::atomic<std::uint16_t> last_copy_us{0};
std::atomic<std::uint32_t> last_accepted_sequence{0};
std::atomic<std::uint32_t> last_displayed_sequence{0};
std::atomic<std::uint8_t> requested_lane_mask{ledgrid::kAllLanesMask};
std::atomic<std::uint8_t> applied_lane_mask{ledgrid::kAllLanesMask};
std::atomic<std::uint8_t> requested_stagger_phases{ledgrid::kStaggerOff};
std::atomic<std::uint8_t> applied_stagger_phases{ledgrid::kStaggerOff};

std::uint16_t duration_u16(std::uint32_t value) {
  return value > UINT16_MAX ? UINT16_MAX : static_cast<std::uint16_t>(value);
}

void lock_runtime() { xSemaphoreTake(runtime_mutex, portMAX_DELAY); }
void unlock_runtime() { xSemaphoreGive(runtime_mutex); }
ledgrid::FrameMailboxCounters mailbox_counters() {
  portENTER_CRITICAL(&mailbox_mux);
  const auto counters = frame_mailbox.counters();
  portEXIT_CRITICAL(&mailbox_mux);
  return counters;
}

// The caller holds runtime_mutex, making the output configuration and mailbox
// publication one coherent command-side snapshot.
bool publish_working_frame_locked(
    const OutputConfiguration& output) {
  int slot = -1;
  portENTER_CRITICAL(&mailbox_mux);
  slot = frame_mailbox.begin_write();
  portEXIT_CRITICAL(&mailbox_mux);
  if (slot < 0) return false;

  const std::size_t bytes = output.rgb_bytes();
  const std::uint32_t copy_started =
      static_cast<std::uint32_t>(esp_timer_get_time());
  std::memcpy(mailbox_frames[slot], working_frame, bytes);
  last_copy_us = duration_u16(
      static_cast<std::uint32_t>(esp_timer_get_time()) - copy_started);

  ledgrid::FrameMetadata metadata{};
  metadata.sequence = next_sequence.fetch_add(1, std::memory_order_relaxed);
  metadata.byte_count = bytes;
  metadata.strip_count = output.strip_count;
  metadata.leds_per_strip = output.leds_per_strip;
  metadata.brightness = output.brightness;

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
  const auto wanted_mask=requested_lane_mask.load();
  if (wanted_mask != applied_lane_mask && led_driver.set_lane_mask(wanted_mask)) applied_lane_mask=wanted_mask;
  const auto wanted_stagger=requested_stagger_phases.load();
  if (wanted_stagger != applied_stagger_phases && led_driver.set_stagger_phases(wanted_stagger)) applied_stagger_phases=wanted_stagger;
    ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100));
    while (true) {
      ledgrid::FrameMetadata metadata{};
      int slot = -1;
      portENTER_CRITICAL(&mailbox_mux);
      slot = frame_mailbox.begin_read(&metadata);
      portEXIT_CRITICAL(&mailbox_mux);
      if (slot < 0) break;

      lock_runtime();
      const auto current_output = output_configuration;
      const bool current =
          metadata.byte_count == current_output.rgb_bytes() &&
          metadata.strip_count == current_output.strip_count &&
          metadata.leds_per_strip == current_output.leds_per_strip &&
          metadata.brightness == current_output.brightness;
      const bool submitted = current && led_driver.submit(
          mailbox_frames[slot], metadata.byte_count, metadata.strip_count,
          metadata.leds_per_strip, metadata.brightness, metadata.sequence);
      unlock_runtime();
      if (!current) {
        portENTER_CRITICAL(&mailbox_mux);
        frame_mailbox.cancel_read(slot);
        portEXIT_CRITICAL(&mailbox_mux);
        break;
      }
      const bool completed = submitted &&
          led_driver.wait_for_done(pdMS_TO_TICKS(100));
      lock_runtime();
      const auto completed_output = output_configuration;
      const bool completion_current =
          metadata.byte_count == completed_output.rgb_bytes() &&
          metadata.strip_count == completed_output.strip_count &&
          metadata.leds_per_strip == completed_output.leds_per_strip &&
          metadata.brightness == completed_output.brightness;
      unlock_runtime();

      portENTER_CRITICAL(&mailbox_mux);
      if (completed && completion_current) {
        frame_mailbox.finish_read(slot);
      } else {
        frame_mailbox.cancel_read(slot);
      }
      portEXIT_CRITICAL(&mailbox_mux);

      if (completed && completion_current) {
        last_displayed_sequence = metadata.sequence;
      } else if (completion_current) {
        ++display_errors;
      }
    }
  }
}

ledgrid::ReceiverStatusV7 status_snapshot() {
  const auto counters = mailbox_counters();
  ledgrid::ReceiverStatusV7 status{};
  status.flags = 0x01U | (led_driver.in_flight() ? 0x02U : 0U);
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
  lock_runtime();
  status.active_strips = output_configuration.strip_count;
  status.leds_per_strip = output_configuration.leds_per_strip;
  status.lane_mask = applied_lane_mask;
  status.capabilities = (1U<<14) | (1U<<20) | (1U<<21);
  status.global_strip_offset = configured_global_strip_offset;
  status.operation_sequence = operation_sequence;
  status.last_processed_command = last_processed_command;
  status.last_result = last_command_result;
  unlock_runtime();
  status.logical_receiver_id = logical_receiver_id.load(std::memory_order_relaxed);
  status.stagger_phases =
      applied_stagger_phases.load(std::memory_order_relaxed);
  status.fec_packets_received =
      fec_packets_received.load(std::memory_order_relaxed);
  status.fec_packets_accepted =
      fec_packets_accepted.load(std::memory_order_relaxed);
  status.fec_corrected_packets =
      fec_corrected_packets.load(std::memory_order_relaxed);
  status.fec_corrected_codewords =
      fec_corrected_codewords.load(std::memory_order_relaxed);
  status.fec_uncorrectable_packets =
      fec_uncorrectable_packets.load(std::memory_order_relaxed);
  status.fec_semantic_crc_errors =
      fec_semantic_crc_errors.load(std::memory_order_relaxed);
  status.fec_framing_errors =
      fec_framing_errors.load(std::memory_order_relaxed);
  status.fec_last_decode_us =
      fec_last_decode_us.load(std::memory_order_relaxed);
  status.fec_max_decode_us =
      fec_max_decode_us.load(std::memory_order_relaxed);
  return status;
}

// Only the executor captures/encodes status. The DMA service copies one already
// encoded immutable version under the publication lock and never takes a
// runtime, native, profile, display or filesystem lock.
void encode_status(const ledgrid::ReceiverStatusV7& status, EncodedStatus* encoded) {
  ledgrid::encode_receiver_status_v8(
      status, encoded->bytes, ledgrid::kStatusBytesV8);
}

bool queue_spi_transaction(std::size_t index) {
  portENTER_CRITICAL(&command_mux);
  std::memcpy(spi_tx_buffers[index],
              status_buffers[published_status].bytes,
              ledgrid::kStatusBytesV8);
  portEXIT_CRITICAL(&command_mux);
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
 if (!data || !length) return false;
 const auto command=static_cast<ledgrid::ReceiverCommand>(data[0]);
 lock_runtime(); const auto output=output_configuration; unlock_runtime();
  switch (command) {
    case ledgrid::ReceiverCommand::Ping:
      if (length != 1) return false;
      gpio_set_level(kStatusLed, !gpio_get_level(kStatusLed));
      return true;

    case ledgrid::ReceiverCommand::SetPixel: {
      if (length != 6) return false;
      const std::uint16_t pixel =
          (static_cast<std::uint16_t>(data[1]) << 8) | data[2];
      if (pixel >= output.total_leds()) return false;
      const std::size_t offset = static_cast<std::size_t>(pixel) * 3U;
      std::memcpy(working_frame + offset, data + 3, 3);
      return true;
    }

    case ledgrid::ReceiverCommand::SetBrightness: {
      if (length != 2) return false;
      lock_runtime();
      const bool updated = true;
      output_configuration.brightness = data[1];
      if (updated) {
        publish_working_frame_locked(output_configuration);
      }
      unlock_runtime();
      if (display_task_handle != nullptr) xTaskNotifyGive(display_task_handle);
      return updated;
    }

    case ledgrid::ReceiverCommand::Show: {
      if (length != 1) return false;
      lock_runtime();
      if (true) {
        publish_working_frame_locked(output_configuration);
      }
      unlock_runtime();
      return true;
    }

    case ledgrid::ReceiverCommand::Clear: {
      if (length != 1) return false;
      lock_runtime();
      const auto current_output = output_configuration;
      std::memset(working_frame, 0, current_output.rgb_bytes());
      if (true) {
        publish_working_frame_locked(current_output);
      }
      unlock_runtime();
      return true;
    }

    case ledgrid::ReceiverCommand::SetRange: {
      if (length < 4) return false;
      const std::uint16_t start =
          (static_cast<std::uint16_t>(data[1]) << 8) | data[2];
      std::uint16_t count = data[3];
      if (start >= output.total_leds()) return false;
      count = static_cast<std::uint16_t>(std::min<std::size_t>(
          count, output.total_leds() - start));
      const std::size_t expected = 4U + static_cast<std::size_t>(count) * 3U;
      if (length != expected) return false;
      std::memcpy(
          working_frame + static_cast<std::size_t>(start) * 3U,
          data + 4,
          static_cast<std::size_t>(count) * 3U);
      return true;
    }

    case ledgrid::ReceiverCommand::SetAll: {
      const std::size_t expected = 1U + output.rgb_bytes();
      if (length != expected) return false;
      lock_runtime();
      const auto current_output = output_configuration;
      if (current_output.rgb_bytes() != output.rgb_bytes()) {
        unlock_runtime();
        return false;
      }
      std::memcpy(working_frame, data + 1, current_output.rgb_bytes());
      if (!publish_working_frame_locked(current_output)) {
        unlock_runtime();
        return false;
      }
      unlock_runtime();
      if (display_task_handle != nullptr) xTaskNotifyGive(display_task_handle);
      return true;
    }

    case ledgrid::ReceiverCommand::SetLaneMask:
      if (length != 2) return false;
      requested_lane_mask = data[1];
      return true;

    case ledgrid::ReceiverCommand::SetStagger:
      if (length != 2 || data[1] < ledgrid::kStaggerOff ||
          data[1] > ledgrid::kMaxStaggerPhases) {
        return false;
      }
      requested_stagger_phases = data[1];
      return true;

    case ledgrid::ReceiverCommand::Config: {
      if (!ledgrid::valid_installed_config(data, length)) return false;
      const std::uint16_t leds=(data[2]<<8)|data[3];
      const std::uint16_t offset=(data[6]<<8)|data[7];
      if (leds != 138 || offset != data[5]*8 || data[1] != (data[5]==4?1:8)) return false;
      lock_runtime();
      const bool configured = ledgrid::configure_host_output(data, length, &output_configuration, working_frame, sizeof(working_frame));
      if (!configured) { unlock_runtime(); return false; }
      logical_receiver_id=data[5]; configured_global_strip_offset=offset;
      unlock_runtime(); return true;
    }
    default:
      return false;
  }
}

// Called before the driver reports completion to the service task. A packet
// received while a status candidate is being built invalidates that candidate.
void IRAM_ATTR spi_transaction_completed(spi_slave_transaction_t*) {
  portENTER_CRITICAL_ISR(&command_mux);
  command_queue.received();
  portEXIT_CRITICAL_ISR(&command_mux);
}

void execute_command(const CommandQueue::Command& command) {
  const bool accepted = !command.rejected && process_command(command.bytes, command.size);
  lock_runtime(); ++operation_sequence; last_processed_command=command.bytes[0]; last_command_result=accepted?1:4; unlock_runtime();
}

void command_executor() {
  // Executor-owned payload survives every DMA/FEC buffer reuse while storage
  // blocks. The queue counts this executing slot against its fixed capacity.
  static CommandQueue::Command command;
  while (true) {
    portENTER_CRITICAL(&command_mux);
    const bool available = command_queue.take(&command);
    portEXIT_CRITICAL(&command_mux);
    if (available) {
      execute_command(command);
      portENTER_CRITICAL(&command_mux);
      command_queue.complete();
      portEXIT_CRITICAL(&command_mux);
      continue;
    }
    std::uint64_t ticket = 0;
    portENTER_CRITICAL(&command_mux);
    const bool capture = command_queue.publication_ticket(&ticket) &&
                         ticket != published_frontier;
    const unsigned candidate = 1U - published_status;
    portEXIT_CRITICAL(&command_mux);
    if (capture) {
      // Potentially blocking locks and CRC encoding stay outside the short
      // admission lock. A received mutation/query invalidates this candidate.
      encode_status(status_snapshot(), &status_buffers[candidate]);
      portENTER_CRITICAL(&command_mux);
      if (command_queue.can_publish(ticket)) {
        published_status = candidate;
        published_frontier = ticket;
      }
      portEXIT_CRITICAL(&command_mux);
    }
    // Notifications coalesce queries; they never occupy a mutation FIFO slot.
    // A receive during capture leaves a pending notification, so no wake is lost.
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
  }
}

void spi_service(void*) {
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
      const std::uint32_t decode_started =
          static_cast<std::uint32_t>(esp_timer_get_time());
      ledgrid::ReceiverPacketPayload decoded{};
      ledgrid::ReceiverPacketDecodeReport decode_report{};
      const bool decoded_ok = ledgrid::decode_receiver_packet_payload(
          packet, bytes, &decoded, &decode_report, fec_semantic_buffer,
          sizeof(fec_semantic_buffer));
      const auto fec_outcome = ledgrid::receiver_fec_packet_outcome(
          decoded_ok, decode_report);
      const bool fec_shaped =
          fec_outcome != ledgrid::ReceiverFecPacketOutcome::NotFec;
      if (fec_shaped) ++fec_packets_received;
      const std::uint16_t decode_us = duration_u16(
          static_cast<std::uint32_t>(esp_timer_get_time()) - decode_started);
      last_crc_us = decode_us;
      if (fec_shaped) {
        fec_last_decode_us = decode_us;
        std::uint16_t prior_max =
            fec_max_decode_us.load(std::memory_order_relaxed);
        while (decode_us > prior_max &&
               !fec_max_decode_us.compare_exchange_weak(
                   prior_max, decode_us, std::memory_order_relaxed)) {}
      }
      if (!decoded_ok) {
        ++crc_errors;
        if (fec_shaped) {
          switch (fec_outcome) {
            case ledgrid::ReceiverFecPacketOutcome::Uncorrectable:
              ++fec_uncorrectable_packets;
              break;
            case ledgrid::ReceiverFecPacketOutcome::SemanticCrcError:
              ++fec_semantic_crc_errors;
              break;
            default:
              ++fec_framing_errors;
              break;
          }
        }
      } else {
        ++crc_ok_packets;
        if (fec_shaped) {
          ++fec_packets_accepted;
          if (decode_report.corrected_codewords != 0U) {
            ++fec_corrected_packets;
            fec_corrected_codewords.fetch_add(
                decode_report.corrected_codewords,
                std::memory_order_relaxed);
          }
        }
        const std::uint8_t* command = decoded.data;
        const std::size_t payload_bytes = decoded.size;
        const bool status_query = command[0] == static_cast<std::uint8_t>(
            ledgrid::ReceiverCommand::StatusQuery);
        if (!status_query) {
          portENTER_CRITICAL(&command_mux);
          const auto admission = command_queue.admit(command, payload_bytes);
          portEXIT_CRITICAL(&command_mux);
          if (admission != CommandQueue::Admission::Accepted) ++spi_queue_errors;
        }
      }
    }
    // Classification includes counter updates and DMA refill. The executor
    // cannot publish a snapshot made halfway through either operation.
    // queue_spi_transaction counts an API failure; commands are never retried.
    queue_spi_transaction(index);
    portENTER_CRITICAL(&command_mux);
    command_queue.classified();
    portEXIT_CRITICAL(&command_mux);
    xTaskNotifyGive(command_task_handle);
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
  slave_config.post_trans_cb = spi_transaction_completed;

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

  runtime_mutex = xSemaphoreCreateMutex();
  if (runtime_mutex == nullptr
  ) {
    ESP_LOGE(kLogTag, "receiver runtime mutex allocation failed");
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }



  if (!led_driver.begin(kLedPins, kMaxStrips, kMaxLedsPerStrip)) {
    ESP_LOGE(kLogTag, "LCD/I80 parallel LED driver initialization failed");
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }

  if (xTaskCreatePinnedToCore(
          display_task,
          "led-display",
          8192,
          nullptr,
          ledgrid::kReceiverDisplayTaskPriority,
          &display_task_handle,
          ledgrid::kReceiverDisplayTaskCore) != pdPASS) {
    ESP_LOGE(kLogTag, "Display task creation failed");
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }

  ESP_LOGI(kLogTag, "LED Grid ESP32-S3 parallel receiver protocol v8");
  // Boot/reset starts with empty admission state and a fresh committed image;
  // no prior DMA descriptors or published snapshots survive.
  encode_status(status_snapshot(), &status_buffers[0]);
  command_task_handle = xTaskGetCurrentTaskHandle();
  initialize_spi();
  if (xTaskCreatePinnedToCore(
          spi_service, "led-spi", 8192, nullptr,
          ledgrid::kReceiverSpiServiceTaskPriority, &spi_task_handle,
          ledgrid::kReceiverSpiTaskCore) != pdPASS) {
    ESP_LOGE(kLogTag, "SPI service task creation failed");
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }
  lock_runtime();
  const auto initial_output = output_configuration;
  unlock_runtime();
  ESP_LOGI(kLogTag,
      "Ready: %u strips x %u LEDs, SPI queue=%u, encoded frame=%u bytes",
      initial_output.strip_count,
      initial_output.leds_per_strip,
      static_cast<unsigned>(kSpiQueueDepth),
      static_cast<unsigned>(
          ledgrid::ws2812_encoded_size(initial_output.leds_per_strip)));
  command_executor();
}
