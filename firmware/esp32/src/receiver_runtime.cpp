#include "ledgrid/receiver_runtime.hpp"

#include <algorithm>
#include <cstring>

#include "ledgrid/sha256.hpp"
#include "ledgrid/startup_animation.hpp"

namespace ledgrid {
namespace {

std::uint16_t read_u16(const std::uint8_t* input) {
  return static_cast<std::uint16_t>(
      (static_cast<std::uint16_t>(input[0]) << 8U) | input[1]);
}

std::uint32_t read_u32(const std::uint8_t* input) {
  return (static_cast<std::uint32_t>(input[0]) << 24U) |
         (static_cast<std::uint32_t>(input[1]) << 16U) |
         (static_cast<std::uint32_t>(input[2]) << 8U) | input[3];
}

std::uint64_t read_u64(const std::uint8_t* input) {
  std::uint64_t value = 0;
  for (std::size_t index = 0; index < 8; ++index) {
    value = (value << 8U) | input[index];
  }
  return value;
}

bool equal_bytes(const std::uint8_t* left, const std::uint8_t* right,
                 std::size_t size) {
  return std::memcmp(left, right, size) == 0;
}

bool valid_cadence(std::uint16_t cadence) {
  return cadence >= kMinLocalCadenceHz && cadence <= kMaxLocalCadenceHz;
}

std::uint32_t saturating_increment(std::uint32_t value) {
  return value == UINT32_MAX ? value : value + 1U;
}

constexpr std::uint16_t kHueCycleSteps = 6U * 256U;
constexpr std::uint16_t kSpatialStep =
    kHueCycleSteps / kStartupRainbowPeriodPixels;

void hue_to_rgb(std::uint16_t hue, std::uint8_t* rgb) {
  const std::uint8_t sector = hue >> 8U;
  const std::uint8_t ramp = hue & 0xFFU;
  const std::uint8_t falling = 0xFFU - ramp;
  switch (sector) {
    case 0: rgb[0] = 255; rgb[1] = ramp; rgb[2] = 0; break;
    case 1: rgb[0] = falling; rgb[1] = 255; rgb[2] = 0; break;
    case 2: rgb[0] = 0; rgb[1] = 255; rgb[2] = ramp; break;
    case 3: rgb[0] = 0; rgb[1] = falling; rgb[2] = 255; break;
    case 4: rgb[0] = ramp; rgb[1] = 0; rgb[2] = 255; break;
    default: rgb[0] = 255; rgb[1] = 0; rgb[2] = falling; break;
  }
}

}  // namespace

ReceiverRuntime::ReceiverRuntime(bool local_background_enabled)
    : local_background_enabled_(local_background_enabled) {}

bool ReceiverOutputState::advance_revision() {
  if (revision_ == UINT64_MAX) return false;
  ++revision_;
  return true;
}

bool ReceiverOutputState::configure(
    std::uint8_t strip_count, std::uint16_t leds_per_strip) {
  if (strip_count == configuration_.strip_count &&
      leds_per_strip == configuration_.leds_per_strip) {
    return true;
  }
  if (!advance_revision()) return false;
  configuration_.strip_count = strip_count;
  configuration_.leds_per_strip = leds_per_strip;
  return true;
}

bool ReceiverOutputState::set_brightness(std::uint8_t brightness) {
  if (brightness == configuration_.brightness) return true;
  if (!advance_revision()) return false;
  configuration_.brightness = brightness;
  return true;
}

ReceiverOperationResult ReceiverRuntime::finish(ReceiverOperationResult result) {
  last_result_ = result;
  return result;
}

ReceiverOperationResult ReceiverRuntime::start_local(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kLocalBackgroundStartBytes) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  LocalBackgroundParameters candidate{};
  candidate.component_id = read_u16(command + 1);
  candidate.preferred_cadence_hz = read_u16(command + 3);
  candidate.global_strip_offset = read_u32(command + 5);
  candidate.common_seed = read_u32(command + 9);
  candidate.scene_epoch = read_u64(command + 13);
  if (candidate.component_id != kCompiledRainbowComponentId ||
      !valid_cadence(candidate.preferred_cadence_hz)) {
    return finish(ReceiverOperationResult::InvalidCommand);
  }
  if (context_state_ != PresentationContextState::Active ||
      active_context_.scene_epoch != candidate.scene_epoch) {
    return finish(ReceiverOperationResult::InvalidState);
  }
  local_ = candidate;
  ++render_generation_;
  base_mode_ = BaseMode::LocalBackground;
  foreground_state_ = ForegroundState::Cleared;
  transition_reason_ = BaseTransitionReason::LocalStart;
  cadence_initialized_ = false;
  force_local_refresh_ = true;
  return finish(ReceiverOperationResult::Ok);
}

ReceiverOperationResult ReceiverRuntime::update_local(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kLocalBackgroundParametersBytes) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  if (base_mode_ != BaseMode::LocalBackground) {
    return finish(ReceiverOperationResult::InvalidState);
  }
  const std::uint16_t cadence = read_u16(command + 1);
  if (!valid_cadence(cadence)) return finish(ReceiverOperationResult::InvalidCommand);
  local_.preferred_cadence_hz = cadence;
  local_.global_strip_offset = read_u32(command + 3);
  local_.common_seed = read_u32(command + 7);
  ++render_generation_;
  cadence_initialized_ = false;
  force_local_refresh_ = true;
  return finish(ReceiverOperationResult::Ok);
}

ReceiverOperationResult ReceiverRuntime::context_begin(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kPresentationContextBeginBytes || command[1] != kPresentationContextVersion) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  const std::uint8_t* session = command + 2;
  const std::uint64_t revision = read_u64(command + 18);
  const std::uint8_t* digest = command + 26;
  const bool same_staged_session =
      context_state_ != PresentationContextState::None &&
      equal_bytes(session, staged_context_.session, 16);
  if (same_staged_session) {
    if (revision < staged_context_.scene_revision)
      return finish(ReceiverOperationResult::StaleRevision);
    if (revision == staged_context_.scene_revision) {
      if (!equal_bytes(digest, staged_context_.context_digest, 32))
        return finish(ReceiverOperationResult::Conflict);
      return finish(ReceiverOperationResult::Ok);
    }
  }
  const bool same_active_session = context_state_ == PresentationContextState::Active &&
      equal_bytes(session, active_context_.session, 16);
  if (same_active_session) {
    if (revision < active_context_.scene_revision)
      return finish(ReceiverOperationResult::StaleRevision);
    if (revision == active_context_.scene_revision) {
      if (!equal_bytes(digest, active_context_.context_digest, 32))
        return finish(ReceiverOperationResult::Conflict);
      return finish(ReceiverOperationResult::Ok);
    }
  }
  staged_context_ = {};
  std::memcpy(staged_context_.session, session, 16);
  staged_context_.scene_revision = revision;
  std::memcpy(staged_context_.context_digest, digest, 32);
  context_state_ = PresentationContextState::Staging;
  return finish(ReceiverOperationResult::Ok);
}

ReceiverOperationResult ReceiverRuntime::context_set(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size < kPresentationContextSetBaseBytes ||
      size > kPresentationContextSetMaxBytes ||
      command[1] != kPresentationContextVersion) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  const std::uint8_t modifier_count = command[144];
  if (size != kPresentationContextSetBaseBytes +
                  static_cast<std::size_t>(modifier_count) *
                      kPresentationContextSetEntryBytes) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  std::uint8_t context_digest[32] = {};
  sha256(command + 18, size - 18, context_digest);
  if (context_state_ == PresentationContextState::Active &&
      equal_bytes(command + 2, active_context_.session, 16) &&
      read_u64(command + 18) == active_context_.scene_revision &&
      equal_bytes(context_digest, active_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::Ok);
  }
  if (context_state_ == PresentationContextState::Ready &&
      equal_bytes(command + 2, staged_context_.session, 16) &&
      read_u64(command + 18) == staged_context_.scene_revision &&
      equal_bytes(context_digest, staged_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::Ok);
  }
  if (context_state_ != PresentationContextState::Staging ||
      !equal_bytes(command + 2, staged_context_.session, 16) ||
      read_u64(command + 18) != staged_context_.scene_revision)
    return finish(ReceiverOperationResult::InvalidState);
  const std::uint16_t luminance = read_u16(command + 97);
  if (command[26] < 1 || command[26] > 5 || read_u32(command + 27) == 0 ||
      luminance > kQ8_8One || command[103] != 1 ||
      modifier_count > 14) {
    return finish(ReceiverOperationResult::InvalidContext);
  }
  std::uint8_t prior_id = 0;
  std::uint8_t field_count = 0;
  std::uint8_t surface_count = 0;
  for (std::size_t index = 0; index < modifier_count; ++index) {
    const std::size_t offset = 145 + index * 3U;
    const std::uint8_t id = command[offset];
    if (id == 0 || id > 14 || id <= prior_id ||
        read_u16(command + offset + 1) > kQ8_8One) {
      return finish(ReceiverOperationResult::InvalidContext);
    }
    if (id >= 6 && id <= 8) ++field_count;
    if (id >= 9 && id <= 13) ++surface_count;
    prior_id = id;
  }
  if (field_count > 1 || surface_count > 1)
    return finish(ReceiverOperationResult::InvalidContext);
  if (!equal_bytes(context_digest, staged_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::DigestMismatch);
  }
  std::uint8_t plant_canonical[2 + 14 * 3] = {};
  plant_canonical[0] = command[103];
  plant_canonical[1] = modifier_count;
  if (modifier_count != 0) {
    std::memcpy(plant_canonical + 2, command + 145,
                static_cast<std::size_t>(modifier_count) * 3U);
  }
  std::uint8_t plant_digest[32] = {};
  sha256(plant_canonical, 2 + static_cast<std::size_t>(modifier_count) * 3U,
         plant_digest);
  if (!equal_bytes(plant_digest, command + 112, 32)) {
    return finish(ReceiverOperationResult::DigestMismatch);
  }
  staged_context_.vibe_id = command[26];
  staged_context_.vibe_profile_version = read_u32(command + 27);
  staged_context_.vibe_revision = read_u64(command + 31);
  std::memcpy(staged_context_.vibe_digest, command + 39, 32);
  staged_context_.luminance_q8_8 = luminance;
  staged_context_.modifier_revision = read_u64(command + 104);
  std::memcpy(staged_context_.modifier_digest, command + 112, 32);
  context_state_ = PresentationContextState::Ready;
  return finish(ReceiverOperationResult::Ok);
}

ReceiverOperationResult ReceiverRuntime::context_commit(
    const std::uint8_t* command, std::size_t size) {
  if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
  if (size != kPresentationContextCommitBytes ||
      command[1] != kPresentationContextVersion) {
    return finish(ReceiverOperationResult::InvalidSize);
  }
  if (context_state_ == PresentationContextState::Active &&
      equal_bytes(command + 2, active_context_.session, 16) &&
      read_u64(command + 18) == active_context_.scene_revision &&
      read_u64(command + 26) == active_context_.scene_epoch &&
      read_u64(command + 34) == active_context_.present_at_scene_time_us &&
      equal_bytes(command + 42, active_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::Ok);
  }
  if (context_state_ != PresentationContextState::Ready ||
      !equal_bytes(command + 2, staged_context_.session, 16) ||
      read_u64(command + 18) != staged_context_.scene_revision ||
      !equal_bytes(command + 42, staged_context_.context_digest, 32)) {
    return finish(ReceiverOperationResult::InvalidState);
  }
  staged_context_.scene_epoch = read_u64(command + 26);
  staged_context_.present_at_scene_time_us = read_u64(command + 34);
  active_context_ = staged_context_;
  active_context_present_ = true;
  if (base_mode_ == BaseMode::LocalBackground) {
    local_.scene_epoch = active_context_.scene_epoch;
  }
  ++render_generation_;
  context_state_ = PresentationContextState::Active;
  request_local_refresh();
  return finish(ReceiverOperationResult::Ok);
}

ReceiverOperationResult ReceiverRuntime::process_command(
    const std::uint8_t* command,
    std::size_t size,
    std::uint64_t local_monotonic_us) {
  if (command == nullptr || size == 0) return finish(ReceiverOperationResult::InvalidSize);
  switch (static_cast<ReceiverCommand>(command[0])) {
    case ReceiverCommand::LocalBackgroundStart:
      return start_local(command, size);
    case ReceiverCommand::LocalBackgroundStop:
      if (!local_background_enabled_) return finish(ReceiverOperationResult::Unsupported);
      if (size != kLocalBackgroundStopBytes) return finish(ReceiverOperationResult::InvalidSize);
      base_mode_ = BaseMode::StartupFallback;
      ++render_generation_;
      foreground_state_ = ForegroundState::Cleared;
      transition_reason_ = BaseTransitionReason::LocalStop;
      return finish(ReceiverOperationResult::Ok);
    case ReceiverCommand::LocalBackgroundParameters:
      return update_local(command, size);
    case ReceiverCommand::PresentationContextBegin:
      return context_begin(command, size);
    case ReceiverCommand::PresentationContextSet:
      return context_set(command, size);
    case ReceiverCommand::PresentationContextCommit:
      {
        const bool was_active =
            context_state_ == PresentationContextState::Active;
        const ReceiverOperationResult result = context_commit(command, size);
        if (result == ReceiverOperationResult::Ok && !was_active) {
          context_committed_local_us_ = local_monotonic_us;
        }
        return result;
      }
    default:
      return finish(ReceiverOperationResult::InvalidCommand);
  }
}

void ReceiverRuntime::complete_host_frame() {
  base_mode_ = BaseMode::HostFullScene;
  ++render_generation_;
  foreground_state_ = ForegroundState::Cleared;
  transition_reason_ = BaseTransitionReason::HostTakeover;
  last_result_ = ReceiverOperationResult::Ok;
}

void ReceiverRuntime::receiver_restart() {
  base_mode_ = BaseMode::StartupFallback;
  ++render_generation_;
  foreground_state_ = ForegroundState::Cleared;
  maintenance_state_ = MaintenanceState::Inactive;
  transition_reason_ = BaseTransitionReason::ReceiverRestart;
  context_state_ = PresentationContextState::None;
  staged_context_ = {};
  active_context_ = {};
  active_context_present_ = false;
  cadence_initialized_ = false;
  context_committed_local_us_ = 0;
}

bool ReceiverRuntime::local_render_failed_if_current(
    std::uint32_t generation) {
  if (!local_render_still_valid(generation)) return false;
  base_mode_ = BaseMode::StartupFallback;
  ++render_generation_;
  foreground_state_ = ForegroundState::Cleared;
  transition_reason_ = BaseTransitionReason::LocalRenderFailure;
  last_result_ = ReceiverOperationResult::RenderFailed;
  context_state_ = PresentationContextState::None;
  staged_context_ = {};
  active_context_ = {};
  active_context_present_ = false;
  cadence_initialized_ = false;
  return true;
}

bool ReceiverRuntime::local_frame_due(std::uint64_t now_us) {
  if (base_mode_ != BaseMode::LocalBackground) return false;
  if (force_local_refresh_ || !cadence_initialized_) return true;
  const std::uint64_t deadline = cadence_origin_us_ +
      (next_cadence_index_ * 1000000ULL + local_.preferred_cadence_hz - 1U) /
          local_.preferred_cadence_hz;
  if (now_us < deadline) return false;
  const std::uint64_t elapsed = now_us - cadence_origin_us_;
  const std::uint64_t due_index =
      (elapsed * local_.preferred_cadence_hz) / 1000000ULL;
  const std::uint64_t late_periods =
      due_index > next_cadence_index_ ? due_index - next_cadence_index_ : 0;
  if (late_periods != 0) {
    const std::uint64_t remaining = UINT32_MAX - render_stats_.missed_cadence;
    render_stats_.missed_cadence += static_cast<std::uint32_t>(
        std::min<std::uint64_t>(late_periods, remaining));
  }
  return true;
}

bool ReceiverRuntime::local_frame_rendered_if_current(
    std::uint32_t generation,
    std::uint64_t now_us,
    std::uint64_t frame_scene_time_us,
    std::uint32_t render_us) {
  if (!local_render_still_valid(generation)) return false;
  render_stats_.cadence_deadlines = saturating_increment(render_stats_.cadence_deadlines);
  render_stats_.rendered_frames = saturating_increment(render_stats_.rendered_frames);
  render_stats_.last_render_us = static_cast<std::uint16_t>(
      std::min<std::uint32_t>(render_us, UINT16_MAX));
  render_stats_.max_render_us = std::max(
      render_stats_.max_render_us, render_stats_.last_render_us);
  render_stats_.last_frame_scene_time_us = frame_scene_time_us;
  if (!cadence_initialized_ || force_local_refresh_) {
    cadence_origin_us_ = now_us;
    next_cadence_index_ = 1;
    cadence_initialized_ = true;
  } else {
    const std::uint64_t elapsed = now_us - cadence_origin_us_;
    next_cadence_index_ =
        (elapsed * local_.preferred_cadence_hz) / 1000000ULL + 1U;
  }
  force_local_refresh_ = false;
  return true;
}

void ReceiverRuntime::request_local_refresh() {
  if (base_mode_ == BaseMode::LocalBackground) force_local_refresh_ = true;
}

std::uint64_t ReceiverRuntime::scene_time_us(
    std::uint64_t local_monotonic_us) const {
  // A newer context may be staging while the prior committed context remains
  // authoritative. Do not suspend or rewind receiver-local playback until the
  // replacement commits atomically.
  if (!active_context_present_)
    return 0;
  if (local_monotonic_us <= context_committed_local_us_)
    return active_context_.present_at_scene_time_us;
  const std::uint64_t delta = local_monotonic_us - context_committed_local_us_;
  if (UINT64_MAX - active_context_.present_at_scene_time_us < delta)
    return UINT64_MAX;
  return active_context_.present_at_scene_time_us + delta;
}

std::uint8_t apply_luminance_q8_8(
    std::uint8_t channel, std::uint16_t factor_q8_8) {
  const std::uint32_t value =
      (static_cast<std::uint32_t>(channel) * factor_q8_8 + 128U) / 256U;
  return static_cast<std::uint8_t>(std::min<std::uint32_t>(value, 255U));
}

bool render_compiled_rainbow(
    std::uint64_t elapsed_us,
    const LocalBackgroundParameters& parameters,
    std::uint16_t luminance_q8_8,
    std::uint8_t strip_count,
    std::uint16_t leds_per_strip,
    std::uint8_t* output,
    std::size_t output_size) {
  const std::size_t required =
      static_cast<std::size_t>(strip_count) * leds_per_strip * 3U;
  if (output == nullptr || strip_count == 0 || leds_per_strip == 0 ||
      output_size < required || luminance_q8_8 > kQ8_8One) return false;
  const std::uint16_t motion = static_cast<std::uint16_t>(
      ((elapsed_us % kStartupRainbowCycleUs) * kHueCycleSteps) /
      kStartupRainbowCycleUs);
  const std::uint16_t seed_phase = parameters.common_seed % kHueCycleSteps;
  for (std::uint8_t strip = 0; strip < strip_count; ++strip) {
    const std::uint32_t global_strip = parameters.global_strip_offset + strip;
    for (std::uint16_t led = 0; led < leds_per_strip; ++led) {
      const std::uint16_t spatial = static_cast<std::uint16_t>(
          ((global_strip + led) % kStartupRainbowPeriodPixels) * kSpatialStep);
      const std::uint16_t hue = static_cast<std::uint16_t>(
          (spatial + seed_phase + kHueCycleSteps - motion) % kHueCycleSteps);
      const std::size_t offset =
          (static_cast<std::size_t>(strip) * leds_per_strip + led) * 3U;
      hue_to_rgb(hue, output + offset);
      output[offset] = apply_luminance_q8_8(output[offset], luminance_q8_8);
      output[offset + 1] = apply_luminance_q8_8(output[offset + 1], luminance_q8_8);
      output[offset + 2] = apply_luminance_q8_8(output[offset + 2], luminance_q8_8);
    }
  }
  return true;
}

ReceiverRenderTicket capture_render_ticket(
    const ReceiverRuntime& runtime, const ReceiverOutputState& output) {
  ReceiverRenderTicket ticket{};
  ticket.owner = runtime.base_mode();
  ticket.ownership_generation = runtime.render_generation();
  ticket.output_revision = output.revision();
  ticket.output = output.configuration();
  return ticket;
}

bool render_ticket_still_current(
    const ReceiverRuntime& runtime,
    const ReceiverOutputState& output,
    const ReceiverRenderTicket& ticket) {
  return (ticket.owner == BaseMode::StartupFallback ||
          ticket.owner == BaseMode::LocalBackground) &&
         runtime.base_mode() == ticket.owner &&
         runtime.render_generation() == ticket.ownership_generation &&
         output.revision() == ticket.output_revision;
}

PhysicalSubmitResult submit_rendered_frame_if_current(
    const ReceiverRuntime& runtime,
    const ReceiverOutputState& output,
    const ReceiverRenderTicket& ticket,
    PhysicalSubmitCallback submit,
    void* submit_context) {
  if (!render_ticket_still_current(runtime, output, ticket)) {
    return PhysicalSubmitResult::Stale;
  }
  if (submit == nullptr || !submit(submit_context, ticket.output)) {
    return PhysicalSubmitResult::DriverRejected;
  }
  return PhysicalSubmitResult::Submitted;
}

}  // namespace ledgrid
