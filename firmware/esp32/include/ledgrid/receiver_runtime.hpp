#pragma once

#include <cstddef>
#include <cstdint>

#include "ledgrid/animation_pipeline_contract.hpp"
#include "ledgrid/protocol.hpp"

#ifndef LEDGRID_ENABLE_LOCAL_BACKGROUND
#define LEDGRID_ENABLE_LOCAL_BACKGROUND 0
#endif

namespace ledgrid {

// Receiver boot must remain physically dark until the host explicitly applies
// the persisted operator brightness. The compiled fallback renderer remains
// available, but a flash/reboot cannot create an uncommanded visible frame.
constexpr std::uint8_t kReceiverSafeBootBrightness = 0;

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
constexpr std::uint8_t kPresentationModifierCount = 14;
constexpr std::uint8_t kHueShiftModifierId = 4;
static_assert(
    kPresentationContextSetMaxBytes ==
        kPresentationContextSetBaseBytes +
            kPresentationModifierCount * kPresentationContextSetEntryBytes,
    "presentation modifier wire bound drifted");

struct LocalBackgroundParameters {
  std::uint16_t component_id = kCompiledRainbowComponentId;
  std::uint16_t preferred_cadence_hz = 30;
  std::uint32_t global_strip_offset = 0;
  std::uint32_t common_seed = 0;
  std::uint64_t scene_epoch = 0;
  // Installed receivers may be physically connected with local strip 7 at
  // the left edge. This is configured out-of-band from the stable animation
  // parameter wire contract and affects only receiver-native rendering.
  bool reverse_local_strip_order = false;
};

struct LocalRenderStats {
  std::uint32_t cadence_deadlines = 0;
  std::uint32_t rendered_frames = 0;
  std::uint32_t missed_cadence = 0;
  std::uint16_t last_render_us = 0;
  std::uint16_t max_render_us = 0;
  std::uint64_t last_frame_scene_time_us = 0;
};

struct SparseOverlayStats {
  std::uint32_t composite_frames = 0;
  std::uint16_t last_composite_us = 0;
  std::uint16_t max_composite_us = 0;
  std::uint32_t commits = 0;
  std::uint32_t expirations = 0;
};

struct SparseOverlayStatus {
  OverlayOperationResult result = OverlayOperationResult::None;
  OverlayUpdateKind update_kind = OverlayUpdateKind::FullSnapshot;
  std::uint16_t expected_patches = 0;
  std::uint16_t accepted_patches = 0;
  std::uint16_t committed_coverage_pixels = 0;
  std::uint64_t committed_generation = 0;
  std::uint64_t staged_generation = 0;
  std::uint64_t scene_revision = 0;
  std::uint64_t scene_epoch = 0;
  std::uint64_t base_revision = 0;
  std::uint64_t present_at_scene_time_us = 0;
  std::uint32_t lease_ms = 0;
  std::uint32_t lease_remaining_ms = 0;
  std::uint8_t session[kControllerSessionBytes] = {};
};

struct PresentationContext {
  std::uint8_t session[16] = {};
  std::uint64_t scene_revision = 0;
  std::uint64_t scene_epoch = 0;
  std::uint64_t present_at_scene_time_us = 0;
  std::uint8_t vibe_id = 0;
  std::uint32_t vibe_profile_version = 0;
  std::uint64_t vibe_revision = 0;
  std::uint8_t vibe_palette[LEDGRID_NATIVE_BACKGROUND_PALETTE_ROLES][3] = {};
  std::uint16_t tempo_q8_8 = kQ8_8One;
  std::uint16_t luminance_q8_8 = kQ8_8One;
  std::uint16_t chroma_q8_8 = kQ8_8One;
  std::uint16_t energy_q8_8 = kQ8_8One;
  std::uint64_t modifier_revision = 0;
  std::uint8_t context_digest[32] = {};
  std::uint8_t vibe_digest[32] = {};
  std::uint8_t modifier_digest[32] = {};
  // Wire IDs are frozen, one-based, and sparse in a resolved context. Index
  // zero is deliberately unused so malformed/unknown IDs safely read as off.
  std::uint16_t modifier_strengths_q8_8[kPresentationModifierCount + 1U] = {};

  std::uint16_t modifier_strength_q8_8(std::uint8_t modifier_id) const {
    return modifier_id >= 1U && modifier_id <= kPresentationModifierCount
        ? modifier_strengths_q8_8[modifier_id]
        : 0U;
  }
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
  std::uint32_t foreground_revision = 0;
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
  const SparseOverlayStats& overlay_stats() const { return overlay_stats_; }
  OverlayOperationResult last_overlay_result() const {
    return last_overlay_result_;
  }
  SparseOverlayStatus overlay_status(std::uint64_t local_monotonic_us) const;
  const PresentationContext& active_context() const { return active_context_; }
  std::uint16_t active_modifier_strength_q8_8(
      std::uint8_t modifier_id) const {
    return active_context_.modifier_strength_q8_8(modifier_id);
  }
  std::uint16_t staged_modifier_strength_q8_8(
      std::uint8_t modifier_id) const {
    return staged_context_.modifier_strength_q8_8(modifier_id);
  }
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
  bool native_background_started(std::uint16_t cadence_hz,
                                 std::uint32_t global_strip_offset,
                                 std::uint32_t common_seed,
                                 std::uint64_t scene_epoch);
  void native_background_stopped(bool failed);
  void receiver_restart();
  bool local_render_failed_if_current(std::uint32_t generation);

  bool local_frame_due(std::uint64_t now_us);
  bool local_frame_rendered_if_current(
      std::uint32_t generation,
      std::uint64_t local_monotonic_us,
      std::uint64_t frame_scene_time_us,
      std::uint32_t render_us);
  void request_local_refresh();
  // Called only after an active installation-profile binding changes. This
  // invalidates in-flight presentation work when the profile-dependent optic
  // is live, without changing ownership, context, cadence, or foreground.
  bool invalidate_local_presentation_for_profile_change();
  void set_reverse_local_strip_order(bool reversed) {
    local_.reverse_local_strip_order = reversed;
    request_local_refresh();
  }
  bool service_foreground(std::uint64_t local_monotonic_us);
  bool foreground_refresh_pending() const { return foreground_refresh_pending_; }
  std::uint32_t foreground_revision() const { return foreground_revision_; }
  bool composite_foreground(
      const std::uint8_t* base,
      std::size_t pixels,
      std::uint8_t* output,
      std::size_t output_size) const;
  void foreground_composited(std::uint32_t composite_us);
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
  ReceiverOperationResult controller_session_begin(
      const std::uint8_t* command, std::size_t size);
  ReceiverOperationResult overlay_begin(
      const std::uint8_t* command, std::size_t size,
      std::uint64_t local_monotonic_us);
  ReceiverOperationResult overlay_patch(
      const std::uint8_t* command, std::size_t size);
  ReceiverOperationResult overlay_patch_batch(
      const std::uint8_t* command, std::size_t size);
  ReceiverOperationResult overlay_commit(
      const std::uint8_t* command, std::size_t size,
      std::uint64_t local_monotonic_us);
  ReceiverOperationResult overlay_clear(
      const std::uint8_t* command, std::size_t size);
  ReceiverOperationResult overlay_renew(
      const std::uint8_t* command, std::size_t size,
      std::uint64_t local_monotonic_us);
  ReceiverOperationResult finish_overlay(OverlayOperationResult result);
  void discard_overlay_staging();
  void clear_foreground_visibility(bool discard_staging);
  void activate_pending_overlay(std::uint64_t local_monotonic_us);

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
  std::uint32_t foreground_revision_ = 0;
  bool foreground_refresh_pending_ = false;
  OverlayOperationResult last_overlay_result_ = OverlayOperationResult::None;
  SparseOverlayStats overlay_stats_{};
#if LEDGRID_ENABLE_LOCAL_BACKGROUND
  std::uint8_t overlay_rgba_[2][kContractLocalRgbaBytes] = {};
  std::uint8_t overlay_coverage_[2][kContractLocalPixels] = {};
  std::uint8_t committed_plane_ = 0;
  std::uint8_t staging_plane_ = 1;
  std::uint16_t staging_coverage_pixels_ = 0;
  std::uint16_t committed_coverage_pixels_ = 0;
  std::uint8_t controller_session_[kControllerSessionBytes] = {};
  Digest256 controller_snapshot_digest_{};
  std::uint64_t controller_desired_revision_ = 0;
  bool controller_session_present_ = false;
  bool session_requires_snapshot_ = true;
  OverlayGenerationOrderState overlay_generation_order_{};
  OverlayPatchOrderState overlay_patch_order_{};
  Digest256 last_commit_digest_{};
  Digest256 last_clear_digest_{};
  Digest256 last_batch_digest_{};
  std::uint16_t last_batch_first_start_ = 0;
  std::uint16_t last_batch_span_count_ = 0;
  bool last_commit_digest_present_ = false;
  bool last_clear_digest_present_ = false;
  bool last_batch_digest_present_ = false;
  bool committed_overlay_present_ = false;
  bool pending_overlay_present_ = false;
  std::uint64_t staged_scene_revision_ = 0;
  std::uint64_t staged_scene_epoch_ = 0;
  std::uint64_t staged_base_revision_ = 0;
  std::uint32_t staged_lease_ms_ = 0;
  std::uint64_t staged_started_local_us_ = 0;
  std::uint64_t pending_present_at_scene_time_us_ = 0;
  std::uint64_t committed_scene_revision_ = 0;
  std::uint64_t committed_scene_epoch_ = 0;
  std::uint64_t committed_base_revision_ = 0;
  std::uint64_t committed_present_at_scene_time_us_ = 0;
  std::uint32_t committed_lease_ms_ = 0;
  std::uint64_t committed_lease_deadline_us_ = 0;
  bool committed_has_lease_ = false;
#endif
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
// Profile transfer/staging commands are deliberately display-inert. Production
// uses this pure classification seam before comparing active bindings.
bool installation_profile_command_may_change_active_binding(
    ReceiverCommand command);

}  // namespace ledgrid
