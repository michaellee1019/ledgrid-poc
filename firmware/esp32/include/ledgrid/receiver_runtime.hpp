#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/animation_pipeline_contract.hpp"
#include "ledgrid/protocol.hpp"

namespace ledgrid {

constexpr std::uint16_t kCompiledRainbowComponentId = 1;
constexpr std::uint16_t kMinLocalCadenceHz = 1;
constexpr std::uint16_t kMaxLocalCadenceHz = 200;
constexpr std::size_t kLocalBackgroundStartBytes = 21;
constexpr std::size_t kLocalBackgroundStopBytes = 1;
constexpr std::size_t kLocalBackgroundParametersBytes = 11;

constexpr std::uint8_t kPresentationContextVersion = 1;
constexpr std::size_t kPresentationContextBeginBytes = 58;
constexpr std::size_t kPresentationContextSetBaseBytes = 145;
constexpr std::size_t kPresentationContextSetEntryBytes = 3;
constexpr std::size_t kPresentationContextSetMaxBytes = 187;
constexpr std::size_t kPresentationContextCommitBytes = 74;
constexpr std::uint16_t kQ8_8One = 256;

struct LocalBackgroundParameters {
  std::uint16_t component_id = kCompiledRainbowComponentId;
  std::uint16_t preferred_cadence_hz = 30;
  std::uint32_t global_strip_offset = 0;
  std::uint32_t common_seed = 0;
  std::uint64_t scene_epoch = 0;
};

struct LocalRenderStats {
  std::uint32_t cadence_deadlines = 0;
  std::uint32_t rendered_frames = 0;
  std::uint32_t missed_cadence = 0;
  std::uint16_t last_render_us = 0;
  std::uint16_t max_render_us = 0;
  std::uint64_t last_frame_scene_time_us = 0;
};

struct PresentationContext {
  std::uint8_t session[16] = {};
  std::uint64_t scene_revision = 0;
  std::uint64_t scene_epoch = 0;
  std::uint64_t present_at_scene_time_us = 0;
  std::uint8_t vibe_id = 0;
  std::uint32_t vibe_profile_version = 0;
  std::uint64_t vibe_revision = 0;
  std::uint16_t luminance_q8_8 = kQ8_8One;
  std::uint64_t modifier_revision = 0;
  std::uint8_t context_digest[32] = {};
  std::uint8_t vibe_digest[32] = {};
  std::uint8_t modifier_digest[32] = {};
};

struct ReceiverOutputConfiguration {
  std::uint8_t strip_count = 0;
  std::uint16_t leds_per_strip = 0;
  std::uint8_t brightness = 0;

  std::size_t total_leds() const {
    return static_cast<std::size_t>(strip_count) * leds_per_strip;
  }
  std::size_t rgb_bytes() const { return total_leds() * 3U; }
};

// This portable state is guarded by the same runtime mutex as ReceiverRuntime
// in production. Its revision invalidates a frame rendered against old
// geometry or brightness before that frame can reach the physical driver.
class ReceiverOutputState {
 public:
  ReceiverOutputState(
      std::uint8_t strip_count,
      std::uint16_t leds_per_strip,
      std::uint8_t brightness)
      : configuration_{strip_count, leds_per_strip, brightness} {}

  const ReceiverOutputConfiguration& configuration() const {
    return configuration_;
  }
  std::uint64_t revision() const { return revision_; }
  bool configure(std::uint8_t strip_count, std::uint16_t leds_per_strip);
  bool set_brightness(std::uint8_t brightness);

 private:
  bool advance_revision();

  ReceiverOutputConfiguration configuration_{};
  std::uint64_t revision_ = 0;
};

struct ReceiverRenderTicket {
  BaseMode owner = BaseMode::StartupFallback;
  std::uint32_t ownership_generation = 0;
  std::uint64_t output_revision = 0;
  ReceiverOutputConfiguration output{};
};

enum class PhysicalSubmitResult : std::uint8_t {
  Stale = 0,
  Submitted = 1,
  DriverRejected = 2,
};

using PhysicalSubmitCallback = bool (*)(
    void* context, const ReceiverOutputConfiguration& output);

class ReceiverRuntime {
 public:
  explicit ReceiverRuntime(bool local_background_enabled);

  BaseMode base_mode() const { return base_mode_; }
  ForegroundState foreground_state() const { return foreground_state_; }
  MaintenanceState maintenance_state() const { return maintenance_state_; }
  BaseTransitionReason transition_reason() const { return transition_reason_; }
  ReceiverOperationResult last_result() const { return last_result_; }
  PresentationContextState context_state() const { return context_state_; }
  bool local_background_enabled() const { return local_background_enabled_; }
  const LocalBackgroundParameters& local_parameters() const { return local_; }
  const LocalRenderStats& render_stats() const { return render_stats_; }
  const PresentationContext& active_context() const { return active_context_; }
  std::uint64_t staged_context_scene_revision() const {
    return staged_context_.scene_revision;
  }
  const std::uint8_t* staged_context_digest() const {
    return staged_context_.context_digest;
  }
  const std::uint8_t* staged_controller_session() const {
    return staged_context_.session;
  }

  ReceiverOperationResult process_command(
      const std::uint8_t* command,
      std::size_t size,
      std::uint64_t local_monotonic_us = 0);
  void complete_host_frame();
  void receiver_restart();
  bool local_render_failed_if_current(std::uint32_t generation);

  bool local_frame_due(std::uint64_t now_us);
  bool local_frame_rendered_if_current(
      std::uint32_t generation,
      std::uint64_t local_monotonic_us,
      std::uint64_t frame_scene_time_us,
      std::uint32_t render_us);
  void request_local_refresh();
  std::uint64_t scene_time_us(std::uint64_t local_monotonic_us) const;
  std::uint32_t render_generation() const { return render_generation_; }
  bool local_render_still_valid(std::uint32_t generation) const {
    return base_mode_ == BaseMode::LocalBackground &&
           render_generation_ == generation;
  }
  void set_last_result(ReceiverOperationResult result) { last_result_ = result; }

 private:
  ReceiverOperationResult finish(ReceiverOperationResult result);
  ReceiverOperationResult start_local(const std::uint8_t* command, std::size_t size);
  ReceiverOperationResult update_local(const std::uint8_t* command, std::size_t size);
  ReceiverOperationResult context_begin(const std::uint8_t* command, std::size_t size);
  ReceiverOperationResult context_set(const std::uint8_t* command, std::size_t size);
  ReceiverOperationResult context_commit(const std::uint8_t* command, std::size_t size);

  bool local_background_enabled_ = false;
  BaseMode base_mode_ = BaseMode::StartupFallback;
  ForegroundState foreground_state_ = ForegroundState::Cleared;
  MaintenanceState maintenance_state_ = MaintenanceState::Inactive;
  BaseTransitionReason transition_reason_ = BaseTransitionReason::Boot;
  ReceiverOperationResult last_result_ = ReceiverOperationResult::None;
  PresentationContextState context_state_ = PresentationContextState::None;
  LocalBackgroundParameters local_{};
  LocalRenderStats render_stats_{};
  PresentationContext staged_context_{};
  PresentationContext active_context_{};
  bool active_context_present_ = false;
  std::uint64_t cadence_origin_us_ = 0;
  std::uint64_t next_cadence_index_ = 0;
  bool cadence_initialized_ = false;
  bool force_local_refresh_ = false;
  std::uint64_t context_committed_local_us_ = 0;
  std::uint32_t render_generation_ = 0;
};

std::uint8_t apply_luminance_q8_8(
    std::uint8_t channel, std::uint16_t factor_q8_8);

bool render_compiled_rainbow(
    std::uint64_t elapsed_us,
    const LocalBackgroundParameters& parameters,
    std::uint16_t luminance_q8_8,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_size);

ReceiverRenderTicket capture_render_ticket(
    const ReceiverRuntime& runtime, const ReceiverOutputState& output);
bool render_ticket_still_current(
    const ReceiverRuntime& runtime,
    const ReceiverOutputState& output,
    const ReceiverRenderTicket& ticket);
// Production calls this while holding the shared runtime mutex. The callback
// must enqueue only; waiting for DMA completion happens after releasing it.
PhysicalSubmitResult submit_rendered_frame_if_current(
    const ReceiverRuntime& runtime,
    const ReceiverOutputState& output,
    const ReceiverRenderTicket& ticket,
    PhysicalSubmitCallback submit,
    void* submit_context);

}  // namespace ledgrid
