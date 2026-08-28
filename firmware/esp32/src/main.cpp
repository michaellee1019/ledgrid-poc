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
#include "ledgrid/esp_installation_profile_store.hpp"
#include "ledgrid/esp_native_module.hpp"
#include "ledgrid/parallel_led_driver.hpp"
#include "ledgrid/protocol.hpp"
#include "ledgrid/receiver_task_policy.hpp"
#include "ledgrid/receiver_optics.hpp"
#include "ledgrid/receiver_runtime.hpp"
#include "ledgrid/startup_animation.hpp"
#include "ledgrid/ws2812_encoder.hpp"

namespace {

#ifndef LEDGRID_ENABLE_LOCAL_BACKGROUND
#define LEDGRID_ENABLE_LOCAL_BACKGROUND 0
#endif
#ifndef LEDGRID_ENABLE_INSTALLATION_PROFILES
#define LEDGRID_ENABLE_INSTALLATION_PROFILES 0
#endif

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
constexpr std::uint8_t kDefaultBrightness = 50;
constexpr std::uint16_t kInstalledGlobalStrips = 33;
constexpr int kLedPins[kMaxStrips] = {18, 17, 16, 15, 7, 6, 5, 4};

constexpr std::size_t kCrcBytes = 2;
constexpr std::size_t kSpiFrameBytes = 1 + kMaxRgbBytes + kCrcBytes;
constexpr std::size_t kSpiBufferSize =
    ledgrid::kAnimationPipelineMaxTransactionBytes;
constexpr std::size_t kSpiQueueDepth = 2;
static_assert(configNUMBER_OF_CORES > ledgrid::kReceiverDisplayTaskCore,
              "receiver firmware requires the ESP32-S3 dual-core scheduler");
static_assert(kSpiBufferSize == 4096, "transport contract changed");
static_assert(kSpiFrameBytes <= kSpiBufferSize,
              "maximum RGB frame plus CRC exceeds transport buffer");
static_assert(1U + kMaxRgbBytes <=
                  ledgrid::kAlignedEnvelopeMaxSemanticBytes,
              "maximum RGB frame exceeds aligned semantic bound");
static_assert(1U + kMaxRgbBytes <= ledgrid::kFecEnvelopeMaxSemanticBytes,
              "maximum RGB frame exceeds FEC semantic bound");
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
std::uint8_t startup_frame[kMaxRgbBytes] = {};
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
std::uint8_t composite_frame[kMaxRgbBytes] = {};
#else
std::uint8_t* const composite_frame = startup_frame;
#endif
std::uint8_t mailbox_frames[ledgrid::kFrameMailboxSlots][kMaxRgbBytes] = {};
ledgrid::LatestFrameMailbox frame_mailbox;
portMUX_TYPE mailbox_mux = portMUX_INITIALIZER_UNLOCKED;
SemaphoreHandle_t runtime_mutex = nullptr;
SemaphoreHandle_t profile_mutex = nullptr;
SemaphoreHandle_t native_mutex = nullptr;
TaskHandle_t display_task_handle = nullptr;
ledgrid::ParallelLedDriver led_driver;
ledgrid::ReceiverRuntime receiver_runtime(LEDGRID_ENABLE_LOCAL_BACKGROUND != 0);
ledgrid::ReceiverOutputState receiver_output(
    kDefaultStrips, kDefaultLedsPerStrip, kDefaultBrightness);
ledgrid::ReceiverOperationTracker operation_tracker;
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
ledgrid::EspInstallationProfileStore installation_profile_store;
ledgrid::NvsInstallationProfilePersistence installation_profile_persistence;
std::uint8_t installation_profile_scratch[
    2U * ledgrid::kInstallationProfileReceiverBytesV1] = {};
ledgrid::InstallationProfileManager installation_profile_manager(
    &installation_profile_store, &installation_profile_persistence,
    installation_profile_scratch, sizeof(installation_profile_scratch), true);
std::atomic<bool> installation_profile_ready{false};
#endif
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
ledgrid::EspNativeModuleStore native_module_store;
ledgrid::NvsNativeModulePersistence native_module_persistence;
ledgrid::EspNativeModuleBackend native_module_backend;
ledgrid::EspNativeModuleClock native_module_clock;
ledgrid::EspNativeModuleWatchdog native_module_watchdog;
std::uint8_t native_module_scratch[4096] = {};
ledgrid::NativeModuleManager native_module_manager(
    &native_module_store, &native_module_persistence, &native_module_backend,
    &native_module_clock, native_module_scratch, sizeof(native_module_scratch),
    true);
ledgrid::NativeModuleOperationResultLatch native_operation_result_latch;
std::atomic<bool> native_module_ready{false};
#endif

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

bool is_native_module_command(std::uint8_t command) {
  return command >= static_cast<std::uint8_t>(
                        ledgrid::ReceiverCommand::NativeModuleProbe) &&
         command <= static_cast<std::uint8_t>(
                        ledgrid::ReceiverCommand::NativeModuleQuarantineClear);
}

ledgrid::NativeModuleResult native_dispatch_rejection(
    ledgrid::ReceiverOperationResult result) {
  switch (result) {
    case ledgrid::ReceiverOperationResult::Unsupported:
      return ledgrid::NativeModuleResult::Unsupported;
    case ledgrid::ReceiverOperationResult::InvalidSize:
      return ledgrid::NativeModuleResult::InvalidSize;
    case ledgrid::ReceiverOperationResult::InvalidState:
      return ledgrid::NativeModuleResult::InvalidState;
    case ledgrid::ReceiverOperationResult::InvalidCommand:
    default:
      return ledgrid::NativeModuleResult::InvalidCommand;
  }
}

void lock_runtime() {
  if (runtime_mutex != nullptr) xSemaphoreTake(runtime_mutex, portMAX_DELAY);
}

void unlock_runtime() {
  if (runtime_mutex != nullptr) xSemaphoreGive(runtime_mutex);
}

void lock_profile() {
  if (profile_mutex != nullptr) xSemaphoreTake(profile_mutex, portMAX_DELAY);
}

void unlock_profile() {
  if (profile_mutex != nullptr) xSemaphoreGive(profile_mutex);
}

void lock_native() {
  if (native_mutex != nullptr) xSemaphoreTake(native_mutex, portMAX_DELAY);
}

void unlock_native() {
  if (native_mutex != nullptr) xSemaphoreGive(native_mutex);
}

bool receiver_native_modules_available() {
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
  return native_module_ready.load(std::memory_order_acquire);
#else
  return false;
#endif
}

bool installation_profiles_available() {
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
  return installation_profile_ready.load(std::memory_order_acquire);
#else
  return false;
#endif
}

#if LEDGRID_ENABLE_INSTALLATION_PROFILES
bool profile_view_matches_output(
    const ledgrid::InstallationProfileViewV1& profile,
    const ledgrid::ReceiverOutputConfiguration& output) {
  return profile.encoded != nullptr && profile.category != nullptr &&
         profile.encoded_size == ledgrid::installation_profile_receiver_bytes_v1(
                                     output.strip_count,
                                     output.leds_per_strip) &&
         profile.strip_count == output.strip_count &&
         profile.leds_per_strip == output.leds_per_strip &&
         profile.pixel_count == output.total_leds();
}
#endif

#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
ledgrid::NativeModulePresentation native_presentation_snapshot() {
  ledgrid::NativeModulePresentation presentation{};
  presentation.vibe.struct_size = sizeof(ledgrid_native_vibe_v2);
  presentation.modifier_view.struct_size =
      sizeof(ledgrid_native_modifier_view_v2);
  presentation.modifier_view.entries = presentation.modifiers;
  presentation.profile_view.struct_size =
      sizeof(ledgrid_native_profile_view_v2);
  presentation.profile_view.sections = presentation.profile_sections;
  lock_runtime();
  const auto context = receiver_runtime.active_context();
  const auto output = receiver_output.configuration();
  const bool reverse_local_strip_order =
      receiver_runtime.local_parameters().reverse_local_strip_order;
  unlock_runtime();
  presentation.vibe.profile_version = context.vibe_profile_version;
  presentation.vibe.revision = context.vibe_revision;
  std::memcpy(presentation.vibe.palette, context.vibe_palette,
              sizeof(presentation.vibe.palette));
  presentation.vibe.tempo_q8_8 = context.tempo_q8_8;
  presentation.vibe.luminance_q8_8 = context.luminance_q8_8;
  presentation.vibe.chroma_q8_8 = context.chroma_q8_8;
  presentation.vibe.energy_q8_8 = context.energy_q8_8;
  for (std::uint8_t id = 1; id <= ledgrid::kPresentationModifierCount; ++id) {
    const std::uint16_t strength = context.modifier_strength_q8_8(id);
    if (strength == 0) continue;
    auto& entry = presentation.modifiers[presentation.modifier_view.count++];
    entry.id = id;
    entry.strength_q8_8 = strength;
  }
  presentation.profile_view.global_strips = kInstalledGlobalStrips;
  presentation.profile_view.local_strips = output.strip_count;
  presentation.profile_view.leds_per_strip = output.leds_per_strip;
  presentation.profile_view.global_strip_offset =
      configured_global_strip_offset.load(std::memory_order_relaxed);
  presentation.profile_view.reverse_local_strip_order =
      reverse_local_strip_order ? 1 : 0;
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
  lock_profile();
  const auto profile = installation_profile_manager.active_view();
  if (profile.encoded != nullptr) {
    presentation.profile_view.global_strips = profile.global_strip_count;
    presentation.profile_view.local_strips = profile.strip_count;
    presentation.profile_view.leds_per_strip = profile.leds_per_strip;
    presentation.profile_view.global_strip_offset = profile.strip_origin;
    presentation.profile_view.clearance_radius = profile.clearance_radius;
    presentation.profile_view.reverse_local_strip_order =
        profile.reversed_strip_order ? 1 : 0;
    const std::uint8_t* data[] = {
        profile.category, profile.clearance, profile.foliage_edge,
        profile.globe_edge, profile.obstacle_edge, profile.globe_region,
        profile.distance,
        reinterpret_cast<const std::uint8_t*>(profile.normal_x),
        reinterpret_cast<const std::uint8_t*>(profile.normal_y)};
    const std::uint8_t encodings[] = {1, 2, 2, 2, 2, 1, 3, 4, 4};
    for (std::uint8_t index = 0;
         index < LEDGRID_NATIVE_BACKGROUND_MAX_PROFILE_SECTIONS; ++index) {
      auto& section = presentation.profile_sections[index];
      section.id = static_cast<std::uint16_t>(index + 1U);
      section.encoding = encodings[index];
      section.element_width = 1;
      section.element_count = profile.pixel_count;
      section.data = data[index];
    }
    presentation.profile_view.section_count =
        LEDGRID_NATIVE_BACKGROUND_MAX_PROFILE_SECTIONS;
  }
  unlock_profile();
#endif
  return presentation;
}
#endif

ledgrid::FrameMailboxCounters mailbox_counters() {
  portENTER_CRITICAL(&mailbox_mux);
  const auto counters = frame_mailbox.counters();
  portEXIT_CRITICAL(&mailbox_mux);
  return counters;
}

// The caller holds runtime_mutex, making the output configuration and mailbox
// publication one coherent command-side snapshot.
bool publish_working_frame_locked(
    const ledgrid::ReceiverOutputConfiguration& output) {
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

struct PhysicalSubmitContext {
  const std::uint8_t* frame = nullptr;
  std::uint32_t sequence = 0;
};

bool submit_physical_frame(
    void* raw_context,
    const ledgrid::ReceiverOutputConfiguration& output) {
  const auto* context = static_cast<const PhysicalSubmitContext*>(raw_context);
  return context != nullptr && context->frame != nullptr &&
         led_driver.submit(
             context->frame, output.rgb_bytes(), output.strip_count,
             output.leds_per_strip, output.brightness, context->sequence);
}

void display_task(void*) {
  const std::uint64_t animation_started_us = esp_timer_get_time();
  while (true) {
    const std::uint64_t now_us = esp_timer_get_time();
    const std::uint8_t wanted_lane_mask =
        requested_lane_mask.load(std::memory_order_relaxed);
    if (wanted_lane_mask != led_driver.lane_mask() &&
        led_driver.set_lane_mask(wanted_lane_mask)) {
      applied_lane_mask = wanted_lane_mask;
    }
    const std::uint8_t wanted_stagger =
        requested_stagger_phases.load(std::memory_order_relaxed);
    if (wanted_stagger != led_driver.stagger_phases() &&
        led_driver.set_stagger_phases(wanted_stagger)) {
      applied_stagger_phases = wanted_stagger;
    }
    ledgrid::ReceiverRenderTicket ticket{};
    ledgrid::LocalBackgroundParameters parameters{};
    std::uint16_t luminance = ledgrid::kQ8_8One;
    std::uint16_t hue_shift = 0;
    std::uint64_t scene_time = 0;
    bool base_due = false;
    bool foreground_due = false;
    bool has_rendered_base = false;
    bool native_base = false;
    std::uint64_t native_frame_index = 0;
    lock_runtime();
    receiver_runtime.service_foreground(now_us);
    ticket = ledgrid::capture_render_ticket(receiver_runtime, receiver_output);
    if (ticket.owner == ledgrid::BaseMode::LocalBackground) {
      base_due = receiver_runtime.local_frame_due(now_us);
      foreground_due = receiver_runtime.foreground_refresh_pending();
      parameters = receiver_runtime.local_parameters();
      luminance = receiver_runtime.active_context().luminance_q8_8;
      hue_shift = receiver_runtime.active_modifier_strength_q8_8(
          ledgrid::kHueShiftModifierId);
      scene_time = receiver_runtime.scene_time_us(now_us);
      has_rendered_base = receiver_runtime.render_stats().rendered_frames != 0;
      native_base = parameters.component_id == UINT16_MAX;
      native_frame_index = receiver_runtime.render_stats().rendered_frames;
    }
    unlock_runtime();

    if (ticket.owner == ledgrid::BaseMode::StartupFallback) {
      if (!ledgrid::render_startup_rainbow(
            now_us - animation_started_us,
            ticket.output.strip_count,
            ticket.output.leds_per_strip,
            startup_frame,
            sizeof(startup_frame))) {
        lock_runtime();
        const bool current = ledgrid::render_ticket_still_current(
            receiver_runtime, receiver_output, ticket);
        unlock_runtime();
        if (current) ++display_errors;
        vTaskDelay(pdMS_TO_TICKS(10));
        continue;
      }
      const std::uint32_t sequence =
          next_sequence.fetch_add(1, std::memory_order_relaxed);
      PhysicalSubmitContext submit_context{startup_frame, sequence};
      lock_runtime();
      const auto submit_result = ledgrid::submit_rendered_frame_if_current(
          receiver_runtime, receiver_output, ticket, submit_physical_frame,
          &submit_context);
      unlock_runtime();
      if (submit_result == ledgrid::PhysicalSubmitResult::Stale) continue;
      if (submit_result == ledgrid::PhysicalSubmitResult::DriverRejected) {
        ++display_errors;
        continue;
      }
      const bool completed =
          led_driver.wait_for_done(pdMS_TO_TICKS(100));
      lock_runtime();
      const bool completion_current = ledgrid::render_ticket_still_current(
          receiver_runtime, receiver_output, ticket);
      unlock_runtime();
      if (completion_current) {
        if (completed) last_displayed_sequence = sequence;
        else ++display_errors;
      }
      continue;
    }

    if (ticket.owner == ledgrid::BaseMode::LocalBackground) {
      if (!base_due && !foreground_due) {
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(1));
        continue;
      }
      std::uint32_t render_us = 0;
      bool base_changed = false;
      if (base_due) {
        const std::uint64_t render_started = esp_timer_get_time();
        bool rendered = false;
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
        if (native_base) {
          ledgrid::NativeModuleRenderResult native_result{};
          lock_native();
          rendered = native_module_manager.render(
              scene_time, scene_time,
              native_frame_index,
              startup_frame, ticket.output.rgb_bytes(), &native_result);
          base_changed = rendered && native_result.changed;
          unlock_native();
        } else
#endif
        {
          rendered = ledgrid::render_compiled_rainbow(
              scene_time, parameters, luminance, ticket.output.strip_count,
              ticket.output.leds_per_strip, startup_frame, sizeof(startup_frame));
          base_changed = rendered;
        }
        render_us = static_cast<std::uint32_t>(
            esp_timer_get_time() - render_started);
        if (!rendered) {
          lock_runtime();
          const bool failure_applied = ledgrid::render_ticket_still_current(
              receiver_runtime, receiver_output, ticket) &&
              receiver_runtime.local_render_failed_if_current(
                  ticket.ownership_generation);
          unlock_runtime();
          if (failure_applied) ++display_errors;
          continue;
        }
      }
      if (base_due && !base_changed && !foreground_due) {
        lock_runtime();
        receiver_runtime.local_frame_rendered_if_current(
            ticket.ownership_generation, now_us, scene_time, render_us);
        unlock_runtime();
        continue;
      }
      if (!base_due && !has_rendered_base) {
        lock_runtime();
        receiver_runtime.request_local_refresh();
        unlock_runtime();
        continue;
      }
      const std::uint64_t composite_started = esp_timer_get_time();
      lock_runtime();
      const bool composed = ledgrid::render_ticket_still_current(
          receiver_runtime, receiver_output, ticket) &&
          receiver_runtime.composite_foreground(
              startup_frame, ticket.output.total_leds(), composite_frame,
              sizeof(composite_frame));
      if (composed) {
        receiver_runtime.foreground_composited(static_cast<std::uint32_t>(
            esp_timer_get_time() - composite_started));
      }
      unlock_runtime();
      if (!composed) continue;
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
      bool optic_succeeded = true;
      if (hue_shift > 0 && installation_profiles_available()) {
        lock_profile();
        const auto& profile = installation_profile_manager.active_view();
        if (profile_view_matches_output(profile, ticket.output)) {
          // The manager owns the backing bytes. Hold profile_mutex for the
          // complete in-place optic so activation/restore cannot replace the
          // view until this frame is finished with it.
          optic_succeeded = ledgrid::apply_hue_shift_q8_8(
              composite_frame, ticket.output.rgb_bytes(), profile, hue_shift);
        }
        unlock_profile();
      }
      if (!optic_succeeded) {
        lock_runtime();
        const bool failure_applied = ledgrid::render_ticket_still_current(
            receiver_runtime, receiver_output, ticket) &&
            receiver_runtime.local_render_failed_if_current(
                ticket.ownership_generation);
        unlock_runtime();
        if (failure_applied) ++display_errors;
        continue;
      }
#endif
      const std::uint32_t sequence =
          next_sequence.fetch_add(1, std::memory_order_relaxed);
      PhysicalSubmitContext submit_context{composite_frame, sequence};
      lock_runtime();
      const auto submit_result = ledgrid::submit_rendered_frame_if_current(
          receiver_runtime, receiver_output, ticket, submit_physical_frame,
          &submit_context);
      unlock_runtime();
      if (submit_result == ledgrid::PhysicalSubmitResult::Stale) continue;
      if (submit_result == ledgrid::PhysicalSubmitResult::DriverRejected) {
        lock_runtime();
        const bool failure_applied = ledgrid::render_ticket_still_current(
            receiver_runtime, receiver_output, ticket) &&
            receiver_runtime.local_render_failed_if_current(
                ticket.ownership_generation);
        unlock_runtime();
        if (failure_applied) ++display_errors;
        continue;
      }
      const bool completed =
          led_driver.wait_for_done(pdMS_TO_TICKS(100));
      lock_runtime();
      const bool completion_applied = ledgrid::render_ticket_still_current(
          receiver_runtime, receiver_output, ticket) &&
          (completed && base_due
               ? receiver_runtime.local_frame_rendered_if_current(
                     ticket.ownership_generation, now_us, scene_time, render_us)
               : completed
                     ? true
                     : receiver_runtime.local_render_failed_if_current(
                           ticket.ownership_generation));
      unlock_runtime();
      if (completion_applied) {
        if (completed) last_displayed_sequence = sequence;
        else ++display_errors;
      }
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

      lock_runtime();
      const auto current_output = receiver_output.configuration();
      const bool current =
          receiver_runtime.base_mode() == ledgrid::BaseMode::HostFullScene &&
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
      const auto completed_output = receiver_output.configuration();
      const bool completion_current =
          receiver_runtime.base_mode() == ledgrid::BaseMode::HostFullScene &&
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
  const auto output = receiver_output.configuration();
  status.active_strips = output.strip_count;
  status.lane_mask = applied_lane_mask.load(std::memory_order_relaxed);
  status.leds_per_strip = output.leds_per_strip;
  status.capabilities = ledgrid::kCapabilityStatusV3 |
                        ledgrid::kCapabilityExplicitBaseOwnership |
                        ledgrid::kCapabilityAlignedEnvelopeV1 |
                        ledgrid::kCapabilityFecEnvelopeV2 |
                        ledgrid::kCapabilityFecEnvelopeV3 |
                        ledgrid::kCapabilityFecEnvelopeV4 |
                        ledgrid::kCapabilityFecEnvelopeV5;
  if (receiver_runtime.local_background_enabled()) {
    status.capabilities |= ledgrid::kCapabilityStaticLocalBackground |
                           ledgrid::kCapabilityPresentationContextV1 |
                           ledgrid::kCapabilitySparseOverlayV1 |
                           ledgrid::kCapabilitySparseOverlayBatchV1;
  }
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
  if (installation_profile_ready.load(std::memory_order_acquire)) {
    status.capabilities |= ledgrid::kCapabilityInstallationProfileV1 |
                           ledgrid::kCapabilityStatusV5;
  }
#endif
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
  if (receiver_native_modules_available()) {
    status.capabilities |= ledgrid::kCapabilityStatusV6 |
        ledgrid::kCapabilityNativeModuleV2 |
        ledgrid::kCapabilityNativeModuleCacheV1 |
        ledgrid::kCapabilityNativeTypedParametersV1 |
        ledgrid::kCapabilityNativeQuarantineV1 |
        ledgrid::kCapabilityNativeGuardedLoaderV1;
  }
#endif
  status.base_mode = static_cast<std::uint8_t>(receiver_runtime.base_mode());
  status.foreground_state =
      static_cast<std::uint8_t>(receiver_runtime.foreground_state());
  status.maintenance_state =
      static_cast<std::uint8_t>(receiver_runtime.maintenance_state());
  status.transition_reason = receiver_runtime.transition_reason();
  status.last_result = receiver_runtime.last_result();
  status.context_state = receiver_runtime.context_state();
  const auto& local = receiver_runtime.local_parameters();
  status.component_id = local.component_id;
  status.preferred_cadence_hz = local.preferred_cadence_hz;
  // CONFIG topology is independent of local/native execution.  Expose the
  // installed origin even in production host-takeover mode so status-v3 can
  // prove the exact heterogeneous receiver roster after a flash.
  status.global_strip_offset =
      configured_global_strip_offset.load(std::memory_order_relaxed);
  status.common_seed = local.common_seed;
  status.scene_epoch = local.scene_epoch;
  const auto& context = receiver_runtime.active_context();
  status.luminance_q8_8 = context.luminance_q8_8;
  status.active_context_scene_revision = context.scene_revision;
  status.active_vibe_revision = context.vibe_revision;
  status.active_modifier_revision = context.modifier_revision;
  std::memcpy(status.active_context_digest, context.context_digest, 32);
  std::memcpy(status.active_vibe_digest, context.vibe_digest, 32);
  std::memcpy(status.active_modifier_digest, context.modifier_digest, 32);
  std::memcpy(status.active_controller_session, context.session, 16);
  status.staged_context_scene_revision =
      receiver_runtime.staged_context_scene_revision();
  std::memcpy(status.staged_context_digest,
              receiver_runtime.staged_context_digest(), 32);
  std::memcpy(status.staged_controller_session,
              receiver_runtime.staged_controller_session(), 16);
  const auto& stats = receiver_runtime.render_stats();
  status.cadence_deadlines = stats.cadence_deadlines;
  status.rendered_frames = stats.rendered_frames;
  status.missed_cadence = stats.missed_cadence;
  status.last_render_us = stats.last_render_us;
  status.max_render_us = stats.max_render_us;
  status.last_frame_scene_time_us = stats.last_frame_scene_time_us;
  status.last_processed_command = operation_tracker.last_processed_command();
  status.operation_sequence = operation_tracker.sequence();
  const auto overlay = receiver_runtime.overlay_status(
      static_cast<std::uint64_t>(esp_timer_get_time()));
  status.overlay_result = overlay.result;
  status.overlay_update_kind = overlay.update_kind;
  status.overlay_expected_patches = overlay.expected_patches;
  status.overlay_accepted_patches = overlay.accepted_patches;
  status.overlay_committed_coverage_pixels = overlay.committed_coverage_pixels;
  status.overlay_committed_generation = overlay.committed_generation;
  status.overlay_staged_generation = overlay.staged_generation;
  status.foreground_scene_revision = overlay.scene_revision;
  status.foreground_scene_epoch = overlay.scene_epoch;
  status.foreground_base_revision = overlay.base_revision;
  status.foreground_present_at_scene_time_us =
      overlay.present_at_scene_time_us;
  status.overlay_lease_ms = overlay.lease_ms;
  status.overlay_lease_remaining_ms = overlay.lease_remaining_ms;
  std::memcpy(status.overlay_session, overlay.session,
              ledgrid::kControllerSessionBytes);
  const auto& overlay_stats = receiver_runtime.overlay_stats();
  status.overlay_composite_frames = overlay_stats.composite_frames;
  status.overlay_last_composite_us = overlay_stats.last_composite_us;
  status.overlay_max_composite_us = overlay_stats.max_composite_us;
  status.overlay_commits = overlay_stats.commits;
  status.overlay_expirations = overlay_stats.expirations;
  unlock_runtime();
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
  lock_profile();
  status.installation_profile = installation_profile_manager.status();
  unlock_profile();
#endif
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
  lock_native();
  status.native_module = native_module_manager.status();
  native_operation_result_latch.apply(
      status.operation_sequence, status.last_processed_command,
      &status.native_module);
  unlock_native();
#endif
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

bool queue_spi_transaction(
    std::size_t index, bool status_v4 = false, bool status_v5 = false,
    bool status_v6 = false, bool status_v7 = false) {
  const auto status = status_snapshot();
  if (status_v7) {
    ledgrid::encode_receiver_status_v7(
        status, spi_tx_buffers[index], kSpiBufferSize);
  } else if (status_v6 && LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES != 0) {
    ledgrid::encode_receiver_status_v6(
        status, spi_tx_buffers[index], kSpiBufferSize);
  } else if (status_v5 && LEDGRID_ENABLE_INSTALLATION_PROFILES != 0) {
    ledgrid::encode_receiver_status_v5(
        status, spi_tx_buffers[index], kSpiBufferSize);
  } else if (status_v4 && receiver_runtime.local_background_enabled()) {
    ledgrid::encode_receiver_status_v4(
        status, spi_tx_buffers[index], kSpiBufferSize);
  } else {
    ledgrid::encode_receiver_status_v3(
        status, spi_tx_buffers[index], kSpiBufferSize);
  }
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

bool process_command(
    const std::uint8_t* data, std::size_t length,
    ledgrid::NativeModuleResult* native_result = nullptr) {
  if (data == nullptr || length == 0) return false;
  if (native_result != nullptr) {
    *native_result = ledgrid::NativeModuleResult::None;
  }
  const auto command = static_cast<ledgrid::ReceiverCommand>(data[0]);
  ledgrid::ReceiverOutputConfiguration output{};
  ledgrid::BaseMode current_mode = ledgrid::BaseMode::StartupFallback;
  lock_runtime();
  output = receiver_output.configuration();
  current_mode = receiver_runtime.base_mode();
  unlock_runtime();
  const ledgrid::ReceiverDispatchDecision decision =
      ledgrid::classify_receiver_dispatch(
          data, length, output.rgb_bytes(), current_mode,
          LEDGRID_ENABLE_LOCAL_BACKGROUND != 0,
          installation_profiles_available(),
          receiver_native_modules_available());

  if (decision.route == ledgrid::ReceiverDispatchRoute::Reject) {
    if (native_result != nullptr && is_native_module_command(data[0])) {
      *native_result = native_dispatch_rejection(decision.result);
    }
    if (command != ledgrid::ReceiverCommand::StatusQuery) {
      lock_runtime();
      receiver_runtime.set_last_result(decision.result);
      unlock_runtime();
    }
    return false;
  }

  if (decision.route == ledgrid::ReceiverDispatchRoute::StatusQuery)
    return ledgrid::valid_status_query(
        data, length, LEDGRID_ENABLE_LOCAL_BACKGROUND != 0,
        installation_profiles_available(),
        receiver_native_modules_available());
  if (decision.route == ledgrid::ReceiverDispatchRoute::InstallationProfile) {
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
    ledgrid::InstallationProfileBinding prior_active{};
    ledgrid::InstallationProfileBinding current_active{};
    lock_profile();
    prior_active = installation_profile_manager.ledger().active;
    const auto result = installation_profile_manager.process(data, length);
    current_active = installation_profile_manager.ledger().active;
    unlock_profile();
    const bool active_binding_changed =
        result == ledgrid::InstallationProfileResult::Ok &&
        ledgrid::installation_profile_command_may_change_active_binding(command) &&
        !ledgrid::installation_profile_binding_equal(
            prior_active, current_active);
    if (active_binding_changed) {
      // Never nest profile_mutex and runtime_mutex. The active binding is
      // already durable; invalidating under runtime_mutex is the operation's
      // display linearization point before this command returns.
      lock_runtime();
      const bool invalidated =
          receiver_runtime.invalidate_local_presentation_for_profile_change();
      unlock_runtime();
      if (invalidated && display_task_handle != nullptr) {
        xTaskNotifyGive(display_task_handle);
      }
    }
    return result == ledgrid::InstallationProfileResult::Ok;
#else
    return false;
#endif
  }
  if (decision.route == ledgrid::ReceiverDispatchRoute::NativeModule) {
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
    const auto presentation = native_presentation_snapshot();
    lock_runtime();
    const auto context_state = receiver_runtime.context_state();
    const auto scene_epoch = receiver_runtime.active_context().scene_epoch;
    unlock_runtime();
    lock_native();
    native_module_manager.configure_presentation(presentation);
    native_module_manager.configure_scene(
        context_state == ledgrid::PresentationContextState::Active,
        scene_epoch);
    const auto result = native_module_manager.process(data, length);
    if (native_result != nullptr) *native_result = result;
    const bool executing = native_module_manager.active();
    const auto ledger = native_module_manager.ledger();
    unlock_native();
    if (result == ledgrid::NativeModuleResult::Ok &&
        command == ledgrid::ReceiverCommand::NativeModuleActivate &&
        ledger.active.present) {
      const std::uint64_t activation_epoch =
          (static_cast<std::uint64_t>(data[73]) << 56U) |
          (static_cast<std::uint64_t>(data[74]) << 48U) |
          (static_cast<std::uint64_t>(data[75]) << 40U) |
          (static_cast<std::uint64_t>(data[76]) << 32U) |
          (static_cast<std::uint64_t>(data[77]) << 24U) |
          (static_cast<std::uint64_t>(data[78]) << 16U) |
          (static_cast<std::uint64_t>(data[79]) << 8U) | data[80];
      const std::uint32_t seed =
          (static_cast<std::uint32_t>(data[81]) << 24U) |
          (static_cast<std::uint32_t>(data[82]) << 16U) |
          (static_cast<std::uint32_t>(data[83]) << 8U) | data[84];
      lock_runtime();
      const bool started = receiver_runtime.native_background_started(
          ledger.active.descriptor.cadence_hz,
          ledger.active.descriptor.global_strip_offset, seed,
          activation_epoch);
      unlock_runtime();
      if (!started) {
        const std::uint8_t stop[] = {
            static_cast<std::uint8_t>(ledgrid::ReceiverCommand::NativeModuleStop)};
        lock_native();
        native_module_manager.process(stop, sizeof(stop));
        unlock_native();
        if (native_result != nullptr) {
          *native_result = ledgrid::NativeModuleResult::InvalidState;
        }
        return false;
      }
    } else if (command == ledgrid::ReceiverCommand::NativeModuleStop ||
               !executing) {
      lock_runtime();
      receiver_runtime.native_background_stopped(
          result != ledgrid::NativeModuleResult::Ok);
      unlock_runtime();
    }
    if (display_task_handle != nullptr) xTaskNotifyGive(display_task_handle);
    return result == ledgrid::NativeModuleResult::Ok;
#else
    return false;
#endif
  }
  if (decision.route == ledgrid::ReceiverDispatchRoute::Runtime) {
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
    if (command == ledgrid::ReceiverCommand::LocalBackgroundStart ||
        command == ledgrid::ReceiverCommand::LocalBackgroundStop) {
      lock_native();
      native_module_manager.host_takeover();
      unlock_native();
    }
#endif
    ledgrid::ReceiverOperationResult result =
        ledgrid::ReceiverOperationResult::Unsupported;
    lock_runtime();
    if (logical_receiver_id.load(std::memory_order_relaxed) != 0xFF) {
      result = receiver_runtime.process_command(
          data, length, static_cast<std::uint64_t>(esp_timer_get_time()));
    } else {
      receiver_runtime.set_last_result(result);
    }
    unlock_runtime();
    if (display_task_handle != nullptr) xTaskNotifyGive(display_task_handle);
    return result == ledgrid::ReceiverOperationResult::Ok;
  }

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
      const bool updated = receiver_output.set_brightness(data[1]);
      receiver_runtime.request_local_refresh();
      if (updated &&
          receiver_runtime.base_mode() == ledgrid::BaseMode::HostFullScene) {
        publish_working_frame_locked(receiver_output.configuration());
      }
      unlock_runtime();
      if (display_task_handle != nullptr) xTaskNotifyGive(display_task_handle);
      return updated;
    }

    case ledgrid::ReceiverCommand::Show: {
      if (length != 1) return false;
      lock_runtime();
      if (receiver_runtime.base_mode() == ledgrid::BaseMode::HostFullScene) {
        publish_working_frame_locked(receiver_output.configuration());
      }
      unlock_runtime();
      return true;
    }

    case ledgrid::ReceiverCommand::Clear: {
      if (length != 1) return false;
      lock_runtime();
      const auto current_output = receiver_output.configuration();
      std::memset(working_frame, 0, current_output.rgb_bytes());
      if (receiver_runtime.base_mode() == ledgrid::BaseMode::HostFullScene) {
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
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
      lock_native();
      native_module_manager.host_takeover();
      unlock_native();
#endif
      lock_runtime();
      const auto current_output = receiver_output.configuration();
      if (current_output.rgb_bytes() != output.rgb_bytes()) {
        unlock_runtime();
        return false;
      }
      std::memcpy(working_frame, data + 1, current_output.rgb_bytes());
      if (!publish_working_frame_locked(current_output)) {
        unlock_runtime();
        return false;
      }
      receiver_runtime.complete_host_frame();
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
      std::uint8_t new_logical_id = 0xFF;
      std::uint16_t new_global_offset = 0;
      if (!ledgrid::parse_receiver_topology(
              data, length,
              logical_receiver_id.load(std::memory_order_relaxed),
              configured_global_strip_offset.load(std::memory_order_relaxed),
              &new_logical_id, &new_global_offset)) return false;
      const std::uint8_t new_strips = data[1];
      const std::uint16_t new_leds =
          (static_cast<std::uint16_t>(data[2]) << 8) | data[3];
      // Six- and eight-byte CONFIG carry installed direction. Only the
      // eight-byte form carries an authoritative global strip offset.
      // Legacy four/five-byte CONFIG preserves the provisioned direction.
      const bool has_installed_direction = length == 6 || length == 8;
      const bool has_explicit_topology = length == 8;
      const bool reverse_local_strip_order =
          has_installed_direction && (data[4] & 0x80U) != 0;
      if (new_strips == 0 || new_strips > kMaxStrips || new_leds == 0 ||
          new_leds > kMaxLedsPerStrip ||
          (has_explicit_topology &&
           (new_global_offset > kInstalledGlobalStrips ||
            new_strips > kInstalledGlobalStrips - new_global_offset))) {
        return false;
      }
      lock_runtime();
      const auto prior_output = receiver_output.configuration();
      const bool configured = receiver_output.configure(new_strips, new_leds);
      if (configured &&
          (new_strips != prior_output.strip_count ||
           new_leds != prior_output.leds_per_strip)) {
        std::memset(working_frame, 0, sizeof(working_frame));
      }
      if (has_installed_direction) {
        receiver_runtime.set_reverse_local_strip_order(
            reverse_local_strip_order);
      } else {
        receiver_runtime.request_local_refresh();
      }
      const bool effective_reverse_local_strip_order =
          receiver_runtime.local_parameters().reverse_local_strip_order;
      unlock_runtime();
      if (!configured) return false;
      logical_receiver_id.store(new_logical_id, std::memory_order_release);
      if (has_explicit_topology) {
        configured_global_strip_offset.store(
            new_global_offset, std::memory_order_release);
        explicit_receiver_topology.store(true, std::memory_order_release);
      }
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
      const bool topology_is_configured =
          has_explicit_topology ||
          explicit_receiver_topology.load(std::memory_order_acquire);
      ledgrid::NativeModuleTopology native_topology{};
      native_topology.configured = topology_is_configured;
      native_topology.logical_receiver_id = new_logical_id;
      native_topology.global_strips = kInstalledGlobalStrips;
      native_topology.local_strips = new_strips;
      native_topology.leds_per_strip = new_leds;
      native_topology.global_strip_offset = new_global_offset;
      native_topology.reverse_local_strip_order =
          effective_reverse_local_strip_order;
      lock_native();
      native_module_manager.configure_topology(native_topology);
      unlock_native();
#endif
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
      if (has_installed_direction) {
        const bool topology_is_configured =
            has_explicit_topology ||
            explicit_receiver_topology.load(std::memory_order_acquire);
        lock_profile();
        installation_profile_manager.configure_identity(
            new_logical_id, effective_reverse_local_strip_order,
            topology_is_configured
                ? kInstalledGlobalStrips
                : ledgrid::kInstallationProfileGlobalStripsV1,
            new_strips, new_leds,
            topology_is_configured ? new_global_offset : UINT16_MAX);
        unlock_profile();
      }
#endif
      if (display_task_handle != nullptr) xTaskNotifyGive(display_task_handle);
      return true;
    }

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

  runtime_mutex = xSemaphoreCreateMutex();
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
  profile_mutex = xSemaphoreCreateMutex();
#endif
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
  native_mutex = xSemaphoreCreateMutex();
#endif
  if (runtime_mutex == nullptr
#if LEDGRID_ENABLE_INSTALLATION_PROFILES
      || profile_mutex == nullptr
#endif
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
      || native_mutex == nullptr
#endif
  ) {
    ESP_LOGE(kLogTag, "receiver runtime mutex allocation failed");
    while (true) vTaskDelay(pdMS_TO_TICKS(1000));
  }

#if LEDGRID_ENABLE_INSTALLATION_PROFILES
  const bool store_ready = installation_profile_store.begin();
  const bool persistence_ready = installation_profile_persistence.begin();
  const bool manager_ready = installation_profile_manager.begin();
  const bool profiles_ready = store_ready && persistence_ready && manager_ready;
  installation_profile_ready.store(profiles_ready, std::memory_order_release);
  if (!profiles_ready) {
    ESP_LOGE(kLogTag, "installation-profile cache initialization failed");
  }
#endif

#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
  const bool native_store_ready = native_module_store.begin();
  const bool native_persistence_ready = native_module_persistence.begin();
  native_module_manager.set_watchdog(&native_module_watchdog);
  const bool native_manager_ready = native_module_manager.begin();
  const bool modules_ready = native_store_ready && native_persistence_ready &&
                             native_manager_ready;
  native_module_ready.store(modules_ready, std::memory_order_release);
  if (!modules_ready) {
    ESP_LOGE(kLogTag, "native-module cache/loader initialization failed");
  }
#endif

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

  ESP_LOGI(kLogTag, "LED Grid ESP32-S3 parallel receiver v3");
  initialize_spi();
  lock_runtime();
  const auto initial_output = receiver_output.configuration();
  unlock_runtime();
  ESP_LOGI(kLogTag,
      "Ready: %u strips x %u LEDs, SPI queue=%u, encoded frame=%u bytes",
      initial_output.strip_count,
      initial_output.leds_per_strip,
      static_cast<unsigned>(kSpiQueueDepth),
      static_cast<unsigned>(
          ledgrid::ws2812_encoded_size(initial_output.leds_per_strip)));
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
    bool request_v4 = false;
    bool request_v5 = false;
    bool request_v6 = false;
    bool request_v7 = false;

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
        const bool status_query =
            command[0] == static_cast<std::uint8_t>(
                              ledgrid::ReceiverCommand::StatusQuery);
        bool dispatch_allowed = true;
        std::uint32_t operation_sequence = 0;
        if (!status_query) {
          lock_runtime();
          dispatch_allowed = operation_tracker.begin(command[0]);
          operation_sequence = operation_tracker.sequence();
          unlock_runtime();
        }
        ledgrid::NativeModuleResult native_result =
            ledgrid::NativeModuleResult::None;
        const bool accepted = dispatch_allowed &&
            process_command(command, payload_bytes, &native_result);
#if LEDGRID_ENABLE_RECEIVER_NATIVE_MODULES
        if (dispatch_allowed && is_native_module_command(command[0])) {
          // Rendering runs on the display task and may update live failure
          // telemetry while the SPI queue drains. Preserve the exact result of
          // this operation alongside the sequence/command acknowledgement.
          lock_native();
          native_operation_result_latch.record(
              operation_sequence, command[0], native_result);
          unlock_native();
        }
#endif
        request_v4 = status_query && accepted &&
            payload_bytes == ledgrid::kStatusBytesV4;
        request_v5 = status_query && accepted &&
            payload_bytes == ledgrid::kStatusBytesV5;
        request_v6 = status_query && accepted &&
            payload_bytes == ledgrid::kStatusBytesV6;
        request_v7 = status_query && accepted &&
            payload_bytes == ledgrid::kStatusBytesV7;
        if (!status_query && dispatch_allowed) {
          lock_runtime();
          if (command[0] < 0x10 || command[0] == 0xFF) {
            receiver_runtime.set_last_result(
                accepted ? ledgrid::ReceiverOperationResult::Ok
                         : ledgrid::ReceiverOperationResult::InvalidCommand);
          }
          unlock_runtime();
        }
      }
    }
    queue_spi_transaction(
        index, request_v4, request_v5, request_v6, request_v7);
  }
}
